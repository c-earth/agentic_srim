"""Stage independently verified fresh and continued TRIM simulations from pinned bytes."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
import re
from pathlib import Path
from tempfile import mkdtemp

from src.backend.config import Config
from src.backend.paths import ProjectPaths
from src.backend.records import CreatedDirectory, _lock_worker
from src.srim.metadata import JSONObject, _collect_resource_references, _inventory, _read_json, _simulation_path, _write_input_file, initialize_simulation, read_simulation
from src.srim.runtime import _read_regular_bytes, _validate_preparation_allocations
from src.srim.trim_inputs import render_trim_events, validate_trim_request
from src.srim.trim_outputs import parse_trim_checkpoint
from src.srim.trim_resolution import ResolvedTRIMInputs, verify_trim_resolution


def load_trim_input_bundle(source: Path) -> ResolvedTRIMInputs:
    """Load absolute source's exported manifest, replay it and return copied inputs.

    Verify exact safe inventories and hashes without writes or installed aliases.
    Raise ValueError for malformed/tampered bundles and OSError for unsafe I/O.
    """
    if not isinstance(source, Path) or not source.is_absolute():
        raise ValueError("TRIM bundle source must be absolute")
    manifest = _read_json(source / "manifest.json")
    if set(manifest) != {"schema_version", "engine", "files"} or manifest["schema_version"] != 1 or manifest["engine"] != "trim":
        raise ValueError("Invalid TRIM bundle manifest")
    actual = [entry for entry in _inventory(source, source) if entry["path"] != "manifest.json"]
    if actual != manifest["files"]:
        raise ValueError("TRIM bundle inventory differs")
    docs = {name: _read_json(source / "input" / f"{name}.json") for name in ("request", "selections", "effective-request", "resolved")}
    allowed = {item["path"] for item in actual}
    declared = docs["resolved"].get("files")
    if not isinstance(declared, list) or any(not isinstance(item, dict) or not isinstance(item.get("path"), str) or item["path"] not in allowed or not item["path"].startswith("input/") for item in declared):
        raise ValueError("Unsafe/missing TRIM snapshot identity")
    if allowed != {item["path"] for item in declared} | {"input/" + name + ".json" for name in docs}:
        raise ValueError("Unexpected TRIM bundle files")
    snapshots = tuple((item["path"].removeprefix("input/"), _read_regular_bytes(source / item["path"])) for item in docs["resolved"]["files"])
    snapshot_map = dict(snapshots)
    bundle = ResolvedTRIMInputs(docs["request"], docs["selections"], docs["effective-request"], docs["resolved"], snapshots, tuple(sorted((name, snapshot_map["installation/" + name]) for name in ("TRIM.exe", "Data/TRIM.cfg", "Data/VERSION", "Data/SCOEF03.dat"))))
    verify_trim_resolution(bundle)
    return deepcopy(bundle)


def _trim_runtime_manifest(files: Mapping[str, bytes]) -> JSONObject:
    """Return sorted initial runtime hashes for safe relative files without effects.

    Require fresh numerical files and optional complete checkpoint descendants.
    Raise ValueError for invalid names, collisions or nonbyte payloads.
    """
    from src.srim.trim_outputs import _trim_source
    required = {"TRIM.exe", "TRIM.IN", "TRIMAUTO", "Data/TRIM.cfg", "Data/VERSION", "Data/SCOEF03.dat"}
    if not isinstance(files, Mapping) or not required <= set(files):
        raise ValueError("Incomplete TRIM runtime")
    seen: set[str] = set()
    entries: list[JSONObject] = []
    for name, payload in sorted(files.items()):
        item = _trim_source(name, payload)
        if name.casefold() in seen or any(name.casefold().startswith(previous + "/") or previous.startswith(name.casefold() + "/") for previous in seen) or (name not in required | {"TRIM.DAT"} and not name.startswith("checkpoint/")):
            raise ValueError("Unexpected/colliding TRIM runtime path")
        seen.add(name.casefold())
        entries.append(item)
    return {"schema_version": 1, "files": entries}


def prepare_trim_simulation(paths: ProjectPaths, config: Config, created: CreatedDirectory, worker: CreatedDirectory, inputs: ResolvedTRIMInputs) -> Path:
    """Verify inputs and stage created's fresh simulation on allocated worker.

    Return created runtime Path; never run native code or resolve live resources.
    Raise ValueError/OSError for invalid allocation/evidence or staging failures,
    retaining partial artifacts with exception notes. Caller data is copied.
    """
    return _stage_trim_runtime(paths, config, created, worker, deepcopy(inputs), None, {})


def _stage_trim_runtime(paths: ProjectPaths, config: Config, created: CreatedDirectory, worker: CreatedDirectory, inputs: ResolvedTRIMInputs, continuation: JSONObject | None, checkpoint_files: Mapping[str, bytes]) -> Path:
    """Stage pinned inputs/control/checkpoint_files for allocations under a lease.

    Return exclusively published runtime and initialized created record.
    continuation is None for fresh; otherwise contains sealed parent evidence.
    Run no native code; retain partial files on ValueError/OSError/interruption.
    """
    simulation_id = _validate_preparation_allocations(paths, created, worker)
    effective = verify_trim_resolution(inputs)
    if type(config.hide_trim_window) is not bool:
        raise ValueError("hide_trim_window must be boolean")
    files = dict(inputs.engine_files)
    files["TRIM.IN"] = dict(inputs.snapshots)["engine/TRIM.IN"]
    if "engine/TRIM.DAT" in dict(inputs.snapshots):
        files["TRIM.DAT"] = dict(inputs.snapshots)["engine/TRIM.DAT"]
    control: JSONObject = {"schema_version": 1, "engine": "trim", "kind": "fresh", "parent_id": None, "initial_count": 0, "total_ions": effective["parameters"]["total_ions"], "autosave_ions": effective["parameters"]["autosave_ions"], "event_prefix_sha256": None, "edits": []}
    files["TRIMAUTO"] = b"1\r\n\r\n"
    if continuation is None and checkpoint_files:
        raise ValueError("Fresh simulations cannot import checkpoint files")
    if continuation is not None:
        if not checkpoint_files:
            raise ValueError("Continuation requires complete checkpoint files")
        control = deepcopy(continuation["control"])
        files["TRIMAUTO"] = b"2\r\ncheckpoint\\\r\n"
        files.update({"checkpoint/" + name: data for name, data in checkpoint_files.items() if name != "TRIM.DAT"})
        if "TRIM.DAT" in checkpoint_files:
            files["TRIM.DAT"] = checkpoint_files["TRIM.DAT"]
    manifest = _trim_runtime_manifest(files)
    runtime = worker.path / "trim" / simulation_id
    staging: Path | None = None
    with _lock_worker(paths, worker.path):
        _validate_preparation_allocations(paths, created, worker)
        if runtime.exists():
            raise FileExistsError(str(runtime))
        staging = Path(mkdtemp(prefix=".trim-", dir=runtime.parent))
        try:
            for name, data in files.items():
                target = staging / name
                target.parent.mkdir(parents=True, exist_ok=True)
                _write_input_file(target, data)
            for name in ("SRIM Outputs", "SRIM Restore"):
                (staging / name).mkdir()
            if _inventory(staging, staging) != manifest["files"]:
                raise ValueError("Staged TRIM runtime differs")
            evidence = {name: data for name, data in inputs.snapshots if not name.startswith("engine/")}
            for name, doc in (("effective-request.json", inputs.effective_request), ("selections.json", inputs.selections), ("execution/runtime.json", manifest), ("execution/control.json", control)):
                evidence[name] = (json.dumps(doc, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
            continuation_metadata = None
            if continuation is not None:
                evidence["continuation/parent.json"] = (json.dumps(continuation["parent"], sort_keys=True, indent=2) + "\n").encode("utf-8")
                evidence.update({"continuation/checkpoint/" + name: data for name, data in checkpoint_files.items()})
                continuation_metadata = {"parent_id": control["parent_id"], "files": [{"path": "input/" + name, "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)} for name, data in sorted(evidence.items()) if name.startswith("continuation/")]}
            native = {name.removeprefix("engine/"): data for name, data in inputs.snapshots if name.startswith("engine/")}
            native["TRIMAUTO"] = files["TRIMAUTO"]
            profile = inputs.resolved["installation"]
            engine = {"name": "trim", "version": profile["version_text"], "executable": {"name": "TRIM.exe", "sha256": hashlib.sha256(files["TRIM.exe"]).hexdigest()}}
            initialize_simulation(paths, created, engine, inputs.request, inputs.resolved, continuation_metadata, engine_inputs=native, input_files=evidence)
            staging.rename(runtime)
            return runtime
        except BaseException as error:
            error.add_note(f"TRIM staging retained: {staging}; record: {created.path}; runtime: {runtime}")
            raise


def verify_trim_runtime(paths: ProjectPaths, simulation_id: str, runtime: Path) -> ResolvedTRIMInputs:
    """Verify prepared matching runtime/record and return replayed pinned inputs.

    Require created state, sealed inputs, exact staged files and empty outputs.
    Read no installations/aliases. Raise ValueError/OSError for unsafe ownership,
    tampering, missing or unexpected files; perform no writes.
    """
    record = _simulation_path(paths, simulation_id)
    if not isinstance(runtime, Path) or not runtime.is_absolute() or runtime != paths.workers / runtime.parent.parent.name / "trim" / simulation_id or not re.fullmatch(r"worker_[1-9][0-9]*", runtime.parent.parent.name):
        raise ValueError("Invalid TRIM runtime ownership")
    metadata = read_simulation(paths, simulation_id)
    if metadata["status"] != "created" or metadata["engine"]["name"] != "trim" or _inventory(record / "input", record) != metadata["inputs"]:
        raise ValueError("TRIM record is not sealed and prepared")
    docs = {name: _read_json(record / "input" / f"{name}.json") for name in ("request", "selections", "effective-request", "resolved")}
    allowed = {item["path"] for item in metadata["inputs"]}
    if any(item["path"] not in allowed for item in docs["resolved"]["files"]):
        raise ValueError("TRIM snapshots escape sealed input inventory")
    snapshots = tuple((item["path"].removeprefix("input/"), _read_regular_bytes(record / item["path"])) for item in docs["resolved"]["files"])
    mapping = dict(snapshots)
    bundle = ResolvedTRIMInputs(docs["request"], docs["selections"], docs["effective-request"], docs["resolved"], snapshots, tuple(sorted((name, mapping["installation/" + name]) for name in ("TRIM.exe", "Data/TRIM.cfg", "Data/VERSION", "Data/SCOEF03.dat"))))
    verify_trim_resolution(bundle)
    expected_engine = {"name": "trim", "version": bundle.resolved["installation"]["version_text"], "executable": {"name": "TRIM.exe", "sha256": hashlib.sha256(dict(bundle.engine_files)["TRIM.exe"]).hexdigest()}}
    if metadata["engine"] != expected_engine or metadata["resources"] != _collect_resource_references(bundle.resolved):
        raise ValueError("TRIM metadata engine/resource identity differs from pinned bytes")
    manifest = _read_json(record / "input/execution/runtime.json")
    if set(manifest) != {"schema_version", "files"} or type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1 or not isinstance(manifest["files"], list) or any(not isinstance(item, dict) or set(item) != {"path", "sha256", "size_bytes"} or not isinstance(item["path"], str) or not isinstance(item["sha256"], str) or re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) is None or type(item["size_bytes"]) is not int or item["size_bytes"] < 0 for item in manifest["files"]):
        raise ValueError("Invalid TRIM runtime manifest")
    _trim_runtime_manifest({item["path"]: b"" for item in manifest["files"]})
    files = {item["path"]: _read_regular_bytes(runtime / item["path"]) for item in manifest["files"]}
    if _trim_runtime_manifest(files) != manifest or _inventory(runtime, runtime) != manifest["files"]:
        raise ValueError("TRIM runtime bytes differ from manifest")
    expected = dict(bundle.engine_files)
    expected["TRIM.IN"] = mapping["engine/TRIM.IN"]
    control = _read_json(record / "input/execution/control.json")
    control_keys = {"schema_version", "engine", "kind", "parent_id", "initial_count", "total_ions", "autosave_ions", "event_prefix_sha256", "edits"}
    if set(control) != control_keys or type(control["schema_version"]) is not int or control["schema_version"] != 1 or control["engine"] != "trim" or control["kind"] not in ("fresh", "resume") or any(type(control[key]) is not int for key in ("initial_count", "total_ions", "autosave_ions")) or not 0 <= control["initial_count"] < control["total_ions"] or not isinstance(control["edits"], list):
        raise ValueError("Invalid TRIM execution controls")
    if control["event_prefix_sha256"] is not None and (not isinstance(control["event_prefix_sha256"], str) or re.fullmatch(r"[0-9a-f]{64}", control["event_prefix_sha256"]) is None):
        raise ValueError("Invalid continuation event prefix hash")
    if control["kind"] == "fresh" and (control["parent_id"] is not None or control["initial_count"] != 0 or control["edits"] or control["event_prefix_sha256"] is not None or metadata["continuation"] is not None or control["total_ions"] != bundle.effective_request["parameters"]["total_ions"] or control["autosave_ions"] != bundle.effective_request["parameters"]["autosave_ions"]):
        raise ValueError("Fresh TRIM controls differ from resolved inputs")
    expected["TRIMAUTO"] = b"1\r\n\r\n" if control["kind"] == "fresh" else b"2\r\ncheckpoint\\\r\n"
    if control["kind"] == "fresh" and "engine/TRIM.DAT" in mapping:
        expected["TRIM.DAT"] = mapping["engine/TRIM.DAT"]
    if any(files.get(name) != data for name, data in expected.items()):
        raise ValueError("Runtime differs from pinned TRIM bytes")
    if control["kind"] == "fresh" and set(files) != set(expected):
        raise ValueError("Fresh runtime contains extra staged files")
    if control["kind"] == "resume":
        if metadata["continuation"] is None or metadata["continuation"]["parent_id"] != control["parent_id"]:
            raise ValueError("Invalid continuation control identity")
        expected_names = set(expected)
        for item in metadata["continuation"]["files"]:
            relative = item["path"].removeprefix("input/continuation/checkpoint/")
            if relative == item["path"]:
                continue
            actual = files.get("TRIM.DAT" if relative == "TRIM.DAT" else "checkpoint/" + relative)
            expected_names.add("TRIM.DAT" if relative == "TRIM.DAT" else "checkpoint/" + relative)
            if actual != _read_regular_bytes(record / item["path"]):
                raise ValueError("Continuation checkpoint differs")
        if set(files) != expected_names:
            raise ValueError("Continuation runtime contains extra staged files")
        checkpoint_names = {name.casefold(): name for name in files if name.startswith("checkpoint/")}
        if not {"checkpoint/tdata.sav", "checkpoint/trimin.sav"} <= set(checkpoint_names):
            raise ValueError("Continuation lacks textual checkpoint identity")
        saved = parse_trim_checkpoint(files[checkpoint_names["checkpoint/tdata.sav"]], files[checkpoint_names["checkpoint/trimin.sav"]])
        if saved["counts"]["current"] != control["initial_count"] or saved["counts"]["total"] != control["total_ions"] or saved["counts"]["autosave"] != control["autosave_ions"]:
            raise ValueError("Continuation controls differ from saved native counts")
    if _inventory(record / "output", record) or set(p.name for p in (record / "output").iterdir()) != {"raw", "parsed"}:
        raise ValueError("TRIM outputs must be empty before execution")
    directories = {p.relative_to(runtime).as_posix() for p in runtime.rglob("*") if p.is_dir()}
    allowed = {"Data", "SRIM Outputs", "SRIM Restore"} | {str(Path(name).parent).replace("\\", "/") for name in files if "/" in name}
    if directories != allowed:
        raise ValueError("Unexpected TRIM runtime directories")
    return deepcopy(bundle)


def prepare_trim_continuation(paths: ProjectPaths, config: Config, created: CreatedDirectory, worker: CreatedDirectory, parent_id: str, *, total_ions: int, autosave_ions: int, events: list[JSONObject] | None = None) -> Path:
    """Prepare a new record continuing parent_id to explicit total/autosave counts.

    events is a list of structured appended-prefix-preserving events for event
    modes, otherwise None. Preserve complete terminal parent's bytes and physics.
    Return new runtime; reject ineligible/incomplete/changed checkpoints with
    ValueError, propagate safe I/O/lease errors and never modify the parent.
    """
    parent = _simulation_path(paths, parent_id)
    metadata = read_simulation(paths, parent_id)
    if metadata["engine"]["name"] != "trim" or metadata["status"] not in ("succeeded", "failed"):
        raise ValueError("Continuation requires a terminal TRIM parent")
    if _inventory(parent / "input", parent) != metadata["inputs"] or _inventory(parent / "output", parent) != metadata["outputs"]:
        raise ValueError("Parent record has changed")
    if metadata["status"] == "failed":
        execution = _read_json(parent / "output/raw/execution.json")
        if execution["failure"]["code"] not in ("timeout", "popup", "cancelled"):
            raise ValueError("Failed parent is not eligible for continuation")
    resolved = _read_json(parent / "input/resolved.json")
    if any(item["path"] not in {entry["path"] for entry in metadata["inputs"]} for item in resolved["files"]):
        raise ValueError("Parent snapshots escape sealed input inventory")
    snapshots = tuple((item["path"].removeprefix("input/"), _read_regular_bytes(parent / item["path"])) for item in resolved["files"])
    mapping = dict(snapshots)
    bundle = ResolvedTRIMInputs(_read_json(parent / "input/request.json"), _read_json(parent / "input/selections.json"), _read_json(parent / "input/effective-request.json"), resolved, snapshots, tuple(sorted((name, mapping["installation/" + name]) for name in ("TRIM.exe", "Data/TRIM.cfg", "Data/VERSION", "Data/SCOEF03.dat"))))
    verify_trim_resolution(bundle)
    saved = parent / "output/raw/runtime/SRIM Restore"
    checkpoint_files = {item["path"]: _read_regular_bytes(saved / item["path"]) for item in _inventory(saved, saved)}
    required = {"e2recoil.sav", "ioniz.sav", "novac.sav", "phonon.sav", "sputter.sav", "stopping.sav", "tdata.sav", "trimin.sav", "vacancy.sav", "xrange.sav", "yzradial#.sav", "yzrange#.sav"}
    if bundle.effective_request["parameters"]["engine_family"] == "2013":
        required |= {"ioniz3d.sav", "novac3d.sav", "phonon3d.sav", "range3d.sav", "recoil3d.sav", "vac3d.sav"}
    names = {name.casefold(): name for name in checkpoint_files}
    if not required <= set(names):
        raise ValueError("Parent has an incomplete saved-state set")
    data_name = names["tdata.sav"]
    saved_state = parse_trim_checkpoint(checkpoint_files[data_name], checkpoint_files[names["trimin.sav"]])
    if type(total_ions) is not int or total_ions <= saved_state["counts"]["current"] or type(autosave_ions) is not int:
        raise ValueError("Explicit total must exceed saved count; autosave must be an integer")
    from src.srim.trim_outputs import compare_trim_saved_values
    if compare_trim_saved_values(saved_state, bundle, resumed=True)["differences"]:
        raise ValueError("Parent checkpoint changed immutable requested values")
    original = checkpoint_files[data_name]
    lines = original.splitlines(keepends=True)
    index = saved_state["count_line"]["line"] - 1
    before = lines[index]
    tokens = before.split()
    tokens[1:3] = [str(total_ions).encode("ascii"), str(autosave_ions).encode("ascii")]
    lines[index] = b" ".join(tokens) + (b"\r\n" if before.endswith(b"\r\n") else b"\n")
    checkpoint_files[data_name] = b"".join(lines)
    edits = [{"path": "checkpoint/" + data_name, "field": "total_ions/autosave_ions", "before": before.decode("cp1252"), "after": lines[index].decode("cp1252"), "source_sha256": hashlib.sha256(original).hexdigest(), "result_sha256": hashlib.sha256(checkpoint_files[data_name]).hexdigest()}]
    prefix_hash = None
    original_events = bundle.request["parameters"]["events"]
    if original_events is not None:
        original_data = _read_regular_bytes(parent / "output/raw/runtime/TRIM.DAT")
        event_lines = original_data.removesuffix(b"\x1a").splitlines()
        if not isinstance(events, list) or len(events) < len(event_lines) - 10:
            raise ValueError("Event continuation requires complete preserved event prefix")
        candidate = deepcopy(bundle.request)
        candidate["parameters"]["events"] = deepcopy(events)
        candidate["parameters"]["total_ions"] = total_ions
        validate_trim_request(candidate)
        explicit = deepcopy(bundle.effective_request)
        explicit["parameters"]["events"] = deepcopy(events)
        explicit["parameters"]["total_ions"] = total_ions
        data = render_trim_events(explicit)
        new_lines = data.removesuffix(b"\x1a").splitlines()
        if new_lines[:len(event_lines)] != event_lines or len(new_lines) <= len(event_lines):
            raise ValueError("Event continuation must only append preserved records")
        checkpoint_files["TRIM.DAT"] = data
        prefix_hash = hashlib.sha256(original_data).hexdigest()
        edits.append({"path": "TRIM.DAT", "field": "events", "before": len(event_lines) - 10, "after": len(events), "source_sha256": prefix_hash, "result_sha256": hashlib.sha256(data).hexdigest()})
    elif events is not None:
        raise ValueError("Ordinary modes do not accept events")
    control = {"schema_version": 1, "engine": "trim", "kind": "resume", "parent_id": parent_id, "initial_count": saved_state["counts"]["current"], "total_ions": total_ions, "autosave_ions": autosave_ions, "event_prefix_sha256": prefix_hash, "edits": edits}
    return _stage_trim_runtime(paths, config, created, worker, bundle, {"control": control, "parent": {"parent_id": parent_id, "inputs": metadata["inputs"], "outputs": metadata["outputs"], "checkpoint_atomicity": "not established for binary native arrays"}}, checkpoint_files)
