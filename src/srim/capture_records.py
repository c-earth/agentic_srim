"""Record CAB GUI captures through the permanent simulation lifecycle."""

import hashlib
import json
from pathlib import Path

from src.backend.config import Config
from src.backend.paths import ProjectPaths
from src.backend.records import CreatedDirectory
from src.backend.resources import ResourceReference, verify_resource
from src.srim.defaults import _validate_default_generation, verify_sr_baseline
from src.srim.inputs import render_sr_input
from src.srim.metadata import JSONObject, _inventory, _read_json, _simulation_path, _write_input_file, finish_simulation, initialize_simulation
from src.srim.storage import preserve_sr_runtime


def initialize_capture_simulation(paths: ProjectPaths, config: Config, created: CreatedDirectory, runtime: Path, defaults: JSONObject, request: JSONObject) -> JSONObject:
    """Initialize paths/config's created capture from runtime/defaults/request.

    Return created sr-capture metadata. Validate defaults, native inventory and
    rendered diagnostic input, preserving exact native bytes, baseline and
    defaults under input/cab before execution. Propagate ValueError/OSError with
    partial record evidence; launch nothing and leave runtime untouched.
    """
    _validate_default_generation(defaults)
    if runtime.name != created.path.name or runtime.parent.name != "sr" or runtime.parent.parent.parent != paths.workers:
        raise ValueError("Capture runtime differs from allocated simulation")
    inventory = _inventory(runtime, runtime)
    expected = {"SR.exe", "SR.exe.manifest", "SR.IN", "Data/ATOMDATA", "Data/Compound.dat", "Data/SCOEF03.dat", "Data/SNUC03.dat", "Data/VERSION"}
    if {entry["path"] for entry in inventory} != expected:
        raise ValueError("Incomplete native capture inventory")
    if (runtime / "SR.IN").read_bytes() != render_sr_input(request):
        raise ValueError("Capture request differs from native input")
    for role, filename in (("atomdata", "ATOMDATA"), ("electronic", "SCOEF03.dat"), ("nuclear", "SNUC03.dat")):
        reference = ResourceReference(**defaults["resources"][role])
        verify_resource(paths, reference)
        contents = (runtime / "Data" / filename).read_bytes()
        if (hashlib.sha256(contents).hexdigest(), len(contents)) != (reference.sha256, reference.size_bytes):
            raise ValueError("Capture inputs differ from selected defaults")
    version = (runtime / "Data" / "VERSION").read_bytes().decode("ascii").splitlines()[0].strip()
    executable_hash = hashlib.sha256((runtime / "SR.exe").read_bytes()).hexdigest()
    engine: JSONObject = {"name": "sr-capture", "version": version, "executable": {"name": "SR.exe", "sha256": executable_hash}}
    resolved: JSONObject = {"schema_version": 1, "parameters": request["parameters"], "sources": [{"id": "sr-gui-executable", "name": "SR.exe", "version": version, "sha256": executable_hash, "citation": None}], "resources": [], "cab": None}
    files = {"cab/native/" + entry["path"]: (runtime / entry["path"]).read_bytes() for entry in inventory}
    files["cab/defaults.json"] = (json.dumps(defaults, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    baseline = _read_json(paths.resources / "baselines" / (str(defaults["baseline_id"]) + ".json"))
    verify_sr_baseline(paths, config, baseline)
    files["cab/baseline.json"] = (json.dumps(baseline, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    manifest = (json.dumps({"schema_version": 1, "files": inventory}, sort_keys=True, indent=2) + "\n").encode("utf-8")
    return initialize_simulation(paths, created, engine, request, resolved, engine_inputs={"runtime.json": manifest}, input_files=files)


def finish_capture_simulation(paths: ProjectPaths, config: Config, simulation_id: str, runtime: Path, failure: JSONObject | None = None) -> JSONObject:
    """Preserve runtime and finish paths/config's simulation_id capture with failure.

    Copy a valid available correction JSON into parsed/result.json. Success
    requires a successful cleaned-up capture manifest. Return terminal metadata;
    propagate validation/preservation/publication failures while retaining the
    worker runtime. failure uses the existing message/exit_code schema or None.
    """
    record = _simulation_path(paths, simulation_id)
    if failure is None:
        manifest = _read_json(runtime / "manifest.json")
        if manifest.get("status") != "succeeded" or manifest.get("cleanup") is not True:
            raise ValueError("Successful capture requires successful GUI cleanup")
    preserve_sr_runtime(paths, simulation_id, runtime)
    correction_path = runtime / "correction.json"
    if correction_path.exists():
        correction = _read_json(correction_path)
        if set(correction) == {"compound", "correction", "process_id", "recorded_at"}:
            _write_input_file(record / "output" / "parsed" / "result.json", correction_path.read_bytes())
    return finish_simulation(paths, config, simulation_id, failure)
