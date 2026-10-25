"""Verify diagnostic evidence boundaries and independent SR/TRIM failure findings."""

from __future__ import annotations

from dataclasses import replace
import hashlib
from unittest.mock import patch

from src.mcp.common.contracts import RUN_DIAGNOSTICS_SCHEMA, canonical_bytes, validate_document
from src.mcp.common.diagnostics import read_run_diagnostics
from src.mcp.srim_mcp.operations import read_operation as scientific_read
from src.mcp.project_mcp.operations import read_operation as project_read
from test_contracts import Fixture


class TestDiagnostics(Fixture):
    """Exercise sealed synthetic evidence with no native execution or input replay."""

    def prepare(self: TestDiagnostics, engine: str, documents: dict[str, object], *, status: str = "failed") -> dict:
        """Write documents and metadata for engine/status; return synthetic metadata.

        Mutate only the isolated fixture root. Fixture documents use exact hashes.
        """
        self.identity = "srim_20000101_000000_1"
        self.record = self.paths.simulation_memory / self.identity
        self.record.mkdir(parents=True, exist_ok=True)
        outputs = []
        for name, document in documents.items():
            payload = canonical_bytes(document)
            path = self.record / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            outputs.append({"path": name, "sha256": hashlib.sha256(payload).hexdigest(), "size_bytes": len(payload)})
        self.metadata = {"id": self.identity, "engine": {"name": engine}, "status": status, "failure": {"message": "An arbitrary metadata failure", "exit_code": None} if status == "failed" else None, "outputs": outputs, "inputs": []}
        (self.record / "metadata.json").write_bytes(canonical_bytes(self.metadata))
        self.reader = patch("src.mcp.common.diagnostics.read_simulation", return_value=self.metadata)
        self.reader.start()
        self.addCleanup(self.reader.stop)
        return self.metadata

    def trim_report(self: TestDiagnostics) -> dict:
        """Return a fresh producer-shaped TRIM execution report without side effects."""
        return {"schema_version": 1, "stage": "finished", "failure": {"code": "monitor", "message": "Unfamiliar diagnostic wording"}, "process": {"termination": "exit", "exit_code": 0, "monitor_errors": ["Observation handle unavailable"], "popups": []}, "completion": {"complete": False, "checks": [{"field": "parsing", "status": "different", "detail": "Incomplete profile"}]}}

    def test_trim_multiple_findings_and_clean_exit(self: TestDiagnostics) -> None:
        """Retain monitoring, parser and native evidence despite one primary code."""
        self.prepare("trim", {"output/raw/execution.json": self.trim_report(), "output/parsed/results.json": {"schema_version": 1, "complete": False, "issues": [{"code": "inconsistent", "message": "Different grid"}]}})
        result = read_run_diagnostics(self.science, self.identity)
        validate_document(result, RUN_DIAGNOSTICS_SCHEMA)
        self.assertTrue({"monitoring", "parser", "native"} <= {item["category"] for item in result["observations"]})
        self.assertTrue(result["unresolved"])
        self.assertFalse(any(item["category"] == "input-validation" for item in result["observations"]))
        self.assertTrue(any("exit code: 0" in item["message"] for item in result["observations"]))

    def test_sr_structured_stages_not_message_matching(self: TestDiagnostics) -> None:
        """Classify varied SR stages without interpreting identical misleading text."""
        self.prepare("sr", {})
        for stage, category in (("parse", "parser"), ("preflight", "input-validation"), ("exit", "native"), ("timeout", "timeout"), ("preserve", "preservation"), ("future-stage", "unknown")):
            report = {"schema_version": 1, "stage": stage, "message": "native parser monitoring input failure", "exit_code": 7, "timed_out": stage == "timeout"}
            payload = canonical_bytes(report)
            path = self.record / "output/raw/execution.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            self.metadata["outputs"] = [{"path": "output/raw/execution.json", "sha256": hashlib.sha256(payload).hexdigest(), "size_bytes": len(payload)}]
            (self.record / "metadata.json").write_bytes(canonical_bytes(self.metadata))
            result = read_run_diagnostics(self.science, self.identity)
            self.assertEqual(result["observations"][0]["category"], category)
            self.assertEqual(result["observations"][0]["cause_status"], "unresolved" if category == "unknown" else "recorded")

    def test_unknown_codes_and_formats(self: TestDiagnostics) -> None:
        """Retain unknown codes and expose unsupported versions as unresolved."""
        report = self.trim_report()
        report["failure"] = {"code": "future-code", "message": "Input mistake"}
        self.prepare("trim", {"output/raw/execution.json": report, "output/parsed/results.json": {"schema_version": 99, "complete": False}})
        result = read_run_diagnostics(self.science, self.identity)
        self.assertEqual(result["observations"][0]["category"], "unknown")
        self.assertEqual(result["observations"][0]["cause_status"], "unresolved")
        self.assertTrue(any(item["code"] == "unsupported" for item in result["issues"]))

    def test_missing_and_changed_evidence(self: TestDiagnostics) -> None:
        """Preserve metadata while rejecting altered sealed diagnostics."""
        self.prepare("trim", {"output/raw/execution.json": self.trim_report()})
        (self.record / "output/raw/execution.json").write_bytes(b'{}')
        result = read_run_diagnostics(self.science, self.identity)
        self.assertEqual(result["issues"][0]["code"], "integrity")
        self.assertEqual(result["observations"], [])
        self.assertEqual(result["recorded_failure"], self.metadata["failure"])
        self.assertEqual(len(result["evidence"]), 1)
        (self.record / "output/raw/execution.json").unlink()
        self.assertTrue(read_run_diagnostics(self.science, self.identity)["issues"])

    def test_limits_and_truncation(self: TestDiagnostics) -> None:
        """Bound messages/findings and expose omitted counts and oversized inputs."""
        report = self.trim_report()
        report["process"]["monitor_errors"] = ["x" * 800 for _ in range(80)]
        self.prepare("trim", {"output/raw/execution.json": report})
        result = read_run_diagnostics(self.science, self.identity)
        self.assertEqual(len(result["observations"]), 64)
        self.assertGreater(result["omitted_observations"], 0)
        self.assertTrue(all(len(item["message"]) <= 512 for item in result["observations"]))
        context = replace(self.science, limits={**self.science.limits, "max_result_bytes": 1})
        self.assertEqual(read_run_diagnostics(context, self.identity)["issues"][0]["code"], "too-large")

    def test_nonterminal_and_success(self: TestDiagnostics) -> None:
        """Avoid unsealed running evidence and report successful sealed execution."""
        self.prepare("sr", {}, status="running")
        result = read_run_diagnostics(self.science, self.identity)
        self.assertEqual(result["investigation_status"], "not-terminal")
        self.assertEqual(result["issues"], [])
        self.metadata["status"] = "succeeded"
        report = {"schema_version": 1, "stage": "completed", "message": None, "exit_code": 0, "timed_out": False}
        payload = canonical_bytes(report)
        path = self.record / "output/raw/execution.json"
        path.parent.mkdir(parents=True)
        path.write_bytes(payload)
        self.metadata["outputs"] = [{"path": "output/raw/execution.json", "sha256": hashlib.sha256(payload).hexdigest(), "size_bytes": len(payload)}]
        (self.record / "metadata.json").write_bytes(canonical_bytes(self.metadata))
        self.assertEqual(read_run_diagnostics(self.science, self.identity)["investigation_status"], "no-recorded-failure")

    def test_readers_do_not_require_input_replay(self: TestDiagnostics) -> None:
        """Expose identical reports through both readers and the diagnostic section."""
        self.prepare("trim", {"output/raw/execution.json": self.trim_report()})
        with patch("src.mcp.srim_mcp.operations.read_simulation", return_value=self.metadata), patch("src.mcp.srim_mcp.operations.project_simulation_inputs", side_effect=ValueError("Invalid input replay")), patch("src.mcp.project_mcp.operations.read_simulation", return_value=self.metadata), patch("src.mcp.project_mcp.operations.project_simulation_inputs", side_effect=ValueError("Invalid input replay")):
            science = scientific_read("simulation_get", {"simulation_id": self.identity}, self.science)
            project = project_read("run_get", {"simulation_id": self.identity}, self.project)
            section = scientific_read("result_read", {"simulation_id": self.identity, "section": "diagnostics"}, self.science)
            self.assertEqual(science["diagnostics"], section)
            self.assertEqual(project["diagnostics"], section)
            self.assertIsNone(project["source"])

    def test_metadata_change_is_rejected(self: TestDiagnostics) -> None:
        """Reject a changed metadata pointer rather than publishing mixed evidence."""
        self.prepare("trim", {"output/raw/execution.json": self.trim_report()})
        original = (self.record / "metadata.json").read_bytes()
        with patch("src.mcp.common.diagnostics._read_regular_bytes", side_effect=[original, original + b' ']):
            with self.assertRaisesRegex(ValueError, "stale"):
                read_run_diagnostics(self.science, self.identity)

    def test_no_diagnostics_does_not_infer_cause(self: TestDiagnostics) -> None:
        """Keep failed status and unresolved cause without fabricating a category."""
        self.prepare("sr", {})
        result = read_run_diagnostics(self.science, self.identity)
        self.assertEqual(result["investigation_status"], "incomplete")
        self.assertEqual(result["observations"], [])
        self.assertTrue(result["unresolved"])
    def test_trim_failure_families_and_older_versions(self: TestDiagnostics) -> None:
        """Classify structured TRIM codes across supported historical versions."""
        self.prepare("trim", {})
        for version, code, category in ((1, "parse", "parser"), (2, "preflight", "input-validation"), (3, "integrity", "input-integrity"), (4, "preservation", "preservation"), (4, "timeout", "timeout"), (4, "cancelled", "cancellation"), (4, "substitution", "saved-value-mismatch"), (4, "output-requirement", "output-requirement")):
            report = self.trim_report()
            report["schema_version"] = version
            report["failure"] = {"code": code, "message": "Identical unrelated message"}
            payload = canonical_bytes(report)
            path = self.record / "output/raw/execution.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            self.metadata["outputs"] = [{"path": "output/raw/execution.json", "sha256": hashlib.sha256(payload).hexdigest(), "size_bytes": len(payload)}]
            (self.record / "metadata.json").write_bytes(canonical_bytes(self.metadata))
            result = read_run_diagnostics(self.science, self.identity)
            self.assertEqual(result["observations"][0]["category"], category)

    def test_malformed_reports_and_identity_mismatch(self: TestDiagnostics) -> None:
        """Retain other findings when a sealed report has wrong types or identity."""
        report = self.trim_report()
        report["process"]["monitor_errors"] = "wrong type"
        self.prepare("trim", {"output/raw/execution.json": report, "output/parsed/results.json": {"schema_version": 1, "complete": False, "issues": []}, "output/parsed/saved-values.json": {"schema_version": 1, "engine": "sr", "differences": []}})
        result = read_run_diagnostics(self.science, self.identity)
        self.assertEqual({item["code"] for item in result["issues"]}, {"malformed", "integrity"})
        self.assertTrue(any(item["category"] == "parser" for item in result["observations"]))

    def test_independent_comparison_and_recovery_evidence(self: TestDiagnostics) -> None:
        """Report saved values/config/history findings and nested native diagnostics."""
        self.prepare("trim", {"output/raw/execution.json": self.trim_report(), "output/parsed/saved-values.json": {"schema_version": 1, "differences": [{"field": "count", "status": "different"}]}, "output/parsed/config-integrity.json": {"schema_version": 1, "status": "different", "reason": "Unknown configuration layout"}, "output/parsed/availability.json": {"schema_version": 1, "histories": {"status": "unverified"}}, "output/parsed/results.json": {"schema_version": 2, "complete": True, "issues": [], "native": {"issues": [{"code": "missing", "message": "Native text absent"}]}}})
        result = read_run_diagnostics(self.science, self.identity)
        self.assertTrue({"saved-value-mismatch", "input-integrity", "output-requirement", "parser"} <= {item["category"] for item in result["observations"]})
        self.assertTrue(any(item["evidence"][0]["json_pointer"] == "/native/issues/0" for item in result["observations"]))

    def test_missing_result_points_to_investigation(self: TestDiagnostics) -> None:
        """Direct absent parsed-result callers to the independent diagnostic reader."""
        self.prepare("sr", {})
        with patch("src.mcp.srim_mcp.operations.project_simulation_inputs", return_value={"files": []}), patch("src.mcp.srim_mcp.operations.read_simulation", return_value=self.metadata):
            with self.assertRaisesRegex(ValueError, "section diagnostics"):
                scientific_read("result_read", {"simulation_id": self.identity, "section": "summary"}, self.science)
