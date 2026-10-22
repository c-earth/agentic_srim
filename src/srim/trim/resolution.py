"""Resolve pinned layered TRIM material intent and independently replay its evidence."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import asdict
from decimal import Decimal, localcontext
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
from src.backend.config import Config
from src.backend.paths import ProjectPaths
from src.backend.records import CreatedDirectory, _validate_paths
from src.backend.resources import ResourceReference, _hash_file, _unique_object, register_resource, resolve_resource, verify_resource
from src.srim.cab.strengths import _SYMBOLS, _apply_cab_overrides, _decode_payload, _encode_payload, _normalize_entries
from src.srim.cab.cache import CABEntryReference, _dependency_key, _validate_entry, load_cab_entry
from src.srim.cab.constants import CAB_PRECISION, CAB_ROUNDING, CAB_UNITS, SR_BONDS
from src.srim.resources.compatibility import read_module_version
from src.srim.resources.defaults import read_sr_defaults
from src.srim.resources.materials import _atomic_composition, _encode_atomdata, _encode_compounds, apply_material_overrides, parse_atomdata, parse_compounds
from src.srim.common.types import JSONObject
from src.srim.common.records import _validate_document
from src.srim.resources.resolution import _calculate_cab, _make_dependency_inputs, _publish_effective_selection, _resolve_selection, resolve_material_correction
from src.srim.common.files import _read_regular_bytes
from src.srim.trim.defaults import _encode_trim_damage, apply_trim_damage_overrides, parse_trim_damage, read_trim_defaults, read_trim_installation
from src.srim.trim.inputs import ENERGIES, _decimal, flatten_trim_target, render_trim_events, render_trim_input, validate_trim_engine_request, validate_trim_request
from src.srim.trim.models import ResolvedTRIMInputs


def _resolve_damage_selection(paths: ProjectPaths, config: Config, selection: JSONObject, defaults: JSONObject) -> tuple[JSONObject, dict[str, bytes]]:
    """Snapshot selection's damage source/edits under paths/config/defaults.

    Return resolved-role evidence and exact source/effective bytes without
    publication or alias changes; orchestration publishes requested variants
    after all layers validate. Raise ValueError for mode/data/hash failures and
    OSError for unsafe/missing/changing files. config is the shared selection context.
    """
    if not isinstance(selection, dict) or ("version" in selection) == ("source" in selection) or set(selection) != ({"version", "overrides", "create_version"} if "version" in selection else {"source", "overrides", "create_version"}) or not isinstance(selection["overrides"], list) or type(selection["create_version"]) is not bool:
        raise ValueError("Damage selection requires one source, overrides and create_version")
    reference: ResourceReference | None = None
    if "version" in selection:
        version = selection["version"]
        if not isinstance(version, str) or (version != "default" and re.fullmatch(r"v[1-9][0-9]*", version) is None):
            raise ValueError("Damage version requires default or vN")
        reference = ResourceReference(**defaults["damage"]) if version == "default" else resolve_resource(paths, "material", "trim-damage", version)
        source = _read_regular_bytes(verify_resource(paths, reference))
        if (hashlib.sha256(source).hexdigest(), len(source)) != (reference.sha256, reference.size_bytes):
            raise ValueError("Damage version changed while snapshotting")
        kind, origin = "version", asdict(reference)
    else:
        source_spec = selection["source"]
        if not isinstance(source_spec, dict) or source_spec.get("kind") not in ("file", "inline") or set(source_spec) != ({"kind", "path"} if source_spec["kind"] == "file" else {"kind", "data"}):
            raise ValueError("Invalid damage source")
        kind = source_spec["kind"]
        if kind == "file":
            if not isinstance(source_spec["path"], str) or not Path(source_spec["path"]).is_absolute():
                raise ValueError("Damage source requires an absolute path")
            origin = source_spec["path"]
            expected = _hash_file(Path(origin))
            source = _read_regular_bytes(Path(origin))
            if (hashlib.sha256(source).hexdigest(), len(source)) != expected:
                raise ValueError("Damage source changed while snapshotting")
        else:
            origin = "inline"
            data = source_spec["data"]
            if isinstance(data, str):
                source = data.encode("utf-8")
            elif isinstance(data, dict):
                source = (json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
            else:
                raise ValueError("Inline damage source requires JSON text/object")
    effective = _encode_trim_damage(apply_trim_damage_overrides(parse_trim_damage(source), selection["overrides"]))
    snapshots = {"resources/damage/source": source, "resources/damage/effective": effective}
    manifests = {name.rsplit("/", 1)[1]: {"path": "input/" + name, "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)} for name, data in snapshots.items()}
    recorded = {"reference": asdict(reference) if reference is not None else None, "base": asdict(reference) if reference is not None and selection["create_version"] else None, "source": {"kind": kind, "origin": origin, "snapshot": manifests["source"]}, "overrides": deepcopy(selection["overrides"]), "effective": manifests["effective"], "published": None}
    _validate_document(recorded, "resolved-role")
    return recorded, snapshots


def _resolve_damage(atomic_number: int, atomdata: JSONObject, damage: JSONObject, overrides: JSONObject) -> tuple[JSONObject, JSONObject]:
    """Return three energies and origins for atomic_number and selected data/overrides.

    Component overrides win, then damage rows, then Surf's positive sublimation
    or observed zero-to-2 rule. Preserve explicit zero. No mutation/I/O; raise
    ValueError when a needed value lacks usable data, not for native Z limits.
    """
    atom = next((row for row in atomdata["rows"] if row["atomic_number"] == atomic_number), None)
    row = next((row for row in damage["rows"] if row["atomic_number"] == atomic_number), None)
    values: JSONObject = {}
    origins: JSONObject = {}
    for field in ENERGIES:
        if field in overrides:
            value, origin = overrides[field], {"kind": "component-override"}
        elif row is not None and field in row:
            value, origin = row[field], {"kind": "damage-resource", "atomic_number": atomic_number}
        elif field == "surface_binding_eV" and atom is not None:
            sublimation = atom["sublimation_energy_eV"]
            number = _decimal(sublimation, "atomdata.sublimation_energy_eV", minimum=Decimal(0))
            value = sublimation if number > 0 else "2"
            origin = {"kind": "atomdata-sublimation" if number > 0 else "zero-fallback", "surface_rule_version": 1, "sublimation_energy_eV": sublimation, "atomic_number": atomic_number}
        else:
            raise ValueError(f"Component Z={atomic_number} lacks {field} data or an explicit override")
        values[field] = format(_decimal(value, f"component.{field}", minimum=Decimal(0)), "f")
        origins[field] = {**origin, "value": values[field]}
    return values, origins


def _construct_trim_layer(layer: JSONObject, atomdata: JSONObject, compounds: JSONObject | None, damage: JSONObject) -> tuple[JSONObject, JSONObject]:
    """Construct numeric layer and provenance from validated layer/material inputs.

    atomdata/compounds/damage are pinned effective data. Convert compound mass
    proportions using existing material arithmetic; apply density/component mass
    and energy overrides. Preserve repeated/direct entries. No CAB execution,
    mutation or I/O. Raise ValueError for unavailable/inconsistent selected data.
    """
    material = layer["material"]
    index = material.get("record_index")
    if material["source"] == "compound":
        if compounds is None or type(index) is not int or not 1 <= index <= len(compounds["records"]):
            raise ValueError("Layer selects a missing effective compound record")
        record = compounds["records"][index - 1]
        composition = _atomic_composition(record, atomdata)
        bonds = record["bonds"]
        density = record["density_g_cm3"]
        source = {"source": "compound", "record_index": index, "record": deepcopy(record), "index_map": deepcopy(compounds["index_map"])}
    else:
        composition = deepcopy(material["elements"])
        bonds = material.get("bonds", {})
        density = material["density_g_cm3"]
        source = {"source": "direct", "record_index": None, "record": deepcopy(material), "index_map": []}
    edits = {item["component_index"]: item for item in layer["overrides"]["components"]}
    if any(index > len(composition) for index in edits):
        raise ValueError("Component override index exceeds effective layer composition")
    components: list[JSONObject] = []
    origins: list[JSONObject] = []
    rows = {row["atomic_number"]: row for row in atomdata["rows"]}
    for number, component in enumerate(composition, 1):
        z = component["atomic_number"]
        overrides = edits.get(number, {})
        if "mass_amu" in overrides:
            mass, mass_origin = overrides["mass_amu"], "component-override"
        elif z in rows:
            mass, mass_origin = rows[z]["natural_mass_amu"], "atomdata-natural-mass"
        else:
            raise ValueError(f"Component {number}, Z={z} lacks mass data or an explicit override")
        energies, energy_origins = _resolve_damage(z, atomdata, damage, overrides)
        component = {"atomic_number": z, "mass_amu": format(_decimal(mass, "component.mass_amu", minimum=Decimal(0), strict=True), "f"), "stoichiometry": format(_decimal(component["stoichiometry"], "component.stoichiometry", minimum=Decimal(0), strict=True), "f"), **energies}
        components.append(component)
        origins.append({"component_index": number, "atomic_number": z, "mass": {"kind": mass_origin, "value": component["mass_amu"]}, "damage": energy_origins})
    density_override = layer["overrides"]["density_g_cm3"]
    if density_override is not None:
        density = density_override
    normalized = _normalize_entries("bond", bonds)
    bond_records = [{"identifier": f"{_SYMBOLS[left - 1]}{order}{_SYMBOLS[right - 1]}", "count": count} for (left, right, order), count in normalized.items()]
    factor = layer["correction"].get("applied_factor", "1.0")
    numeric = {"name": layer["name"], "thickness_angstrom": format(Decimal(layer["thickness_angstrom"]), "f"), "density_g_cm3": format(_decimal(density, "layer.density_g_cm3", minimum=Decimal(0), strict=True), "f"), "phase": material["phase"], "correction_factor": format(Decimal(factor), "f"), "components": components}
    provenance = {"material": source, "composition": deepcopy(composition), "bonds": bond_records, "density": {"kind": "layer-override" if density_override is not None else material["source"], "value": numeric["density_g_cm3"]}, "components": origins}
    return numeric, provenance


def resolve_trim_inputs(paths: ProjectPaths, config: Config, worker: CreatedDirectory, request: JSONObject, selections: JSONObject, *, capture_timeout_seconds: float = 30.0, dependency_timeout_seconds: float = 60.0, progress: Callable[[JSONObject], None] | None = None) -> ResolvedTRIMInputs:
    """Resolve request/selections into a pinned layered input bundle under paths/config.

    worker identifies existing SR/CAB dependency work. Timeouts bound dependency
    operations; progress receives tagged events after each layer completes.
    Snapshot sources, validate all material layers before requested publications,
    resolve per-layer corrections and independently verify the returned bundle.
    May publish requested versions/run missing CAB dependencies but never TRIM.
    Raise ValueError/OSError/dependency errors with retained evidence; callbacks
    and interruptions propagate. Do not mutate caller inputs or stage a main run.
    """
    validate_trim_request(request)
    _validate_paths(paths)
    if not isinstance(worker, CreatedDirectory) or worker.kind != "worker" or type(worker.suffix) is not int or worker.suffix < 1 or worker.path != paths.workers / f"worker_{worker.suffix}" or not worker.path.is_dir() or worker.path.is_symlink() or worker.path.is_junction():
        raise ValueError("Expected allocated link-free worker")
    for timeout in (capture_timeout_seconds, dependency_timeout_seconds):
        if type(timeout) not in (int, float) or timeout <= 0 or not math.isfinite(timeout):
            raise ValueError("Operation timeouts must be finite and positive")
    if progress is not None and not callable(progress):
        raise ValueError("progress must be callable or None")
    intent = json.loads(json.dumps(request, allow_nan=False))
    chosen = json.loads(json.dumps(selections, allow_nan=False))
    roles = {"atomdata", "compound", "electronic", "nuclear", "core", "bond", "damage"}
    uses_compound = any(layer["material"]["source"] == "compound" for layer in intent["parameters"]["layers"])
    if not isinstance(chosen, dict) or set(chosen) != roles or (chosen["compound"] is None) == uses_compound or any(chosen[role] is None for role in roles - {"compound"}):
        raise ValueError("Selections require seven roles and matching compound use")
    profile, installation = read_trim_installation(config)
    generation = read_sr_defaults(paths)
    damage_defaults = read_trim_defaults(paths)
    encoded_sr = (json.dumps(generation, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    encoded_trim = (json.dumps(damage_defaults, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    defaults = {"sr": {"generation": generation, "sha256": hashlib.sha256(encoded_sr).hexdigest(), "size_bytes": len(encoded_sr)}, "trim": {"generation": damage_defaults, "sha256": hashlib.sha256(encoded_trim).hexdigest(), "size_bytes": len(encoded_trim)}, "dependency_engine": None}
    resources: JSONObject = {}
    snapshots: dict[str, bytes] = {}
    published: list[JSONObject] = []
    completed_entries: list[JSONObject] = []
    try:
        for role in sorted(roles):
            if chosen[role] is None:
                continue
            resources[role], contents = _resolve_damage_selection(paths, config, chosen[role], damage_defaults) if role == "damage" else _resolve_selection(paths, config, role, chosen[role], generation)
            snapshots.update(contents)
        atoms = parse_atomdata(snapshots["resources/atomdata/effective"])
        compounds = parse_compounds(snapshots["resources/compound/effective"]) if uses_compound else None
        if compounds is not None:
            compounds = apply_material_overrides("compound", parse_compounds(snapshots["resources/compound/source"]), chosen["compound"]["overrides"])
        damage = parse_trim_damage(snapshots["resources/damage/effective"])
        layers = [_construct_trim_layer(layer, atoms, compounds, damage) for layer in intent["parameters"]["layers"]]
        projectile = deepcopy(intent["parameters"]["projectile"])
        if projectile["mass_amu"] is None:
            row = next((row for row in atoms["rows"] if row["atomic_number"] == projectile["atomic_number"]), None)
            if row is None:
                raise ValueError("Projectile lacks selected isotope mass or an explicit mass")
            projectile["mass_amu"] = row["isotope_mass_amu"]
        base_parameters = {key: deepcopy(value) for key, value in intent["parameters"].items() if key not in ("projectile", "layers")}
        flat_atoms, flat_layers, mapping = flatten_trim_target([item[0] for item in layers])
        effective = {"schema_version": 1, "engine": "trim", "parameters": {**base_parameters, "projectile": projectile, "engine_family": profile["engine_family"], "native_fields": deepcopy(profile["native_fields"]), "atoms": flat_atoms, "layers": flat_layers}}
        validate_trim_engine_request(effective)
        for role in sorted(resources):
            if role == "damage":
                if chosen[role]["create_version"]:
                    recorded = resources[role]
                    source = snapshots["resources/damage/source"]
                    provenance = {"origin": "user", "source": {"kind": recorded["source"]["kind"], "origin": recorded["source"]["origin"], "sha256": hashlib.sha256(source).hexdigest(), "size_bytes": len(source)}, "base": recorded["base"], "changes": recorded["overrides"], "copied_entries": [], "baseline_id": None, "procedure": None}
                    recorded["published"] = asdict(register_resource(paths, config, "material", "trim-damage", None, snapshots["resources/damage/effective"], provenance=provenance))
            else:
                resources[role] = _publish_effective_selection(paths, config, role, chosen[role], resources[role], snapshots)
            if resources[role]["published"] is not None:
                published.append(resources[role]["published"])
        dependency_inputs = None
        if any(layer["correction"]["mode"] == "derived" for layer in intent["parameters"]["layers"]):
            module_bytes = _read_regular_bytes(config.srim_root / "SR Module" / "SRModule.exe")
            version_bytes = read_module_version(config.srim_root)[1]
            sr_files = {"SRModule.exe": module_bytes, "VERSION": version_bytes, "SCOEF03.dat": snapshots["resources/electronic/effective"], "SNUC03.dat": snapshots["resources/nuclear/effective"]}
            dependency_inputs = _make_dependency_inputs(defaults["sr"], resources, atoms, sr_files)
            defaults["dependency_engine"] = {name: {"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)} for name, data in sr_files.items() if name in ("SRModule.exe", "VERSION")}
        records: list[JSONObject] = []
        native_evidence: dict[str, JSONObject] = {}
        references: dict[str, JSONObject] = {}
        for role in ("core", "bond"):
            view = json.loads(snapshots[f"resources/{role}/source"], object_pairs_hook=_unique_object)
            if set(view) == {"schema_version", "dataset", "entries", "values"}:
                for reference in view["entries"]:
                    references[json.dumps(reference, sort_keys=True)] = reference
        for index, ((numeric, provenance), original) in enumerate(zip(layers, intent["parameters"]["layers"], strict=True), 1):
            composition = [{"atomic_number": c["atomic_number"], "atomic_mass": c["mass_amu"], "atom_count": c["stoichiometry"]} for c in numeric["components"]]
            events: list[JSONObject] = []
            correction, dependencies = resolve_material_correction(paths, config, worker, original["correction"], composition, provenance["bonds"], atoms, defaults["sr"], resources, snapshots, dependency_inputs, completed_entries, capture_timeout_seconds=capture_timeout_seconds, dependency_timeout_seconds=dependency_timeout_seconds, progress=events.append if progress is not None else None)
            for event in events:
                progress({**event, "layer_index": index})
            numeric["correction_factor"] = format(Decimal(correction["applied_factor"]), "f")
            records.append({"layer_index": index, "parameters": numeric, "provenance": provenance, "correction": correction, "native_dependencies": dependencies})
            for dependency in dependencies:
                native_evidence[json.dumps(dependency, sort_keys=True)] = dependency
            if correction["mode"] == "derived":
                for reference in correction["bragg_entries"] + [item["entry_reference"] for key in ("core_entries", "bond_entries") for item in correction[key] if item["entry_reference"] is not None]:
                    references[json.dumps(reference, sort_keys=True)] = reference
        dependency_records: list[JSONObject] = []
        pending = [references[key] for key in sorted(references)]
        visited: set[str] = set()
        while pending:
            reference = pending.pop(0)
            identity = json.dumps(reference, sort_keys=True)
            if identity in visited:
                continue
            visited.add(identity)
            entry_ref = CABEntryReference(ResourceReference(**reference["dataset"]), reference["dependency_key"], reference["entry_id"], reference["sha256"], reference["size_bytes"])
            entry = load_cab_entry(paths, entry_ref)
            payload = _read_regular_bytes(verify_resource(paths, entry_ref.dataset).parent / "entries" / reference["dependency_key"] / (reference["entry_id"] + ".json"))
            if (hashlib.sha256(payload).hexdigest(), len(payload)) != (reference["sha256"], reference["size_bytes"]):
                raise ValueError("Dependency entry changed during snapshot")
            snapshots[f'dependencies/{reference["sha256"]}.json'] = payload
            dependency_records.append({"entry": deepcopy(reference), "snapshot": "input/dependencies/" + reference["sha256"] + ".json"})
            pending.extend(entry["dependency"]["entries"])
        dependency_records.extend({"simulation": native_evidence[key]} for key in sorted(native_evidence))
        flat_atoms, flat_layers, mapping = flatten_trim_target([item["parameters"] for item in records])
        effective["parameters"].update({"atoms": flat_atoms, "layers": flat_layers})
        snapshots.update({"installation/" + name: payload for name, payload in installation.items()})
        snapshots["installation/Data/SCOEF03.dat"] = snapshots["resources/electronic/effective"]
        engine_files = {name: snapshots["installation/" + name] for name in ("TRIM.exe", "Data/TRIM.cfg", "Data/VERSION", "Data/SCOEF03.dat")}
        snapshots["engine/TRIM.IN"] = render_trim_input(effective)
        event_bytes = render_trim_events(effective)
        if event_bytes is not None:
            snapshots["engine/TRIM.DAT"] = event_bytes
        native_checks: list[JSONObject] = []
        native_checks.append({"code": "native-bounds", "path": "parameters", "requested": {"layer_count": len(effective["parameters"]["layers"]), "atom_entry_count": len(effective["parameters"]["atoms"]), "total_ions": intent["parameters"]["total_ions"], "random_seed": intent["parameters"]["random_seed"], "autosave_ions": intent["parameters"]["autosave_ions"], "energy_keV": intent["parameters"]["projectile"]["energy_keV"], "angle_degrees": intent["parameters"]["projectile"]["angle_degrees"]}, "detail": "Native acceptance, completion and saved values require execution verification; no speculative numeric/size caps were applied."})
        if intent["parameters"]["autosave_ions"] in (0, 32001, 99999):
            native_checks.append({"code": "autosave-saved-value", "path": "parameters.autosave_ions", "requested": intent["parameters"]["autosave_ions"], "detail": "Short probes saved this input as 32000; disabling autosave is not established."})
        for path, name in [("parameters.name", intent["parameters"]["name"])] + [(f"parameters.layers[{i}].name", layer["name"]) for i, layer in enumerate(intent["parameters"]["layers"], 1)]:
            if '"' in name:
                native_checks.append({"code": "quoted-name", "path": path, "requested": name, "detail": "Embedded quotes were doubled for native serialization; verify the saved native name."})
        for i, event in enumerate(intent["parameters"]["events"] or [], 1):
            native_checks.append({"code": "event-geometry", "path": f"parameters.events[{i}]", "requested": deepcopy(event), "detail": "Native position, species, boundary and direction-vector behavior requires verification; no event relocation or normalization was applied."})
            if len(event["id"]) != 5:
                native_checks.append({"code": "event-identifier", "path": f"parameters.events[{i}].id", "requested": event["id"], "detail": "Five-character identifiers are tested; this identifier is preserved for native evaluation."})
            setup = Decimal(intent["parameters"]["projectile"]["energy_keV"]).as_tuple()
            if Decimal(event["energy_eV"]) > Decimal((setup.sign, setup.digits, setup.exponent + 3)):
                native_checks.append({"code": "event-energy-coverage", "path": f"parameters.events[{i}].energy_eV", "requested": event["energy_eV"], "detail": "Event energy exceeds setup energy used for native stopping precomputation; request preserved unchanged."})
        if intent["parameters"]["events"] is not None:
            native_checks.append({"code": "event-consumption", "path": "parameters.total_ions", "requested": intent["parameters"]["total_ions"], "detail": "Event exhaustion and native count interpretation require execution verification; event count did not replace total_ions."})
        resolved = {"schema_version": 1, "engine": "trim", "installation": profile, "defaults": defaults, "resources": resources, "layers": records, "atom_index_map": mapping, "dependencies": dependency_records, "native_checks": native_checks, "files": [{"path": "input/" + name, "sha256": hashlib.sha256(payload).hexdigest(), "size_bytes": len(payload)} for name, payload in sorted(snapshots.items())]}
        bundle = ResolvedTRIMInputs(intent, chosen, effective, resolved, tuple(sorted(snapshots.items())), tuple(sorted(engine_files.items())))
        if verify_trim_resolution(bundle) != effective:
            raise ValueError("TRIM resolution failed independent replay")
        return bundle
    except BaseException as error:
        error.add_note("TRIM resolution retained explicit versions: " + json.dumps(published, sort_keys=True))
        error.add_note("TRIM resolution retained dependency entries: " + json.dumps(completed_entries, sort_keys=True))
        error.add_note(f"Dependency evidence remains under {paths.simulation_memory} and {worker.path}")
        raise


def verify_trim_resolution(inputs: ResolvedTRIMInputs) -> JSONObject:
    """Independently reconstruct inputs' explicit request solely from pinned bytes.

    Validate schemas, source edits, reference/hash linkage, material/energy
    precedence, installed fixed fields, CAB contributions/arithmetic, native
    byte files and complete inventories. Return copied effective request. Perform
    no filesystem reads/writes, alias lookup or native operations. Raise ValueError
    for unsafe, duplicate, absent or inconsistent evidence; do not mutate inputs.
    """
    try:
        if not isinstance(inputs, ResolvedTRIMInputs):
            raise ValueError("Expected ResolvedTRIMInputs")
        intent = deepcopy(inputs.request)
        chosen = deepcopy(inputs.selections)
        resolved = deepcopy(inputs.resolved)
        validate_trim_request(intent)
        if not isinstance(resolved, dict) or set(resolved) != {"schema_version", "engine", "installation", "defaults", "resources", "layers", "atom_index_map", "dependencies", "native_checks", "files"} or type(resolved["schema_version"]) is not int or resolved["schema_version"] != 1 or resolved["engine"] != "trim":
            raise ValueError("Invalid resolved TRIM schema")
        snapshots: dict[str, bytes] = {}
        for pair in inputs.snapshots:
            if not isinstance(pair, tuple) or len(pair) != 2 or not isinstance(pair[0], str) or not isinstance(pair[1], bytes):
                raise ValueError("Invalid TRIM snapshot pair")
            name, payload = pair
            path = PurePosixPath(name)
            if path.is_absolute() or name != path.as_posix() or len(path.parts) < 2 or path.parts[0] not in ("resources", "installation", "engine", "dependencies") or any(part in (".", "..") or ":" in part or "\\" in part or "\x00" in part for part in path.parts) or name in snapshots:
                raise ValueError("Unsafe/duplicate TRIM snapshot path")
            snapshots[name] = payload
        manifests = [{"path": "input/" + name, "sha256": hashlib.sha256(payload).hexdigest(), "size_bytes": len(payload)} for name, payload in sorted(snapshots.items())]
        if resolved["files"] != manifests:
            raise ValueError("TRIM snapshot inventory does not match exact bytes")
        required_installation = ("TRIM.exe", "TRIM.IN", "Data/TRIM.cfg", "Data/VERSION")
        expected_names = {"installation/" + name for name in (*required_installation, "Data/SCOEF03.dat")} | {"engine/TRIM.IN"}
        if not expected_names <= set(snapshots):
            raise ValueError("Missing installed/native TRIM snapshots")
        try:
            version = snapshots["installation/Data/VERSION"].decode("ascii").splitlines()[0].strip()
            family = re.fullmatch(r"SRIM-(2008|2013)\.[0-9]+", version)
            lines = snapshots["installation/TRIM.IN"].decode("cp1252").splitlines()
            ion = lines[2].split()
            calculation = lines[4].split()
            stopping = [line for line in lines if line.strip()][-1].split()
            if family is None or len(ion) != 7 or len(calculation) != 3 or len(stopping) != 1 or not snapshots["installation/TRIM.exe"]:
                raise ValueError("Invalid preserved installed TRIM input/version")
            _decimal(ion[5], "installed.legacy_bragg")
            native = {"legacy_bragg": ion[5], "reminders": int(calculation[2]), "stopping_version": int(stopping[0])}
        except (IndexError, ValueError, UnicodeError) as error:
            raise ValueError("Cannot reconstruct preserved TRIM profile") from error
        profile = {"schema_version": 1, "engine_family": family.group(1), "version_text": version, "native_fields": native, "files": [{"path": name, "sha256": hashlib.sha256(snapshots["installation/" + name]).hexdigest(), "size_bytes": len(snapshots["installation/" + name])} for name in required_installation]}
        if resolved["installation"] != profile:
            raise ValueError("Installed TRIM profile differs from source bytes")
        defaults = resolved["defaults"]
        if not isinstance(defaults, dict) or set(defaults) != {"sr", "trim", "dependency_engine"}:
            raise ValueError("Invalid TRIM recorded defaults")
        _validate_document(defaults["sr"], "recorded-generation")
        trim_pointer = defaults["trim"]
        if not isinstance(trim_pointer, dict) or set(trim_pointer) != {"generation", "sha256", "size_bytes"}:
            raise ValueError("Invalid recorded damage generation")
        encoded = (json.dumps(trim_pointer["generation"], indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        if (hashlib.sha256(encoded).hexdigest(), len(encoded)) != (trim_pointer["sha256"], trim_pointer["size_bytes"]):
            raise ValueError("Damage generation hash mismatch")
        generation = defaults["sr"]["generation"]
        damage_generation = trim_pointer["generation"]
        if not isinstance(damage_generation, dict) or set(damage_generation) != {"schema_version", "damage"} or type(damage_generation["schema_version"]) is not int or damage_generation["schema_version"] != 1:
            raise ValueError("Invalid preserved damage pointer")
        _validate_document(damage_generation["damage"], "reference")
        if (damage_generation["damage"]["category"], damage_generation["damage"]["name"]) != ("material", "trim-damage"):
            raise ValueError("Damage pointer identity mismatch")
        roles = {"atomdata", "compound", "electronic", "nuclear", "core", "bond", "damage"}
        uses_compound = any(layer["material"]["source"] == "compound" for layer in intent["parameters"]["layers"])
        if not isinstance(chosen, dict) or set(chosen) != roles or (chosen["compound"] is None) == uses_compound or any(chosen[role] is None for role in roles - {"compound"}) or not isinstance(resolved["resources"], dict) or set(resolved["resources"]) != roles - (set() if uses_compound else {"compound"}):
            raise ValueError("TRIM resource selection inventory mismatch")
        entry_data: dict[str, JSONObject] = {}
        entry_references: dict[str, JSONObject] = {}
        simulation_evidence: dict[str, JSONObject] = {}
        for item in resolved["dependencies"]:
            if not isinstance(item, dict):
                raise ValueError("Invalid dependency inventory row")
            if set(item) == {"entry", "snapshot"}:
                reference = item["entry"]
                _validate_document(reference, "entry-reference")
                name = "dependencies/" + reference["sha256"] + ".json"
                if item["snapshot"] != "input/" + name or name not in snapshots:
                    raise ValueError("Missing dependency snapshot")
                payload = snapshots[name]
                if (hashlib.sha256(payload).hexdigest(), len(payload)) != (reference["sha256"], reference["size_bytes"]):
                    raise ValueError("Dependency snapshot hash mismatch")
                entry = json.loads(payload, object_pairs_hook=_unique_object)
                _validate_entry(entry)
                identity = json.dumps(reference, sort_keys=True)
                if identity in entry_data or entry["kind"] != reference["dataset"]["name"] or entry["entry_id"] != reference["entry_id"] or _dependency_key(entry["dependency"]) != reference["dependency_key"]:
                    raise ValueError("Invalid/duplicate dependency identity")
                entry_data[identity], entry_references[identity] = entry, reference
                expected_names.add(name)
            elif set(item) == {"simulation"}:
                identity = json.dumps(item["simulation"], sort_keys=True)
                if identity in simulation_evidence:
                    raise ValueError("Duplicate native dependency reference")
                simulation_evidence[identity] = item["simulation"]
            else:
                raise ValueError("Invalid dependency inventory fields")
        for entry in entry_data.values():
            if any(json.dumps(ref, sort_keys=True) not in entry_data for ref in entry["dependency"]["entries"]):
                raise ValueError("Missing transitive dependency snapshot")
        material: dict[str, JSONObject] = {}
        strengths_by_kind: dict[str, dict[object, str]] = {}
        removed_by_kind: dict[str, list[str]] = {}
        recovered: dict[str, bool] = {}
        root_references: dict[str, JSONObject] = {}
        for role in sorted(resolved["resources"]):
            selection = chosen[role]
            recorded = resolved["resources"][role]
            _validate_document(recorded, "resolved-role")
            if not isinstance(selection, dict) or ("version" in selection) == ("source" in selection) or set(selection) != ({"version", "overrides", "create_version"} if "version" in selection else {"source", "overrides", "create_version"}) or not isinstance(selection["overrides"], list) or type(selection["create_version"]) is not bool or recorded["overrides"] != selection["overrides"]:
                raise ValueError("Preserved selection schema/edits differ")
            payloads: dict[str, bytes] = {}
            for kind, manifest in (("source", recorded["source"]["snapshot"]), ("effective", recorded["effective"])):
                name = f"resources/{role}/{kind}"
                expected_names.add(name)
                if manifest["path"] != "input/" + name or name not in snapshots or (hashlib.sha256(snapshots[name]).hexdigest(), len(snapshots[name])) != (manifest["sha256"], manifest["size_bytes"]):
                    raise ValueError("Preserved resource snapshot mismatch")
                payloads[kind] = snapshots[name]
            reference = recorded["reference"]
            category = "material" if role in ("atomdata", "compound", "damage") else "engine" if role in ("electronic", "nuclear") else "cab"
            resource_name = "trim-damage" if role == "damage" else role
            if "version" in selection:
                v = selection["version"]
                expected_ref = damage_generation["damage"] if role == "damage" else generation["resources"][role]
                if not isinstance(v, str) or (v != "default" and re.fullmatch(r"v[1-9][0-9]*", v) is None) or reference is None or (v == "default" and reference != expected_ref) or (v != "default" and reference["version"] != v) or recorded["source"]["kind"] != "version" or recorded["source"]["origin"] != reference or (reference["category"], reference["name"]) != (category, resource_name):
                    raise ValueError("Preserved version does not match its pinned selection")
            else:
                source = selection["source"]
                if not isinstance(source, dict) or source.get("kind") not in ("file", "inline") or set(source) != ({"kind", "path"} if source["kind"] == "file" else {"kind", "data"}) or reference is not None or source["kind"] != recorded["source"]["kind"]:
                    raise ValueError("Preserved source does not match selection")
                if source["kind"] == "file":
                    if not isinstance(source["path"], str) or not Path(source["path"]).is_absolute() or source["path"] != recorded["source"]["origin"]:
                        raise ValueError("Preserved source path differs from origin")
                else:
                    data = source["data"]
                    supplied = data.encode("utf-8" if role in ("compound", "core", "bond", "damage") else "ascii") if isinstance(data, str) else (json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8") if isinstance(data, dict) and role in ("compound", "core", "bond", "damage") else None
                    if supplied != payloads["source"] or recorded["source"]["origin"] != "inline":
                        raise ValueError("Inline source differs from pinned bytes")
            view = json.loads(payloads["source"], object_pairs_hook=_unique_object) if role in ("core", "bond") else None
            is_default = isinstance(view, dict) and set(view) == {"schema_version", "dataset", "entries", "values"}
            if is_default:
                if reference is None or view["dataset"] != reference or type(view["schema_version"]) is not int or view["schema_version"] != 1 or not isinstance(view["entries"], list):
                    raise ValueError("Invalid recovered source view")
                values: dict[object, str] = {}
                for ref in view["entries"]:
                    identity = json.dumps(ref, sort_keys=True)
                    if identity not in entry_data or ref["dataset"] != reference:
                        raise ValueError("Recovered source lacks pinned entry")
                    entry = entry_data[identity]
                    identifier = entry["entry_id"]
                    if role == "core":
                        key = int(identifier[1:])
                    else:
                        left, right, order = identifier.split("-")
                        key = (int(left[1:]), int(right[1:]), {"s": "-", "d": "=", "t": "#"}[order])
                    if key in values:
                        raise ValueError("Duplicate recovered source identity")
                    values[key] = entry["value"]
                    root_references[identity] = ref
                if json.loads(_encode_payload(role, values)) != view["values"]:
                    raise ValueError("Recovered source values differ from entry snapshots")
            elif reference is not None and (hashlib.sha256(payloads["source"]).hexdigest(), len(payloads["source"])) != (reference["sha256"], reference["size_bytes"]):
                raise ValueError("Source differs from selected immutable identity")
            if role in ("atomdata", "compound"):
                parsed = parse_atomdata(payloads["source"]) if role == "atomdata" else parse_compounds(payloads["source"])
                edited = apply_material_overrides(role, parsed, selection["overrides"])
                expected = payloads["source"] if not selection["overrides"] else _encode_atomdata(edited) if role == "atomdata" else _encode_compounds(edited)
                material[role] = edited
            elif role == "damage":
                edited = apply_trim_damage_overrides(parse_trim_damage(payloads["source"]), selection["overrides"])
                material[role] = edited
                expected = _encode_trim_damage(edited)
            elif role in ("core", "bond"):
                base = _decode_payload(role, json.dumps(view["values"]).encode("utf-8")) if is_default else _decode_payload(role, payloads["source"])
                values, removed = _apply_cab_overrides(role, base, selection["overrides"])
                strengths_by_kind[role], removed_by_kind[role] = values, removed
                recovered[role] = is_default and not selection["create_version"]
                expected = (json.dumps({"schema_version": 1, "dataset": reference, "overrides": selection["overrides"]}, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8") if recovered[role] else _encode_payload(role, values)
            else:
                from src.srim.resources.electronic import _replace_rows as replace_electronic
                from src.srim.resources.nuclear import _replace_rows as replace_nuclear
                from src.srim.cab.strengths import _atomic_number
                replacements: dict[object, str] = {}
                for operation in selection["overrides"]:
                    if not isinstance(operation, dict) or set(operation) != {"operation", "key", "value"} or operation["operation"] != "set" or not isinstance(operation["value"], str):
                        raise ValueError("Invalid coefficient override")
                    raw = operation["key"]
                    if role == "electronic":
                        key = _atomic_number(raw)
                    else:
                        if not isinstance(raw, list) or len(raw) != 2 or any(type(z) is not int for z in raw) or not 1 <= raw[0] <= raw[1] <= 92:
                            raise ValueError("Invalid coefficient pair")
                        key = tuple(raw)
                    if key in replacements:
                        raise ValueError("Duplicate coefficient override")
                    replacements[key] = operation["value"]
                rows = payloads["source"].splitlines()
                if not rows:
                    raise ValueError("Empty coefficient source")
                expected = replace_electronic(payloads["source"], replacements or {1: rows[0]}) if role == "electronic" else replace_nuclear(payloads["source"], replacements or {(1, 1): rows[0]})
            if expected != payloads["effective"]:
                raise ValueError("Effective resource bytes do not reconstruct")
            if (recorded["published"] is not None) != selection["create_version"] or recorded["base"] != (reference if selection["create_version"] else None):
                raise ValueError("Version publication differs from selection intent")
            if recorded["published"] is not None:
                published = recorded["published"]
                if (published["category"], published["name"], published["sha256"], published["size_bytes"]) != (category, resource_name, hashlib.sha256(expected).hexdigest(), len(expected)):
                    raise ValueError("Published resource identity differs from effective bytes")
        if snapshots["installation/Data/SCOEF03.dat"] != snapshots["resources/electronic/effective"]:
            raise ValueError("TRIM engine coefficients differ from selected resource")
        expected_engine = {name: snapshots["installation/" + name] for name in ("TRIM.exe", "Data/TRIM.cfg", "Data/VERSION", "Data/SCOEF03.dat")}
        if not isinstance(inputs.engine_files, tuple) or len(inputs.engine_files) != 4 or dict(inputs.engine_files) != expected_engine:
            raise ValueError("Pinned TRIM engine files differ from snapshots")
        has_derived = any(layer["correction"]["mode"] == "derived" for layer in intent["parameters"]["layers"])
        dependency_engine = defaults["dependency_engine"]
        if has_derived:
            if not isinstance(dependency_engine, dict) or set(dependency_engine) != {"SRModule.exe", "VERSION"}:
                raise ValueError("Missing SR dependency engine identities")
            for name, identity in dependency_engine.items():
                if not isinstance(identity, dict) or set(identity) != {"sha256", "size_bytes"}:
                    raise ValueError("Invalid SR dependency identity")
                _validate_document({"category": "engine", "name": "dependency", "version": "v1", **identity}, "reference")
        elif dependency_engine is not None:
            raise ValueError("Unexpected SR dependency engine identity")
        if not isinstance(resolved["layers"], list) or len(resolved["layers"]) != len(intent["parameters"]["layers"]):
            raise ValueError("Layer provenance inventory differs from request")
        layers: list[JSONObject] = []
        expected_native_evidence: dict[str, JSONObject] = {}
        for layer_index, (original, recorded) in enumerate(zip(intent["parameters"]["layers"], resolved["layers"], strict=True), 1):
            if not isinstance(recorded, dict) or set(recorded) != {"layer_index", "parameters", "provenance", "correction", "native_dependencies"} or type(recorded["layer_index"]) is not int or recorded["layer_index"] != layer_index:
                raise ValueError("Invalid resolved layer record")
            numeric, provenance = _construct_trim_layer(original, material["atomdata"], material.get("compound"), material["damage"])
            if recorded["provenance"] != provenance:
                raise ValueError("Layer material/damage provenance does not reconstruct")
            composition = [{"atomic_number": component["atomic_number"], "atomic_mass": component["mass_amu"], "atom_count": component["stoichiometry"]} for component in numeric["components"]]
            target = {"bonds": provenance["bonds"]}
            correction = recorded["correction"]
            requested_correction = original["correction"]
            expected_dependencies: list[JSONObject] = []
            _validate_document(correction, "cab-v2")
            if correction["mode"] != requested_correction["mode"]:
                raise ValueError("Layer correction mode differs from request")
            if correction["mode"] == "derived":
                if correction["composition"] != composition or correction["bonds"] != target["bonds"]:
                    raise ValueError("CAB composition/bonds differ from target")
                values: dict[str, dict[object, str]] = {"core": {}, "bond": {}, "bragg": {}}
                references: dict[str, JSONObject] = {}
                omissions: list[JSONObject] = []
                expected_bond_ids = []
                for bond in target["bonds"]:
                    key = next(iter(_normalize_entries("bond", {bond["identifier"]: "0"})))
                    expected_bond_ids.append((f"z{key[0]:03d}-z{key[1]:03d}-" + {"-": "s", "=": "d", "#": "t"}[key[2]], key, bond))
                if [entry["entry_id"] for entry in correction["bond_entries"]] != [item[0] for item in expected_bond_ids]:
                    raise ValueError("Selected bond contributions differ from target bonds")
                for kind, contributions in (("bond", correction["bond_entries"]), ("core", correction["core_entries"])):
                    for index, contribution in enumerate(contributions):
                        if kind == "bond":
                            identifier, key, bond = expected_bond_ids[index]
                        else:
                            identifier = contribution["entry_id"]
                            if not identifier.startswith("z") or not identifier[1:].isdigit():
                                raise ValueError("Invalid core contribution identity")
                            key, bond = int(identifier[1:]), None
                        source_value = strengths_by_kind[kind].get(key)
                        matching_override = None
                        for override_index, operation in enumerate(resolved["resources"][kind]["overrides"]):
                            normalized = next(iter(_normalize_entries(kind, {operation["key"]: "0"})))
                            if normalized == key and operation["operation"] == "set":
                                matching_override = override_index
                        removed = identifier in removed_by_kind[kind]
                        origin = contribution["origin"]
                        entry_reference = contribution["entry_reference"]
                        if entry_reference is not None:
                            _validate_document(entry_reference, "entry-reference")
                            identity = json.dumps(entry_reference, sort_keys=True)
                            if identity not in entry_data:
                                raise ValueError("Missing pinned CAB contribution entry")
                            entry = entry_data[identity]
                            root_references[identity] = entry_reference
                            references[json.dumps(entry_reference, sort_keys=True)] = entry_reference
                        else:
                            entry = None
                        if kind == "bond" and Decimal(bond["count"]) == 0:
                            expected_value, expected_origin = "0", "zero-fallback"
                            omissions.append({"dataset": kind, "entry_id": identifier, "reason": "zero-count"})
                        elif removed or (source_value is None and not recovered[kind]):
                            if kind == "core":
                                if entry is None or entry["kind"] != "bragg" or entry["entry_id"] != identifier:
                                    raise ValueError("Missing core fallback Bragg evidence")
                                if entry_reference not in correction["bragg_entries"]:
                                    raise ValueError("Core fallback must use the effective denominator Bragg entry")
                                expected_value, expected_origin = entry["value"], "bragg-fallback"
                            else:
                                expected_value, expected_origin = "0", "zero-fallback"
                            omissions.append({"dataset": kind, "entry_id": identifier, "reason": "removed-entry" if removed else "missing-user-entry"})
                        elif kind == "bond" and source_value is None and key not in SR_BONDS:
                            expected_value, expected_origin = "0", "zero-fallback"
                            omissions.append({"dataset": kind, "entry_id": identifier, "reason": "unsupported-default-bond"})
                        elif matching_override is not None or not recovered[kind]:
                            expected_value = source_value
                            expected_origin = "override" if matching_override is not None else "dataset"
                            if contribution["snapshot"] != resolved["resources"][kind]["effective"] or contribution["override_index"] != matching_override or entry is not None:
                                raise ValueError("Supplied contribution snapshot/override linkage mismatch")
                        else:
                            selected = resolved["resources"][kind]["reference"]
                            if entry is None or entry_reference["dataset"] != selected or entry["entry_id"] != identifier or entry["kind"] != kind:
                                raise ValueError("Recovered contribution lacks matching immutable dataset evidence")
                            expected_value = entry["value"]
                            expected_origin = "convention" if kind == "core" and key == 1 else "recovered-default"
                            if source_value is not None and Decimal(source_value) != Decimal(expected_value):
                                raise ValueError("Recovered contribution differs from preserved available value")
                        if expected_value is None or Decimal(contribution["value"]) != Decimal(expected_value) or origin != expected_origin:
                            raise ValueError("CAB contribution does not follow selected dataset/fallback policy")
                        if origin not in ("dataset", "override") and (contribution["snapshot"] is not None or contribution["override_index"] is not None):
                            raise ValueError("Calculated/fallback contribution cannot claim a user snapshot")
                        if origin == "zero-fallback" and entry_reference is not None:
                            raise ValueError("Zero fallback cannot claim a calculated entry")
                        values[kind][bond["identifier"] if kind == "bond" else key] = contribution["value"]
                has_effective_bonds = any(Decimal(bond["count"]) > 0 and Decimal(values["bond"][bond["identifier"]]) != 0 for bond in target["bonds"])
                expected_core_ids = [f'z{atom["atomic_number"]:03d}' for atom in composition] if has_effective_bonds else []
                if [entry["entry_id"] for entry in correction["core_entries"]] != expected_core_ids or [entry["entry_id"] for entry in correction["bragg_entries"]] != expected_core_ids:
                    raise ValueError("Core/Bragg contributions differ from required effective composition")
                expected_hashes = {"ATOMDATA": resolved["resources"]["atomdata"]["effective"]["sha256"], "SCOEF03.dat": resolved["resources"]["electronic"]["effective"]["sha256"], "SNUC03.dat": resolved["resources"]["nuclear"]["effective"]["sha256"], "SRModule.exe": dependency_engine["SRModule.exe"]["sha256"], "VERSION": dependency_engine["VERSION"]["sha256"]}
                for entry_reference in correction["bragg_entries"]:
                    _validate_document(entry_reference, "entry-reference")
                    identity = json.dumps(entry_reference, sort_keys=True)
                    if identity not in entry_data:
                        raise ValueError("Missing pinned CAB contribution entry")
                    entry = entry_data[identity]
                    root_references[identity] = entry_reference
                    if entry["kind"] != "bragg" or {item["role"]: item["sha256"] for item in entry["dependency"]["files"]} != expected_hashes:
                        raise ValueError("Main Bragg contribution does not match effective dependencies")
                    values["bragg"][int(entry["entry_id"][1:])] = entry["value"]
                    references[json.dumps(entry_reference, sort_keys=True)] = entry_reference
                calculation = _calculate_cab(composition, target["bonds"], values["core"], values["bond"], values["bragg"])
                if correction["calculation"] != calculation or Decimal(correction["applied_factor"]) != Decimal(calculation["calculated_factor"]) or correction["omissions"] != omissions:
                    raise ValueError("CAB arithmetic/omissions do not reconstruct")
                hydrogen = material["atomdata"]["rows"][0]["isotope_mass_amu"]
                with localcontext() as context:
                    context.prec, context.rounding = CAB_PRECISION, CAB_ROUNDING
                    energy = format(125 * Decimal(hydrogen), "f")
                if correction["reference"] != {"projectile": 1, "mass_amu": hydrogen, "energy_keV": energy, "phase": "gas", "contribution_units": CAB_UNITS}:
                    raise ValueError("CAB reference differs from effective ATOMDATA")
                pending = [references[key] for key in sorted(references)]
                visited: set[str] = set()
                evidence_seen: set[str] = set()
                while pending:
                    reference = pending.pop(0)
                    identity = json.dumps(reference, sort_keys=True)
                    if identity in visited:
                        continue
                    visited.add(identity)
                    _validate_document(reference, "entry-reference")
                    identity = json.dumps(reference, sort_keys=True)
                    if identity not in entry_data:
                        raise ValueError("Missing pinned CAB contribution entry")
                    entry = entry_data[identity]
                    root_references[identity] = reference
                    for evidence in entry["simulations"] + entry["captures"]:
                        key = json.dumps(evidence, sort_keys=True)
                        if key not in evidence_seen:
                            evidence_seen.add(key)
                            expected_dependencies.append(evidence)
                    pending.extend(entry["dependency"]["entries"])
            elif correction != requested_correction:
                raise ValueError("Explicit correction differs from original intent")
            if recorded["native_dependencies"] != expected_dependencies:
                raise ValueError("Layer native dependencies do not reconstruct")
            for evidence in expected_dependencies:
                expected_native_evidence[json.dumps(evidence, sort_keys=True)] = evidence
            numeric["correction_factor"] = format(Decimal(correction["applied_factor"]), "f")
            if numeric != recorded["parameters"]:
                raise ValueError("Resolved numeric layer differs from replay")
            layers.append(numeric)
        if simulation_evidence != expected_native_evidence:
            raise ValueError("Native dependency inventory differs from layer corrections")
        pending = [root_references[key] for key in sorted(root_references)]
        reachable: set[str] = set()
        while pending:
            reference = pending.pop(0)
            key = json.dumps(reference, sort_keys=True)
            if key in reachable:
                continue
            if key not in entry_data:
                raise ValueError("Missing reachable dependency entry")
            reachable.add(key)
            pending.extend(entry_data[key]["dependency"]["entries"])
        if reachable != set(entry_data):
            raise ValueError("Extraneous dependency snapshot inventory")
        projectile = deepcopy(intent["parameters"]["projectile"])
        if projectile["mass_amu"] is None:
            atom = next((row for row in material["atomdata"]["rows"] if row["atomic_number"] == projectile["atomic_number"]), None)
            if atom is None:
                raise ValueError("Missing selected projectile mass")
            projectile["mass_amu"] = atom["isotope_mass_amu"]
        flat_atoms, flat_layers, mapping = flatten_trim_target(layers)
        effective = {"schema_version": 1, "engine": "trim", "parameters": {**{key: deepcopy(value) for key, value in intent["parameters"].items() if key not in ("projectile", "layers")}, "projectile": projectile, "engine_family": profile["engine_family"], "native_fields": profile["native_fields"], "atoms": flat_atoms, "layers": flat_layers}}
        validate_trim_engine_request(effective)
        if resolved["atom_index_map"] != mapping or inputs.effective_request != effective or snapshots["engine/TRIM.IN"] != render_trim_input(effective):
            raise ValueError("Effective TRIM input/index map differs from reconstructed intent")
        event_bytes = render_trim_events(effective)
        if event_bytes is not None:
            expected_names.add("engine/TRIM.DAT")
            if snapshots.get("engine/TRIM.DAT") != event_bytes:
                raise ValueError("Event bytes differ from reconstructed intent")
        if set(snapshots) != expected_names:
            raise ValueError("Unexpected/missing snapshot files")
        native_checks: list[JSONObject] = []
        native_checks.append({"code": "native-bounds", "path": "parameters", "requested": {"layer_count": len(effective["parameters"]["layers"]), "atom_entry_count": len(effective["parameters"]["atoms"]), "total_ions": intent["parameters"]["total_ions"], "random_seed": intent["parameters"]["random_seed"], "autosave_ions": intent["parameters"]["autosave_ions"], "energy_keV": intent["parameters"]["projectile"]["energy_keV"], "angle_degrees": intent["parameters"]["projectile"]["angle_degrees"]}, "detail": "Native acceptance, completion and saved values require execution verification; no speculative numeric/size caps were applied."})
        if intent["parameters"]["autosave_ions"] in (0, 32001, 99999):
            native_checks.append({"code": "autosave-saved-value", "path": "parameters.autosave_ions", "requested": intent["parameters"]["autosave_ions"], "detail": "Short probes saved this input as 32000; disabling autosave is not established."})
        for path, name in [("parameters.name", intent["parameters"]["name"])] + [(f"parameters.layers[{i}].name", layer["name"]) for i, layer in enumerate(intent["parameters"]["layers"], 1)]:
            if '"' in name:
                native_checks.append({"code": "quoted-name", "path": path, "requested": name, "detail": "Embedded quotes were doubled for native serialization; verify the saved native name."})
        for i, event in enumerate(intent["parameters"]["events"] or [], 1):
            native_checks.append({"code": "event-geometry", "path": f"parameters.events[{i}]", "requested": deepcopy(event), "detail": "Native position, species, boundary and direction-vector behavior requires verification; no event relocation or normalization was applied."})
            if len(event["id"]) != 5:
                native_checks.append({"code": "event-identifier", "path": f"parameters.events[{i}].id", "requested": event["id"], "detail": "Five-character identifiers are tested; this identifier is preserved for native evaluation."})
            setup = Decimal(intent["parameters"]["projectile"]["energy_keV"]).as_tuple()
            if Decimal(event["energy_eV"]) > Decimal((setup.sign, setup.digits, setup.exponent + 3)):
                native_checks.append({"code": "event-energy-coverage", "path": f"parameters.events[{i}].energy_eV", "requested": event["energy_eV"], "detail": "Event energy exceeds setup energy used for native stopping precomputation; request preserved unchanged."})
        if intent["parameters"]["events"] is not None:
            native_checks.append({"code": "event-consumption", "path": "parameters.total_ions", "requested": intent["parameters"]["total_ions"], "detail": "Event exhaustion and native count interpretation require execution verification; event count did not replace total_ions."})
        if native_checks != resolved["native_checks"]:
            raise ValueError("Native execution checks differ from original intent")
        return effective
    except (KeyError, IndexError, TypeError, UnicodeError) as error:
        raise ValueError("Malformed TRIM bundle evidence") from error
