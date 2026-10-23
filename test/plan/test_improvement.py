"""Verify existing self-improvement record support preserves approval boundaries."""

from __future__ import annotations

import json
from uuid import uuid4
from unittest.mock import patch

from src.backend.project_records import write_record_json
from src.srim.common.types import JSONObject

from src.plan.improvement import create_improvement_proposal, read_improvement_status, record_assessment, record_observation, validate_improvement_event
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).absolute().parents[1] / "mcp"))
from test_contracts import Fixture


class TestImprovement(Fixture):
    """Preserve immutable observations/proposals without introducing trial authority."""

    def test_observation_proposal_and_malformed_evidence(self: TestImprovement) -> None:
        """Verify local references, ignore old-root events and report invalid evidence."""
        details = {"problem": "Repeated inspection", "evidence": "Explicit measured task evidence", "sources": ["doc/example"], "criteria": ["time"], "measurements": [], "uncertainty": "No speedup measured"}
        observation = record_observation(self.paths, self.config, details, str(uuid4()))
        identity = str(uuid4())
        markdown = "# " + identity + "\nIdentity and evidence\nExact change\nTradeoffs\nBounded trial\nRestoration\nDecisions\n"
        result = create_improvement_proposal(self.paths, self.config, identity, [observation["event"]["event_id"]], markdown, str(uuid4()))
        for reference in (observation["reference"], result["proposal"], result["event"]):
            self.assertTrue(reference["path"].startswith(".local/workflow-improvement/"))
        self.assertFalse((self.root / ".cache" / "workflow-improvement").exists())
        self.assertEqual((self.root / result["proposal"]["path"]).read_bytes(), markdown.encode("utf-8"))
        self.assertFalse(read_improvement_status(self.paths)["issues"])
        old_identity = str(uuid4())
        old_event = {**observation["event"], "event_id": old_identity}
        old_path = self.root / ".cache" / "workflow-improvement" / "events" / (old_identity + ".json")
        old_path.parent.mkdir(parents=True)
        old_path.write_text(json.dumps(old_event), encoding="utf-8")
        self.assertNotIn(old_identity, read_improvement_status(self.paths)["event_hashes"])
        forged = {**observation["event"], "approved": True}
        with self.assertRaises(ValueError):
            validate_improvement_event(forged)
        (self.root / ".local" / "workflow-improvement" / "events" / "broken.json").write_bytes(b"{}")
        self.assertTrue(read_improvement_status(self.paths)["issues"])

    def assessment_details(self: TestImprovement, observations: list[str], *, disposition: str = "proposal_needed", predecessor: str | None = None, related: list[str] | None = None) -> JSONObject:
        """Return fresh assessment details for observations and optional links.

        disposition, predecessor and related select the proposed decision and
        chain; no files are read or written and no validation is performed.
        """
        return {"observation_ids": observations, "disposition": disposition, "rationale": "Supported reusable finding", "uncertainty": "No measured speedup", "follow_up": "Prepare exact proposal or reconsider when evidence arrives", "related_proposal_ids": [] if related is None else related, "predecessor_assessment_id": predecessor}

    def assessment_observation(self: TestImprovement) -> str:
        """Create one fixture observation and return its UUID.

        Uses self's isolated paths/config, never repository evidence or physics.
        Validation and filesystem errors propagate to the test runner.
        """
        details = {"problem": "Substantial avoidable rework", "evidence": "Explicit fixture evidence", "sources": [], "criteria": ["quality"], "measurements": [], "uncertainty": "Fixture, not a measured live task"}
        return record_observation(self.paths, self.config, details, str(uuid4()))["event"]["event_id"]

    def test_assessment_pending_and_proposal_resolution(self: TestImprovement) -> None:
        """Verify unassessed and owed proposals remain visible until linked."""
        observation = self.assessment_observation()
        self.assertEqual(read_improvement_status(self.paths)["pending_assessments"][0]["reason"], "unassessed")
        result = record_assessment(self.paths, self.config, self.assessment_details([observation]), str(uuid4()))
        self.assertEqual(result["event"]["schema_version"], 2)
        self.assertEqual(read_improvement_status(self.paths)["pending_assessments"][0]["reason"], "proposal_required")
        identity = str(uuid4())
        markdown = "# " + identity + "\nIdentity and evidence\nExact change\nTradeoffs\nBounded trial\nRestoration\nDecisions\n"
        create_improvement_proposal(self.paths, self.config, identity, [observation], markdown, str(uuid4()))
        status = read_improvement_status(self.paths)
        self.assertEqual(status["pending_assessments"], [])
        self.assertFalse(status["issues"])
        self.assertIsNone(status["proposals"][0]["state"])
        self.assertEqual(status["assessments"][0]["covering_proposal_ids"], [identity])

    def test_assessment_supersession_and_forks(self: TestImprovement) -> None:
        """Accept explicit supersession, reject writes and report injected forks."""
        observation = self.assessment_observation()
        first = record_assessment(self.paths, self.config, self.assessment_details([observation], disposition="deferred_insufficient_evidence"), str(uuid4()))["event"]
        self.assertEqual(read_improvement_status(self.paths)["pending_assessments"][0]["reason"], "deferred")
        second = record_assessment(self.paths, self.config, self.assessment_details([observation], disposition="no_reusable_change", predecessor=first["event_id"]), str(uuid4()))["event"]
        self.assertEqual(read_improvement_status(self.paths)["pending_assessments"], [])
        self.assertEqual(read_improvement_status(self.paths)["assessments"][0]["event_id"], second["event_id"])
        with self.assertRaises(ValueError):
            record_assessment(self.paths, self.config, self.assessment_details([observation]), str(uuid4()))
        fork = {**second, "event_id": str(uuid4())}
        write_record_json(self.paths.root / ".local/workflow-improvement/events" / (fork["event_id"] + ".json"), fork)
        status = read_improvement_status(self.paths)
        self.assertTrue(status["issues"])
        self.assertFalse(status["assessments"][0]["valid"])
        self.assertEqual(status["pending_assessments"][0]["reason"], "conflict")

    def test_assessment_closed_schema_and_references(self: TestImprovement) -> None:
        """Reject malformed details and absent/cross-group references without writes."""
        observation = self.assessment_observation()
        baseline = self.assessment_details([observation])
        invalid = [{**baseline, "extra": True}, {**baseline, "observation_ids": []}, {**baseline, "observation_ids": [observation, observation]}, {**baseline, "observation_ids": [True]}, {**baseline, "disposition": "automatic_adoption"}, {**baseline, "rationale": " "}, {**baseline, "follow_up": ""}, {**baseline, "related_proposal_ids": [str(uuid4())]}, {**baseline, "predecessor_assessment_id": True}, {**baseline, "observation_ids": [str(uuid4())]}, {**baseline, "predecessor_assessment_id": str(uuid4())}, {**baseline, "disposition": "already_addressed", "related_proposal_ids": [str(uuid4())]}]
        before = sorted((self.root / ".local/workflow-improvement/events").glob("*.json"))
        for details in invalid:
            with self.subTest(details=details), self.assertRaises(ValueError):
                record_assessment(self.paths, self.config, details, str(uuid4()))
        self.assertEqual(before, sorted((self.root / ".local/workflow-improvement/events").glob("*.json")))
        other = self.assessment_observation()
        first = record_assessment(self.paths, self.config, self.assessment_details([observation]), str(uuid4()))["event"]
        with self.assertRaises(ValueError):
            record_assessment(self.paths, self.config, self.assessment_details([other], predecessor=first["event_id"]), str(uuid4()))
        with self.assertRaises(ValueError):
            record_assessment(self.paths, self.config, self.assessment_details([observation, other]), str(uuid4()))
        event = {**first, "schema_version": 1}
        with self.assertRaises(ValueError):
            validate_improvement_event(event)

    def test_assessment_filtered_status_and_existing_proposal(self: TestImprovement) -> None:
        """Keep v1 bytes/hashes and select assessments through valid proposal links."""
        observation = self.assessment_observation()
        identity = str(uuid4())
        markdown = "# " + identity + "\nIdentity and evidence\nExact change\nTradeoffs\nBounded trial\nRestoration\nDecisions\n"
        create_improvement_proposal(self.paths, self.config, identity, [observation], markdown, str(uuid4()))
        before = read_improvement_status(self.paths)["event_hashes"]
        record_assessment(self.paths, self.config, self.assessment_details([observation], disposition="already_addressed", related=[identity]), str(uuid4()))
        self.assertTrue(before.items() <= read_improvement_status(self.paths)["event_hashes"].items())
        selected = read_improvement_status(self.paths, proposal_id=identity)
        self.assertEqual(len(selected["assessments"]), 1)
        self.assertEqual(selected["pending_assessments"], [])
        self.assertEqual(read_improvement_status(self.paths, proposal_id=str(uuid4()))["assessments"], [])
        proposal_path = self.root / ".local/workflow-improvement/proposals" / identity / "proposal.md"
        proposal_path.write_text("changed bytes", encoding="utf-8")
        status = read_improvement_status(self.paths)
        self.assertTrue(status["issues"])
        self.assertEqual(status["pending_assessments"][0]["reason"], "conflict")

    def test_assessment_injected_cycles_orphans_and_versions(self: TestImprovement) -> None:
        """Preserve invalid-chain bytes and reject unsupported version combinations."""
        observation = self.assessment_observation()
        first = record_assessment(self.paths, self.config, self.assessment_details([observation]), str(uuid4()))["event"]
        event_path = self.root / ".local/workflow-improvement/events" / (first["event_id"] + ".json")
        for predecessor in (first["event_id"], str(uuid4())):
            broken = {**first, "details": {**first["details"], "predecessor_assessment_id": predecessor}}
            event_path.write_text(json.dumps(broken), encoding="utf-8")
            before = event_path.read_bytes()
            self.assertTrue(read_improvement_status(self.paths)["issues"])
            self.assertEqual(event_path.read_bytes(), before)
        for version in (True, 0, 3):
            with self.assertRaises(ValueError):
                validate_improvement_event({**first, "schema_version": version})

    def test_assessment_publication_failure_and_no_authority(self: TestImprovement) -> None:
        """Propagate publication errors and create no approvals or source edits."""
        observation = self.assessment_observation()
        details = self.assessment_details([observation])
        with patch("src.plan.improvement.write_record_json", side_effect=OSError("fixture failure")):
            with self.assertRaises(OSError):
                record_assessment(self.paths, self.config, details, str(uuid4()))
        record_assessment(self.paths, self.config, details, str(uuid4()))
        status = read_improvement_status(self.paths)
        self.assertEqual({event["kind"] for event in status["events"]}, {"observation", "assessment"})
        self.assertEqual(status["proposals"], [])
        self.assertFalse((self.root / ".agents").exists())
        self.assertFalse((self.root / ".codex").exists())
