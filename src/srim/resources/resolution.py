"""Resolve shared resource selections and CAB material corrections for SR and TRIM."""

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict
from decimal import Decimal, DecimalException, localcontext
import hashlib
import json
import math
from pathlib import Path
import re
from src.backend.config import Config
from src.backend.paths import ProjectPaths
from src.backend.records import CreatedDirectory
from src.backend.resources import ResourceReference, _hash_file, _unique_object, register_resource, resolve_resource, verify_resource
from src.srim.cab.strengths import _apply_cab_overrides, _decode_payload, _encode_payload, _normalize_entries
from src.srim.cab.cache import CABEntryReference, load_cab_entry
from src.srim.cab.constants import CAB_PRECISION, CAB_ROUNDING, CAB_UNITS, SR_BONDS
from src.srim.resources.materials import _encode_atomdata, _encode_compounds, apply_material_overrides, parse_atomdata, parse_compounds
from src.srim.common.types import JSONObject
from src.srim.sr.models import SRDependencyInputs
from src.srim.common.records import _validate_document
from src.srim.common.files import _read_regular_bytes


def _resolve_selection(paths: ProjectPaths, config: Config, role: str, selection: JSONObject, defaults: JSONObject) -> tuple[JSONObject, dict[str, bytes]]:
    """Snapshot role's selection under paths/config/defaults without publication or recovery.

    Validate version/source/patch modes, preserve source/effective bytes and return
    resolved-role JSON plus safe snapshot bytes. Pin available default entry views
    for edits/freezing. Raise ValueError for invalid data and propagate I/O errors;
    never mutate caller maps or reread a mutable source as a later execution input.
    """
    if role not in ("atomdata", "compound", "electronic", "nuclear", "core", "bond") or not isinstance(selection, dict):
        raise ValueError("Unknown resource selection role")
    if ("version" in selection) == ("source" in selection) or set(selection) != ({"version", "overrides", "create_version"} if "version" in selection else {"source", "overrides", "create_version"}) or not isinstance(selection["overrides"], list) or type(selection["create_version"]) is not bool:
        raise ValueError("Selection requires exactly version or source, overrides and create_version")
    reference: ResourceReference | None = None
    category = "material" if role in ("atomdata", "compound") else "engine" if role in ("electronic", "nuclear") else "cab"
    recovered = False
    if "version" in selection:
        version = selection["version"]
        if not isinstance(version, str) or (version != "default" and re.fullmatch(r"v[1-9][0-9]*", version) is None):
            raise ValueError("Version selection must be default or a numbered vN")
        reference = ResourceReference(**defaults["resources"][role]) if version == "default" else resolve_resource(paths, category, role, version)
        contents = _read_regular_bytes(verify_resource(paths, reference))
        if (hashlib.sha256(contents).hexdigest(), len(contents)) != (reference.sha256, reference.size_bytes):
            raise ValueError("Selected resource changed while snapshotting")
        source_kind, origin = "version", asdict(reference)
        if role in ("core", "bond"):
            descriptor = json.loads(contents, object_pairs_hook=_unique_object)
            recovered = descriptor.get("origin") == "installation"
            if recovered:
                references: list[JSONObject] = []
                values: dict[object, str] = {}
                for entry_path in sorted(verify_resource(paths, reference).parent.glob("entries/*/*.json")):
                    checksum, size = _hash_file(entry_path)
                    entry_reference = CABEntryReference(reference, entry_path.parent.name, entry_path.stem, checksum, size)
                    entry = load_cab_entry(paths, entry_reference)
                    identifier = entry["entry_id"]
                    if role == "core":
                        key = int(identifier[1:])
                    else:
                        left, right, order = identifier.split("-")
                        key = (int(left[1:]), int(right[1:]), {"s": "-", "d": "=", "t": "#"}[order])
                    if key in values:
                        raise ValueError("Duplicate recovered strength identity")
                    values[key] = entry["value"]
                    references.append(asdict(entry_reference))
                view = {"schema_version": 1, "dataset": asdict(reference), "entries": references, "values": json.loads(_encode_payload(role, values))}
                contents = (json.dumps(view, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    else:
        source = selection["source"]
        if not isinstance(source, dict) or source.get("kind") not in ("file", "inline") or set(source) != ({"kind", "path"} if source["kind"] == "file" else {"kind", "data"}):
            raise ValueError("Invalid local resource source")
        source_kind = source["kind"]
        if source_kind == "file":
            if not isinstance(source["path"], str) or not Path(source["path"]).is_absolute():
                raise ValueError("Source file requires an absolute filesystem path")
            origin = source["path"]
            expected = _hash_file(Path(origin))
            contents = _read_regular_bytes(Path(origin))
            if (hashlib.sha256(contents).hexdigest(), len(contents)) != expected:
                raise ValueError("Source file changed while snapshotting")
        else:
            origin = "inline"
            data = source["data"]
            if isinstance(data, str):
                contents = data.encode("utf-8" if role in ("compound", "core", "bond") else "ascii")
            elif isinstance(data, dict) and role in ("compound", "core", "bond"):
                contents = (json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
            else:
                raise ValueError("Inline source has the wrong kind-specific format")
    if role in ("atomdata", "compound"):
        parsed = parse_atomdata(contents) if role == "atomdata" else parse_compounds(contents)
        edited = apply_material_overrides(role, parsed, selection["overrides"])
        effective = contents if not selection["overrides"] else _encode_atomdata(edited) if role == "atomdata" else _encode_compounds(edited)
    elif role in ("core", "bond"):
        view = json.loads(contents, object_pairs_hook=_unique_object) if recovered else None
        base_values = _decode_payload(role, json.dumps(view["values"]).encode("utf-8")) if recovered else _decode_payload(role, contents)
        values, removed = _apply_cab_overrides(role, base_values, selection["overrides"])
        if recovered and not selection["create_version"]:
            effective = (json.dumps({"schema_version": 1, "dataset": asdict(reference), "overrides": selection["overrides"]}, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        else:
            effective = _encode_payload(role, values)
    else:
        from src.srim.resources.electronic import _replace_rows as replace_electronic
        from src.srim.resources.nuclear import _replace_rows as replace_nuclear
        rows = contents.splitlines()
        if not rows:
            raise ValueError("Coefficient table cannot be empty")
        replacements: dict[object, str | bytes] = {}
        for operation in selection["overrides"]:
            if not isinstance(operation, dict) or set(operation) != {"operation", "key", "value"} or operation["operation"] != "set" or not isinstance(operation["value"], str):
                raise ValueError("Coefficient overrides require set with a complete row")
            if role == "electronic":
                from src.srim.cab.strengths import _atomic_number
                key = _atomic_number(operation["key"])
            else:
                raw = operation["key"]
                if not isinstance(raw, list) or len(raw) != 2 or any(type(item) is not int for item in raw) or not 1 <= raw[0] <= raw[1] <= 92:
                    raise ValueError("Nuclear pair keys must be lower-Z first")
                key = tuple(raw)
            if key in replacements:
                raise ValueError("Duplicate coefficient override identity")
            replacements[key] = operation["value"]
        if role == "electronic":
            effective = replace_electronic(contents, replacements or {1: rows[0]})
        else:
            effective = replace_nuclear(contents, replacements or {(1, 1): rows[0]})
    snapshots = {f"resources/{role}/source": contents, f"resources/{role}/effective": effective}
    documents = {name.rsplit("/", 1)[1]: {"path": "input/" + name, "sha256": hashlib.sha256(payload).hexdigest(), "size_bytes": len(payload)} for name, payload in snapshots.items()}
    resolved = {"reference": asdict(reference) if reference is not None else None, "base": asdict(reference) if reference is not None and selection["create_version"] else None, "source": {"kind": source_kind, "origin": origin, "snapshot": documents["source"]}, "overrides": deepcopy(selection["overrides"]), "effective": documents["effective"], "published": None}
    _validate_document(resolved, "resolved-role")
    return resolved, snapshots


def _publish_effective_selection(paths: ProjectPaths, config: Config, role: str, selection: JSONObject, resolved_role: JSONObject, snapshots: Mapping[str, bytes]) -> JSONObject:
    """Publish snapshotted role bytes under paths/config if selection requests create_version.

    Return an independent updated resolved_role; read only preserved snapshots,
    retain available copied-default references and never change aliases. No mutable
    source path is reread. Raise ValueError for inconsistent snapshots/provenance,
    propagate publication failures and retain successful versions on later failures.
    """
    result = deepcopy(resolved_role)
    _validate_document(result, "resolved-role")
    if not selection["create_version"]:
        return result
    source_manifest = result["source"]["snapshot"]
    effective_manifest = result["effective"]
    source = snapshots[source_manifest["path"][6:]]
    effective = snapshots[effective_manifest["path"][6:]]
    for manifest, payload in ((source_manifest, source), (effective_manifest, effective)):
        if (hashlib.sha256(payload).hexdigest(), len(payload)) != (manifest["sha256"], manifest["size_bytes"]):
            raise ValueError("Publication snapshot integrity mismatch")
    copied = []
    if role in ("core", "bond"):
        view = json.loads(source, object_pairs_hook=_unique_object)
        if set(view) == {"schema_version", "dataset", "entries", "values"}:
            copied = view["entries"]
    provenance = {"origin": "user", "source": {"kind": result["source"]["kind"], "origin": result["source"]["origin"], "sha256": source_manifest["sha256"], "size_bytes": source_manifest["size_bytes"]}, "base": result["base"], "changes": result["overrides"], "copied_entries": copied, "baseline_id": None, "procedure": None}
    category = "material" if role in ("atomdata", "compound") else "engine" if role in ("electronic", "nuclear") else "cab"
    result["published"] = asdict(register_resource(paths, config, category, role, None, effective, provenance=provenance))
    return result


def _make_dependency_inputs(defaults: JSONObject, resources: JSONObject, atomdata: JSONObject, engine_files: Mapping[str, bytes]) -> SRDependencyInputs:
    """Copy validated defaults/resources/atomdata/engine_files into a pinned dependency bundle.

    Return SRDependencyInputs without I/O or alias resolution; raise ValueError for
    invalid role schemas, atomic data or a nonexact four-file byte inventory.
    """
    _validate_document(defaults, "recorded-generation")
    if set(engine_files) != {"SRModule.exe", "VERSION", "SCOEF03.dat", "SNUC03.dat"} or any(not isinstance(value, bytes) for value in engine_files.values()):
        raise ValueError("Dependency bundle requires four pinned engine files")
    for role in ("atomdata", "electronic", "nuclear"):
        if role not in resources:
            raise ValueError("Dependency bundle lacks effective material/coefficient inputs")
        _validate_document(resources[role], "resolved-role")
    _encode_atomdata(atomdata)
    effective = {"atomdata": _encode_atomdata(atomdata), "electronic": engine_files["SCOEF03.dat"], "nuclear": engine_files["SNUC03.dat"]}
    for role, contents in effective.items():
        manifest = resources[role]["effective"]
        if (hashlib.sha256(contents).hexdigest(), len(contents)) != (manifest["sha256"], manifest["size_bytes"]):
            raise ValueError("Pinned dependency bytes differ from their effective role evidence")
    return SRDependencyInputs(deepcopy(defaults), deepcopy(resources), deepcopy(atomdata), tuple(sorted(engine_files.items())))


def _calculate_cab(composition: Sequence[JSONObject], bonds: Sequence[JSONObject], cores: Mapping[int, str], strengths: Mapping[str, str], stopping: Mapping[int, str]) -> JSONObject:
    """Return validated CAB totals/factor for composition/bonds and selected cores/strengths/stopping.

    Use a local Decimal context, preserve supplied count scale and require all
    needed positive stopping values. With no effective bonds return exactly 1.0
    and zero totals without consulting missing unused maps. Perform no I/O; raise
    ValueError for invalid quantities, missing contributions or nonpositive factor.
    """
    try:
        if not isinstance(composition, (list, tuple)) or not composition or not isinstance(bonds, (list, tuple)):
            raise ValueError("CAB requires composition and explicit bonds")
        for atom in composition:
            if not isinstance(atom, dict) or set(atom) != {"atomic_number", "atomic_mass", "atom_count"} or type(atom["atomic_number"]) is not int or not 1 <= atom["atomic_number"] <= 92:
                raise ValueError("Invalid CAB composition")
            for field in ("atomic_mass", "atom_count"):
                _normalize_entries("core", {atom["atomic_number"]: atom[field]})
                if Decimal(atom[field]) <= 0:
                    raise ValueError("CAB composition must be positive")
        effective = False
        for bond in bonds:
            if not isinstance(bond, dict) or set(bond) != {"identifier", "count"}:
                raise ValueError("Invalid CAB bond")
            _normalize_entries("bond", {bond["identifier"]: bond["count"]})
            strength = strengths.get(bond["identifier"], "0")
            _normalize_entries("bond", {bond["identifier"]: strength})
            effective |= Decimal(bond["count"]) > 0 and Decimal(strength) != 0
        if not effective:
            return {"method": "cab-weighted-sum", "version": 1, "precision": CAB_PRECISION, "rounding": CAB_ROUNDING, "path": "no-effective-bonds", "intermediate_totals": {"core": "0", "bond": "0", "bragg": "0"}, "calculated_factor": "1.0"}
        totals = {"core": Decimal(0), "bond": Decimal(0), "bragg": Decimal(0)}
        with localcontext() as context:
            context.prec, context.rounding = CAB_PRECISION, CAB_ROUNDING
            for atom in composition:
                z = atom["atomic_number"]
                if z not in cores or z not in stopping:
                    raise ValueError("Missing required CAB core or Bragg contribution")
                _normalize_entries("core", {z: cores[z]})
                _normalize_entries("core", {z: stopping[z]})
                if Decimal(stopping[z]) <= 0:
                    raise ValueError("Bragg stopping must be positive")
                totals["core"] += Decimal(atom["atom_count"]) * Decimal(cores[z])
                totals["bragg"] += Decimal(atom["atom_count"]) * Decimal(stopping[z])
            for bond in bonds:
                totals["bond"] += Decimal(bond["count"]) * Decimal(strengths.get(bond["identifier"], "0"))
            factor = (totals["core"] + totals["bond"]) / totals["bragg"]
            if not factor.is_finite() or factor <= 0:
                raise ValueError("Derived CAB factor must be positive and finite")
        return {"method": "cab-weighted-sum", "version": 1, "precision": CAB_PRECISION, "rounding": CAB_ROUNDING, "path": "weighted-sum", "intermediate_totals": {key: str(value) for key, value in totals.items()}, "calculated_factor": str(factor)}
    except (DecimalException, OverflowError) as error:
        raise ValueError("CAB arithmetic exceeds supported decimal limits") from error


def resolve_material_correction(
    paths: ProjectPaths,
    config: Config,
    worker: CreatedDirectory,
    correction: JSONObject,
    composition: Sequence[JSONObject],
    bonds: Sequence[JSONObject],
    atomdata: JSONObject,
    defaults: JSONObject,
    resources: JSONObject,
    snapshots: Mapping[str, bytes],
    dependency_inputs: SRDependencyInputs | None,
    completed_entries: list[JSONObject],
    *,
    capture_timeout_seconds: float = 30.0,
    dependency_timeout_seconds: float = 60.0,
    progress: Callable[[JSONObject], None] | None = None,
) -> tuple[JSONObject, list[JSONObject]]:
    """Resolve a material correction using pinned inputs and existing CAB machinery.

    paths/config/worker locate dependency records and owned operations. correction,
    composition, bonds, atomdata, defaults, resources and snapshots supply validated
    material intent and immutable sources. dependency_inputs is required for derived
    mode; completed_entries accumulates retained published dependency references.
    Timeouts bound dependency operations. For derived correction, progress emits
    one event per bond and Bragg/core contribution, including cached, resolved,
    fallback and skipped values, reaching the fixed total on successful nonempty
    contribution resolution. None/supplied modes emit no events. Later evidence
    verification can still fail after that total; callback exceptions propagate.
    Return copied effective correction and dependency references. May run/publish
    SR/CAB dependencies and invoke callbacks, but never a main simulation. Preserve
    existing ValueError/OSError/interruption behavior and mutate only the explicit
    completed_entries accumulator.
    """
    for timeout in (capture_timeout_seconds, dependency_timeout_seconds):
        if type(timeout) not in (int, float) or timeout <= 0 or not math.isfinite(timeout):
            raise ValueError("Operation timeouts must be finite and positive")
    if progress is not None and not callable(progress):
        raise ValueError("progress must be callable or None")
    if not isinstance(correction, dict) or correction.get("mode") not in ("none", "supplied", "derived"):
        raise ValueError("Invalid material correction mode")
    if not isinstance(completed_entries, list):
        raise ValueError("completed_entries must be a list")
    correction = deepcopy(correction)
    dependencies: list[JSONObject] = []
    if correction["mode"] != "derived":
        _validate_document(correction, "cab")
        return correction, dependencies
    if set(correction) != {"mode"} or not isinstance(dependency_inputs, SRDependencyInputs):
        raise ValueError("Derived correction requires pinned dependency inputs")
    _validate_document(defaults, "recorded-generation")
    generation = defaults["generation"]
    if correction["mode"] == "derived":
        values_by_kind: dict[str, dict[object, str]] = {}
        recovered: dict[str, bool] = {}
        removed_by_kind: dict[str, list[str]] = {}
        available: dict[str, dict[str, JSONObject]] = {}
        for kind in ("core", "bond"):
            source = json.loads(snapshots[f"resources/{kind}/source"], object_pairs_hook=_unique_object)
            is_default = set(source) == {"schema_version", "dataset", "entries", "values"}
            values = _decode_payload(kind, json.dumps(source["values"]).encode("utf-8")) if is_default else _decode_payload(kind, snapshots[f"resources/{kind}/source"])
            values, removed = _apply_cab_overrides(kind, values, resources[kind]["overrides"])
            values_by_kind[kind] = values
            removed_by_kind[kind] = removed
            recovered[kind] = is_default and resources[kind]["published"] is None
            available[kind] = {entry["entry_id"]: entry for entry in source["entries"]} if recovered[kind] else {}
        bond_entries: list[JSONObject] = []
        core_entries: list[JSONObject] = []
        bragg_entries: list[JSONObject] = []
        omissions: list[JSONObject] = []
        strengths: dict[str, str] = {}
        cores: dict[int, str] = {}
        stopping: dict[int, str] = {}
        all_entries: dict[str, JSONObject] = {}
        total = len(bonds) + 2 * len(composition)
        completed = 0
        for bond in bonds:
            key = next(iter(_normalize_entries("bond", {bond["identifier"]: "0"})))
            identifier = f"z{key[0]:03d}-z{key[1]:03d}-" + {"-": "s", "=": "d", "#": "t"}[key[2]]
            value = values_by_kind["bond"].get(key)
            entry_reference = None
            origin = "dataset"
            state = "resolved"
            override_index = None
            for index, operation in enumerate(resources["bond"]["overrides"]):
                normalized = next(iter(_normalize_entries("bond", {operation["key"]: "0"})))
                if normalized == key and operation["operation"] == "set":
                    origin, override_index = "override", index
            if Decimal(bond["count"]) == 0:
                value, origin = "0", "zero-fallback"
                omissions.append({"dataset": "bond", "entry_id": identifier, "reason": "zero-count"})
            elif identifier in removed_by_kind["bond"]:
                value, origin = "0", "zero-fallback"
                omissions.append({"dataset": "bond", "entry_id": identifier, "reason": "removed-entry"})
            elif value is None and recovered["bond"] and key in SR_BONDS:
                if resources["bond"]["reference"] != generation["resources"]["bond"]:
                    raise ValueError("An inactive default bond version cannot recover missing entries")
                from src.srim.cab.recovery import resolve_default_bond
                result = resolve_default_bond(paths, config, worker, generation, key, capture_timeout_seconds=capture_timeout_seconds, dependency_timeout_seconds=dependency_timeout_seconds)
                entry_reference = asdict(result.reference)
                completed_entries.append(entry_reference)
                entry = load_cab_entry(paths, result.reference)
                value, origin = entry["value"], "recovered-default"
                state = "calculated" if result.calculated else "cached"
            elif value is None:
                value, origin = "0", "zero-fallback"
                omissions.append({"dataset": "bond", "entry_id": identifier, "reason": "unsupported-default-bond" if recovered["bond"] else "missing-user-entry"})
            elif origin == "dataset" and identifier in available["bond"]:
                entry_reference, origin = available["bond"][identifier], "recovered-default"
                state = "cached"
            if origin == "zero-fallback":
                state = "fallback"
            if entry_reference is not None:
                all_entries[json.dumps(entry_reference, sort_keys=True)] = entry_reference
            strengths[bond["identifier"]] = value
            bond_entries.append({"entry_id": identifier, "value": value, "origin": origin, "entry_reference": entry_reference, "snapshot": resources["bond"]["effective"] if origin in ("dataset", "override") else None, "override_index": override_index if origin == "override" else None})
            completed += 1
            if progress is not None:
                progress({"kind": "bond", "entry_id": identifier, "state": state, "completed": completed, "total": total})
        if any(Decimal(bond["count"]) > 0 and Decimal(strengths[bond["identifier"]]) != 0 for bond in bonds):
            from src.srim.cab.bragg import resolve_bragg
            for atom in composition:
                z = atom["atomic_number"]
                identifier = f"z{z:03d}"
                result = resolve_bragg(paths, config, worker, dependency_inputs, z, timeout_seconds=dependency_timeout_seconds)
                entry_reference = asdict(result.reference)
                completed_entries.append(entry_reference)
                entry = load_cab_entry(paths, result.reference)
                stopping[z] = entry["value"]
                bragg_entries.append(entry_reference)
                all_entries[json.dumps(entry_reference, sort_keys=True)] = entry_reference
                completed += 1
                if progress is not None:
                    progress({"kind": "bragg", "entry_id": identifier, "state": "calculated" if result.calculated else "cached", "completed": completed, "total": total})
                value = values_by_kind["core"].get(z)
                entry_reference, origin, override_index = None, "dataset", None
                state = "resolved"
                for index, operation in enumerate(resources["core"]["overrides"]):
                    normalized = next(iter(_normalize_entries("core", {operation["key"]: "0"})))
                    if normalized == z and operation["operation"] == "set":
                        origin, override_index = "override", index
                if identifier in removed_by_kind["core"] or (value is None and not recovered["core"]):
                    value, origin, entry_reference = stopping[z], "bragg-fallback", bragg_entries[-1]
                    state = "fallback"
                    omissions.append({"dataset": "core", "entry_id": identifier, "reason": "removed-entry" if identifier in removed_by_kind["core"] else "missing-user-entry"})
                elif value is None:
                    if resources["core"]["reference"] != generation["resources"]["core"]:
                        raise ValueError("An inactive default core version cannot recover missing entries")
                    from src.srim.cab.recovery import resolve_default_core
                    result = resolve_default_core(paths, config, worker, generation, z, capture_timeout_seconds=capture_timeout_seconds, dependency_timeout_seconds=dependency_timeout_seconds)
                    entry_reference = asdict(result.reference)
                    completed_entries.append(entry_reference)
                    value, origin = load_cab_entry(paths, result.reference)["value"], "recovered-default"
                    state = "calculated" if result.calculated else "cached"
                elif origin == "dataset" and identifier in available["core"]:
                    entry_reference, origin = available["core"][identifier], "convention" if z == 1 else "recovered-default"
                    state = "cached"
                if entry_reference is not None:
                    all_entries[json.dumps(entry_reference, sort_keys=True)] = entry_reference
                cores[z] = value
                core_entries.append({"entry_id": identifier, "value": value, "origin": origin, "entry_reference": entry_reference, "snapshot": resources["core"]["effective"] if origin in ("dataset", "override") else None, "override_index": override_index if origin == "override" else None})
                completed += 1
                if progress is not None:
                    progress({"kind": "core", "entry_id": identifier, "state": state, "completed": completed, "total": total})
        else:
            for atom in composition:
                for kind in ("bragg", "core"):
                    completed += 1
                    if progress is not None:
                        progress({"kind": kind, "entry_id": f'z{atom["atomic_number"]:03d}', "state": "skipped", "completed": completed, "total": total})
        calculation = _calculate_cab(composition, bonds, cores, strengths, stopping)
        with localcontext() as context:
            context.prec, context.rounding = CAB_PRECISION, CAB_ROUNDING
            hydrogen = atomdata["rows"][0]["isotope_mass_amu"]
            energy = format(125 * Decimal(hydrogen), "f")
        correction = {"mode": "derived", "applied_factor": calculation["calculated_factor"], "core_entries": core_entries, "bond_entries": bond_entries, "bragg_entries": bragg_entries, "composition": composition, "bonds": bonds, "omissions": omissions, "reference": {"projectile": 1, "mass_amu": hydrogen, "energy_keV": energy, "phase": "gas", "contribution_units": CAB_UNITS}, "calculation": calculation}
        pending = [all_entries[key] for key in sorted(all_entries)]
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
                    dependencies.append(evidence)
            pending.extend(entry["dependency"]["entries"])
    return correction, dependencies
