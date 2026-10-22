"""Check explicit analysis provenance, immutable artifacts and terminal records."""

from __future__ import annotations

from uuid import uuid4
import hashlib
import json
from unittest.mock import patch

from src.analysis.records import attach_analysis_artifacts, check_analysis_inputs, create_analysis, finish_analysis, read_analysis, revise_analysis
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).absolute().parents[1] / "mcp"))
from test_contracts import Fixture


class TestAnalyses(Fixture):
    """Record ad-hoc evidence without an analysis execution engine or physics claim."""

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
