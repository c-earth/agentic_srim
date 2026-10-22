"""Verify explicit multiple-case management independent of agent-session state."""

from __future__ import annotations

from uuid import uuid4

from src.case.records import create_case, list_cases, read_case, revise_case
from src.analysis.records import create_analysis
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).absolute().parents[1] / "mcp"))
from test_contracts import Fixture


class TestCases(Fixture):
    """Create multiple cases and update each explicitly without a global current case."""

    def test_multiple_cases_in_one_session(self: TestCases) -> None:
        """Create two cases, interleave revisions, and preserve independent histories."""
        spec = {"title": "A", "question": "Question A?", "status": "open", "tags": [], "notes": "", "members": []}
        first = create_case(self.paths, self.config, spec, str(uuid4()))
        second = create_case(self.paths, self.config, {**spec, "title": "B", "question": "Question B?"}, str(uuid4()))
        self.assertNotEqual(first["record"]["id"], second["record"]["id"])
        revise_case(self.paths, self.config, first["record"]["id"], 1, {**spec, "notes": "Only A"}, str(uuid4()))
        self.assertEqual(read_case(self.paths, second["record"]["id"])["record"]["notes"], "")
        self.assertEqual(list_cases(self.paths)["total"], 2)

    def test_analysis_can_belong_to_multiple_cases(self: TestCases) -> None:
        """Attach one immutable analysis revision to separate independent case records."""
        analysis = create_analysis(self.paths, self.config, {"title": "Shared evidence", "question": "What is known?", "method": {"kind": "ad-hoc", "reference": None, "description": "Inspect explicit records", "citations": [], "code_artifacts": []}, "inputs": [], "eligibility": {"allow_failed_runs": False, "allow_checkpoint_recovery": False, "require_complete_parser": True}, "parameters": {}, "findings": "", "limitations": ""}, str(uuid4()))
        spec = {"title": "A", "question": "Question?", "status": "open", "tags": [], "notes": "", "members": [{"member_id": str(uuid4()), "role": "shared-analysis", "note": "Explicit shared reference", "reference": analysis["reference"]}]}
        first = create_case(self.paths, self.config, spec, str(uuid4()))
        second = create_case(self.paths, self.config, {**spec, "title": "B"}, str(uuid4()))
        self.assertNotEqual(first["reference"]["id"], second["reference"]["id"])
        self.assertEqual(first["record"]["members"][0]["reference"], second["record"]["members"][0]["reference"])
