"""Verify existing self-improvement record support preserves approval boundaries."""

from __future__ import annotations

from uuid import uuid4

from src.plan.improvement import create_improvement_proposal, read_improvement_status, record_observation, validate_improvement_event
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).absolute().parents[1] / "mcp"))
from test_contracts import Fixture


class TestImprovement(Fixture):
    """Preserve immutable observations/proposals without introducing trial authority."""

    def test_observation_proposal_and_malformed_evidence(self: TestImprovement) -> None:
        """Link exact proposal bytes, reject forged schema and report malformed events."""
        details = {"problem": "Repeated inspection", "evidence": "Explicit measured task evidence", "sources": ["doc/example"], "criteria": ["time"], "measurements": [], "uncertainty": "No speedup measured"}
        observation = record_observation(self.paths, self.config, details, str(uuid4()))
        identity = str(uuid4())
        markdown = "# " + identity + "\nIdentity and evidence\nExact change\nTradeoffs\nBounded trial\nRestoration\nDecisions\n"
        result = create_improvement_proposal(self.paths, self.config, identity, [observation["event"]["event_id"]], markdown, str(uuid4()))
        self.assertEqual((self.root / result["proposal"]["path"]).read_bytes(), markdown.encode("utf-8"))
        self.assertFalse(read_improvement_status(self.paths)["issues"])
        forged = {**observation["event"], "approved": True}
        with self.assertRaises(ValueError):
            validate_improvement_event(forged)
        (self.root / ".cache" / "workflow-improvement" / "events" / "broken.json").write_bytes(b"{}")
        self.assertTrue(read_improvement_status(self.paths)["issues"])
