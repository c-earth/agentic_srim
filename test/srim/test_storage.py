"""Verify permanent runtime evidence and conservative end-of-batch worker cleanup."""

from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

from _fixtures import fake_bragg_run
import test_queue as queue_tests
from src.backend.records import _lock_worker
from src.srim.sr.execution import run_sr_simulation
from src.srim.common.files import _inventory
from src.srim.common.records import read_simulation
from src.srim.sr.queue import run_sr_batch
from src.srim.sr.storage import cleanup_sr_batch, preserve_sr_runtime, verify_preserved_runtime


class TestSRStorage(unittest.TestCase):
    """Use inherited unittest construction and a prepared temporary queue fixture.

    setUp supplies fixture and task; fixture owns the isolated project, config,
    worker and cleanup callbacks. Tests operate only on those temporary paths.
    """

    def setUp(self: TestSRStorage) -> None:
        """Initialize self's shared fixture and prepared task; return None and register cleanup."""
        self.fixture = queue_tests.TestPreparedQueue("test_validation")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.task = self.fixture._make_task(self.fixture.worker)

    def test_runtime_preservation(self: TestSRStorage) -> None:
        """Verify self's exact permanent runtime remains after runtime cleanup; return None."""
        with patch("src.srim.sr.execution.subprocess.run", side_effect=fake_bragg_run):
            run_sr_simulation(self.fixture.paths, self.fixture.config, self.task.simulation_id, self.task.runtime)
        verify_preserved_runtime(self.fixture.paths, self.task.simulation_id, self.task.runtime)
        preserve_sr_runtime(self.fixture.paths, self.task.simulation_id, self.task.runtime)
        record = self.fixture.paths.simulation_memory / self.task.simulation_id
        saved = record / "output/raw/runtime"
        self.assertEqual(_inventory(saved, saved), _inventory(self.task.runtime, self.task.runtime))
        report = cleanup_sr_batch(self.fixture.paths, [self.fixture.worker.path])
        self.assertEqual(report.cleaned_workers, (self.fixture.worker.path,))
        self.assertTrue((saved / "stopping.txt").exists())

    def test_failed_run_preservation(self: TestSRStorage) -> None:
        """Preserve self's failed-launch diagnostics and safely clean its worker; return None."""
        with patch("src.srim.sr.execution.subprocess.run", side_effect=OSError("controlled launch failure")):
            metadata = run_sr_simulation(self.fixture.paths, self.fixture.config, self.task.simulation_id, self.task.runtime)
        self.assertEqual(metadata["status"], "failed")
        verify_preserved_runtime(self.fixture.paths, self.task.simulation_id, self.task.runtime)
        self.assertFalse(cleanup_sr_batch(self.fixture.paths, [self.fixture.worker.path]).retained_workers)

    def test_preservation_failure_retains_worker(self: TestSRStorage) -> None:
        """Keep self's working files and expose preservation failure separately from cleanup; return None."""
        with patch("src.srim.sr.execution.subprocess.run", side_effect=fake_bragg_run), patch("src.srim.sr.storage._write_input_file", side_effect=PermissionError("archive blocked")):
            result = run_sr_batch(self.fixture.paths, self.fixture.config, [self.task], max_workers=1)
        self.assertIsInstance(result.task_results[0].error, PermissionError)
        self.assertEqual(len(result.cleanup.retained_workers), 1)
        self.assertTrue(self.task.runtime.is_dir())

    def test_batch_end_cleanup(self: TestSRStorage) -> None:
        """Keep all self's runtimes during execution and remove them together after completion; return None."""
        other = self.fixture._make_task(self.fixture.worker)
        self.fixture.expected_runtimes = (self.task.runtime, other.runtime)
        with patch("src.srim.sr.execution.subprocess.run", side_effect=self.fixture._fake_run):
            result = run_sr_batch(self.fixture.paths, self.fixture.config, [self.task, other], max_workers=1)
        self.assertFalse(result.cleanup.retained_workers)
        self.assertTrue(self.fixture.worker.path.is_dir())
        self.assertEqual(list((self.fixture.worker.path / "sr").iterdir()), [])
        self.assertEqual(list((self.fixture.worker.path / "trim").iterdir()), [])
        reused = self.fixture._make_task(self.fixture.worker)
        with patch("src.srim.sr.execution.subprocess.run", side_effect=fake_bragg_run):
            next_batch = run_sr_batch(self.fixture.paths, self.fixture.config, [reused], max_workers=1)
        self.assertEqual(next_batch.cleanup.cleaned_workers, (self.fixture.worker.path,))
        self.assertEqual(next_batch.task_results[0].metadata["status"], "succeeded")

    def test_unrelated_files_retain_worker(self: TestSRStorage) -> None:
        """Retain self's worker containing unrelated files or an unsubmitted prepared task; return None."""
        extra = self.fixture._make_task(self.fixture.worker)
        with patch("src.srim.sr.execution.subprocess.run", side_effect=fake_bragg_run):
            result = run_sr_batch(self.fixture.paths, self.fixture.config, [self.task], max_workers=1)
        self.assertEqual(len(result.cleanup.retained_workers), 1)
        self.assertTrue(extra.runtime.exists())
        (self.fixture.worker.path / "keep").write_bytes(b"unrelated")
        report = cleanup_sr_batch(self.fixture.paths, [self.fixture.worker.path])
        self.assertIn("unrelated", report.retained_workers[0].reason)

    def test_busy_worker_is_retained(self: TestSRStorage) -> None:
        """Report self's already leased worker without deleting it; return None."""
        with _lock_worker(self.fixture.paths, self.fixture.worker.path):
            report = cleanup_sr_batch(self.fixture.paths, [self.fixture.worker.path])
        self.assertEqual(len(report.retained_workers), 1)
        self.assertTrue(self.fixture.worker.path.is_dir())

    def test_unsafe_paths_are_rejected(self: TestSRStorage) -> None:
        """Reject self's outside-root targets and altered permanent inventories; return None."""
        outside = self.fixture.paths.root / "installation"
        report = cleanup_sr_batch(self.fixture.paths, [outside, Path("relative")])
        self.assertEqual(len(report.retained_workers), 2)
        self.assertTrue(outside.is_dir())
        with patch("src.srim.sr.execution.subprocess.run", side_effect=fake_bragg_run):
            run_sr_simulation(self.fixture.paths, self.fixture.config, self.task.simulation_id, self.task.runtime)
        record = self.fixture.paths.simulation_memory / self.task.simulation_id
        (record / "output/raw/runtime/SR.IN").write_bytes(b"corrupted")
        with self.assertRaises(ValueError):
            verify_preserved_runtime(self.fixture.paths, self.task.simulation_id, self.task.runtime)
        self.assertEqual(read_simulation(self.fixture.paths, self.task.simulation_id)["status"], "succeeded")
