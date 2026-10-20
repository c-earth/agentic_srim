"""Verify numbered directories, durable deletion, batch reports, and Windows process locking."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Event, Thread, Timer
import unittest
from unittest.mock import patch

from src.backend.config import Config
from src.backend.paths import create_base_directories, get_project_paths
from src.backend.records import (
    BatchOperationError, CreateRequest, DeleteRequest, _lock_parent,
    _read_tombstone, _record_time, _lock_worker, create_directories, delete_directories,
)


class TestRecords(unittest.TestCase):
    """Use unittest construction to test record APIs with automatically removed fixtures beneath this module."""

    def _hold_lock(self: TestRecords, parent: Path, ready: Event, release: Event) -> None:
        """Hold parent until release, signaling ready for self's contention test; return None or propagate lock errors."""
        with _lock_parent(parent):
            ready.set()
            release.wait(timeout=5)

    def test_bounded_lock_waiting(self: TestRecords) -> None:
        """Check self's cross-thread timeout, release and immediate validation failures; return None and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            parent = Path(directory).absolute()
            ready, release = Event(), Event()
            holder = Thread(target=self._hold_lock, args=(parent, ready, release))
            holder.start()
            try:
                self.assertTrue(ready.wait(timeout=5))
                with self.assertRaises(OSError):
                    with _lock_parent(parent, timeout_seconds=0.03):
                        pass
                release_timer = Timer(0.03, release.set)
                release_timer.start()
                try:
                    with _lock_parent(parent, timeout_seconds=1.0):
                        self.assertTrue(release.is_set())
                finally:
                    release_timer.join(timeout=5)
            finally:
                release.set()
                holder.join(timeout=5)
            with _lock_parent(parent, timeout_seconds=1.0):
                with self.assertRaises(OSError):
                    with _lock_parent(parent, timeout_seconds=1.0):
                        pass
            for invalid in (True, -1, float("inf"), float("nan"), 10 ** 1000):
                with self.assertRaises(ValueError):
                    with _lock_parent(parent, timeout_seconds=invalid):
                        pass

    def test_independent_worker_leases(self: TestRecords) -> None:
        """Check self's worker leases release the parent, allow other workers and reject active deletion; return None."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths = get_project_paths(Path(directory).absolute())
            create_base_directories(paths)
            config = Config("UTC", paths.root)
            first, second = create_directories(paths, config, [CreateRequest("worker"), CreateRequest("worker")]).completed
            with _lock_worker(paths, first.path):
                with _lock_parent(paths.workers):
                    pass
                with _lock_worker(paths, second.path):
                    pass
                with self.assertRaises(BatchOperationError):
                    delete_directories(paths, config, [DeleteRequest("worker", first.path.name)])
                delete_directories(paths, config, [DeleteRequest("worker", second.path.name)])
                self.assertTrue(first.path.exists())
                self.assertFalse(second.path.exists())
            delete_directories(paths, config, [DeleteRequest("worker", first.path.name)])

    def test_mixed_creation_and_collisions(self: TestRecords) -> None:
        """Check self's mixed names, numeric collisions, worker layout, and independent continuations; return None and remove fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths = get_project_paths(Path(directory).absolute())
            create_base_directories(paths)
            config = Config("UTC", paths.root)
            instant = datetime(2026, 10, 17, 14, 30, 25, tzinfo=timezone.utc)
            (paths.simulation_memory / "srim_20261017_143025_1").write_text("occupied")
            requests = [CreateRequest("srim", instant), CreateRequest("post", instant), CreateRequest("case", instant), CreateRequest("worker")]
            report = create_directories(paths, config, requests)
            self.assertEqual([item.path.name for item in report.completed], ["srim_20261017_143025_2", "post_20261017_143025_1", "case_20261017_143025_1", "worker_1"])
            self.assertEqual({item.name for item in (paths.workers / "worker_1").iterdir()}, {"sr", "trim"})
            self.assertIsNone(report.completed[-1].timestamp)
            self.assertEqual(create_directories(paths, config, [requests[0]]).completed[0].suffix, 3)
            many = create_directories(paths, config, [requests[0]] * 9)
            self.assertEqual([item.suffix for item in many.completed], list(range(4, 13)))
            self.assertIsNone(report.failed_index)

    def test_timezones(self: TestRecords) -> None:
        """Check self's UTC, signed-offset, local, and invalid time conversions; return None without persistent side effects."""
        instant = datetime(2026, 10, 17, 23, 30, tzinfo=timezone.utc)
        root = Path(__file__).absolute().parent
        for setting, expected in (("UTC", instant), ("UTC+02:30", instant.astimezone(timezone(timedelta(hours=2, minutes=30)))), ("UTC-03:00", instant.astimezone(timezone(-timedelta(hours=3))))):
            result = _record_time(Config(setting, root), instant)
            self.assertEqual(result.isoformat(), expected.isoformat())
        self.assertEqual(_record_time(Config("system", root), instant).isoformat(), instant.astimezone().isoformat())
        self.assertIsNotNone(_record_time(Config("UTC", root), None).utcoffset())
        for setting in ("bad", "UTC+24:00", "UTC+00:60"):
            with self.assertRaises(ValueError):
                _record_time(Config(setting, root), instant)
        with self.assertRaises(ValueError):
            _record_time(Config("UTC", root), datetime(2026, 1, 1))

    def test_validation_before_mutation(self: TestRecords) -> None:
        """Check all self's invalid requests fail before creating anything; return None and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths = get_project_paths(Path(directory).absolute())
            create_base_directories(paths)
            config = Config("UTC", paths.root)
            for request in (CreateRequest("unknown"), CreateRequest("worker", datetime.now(timezone.utc)), CreateRequest("srim", datetime.now())):
                with self.assertRaises(ValueError):
                    create_directories(paths, config, [CreateRequest("worker"), request])
            for name in ("../case_20261017_000000_1", "case_20260230_000000_1", "case_20261017_000000_01", "case_20261017_000000_0", "case_20261017_250000_1", "CASE_20261017_000000_1"):
                with self.assertRaises(ValueError):
                    delete_directories(paths, config, [DeleteRequest("case", name)])
            self.assertEqual(list(paths.workers.iterdir()), [])
            self.assertEqual(create_directories(paths, config, []).completed, ())
            self.assertEqual(delete_directories(paths, config, []).completed, ())

    def test_durable_deletion_and_worker_reuse(self: TestRecords) -> None:
        """Check self's content deletion, tombstone schema, permanent reservation, idempotence, and worker reuse; return None and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths = get_project_paths(Path(directory).absolute())
            create_base_directories(paths)
            config = Config("UTC", paths.root)
            instant = datetime(2026, 10, 17, tzinfo=timezone.utc)
            created = create_directories(paths, config, [CreateRequest("srim", instant), CreateRequest("post", instant), CreateRequest("case", instant), CreateRequest("worker")]).completed
            for item in created:
                (item.path / "output.txt").write_text("evidence")
            requests = [DeleteRequest(item.kind, item.path.name, "test reason") for item in created]
            report = delete_directories(paths, config, requests)
            self.assertEqual(len(report.completed), 4)
            for item in report.completed[:3]:
                data = _read_tombstone(item.tombstone)
                self.assertEqual(data["state"], "deleted")
                self.assertEqual(data["reason"], "test reason")
                self.assertFalse(item.path.exists())
                self.assertTrue(item.tombstone.read_bytes().endswith(b"\n"))
            self.assertIsNone(report.completed[-1].tombstone)
            self.assertEqual(delete_directories(paths, config, requests[:3]).completed, report.completed[:3])
            repeated = create_directories(paths, config, [CreateRequest("srim", instant), CreateRequest("worker")])
            self.assertEqual([item.suffix for item in repeated.completed], [2, 1])
            created[0].path.mkdir()
            with self.assertRaises(BatchOperationError) as caught:
                delete_directories(paths, config, requests[:1])
            self.assertIsInstance(caught.exception.__cause__, FileExistsError)

    def test_interrupted_deletion_retry(self: TestRecords) -> None:
        """Check self's pending tombstone survives failure, reserves identity, and retains original intent on retry; return None and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths = get_project_paths(Path(directory).absolute())
            create_base_directories(paths)
            config = Config("UTC", paths.root)
            request = CreateRequest("case", datetime(2026, 10, 17, tzinfo=timezone.utc))
            item = create_directories(paths, config, [request]).completed[0]
            deletion = DeleteRequest("case", item.path.name, "original")
            with patch("src.backend.records.shutil.rmtree", side_effect=PermissionError("busy")):
                with self.assertRaises(BatchOperationError) as caught:
                    delete_directories(paths, config, [deletion])
            tombstone = paths.cases / ".deleted" / f"{item.path.name}.json"
            pending = _read_tombstone(tombstone)
            self.assertEqual(pending["state"], "pending")
            self.assertIn(tombstone, caught.exception.report.partial_paths)
            self.assertIn(item.path, caught.exception.report.partial_paths)
            self.assertEqual(create_directories(paths, config, [request]).completed[0].suffix, 2)
            delete_directories(paths, config, [DeleteRequest("case", item.path.name, "replacement")])
            final = _read_tombstone(tombstone)
            self.assertEqual(final["requested_at"], pending["requested_at"])
            self.assertEqual(final["reason"], "original")

    def test_pending_after_removed_target(self: TestRecords) -> None:
        """Check self's final-write failure leaves recoverable pending intent after removal; return None and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths = get_project_paths(Path(directory).absolute())
            create_base_directories(paths)
            config = Config("UTC", paths.root)
            item = create_directories(paths, config, [CreateRequest("post")]).completed[0]
            from src.backend.records import _write_tombstone
            tombstone = paths.postprocessing_memory / ".deleted" / f"{item.path.name}.json"
            tombstone.parent.mkdir()
            _write_tombstone(tombstone, {"schema_version": 1, "kind": "post", "name": item.path.name, "state": "pending", "requested_at": datetime.now(timezone.utc).isoformat(), "deleted_at": None, "reason": None})
            with patch("src.backend.records._write_tombstone", side_effect=PermissionError("final write")):
                with self.assertRaises(BatchOperationError):
                    delete_directories(paths, config, [DeleteRequest("post", item.path.name)])
            self.assertFalse(item.path.exists())
            delete_directories(paths, config, [DeleteRequest("post", item.path.name)])
            self.assertEqual(_read_tombstone(tombstone)["state"], "deleted")

    def test_mixed_batch_failure(self: TestRecords) -> None:
        """Check self's first operational failure preserves successes and skips later items; return None and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths = get_project_paths(Path(directory).absolute())
            create_base_directories(paths)
            config = Config("UTC", paths.root)
            worker = create_directories(paths, config, [CreateRequest("worker")]).completed[0]
            with self.assertRaises(BatchOperationError) as caught:
                delete_directories(paths, config, [DeleteRequest("worker", worker.path.name), DeleteRequest("case", "case_20261017_000000_1"), DeleteRequest("worker", "worker_2")])
            report = caught.exception.report
            self.assertEqual(len(report.completed), 1)
            self.assertEqual(report.failed_index, 1)
            self.assertEqual(report.unattempted_indices, (2,))
            self.assertIsInstance(caught.exception.__cause__, FileNotFoundError)

    def test_creation_partial_worker(self: TestRecords) -> None:
        """Check self's failed worker subdirectory creation reports retained pieces and prior successes; return None and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths = get_project_paths(Path(directory).absolute())
            create_base_directories(paths)
            config = Config("UTC", paths.root)
            original = Path.mkdir

            def mkdir(path: Path, mode: int = 0o777, parents: bool = False, exist_ok: bool = False) -> None:
                """Create path with supplied mkdir options except trim, which raises PermissionError; return None otherwise."""
                if path.name == "trim":
                    raise PermissionError("blocked trim")
                original(path, mode=mode, parents=parents, exist_ok=exist_ok)

            with patch.object(Path, "mkdir", autospec=True, side_effect=mkdir):
                with self.assertRaises(BatchOperationError) as caught:
                    create_directories(paths, config, [CreateRequest("case"), CreateRequest("worker"), CreateRequest("post")])
            self.assertEqual(len(caught.exception.report.completed), 1)
            self.assertEqual(caught.exception.report.failed_index, 1)
            self.assertEqual(caught.exception.report.unattempted_indices, (2,))
            self.assertEqual(caught.exception.report.partial_paths, (paths.workers / "worker_1", paths.workers / "worker_1" / "sr"))

    def test_missing_bases_and_links(self: TestRecords) -> None:
        """Check self's missing bases and simulated path/descendant links reject operations; return None and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths = get_project_paths(Path(directory).absolute())
            config = Config("UTC", paths.root)
            with self.assertRaises(BatchOperationError):
                create_directories(paths, config, [CreateRequest("worker")])
            create_base_directories(paths)
            worker = create_directories(paths, config, [CreateRequest("worker")]).completed[0]
            for method in ("is_symlink", "is_junction"):
                for linked in (paths.root.parent, worker.path / "sr"):

                    def is_link(path: Path) -> bool:
                        """Return whether path is the simulated link, without side effects."""
                        return path == linked

                    with patch.object(Path, method, autospec=True, side_effect=is_link):
                        with self.assertRaises(BatchOperationError) as caught:
                            delete_directories(paths, config, [DeleteRequest("worker", worker.path.name)])
                    self.assertIsInstance(caught.exception.__cause__, OSError)
            self.assertTrue(worker.path.exists())

    def test_malformed_tombstones(self: TestRecords) -> None:
        """Check self's malformed schema, state, times, and identities are rejected; return None and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths = get_project_paths(Path(directory).absolute())
            create_base_directories(paths)
            config = Config("UTC", paths.root)
            item = create_directories(paths, config, [CreateRequest("case")]).completed[0]
            result = delete_directories(paths, config, [DeleteRequest("case", item.path.name)]).completed[0]
            original = _read_tombstone(result.tombstone)
            for changes in ({"schema_version": True}, {"name": "case_20261017_000000_99"}, {"state": "unknown"}, {"requested_at": "2026-10-17T00:00:00"}, {"deleted_at": None}, {"kind": "post"}, {"extra": 1}):
                result.tombstone.write_text(json.dumps(original | changes), encoding="utf-8")
                with self.assertRaises(ValueError):
                    _read_tombstone(result.tombstone)
            result.tombstone.write_text("{", encoding="utf-8")
            with self.assertRaises(BatchOperationError) as caught:
                delete_directories(paths, config, [DeleteRequest("case", item.path.name)])
            self.assertIsInstance(caught.exception.__cause__, ValueError)

    def test_tombstone_write_failure(self: TestRecords) -> None:
        """Check self's failed atomic replacement preserves target and reports synchronized temporary JSON; return None and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths = get_project_paths(Path(directory).absolute())
            create_base_directories(paths)
            config = Config("UTC", paths.root)
            item = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            with patch("src.backend.records.os.replace", side_effect=PermissionError("blocked replace")):
                with self.assertRaises(BatchOperationError) as caught:
                    delete_directories(paths, config, [DeleteRequest("srim", item.path.name)])
            self.assertTrue(item.path.exists())
            temporary = [path for path in caught.exception.report.partial_paths if path.suffix == ".tmp"]
            self.assertEqual(len(temporary), 1)
            self.assertEqual(json.loads(temporary[0].read_text())["state"], "pending")

    def test_process_locking_and_creation(self: TestRecords) -> None:
        """Check self's independent processes contend, release locks, and allocate unique names; return None and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths = get_project_paths(Path(directory).absolute())
            create_base_directories(paths)
            config = Config("UTC", paths.root)
            script = '"""Exercise allocation in a separate process."""\nimport sys\nfrom pathlib import Path\nfrom src.backend.config import Config\nfrom src.backend.paths import get_project_paths\nfrom src.backend.records import CreateRequest, BatchOperationError, create_directories\np = get_project_paths(Path(sys.argv[1]))\ntry:\n    r = create_directories(p, Config("UTC", p.root), [CreateRequest("worker")])\n    print(r.completed[0].path.name)\nexcept BatchOperationError:\n    sys.exit(7)\n'
            environment = os.environ.copy()
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            with _lock_parent(paths.workers):
                blocked = subprocess.run([r"D:\projects\agentic_srim\.venv\Scripts\python.exe", "-B", "-c", script, str(paths.root)], cwd=Path(__file__).absolute().parents[2], env=environment, capture_output=True, text=True, timeout=20)
            self.assertEqual(blocked.returncode, 7, blocked.stderr)
            lock_script = '"""Hold a lock until terminated to verify process-exit release."""\nimport sys\nfrom pathlib import Path\nfrom src.backend.records import _lock_parent\nwith _lock_parent(Path(sys.argv[1])):\n    print("locked", flush=True)\n    sys.stdin.read()\n'
            holder = subprocess.Popen([r"D:\projects\agentic_srim\.venv\Scripts\python.exe", "-B", "-c", lock_script, str(paths.workers)], cwd=Path(__file__).absolute().parents[2], env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                self.assertEqual(holder.stdout.readline().strip(), "locked")
                with self.assertRaises(BatchOperationError):
                    create_directories(paths, config, [CreateRequest("worker")])
            finally:
                holder.terminate()
                holder.communicate(timeout=20)
            processes = [subprocess.Popen([r"D:\projects\agentic_srim\.venv\Scripts\python.exe", "-B", "-c", script, str(paths.root)], cwd=Path(__file__).absolute().parents[2], env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(4)]
            names: list[str] = []
            for process in processes:
                stdout, stderr = process.communicate(timeout=20)
                self.assertIn(process.returncode, (0, 7), stderr)
                if process.returncode == 0:
                    names.append(stdout.strip())
            self.assertGreaterEqual(len(names), 1)
            self.assertEqual(len(names), len(set(names)))
            next_item = create_directories(paths, config, [CreateRequest("worker")]).completed[0]
            self.assertNotIn(next_item.path.name, names)
