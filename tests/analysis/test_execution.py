"""Accept unittest discovery; verify supervised jobs, budgets, recovery and owned descendants."""

from pathlib import Path
import os
import subprocess
import sys
import time
from unittest.mock import patch
import uuid

from analysis.builtins import bundle
from analysis.records import AnalysisError, encode, write_json
from analysis.runner import Dispatcher
from analysis_support import AnalysisTest


class ExecutionTests(AnalysisTest):
    """Construct isolated runtimes; exercise real Python children without native simulator execution."""

    def custom(self, code: str, tests: str | None = None) -> dict:
        """Accept trusted method source/optional tests; register an isolated immutable method; return reference."""
        spec, files = bundle("dataset_summary")
        files = {"method.py": code, "tests/test_method.py": tests or '"""Accept unittest; verify fixture availability."""\nimport unittest\nclass Tests(unittest.TestCase):\n    """Construct unittest cases; expose a basic passing test."""\n    def test_ok(self):\n        """Accept no inputs; assert fixture truth; return nothing."""\n        self.assertTrue(True)\n'}
        return self.store.perform("register_method", self.audit(schema_version=1, method_id="custom", version=1, specification=spec, files=files))["method"]

    def test_timeout_cancellation_retry_budget_and_responsive_reads(self) -> None:
        """Accept no inputs; supervise sleeping descendants, cancel/retry with unchanged budgets; return nothing."""
        code = '"""Accept execution inputs; sleep with an owned descendant."""\nimport subprocess,sys,time\nfrom pathlib import Path\ndef run(inputs, parameters, context):\n    """Accept pinned inputs/parameters/context; spawn a child, sleep and return no result."""\n    p=subprocess.Popen([sys.executable,"-B","-c","import time; time.sleep(120)"])\n    Path(context["output_directory"]).joinpath("descendant.txt").write_text(str(p.pid))\n    print("x"*100000,flush=True)\n    time.sleep(120)\n'
        method = self.custom(code)
        self.verify(method)
        request = self.summary_request(method, self.source())
        request["limits"] = {"wall_seconds": 2, "total_seconds": 6, "max_attempts": 2, "log_bytes": 256}
        submitted = self.store.perform("submit_analysis", request)
        timed = self.wait(submitted["analysis_id"])
        self.assertEqual(timed["state"], "TIMED_OUT", timed["error"])
        attempt_dir = self.runtime / "analyses" / timed["analysis_id"] / "attempts" / timed["current_attempt"]
        from agentic_srim.execution.processes import is_process_alive
        descendant = attempt_dir / "output" / "descendant.txt"
        self.assertFalse(is_process_alive(int(descendant.read_text())))
        self.assertIn("truncated", (attempt_dir / "stdout.log").read_text())
        self.assertLessEqual(sum((attempt_dir / name).stat().st_size for name in ("stdout.log", "stderr.log")), 256)
        retry = self.store.perform("retry_analysis", self.audit(analysis_id=timed["analysis_id"]))
        self.assertEqual(retry["limits"], timed["limits"])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status = self.store.perform("get_analysis", {"analysis_id": timed["analysis_id"]})
            if status["state"] == "RUNNING":
                break
            time.sleep(0.02)
        started = time.monotonic()
        self.assertTrue(self.store.perform("list_analyses", {})["analyses"])
        self.assertLess(time.monotonic() - started, 1)
        self.store.perform("cancel_analysis", self.audit(analysis_id=timed["analysis_id"]))
        cancelled = self.wait(timed["analysis_id"])
        self.assertEqual(cancelled["state"], "CANCELLED")
        self.assertGreater(cancelled["consumed_seconds"], timed["consumed_seconds"])
        with self.assertRaisesRegex(AnalysisError, "budget"):
            self.store.perform("retry_analysis", self.audit(analysis_id=timed["analysis_id"]))

    def test_recovery_of_abandoned_records_never_recomputes_and_ambiguous_owner_blocks(self) -> None:
        """Accept no inputs; reconcile interrupted metadata, preserve ownership ambiguity and exact replay; return nothing."""
        method = self.methods()["dataset_summary"]
        job = self.store.perform("verify_method", self.audit(schema_version=1, method=method, limits={}))
        attempt_id = uuid.uuid4().hex
        attempt = {"id": attempt_id, "ordinal": 1, "state": "RUNNING", "started_at": "fixture", "owner": {"pid": os.getpid(), "group": os.getpid()},
                   "output_sha256": None, "error": None, "elapsed_seconds": 0}
        with self.store.connection(True) as connection:
            record = self.store.job(connection, job["analysis_id"])
            record.update(state="RUNNING", current_attempt=attempt_id, attempt_count=1)
            self.store.save_job(connection, record)
            connection.execute("INSERT INTO attempts VALUES(?,?,?,?)", (attempt_id, record["id"], 1, encode(attempt)))
        unresolved = self.store.perform("recover_analyses", self.audit())
        self.assertEqual(unresolved["recovered"][0]["state"], "RECOVERY_REQUIRED")
        attempt["owner"] = None
        with self.store.connection(True) as connection:
            connection.execute("UPDATE attempts SET record=? WHERE id=?", (encode(attempt), attempt_id))
        request = self.audit()
        recovered = self.store.perform("recover_analyses", request)
        self.assertEqual(recovered["recovered"][0]["state"], "FAILED")
        self.assertEqual(self.store.perform("recover_analyses", request), recovered)
        status = self.store.perform("get_analysis", {"analysis_id": job["analysis_id"]})
        self.assertEqual(status["remaining_attempts"], 0)

    def test_failed_verification_dependency_and_output_schema_are_not_success(self) -> None:
        """Accept no inputs; retain failed-test reports and reject wrong method outputs; return nothing."""
        tests = '"""Accept unittest; intentionally fail."""\nimport unittest\nclass Tests(unittest.TestCase):\n    """Construct a failing contract fixture."""\n    def test_fail(self):\n        """Accept no inputs; fail intentionally; return nothing."""\n        self.fail("independent failure")\n'
        method = self.custom('"""Accept import; expose no callable."""\n', tests)
        self.start()
        submitted = self.store.perform("verify_method", self.audit(schema_version=1, method=method, limits={}))
        failed = self.wait(submitted["analysis_id"])
        self.assertEqual(failed["state"], "FAILED")
        self.assertEqual(failed["attempts"][-1]["verification_summary"]["failures"], 1)
        with self.assertRaises(AnalysisError):
            self.store.perform("submit_analysis", self.summary_request(method, self.source()))
        spec, files = bundle("dataset_summary")
        files = {"method.py": '"""Accept import; expose deliberately invalid output."""\ndef run(inputs, parameters, context):\n    """Accept inputs/parameters/context; return an invalid empty result."""\n    return {}\n',
                 "tests/test_method.py": tests.replace('self.fail("independent failure")', 'self.assertTrue(True)')}
        bad_method = self.store.perform("register_method", self.audit(schema_version=1, method_id="bad_output", version=1, specification=spec, files=files))["method"]
        self.verify(bad_method)
        bad_job = self.store.perform("submit_analysis", self.summary_request(bad_method, self.source()))
        self.assertEqual(self.wait(bad_job["analysis_id"])["state"], "FAILED")
        spec["requirements"] = {"missing-analysis-regression-dependency": "1.0.0"}
        missing = self.store.perform("register_method", self.audit(schema_version=1, method_id="missing_dependency", version=1, specification=spec, files=files))["method"]
        missing_job = self.store.perform("verify_method", self.audit(schema_version=1, method=missing, limits={}))
        missing_result = self.wait(missing_job["analysis_id"])
        self.assertEqual(missing_result["state"], "FAILED")
        self.assertEqual(missing_result["error"]["code"], "environment_error")

    def test_abrupt_supervisor_exit_terminates_owned_tree_and_recovery_does_not_rerun(self) -> None:
        """Accept no inputs; kill a Python supervisor, confirm descendants exit, recover original attempt; return nothing."""
        code = '"""Accept execution; create a long-lived owned descendant."""\nimport subprocess,sys,time\nfrom pathlib import Path\ndef run(inputs, parameters, context):\n    """Accept inputs/parameters/context; spawn descendant and sleep; return no result."""\n    p=subprocess.Popen([sys.executable,"-B","-c","import time; time.sleep(120)"])\n    Path(context["output_directory"]).joinpath("descendant.txt").write_text(str(p.pid))\n    time.sleep(120)\n'
        method = self.custom(code)
        self.verify(method)
        self.worker.stop()
        self.worker = None
        request = self.summary_request(method, self.source())
        request["limits"] = {"wall_seconds": 10, "total_seconds": 10}
        job = self.store.perform("submit_analysis", request)
        config = self.runtime / "analysis.toml"
        config.write_text("runtime_directory='.'\n[stores]\nlocal='.'\n", encoding="utf-8")
        process = subprocess.Popen([sys.executable, "-B", "-m", "analysis", "--config", str(config), "mcp"],
                                   stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline, descendant = time.monotonic() + 8, None
            while time.monotonic() < deadline:
                matches = list((self.runtime / "analyses" / job["analysis_id"]).glob("attempts/*/output/descendant.txt"))
                if matches:
                    descendant = int(matches[0].read_text())
                    break
                time.sleep(0.03)
            status = self.store.perform("get_analysis", {"analysis_id": job["analysis_id"]})
            self.assertIsNotNone(descendant, f"Supervisor {process.poll()}; status {status}")
        finally:
            process.kill()
            process.wait(timeout=5)
            process.stdin.close()
        from agentic_srim.execution.processes import is_process_alive
        deadline = time.monotonic() + 5
        while is_process_alive(descendant) and time.monotonic() < deadline:
            time.sleep(0.03)
        self.assertFalse(is_process_alive(descendant))
        recovered = self.store.perform("recover_analyses", self.audit())
        self.assertEqual(recovered["recovered"][0]["state"], "FAILED")
        self.assertEqual(len(self.store.perform("get_analysis", {"analysis_id": job["analysis_id"]})["attempts"]), 1)
