"""Export independently verified TRIM inputs as self-contained immutable-byte bundles."""

from copy import deepcopy
from collections.abc import Sequence
import errno
import hashlib
import json
import os
from pathlib import Path
from tempfile import mkdtemp
import re
import shutil

from src.srim.metadata import _write_input_file
from src.srim.runtime import _read_regular_bytes
from src.srim.trim_resolution import ResolvedTRIMInputs, verify_trim_resolution
from src.backend.paths import ProjectPaths
from src.backend.records import _lock_worker
from src.srim.metadata import JSONObject, _inventory, _simulation_path, read_simulation
from src.srim.storage import BatchCleanupReport, RetainedWorker
from src.srim.trim_outputs import PROFILES, parse_trim_outputs


def write_trim_input_bundle(destination: Path, inputs: ResolvedTRIMInputs) -> Path:
    """Verify inputs and exclusively export a complete bundle to absolute destination.

    Require an existing safe parent and absent destination. Copy nested caller
    data, replay it offline, write synchronized files into unique sibling staging,
    verify all hashes, then rename without merging/overwriting. Return destination.
    No worker allocation, lifecycle transition, deletion or native execution.
    Raise ValueError for invalid bundle/path, FileExistsError for a collision and
    OSError for unsafe/I/O failures; identify retained staging artifacts in notes.
    """
    if not isinstance(destination, Path) or not destination.is_absolute() or destination.name in ("", ".", "..") or ".." in destination.parts:
        raise ValueError("Input bundle destination must be an absolute new directory")
    for entry in (*reversed(destination.parents), destination):
        if entry.is_symlink() or entry.is_junction():
            raise OSError(errno.ELOOP, "Input bundle path contains a link or junction", str(entry))
    if os.path.lexists(destination):
        raise FileExistsError(errno.EEXIST, "Input bundle destination already exists", str(destination))
    if not destination.parent.is_dir():
        raise FileNotFoundError(errno.ENOENT, "Input bundle parent must exist", str(destination.parent))
    pinned = deepcopy(inputs)
    verify_trim_resolution(pinned)
    files = {"input/" + name: payload for name, payload in pinned.snapshots}
    for name, document in (("request", pinned.request), ("selections", pinned.selections), ("effective-request", pinned.effective_request), ("resolved", pinned.resolved)):
        files[f"input/{name}.json"] = (json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    manifest = {"schema_version": 1, "engine": "trim", "files": [{"path": name, "sha256": hashlib.sha256(payload).hexdigest(), "size_bytes": len(payload)} for name, payload in sorted(files.items())]}
    staging = Path(mkdtemp(prefix=".trim-input-", dir=destination.parent))
    try:
        for name, payload in sorted(files.items()):
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            _write_input_file(target, payload)
        for item in manifest["files"]:
            payload = _read_regular_bytes(staging / item["path"])
            if (hashlib.sha256(payload).hexdigest(), len(payload)) != (item["sha256"], item["size_bytes"]):
                raise ValueError("Input bundle changed during export verification")
        _write_input_file(staging / "manifest.json", (json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8"))
        for entry in (*reversed(destination.parents), destination):
            if entry.is_symlink() or entry.is_junction():
                raise OSError(errno.ELOOP, "Input bundle destination became unsafe", str(entry))
        if os.path.lexists(destination):
            raise FileExistsError(errno.EEXIST, "Input bundle destination appeared during export", str(destination))
        staging.rename(destination)
        return destination
    except BaseException as error:
        error.add_note(f"TRIM input export retained staging artifacts: {staging}")
        raise


def preserve_trim_runtime(paths: ProjectPaths, simulation_id: str, runtime: Path) -> None:
    """Copy stopped runtime for paths/simulation_id exclusively to permanent raw storage.

    Caller holds the worker lease. Return None after flushed hash verification;
    reuse only an identical preserved copy. Raise ValueError/OSError on ownership,
    collision, changing bytes or unsafe I/O, retaining source/staging evidence.
    """
    record = _simulation_path(paths, simulation_id)
    if not isinstance(runtime, Path) or not runtime.is_absolute() or runtime != paths.workers / runtime.parent.parent.name / "trim" / simulation_id or re.fullmatch(r"worker_[1-9][0-9]*", runtime.parent.parent.name) is None:
        raise ValueError("Invalid TRIM runtime owner")
    source = _inventory(runtime, runtime)
    parent = record / "output/raw"
    _inventory(parent, record)
    destination = parent / "runtime"
    if os.path.lexists(destination):
        if _inventory(destination, destination) != source or {p.relative_to(runtime).as_posix() for p in runtime.rglob("*") if p.is_dir()} != {p.relative_to(destination).as_posix() for p in destination.rglob("*") if p.is_dir()}:
            raise FileExistsError("Preserved TRIM runtime differs")
        return
    staging = Path(mkdtemp(prefix=".runtime-", dir=parent))
    try:
        directories = {p.relative_to(runtime).as_posix() for p in runtime.rglob("*") if p.is_dir()}
        for name in sorted(directories):
            (staging / name).mkdir(parents=True, exist_ok=True)
        for item in source:
            target = staging / item["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(runtime / item["path"], target)
            with target.open("r+b") as handle:
                os.fsync(handle.fileno())
        if _inventory(staging, staging) != source or _inventory(runtime, runtime) != source or {p.relative_to(runtime).as_posix() for p in runtime.rglob("*") if p.is_dir()} != directories:
            raise ValueError("TRIM runtime changed during preservation")
        staging.rename(destination)
    except BaseException as error:
        error.add_note(f"TRIM runtime retained at {runtime}; staging: {staging}")
        raise


def verify_preserved_trim_runtime(paths: ProjectPaths, simulation_id: str, runtime: Path) -> None:
    """Verify terminal paths/simulation_id and exact working/preserved runtime bytes.

    Return None without writes. Raise ValueError/OSError for unsafe ownership,
    nonterminal records, changed inventories or missing evidence.
    """
    if not isinstance(runtime, Path) or not runtime.is_absolute() or runtime != paths.workers / runtime.parent.parent.name / "trim" / simulation_id or re.fullmatch(r"worker_[1-9][0-9]*", runtime.parent.parent.name) is None:
        raise ValueError("Invalid TRIM runtime owner")
    record = _simulation_path(paths, simulation_id)
    metadata = read_simulation(paths, simulation_id)
    if metadata["engine"]["name"] != "trim" or metadata["status"] not in ("succeeded", "failed"):
        raise ValueError("Expected terminal TRIM simulation")
    if _inventory(record / "input", record) != metadata["inputs"] or _inventory(record / "output", record) != metadata["outputs"]:
        raise ValueError("Sealed TRIM inventories changed")
    if _inventory(runtime, runtime) != _inventory(record / "output/raw/runtime", record / "output/raw/runtime"):
        raise ValueError("Working TRIM runtime differs from preserved copy")
    saved = record / "output/raw/runtime"
    if {p.relative_to(runtime).as_posix() for p in runtime.rglob("*") if p.is_dir()} != {p.relative_to(saved).as_posix() for p in saved.rglob("*") if p.is_dir()}:
        raise ValueError("Working TRIM directory layout differs from preserved copy")


def parse_trim_directory(source: Path, destination: Path, *, request: JSONObject | None = None) -> Path:
    """Freeze absolute quiescent source and export parsed results to new destination.

    request optionally supplies explicit engine parameters. Stream-copy all raw
    files and parse summary/profiles/textual checkpoint only. Return destination
    even for reported native parse issues. Invalid/colliding/unsafe paths and
    changing source or I/O raise ValueError/OSError with retained staging notes.
    No source mutation, engine launch or simulation lifecycle occurs.
    """
    if not isinstance(source, Path) or not isinstance(destination, Path) or not source.is_absolute() or not destination.is_absolute() or ".." in source.parts or ".." in destination.parts:
        raise ValueError("Import requires absolute canonical paths")
    for path in (source, destination):
        for entry in (*reversed(path.parents), path):
            if entry.is_symlink() or entry.is_junction():
                raise OSError("Unsafe TRIM import path")
    source = source.resolve(strict=True)
    destination = destination.resolve()
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("TRIM import source and destination overlap")
    if destination.exists():
        raise FileExistsError(str(destination))
    if request is not None:
        from src.srim.trim_inputs import validate_trim_engine_request
        validate_trim_engine_request(request)
    inventory = _inventory(source, source)
    directories = {p.relative_to(source).as_posix() for p in source.rglob("*") if p.is_dir()}
    staging = Path(mkdtemp(prefix=".trim-import-", dir=destination.parent))
    try:
        raw = staging / "raw/runtime"
        raw.mkdir(parents=True)
        for name in sorted(directories):
            (raw / name).mkdir(parents=True, exist_ok=True)
        for item in inventory:
            target = raw / item["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / item["path"], target)
            with target.open("r+b") as handle:
                os.fsync(handle.fileno())
        if _inventory(raw, raw) != inventory or _inventory(source, source) != inventory or {p.relative_to(source).as_posix() for p in source.rglob("*") if p.is_dir()} != directories:
            raise ValueError("TRIM import source changed during snapshot")
        wanted = {name.casefold() for name in ("TDATA.txt", *PROFILES)}
        files = {item["path"]: _read_regular_bytes(raw / item["path"]) for item in inventory if item["path"].casefold() in wanted | {"srim outputs/" + name for name in wanted} | {"srim restore/tdata.sav", "srim restore/trimin.sav"}}
        report = parse_trim_outputs(files, request)
        (staging / "parsed").mkdir()
        _write_input_file(staging / "parsed/results.json", (json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
        if request is not None:
            _write_input_file(staging / "request.json", (json.dumps(request, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
        manifest = {"schema_version": 1, "engine": "trim", "kind": "trim-output-import", "files": _inventory(staging, staging)}
        _write_input_file(staging / "manifest.json", (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode("utf-8"))
        staging.rename(destination)
        return destination
    except BaseException as error:
        error.add_note(f"TRIM import staging retained at {staging}")
        raise


def cleanup_trim_batch(paths: ProjectPaths, worker_paths: Sequence[Path]) -> BatchCleanupReport:
    """Remove only verified terminal preserved TRIM runtimes in worker_paths.

    Lock canonical paths workers independently and retain SR/unknown/unsafe or
    changed data. Return cleaned/retained outcomes; never delete permanent
    records or allocation directories. Per-worker errors are returned, not raised.
    """
    cleaned: list[Path] = []
    retained: list[RetainedWorker] = []
    for worker in dict.fromkeys(worker_paths):
        try:
            with _lock_worker(paths, worker):
                resolved = worker.resolve(strict=True)
                if resolved != worker or resolved.parent != paths.workers.resolve(strict=True) or re.fullmatch(r"worker_[1-9][0-9]*", worker.name) is None:
                    raise ValueError("TRIM cleanup escapes worker root")
                if {p.name for p in worker.iterdir()} != {"sr", "trim"}:
                    raise ValueError("Unknown worker contents")
                for child in (worker / "sr", worker / "trim"):
                    if child.is_symlink() or child.is_junction() or not child.is_dir():
                        raise OSError("Unsafe worker directory")
                if any((worker / "sr").iterdir()):
                    raise ValueError("Worker contains SR evidence")
                runtimes = tuple((worker / "trim").iterdir())
                for runtime in runtimes:
                    verify_preserved_trim_runtime(paths, runtime.name, runtime)
                for runtime in runtimes:
                    target = runtime.resolve(strict=True)
                    if target != runtime or target.parent != resolved / "trim":
                        raise ValueError("TRIM cleanup target escapes worker trim directory")
                    shutil.rmtree(target)
                cleaned.append(worker)
        except Exception as error:
            retained.append(RetainedWorker(worker, str(error) or type(error).__name__, error))
    return BatchCleanupReport(tuple(cleaned), tuple(retained))
