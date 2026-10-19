"""Prepare isolated SRModule runtimes and initialize complete simulation provenance."""

from collections.abc import Mapping
from datetime import datetime
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
from tempfile import mkdtemp

from src.backend.config import Config
from src.backend.paths import ProjectPaths
from src.backend.records import CreatedDirectory, _lock_parent, _validate_paths
from src.backend.resources import ResourceReference, verify_resource
from src.srim.inputs import render_sr_input
from src.srim.metadata import JSONObject, _simulation_path, _validate_document, _write_input_file, initialize_simulation


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
    electronic: JSONObject,
    nuclear: JSONObject,
) -> Path:
    """Prepare created's SRModule simulation in worker using paths/config and explicit inputs.

    request supplies all physical parameters. electronic and nuclear are existing
    resource-selection documents naming engine/electronic and engine/nuclear with
    SCOEF03.dat and SNUC03.dat runtime filenames and caller source attribution.
    Snapshot caller documents, verify resource bytes, and read SRModule.exe and
    VERSION from config.srim_root/SR Module. Use VERSION's trimmed first line as
    engine identity and retain its complete ASCII bytes.

    Require base setup, an empty allocated simulation, and an allocated worker.
    Under the worker-parent lock, publish five synchronized runtime files through
    a staging-directory rename, then initialize all simulation inputs and metadata.
    Return worker/sr/simulation_id with simulation status created. Preserve other
    runtimes and trim contents; run no executable and register no resources.

    Raise ValueError for request, identity, version, selection, or integrity errors.
    Propagate OSError for links, missing files, collisions, locks, and write errors.
    Retain partial artifacts and identify their paths in exception notes. Do not
    retry, clean up, overwrite existing destinations, or mark simulations failed.
    """
    _validate_paths(paths)
    _validate_document(request, "request")
    _validate_document(electronic, "selection")
    _validate_document(nuclear, "selection")
    request_snapshot: JSONObject = json.loads(json.dumps(request, allow_nan=False))
    electronic_snapshot: JSONObject = json.loads(json.dumps(electronic, allow_nan=False))
    nuclear_snapshot: JSONObject = json.loads(json.dumps(nuclear, allow_nan=False))
    input_bytes = render_sr_input(request_snapshot)
    if not isinstance(config.srim_root, Path) or not config.srim_root.is_absolute():
        raise ValueError("SRIM root must be an absolute Path")
    if not isinstance(created, CreatedDirectory) or created.kind != "srim" or not isinstance(created.timestamp, datetime) or created.timestamp.utcoffset() is None or type(created.suffix) is not int or created.suffix < 1:
        raise ValueError("Expected an allocated simulation with aware timestamp")
    instant = created.timestamp
    simulation_id = f"srim_{instant.year:04d}{instant.month:02d}{instant.day:02d}_{instant.hour:02d}{instant.minute:02d}{instant.second:02d}_{created.suffix}"
    if created.path != paths.simulation_memory / simulation_id:
        raise ValueError("Simulation allocation does not match its identity")
    if not isinstance(worker, CreatedDirectory) or worker.kind != "worker" or worker.timestamp is not None or type(worker.suffix) is not int or worker.suffix < 1 or worker.path != paths.workers / f"worker_{worker.suffix}":
        raise ValueError("Expected an allocated worker")
    runtime = worker.path / "sr" / simulation_id
    artifacts: list[Path] = []
    try:
        with _lock_parent(paths.workers):
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
            executable_bytes = _read_regular_bytes(module / "SRModule.exe")
            version_bytes = _read_regular_bytes(module / "VERSION")
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
                "SCOEF03.dat": _read_selected_resource(paths, electronic_snapshot, "electronic", "SCOEF03.dat"),
                "SNUC03.dat": _read_selected_resource(paths, nuclear_snapshot, "nuclear", "SNUC03.dat"),
            }
            manifest = _make_runtime_manifest(files)
            engine: JSONObject = {
                "name": "sr",
                "version": version,
                "executable": {"name": "SRModule.exe", "sha256": hashlib.sha256(executable_bytes).hexdigest()},
            }
            resolved: JSONObject = {
                "schema_version": 1,
                "parameters": request_snapshot["parameters"],
                "sources": [
                    {"id": "sr-module-executable", "name": "SRModule.exe", "version": version, "sha256": hashlib.sha256(executable_bytes).hexdigest(), "citation": None},
                    {"id": "sr-module-version", "name": "VERSION", "version": version, "sha256": hashlib.sha256(version_bytes).hexdigest(), "citation": None},
                ],
                "resources": [electronic_snapshot, nuclear_snapshot],
                "cab": request_snapshot["parameters"]["correction"],
            }
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
                engine_inputs={"SR.IN": input_bytes, "VERSION": version_bytes, "runtime.json": manifest_bytes},
            )
            return runtime
    except (OSError, ValueError) as error:
        error.add_note("SR preparation retained artifacts: " + ", ".join(str(path) for path in (*artifacts, created.path) if os.path.lexists(path)))
        raise
