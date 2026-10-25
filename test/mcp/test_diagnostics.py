"""Verify diagnostic evidence boundaries and independent SR/TRIM failure findings."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from threading import Event
from uuid import uuid4
from unittest.mock import patch

from src.mcp.common.contracts import RUN_DIAGNOSTICS_SCHEMA, canonical_bytes, validate_document
from src.mcp.common.diagnostics import read_run_diagnostics
from src.mcp.srim_mcp.operations import read_operation as scientific_read, write_operation as scientific_write, reconcile_runs, reconcile_job_runs
from src.mcp.srim_mcp.catalog import build_catalog
from src.backend.records import CreateRequest, _lock_worker, create_directories
from src.srim.common.records import initialize_simulation, begin_execution_evidence, start_simulation, record_execution_stopped, read_simulation
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

    def test_closeout_and_reconcile(self: TestDiagnostics) -> None:
        """Read self's stranded stop proof, recover via the adapter and preserve terminal bytes; return None."""
        created, worker = create_directories(self.paths, self.config, [CreateRequest("srim"), CreateRequest("worker")]).completed
        engine = {"name": "sr", "version": "2013", "executable": {"name": "SRModule.exe", "sha256": "a" * 64}}
        request = {"schema_version": 1, "parameters": {"energy_keV": "125"}}
        resolved = {"schema_version": 1, "parameters": request["parameters"], "sources": [], "resources": [], "cab": None}
        initialize_simulation(self.paths, created, engine, request, resolved, engine_inputs={"SR.IN": b"input"})
        runtime = worker.path / "sr" / created.path.name
        runtime.mkdir()
        (runtime / "SR.IN").write_bytes(b"input")
        with _lock_worker(self.paths, worker.path):
            launch = begin_execution_evidence(self.paths, self.config, created.path.name, worker.path.name)
            start_simulation(self.paths, self.config, created.path.name)
            record_execution_stopped(self.paths, self.config, created.path.name, launch["execution_id"], reason="exit", exit_code=0)
        before = scientific_read("run_closeout", {"simulation_ids": [created.path.name]}, self.science)
        validate_document(before, build_catalog(self.science)["schemas"]["srim.run-closeout.v1"])
        self.assertFalse(before["complete"])
        self.assertEqual(before["counts"]["stale_stopped"], 1)
        events = []
        with patch("src.srim.sr.execution.subprocess.run", side_effect=AssertionError("Recovery must never launch native work")):
            result = scientific_write("run_reconcile", {"simulation_ids": [created.path.name], "request_id": str(uuid4())}, self.science, Event().is_set, events.append)
        self.assertEqual(result["outcome"], "succeeded")
        self.assertEqual(result["data"]["recovered_ids"], [created.path.name])
        self.assertTrue(result["data"]["closeout"]["complete"])
        self.assertEqual(read_simulation(self.paths, created.path.name)["status"], "failed")
        self.assertEqual((created.path / "output/raw/runtime/SR.IN").read_bytes(), b"input")
        original = (created.path / "metadata.json").read_bytes()
        job = {"id": "job_" + str(uuid4()), "state": "interrupted", "simulation_ids": [created.path.name]}
        again = reconcile_job_runs(self.science, [job])
        self.assertFalse(again["recovered_ids"])
        self.assertTrue(again["closeout"]["complete"])
        self.assertEqual((created.path / "metadata.json").read_bytes(), original)
        empty = reconcile_job_runs(self.science, [])
        self.assertTrue(empty["closeout"]["complete"])

    def test_reconcile_blocks_unsafe_evidence(self: TestDiagnostics) -> None:
        """Leave self's missing, corrupt, foreign and input-damaged stop proofs unresolved; return None."""
        for damage in ("missing", "malformed", "foreign", "hash", "input", "path"):
            with self.subTest(damage=damage):
                created, worker = create_directories(self.paths, self.config, [CreateRequest("srim"), CreateRequest("worker")]).completed
                engine = {"name": "sr", "version": "2013", "executable": {"name": "SRModule.exe", "sha256": "a" * 64}}
                request = {"schema_version": 1, "parameters": {"energy_keV": "125"}}
                resolved = {"schema_version": 1, "parameters": request["parameters"], "sources": [], "resources": [], "cab": None}
                initialize_simulation(self.paths, created, engine, request, resolved, engine_inputs={"SR.IN": b"input"})
                runtime = worker.path / "sr" / created.path.name
                runtime.mkdir()
                (runtime / "SR.IN").write_bytes(b"input")
                with _lock_worker(self.paths, worker.path):
                    launch = begin_execution_evidence(self.paths, self.config, created.path.name, worker.path.name)
                    start_simulation(self.paths, self.config, created.path.name)
                    record_execution_stopped(self.paths, self.config, created.path.name, launch["execution_id"], reason="exit", exit_code=0)
                target = created.path / "lifecycle/stopped.json"
                if damage == "missing":
                    target.unlink()
                elif damage == "malformed":
                    target.write_bytes(b"not-json")
                elif damage in ("foreign", "hash"):
                    stopped = json.loads(target.read_bytes())
                    stopped["execution_id" if damage == "foreign" else "launch_sha256"] = str(uuid4()) if damage == "foreign" else "0" * 64
                    target.write_bytes(canonical_bytes(stopped))
                elif damage == "input":
                    (created.path / "input/engine/SR.IN").write_bytes(b"changed")
                else:
                    target = created.path / "lifecycle/launch.json"
                    launch["runtime_path"] = "../elsewhere"
                    target.write_bytes(canonical_bytes(launch))
                with patch("src.mcp.srim_mcp.operations.finish_simulation") as finish:
                    result = reconcile_runs(self.science, [created.path.name])
                finish.assert_not_called()
                self.assertEqual(result["unresolved_ids"], [created.path.name])
                self.assertFalse(result["closeout"]["complete"])
                self.assertEqual(read_simulation(self.paths, created.path.name)["status"], "running")

    def test_reconcile_contention_and_metadata_failure(self: TestDiagnostics) -> None:
        """Keep self's busy/failed writes unresolved and finalize despite missing runtime copies; return None."""
        for failure in ("busy", "metadata", "metadata-unexpected", "missing-runtime", "copy-error", "copy-unexpected"):
            with self.subTest(failure=failure):
                created, worker = create_directories(self.paths, self.config, [CreateRequest("srim"), CreateRequest("worker")]).completed
                engine = {"name": "sr", "version": "2013", "executable": {"name": "SRModule.exe", "sha256": "a" * 64}}
                request = {"schema_version": 1, "parameters": {"energy_keV": "125"}}
                resolved = {"schema_version": 1, "parameters": request["parameters"], "sources": [], "resources": [], "cab": None}
                initialize_simulation(self.paths, created, engine, request, resolved, engine_inputs={"SR.IN": b"input"})
                runtime = worker.path / "sr" / created.path.name
                runtime.mkdir()
                (runtime / "SR.IN").write_bytes(b"input")
                with _lock_worker(self.paths, worker.path):
                    launch = begin_execution_evidence(self.paths, self.config, created.path.name, worker.path.name)
                    start_simulation(self.paths, self.config, created.path.name)
                    record_execution_stopped(self.paths, self.config, created.path.name, launch["execution_id"], reason="exit", exit_code=0)
                if failure == "busy":
                    with _lock_worker(self.paths, worker.path):
                        result = reconcile_runs(self.science, [created.path.name])
                elif failure in ("metadata", "metadata-unexpected"):
                    with patch("src.mcp.srim_mcp.operations.finish_simulation", side_effect=RuntimeError("unexpected publication error") if failure == "metadata-unexpected" else OSError("metadata unavailable")):
                        result = reconcile_runs(self.science, [created.path.name])
                elif failure == "missing-runtime":
                    self.assertTrue(runtime.is_relative_to(self.root))
                    (runtime / "SR.IN").unlink()
                    runtime.rmdir()
                    result = reconcile_runs(self.science, [created.path.name])
                else:
                    with patch("src.mcp.srim_mcp.operations.preserve_sr_runtime", side_effect=RuntimeError("unexpected copy error") if failure == "copy-unexpected" else OSError("copy unavailable")):
                        result = reconcile_runs(self.science, [created.path.name])
                self.assertTrue(result["issues"])
                if failure in ("busy", "metadata", "metadata-unexpected"):
                    self.assertEqual(result["unresolved_ids"], [created.path.name])
                    self.assertEqual(read_simulation(self.paths, created.path.name)["status"], "running")
                else:
                    self.assertTrue(result["closeout"]["complete"])
                    self.assertEqual(result["recovered_ids"], [created.path.name])
                    self.assertEqual(read_simulation(self.paths, created.path.name)["status"], "failed")
