"""Preserve permanent runtime evidence and clear verified runtimes for reusable workers."""

from collections.abc import Sequence
import errno
import os
from pathlib import Path
import re
import shutil
from tempfile import mkdtemp
from src.backend.paths import ProjectPaths
from src.backend.records import _lock_parent, _lock_worker
from src.srim.common.files import _inventory, _read_json, _write_input_file
from src.srim.common.records import _simulation_path, read_simulation
from src.srim.common.types import RetainedWorker
from src.srim.common.types import BatchCleanupReport


def preserve_sr_runtime(paths: ProjectPaths, simulation_id: str, runtime: Path) -> None:
    """Copy runtime exclusively into paths' simulation_id output/raw/runtime; return None.

    Require the matching canonical worker runtime and an initialized output tree.
    Reject links and changing bytes; synchronize and verify a staging copy before
    rename. An already identical copy is reusable, never overwritten. Raise
    ValueError/OSError on identity, collision, integrity or filesystem failure,
    retaining the source and any staging evidence. Caller owns the worker lease.
    """
    record = _simulation_path(paths, simulation_id)
    if not isinstance(runtime, Path) or not runtime.is_absolute() or runtime.name != simulation_id or runtime.parent.name != "sr" or runtime.parent.parent.parent != paths.workers or re.fullmatch(r"worker_[1-9][0-9]*", runtime.parent.parent.name) is None:
        raise ValueError("Runtime does not match simulation and worker")
    source = _inventory(runtime, runtime)
    parent = record / "output" / "raw"
    _inventory(parent, record)
    destination = parent / "runtime"
    if os.path.lexists(destination):
        if _inventory(destination, destination) != source:
            raise FileExistsError("Preserved runtime differs from working files")
        return
    staging = Path(mkdtemp(prefix=".runtime-", dir=parent))
    try:
        for entry in source:
            target = staging / entry["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            _write_input_file(target, (runtime / entry["path"]).read_bytes())
        if _inventory(staging, staging) != source or _inventory(runtime, runtime) != source:
            raise ValueError("Runtime changed during preservation")
        staging.rename(destination)
    except BaseException as error:
        error.add_note(f"Runtime retained at {runtime}; preservation staging: {staging}")
        raise


def verify_preserved_runtime(paths: ProjectPaths, simulation_id: str, runtime: Path) -> None:
    """Verify paths' terminal simulation_id and its exact saved runtime; return None.

    runtime must be its canonical link-free worker directory. Check sealed input
    and output inventories and every runtime byte. Raise ValueError/OSError for
    unsafe, missing, nonterminal, modified or incompletely preserved data. Write
    no data except persistent metadata coordination files.
    """
    if not isinstance(runtime, Path) or runtime.name != simulation_id or runtime.parent.name != "sr" or runtime.parent.parent.parent != paths.workers or re.fullmatch(r"worker_[1-9][0-9]*", runtime.parent.parent.name) is None:
        raise ValueError("Invalid runtime owner")
    record = _simulation_path(paths, simulation_id)
    metadata = read_simulation(paths, simulation_id)
    if metadata["status"] not in ("succeeded", "failed"):
        raise ValueError("Runtime belongs to an unfinished simulation")
    if _inventory(record / "input", record) != metadata["inputs"] or _inventory(record / "output", record) != metadata["outputs"]:
        raise ValueError("Permanent simulation inventories were changed")
    if _inventory(runtime, runtime) != _inventory(record / "output" / "raw" / "runtime", record / "output" / "raw" / "runtime"):
        raise ValueError("Working files differ from preserved runtime")


def cleanup_sr_batch(paths: ProjectPaths, worker_paths: Sequence[Path]) -> BatchCleanupReport:
    """Clear fully preserved runtimes in worker_paths under paths; return outcomes.

    Deduplicate canonical worker identities and lock each against concurrent use
    and deletion. Retain workers with unknown files, TRIM contents, unfinished or
    unpreserved runtimes, legacy CAB references, or any validation/I/O failure.
    Verify resolved deletion targets remain inside each worker's sr directory.
    Keep empty worker/sr/trim directories for reuse and remove no permanent
    records or resources; report per-worker exceptions without retry.
    """
    cleaned: list[Path] = []
    retained: list[RetainedWorker] = []
    for worker in dict.fromkeys(worker_paths):
        try:
            with _lock_worker(paths, worker), _lock_parent(paths.workers, timeout_seconds=30.0):
                resolved = worker.resolve(strict=True)
                if resolved.parent != paths.workers.resolve(strict=True) or resolved != worker or re.fullmatch(r"worker_[1-9][0-9]*", worker.name) is None:
                    raise ValueError("Cleanup target escapes the canonical worker root")
                if {entry.name for entry in worker.iterdir()} != {"sr", "trim"}:
                    raise ValueError("Worker contains unrelated files")
                for child in (worker / "sr", worker / "trim"):
                    if child.is_symlink() or child.is_junction() or not child.is_dir():
                        raise OSError(errno.ELOOP, "Worker contains an unsafe directory", str(child))
                if any((worker / "trim").iterdir()):
                    raise ValueError("Worker contains TRIM files")
                runtimes = tuple((worker / "sr").iterdir())
                for runtime in runtimes:
                    verify_preserved_runtime(paths, runtime.name, runtime)
                for entry_path in (paths.resources / "cab").glob("*/v*/entries/*/*.json"):
                    pending: list[object] = [_read_json(entry_path)]
                    while pending:
                        item = pending.pop()
                        if isinstance(item, dict):
                            if item.get("kind") == "worker" and item.get("id") == worker.name:
                                raise ValueError("Worker is referenced by legacy CAB evidence")
                            pending.extend(item.values())
                        elif isinstance(item, list):
                            pending.extend(item)
                for runtime in runtimes:
                    target = runtime.resolve(strict=True)
                    if target != runtime or target.parent != resolved / "sr":
                        raise ValueError("Cleanup runtime escapes the worker sr directory")
                    shutil.rmtree(target)
                cleaned.append(worker)
        except Exception as error:
            retained.append(RetainedWorker(worker, str(error) or type(error).__name__, error))
    return BatchCleanupReport(tuple(cleaned), tuple(retained))
