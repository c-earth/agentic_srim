"""Project sealed native simulation inputs and resolve pinned evidence bytes."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import hashlib
import json

from src.backend.paths import ProjectPaths
from src.knowledge.schema import convert_quantity, validate_search_document
from src.srim.inputs import render_sr_input, validate_sr_engine_request
from src.srim.metadata import JSONObject, _inventory, _read_json, _simulation_path, read_simulation
from src.srim.runtime import _read_regular_bytes
from src.srim.trim_inputs import MODES, render_trim_events, render_trim_input, validate_trim_engine_request
from src.srim.trim_outputs import compare_trim_saved_values, parse_trim_checkpoint
from src.srim.trim_resolution import ResolvedTRIMInputs


def project_simulation_inputs(paths: ProjectPaths, simulation_id: str) -> JSONObject:
    """Return verified input-only search projection for paths' simulation_id.

    Args:
        paths: Existing agreed absolute project directory layout.
        simulation_id: Canonical terminal SR/TRIM record identity.

    Returns:
        Independent SearchRecord with exact input evidence and lazy file references.

    Side effects:
        Read permanent evidence; existing metadata readers may create lock files.
        Never read output contents, installations or mutable resource defaults.

    Raises:
        ValueError: Record is ineligible, unsupported, changing or fails replay.
        OSError: A source file/path is missing, unsafe or unreadable.
    """
    record = _simulation_path(paths, simulation_id)
    metadata = read_simulation(paths, simulation_id)
    metadata_bytes = _read_regular_bytes(record / "metadata.json")
    if json.loads(metadata_bytes) != metadata:
        raise ValueError("stale: metadata changed during read")
    engine = metadata["engine"]["name"]
    if engine not in ("sr", "trim") or metadata["status"] not in ("succeeded", "failed"):
        raise ValueError("ineligible: only terminal SR/TRIM records are searchable")
    if _inventory(record / "input", record) != metadata["inputs"]:
        raise ValueError("integrity: preserved input inventory changed")
    names = {item["path"] for item in metadata["inputs"]}
    effective_name = "input/effective-request.json" if "input/effective-request.json" in names else "input/request.json"
    effective = _read_json(record / effective_name)
    if engine == "sr":
        validate_sr_engine_request(effective)
    else:
        validate_trim_engine_request(effective)
    p = deepcopy(effective["parameters"])
    native_name = "input/engine/SR.IN" if engine == "sr" else "input/engine/TRIM.IN"
    if effective_name not in names or native_name not in names:
        raise ValueError("unsupported: missing preserved effective/native inputs")
    control_name: str | None = None
    event_name = "input/engine/TRIM.DAT"
    if engine == "sr":
        validate_sr_engine_request(effective)
        if render_sr_input(effective) != _read_regular_bytes(record / native_name):
            raise ValueError("conflicting: saved SR effective request does not reproduce SR.IN")
        layers = [{"index": 1, "label": None, "phase": p["target"]["phase"], "thickness_angstrom": None, "density_g_cm3": p["target"]["density_g_cm3"], "components": [{"atomic_number": item["atomic_number"], "mass_amu": item["mass_amu"], "atomic_proportion": item["stoichiometry"]} for item in p["target"]["elements"]]}]
        energy = {"kind": p["energy"]["mode"], "values_keV": p["energy"].get("values_keV", []), "minimum_keV": p["energy"].get("minimum_keV"), "maximum_keV": p["energy"].get("maximum_keV")}
        inputs = {"engine": "sr", "engine_family": None, "mode": p["energy"]["mode"], "mode_code": None, "projectile": p["projectile"], "energy": energy, "target_label": None, "layers": layers, "incidence_angle_degrees": None, "total_ions": None, "random_seed": None, "events": None}
    else:
        validate_trim_engine_request(effective)
        if render_trim_input(effective) != _read_regular_bytes(record / native_name):
            raise ValueError("conflicting: saved TRIM effective request does not reproduce TRIM.IN")
        if p["events"] is not None and (event_name not in names or render_trim_events(effective) != _read_regular_bytes(record / event_name)):
            raise ValueError("conflicting: saved event input differs from effective events")
        if metadata["continuation"] is not None:
            control_name = "input/execution/control.json"
            if control_name not in names:
                raise ValueError("missing: continuation control")
            control = _read_json(record / control_name)
            if control.get("kind") != "resume" or control.get("parent_id") != metadata["continuation"]["parent_id"] or type(control.get("total_ions")) is not int:
                raise ValueError("conflicting: continuation identity/count")
            checkpoint_names = {name.casefold(): name for name in names if name.startswith("input/continuation/checkpoint/")}
            saved_name = checkpoint_names.get("input/continuation/checkpoint/tdata.sav")
            trim_name = checkpoint_names.get("input/continuation/checkpoint/trimin.sav")
            if saved_name is None or trim_name is None:
                raise ValueError("missing: continuation textual checkpoint")
            checkpoint = parse_trim_checkpoint(_read_regular_bytes(record / saved_name), _read_regular_bytes(record / trim_name))
            resolved = _read_json(record / "input/resolved.json")
            if any(item["path"] not in names for item in resolved["files"]):
                raise ValueError("integrity: continuation snapshot escapes input inventory")
            snapshots = tuple((item["path"].removeprefix("input/"), _read_regular_bytes(record / item["path"])) for item in resolved["files"] if item["path"] in names)
            mapping = dict(snapshots)
            bundle = ResolvedTRIMInputs(_read_json(record / "input/request.json"), _read_json(record / "input/selections.json"), effective, resolved, snapshots, tuple(sorted((name, mapping["installation/" + name]) for name in ("TRIM.exe", "Data/TRIM.cfg", "Data/VERSION", "Data/SCOEF03.dat"))))
            if compare_trim_saved_values(checkpoint, bundle, resumed=True)["differences"] or checkpoint["counts"]["total"] != control["total_ions"] or checkpoint["counts"]["autosave"] != control["autosave_ions"] or checkpoint["counts"]["current"] != control["initial_count"]:
                raise ValueError("conflicting: continuation checkpoint/control values")
            p["total_ions"] = control["total_ions"]
            p["autosave_ions"] = control["autosave_ions"]
            if p["events"] is not None:
                event_name = checkpoint_names.get("input/continuation/checkpoint/trim.dat")
                if event_name is None:
                    raise ValueError("missing: continuation event input")
                payload = _read_regular_bytes(record / event_name)
                events = []
                for line in payload.removesuffix(b"\x1a").decode("ascii").splitlines()[10:]:
                    tokens = line.split()
                    if len(tokens) != 9:
                        raise ValueError("malformed: continuation event row")
                    events.append({"id": tokens[0], "atomic_number": int(tokens[1]), "energy_eV": tokens[2], "position_angstrom": tokens[3:6], "direction_cosines": tokens[6:9]})
                p["events"] = events
                candidate = deepcopy(effective)
                candidate["parameters"] = p
                prefix = render_trim_events(effective).removesuffix(b"\x1a")
                if not payload.startswith(prefix) or render_trim_events(candidate) != payload:
                    raise ValueError("conflicting: continuation event prefix or rendering")
        layers = [{"index": index, "label": layer["name"], "phase": layer["phase"], "thickness_angstrom": layer["thickness_angstrom"], "density_g_cm3": layer["density_g_cm3"], "components": [{"atomic_number": atom["atomic_number"], "mass_amu": atom["mass_amu"], "atomic_proportion": amount} for atom, amount in zip(p["atoms"], layer["stoichiometry"], strict=True) if Decimal(amount) > 0]} for index, layer in enumerate(p["layers"], 1)]
        events = None if p["events"] is None else [{"id": event["id"], "atomic_number": event["atomic_number"], "energy_keV": convert_quantity(event["energy_eV"], "eV", "keV")} for event in p["events"]]
        projectile = {"atomic_number": p["projectile"]["atomic_number"], "mass_amu": p["projectile"]["mass_amu"]} if events is None else None
        values = [p["projectile"]["energy_keV"]] if events is None else [event["energy_keV"] for event in events]
        inputs = {"engine": "trim", "engine_family": p["engine_family"], "mode": p["mode"], "mode_code": MODES[p["engine_family"]][p["mode"]], "projectile": projectile, "energy": {"kind": "single" if events is None else "list", "values_keV": values, "minimum_keV": None, "maximum_keV": None}, "target_label": p["name"], "layers": layers, "incidence_angle_degrees": p["projectile"]["angle_degrees"] if events is None else None, "total_ions": p["total_ions"], "random_seed": p["random_seed"], "events": events}
    reference = {"kind": "simulation", "id": simulation_id, "metadata_sha256": hashlib.sha256(metadata_bytes).hexdigest(), "input_inventory_sha256": hashlib.sha256(json.dumps(sorted(metadata["inputs"], key=lambda item: item["path"]), sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()}
    files = [{"owner": deepcopy(reference), "path": item["path"], "sha256": item["sha256"], "size_bytes": item["size_bytes"], "role": role} for role, inventory in (("input", metadata["inputs"]), ("output", metadata["outputs"])) for item in inventory]
    files.sort(key=lambda item: item["path"])
    by_name = {file["path"]: file for file in files}
    observations = []
    pointers = {"mode": "/parameters/energy/mode" if engine == "sr" else "/parameters/mode", "projectile": "/parameters/projectile", "energy": "/parameters/energy" if engine == "sr" else "/parameters/projectile/energy_keV", "target_label": "/parameters/name", "layers": "/parameters/target" if engine == "sr" else "/parameters/layers", "incidence_angle_degrees": "/parameters/projectile/angle_degrees", "total_ions": "/parameters/total_ions", "random_seed": "/parameters/random_seed", "events": "/parameters/events", "engine_family": "/parameters/engine_family"}
    for field, value in inputs.items():
        if value is None or field not in pointers:
            continue
        source_name = control_name if control_name and field == "total_ions" else effective_name
        pointer = "/total_ions" if source_name == control_name else pointers[field]
        evidence = [{"file": by_name[source_name], "json_pointer": pointer}, {"file": by_name[native_name], "json_pointer": None}]
        if inputs["events"] is not None and field in ("events", "energy"):
            evidence = [{"file": by_name[event_name], "json_pointer": None}]
        observations.append({"field": field, "value": deepcopy(value), "unit": {"energy": "keV", "incidence_angle_degrees": "deg"}.get(field), "evidence": evidence})
    result = {"schema_version": 1, "reference": reference, "origin": "native", "execution_status": metadata["status"], "inputs": inputs, "observations": observations, "files": files, "issues": []}
    if _read_regular_bytes(record / "metadata.json") != metadata_bytes or _inventory(record / "input", record) != metadata["inputs"]:
        raise ValueError("stale: simulation changed during projection")
    validate_search_document(result, "record")
    return result


def read_source_record(paths: ProjectPaths, reference: JSONObject) -> JSONObject:
    """Return paths' source record only when reference still pins its exact evidence.

    Args:
        paths: Existing absolute project layout.
        reference: Native SourceReference with metadata and inventory hashes.

    Returns:
        Independent verified SearchRecord; no output contents are read.

    Raises:
        ValueError: Reference is malformed, stale or fails input replay.
        OSError: Evidence is missing, unsafe or unreadable. Only lock files may be created.
    """
    validate_search_document(reference, "reference")
    result = project_simulation_inputs(paths, reference["id"])
    if result["reference"] != reference:
        raise ValueError("stale: source reference no longer matches native evidence")
    return result


def read_source_file(paths: ProjectPaths, reference: JSONObject, file: JSONObject, *, max_bytes: int = 67108864) -> bytes:
    """Return selected verified file bytes for paths, pinned reference and file.

    Args:
        paths: Existing absolute project directory layout.
        reference: Pinned native owner identity.
        file: Exact inventoried FileReference owned by reference.
        max_bytes: Positive integer maximum file size, default 64 MiB.

    Returns:
        Exact bounded bytes without parsing or writing source data.

    Raises:
        ValueError: Owner/file is invalid, stale, oversized or changed.
        OSError: A file/path is missing, unsafe or unreadable; lock files may be created.
    """
    validate_search_document(file, "file")
    if type(max_bytes) is not int or max_bytes < 1 or file["owner"] != reference:
        raise ValueError("file: invalid owner or size bound")
    owner = read_source_record(paths, reference)
    if file not in owner["files"] or file["size_bytes"] > max_bytes:
        raise ValueError("file: not inventoried or exceeds byte bound")
    path = _simulation_path(paths, reference["id"]) / file["path"]
    if path.stat().st_size > max_bytes:
        raise ValueError("file: current size exceeds byte bound")
    contents = _read_regular_bytes(path)
    if len(contents) != file["size_bytes"] or hashlib.sha256(contents).hexdigest() != file["sha256"]:
        raise ValueError("integrity: selected file bytes changed")
    return contents
