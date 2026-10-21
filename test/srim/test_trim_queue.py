"""Verify TRIM batch ordering, independent failures and explicit cleanup policy."""

from __future__ import annotations

from pathlib import Path
from concurrent.futures import Future
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from _fixtures import make_test_project
from test_trim_runtime import CACHE
from src.srim.storage import BatchCleanupReport
from src.srim.trim_queue import PreparedTRIMTask, TRIMTaskResult, run_trim_batch


class TestTRIMQueue(unittest.TestCase):
    """Exercise scheduling with mocked native task outcomes and no real processes."""

    def test_input_order_and_same_worker_serialization(self: TestTRIMQueue) -> None:
        """Keep submitted same-worker tasks ordered even with higher capacity; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            paths, config, worker = make_test_project(Path(directory))
            tasks = [PreparedTRIMTask(str(i), worker.path / "trim" / str(i)) for i in range(4)]
            outcomes = [TRIMTaskResult(task, {"status": "succeeded"}, None, 0.0) for task in tasks]
            with patch("src.srim.trim_queue.verify_trim_runtime"), patch("src.srim.trim_queue._execute_trim_task", side_effect=outcomes) as execute:
                result = run_trim_batch(paths, config, tasks, max_workers=3)
            self.assertEqual([r.task for r in result.task_results], tasks)
            self.assertEqual([call.args[2] for call in execute.call_args_list], tasks)
            self.assertEqual(result.cleanup, BatchCleanupReport((), ()))

    def test_preflight_before_launch(self: TestTRIMQueue) -> None:
        """Reject duplicate tasks before dispatch and native state changes; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            paths, config, worker = make_test_project(Path(directory))
            task = PreparedTRIMTask("same", worker.path / "trim/same")
            with patch("src.srim.trim_queue.verify_trim_runtime"), patch("src.srim.trim_queue._execute_trim_task") as execute:
                with self.assertRaises(ValueError):
                    run_trim_batch(paths, config, [task, task], max_workers=2)
                execute.assert_not_called()

    def test_independent_failures(self: TestTRIMQueue) -> None:
        """Continue next same-worker task after an ordinary failed result; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            paths, config, worker = make_test_project(Path(directory))
            tasks = [PreparedTRIMTask(str(i), worker.path / "trim" / str(i)) for i in range(2)]
            outcomes = [TRIMTaskResult(tasks[0], {"status": "failed"}, None, 0.0), TRIMTaskResult(tasks[1], {"status": "succeeded"}, None, 0.0)]
            with patch("src.srim.trim_queue.verify_trim_runtime"), patch("src.srim.trim_queue._execute_trim_task", side_effect=outcomes):
                result = run_trim_batch(paths, config, tasks, max_workers=2)
            self.assertEqual([r.metadata["status"] for r in result.task_results], ["failed", "succeeded"])

    def test_explicit_cleanup(self: TestTRIMQueue) -> None:
        """Only invoke cleanup when explicitly requested after task completion; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            paths, config, worker = make_test_project(Path(directory))
            task = PreparedTRIMTask("one", worker.path / "trim/one")
            with patch("src.srim.trim_queue.verify_trim_runtime"), patch("src.srim.trim_queue._execute_trim_task", return_value=TRIMTaskResult(task, {"status": "succeeded"}, None, 0.0)), patch("src.srim.trim_queue.cleanup_trim_batch", return_value=BatchCleanupReport((worker.path,), ())) as cleanup:
                result = run_trim_batch(paths, config, [task], max_workers=1, cleanup=True)
            cleanup.assert_called_once_with(paths, (worker.path,))
            self.assertEqual(result.cleanup.cleaned_workers, (worker.path,))

    def test_parallel_distinct_workers(self: TestTRIMQueue) -> None:
        """Dispatch another worker before the pending task on an occupied worker; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            paths, config, worker = make_test_project(Path(directory))
            tasks = [PreparedTRIMTask("first", worker.path / "trim/first"), PreparedTRIMTask("second", worker.path / "trim/second"), PreparedTRIMTask("third", paths.workers / "worker_2/trim/third")]
            futures: list[Future[TRIMTaskResult]] = []
            for index in (0, 2, 1):
                future: Future[TRIMTaskResult] = Future()
                future.set_result(TRIMTaskResult(tasks[index], {"status": "succeeded"}, None, 0.0))
                futures.append(future)
            with patch("src.srim.trim_queue.verify_trim_runtime"), patch("src.srim.trim_queue.ThreadPoolExecutor") as factory:
                factory.return_value.submit.side_effect = futures
                result = run_trim_batch(paths, config, tasks, max_workers=2)
            self.assertEqual([call.args[3] for call in factory.return_value.submit.call_args_list], [tasks[0], tasks[2], tasks[1]])
            self.assertEqual([r.task for r in result.task_results], tasks)

    def test_interrupt_cancels_active_jobs(self: TestTRIMQueue) -> None:
        """Set native cancellation signal and skip cleanup when a task interrupts; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            paths, config, worker = make_test_project(Path(directory))
            task = PreparedTRIMTask("first", worker.path / "trim/first")
            future: Future[TRIMTaskResult] = Future()
            future.set_exception(KeyboardInterrupt())
            with patch("src.srim.trim_queue.verify_trim_runtime"), patch("src.srim.trim_queue.ThreadPoolExecutor") as factory, patch("src.srim.trim_queue.cleanup_trim_batch") as cleanup:
                factory.return_value.submit.return_value = future
                with self.assertRaises(KeyboardInterrupt):
                    run_trim_batch(paths, config, [task], max_workers=1, cleanup=True)
                self.assertTrue(factory.return_value.submit.call_args.args[-1]())
                cleanup.assert_not_called()
