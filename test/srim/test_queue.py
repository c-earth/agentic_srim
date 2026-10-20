"""Verify prepared batch validation, native-call overlap, ordering and interruption."""

from __future__ import annotations

from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from threading import Barrier, Event, Lock
from typing import BinaryIO
import unittest
from unittest.mock import patch

from _fixtures import fake_bragg_run, make_test_project, make_test_request, make_test_selections
from src.backend.records import CreateRequest, CreatedDirectory, create_directories
from src.srim.metadata import read_simulation
from src.srim.queue import PreparedSRTask, run_sr_batch
from src.srim.runtime import prepare_sr_simulation


class TestPreparedQueue(unittest.TestCase):
    """Test batches with temporary project state and synchronized controlled native calls.

    Inherited unittest construction takes a test method name. setUp provides
    paths/config/worker, activity counters, optional barrier/failure controls,
    expected runtimes and ordered native starts for assertions and cleanup.
    """

    def setUp(self: TestPreparedQueue) -> None:
        """Create self's isolated project and synchronization controls; return None and register cleanup."""
        self.temporary = TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temporary.cleanup)
        self.paths, self.config, self.worker = make_test_project(Path(self.temporary.name).absolute())
        self.guard = Lock()
        self.active = 0
        self.peak = 0
        self.starts: list[str] = []
        self.active_workers: set[Path] = set()
        self.barrier: Barrier | None = None
        self.failure: Path | None = None
        self.exception: Path | None = None
        self.interrupt: Path | None = None
        self.expected_runtimes: tuple[Path, ...] = ()

    def _make_task(self: TestPreparedQueue, worker: CreatedDirectory) -> PreparedSRTask:
        """Prepare one new simulation for self's supplied worker; return its task or propagate setup errors."""
        created = create_directories(self.paths, self.config, [CreateRequest("srim")]).completed[0]
        request = make_test_request()
        request["parameters"]["correction"] = {"mode": "none", "applied_factor": "1.0"}
        runtime = prepare_sr_simulation(self.paths, self.config, created, worker, request, make_test_selections())
        return PreparedSRTask(created.path.name, runtime)

    def _fake_run(self: TestPreparedQueue, args: list[str], *, cwd: Path, stdin: int, stdout: BinaryIO, stderr: BinaryIO, timeout: float, check: bool, creationflags: int, startupinfo: subprocess.STARTUPINFO | None) -> subprocess.CompletedProcess[bytes]:
        """Measure self's native activity from args/cwd and simulate bounded output.

        stdin/stdout/stderr/timeout/check/creationflags/startupinfo are forwarded
        to the controlled engine. Assert worker exclusion and runtime retention,
        optionally synchronize starts or raise configured errors, and return a
        CompletedProcess. Always release activity counters on failure.
        """
        worker = cwd.parent.parent
        with self.guard:
            self.assertNotIn(worker, self.active_workers)
            self.active_workers.add(worker)
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.starts.append(cwd.name)
        try:
            for runtime in self.expected_runtimes:
                self.assertTrue(runtime.is_dir())
            if self.barrier is not None:
                self.barrier.wait(timeout=10)
            else:
                Event().wait(0.04)
            if cwd == self.exception:
                raise LookupError("controlled unexpected failure")
            if cwd == self.interrupt:
                raise KeyboardInterrupt()
            result = fake_bragg_run(args, cwd=cwd, stdin=stdin, stdout=stdout, stderr=stderr, timeout=timeout, check=check, creationflags=creationflags, startupinfo=startupinfo)
            return subprocess.CompletedProcess(args, 7) if cwd == self.failure else result
        finally:
            with self.guard:
                self.active -= 1
                self.active_workers.remove(worker)

    def test_validation(self: TestPreparedQueue) -> None:
        """Reject self's invalid batches before native launch and accept an empty batch; return None."""
        task = self._make_task(self.worker)
        with patch("src.srim.execution.subprocess.run") as launch:
            for count in (0, -1, True, 1.5):
                with self.subTest(count=count), self.assertRaises(ValueError):
                    run_sr_batch(self.paths, self.config, [task], max_workers=count)
            for timeout in (0, True, float("inf"), 10 ** 1000):
                with self.subTest(timeout=repr(timeout)), self.assertRaises(ValueError):
                    run_sr_batch(self.paths, self.config, [task], max_workers=1, timeout_seconds=timeout)
            with self.assertRaises(ValueError):
                run_sr_batch(self.paths, self.config, [task, task], max_workers=2)
            result = run_sr_batch(self.paths, self.config, [], max_workers=1)
            self.assertEqual(result.task_results, ())
            self.assertEqual(result.cleanup.cleaned_workers, ())
            launch.assert_not_called()
        self.assertEqual(read_simulation(self.paths, task.simulation_id)["status"], "created")

    def test_distinct_workers_overlap(self: TestPreparedQueue) -> None:
        """Require self's two distinct native calls to overlap and preserve results after cleanup; return None."""
        other = create_directories(self.paths, self.config, [CreateRequest("worker")]).completed[0]
        tasks = [self._make_task(worker) for worker in (self.worker, other)]
        self.barrier = Barrier(2)
        with patch("src.srim.execution.subprocess.run", side_effect=self._fake_run):
            result = run_sr_batch(self.paths, self.config, tasks, max_workers=2)
        self.assertEqual(self.peak, 2)
        self.assertTrue(all(item.metadata["status"] == "succeeded" for item in result.task_results))
        self.assertEqual(len(result.cleanup.cleaned_workers), 2)
        self.assertFalse(result.cleanup.retained_workers)
        for task in tasks:
            self.assertTrue((self.paths.simulation_memory / task.simulation_id / "output/raw/runtime/stopping.txt").is_file())
            self.assertFalse(task.runtime.exists())

    def test_concurrency_limit(self: TestPreparedQueue) -> None:
        """Bound self's six tasks across three workers to two active calls; return None."""
        workers = [self.worker, *create_directories(self.paths, self.config, [CreateRequest("worker"), CreateRequest("worker")]).completed]
        tasks = [self._make_task(workers[index % 3]) for index in range(6)]
        with patch("src.srim.execution.subprocess.run", side_effect=self._fake_run):
            result = run_sr_batch(self.paths, self.config, tasks, max_workers=2)
        self.assertLessEqual(self.peak, 2)
        self.assertTrue(all(item.error is None and item.metadata["status"] == "succeeded" for item in result.task_results))

    def test_same_worker_order(self: TestPreparedQueue) -> None:
        """Keep self's single-worker submission order despite a larger concurrency limit; return None."""
        tasks = [self._make_task(self.worker) for _ in range(3)]
        with patch("src.srim.execution.subprocess.run", side_effect=self._fake_run):
            result = run_sr_batch(self.paths, self.config, tasks, max_workers=3)
        self.assertEqual(self.peak, 1)
        self.assertEqual(self.starts, [task.simulation_id for task in tasks])
        self.assertEqual(tuple(item.task for item in result.task_results), tuple(tasks))

    def test_result_order(self: TestPreparedQueue) -> None:
        """Preserve self's input order while skipping tasks whose worker remains busy; return None."""
        other = create_directories(self.paths, self.config, [CreateRequest("worker")]).completed[0]
        tasks = [self._make_task(worker) for worker in (self.worker, self.worker, other)]
        with patch("src.srim.execution.subprocess.run", side_effect=self._fake_run):
            result = run_sr_batch(self.paths, self.config, tasks, max_workers=2)
        self.assertEqual(tuple(item.task for item in result.task_results), tuple(tasks))
        self.assertLess(self.starts.index(tasks[2].simulation_id), self.starts.index(tasks[1].simulation_id))

    def test_failure_isolation(self: TestPreparedQueue) -> None:
        """Keep self's failed metadata, unexpected error and later success independent; return None."""
        tasks = [self._make_task(self.worker) for _ in range(3)]
        self.failure, self.exception = tasks[0].runtime, tasks[1].runtime
        with patch("src.srim.execution.subprocess.run", side_effect=self._fake_run):
            result = run_sr_batch(self.paths, self.config, tasks, max_workers=2)
        self.assertEqual(result.task_results[0].metadata["status"], "failed")
        self.assertIsInstance(result.task_results[1].error, LookupError)
        self.assertEqual(result.task_results[2].metadata["status"], "succeeded")
        self.assertEqual(len(result.cleanup.retained_workers), 1)

    def test_interruption(self: TestPreparedQueue) -> None:
        """Stop self's dispatch on interruption, retain workers and leave pending records created; return None."""
        tasks = [self._make_task(self.worker) for _ in range(2)]
        self.interrupt = tasks[0].runtime
        with patch("src.srim.execution.subprocess.run", side_effect=self._fake_run), self.assertRaises(KeyboardInterrupt):
            run_sr_batch(self.paths, self.config, tasks, max_workers=1)
        self.assertTrue(self.worker.path.is_dir())
        self.assertEqual(read_simulation(self.paths, tasks[1].simulation_id)["status"], "created")
