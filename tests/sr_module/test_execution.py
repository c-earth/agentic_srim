"""Accept repository fixtures and unittest discovery; expose sr_module/test_execution.py checks without native execution unless opted in."""

import os
import shutil
import unittest
from unittest.mock import patch
import uuid
from agentic_srim.backends.sr_module.input import OUTPUT_NAME
from agentic_srim.backends.sr_module.output import parse
from agentic_srim.capabilities.stopping_range.workflow import execute, submit
from agentic_srim.execution.jobs import cancel, read_verified, recover
from agentic_srim.config import Settings
from agentic_srim.execution.workers import dispatch
from agentic_srim.backends.sr_module.runner import SUPPORT_FILES
from agentic_srim.storage.artifacts import checksum, write_json
from agentic_srim.storage.database import connect, now, transition
from sr_module.support import IsolatedTest, NATIVE


class ExecutionTests(IsolatedTest):
    """Construct unittest cases; verify sr_module contracts and report failures."""

    def test_success_preserves_lineage_and_reuses_workspace(self) -> None:
        """Accept no inputs; verify two runs, copied-once files, hashes, and tampering."""
        source_before = {p: checksum(self.settings.sr_module_directory / p) for p in SUPPORT_FILES + ("SR.IN",)}
        trim = self.runtime / "workers" / "worker-1" / "trim"
        trim.mkdir(parents=True)
        (trim / OUTPUT_NAME).write_bytes(b"unrelated TRIM file")
        with patch("agentic_srim.capabilities.stopping_range.workflow.run", side_effect=self.simulate):
            first = execute(self.runtime, submit(self.request, self.settings))
            workspace = self.runtime / "workers" / "worker-1" / "sr_module"
            times = {p: (workspace / p).stat().st_mtime_ns for p in SUPPORT_FILES}
            second = execute(self.runtime, submit(self.request, self.settings))
        self.assertEqual(first["state"], "CHECKED")
        self.assertEqual(second["state"], "CHECKED")
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(first["execution_hash"], second["execution_hash"])
        self.assertEqual(times, {p: (workspace / p).stat().st_mtime_ns for p in SUPPORT_FILES})
        self.assertEqual((trim / OUTPUT_NAME).read_bytes(), b"unrelated TRIM file")
        self.assertFalse((workspace.parent / "SR.IN").exists())
        self.assertFalse((workspace.parent / OUTPUT_NAME).exists())
        self.assertEqual(source_before, {p: checksum(self.settings.sr_module_directory / p) for p in source_before})
        self.assertEqual([e["state"] for e in first["events"]], ["QUEUED", "RUNNING", "COLLECTED", "PARSED", "CHECKED"])
        self.assertEqual(read_verified(self.runtime, first["id"])["result"], first["result"])
        archive = self.runtime / first["attempts"][0]["artifact_path"]
        (archive / OUTPUT_NAME).write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "checksum"):
            read_verified(self.runtime, first["id"])

    def test_stale_output_cannot_satisfy_missing_output(self) -> None:
        """Accept no inputs; verify leftover output is removed and no result accepted."""
        with patch("agentic_srim.capabilities.stopping_range.workflow.run", side_effect=self.simulate):
            execute(self.runtime, submit(self.request, self.settings))
        with patch("agentic_srim.capabilities.stopping_range.workflow.run", return_value=0):
            failed = execute(self.runtime, submit(self.request, self.settings))
        self.assertEqual(failed["state"], "QUARANTINED")
        self.assertNotIn("result", failed)
        self.assertIn("expected table", failed["error"])
        read_verified(self.runtime, failed["id"])

    def test_retry_is_an_attempt_not_a_new_run(self) -> None:
        """Accept no inputs; verify timeout retry is bounded and retains both attempts."""
        settings = Settings(self.settings.sr_module_directory, self.runtime, 1, 2)
        count = 0

        def flaky(*args) -> int:
            """Accept runner arguments; fail once then return fixture simulation status."""
            nonlocal count
            count += 1
            if count == 1:
                raise TimeoutError("test timeout")
            return self.simulate(*args)

        with patch("agentic_srim.capabilities.stopping_range.workflow.run", side_effect=flaky):
            result = execute(self.runtime, submit(self.request, settings))
        self.assertEqual(result["state"], "CHECKED")
        self.assertEqual([a["state"] for a in result["attempts"]], ["FAILED", "CHECKED"])
        self.assertTrue(all(a["run_id"] == result["id"] for a in result["attempts"]))
        read_verified(self.runtime, result["id"])

    def test_cancellation_before_native_start(self) -> None:
        """Accept no inputs; verify queued cancellation prevents native launch."""
        run_id = submit(self.request, self.settings)
        cancel(self.runtime, run_id)
        with patch("agentic_srim.execution.processes.subprocess.Popen") as popen:
            result = execute(self.runtime, run_id)
        popen.assert_not_called()
        self.assertEqual(result["state"], "CANCELLED")
        read_verified(self.runtime, run_id)

    def test_worker_data_tampering_is_rejected(self) -> None:
        """Accept no inputs; verify changed copied reference data cannot be executed."""
        with patch("agentic_srim.capabilities.stopping_range.workflow.run", side_effect=self.simulate):
            execute(self.runtime, submit(self.request, self.settings))
        (self.runtime / "workers" / "worker-1" / "sr_module" / "VERSION").write_text("changed")
        with patch("agentic_srim.capabilities.stopping_range.workflow.run") as runner:
            result = execute(self.runtime, submit(self.request, self.settings))
        runner.assert_not_called()
        self.assertEqual(result["state"], "FAILED")
        self.assertIn("checksum", result["error"])

    def test_abandoned_attempt_recovery(self) -> None:
        """Accept no inputs; recover an interrupted attempt and prevent stale adoption."""
        run_id = submit(self.request, self.settings)
        attempt_id = uuid.uuid4().hex
        archive = self.runtime / "runs" / run_id / "attempts" / attempt_id
        archive.mkdir(parents=True)
        write_json(archive / "request.json", self.data)
        with connect(self.runtime) as connection:
            connection.execute("INSERT INTO attempts(id,run_id,ordinal,state,started_at,artifact_path) VALUES(?,?,?,?,?,?)",
                               (attempt_id, run_id, 1, "RUNNING", now(), str(archive.relative_to(self.runtime))))
            transition(connection, run_id, "RUNNING", attempt_id)
        with patch("agentic_srim.capabilities.stopping_range.workflow.run") as runner:
            with self.assertRaisesRegex(RuntimeError, "requires recovery"):
                execute(self.runtime, submit(self.request, self.settings))
        runner.assert_not_called()
        worker = self.runtime / "workers" / "worker-1"
        workspace = worker / "sr_module"
        workspace.mkdir()
        shutil.copyfile(NATIVE, workspace / OUTPUT_NAME)
        (worker / OUTPUT_NAME).write_text("unrelated root output")
        write_json(worker / "active_attempt.json", {"attempt_id": attempt_id})
        write_json(archive / "native_process.json", {"pid": 123})
        with patch("agentic_srim.execution.jobs.is_process_alive", return_value=False):
            self.assertEqual(recover(self.runtime), [run_id])
        self.assertEqual((archive / OUTPUT_NAME).read_bytes(), NATIVE.read_bytes())
        self.assertEqual(read_verified(self.runtime, run_id)["state"], "FAILED")
        self.assertEqual(recover(self.runtime), [])

    @unittest.skipUnless(os.environ.get("AGENTIC_SRIM_INTEGRATION") == "1", "Native execution explicitly opt-in")
    def test_native_worker_integration(self) -> None:
        """Accept no inputs; execute installed SR Module twice and compare fixture rows."""
        original = {name: checksum(self.settings.sr_module_directory / name) for name in SUPPORT_FILES + ("SR.IN",)}
        expected = parse(NATIVE.read_text(encoding="cp1252"), self.request).to_dict()["rows"]
        first = dispatch(self.runtime, submit(self.request, self.settings))
        second = dispatch(self.runtime, submit(self.request, self.settings))
        self.assertEqual(first["state"], "CHECKED", first.get("error"))
        self.assertEqual(second["state"], "CHECKED", second.get("error"))
        self.assertEqual(first["result"]["rows"], expected)
        self.assertEqual(second["result"]["rows"], expected)
        self.assertEqual(original, {name: checksum(self.settings.sr_module_directory / name) for name in original})
