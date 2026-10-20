"""Prepare isolated SRModule runtimes and initialize complete simulation provenance."""

from collections.abc import Callable, Mapping
from datetime import datetime
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import stat
from tempfile import mkdtemp

from src.backend.config import Config
from src.backend.paths import ProjectPaths
from src.backend.records import CreatedDirectory, _lock_worker, _validate_paths
from src.backend.resources import ResourceReference, verify_resource
from src.srim.compatibility import read_module_version
from src.srim.inputs import render_sr_input
from src.srim.metadata import JSONObject, ResolvedSRInputs, _validate_input_files, _simulation_path, _validate_document, _write_input_file, initialize_simulation


def _read_regular_bytes(path: Path) -> bytes:
    """Read regular path as bytes without writes, rejecting links and junctions.

    Check path and all ancestors before reading. Return exact contents, or raise
    OSError for links, missing files, nonregular objects, and access failures.
    """
    for entry in (*reversed(path.parents), path):
        if entry.is_symlink() or entry.is_junction():
            raise OSError(errno.ELOOP, "Runtime source path contains a link or junction", str(entry))
    if not stat.S_ISREG(path.stat().st_mode):
        raise OSError(errno.EINVAL, "Runtime source must be a regular file", str(path))
    return path.read_bytes()


def _read_selected_resource(paths: ProjectPaths, selection: JSONObject, name: str, runtime_filename: str) -> bytes:
    """Return paths' verified selection bytes for expected name and runtime_filename.

    Require an engine resource with that name and filename. Validate selection's
    source/transformation provenance and verify any original reference. Recheck
    actual read bytes against the selected reference's hash and size. Perform no
    data writes, but verification can create persistent resource lock files.
    Raise ValueError on schema, identity, or integrity errors; propagate OSError.
    """
    _validate_document(selection, "selection")
    reference = ResourceReference(**selection["reference"])
    if reference.category != "engine" or reference.name != name or selection["runtime_filename"] != runtime_filename:
        raise ValueError(f"Expected engine/{name} selection with filename {runtime_filename}")
    if "transformation" in selection:
        verify_resource(paths, ResourceReference(**selection["transformation"]["original"]))
    payload = _read_regular_bytes(verify_resource(paths, reference))
    if (hashlib.sha256(payload).hexdigest(), len(payload)) != (reference.sha256, reference.size_bytes):
        raise ValueError("Selected resource changed while reading")
    return payload


def _make_runtime_manifest(files: Mapping[str, bytes]) -> JSONObject:
    """Return a sorted version-one filename/hash/size manifest of files without effects.

    files must contain exactly SRModule.exe, SR.IN, VERSION, SCOEF03.dat, and
    SNUC03.dat as byte values. Raise ValueError for other keys or value types.
    Hash the supplied bytes without mutation or filesystem operations.
    """
    if set(files) != {"SRModule.exe", "SR.IN", "VERSION", "SCOEF03.dat", "SNUC03.dat"} or any(not isinstance(value, bytes) for value in files.values()):
        raise ValueError("Runtime manifest requires exactly five runtime byte files")
    return {
        "schema_version": 1,
        "files": [
            {"filename": name, "size_bytes": len(files[name]), "sha256": hashlib.sha256(files[name]).hexdigest()}
            for name in sorted(files)
        ],
    }


def prepare_sr_simulation(
    paths: ProjectPaths,
    config: Config,
    created: CreatedDirectory,
    worker: CreatedDirectory,
    request: JSONObject,
    selections: JSONObject,
    *,
    capture_timeout_seconds: float = 30.0,
    dependency_timeout_seconds: float = 60.0,
    progress: Callable[[JSONObject], None] | None = None,
) -> Path:
    """Resolve request/selections under paths/config and stage created's main runtime in worker.

    capture_timeout_seconds/dependency_timeout_seconds bound owned dependency runs;
    progress receives completed dependency updates and propagates callback failures.
    Validate allocations before effects, resolve dependencies outside the main lock
    and return a prepared Path with created status. Never run the main simulation.
    Raise ValueError for invalid input; preserve evidence on dependency/filesystem
    failures and propagate interruption after owned cleanup.
    """
    _validate_preparation_allocations(paths, created, worker)
    for timeout in (capture_timeout_seconds, dependency_timeout_seconds):
        if type(timeout) not in (int, float) or timeout <= 0 or not math.isfinite(timeout):
            raise ValueError("Operation timeouts must be positive and finite")
    if progress is not None and not callable(progress):
        raise ValueError("progress must be callable or None")
    from src.srim.resolution import resolve_sr_inputs
    inputs = resolve_sr_inputs(paths, config, worker, request, selections, capture_timeout_seconds=capture_timeout_seconds, dependency_timeout_seconds=dependency_timeout_seconds, progress=progress)
    return _prepare_sr_runtime(paths, config, created, worker, inputs)


def _validate_preparation_allocations(paths: ProjectPaths, created: CreatedDirectory, worker: CreatedDirectory) -> str:
    """Validate paths/created/worker allocations without writes and return the simulation ID.

    Require an empty correctly identified main record and allocated link-free worker
    with sr/trim directories. Raise ValueError/OSError for invalid layout or identity.
    """
    _validate_paths(paths)
    if not isinstance(created, CreatedDirectory) or created.kind != "srim" or not isinstance(created.timestamp, datetime) or created.timestamp.utcoffset() is None or type(created.suffix) is not int or created.suffix < 1:
        raise ValueError("Expected an allocated simulation with aware timestamp")
    instant = created.timestamp
    simulation_id = f"srim_{instant.year:04d}{instant.month:02d}{instant.day:02d}_{instant.hour:02d}{instant.minute:02d}{instant.second:02d}_{created.suffix}"
    if created.path != paths.simulation_memory / simulation_id:
        raise ValueError("Simulation allocation does not match its identity")
    if not isinstance(worker, CreatedDirectory) or worker.kind != "worker" or worker.timestamp is not None or type(worker.suffix) is not int or worker.suffix < 1 or worker.path != paths.workers / f"worker_{worker.suffix}":
        raise ValueError("Expected an allocated worker")
    record = _simulation_path(paths, simulation_id)
    if any(record.iterdir()):
        raise ValueError("Simulation preparation requires an empty record")
    for directory in (worker.path, worker.path / "sr", worker.path / "trim"):
        if directory.is_symlink() or directory.is_junction():
            raise OSError(errno.ELOOP, "Worker path contains a link", str(directory))
        if not directory.is_dir():
            raise FileNotFoundError(str(directory))
    return simulation_id


def _prepare_sr_runtime(paths: ProjectPaths, config: Config, created: CreatedDirectory, worker: CreatedDirectory, inputs: ResolvedSRInputs) -> Path:
    """Stage pinned inputs for created in worker under paths/config and return runtime Path.

    inputs carries original/effective JSON, snapshots and exact engine bytes. Validate
    allocation and bundle, recheck live engine identity, then initialize immutable
    record evidence under the worker lock. Resolve no aliases/dependencies and run
    no engine. Raise ValueError for inconsistent bundles, propagate I/O/lock failures
    with retained paths, and leave successfully prepared main status created.
    """
    simulation_id = _validate_preparation_allocations(paths, created, worker)
    if not isinstance(inputs, ResolvedSRInputs):
        raise ValueError("Expected ResolvedSRInputs")
    request_snapshot = json.loads(json.dumps(inputs.request, allow_nan=False))
    resolved_snapshot = json.loads(json.dumps(inputs.resolved, allow_nan=False))
    _validate_document(request_snapshot, "request")
    _validate_document(resolved_snapshot, "resolved")
    effective = json.loads(json.dumps(inputs.effective_request, allow_nan=False))
    input_bytes = render_sr_input(effective)
    engine_files = dict(inputs.engine_files)
    if len(engine_files) != len(inputs.engine_files) or set(engine_files) != {"SRModule.exe", "VERSION", "SCOEF03.dat", "SNUC03.dat"} or any(not isinstance(value, bytes) for value in engine_files.values()):
        raise ValueError("Expected four pinned engine files")
    snapshots = _validate_input_files(dict(inputs.snapshots))
    if len(snapshots) != len(inputs.snapshots):
        raise ValueError("Duplicate snapshot names")
    if resolved_snapshot["schema_version"] == 1:
        if request_snapshot != effective or resolved_snapshot["parameters"] != effective["parameters"] or resolved_snapshot["cab"] != effective["parameters"]["correction"] or len(resolved_snapshot["resources"]) != 2:
            raise ValueError("Inconsistent legacy runtime bundle")
        for selection, role, filename in zip(resolved_snapshot["resources"], ("electronic", "nuclear"), ("SCOEF03.dat", "SNUC03.dat"), strict=True):
            if _read_selected_resource(paths, selection, role, filename) != engine_files[filename]:
                raise ValueError("Pinned coefficient bytes differ from selected resources")
    else:
        from src.srim.resolution import verify_sr_resolution
        if verify_sr_resolution(paths, request_snapshot, inputs.selections, resolved_snapshot, snapshots) != effective:
            raise ValueError("Effective runtime input differs from verified resolution")
        snapshots["selections.json"] = (json.dumps(inputs.selections, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        snapshots["effective-request.json"] = (json.dumps(effective, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    if not isinstance(config.srim_root, Path) or not config.srim_root.is_absolute():
        raise ValueError("SRIM root must be an absolute Path")
    runtime = worker.path / "sr" / simulation_id
    artifacts: list[Path] = []
    try:
        with _lock_worker(paths, worker.path):
            _validate_paths(paths)
            for directory in (worker.path, worker.path / "sr", worker.path / "trim"):
                if directory.is_symlink() or directory.is_junction():
                    raise OSError(errno.ELOOP, "Worker directory is a link or junction", str(directory))
                if not directory.is_dir():
                    raise FileNotFoundError(errno.ENOENT, "Worker directory is missing", str(directory))
            record = _simulation_path(paths, simulation_id)
            if any(record.iterdir()):
                raise ValueError("Simulation preparation requires an empty record")
            if os.path.lexists(runtime):
                raise FileExistsError(errno.EEXIST, "Runtime destination already exists", str(runtime))
            module = config.srim_root / "SR Module"
            executable_bytes = engine_files["SRModule.exe"]
            version_bytes = engine_files["VERSION"]
            if _read_regular_bytes(module / "SRModule.exe") != executable_bytes or read_module_version(config.srim_root)[1] != version_bytes:
                raise ValueError("Pinned engine no longer matches the installation")
            try:
                version_lines = version_bytes.decode("ascii").splitlines()
            except UnicodeError as error:
                raise ValueError("Module VERSION must be ASCII") from error
            if not version_lines or not version_lines[0].strip():
                raise ValueError("Module VERSION requires a nonblank first line")
            version = version_lines[0].strip()
            files = {
                "SRModule.exe": executable_bytes,
                "SR.IN": input_bytes,
                "VERSION": version_bytes,
                "SCOEF03.dat": engine_files["SCOEF03.dat"],
                "SNUC03.dat": engine_files["SNUC03.dat"],
            }
            manifest = _make_runtime_manifest(files)
            engine: JSONObject = {
                "name": "sr",
                "version": version,
                "executable": {"name": "SRModule.exe", "sha256": hashlib.sha256(executable_bytes).hexdigest()},
            }
            resolved = resolved_snapshot
            expected_sources = [
                {"id": "sr-module-executable", "name": "SRModule.exe", "version": version, "sha256": hashlib.sha256(executable_bytes).hexdigest(), "citation": None},
                {"id": "sr-module-version", "name": "VERSION", "version": version, "sha256": hashlib.sha256(version_bytes).hexdigest(), "citation": None},
            ]
            if resolved["sources"] != expected_sources:
                raise ValueError("Pinned executable source evidence mismatch")
            _validate_document(engine, "engine")
            _validate_document(resolved, "resolved")
            manifest_bytes = (json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
            staging = Path(mkdtemp(prefix=".staging-", dir=worker.path / "sr"))
            artifacts.append(staging)
            for name in sorted(files):
                _write_input_file(staging / name, files[name])
            staging.rename(runtime)
            artifacts.append(runtime)
            initialize_simulation(
                paths,
                created,
                engine,
                request_snapshot,
                resolved,
                engine_inputs={**files, "runtime.json": manifest_bytes},
                input_files=snapshots,
            )
            return runtime
    except (OSError, ValueError) as error:
        error.add_note("SR preparation retained artifacts: " + ", ".join(str(path) for path in (*artifacts, created.path) if os.path.lexists(path)))
        raise
