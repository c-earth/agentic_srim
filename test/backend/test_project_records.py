"""Verify immutable case/analysis revision storage and optimistic concurrency."""

from __future__ import annotations

from uuid import uuid4
from unittest.mock import patch

from src.backend.project_records import initialize_record, list_records, read_record, revise_record, write_record_json
from src.srim.common.types import JSONObject
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).absolute().parents[1] / "mcp"))
from test_contracts import Fixture


class TestProjectRecords(Fixture):
    """Check temporary publication failures, identity allocation, revision chains and CAS."""

    def test_initial_pointer_failure_is_reported(self: TestProjectRecords) -> None:
        """Report real failed initial pointer publication alongside valid case/analysis records."""
        def fail_initial_pointer(path: Path, document: JSONObject, *, replace: bool = False) -> JSONObject:
            """Publish document/path with replace, raising before initial pointer writes."""
            if path.name == "current.json" and not replace:
                raise OSError("Injected initial pointer failure")
            return write_record_json(path, document, replace=replace)

        for kind in ("case", "analysis"):
            with self.subTest(kind=kind):
                spec = {"title": "Case", "question": "Question?", "status": "open", "tags": [], "notes": "", "members": []} if kind == "case" else {"title": "Analysis", "question": "Question?", "status": "draft", "method": {"kind": "ad-hoc", "reference": None, "description": "Inspection", "citations": [], "code_artifacts": []}, "inputs": [], "eligibility": {"allow_failed_runs": False, "allow_checkpoint_recovery": False, "require_complete_parser": True}, "parameters": {}, "artifacts": [], "findings": "", "limitations": "", "failure": None}
                good = initialize_record(self.paths, self.config, kind, spec, str(uuid4()))
                with patch("src.backend.project_records.write_record_json", side_effect=fail_initial_pointer), self.assertRaises(OSError):
                    initialize_record(self.paths, self.config, kind, spec, str(uuid4()))
                parent = self.paths.cases if kind == "case" else self.paths.postprocessing_memory
                partial = next(path for path in parent.iterdir() if path.name != good["record"]["id"] and not path.name.startswith("."))
                revision = partial / "revisions" / "r000001.json"
                before = revision.read_bytes()
                listing = list_records(self.paths, kind)
                self.assertEqual(listing["total"], 1)
                self.assertEqual(listing["matches"][0]["reference"], good["reference"])
                self.assertEqual(len(listing["issues"]), 1)
                self.assertEqual(listing["issues"][0]["id"], partial.name)
                self.assertIn("current.json is missing", listing["issues"][0]["message"])
                filtered = list_records(self.paths, kind, status="completed", offset=1, limit=1)
                self.assertEqual(filtered["total"], 0)
                self.assertEqual(filtered["issues"], listing["issues"])
                self.assertEqual(revision.read_bytes(), before)
                self.assertFalse((partial / "current.json").exists())

    def test_missing_pointer_allocation_classification(self: TestProjectRecords) -> None:
        """Distinguish empty, initialized/staged and unknown layouts without repair."""
        for number, mode in enumerate(("empty", "revisions", "root-stage", "revision-stage", "unknown"), 1):
            target = self.paths.cases / f"case_20000101_000000_{number}"
            target.mkdir()
            if mode in ("revisions", "revision-stage"):
                (target / "revisions").mkdir()
            if mode in ("root-stage", "revision-stage"):
                ((target / "revisions" if mode == "revision-stage" else target) / ".project-test").write_bytes(b"unfinished")
            if mode == "unknown":
                (target / "legacy.json").write_bytes(b"{}")
        spec = {"title": "Published", "question": "Question?", "status": "open", "tags": [], "notes": "", "members": []}
        good = initialize_record(self.paths, self.config, "case", spec, str(uuid4()))
        identity = good["record"]["id"]
        listing = list_records(self.paths, "case")
        self.assertEqual(listing["total"], 1)
        self.assertEqual({issue["id"] for issue in listing["issues"]}, {f"case_20000101_000000_{number}" for number in (2, 3, 4)})
        before = {path: path.read_bytes() for path in self.paths.cases.rglob("*") if path.is_file()}
        self.assertEqual(list_records(self.paths, "case"), listing)
        self.assertEqual(before, {path: path.read_bytes() for path in self.paths.cases.rglob("*") if path.is_file()})
        with patch("src.backend.project_records.os.replace", side_effect=OSError("Injected later pointer failure")), self.assertRaises(OSError):
            revise_record(self.paths, self.config, "case", identity, 1, {**spec, "notes": "Unpublished"}, str(uuid4()))
        self.assertEqual(read_record(self.paths, "case", identity)["reference"], good["reference"])
        self.assertEqual(list_records(self.paths, "case"), listing)

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
