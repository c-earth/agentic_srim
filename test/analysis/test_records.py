"""Check explicit analysis provenance, immutable artifacts and terminal records."""

from __future__ import annotations

from uuid import uuid4
import hashlib
import json
from unittest.mock import patch

from src.backend.paths import ProjectPaths
from src.srim.common.types import JSONObject
from src.analysis.records import attach_analysis_artifacts, check_analysis_inputs, create_analysis, finish_analysis, read_analysis, revise_analysis
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).absolute().parents[1] / "mcp"))
from test_contracts import Fixture


class TestAnalyses(Fixture):
    """Check temporary declared artifact roles and provenance without executing methods."""

    def test_declared_artifact_roles(self: TestAnalyses) -> None:
        """Require declared code/method roles for ad-hoc and registered completions."""
        def registered_method(paths: ProjectPaths, reference: JSONObject) -> JSONObject:
            """Return permissive input/output schemas for test paths/reference without I/O."""
            return {"manifest": {"input_schema": {}, "output_schema": {}}}

        source = self.root / "evidence.txt"
        source.write_bytes(b"Retained method evidence")
        with patch("src.plan.catalog.read_capability", side_effect=registered_method):
            for kind in ("ad-hoc", "registered"):
                for role in ("code", "method", "data", "figure", "report", "diagnostic", "missing"):
                    with self.subTest(kind=kind, role=role):
                        declared = "missing.txt" if role == "missing" else "evidence.txt"
                        spec = {"title": "Roles", "question": "Declared method evidence?", "method": {"kind": kind, "reference": {} if kind == "registered" else None, "description": "Inspect retained evidence", "citations": [], "code_artifacts": [declared]}, "inputs": [], "eligibility": {"allow_failed_runs": False, "allow_checkpoint_recovery": False, "require_complete_parser": True}, "parameters": {}, "findings": "", "limitations": ""}
                        identity = create_analysis(self.paths, self.config, spec, str(uuid4()))["record"]["id"]
                        attached = attach_analysis_artifacts(self.paths, self.config, identity, 1, [{"name": "evidence.txt", "role": "method" if role == "missing" else role, "source_path": str(source), "media_type": "text/plain"}], str(uuid4()), allowed_roots=[self.root])
                        artifact = attached["record"]["artifacts"][0]
                        stored = self.paths.postprocessing_memory / identity / artifact["path"]
                        before = stored.read_bytes()
                        if role in ("code", "method"):
                            final = finish_analysis(self.paths, self.config, identity, 2, "completed", "Inspected", "Software test", None, str(uuid4()))
                            self.assertEqual(final["record"]["status"], "completed")
                        else:
                            with self.assertRaisesRegex(ValueError, declared.replace(".", r"\.")) as raised:
                                finish_analysis(self.paths, self.config, identity, 2, "completed", "Inspected", "Software test", None, str(uuid4()))
                            if role != "missing":
                                self.assertIn(role, str(raised.exception))
                                self.assertIn("code or method", str(raised.exception))
                            self.assertEqual(read_analysis(self.paths, identity)["reference"], attached["reference"])
                            self.assertFalse((self.paths.postprocessing_memory / identity / "revisions" / "r000003.json").exists())
                        self.assertEqual(stored.read_bytes(), before)

    def test_failed_and_empty_reference_completion(self: TestAnalyses) -> None:
        """Retain failed-completion and empty-declaration evidence rules."""
        source = self.root / "diagnostic.txt"
        source.write_bytes(b"Diagnostic evidence")
        for status, references in (("failed", ["diagnostic.txt"]), ("completed", [])):
            with self.subTest(status=status):
                spec = {"title": "Boundaries", "question": "Existing policy?", "method": {"kind": "ad-hoc", "reference": None, "description": "Record diagnostics", "citations": [], "code_artifacts": references}, "inputs": [], "eligibility": {"allow_failed_runs": False, "allow_checkpoint_recovery": False, "require_complete_parser": True}, "parameters": {}, "findings": "", "limitations": ""}
                identity = create_analysis(self.paths, self.config, spec, str(uuid4()))["record"]["id"]
                if status == "completed":
                    with self.assertRaisesRegex(ValueError, "findings and method/artifact evidence"):
                        finish_analysis(self.paths, self.config, identity, 1, status, "Inspected", "Test", None, str(uuid4()))
                attach_analysis_artifacts(self.paths, self.config, identity, 1, [{"name": "diagnostic.txt", "role": "diagnostic", "source_path": str(source), "media_type": "text/plain"}], str(uuid4()), allowed_roots=[self.root])
                failure = {"code": "test", "message": "Test failure"} if status == "failed" else None
                if status == "completed":
                    with self.assertRaises(ValueError):
                        finish_analysis(self.paths, self.config, identity, 2, status, " ", "Test", None, str(uuid4()))
                final = finish_analysis(self.paths, self.config, identity, 2, status, "Inspected", "Test", failure, str(uuid4()))
                self.assertEqual(final["record"]["status"], status)

    def test_artifact_snapshot_and_terminal_state(self: TestAnalyses) -> None:
        """Attach a method file, finish provenance, reject later edits and catch tampering."""
        spec = {"title": "Inspection", "question": "What happened?", "method": {"kind": "ad-hoc", "reference": None, "description": "Inspect the retained evidence", "citations": [], "code_artifacts": ["method.txt"]}, "inputs": [], "eligibility": {"allow_failed_runs": False, "allow_checkpoint_recovery": False, "require_complete_parser": True}, "parameters": {}, "findings": "", "limitations": ""}
        first = create_analysis(self.paths, self.config, spec, str(uuid4()))
        identity = first["record"]["id"]
        source = self.root / "method.txt"
        source.write_text("Explicit reproducible inspection method", encoding="utf-8")
        attached = attach_analysis_artifacts(self.paths, self.config, identity, 1, [{"name": "method.txt", "role": "method", "source_path": str(source), "media_type": "text/plain"}], str(uuid4()), allowed_roots=[self.root])
        source.write_text("Later caller edit", encoding="utf-8")
        final = finish_analysis(self.paths, self.config, identity, 2, "completed", "Evidence was inspected", "No physical validation", None, str(uuid4()))
        self.assertEqual(final["record"]["revision"], 3)
        with self.assertRaises(ValueError):
            revise_analysis(self.paths, self.config, identity, 3, spec, str(uuid4()))
        artifact = attached["record"]["artifacts"][0]
        (self.paths.postprocessing_memory / identity / artifact["path"]).write_bytes(b"tampered")
        self.assertTrue(read_analysis(self.paths, identity)["issues"])

    def test_failed_and_recovered_input_eligibility(self: TestAnalyses) -> None:
        """Require explicit failed/recovered eligibility and retain recovery warnings."""
        source = {"kind": "simulation", "id": "srim_20000101_000000_1", "metadata_sha256": "0" * 64, "input_inventory_sha256": "1" * 64}
        payload = json.dumps({"result_source": "checkpoint-recovery", "complete": True, "recovery": {"warnings": [{"code": "inferred_geometry", "message": "Layout qualification"}]}}).encode("utf-8")
        file = {"owner": source, "path": "output/parsed/results.json", "sha256": hashlib.sha256(payload).hexdigest(), "size_bytes": len(payload), "role": "output"}
        selected = [{"source": source, "files": [file], "purpose": "Inspect recovery provenance"}]
        eligibility = {"allow_failed_runs": False, "allow_checkpoint_recovery": False, "require_complete_parser": True}
        with patch("src.analysis.records.read_source_record", return_value={"execution_status": "failed", "inputs": {"engine": "trim"}}), patch("src.analysis.records.read_source_file", return_value=payload):
            with self.assertRaises(ValueError):
                check_analysis_inputs(self.paths, selected, eligibility)
            with self.assertRaises(ValueError):
                check_analysis_inputs(self.paths, selected, {**eligibility, "allow_failed_runs": True})
            report = check_analysis_inputs(self.paths, selected, {**eligibility, "allow_failed_runs": True, "allow_checkpoint_recovery": True})
            self.assertEqual(report[0]["files"][0]["warnings"][0]["code"], "inferred_geometry")
