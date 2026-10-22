"""Resolve and verify pinned SR requests using shared resource and CAB resolution."""

from collections.abc import Callable, Mapping
from copy import deepcopy
from decimal import Decimal, localcontext
import hashlib
import json
import math
from pathlib import Path
import re
from src.backend.config import Config
from src.backend.paths import ProjectPaths
from src.backend.records import CreatedDirectory
from src.backend.resources import ResourceReference, _read_json, _unique_object, verify_resource
from src.srim.cab.strengths import _apply_cab_overrides, _decode_payload, _encode_payload, _normalize_entries
from src.srim.cab.cache import CABEntryReference, load_cab_entry
from src.srim.cab.constants import CAB_PRECISION, CAB_ROUNDING, CAB_UNITS, SR_BONDS
from src.srim.resources.defaults import read_sr_defaults
from src.srim.resources.compatibility import read_module_version
from src.srim.sr.inputs import validate_sr_engine_request, validate_sr_request
from src.srim.resources.materials import _encode_atomdata, _encode_compounds, apply_material_overrides, construct_sr_target, parse_atomdata, parse_compounds
from src.srim.common.types import JSONObject
from src.srim.sr.models import ResolvedSRInputs
from src.srim.common.records import _validate_document, _validate_input_files
from src.srim.common.files import _read_regular_bytes
from src.srim.resources.resolution import _calculate_cab, _make_dependency_inputs, _publish_effective_selection, _resolve_selection, resolve_material_correction


def resolve_sr_inputs(paths: ProjectPaths, config: Config, worker: CreatedDirectory, request: JSONObject, selections: JSONObject, *, capture_timeout_seconds: float = 30.0, dependency_timeout_seconds: float = 60.0, progress: Callable[[JSONObject], None] | None = None) -> ResolvedSRInputs:
    """Resolve request/selections with paths/config/worker into a pinned, replayable input bundle.

    Capture/dependency timeouts bound owned runs; progress reports successful
    dependency completion and propagates callback errors. Snapshot defaults once,
    validate all selections/target before explicit publication, then reuse matching
    results and derive only missing contributions. Never run the main simulation.
    Return independent JSON/byte inventories; propagate failures with retained
    dependency/published-version evidence and clean up owned interrupted processes.
    """
    validate_sr_request(request)
    if request["schema_version"] != 2:
        raise ValueError("Resource resolution requires version-two intent; legacy inputs use low-level preparation")
    for timeout in (capture_timeout_seconds, dependency_timeout_seconds):
        if type(timeout) not in (int, float) or timeout <= 0 or not math.isfinite(timeout):
            raise ValueError("Operation timeouts must be finite and positive")
    if progress is not None and not callable(progress):
        raise ValueError("progress must be callable or None")
    if not isinstance(worker, CreatedDirectory) or worker.kind != "worker" or worker.path != paths.workers / f"worker_{worker.suffix}" or not worker.path.is_dir():
        raise ValueError("Expected an allocated worker")
    intent = deepcopy(request)
    chosen = deepcopy(selections)
    roles = {"atomdata", "electronic", "nuclear", "core", "bond", "compound"}
    if not isinstance(chosen, dict) or set(chosen) != roles or (chosen["compound"] is None) != (intent["parameters"]["target"]["source"] == "direct"):
        raise ValueError("Selections must name all six roles and match target source")
    generation = read_sr_defaults(paths)
    pointer = (json.dumps(generation, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    defaults = {"generation": generation, "sha256": hashlib.sha256(pointer).hexdigest(), "size_bytes": len(pointer)}
    resources: JSONObject = {}
    snapshots: dict[str, bytes] = {}
    published: list[JSONObject] = []
    completed_entries: list[JSONObject] = []
    try:
        for role in sorted(roles):
            if chosen[role] is None:
                continue
            resources[role], contents = _resolve_selection(paths, config, role, chosen[role], generation)
            snapshots.update(contents)
        atoms = apply_material_overrides("atomdata", parse_atomdata(snapshots["resources/atomdata/source"]), chosen["atomdata"]["overrides"])
        compounds = apply_material_overrides("compound", parse_compounds(snapshots["resources/compound/source"]), chosen["compound"]["overrides"]) if chosen["compound"] is not None else None
        target_intent = deepcopy(intent["parameters"]["target"])
        if target_intent["source"] == "direct" and "bonds" not in target_intent:
            target_intent["bonds"] = {}
        target = construct_sr_target(target_intent, atoms, compounds)
        engine_files = {"SRModule.exe": _read_regular_bytes(config.srim_root / "SR Module" / "SRModule.exe"), "VERSION": read_module_version(config.srim_root)[1], "SCOEF03.dat": snapshots["resources/electronic/effective"], "SNUC03.dat": snapshots["resources/nuclear/effective"]}
        version_lines = engine_files["VERSION"].decode("ascii").splitlines()
        if not version_lines or not version_lines[0].strip():
            raise ValueError("Module VERSION requires a nonblank first line")
        version = version_lines[0].strip()
        for role in sorted(resources):
            resources[role] = _publish_effective_selection(paths, config, role, chosen[role], resources[role], snapshots)
            if resources[role]["published"] is not None:
                published.append(resources[role]["published"])
        sources = [{"id": "sr-module-executable", "name": "SRModule.exe", "version": version, "sha256": hashlib.sha256(engine_files["SRModule.exe"]).hexdigest(), "citation": None}, {"id": "sr-module-version", "name": "VERSION", "version": version, "sha256": hashlib.sha256(engine_files["VERSION"]).hexdigest(), "citation": None}]
        dependency_inputs = _make_dependency_inputs(defaults, resources, atoms, engine_files)
        composition = [{"atomic_number": element["atomic_number"], "atomic_mass": element["mass_amu"], "atom_count": element["stoichiometry"]} for element in target["target"]["elements"]]
        correction = deepcopy(intent["parameters"]["correction"])
        dependencies: list[JSONObject] = []
        correction, dependencies = resolve_material_correction(
            paths, config, worker, correction, composition, target["bonds"], atoms,
            defaults, resources, snapshots, dependency_inputs, completed_entries,
            capture_timeout_seconds=capture_timeout_seconds,
            dependency_timeout_seconds=dependency_timeout_seconds, progress=progress,
        )
        effective_request = {"schema_version": 2, "parameters": {**deepcopy(intent["parameters"]), "target": target["target"], "correction": {"mode": "derived", "applied_factor": format(Decimal(correction["applied_factor"]), "f")} if correction["mode"] == "derived" else deepcopy(correction)}}
        validate_sr_engine_request(effective_request)
        resolved = {"schema_version": 2, "parameters": effective_request["parameters"], "sources": sources, "resources": resources, "cab": correction, "defaults": defaults, "target_provenance": target["provenance"], "dependencies": dependencies}
        _validate_document(resolved, "resolved")
        bundle = ResolvedSRInputs(intent, chosen, effective_request, resolved, tuple(sorted(snapshots.items())), tuple(sorted(engine_files.items())))
        if verify_sr_resolution(paths, intent, chosen, resolved, snapshots) != effective_request:
            raise ValueError("Resolution failed independent reconstruction")
        return bundle
    except BaseException as error:
        error.add_note("Resolution retained explicit versions: " + json.dumps(published, sort_keys=True))
        error.add_note("Resolution completed entries retained: " + json.dumps(completed_entries, sort_keys=True))
        error.add_note(f"Dependency evidence retained under {paths.simulation_memory} and {worker.path}")
        raise


def verify_sr_resolution(paths: ProjectPaths, request: JSONObject, selections: JSONObject, resolved: JSONObject, snapshots: Mapping[str, bytes]) -> JSONObject:
    """Reconstruct request/selections/resolved from preserved snapshots in paths without processes.

    Verify concrete resources, original/effective source linkage, immutable entry
    evidence and arithmetic; return the validated effective engine request. Never
    reread mutable source paths or default aliases, calculate missing entries or
    modify inputs. Raise ValueError for inconsistent evidence and propagate I/O errors.
    """
    validate_sr_request(request)
    _validate_document(resolved, "resolved")
    if request["schema_version"] != 2 or resolved["schema_version"] != 2:
        raise ValueError("Resolution reconstruction requires version-two records")
    snapshots = _validate_input_files(snapshots)
    roles = {"atomdata", "electronic", "nuclear", "core", "bond", "compound"}
    if not isinstance(selections, dict) or set(selections) != roles:
        raise ValueError("Invalid preserved resource selection roles")
    required = roles - ({"compound"} if request["parameters"]["target"]["source"] == "direct" else set())
    if set(resolved["resources"]) != required or (selections["compound"] is None) != ("compound" not in required):
        raise ValueError("Resolved roles differ from target selection")
    generation = resolved["defaults"]["generation"]
    material: dict[str, JSONObject] = {}
    strengths_by_kind: dict[str, dict[object, str]] = {}
    removed_by_kind: dict[str, list[str]] = {}
    recovered: dict[str, bool] = {}
    recovered_refs: dict[str, dict[str, JSONObject]] = {}
    for role in sorted(required):
        selection = selections[role]
        recorded = resolved["resources"][role]
        if not isinstance(selection, dict) or ("version" in selection) == ("source" in selection) or set(selection) != ({"version", "overrides", "create_version"} if "version" in selection else {"source", "overrides", "create_version"}) or not isinstance(selection["overrides"], list) or type(selection["create_version"]) is not bool or selection["overrides"] != recorded["overrides"]:
            raise ValueError("Preserved selection schema or edits differ from resolution")
        payloads: dict[str, bytes] = {}
        for name, manifest in (("source", recorded["source"]["snapshot"]), ("effective", recorded["effective"])):
            relative = manifest["path"][6:]
            if not relative.startswith(f"resources/{role}/") or relative not in snapshots:
                raise ValueError("Missing role snapshot")
            contents = snapshots[relative]
            if (hashlib.sha256(contents).hexdigest(), len(contents)) != (manifest["sha256"], manifest["size_bytes"]):
                raise ValueError("Role snapshot integrity mismatch")
            payloads[name] = contents
        reference = recorded["reference"]
        if "version" in selection:
            version = selection["version"]
            if not isinstance(version, str) or (version != "default" and re.fullmatch(r"v[1-9][0-9]*", version) is None) or reference is None or recorded["source"]["kind"] != "version" or recorded["source"]["origin"] != reference:
                raise ValueError("Preserved version reference mismatch")
            if (version == "default" and reference != generation["resources"][role]) or (version != "default" and reference["version"] != version):
                raise ValueError("Selection differs from its pinned concrete version")
            registered = _read_regular_bytes(verify_resource(paths, ResourceReference(**reference)))
        else:
            source = selection["source"]
            if not isinstance(source, dict) or source.get("kind") not in ("file", "inline") or set(source) != ({"kind", "path"} if source["kind"] == "file" else {"kind", "data"}) or reference is not None or source["kind"] != recorded["source"]["kind"]:
                raise ValueError("Preserved source selection mismatch")
            if source["kind"] == "file":
                if not isinstance(source["path"], str) or not Path(source["path"]).is_absolute() or source["path"] != recorded["source"]["origin"]:
                    raise ValueError("Preserved source path differs from origin")
            else:
                data = source["data"]
                supplied = data.encode("utf-8" if role in ("compound", "core", "bond") else "ascii") if isinstance(data, str) else (json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8") if isinstance(data, dict) and role in ("compound", "core", "bond") else None
                if supplied != payloads["source"]:
                    raise ValueError("Preserved inline content differs from source snapshot")
            registered = None
        source_view = json.loads(payloads["source"], object_pairs_hook=_unique_object) if role in ("core", "bond") else None
        default_view = isinstance(source_view, dict) and set(source_view) == {"schema_version", "dataset", "entries", "values"}
        if default_view:
            if reference is None or source_view["dataset"] != reference or type(source_view["schema_version"]) is not int or source_view["schema_version"] != 1 or not isinstance(source_view["entries"], list):
                raise ValueError("Invalid preserved recovered-default base view")
            descriptor = json.loads(registered, object_pairs_hook=_unique_object)
            if descriptor.get("origin") != "installation" or descriptor.get("kind") != role:
                raise ValueError("Base view is not a recovered installation dataset")
            values: dict[object, str] = {}
            refs: dict[str, JSONObject] = {}
            for entry_reference in source_view["entries"]:
                _validate_document(entry_reference, "entry-reference")
                if entry_reference["dataset"] != reference or entry_reference["entry_id"] in refs:
                    raise ValueError("Invalid/duplicate recovered base-view entry")
                entry = load_cab_entry(paths, CABEntryReference(ResourceReference(**reference), entry_reference["dependency_key"], entry_reference["entry_id"], entry_reference["sha256"], entry_reference["size_bytes"]))
                identifier = entry["entry_id"]
                if role == "core":
                    key = int(identifier[1:])
                else:
                    left, right, order = identifier.split("-")
                    key = (int(left[1:]), int(right[1:]), {"s": "-", "d": "=", "t": "#"}[order])
                values[key] = entry["value"]
                refs[identifier] = entry_reference
            if json.loads(_encode_payload(role, values)) != source_view["values"]:
                raise ValueError("Recovered base-view values differ from pinned entries")
            recovered_refs[role] = refs
        elif registered is not None and registered != payloads["source"]:
            raise ValueError("Source snapshot differs from immutable selected bytes")
        if role in ("atomdata", "compound"):
            parsed = parse_atomdata(payloads["source"]) if role == "atomdata" else parse_compounds(payloads["source"])
            edited = apply_material_overrides(role, parsed, selection["overrides"])
            expected = payloads["source"] if not selection["overrides"] else _encode_atomdata(edited) if role == "atomdata" else _encode_compounds(edited)
            material[role] = edited
        elif role in ("core", "bond"):
            base = _decode_payload(role, json.dumps(source_view["values"]).encode("utf-8")) if default_view else _decode_payload(role, payloads["source"])
            values, removed = _apply_cab_overrides(role, base, selection["overrides"])
            strengths_by_kind[role], removed_by_kind[role] = values, removed
            recovered[role] = default_view and not selection["create_version"]
            expected = (json.dumps({"schema_version": 1, "dataset": reference, "overrides": selection["overrides"]}, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8") if recovered[role] else _encode_payload(role, values)
        else:
            from src.srim.resources.electronic import _replace_rows as replace_electronic
            from src.srim.resources.nuclear import _replace_rows as replace_nuclear
            replacements: dict[object, str] = {}
            for operation in selection["overrides"]:
                if not isinstance(operation, dict) or set(operation) != {"operation", "key", "value"} or operation["operation"] != "set" or not isinstance(operation["value"], str):
                    raise ValueError("Invalid preserved coefficient edit")
                if role == "electronic":
                    from src.srim.cab.strengths import _atomic_number
                    key = _atomic_number(operation["key"])
                else:
                    raw = operation["key"]
                    if not isinstance(raw, list) or len(raw) != 2 or any(type(item) is not int for item in raw) or not 1 <= raw[0] <= raw[1] <= 92:
                        raise ValueError("Invalid preserved ordered nuclear key")
                    key = tuple(raw)
                if key in replacements:
                    raise ValueError("Duplicate preserved coefficient edit")
                replacements[key] = operation["value"]
            rows = payloads["source"].splitlines()
            if not rows:
                raise ValueError("Empty preserved coefficient table")
            expected = replace_electronic(payloads["source"], replacements or {1: rows[0]}) if role == "electronic" else replace_nuclear(payloads["source"], replacements or {(1, 1): rows[0]})
        if expected != payloads["effective"]:
            raise ValueError("Effective snapshot does not reconstruct from preserved source and edits")
        if (recorded["published"] is not None) != selection["create_version"] or recorded["base"] != (reference if selection["create_version"] else None):
            raise ValueError("Version publication differs from requested selection")
        if recorded["published"] is not None:
            published_path = verify_resource(paths, ResourceReference(**recorded["published"]))
            if _read_regular_bytes(published_path) != expected:
                raise ValueError("Published variant differs from effective snapshot")
            provenance = _read_json(published_path.parent / "manifest.json")["provenance"]
            if provenance["changes"] != recorded["overrides"] or provenance["base"] != recorded["base"]:
                raise ValueError("Published variant provenance mismatch")
    target_intent = deepcopy(request["parameters"]["target"])
    if target_intent["source"] == "direct" and "bonds" not in target_intent:
        target_intent["bonds"] = {}
    target = construct_sr_target(target_intent, material["atomdata"], material.get("compound"))
    if resolved["target_provenance"] != target["provenance"]:
        raise ValueError("Target provenance does not reconstruct from effective material")
    correction = resolved["cab"]
    requested_correction = request["parameters"]["correction"]
    if correction["mode"] != requested_correction["mode"]:
        raise ValueError("Correction mode differs from original intent")
    expected_dependencies: list[JSONObject] = []
    if correction["mode"] == "derived":
        composition = [{"atomic_number": element["atomic_number"], "atomic_mass": element["mass_amu"], "atom_count": element["stoichiometry"]} for element in target["target"]["elements"]]
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
                    entry = load_cab_entry(paths, CABEntryReference(ResourceReference(**entry_reference["dataset"]), entry_reference["dependency_key"], entry_reference["entry_id"], entry_reference["sha256"], entry_reference["size_bytes"]))
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
        expected_hashes = {"ATOMDATA": resolved["resources"]["atomdata"]["effective"]["sha256"], "SCOEF03.dat": resolved["resources"]["electronic"]["effective"]["sha256"], "SNUC03.dat": resolved["resources"]["nuclear"]["effective"]["sha256"], "SRModule.exe": resolved["sources"][0]["sha256"], "VERSION": resolved["sources"][1]["sha256"]}
        for entry_reference in correction["bragg_entries"]:
            entry = load_cab_entry(paths, CABEntryReference(ResourceReference(**entry_reference["dataset"]), entry_reference["dependency_key"], entry_reference["entry_id"], entry_reference["sha256"], entry_reference["size_bytes"]))
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
            entry = load_cab_entry(paths, CABEntryReference(ResourceReference(**reference["dataset"]), reference["dependency_key"], reference["entry_id"], reference["sha256"], reference["size_bytes"]))
            for evidence in entry["simulations"] + entry["captures"]:
                key = json.dumps(evidence, sort_keys=True)
                if key not in evidence_seen:
                    evidence_seen.add(key)
                    expected_dependencies.append(evidence)
            pending.extend(entry["dependency"]["entries"])
    elif correction != requested_correction:
        raise ValueError("Explicit correction differs from original intent")
    if resolved["dependencies"] != expected_dependencies:
        raise ValueError("Dependency evidence inventory differs from selected immutable entries")
    effective = {"schema_version": 2, "parameters": {**deepcopy(request["parameters"]), "target": target["target"], "correction": {"mode": "derived", "applied_factor": format(Decimal(correction["applied_factor"]), "f")} if correction["mode"] == "derived" else deepcopy(correction)}}
    validate_sr_engine_request(effective)
    if resolved["parameters"] != effective["parameters"]:
        raise ValueError("Resolved numeric parameters do not reconstruct from original intent")
    return effective
