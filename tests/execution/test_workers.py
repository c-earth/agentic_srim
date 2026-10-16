"""Use mocked Python workers and isolated SQLite records to test dispatch failures.

Inputs are unittest discovery and SR/TRIM regression fixtures. Results are
assertions on diagnostics, response states, unchanged archives and recovery.
Only NativeWorkspaceTests launches simulators, with both native gates enabled.
"""

import uuid
import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import MagicMock, patch

from agentic_srim.capabilities.stopping_range.workflow import execute, submit
from agentic_srim.execution.jobs import read_verified, recover
from agentic_srim.execution.workers import dispatch
from agentic_srim.storage.artifacts import checksum, write_json
from agentic_srim.storage.database import connect, get_run, now, transition
from sr_module.support import IsolatedTest
from agentic_srim.config import Settings
from agentic_srim.backends.sr_module.output import parse
from agentic_srim.capabilities.trim.workflow import submit as submit_trim
from sr_module.support import NATIVE
from trim.support import fixture_request


class WorkerTests(IsolatedTest):
    """Construct isolated unittest cases; verify dispatch errors without native execution."""

    def unfinished_run(self, state: str) -> dict:
        """Accept a run phase; persist a queued/interrupted test run and return its record."""
        run_id = submit(self.request, self.settings)
        if state != "QUEUED":
            attempt_id = uuid.uuid4().hex
            archive = self.runtime / "runs" / run_id / "attempts" / attempt_id
            archive.mkdir(parents=True)
            write_json(archive / "request.json", self.data)
            with connect(self.runtime) as connection:
                connection.execute("INSERT INTO attempts(id,run_id,ordinal,state,started_at,artifact_path) VALUES(?,?,?,?,?,?)",
                                   (attempt_id, run_id, 1, "RUNNING", now(), str(archive.relative_to(self.runtime))))
                transition(connection, run_id, state, attempt_id)
        return get_run(self.runtime, run_id)

    def checked_run(self) -> dict:
        """Accept no inputs; return a sealed fixture-backed SR run without launching SR."""
        with patch("agentic_srim.capabilities.stopping_range.workflow.run", side_effect=self.simulate):
            return execute(self.runtime, submit(self.request, self.settings))

    def dispatch_exit(self, run_id: str, code: int, stderr: bytes = b"") -> dict:
        """Accept run ID, exit code and stderr; return dispatch with a mocked exited worker."""
        process = MagicMock(returncode=code)
        process.communicate.return_value = (b"", stderr)
        with patch("agentic_srim.execution.workers.subprocess.Popen", return_value=process):
            return dispatch(self.runtime, run_id)

    def test_crashed_active_workers_retain_diagnostics_and_remain_recoverable(self) -> None:
        """Accept no inputs; retain each active phase and recover without native work; return nothing."""
        for state in ("RUNNING", "COLLECTED", "PARSED"):
            with self.subTest(state=state):
                before = self.unfinished_run(state)
                result = self.dispatch_exit(before["id"], 7, b"archive finalization failed")
                self.assertEqual(result["state"], state)
                self.assertEqual(result["attempts"], before["attempts"])
                self.assertIn("code 7", result["error"])
                self.assertIn("archive finalization failed", result["error"])
                event = result["events"][-1]
                self.assertEqual(event["state"], state)
                self.assertEqual(event["attempt_id"], before["attempts"][0]["id"])
                self.assertEqual(event["detail"], result["error"])
                archive = self.runtime / before["attempts"][0]["artifact_path"]
                self.assertFalse((archive / "outcome.json").exists())
                self.assertFalse((archive / "manifest.json").exists())
                with patch("agentic_srim.execution.workers.subprocess.Popen") as launch:
                    self.assertEqual(recover(self.runtime), [before["id"]])
                    launch.assert_not_called()
                restored = read_verified(self.runtime, before["id"])
                self.assertEqual(restored["state"], "FAILED")
                self.assertEqual(restored["attempts"][0]["state"], "FAILED")
                self.assertIn(event, restored["events"])

    def test_stderr_tail_and_exit_code_survive_without_decoding_failure(self) -> None:
        """Accept no inputs; bound retained stderr and replace invalid UTF-8; return nothing."""
        before = self.unfinished_run("RUNNING")
        stderr = b"discarded-prefix" + b"x" * 5000 + b"\xff last failure"
        result = self.dispatch_exit(before["id"], 23, stderr)
        expected = "Worker exited with code 23\n" + stderr.decode("utf-8", errors="replace")[-4000:]
        self.assertEqual(result["error"], expected)
        self.assertEqual(get_run(self.runtime, before["id"])["events"][-1]["detail"], expected)
        self.assertNotIn("discarded-prefix", result["error"])

    def test_startup_failure_only_finalizes_unstarted_runs(self) -> None:
        """Accept no inputs; record launch errors without finalizing existing attempts; return nothing."""
        for state in ("QUEUED", "RUNNING"):
            with self.subTest(state=state):
                before = self.unfinished_run(state)
                with patch("agentic_srim.execution.workers.subprocess.Popen", side_effect=OSError("cannot start worker")):
                    result = dispatch(self.runtime, before["id"])
                self.assertEqual(result["state"], "FAILED" if state == "QUEUED" else state)
                self.assertEqual(result["attempts"], before["attempts"])
                self.assertIn("Worker startup failed", result["error"])
                self.assertIn("cannot start worker", result["events"][-1]["detail"])

    def test_nonzero_exit_before_attempt_creation_is_failed(self) -> None:
        """Accept no inputs; mark an unstarted worker failure and retain its exit code; return nothing."""
        record = self.unfinished_run("QUEUED")
        result = self.dispatch_exit(record["id"], 9)
        self.assertEqual(result["state"], "FAILED")
        self.assertEqual(result["attempts"], [])
        self.assertEqual(result["error"], "Worker exited with code 9")

    def test_zero_exit_without_terminal_state_is_incomplete(self) -> None:
        """Accept no inputs; detect silent unfinished workers without finalizing attempts; return nothing."""
        for state in ("QUEUED", "RUNNING", "COLLECTED", "PARSED"):
            with self.subTest(state=state):
                before = self.unfinished_run(state)
                result = self.dispatch_exit(before["id"], 0, b"worker stopped early")
                self.assertEqual(result["state"], "FAILED" if state == "QUEUED" else state)
                self.assertEqual(result["attempts"], before["attempts"])
                self.assertIn("execution is incomplete", result["error"])
                self.assertIn("worker stopped early", result["error"])

    def test_late_crash_preserves_checked_result_and_sealed_files(self) -> None:
        """Accept no inputs; retain late exit warnings without changing completed evidence; return nothing."""
        before = self.checked_run()
        archive = self.runtime / before["attempts"][0]["artifact_path"]
        hashes = {path: checksum(path) for path in archive.rglob("*") if path.is_file()}
        result = self.dispatch_exit(before["id"], 4, b"worker cleanup failed")
        self.assertEqual(result["state"], "CHECKED")
        self.assertEqual(result["result"], before["result"])
        self.assertEqual(result["attempts"], before["attempts"])
        self.assertIsNone(result["error"])
        self.assertEqual(result["warnings"], ["Worker exited with code 4\nworker cleanup failed"])
        self.assertEqual(result["events"][-1]["state"], "WORKER_WARNING")
        self.assertEqual(result["events"][-1]["attempt_id"], before["attempts"][0]["id"])
        self.assertEqual(result["events"][-1]["detail"], result["warnings"][0])
        self.assertEqual(hashes, {path: checksum(path) for path in archive.rglob("*") if path.is_file()})
        self.assertEqual(read_verified(self.runtime, before["id"]), result)
        self.assertEqual(self.dispatch_exit(before["id"], 0), result)

    def test_exit_diagnostic_is_saved_before_archive_verification(self) -> None:
        """Accept no inputs; retain crash evidence even when archive verification fails; return nothing."""
        before = self.checked_run()
        (self.runtime / before["attempts"][0]["artifact_path"] / "manifest.json").unlink()
        with self.assertRaises(FileNotFoundError):
            self.dispatch_exit(before["id"], 5, b"worker exit after damaged archive")
        stored = get_run(self.runtime, before["id"])
        self.assertIsNone(stored["error"])
        self.assertIn("code 5", stored["warnings"][0])
        self.assertIn("worker exit after damaged archive", stored["events"][-1]["detail"])
        self.assertNotIn("startup failed", stored["warnings"][0])

    def test_existing_failure_and_normal_completed_runs_are_preserved(self) -> None:
        """Accept no inputs; retain prior failure details and normal success/history; return nothing."""
        failed = self.unfinished_run("QUEUED")
        with connect(self.runtime) as connection:
            transition(connection, failed["id"], "FAILED", detail="original execution failure")
        result = self.dispatch_exit(failed["id"], 6, b"secondary worker failure")
        self.assertIn("original execution failure", result["error"])
        self.assertIn("secondary worker failure", result["error"])
        self.assertEqual(self.dispatch_exit(failed["id"], 0), result)
        checked = self.checked_run()
        self.assertEqual(self.dispatch_exit(checked["id"], 0), checked)


class NativeWorkspaceTests(IsolatedTest):
    """Construct temporary runtimes; verify concurrent native workers when both gates permit it."""

    @unittest.skipUnless(os.environ.get("AGENTIC_SRIM_INTEGRATION") == "1" and os.environ.get("AGENTIC_SRIM_TRIM_INTEGRATION") == "1", "Both native gates explicitly required")
    def test_concurrent_runtimes_reuse_sibling_backends_without_cross_talk(self) -> None:
        """Accept no inputs; run eight native jobs across two runtimes and verify isolated evidence; return nothing."""
        runtimes = [self.runtime / name for name in ("left", "right")]
        settings = [Settings(self.settings.sr_module_directory, runtime,
                             trim_directory=self.settings.sr_module_directory.parent) for runtime in runtimes]
        expected = parse(NATIVE.read_text(encoding="cp1252"), self.request).to_dict()["rows"]
        records = []
        for modes in ((0, 0), (2, 8), (0, 8), (2, 0)):
            barrier = Barrier(2)
            jobs = [(runtime, submit_trim(fixture_request(mode), config) if mode else submit(self.request, config))
                    for runtime, config, mode in zip(runtimes, settings, modes)]

            def launch(job: tuple) -> dict:
                """Accept a runtime/run pair; synchronize dispatch and return verified native results."""
                barrier.wait(timeout=10)
                return dispatch(*job)

            with ThreadPoolExecutor(max_workers=2) as pool:
                pair = list(pool.map(launch, jobs))
            self.assertLess(max(record["attempts"][0]["started_at"] for record in pair),
                            min(record["attempts"][0]["finished_at"] for record in pair))
            for (runtime, run_id), record, mode in zip(jobs, pair, modes):
                self.assertEqual(record["state"], "CHECKED", record.get("error"))
                self.assertEqual(read_verified(runtime, run_id), record)
                other = runtimes[1] if runtime == runtimes[0] else runtimes[0]
                self.assertFalse((other / "runs" / run_id).exists())
                if mode:
                    self.assertEqual(record["result"]["summary"]["actual_ion_count"], 3)
                    self.assertEqual(record["request"]["calculation_mode"], mode)
                else:
                    self.assertEqual(record["result"]["rows"], expected)
                records.append((runtime, record))
        self.assertEqual(len({record["id"] for _, record in records}), 8)
        for runtime, record in records:
            self.assertEqual(read_verified(runtime, record["id"]), record)
        for runtime in runtimes:
            worker = runtime / "workers" / "worker-1"
            self.assertTrue((worker / "sr_module" / "SRModule.exe").is_file())
            self.assertTrue((worker / "trim" / "TRIM.exe").is_file())
            latest = [record for owner, record in records if owner == runtime][-1]
            marker = json.loads((worker / "active_attempt.json").read_text(encoding="utf-8"))
            self.assertEqual(marker, {"attempt_id": latest["attempts"][0]["id"]})
            self.assertEqual(recover(runtime), [])
