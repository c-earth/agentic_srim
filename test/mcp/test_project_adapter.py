"""Check project adapter isolation, multiple cases and omitted approval/execution tools."""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

from src.mcp.project_mcp.catalog import build_catalog
from src.mcp.project_mcp.operations import read_operation, write_operation
from src.srim.common.types import JSONObject
from test_contracts import Fixture


class TestProjectAdapter(Fixture):
    """Keep management mutations independent of native launch and session-global case state."""

    def cancel(self: TestProjectAdapter) -> bool:
        """Return False for self's explicit project mutation without side effects."""
        return False

    def progress(self: TestProjectAdapter, event: JSONObject) -> None:
        """Retain event in self's test list; return None without native execution."""
        self.events.append(event)

    def test_multiple_cases_and_server_boundaries(self: TestProjectAdapter) -> None:
        """Create multiple independent cases and prove project tools never launch physics."""
        self.events = []
        spec = {"title": "A", "question": "Question?", "status": "open", "tags": [], "notes": "", "members": []}
        with patch("src.srim.sr.execution.run_sr_simulation", side_effect=AssertionError("No native execution")):
            first = write_operation("case_create", {"request_id": str(uuid4()), "spec": spec, "allocated_id": None}, self.project, self.cancel, self.progress)
            second = write_operation("case_create", {"request_id": str(uuid4()), "spec": {**spec, "title": "B"}, "allocated_id": None}, self.project, self.cancel, self.progress)
            self.assertNotEqual(first["data"]["record"]["id"], second["data"]["record"]["id"])
            self.assertEqual(read_operation("cases_list", {"status": None, "offset": 0, "limit": 100}, self.project)["total"], 2)
            self.assertEqual(read_operation("runs_list", {"engine": None, "status": None, "offset": 0, "limit": 100}, self.project)["total"], 0)
        tools = build_catalog(self.project)["tools"]
        for forbidden in ("run_submit", "analysis_run", "publish_capability", "approve", "trial_start", "skill_edit"):
            self.assertNotIn(forbidden, tools)
