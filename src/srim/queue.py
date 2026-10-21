"""Queue prepared SR simulations in memory and clean verified workers after the batch."""

from collections.abc import Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextvars import copy_context
from dataclasses import dataclass
import math
from pathlib import Path
from time import perf_counter

from src.knowledge.index import collect_index_updates
from src.backend.config import Config
from src.backend.paths import ProjectPaths
from src.backend.records import _record_time, _validate_paths
from src.srim.execution import run_sr_simulation, verify_sr_runtime
from src.srim.metadata import JSONObject, _inventory, _simulation_path
from src.srim.storage import BatchCleanupReport, cleanup_sr_batch


@dataclass(frozen=True)
class PreparedSRTask:
    """Store an existing simulation_id and absolute runtime Path without I/O or validation."""

    simulation_id: str
    runtime: Path


@dataclass(frozen=True)
class SRTaskResult:
    """Store task, terminal metadata or error, and elapsed_seconds excluding queue waiting.

    Construction only stores fields. metadata can describe an expected failed
    run; error preserves an unexpected Exception and its traceback/notes. Exactly
    one of metadata/error is populated by the batch executor.
    """

    task: PreparedSRTask
    metadata: JSONObject | None
    error: Exception | None
    elapsed_seconds: float


@dataclass(frozen=True)
class SRBatchResult:
    """Store input-ordered task_results, cleanup outcomes and total elapsed_seconds.

    Construction performs no I/O or validation. Total time includes validation,
    execution and cleanup; retained cleanup paths do not alter task outcomes.
    """

    task_results: tuple[SRTaskResult, ...]
    cleanup: BatchCleanupReport
    elapsed_seconds: float


def _validate_batch(paths: ProjectPaths, config: Config, tasks: Sequence[PreparedSRTask], max_workers: int, timeout_seconds: float) -> tuple[PreparedSRTask, ...]:
    """Snapshot and validate tasks under paths/config for max_workers/timeout_seconds.

    Return an immutable ordered tuple, rejecting bool/nonpositive concurrency,
    invalid finite-positive timeouts, duplicate IDs/paths, invalid config and
    unprepared/nonempty runtimes with ValueError/OSError before any launch.
    Existing verification may create persistent coordination files, but changes
    no simulation state. All callers must keep submitted records unmodified.
    """
    if type(max_workers) is not int or max_workers <= 0:
        raise ValueError("max_workers must be a positive integer")
    if type(timeout_seconds) not in (int, float) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be finite and positive")
    try:
        if not math.isfinite(float(timeout_seconds)):
            raise ValueError("timeout_seconds must be finite and positive")
    except OverflowError as error:
        raise ValueError("timeout_seconds must be finite and positive") from error
    if not isinstance(config, Config) or type(config.hide_sr_window) is not bool or type(config.hide_trim_window) is not bool or not isinstance(config.srim_root, Path) or not config.srim_root.is_absolute():
        raise ValueError("Invalid SR configuration")
    _record_time(config, None)
    _validate_paths(paths)
    if not isinstance(tasks, Sequence):
        raise ValueError("tasks must be a sequence of PreparedSRTask")
    prepared = tuple(tasks)
    seen_ids: set[str] = set()
    seen_paths: set[Path] = set()
    for task in prepared:
        if not isinstance(task, PreparedSRTask) or not isinstance(task.simulation_id, str) or not isinstance(task.runtime, Path):
            raise ValueError("Expected a prepared simulation task")
        if task.simulation_id in seen_ids or task.runtime in seen_paths:
            raise ValueError("Duplicate simulation ID or runtime")
        seen_ids.add(task.simulation_id)
        seen_paths.add(task.runtime)
        verify_sr_runtime(paths, task.simulation_id, task.runtime)
        record = _simulation_path(paths, task.simulation_id)
        if {entry.name for entry in (record / "output").iterdir()} != {"raw", "parsed"} or _inventory(record / "output", record):
            raise ValueError("Prepared simulation requires empty output directories")
    return prepared


def _execute_task(paths: ProjectPaths, config: Config, task: PreparedSRTask, timeout_seconds: float) -> SRTaskResult:
    """Execute task with paths/config/timeout_seconds and return its measured outcome.

    Preserve terminal metadata or an Exception independently of other tasks.
    Never retry or clean workers here. BaseException subclasses propagate to
    stop batch dispatch. The ordinary runner owns native processes and records.
    """
    started = perf_counter()
    try:
        metadata = run_sr_simulation(paths, config, task.simulation_id, task.runtime, timeout_seconds=timeout_seconds)
    except Exception as error:
        return SRTaskResult(task, None, error, perf_counter() - started)
    return SRTaskResult(task, metadata, None, perf_counter() - started)


def run_sr_batch(paths: ProjectPaths, config: Config, tasks: Sequence[PreparedSRTask], *, max_workers: int, timeout_seconds: float = 60.0) -> SRBatchResult:
    """Run prepared tasks under paths/config with at most max_workers active workers.

    Validate the complete sequence before dispatch. Preserve same-worker order,
    start the earliest eligible task and return input-ordered results. Ordinary
    failures do not stop other tasks. Persist results immediately; clean only
    submitted workers after every task call finishes, returning cleanup outcomes.
    Finalized input references are indexed once after active calls finish.
    No queue survives caller exit. On BaseException stop dispatch, cancel work
    not started, wait for active bounded calls, retain workers and re-raise.
    timeout_seconds applies separately to each native process, excluding waiting.
    """
    with collect_index_updates(paths):
        started = perf_counter()
        prepared = _validate_batch(paths, config, tasks, max_workers, timeout_seconds)
        pending = list(enumerate(prepared))
        active: dict[Future[SRTaskResult], tuple[int, Path]] = {}
        workers: set[Path] = set()
        results: dict[int, SRTaskResult] = {}
        executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="sr-worker")
        try:
            while pending or active:
                position = 0
                while position < len(pending) and len(active) < max_workers:
                    index, task = pending[position]
                    worker = task.runtime.parent.parent
                    if worker in workers:
                        position += 1
                        continue
                    pending.pop(position)
                    workers.add(worker)
                    future = executor.submit(copy_context().run, _execute_task, paths, config, task, timeout_seconds)
                    active[future] = (index, worker)
                completed, _ = wait(active, return_when=FIRST_COMPLETED)
                for future in completed:
                    index, worker = active.pop(future)
                    workers.remove(worker)
                    results[index] = future.result()
        finally:
            executor.shutdown(wait=True, cancel_futures=True)
        cleanup = cleanup_sr_batch(paths, tuple(dict.fromkeys(task.runtime.parent.parent for task in prepared)))
        return SRBatchResult(tuple(results[index] for index in range(len(prepared))), cleanup, perf_counter() - started)
