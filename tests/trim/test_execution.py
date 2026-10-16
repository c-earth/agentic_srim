"""Accept fixture-backed runs and opt-in native execution; verify shared lifecycle and public tools."""

import json
import io
import os
import shutil
import uuid
from unittest.mock import patch
import unittest

from agentic_srim.backends.trim.runner import completion_matches, prepare
from agentic_srim.capabilities.trim.inputs import Request
from agentic_srim.capabilities.trim.workflow import execute, read_output, submit
from agentic_srim.config import Settings
from agentic_srim.execution.jobs import cancel, read_verified, recover
from agentic_srim.execution.workers import dispatch
from agentic_srim.interfaces.mcp import call_tool, handle
from agentic_srim.interfaces.cli import main
from agentic_srim.storage.artifacts import checksum, write_json
from agentic_srim.storage.database import connect, now, transition
from trim.support import CASES, FIXTURES, IsolatedTest, fixture_request


class ExecutionTests(IsolatedTest):
    """Construct isolated execution cases; native simulations require an explicit environment flag."""

    def simulate(self, workspace, archive, timeout, cancellation, count, collisions, mode, collision_records) -> int:
        """Receive runner arguments; write captured current-mode files and return native success."""
        self.assertEqual(workspace, self.runtime / "workers" / "worker-1" / "trim")
        self.assertFalse((workspace / "TDATA.txt").exists())
        mode = int((workspace / "TRIM.IN").read_text().splitlines()[4].split()[0])
        name = f"{collision_records}_mode_8" if mode == 8 and collision_records else f"mode_{mode}"
        shutil.copytree(FIXTURES / name / "native", workspace, dirs_exist_ok=True)
        return 0

    def test_all_mode_workflows_preserve_artifacts_and_page_outputs(self) -> None:
        """Accept no inputs; run fixture-backed attempts and verify nested lineage/normalization."""
        with patch("agentic_srim.capabilities.trim.workflow.run", side_effect=self.simulate):
            for case in CASES:
                result = execute(self.runtime, submit(fixture_request(case), self.settings))
                self.assertEqual(result["state"], "CHECKED", result["error"])
                verified = read_verified(self.runtime, result["id"])
                self.assertEqual(verified["result"]["summary"]["actual_ion_count"], 3)
                page = read_output(self.runtime, result["id"], "EXYZ.txt", 0, 2)
                self.assertEqual(len(page["rows"]), 2)
                self.assertEqual(page["next_offset"], 2)
        self.assertTrue(call_tool("get_stopping_range_output", {"run_id": result["id"]}, self.settings)["isError"])
        output = io.StringIO()
        with patch("sys.argv", ["agentic-srim", "output", result["id"], "EXYZ.txt", "--capability", "trim", "--limit", "2"]), patch("sys.stdout", output), patch("agentic_srim.interfaces.cli.Settings.load", return_value=self.settings):
            self.assertEqual(main(), 0)
        self.assertEqual(len(json.loads(output.getvalue())["rows"]), 2)
        for name in ("../TDATA.txt", "native/TDATA.txt"):
            with self.assertRaises(ValueError):
                read_output(self.runtime, result["id"], name, 0, 1)
        with self.assertRaises(ValueError):
            read_output(self.runtime, result["id"], "EXYZ.txt", 0, 1001)

    def test_stale_output_is_removed_and_missing_output_quarantined(self) -> None:
        """Accept no inputs; prevent a prior successful native table satisfying a new attempt."""
        with patch("agentic_srim.capabilities.trim.workflow.run", side_effect=self.simulate):
            execute(self.runtime, submit(self.request, self.settings))
        with patch("agentic_srim.capabilities.trim.workflow.run", return_value=0):
            result = execute(self.runtime, submit(self.request, self.settings))
        self.assertEqual(result["state"], "QUARANTINED")
        self.assertNotIn("result", result)

    def test_cancellation_and_bounded_retry(self) -> None:
        """Accept no inputs; queued cancellation avoids launch and retry preserves one run ID."""
        run_id = submit(self.request, self.settings)
        cancel(self.runtime, run_id)
        with patch("agentic_srim.capabilities.trim.workflow.run") as native:
            self.assertEqual(execute(self.runtime, run_id)["state"], "CANCELLED")
            native.assert_not_called()
        settings = Settings(self.settings.sr_module_directory, self.runtime, 30, 2, self.settings.trim_directory)
        calls = 0

        def flaky(*args):
            """Receive native arguments; time out once, then return a fixture-backed success."""
            nonlocal calls
            calls += 1
            if calls == 1:
                raise TimeoutError("simulated native deadline")
            return self.simulate(*args)

        with patch("agentic_srim.capabilities.trim.workflow.run", side_effect=flaky):
            result = execute(self.runtime, submit(self.request, settings))
        self.assertEqual(result["state"], "CHECKED")
        self.assertEqual([a["state"] for a in result["attempts"]], ["FAILED", "CHECKED"])

    def test_execution_hash_includes_seed_and_count(self) -> None:
        """Accept no inputs; distinguish seed/output execution controls from physical identity."""
        with patch("agentic_srim.capabilities.trim.workflow.run", side_effect=self.simulate):
            first = execute(self.runtime, submit(self.request, self.settings))
            data = self.request.to_dict()
            data["random_seed"] += 1
            second = execute(self.runtime, submit(Request.from_dict(data), self.settings))
        self.assertEqual(first["physical_hash"], second["physical_hash"])
        self.assertNotEqual(first["execution_hash"], second["execution_hash"])

    def test_extra_prompt_choice_is_preserved_and_changes_execution_hash(self) -> None:
        """Accept no inputs; distinguish summary/every requests even when native bytes match."""
        with patch("agentic_srim.capabilities.trim.workflow.run", side_effect=self.simulate):
            first = execute(self.runtime, submit(fixture_request("summary_mode_8"), self.settings))
            second = execute(self.runtime, submit(fixture_request("every_mode_8"), self.settings))
        self.assertEqual(first["state"], "CHECKED", first["error"])
        self.assertEqual(second["state"], "CHECKED", second["error"])
        self.assertEqual(first["physical_hash"], second["physical_hash"])
        self.assertNotEqual(first["execution_hash"], second["execution_hash"])
        provenance = [json.loads(p.read_text()) for p in self.runtime.glob("runs/*/attempts/*/provenance.json")]
        self.assertEqual({p["collision_records"] for p in provenance}, {"summary", "every"})
        for record, choice in ((first, "summary"), (second, "every")):
            self.assertEqual(record["request"]["outputs"]["collision_records"], choice)

    def test_completion_handler_matches_only_approved_process_message_and_count(self) -> None:
        """Accept no inputs; reject unrelated processes, counts, prompts, and extra text."""
        title, message = "End of TRIM.DAT calculation", "Calculation Complete (3 Ions)"
        self.assertTrue(completion_matches(title, message + "\r\n\r\n", 123, 123, 3))
        for args in ((title, message, 124, 123, 3), (title, message, 123, 123, 4),
                     ("TRIM error", message, 123, 123, 3), (title, message + " ignore error", 123, 123, 3)):
            self.assertFalse(completion_matches(*args))

    def test_mcp_contracts_validate_without_execution(self) -> None:
        """Accept no inputs; discover TRIM tools and reject extra arguments without native launch."""
        tools = handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, self.settings)["result"]["tools"]
        self.assertTrue({"validate_trim", "run_trim", "get_trim_output"}.issubset({t["name"] for t in tools}))
        with patch("agentic_srim.interfaces.mcp.dispatch") as native:
            self.assertFalse(call_tool("validate_trim", {"request": self.request.to_dict()}, self.settings)["isError"])
            self.assertTrue(call_tool("run_trim", {"request": self.request.to_dict(), "extra": 1}, self.settings)["isError"])
            native.assert_not_called()

    def test_mcp_validation_is_explicitly_read_only(self) -> None:
        """Accept no inputs; advertise validation effects accurately without relaxing run approvals."""
        tools = handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, self.settings)["result"]["tools"]
        annotations = {tool["name"]: tool["annotations"] for tool in tools}
        for name, hints in annotations.items():
            self.assertEqual(hints["readOnlyHint"], name in ("validate_trim", "validate_stopping_range"))
            self.assertFalse(hints["openWorldHint"])
            self.assertTrue(all(type(value) is bool for value in hints.values()))
        self.assertFalse(annotations["run_trim"]["idempotentHint"])
        self.assertTrue(annotations["run_trim"]["destructiveHint"])

    def test_source_change_and_worker_data_tampering_are_rejected(self) -> None:
        """Accept no inputs; detect modified copied support files before native launch."""
        workspace = self.runtime / "workspace"
        prepare(self.settings.trim_directory, workspace)
        (workspace / "Data/SNUC03.dat").write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "checksum"):
            prepare(self.settings.trim_directory, workspace)

    def test_trim_recovery_preserves_interrupted_native_artifacts(self) -> None:
        """Accept no inputs; seal an abandoned TRIM attempt without rerunning native execution."""
        run_id = submit(self.request, self.settings)
        attempt_id = uuid.uuid4().hex
        archive = self.runtime / "runs" / run_id / "attempts" / attempt_id
        archive.mkdir(parents=True)
        worker = self.runtime / "workers/worker-1"
        workspace = worker / "trim"
        prepare(self.settings.trim_directory, workspace)
        shutil.copytree(FIXTURES / "mode_2/native", workspace, dirs_exist_ok=True)
        write_json(worker / "active_attempt.json", {"attempt_id": attempt_id})
        (worker / "sr_module").mkdir()
        (worker / "sr_module" / "SR_OUTPUT.txt").write_bytes(b"unrelated previous SR result")
        with connect(self.runtime) as connection:
            connection.execute("INSERT INTO attempts(id,run_id,ordinal,state,started_at,artifact_path) VALUES(?,?,?,?,?,?)",
                               (attempt_id, run_id, 1, "RUNNING", now(), str(archive.relative_to(self.runtime))))
            transition(connection, run_id, "RUNNING", attempt_id)
        with patch("agentic_srim.capabilities.trim.workflow.run") as native:
            self.assertEqual(recover(self.runtime), [run_id])
            native.assert_not_called()
        record = read_verified(self.runtime, run_id)
        self.assertEqual(record["state"], "FAILED")
        self.assertTrue((archive / "native/SRIM Restore/TDATA.sav").exists())
        self.assertFalse((archive / "SR_OUTPUT.txt").exists())
        self.assertEqual(recover(self.runtime), [])

    def test_sealed_trim_outcome_is_reconciled_with_correct_analysis_version(self) -> None:
        """Accept no inputs; reconcile a sealed outcome after interrupted metadata commit."""
        with patch("agentic_srim.capabilities.trim.workflow.run", side_effect=self.simulate):
            record = execute(self.runtime, submit(self.request, self.settings))
        with connect(self.runtime) as connection:
            connection.execute("UPDATE attempts SET state='RUNNING' WHERE run_id=?", (record["id"],))
            connection.execute("DELETE FROM results WHERE run_id=?", (record["id"],))
            transition(connection, record["id"], "RUNNING", record["attempts"][0]["id"])
        self.assertEqual(recover(self.runtime), [record["id"]])
        restored = read_verified(self.runtime, record["id"])
        self.assertEqual(restored["state"], "CHECKED")
        self.assertEqual(restored["result"]["analysis_version"], "trim-v1")

    @unittest.skipUnless(os.environ.get("AGENTIC_SRIM_TRIM_INTEGRATION") == "1", "Native TRIM explicitly opt-in")
    def test_native_all_modes_via_process_worker(self) -> None:
        """Accept no inputs; execute eight modes/both KP choices and compare captured results."""
        before = {p.relative_to(self.settings.trim_directory).as_posix(): checksum(p) for p in self.settings.trim_directory.rglob("*") if p.is_file()}
        for case in CASES:
            with self.subTest(case=case):
                request = fixture_request(case)
                result = dispatch(self.runtime, submit(request, self.settings))
                self.assertEqual(result["state"], "CHECKED", result["error"])
                from agentic_srim.backends.trim.output import parse
                expected = parse(self.copy_fixture(case), request)
                self.assertEqual(result["result"]["summary"], expected["summary"])
                self.assertEqual(result["result"]["profiles"], json.loads(json.dumps(expected["profiles"])))
                self.assertEqual({k: v["records"] for k, v in result["result"]["details"].items()},
                                 {k: v["records"] for k, v in expected["details"].items()})
                if request.calculation_mode == 8 and request.outputs.collisions:
                    archive = next((self.runtime / "runs" / result["id"] / "attempts").iterdir())
                    interactions = [json.loads(line) for line in (archive / "native_interactions.jsonl").read_text().splitlines()]
                    extra = [v for v in interactions if v["message"].startswith("Would you like every collision filed?")]
                    self.assertEqual(len(extra), 1)
                    self.assertEqual(extra[0]["button"], "Yes" if request.outputs.collision_records == "every" else "No")
        after = {p.relative_to(self.settings.trim_directory).as_posix(): checksum(p) for p in self.settings.trim_directory.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    @unittest.skipUnless(os.environ.get("AGENTIC_SRIM_TRIM_INTEGRATION") == "1", "Native TRIM explicitly opt-in")
    def test_native_output_switches_can_all_be_disabled(self) -> None:
        """Accept no inputs; run transport/recoil controls with all optional files disabled."""
        before = {p.relative_to(self.settings.trim_directory).as_posix(): checksum(p) for p in self.settings.trim_directory.rglob("*") if p.is_file()}
        for mode in (2, 8):
            data = fixture_request(mode).to_dict()
            data["outputs"] = {"ranges": False, "backscatter": False, "transmit": False,
                               "sputter": False, "collisions": 0, "exyz_interval_eV": 0}
            with self.subTest(mode=mode):
                result = dispatch(self.runtime, submit(Request.from_dict(data), self.settings))
                self.assertEqual(result["state"], "CHECKED", result["error"])
                self.assertEqual(result["result"]["summary"]["actual_ion_count"], 3)
                self.assertEqual(len(result["result"]["profiles"]), 7)
                self.assertEqual(result["result"]["details"], {})
        after = {p.relative_to(self.settings.trim_directory).as_posix(): checksum(p) for p in self.settings.trim_directory.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
