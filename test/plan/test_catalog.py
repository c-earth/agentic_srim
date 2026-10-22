"""Verify empty reusable collections and unapproved candidate preservation."""

from __future__ import annotations

import hashlib
from uuid import uuid4

from src.backend.project_records import write_record_json
from src.plan.catalog import create_candidate, list_collections, publish_capability, read_candidate, read_capability
from src.plan.improvement import create_improvement_proposal, record_observation, validate_improvement_event
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).absolute().parents[1] / "mcp"))
from test_contracts import Fixture


class TestReuse(Fixture):
    """Ensure candidate submission cannot activate a workflow or fabricate approval."""

    def test_empty_collections_and_candidate(self: TestReuse) -> None:
        """Require exact local adoption evidence for publication and subsequent reads."""
        self.assertTrue(all(item["total"] == 0 for item in list_collections(self.paths)["collections"]))
        schema = {"type": "object", "additionalProperties": False, "properties": {}, "required": []}
        manifest = {"schema_version": 1, "collection": "workflow", "id": "inspect-results", "title": "Inspect", "description": "Explicit result inspection", "input_schema": schema, "output_schema": schema, "prerequisites": [], "procedure": [{"step_id": "inspect", "instruction": "Inspect saved provenance", "tool_names": ["result_read"]}], "evidence": [], "limitations": ["No physical validation"], "files": []}
        candidate = create_candidate(self.paths, self.config, {"collection": "workflow", "proposed_manifest": manifest, "rationale": "Reusable inspection", "evidence_references": []}, [], str(uuid4()), allowed_roots=[self.root])
        self.assertEqual(read_candidate(self.paths, candidate["candidate"]["id"])["candidate"], candidate["candidate"])
        self.assertTrue(all(item["total"] == 0 for item in list_collections(self.paths)["collections"]))
        with self.assertRaises(ValueError):
            publish_capability(self.paths, self.config, candidate["candidate"]["id"], {"approved": True}, expected_candidate_sha256=candidate["reference"]["sha256"])
        observation = record_observation(self.paths, self.config, {"problem": "Synthetic test", "evidence": "Fixture-only adoption evidence", "sources": [], "criteria": ["quality"], "measurements": [], "uncertainty": "No real adoption is authorized"}, str(uuid4()))
        proposal_id = str(uuid4())
        markdown = "# " + proposal_id + "\nIdentity and evidence\nExact change " + candidate["candidate"]["id"] + " " + candidate["reference"]["sha256"] + "\nTradeoffs\nBounded trial\nRestoration\nDecisions\n"
        proposal = create_improvement_proposal(self.paths, self.config, proposal_id, [observation["event"]["event_id"]], markdown, str(uuid4()))
        approval_id = str(uuid4())
        approval_event = {"schema_version": 1, "event_id": approval_id, "recorded_at": observation["event"]["recorded_at"], "kind": "approval", "proposal_id": proposal_id, "task_ref": "Synthetic local-storage test", "details": {"gate": "adoption", "decision": "approved", "approval_ref": "Synthetic fixture only", "revision": proposal["proposal"]["sha256"]}}
        validate_improvement_event(approval_event)
        approval_path = self.root / ".local" / "workflow-improvement" / "events" / (approval_id + ".json")
        approval_evidence = write_record_json(approval_path, approval_event)
        approval = {"proposal_id": proposal_id, "proposal_revision_sha256": proposal["proposal"]["sha256"], "approval_event_id": approval_id, "approval_event_sha256": approval_evidence["sha256"]}
        published = publish_capability(self.paths, self.config, candidate["candidate"]["id"], approval, expected_candidate_sha256=candidate["reference"]["sha256"])
        self.assertEqual(read_capability(self.paths, published["reference"]), published)
        approval_bytes = approval_path.read_bytes()
        self.assertEqual(hashlib.sha256(approval_bytes).hexdigest(), approval["approval_event_sha256"])
        old_approval_path = self.root / ".cache" / "workflow-improvement" / "events" / approval_path.name
        old_approval_path.parent.mkdir(parents=True)
        old_approval_path.write_bytes(approval_bytes)
        approval_path.unlink()
        try:
            with self.assertRaises(OSError):
                read_capability(self.paths, published["reference"])
            with self.assertRaises(ValueError):
                publish_capability(self.paths, self.config, candidate["candidate"]["id"], approval, expected_candidate_sha256=candidate["reference"]["sha256"])
        finally:
            approval_path.write_bytes(approval_bytes)
        approval_path.write_bytes(approval_bytes + b" ")
        try:
            with self.assertRaises(ValueError):
                read_capability(self.paths, published["reference"])
            with self.assertRaises(ValueError):
                publish_capability(self.paths, self.config, candidate["candidate"]["id"], approval, expected_candidate_sha256=candidate["reference"]["sha256"])
        finally:
            approval_path.write_bytes(approval_bytes)
        self.assertEqual(read_capability(self.paths, published["reference"]), published)
