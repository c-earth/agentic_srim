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
        for forbidden in ("run_submit", "analysis_run"):
            self.assertNotIn(forbidden, tools)

    def test_unified_retrieval_dispatch_limits_and_cancel(self: TestProjectAdapter) -> None:
        """Expose exact unified reads and enforce response caps/cancel-before-publication."""
        from copy import deepcopy
        from src.knowledge.retrieval_schema import EMPTY_QUERY
        self.events = []
        spec = {"title": "Helium", "question": "Range?", "status": "open", "tags": [], "notes": "", "members": []}
        created = write_operation("case_create", {"request_id": str(uuid4()), "spec": spec, "allocated_id": None}, self.project, self.cancel, self.progress)
        query = {**deepcopy(EMPTY_QUERY), "text": "helium"}
        result = read_operation("knowledge_search", {"query": query, "offset": 0, "limit": 1, "expected_generation": None}, self.project)
        self.assertEqual(result["total"], 1)
        read = read_operation("knowledge_get", {"kind": "case", "reference": created["data"]["reference"], "dependencies": True}, self.project)
        self.assertEqual(read["verification"]["record"], "verified")
        links = read_operation("knowledge_links", {"kind": "case", "reference": created["data"]["reference"]}, self.project)
        self.assertEqual(links["total"], 0)
        with patch.dict(self.project.limits, {"max_result_bytes": 1}), self.assertRaisesRegex(ValueError, "too-large"):
            read_operation("knowledge_search", {"query": query}, self.project)
        cancelled = write_operation("knowledge_update", {"request_id": str(uuid4()), "operation": "initialize"}, self.project, lambda: True, self.progress)
        self.assertEqual(cancelled["outcome"], "cancelled")
        preview = write_operation("knowledge_update", {"request_id": str(uuid4()), "operation": "reconcile", "scope": "registered"}, self.project, self.cancel, self.progress)
        self.assertIsNone(preview["data"]["report"])
        decisions = iter((False, True))
        interrupted = write_operation("knowledge_update", {"request_id": str(uuid4()), "operation": "update", "references": [{"kind": "case", "reference": created["data"]["reference"]}]}, self.project, lambda: next(decisions), self.progress)
        self.assertEqual(interrupted["outcome"], "cancelled")
        self.assertEqual(read_operation("knowledge_search", {"query": query}, self.project)["total"], 1)

    def test_database_tools_reload_and_limits(self: TestProjectAdapter) -> None:
        """Reload external registrations, constrain reads and avoid native/index mutations."""
        import json
        original = self.root.joinpath("settings.toml").read_text(encoding="utf-8")
        source = self.root / "external.txt"
        source.write_bytes(b"neutral evidence")
        self.assertEqual(read_operation("database_sources_list", {}, self.project)["sources"], [])
        self.root.joinpath("settings.toml").write_text(original + "\n[knowledge]\ndatabase_paths = " + json.dumps([str(source)]) + "\n", encoding="utf-8")
        with patch("src.srim.sr.execution.run_sr_simulation", side_effect=AssertionError("No physics")), patch("src.srim.trim.execution.run_trim_simulation", side_effect=AssertionError("No physics")):
            identity = read_operation("database_sources_list", {}, self.project)["sources"][0]["source_id"]
            report = read_operation("database_inspect", {"source_id": identity}, self.project)
            self.assertEqual(report["format_detection"]["state"], "insufficient_evidence")
            reference = report["samples"][0]["reference"]
            self.assertEqual(read_operation("database_sample_read", {"reference": reference}, self.project)["verification"], "sample_only")
        with patch.dict(self.project.limits, {"file_chunk_bytes": 1}), self.assertRaisesRegex(ValueError, "too-large"):
            read_operation("database_sample_read", {"reference": reference}, self.project)
        with patch.dict(self.project.limits, {"file_chunk_bytes": 1}), self.assertRaisesRegex(ValueError, "too-large"):
            read_operation("database_inspect", {"source_id": identity}, self.project)
        with patch.dict(self.project.limits, {"max_result_bytes": 1}), self.assertRaisesRegex(ValueError, "too-large"):
            read_operation("database_sources_list", {}, self.project)
        self.root.joinpath("settings.toml").write_text(original, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unconfigured"):
            read_operation("database_sample_read", {"reference": reference}, self.project)
        self.assertFalse(self.root.joinpath("memory/knowledge").exists())

    def test_external_preview_import_dispatch(self: TestProjectAdapter) -> None:
        """Forward exact plans/callbacks, isolate reads and bound results without physics."""
        from copy import deepcopy
        from src.knowledge.external_schema import EMPTY_EXTERNAL_QUERY
        self.events = []
        source_id = "db_" + "a" * 64
        with patch("src.mcp.project_mcp.operations.preview_external_import", return_value={"state": "ready", "plan_sha256": "b" * 64}) as preview:
            result = write_operation("database_preview", {"source_id": source_id, "request_id": str(uuid4())}, self.project, self.cancel, self.progress)
            self.assertEqual(result["outcome"], "succeeded")
            self.assertEqual(result["data"]["plan_sha256"], "b" * 64)
            self.assertEqual(preview.call_args.kwargs["source_id"], source_id)
            self.assertEqual(preview.call_args.kwargs["cancel"], self.cancel)
            self.assertEqual(preview.call_args.kwargs["progress"], self.progress)
        with patch("src.mcp.project_mcp.operations.import_external_preview", return_value={"state": "committed"}) as publish:
            result = write_operation("database_import", {"source_id": source_id, "expected_plan_sha256": "b" * 64, "request_id": str(uuid4())}, self.project, self.cancel, self.progress)
            self.assertEqual(result["outcome"], "succeeded")
            self.assertEqual(publish.call_args.kwargs["expected_plan_sha256"], "b" * 64)
        with patch("src.mcp.project_mcp.operations.preview_external_import") as preview:
            result = write_operation("database_preview", {"source_id": source_id, "request_id": str(uuid4())}, self.project, lambda: True, self.progress)
            self.assertEqual(result["outcome"], "cancelled")
            preview.assert_not_called()
        with patch("src.mcp.project_mcp.operations.preview_external_import", side_effect=InterruptedError):
            self.assertEqual(write_operation("database_preview", {"source_id": source_id, "request_id": str(uuid4())}, self.project, self.cancel, self.progress)["outcome"], "cancelled")
        for state in ("failed", "cancelled"):
            with patch("src.mcp.project_mcp.operations.import_external_preview", return_value={"state": state}):
                result = write_operation("database_import", {"source_id": source_id, "expected_plan_sha256": "b" * 64, "request_id": str(uuid4())}, self.project, self.cancel, self.progress)
                self.assertEqual(result["outcome"], state)
        query = deepcopy(EMPTY_EXTERNAL_QUERY)
        with patch("src.mcp.project_mcp.operations.search_external_database", return_value={"state": "not_loaded", "total": 0}) as search:
            result = read_operation("database_search", {"source_id": source_id, "query": query, "offset": 0, "limit": 1}, self.project)
            self.assertEqual(result["total"], 0)
            self.assertEqual(search.call_args.kwargs["query"], query)
            with patch.dict(self.project.limits, {"max_result_bytes": 1}), self.assertRaisesRegex(ValueError, "too-large"):
                read_operation("database_search", {"source_id": source_id, "query": query}, self.project)
        with patch("src.mcp.project_mcp.operations.get_external_record", return_value={"verification": "not_checked"}) as read:
            self.assertEqual(read_operation("database_get", {"reference": {}, "verify_evidence": False}, self.project)["verification"], "not_checked")
            self.assertFalse(read.call_args.kwargs["verify_evidence"])
        with patch("src.mcp.project_mcp.operations.read_external_status", return_value={"state": "not_loaded"}) as status:
            self.assertEqual(read_operation("database_status", {"source_id": source_id}, self.project)["state"], "not_loaded")
            self.assertEqual(status.call_args.kwargs["source_id"], source_id)
        self.assertFalse(self.root.joinpath("memory/knowledge/external").exists())
        self.assertFalse(self.root.joinpath("no-native-installation").exists())
