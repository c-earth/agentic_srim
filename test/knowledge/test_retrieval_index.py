"""Verify incremental publication, generation, transaction and reconciliation boundaries."""

from __future__ import annotations

from copy import deepcopy
import sqlite3
from unittest.mock import patch
from uuid import uuid4

import test_source as fixtures
from src.backend.paths import ProjectPaths
from src.backend.project_records import initialize_record, revise_record
from src.knowledge.retrieval_index import read_retrieval_status, reconcile_retrieval_index, update_retrieval_index
from src.knowledge.retrieval_schema import EMPTY_QUERY
from src.knowledge.retrieval_search import search_knowledge
from src.srim.common.types import JSONObject


class TestRetrievalIndex(fixtures.NativeFixture):
    """Use cache-owned synthetic projects without launching native engines."""

    def test_revision_publication_current_history_and_noop(self: TestRetrievalIndex) -> None:
        """Index each published revision, keep history, and retain no-op generation."""
        spec = {"title": "Helium", "question": "Range in silica?", "status": "open", "tags": ["range"], "notes": "", "members": []}
        first = initialize_record(self.paths, self.config, "case", spec, str(uuid4()))
        status = read_retrieval_status(self.paths)
        self.assertEqual(status["counts"]["case"]["revisions"], 1)
        response = update_retrieval_index(self.paths, [{"kind": "case", "reference": first["reference"]}])
        self.assertEqual(response["generation"], status["generation"])
        self.assertEqual(len(response["skipped"]), 1)
        second = revise_record(self.paths, self.config, "case", first["record"]["id"], 1, {**spec, "notes": "New evidence"}, str(uuid4()))
        query = deepcopy(EMPTY_QUERY)
        self.assertEqual(search_knowledge(self.paths, query)["matches"][0]["reference"], second["reference"])
        query["revision_scope"] = "history"
        self.assertEqual(search_knowledge(self.paths, query)["total"], 2)
        update_retrieval_index(self.paths, [{"kind": "case", "reference": first["reference"]}])
        query["revision_scope"] = "current"
        self.assertEqual(search_knowledge(self.paths, query)["matches"][0]["reference"], second["reference"])

    def test_missed_notification_reconciliation_preview_and_conflict(self: TestRetrievalIndex) -> None:
        """Repair only derived storage with exact preview/apply digest and explicit scope."""
        spec = {"title": "Missing", "question": "Find me", "status": "open", "tags": [], "notes": "", "members": []}
        with patch("src.knowledge.retrieval_index.notify_project_record_published"):
            created = initialize_record(self.paths, self.config, "case", spec, str(uuid4()))
        self.assertIsNone(read_retrieval_status(self.paths)["generation"])
        self.assertEqual(reconcile_retrieval_index(self.paths)["actions"], [])
        plan = reconcile_retrieval_index(self.paths, scope="project")
        self.assertIn({"kind": "case", "reference": created["reference"]}, plan["actions"])
        before = (self.paths.cases / created["record"]["id"] / "current.json").read_bytes()
        applied = reconcile_retrieval_index(self.paths, scope="project", expected_plan_sha256=plan["plan_sha256"])
        self.assertEqual(applied["report"]["state"], "succeeded")
        self.assertEqual((self.paths.cases / created["record"]["id"] / "current.json").read_bytes(), before)
        with self.assertRaisesRegex(ValueError, "conflict"):
            reconcile_retrieval_index(self.paths, scope="project", expected_plan_sha256=plan["plan_sha256"])

    def test_publication_unlock_and_notification_failure(self: TestRetrievalIndex) -> None:
        """Prove callback runs after source lock release and cannot fail publication."""
        from src.backend.project_records import record_lock
        spec = {"title": "Callback", "question": "Locks?", "status": "open", "tags": [], "notes": "", "members": []}

        def inspect_reference(paths: ProjectPaths, reference: JSONObject) -> None:
            """Acquire source lock for reference under paths, then inject notification failure."""
            with record_lock(paths, reference["kind"], reference["id"]):
                raise OSError("Index unavailable")

        with patch("src.knowledge.retrieval_index.notify_project_record_published", side_effect=inspect_reference) as notify:
            created = initialize_record(self.paths, self.config, "case", spec, str(uuid4()))
            request_id = str(uuid4())
            revised = revise_record(self.paths, self.config, "case", created["record"]["id"], 1, spec, request_id)
            replayed = revise_record(self.paths, self.config, "case", created["record"]["id"], 1, spec, request_id)
        self.assertEqual(revised, replayed)
        self.assertEqual(notify.call_count, 3)

    def test_transaction_failure_preserves_generation(self: TestRetrievalIndex) -> None:
        """An FTS publication error rolls back all document/head changes."""
        from src.knowledge.retrieval_index import initialize_retrieval_index
        from src.knowledge.source import project_simulation_inputs
        before = initialize_retrieval_index(self.paths)["generation"]
        database = self.paths.root / "memory/knowledge/retrieval/index.sqlite"
        connection = sqlite3.connect(database)
        try:
            connection.execute("CREATE TRIGGER fail_retrieval BEFORE INSERT ON documents BEGIN SELECT RAISE(ABORT, 'injected transaction failure'); END")
            connection.commit()
        finally:
            connection.close()
        response = update_retrieval_index(self.paths, [{"kind": "run", "reference": project_simulation_inputs(self.paths, self.created.path.name)["reference"]}])
        self.assertEqual(response["state"], "failed")
        connection = sqlite3.connect(database)
        try:
            self.assertEqual(connection.execute("SELECT value FROM meta WHERE key='generation'").fetchone()[0], before)
            self.assertEqual(connection.execute("SELECT count(*) FROM documents").fetchone()[0], 0)
        finally:
            connection.close()
