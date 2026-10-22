"""Verify immutable case/analysis revision storage and optimistic concurrency."""

from __future__ import annotations

from uuid import uuid4

from src.backend.project_records import initialize_record, read_record, revise_record
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).absolute().parents[1] / "mcp"))
from test_contracts import Fixture


class TestProjectRecords(Fixture):
    """Check native-independent identity allocation, revision chains and CAS."""

    def test_revision_chain_and_tampering(self: TestProjectRecords) -> None:
        """Create/revise one case, reject stale writes and detect changed historic bytes."""
        spec = {"title": "Case", "question": "Question?", "status": "open", "tags": [], "notes": "", "members": []}
        first = initialize_record(self.paths, self.config, "case", spec, str(uuid4()))
        identity = first["record"]["id"]
        second = revise_record(self.paths, self.config, "case", identity, 1, {**spec, "notes": "Evidence"}, str(uuid4()))
        self.assertEqual(second["record"]["revision"], 2)
        self.assertEqual(read_record(self.paths, "case", identity, revision=1)["reference"], first["reference"])
        with self.assertRaises(ValueError):
            revise_record(self.paths, self.config, "case", identity, 1, spec, str(uuid4()))
        (self.paths.cases / identity / "revisions" / "r000001.json").write_bytes(b"{}")
        with self.assertRaises(ValueError):
            read_record(self.paths, "case", identity)
