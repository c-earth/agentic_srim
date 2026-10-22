"""Verify empty reusable collections and unapproved candidate preservation."""

from __future__ import annotations

from uuid import uuid4

from src.plan.catalog import create_candidate, list_collections, publish_capability, read_candidate
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).absolute().parents[1] / "mcp"))
from test_contracts import Fixture


class TestReuse(Fixture):
    """Ensure candidate submission cannot activate a workflow or fabricate approval."""

    def test_empty_collections_and_candidate(self: TestReuse) -> None:
        """Store a declarative candidate while active collections stay empty."""
        self.assertTrue(all(item["total"] == 0 for item in list_collections(self.paths)["collections"]))
        schema = {"type": "object", "additionalProperties": False, "properties": {}, "required": []}
        manifest = {"schema_version": 1, "collection": "workflow", "id": "inspect-results", "title": "Inspect", "description": "Explicit result inspection", "input_schema": schema, "output_schema": schema, "prerequisites": [], "procedure": [{"step_id": "inspect", "instruction": "Inspect saved provenance", "tool_names": ["result_read"]}], "evidence": [], "limitations": ["No physical validation"], "files": []}
        candidate = create_candidate(self.paths, self.config, {"collection": "workflow", "proposed_manifest": manifest, "rationale": "Reusable inspection", "evidence_references": []}, [], str(uuid4()), allowed_roots=[self.root])
        self.assertEqual(read_candidate(self.paths, candidate["candidate"]["id"])["candidate"], candidate["candidate"])
        self.assertTrue(all(item["total"] == 0 for item in list_collections(self.paths)["collections"]))
        with self.assertRaises(ValueError):
            publish_capability(self.paths, self.config, candidate["candidate"]["id"], {"approved": True}, expected_candidate_sha256=candidate["reference"]["sha256"])
