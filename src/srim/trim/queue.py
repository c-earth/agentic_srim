"""Schedule prepared TRIM tasks with worker serialization and explicit verified cleanup."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextvars import copy_context
from dataclasses import dataclass
import math
from pathlib import Path
from threading import Event
from time import perf_counter
from src.knowledge.index import collect_index_updates
from src.backend.config import Config
from src.backend.paths import ProjectPaths
from src.backend.records import _record_time, _validate_paths
from src.srim.common.types import JSONObject
from src.srim.common.types import BatchCleanupReport
from src.srim.trim.execution import run_trim_simulation
from src.srim.trim.runtime import verify_trim_runtime
from src.srim.trim.storage import cleanup_trim_batch


@dataclass(frozen=True)
class PreparedTRIMTask:
    """Store simulation_id and absolute runtime for an already prepared TRIM task.

    Construction stores these public fields without validation or effects.
    """

    simulation_id: str
    runtime: Path


@dataclass(frozen=True)
class TRIMTaskResult:
    """Store task, metadata or error, and elapsed_seconds excluding queue waiting.

    Construction has no effects. Exactly one of metadata/error is populated by
    scheduling; metadata can describe an expected failed native attempt.
    """

    task: PreparedTRIMTask
    metadata: JSONObject | None
    error: Exception | None
    elapsed_seconds: float


@dataclass(frozen=True)
class TRIMBatchResult:
    """Store ordered task_results, cleanup outcomes and total elapsed_seconds.

    Construction stores public fields without I/O; total includes validation
    and optional cleanup. Working runtimes are retained by default.
    """

    task_results: tuple[TRIMTaskResult, ...]
    cleanup: BatchCleanupReport
    elapsed_seconds: float


def _validate_trim_batch(paths: ProjectPaths, config: Config, tasks: Sequence[PreparedTRIMTask], max_workers: int, timeout_seconds: float | None, worker_timeouts: Mapping[str, float | None] | None, cleanup: bool) -> tuple[tuple[PreparedTRIMTask, ...], dict[str, float | None]]:
    """Snapshot/validate tasks, paths, config and concurrency/timeout/cleanup controls.

    Return ordered tasks and resolved worker timeouts before native launch.
    worker_timeouts overrides timeout_seconds by participating worker name.
    None disables a deadline. Reject invalid controls, unknown workers and
    duplicate identities with ValueError; runtime verification may raise OSError.
    Perform reads and persistent coordination only; no lifecycle writes.
    """
    if type(max_workers) is not int or max_workers < 1 or (timeout_seconds is not None and (type(timeout_seconds) not in (int, float) or timeout_seconds <= 0 or not math.isfinite(timeout_seconds))) or type(cleanup) is not bool:
        raise ValueError("Invalid TRIM batch controls")
    if not isinstance(config, Config) or type(config.hide_trim_window) is not bool or not isinstance(tasks, Sequence):
        raise ValueError("Invalid TRIM batch configuration/tasks")
    _record_time(config, None)
    _validate_paths(paths)
    if worker_timeouts is not None and not isinstance(worker_timeouts, Mapping):
        raise ValueError("Invalid TRIM worker timeouts")
    overrides = {} if worker_timeouts is None else dict(worker_timeouts)
    for name, value in overrides.items():
        if type(name) is not str or (value is not None and (type(value) not in (int, float) or value <= 0 or not math.isfinite(value))):
            raise ValueError("Invalid TRIM worker timeout")
    prepared = tuple(tasks)
    ids: set[str] = set()
    runtimes: set[Path] = set()
    for task in prepared:
        if not isinstance(task, PreparedTRIMTask) or task.simulation_id in ids or task.runtime in runtimes:
            raise ValueError("Invalid/duplicate prepared TRIM task")
        ids.add(task.simulation_id)
        runtimes.add(task.runtime)
        verify_trim_runtime(paths, task.simulation_id, task.runtime)
    workers = {task.runtime.parent.parent.name for task in prepared}
    if overrides.keys() - workers:
        raise ValueError("Unknown TRIM timeout worker")
    return prepared, {name: overrides.get(name, timeout_seconds) for name in workers}


def _execute_trim_task(paths: ProjectPaths, config: Config, task: PreparedTRIMTask, timeout_seconds: float | None, cancel: Callable[[], bool]) -> TRIMTaskResult:
    """Run task under paths/config using timeout_seconds and cancellation callback.

    Return measured metadata or captured Exception independently. Never retry or
    clean; propagate BaseException for batch cancellation and owned finalization.
    """
    started = perf_counter()
    try:
        metadata = run_trim_simulation(paths, config, task.simulation_id, task.runtime, timeout_seconds=timeout_seconds, cancel=cancel)
        return TRIMTaskResult(task, metadata, None, perf_counter() - started)
    except Exception as error:
        return TRIMTaskResult(task, None, error, perf_counter() - started)


def run_trim_batch(paths: ProjectPaths, config: Config, tasks: Sequence[PreparedTRIMTask], *, max_workers: int, timeout_seconds: float | None = 60.0, worker_timeouts: Mapping[str, float | None] | None = None, cleanup: bool = False) -> TRIMBatchResult:
    """Run prepared tasks with bounded concurrency and same-worker ordering.

    Validate paths/config/tasks and max_workers/timeout_seconds/cleanup before
    dispatch, including worker_timeouts overrides keyed by worker directory name.
    None means unlimited; otherwise each native attempt gets a fresh positive
    finite deadline. Queue waiting is excluded. Snapshot overrides without
    modifying the caller mapping. Return ordered independent outcomes.
    Keep working copies unless cleanup=True. On BaseException stop dispatch,
    cancel active owned native jobs, wait/preserve, retain workers and re-raise.
    Finalized inputs are indexed once after active calls finish.
    Queue state exists only in memory; no retries or dependency scheduling.
    """
    with collect_index_updates(paths):
        started = perf_counter()
        prepared, timeouts = _validate_trim_batch(paths, config, tasks, max_workers, timeout_seconds, worker_timeouts, cleanup)
        pending = list(enumerate(prepared))
        active: dict[Future[TRIMTaskResult], tuple[int, Path]] = {}
        busy: set[Path] = set()
        results: dict[int, TRIMTaskResult] = {}
        stop = Event()
        executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="trim-worker")
        try:
            while pending or active:
                position = 0
                while position < len(pending) and len(active) < max_workers:
                    index, task = pending[position]
                    worker = task.runtime.parent.parent
                    if worker in busy:
                        position += 1
                        continue
                    pending.pop(position)
                    busy.add(worker)
                    future = executor.submit(copy_context().run, _execute_trim_task, paths, config, task, timeouts[worker.name], stop.is_set)
                    active[future] = index, worker
                completed, _ = wait(active, timeout=0.1, return_when=FIRST_COMPLETED)
                for future in completed:
                    index, worker = active.pop(future)
                    busy.remove(worker)
                    results[index] = future.result()
        except BaseException:
            stop.set()
            raise
        finally:
            executor.shutdown(wait=True, cancel_futures=True)
        outcomes = cleanup_trim_batch(paths, tuple(dict.fromkeys(task.runtime.parent.parent for task in prepared))) if cleanup else BatchCleanupReport((), ())
        return TRIMBatchResult(tuple(results[i] for i in range(len(prepared))), outcomes, perf_counter() - started)
