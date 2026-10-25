"""Verify literal ranked retrieval, relationships, revision consistency and short locks."""

from __future__ import annotations

from copy import deepcopy
from unittest.mock import patch
from uuid import uuid4

import test_source as fixtures
from src.backend.paths import ProjectPaths
from src.backend.project_records import initialize_record, revise_record
from src.knowledge.index import update_index
from src.knowledge.search import search_inputs
from src.knowledge.retrieval_index import update_retrieval_index
from src.knowledge.retrieval_schema import EMPTY_QUERY
from src.knowledge.retrieval_search import search_knowledge, read_knowledge_links
from src.knowledge.retrieval_sources import read_retrieval_document
from src.knowledge.source import project_simulation_inputs, read_source_record
from src.srim.common.types import JSONObject


class TestRetrievalSearch(fixtures.NativeFixture):
    """Exercise cache-owned synthetic records with actual FTS5 and exact references."""

    def test_text_ranking_and_literal_operators(self: TestRetrievalSearch) -> None:
        """Weight titles and questions; treat OR and quote punctuation as literal tokens."""
        spec = {"title": "Helium range", "question": "Silica", "status": "open", "tags": [], "notes": "", "members": []}
        first = initialize_record(self.paths, self.config, "case", spec, str(uuid4()))
        initialize_record(self.paths, self.config, "case", {**spec, "title": "Other study", "question": "Other", "notes": "Helium range"}, str(uuid4()))
        query = {**deepcopy(EMPTY_QUERY), "text": "helium range"}
        result = search_knowledge(self.paths, query)
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["matches"][0]["reference"], first["reference"])
        self.assertTrue(result["matches"][0]["sections"])
        self.assertEqual(search_knowledge(self.paths, {**query, "text": '"helium" OR range'})["total"], 0)
        with self.assertRaises(ValueError):
            search_knowledge(self.paths, {**query, "text": " ".join(["helium"] * 65)})
        import sqlite3
        database = self.paths.root / "memory/knowledge/retrieval/index.sqlite"
        connection = sqlite3.connect(database)
        try:
            connection.execute("UPDATE document_text SET title='inventedword'")
            connection.commit()
        finally:
            connection.close()
        self.assertEqual(search_knowledge(self.paths, {**query, "text": "inventedword"})["total"], 0)

    def test_exact_links_and_scientific_target_filter(self: TestRetrievalSearch) -> None:
        """Follow pinned native members and filter their actual shared target inputs."""
        reference = project_simulation_inputs(self.paths, self.created.path.name)["reference"]
        member = {"member_id": str(uuid4()), "role": "evidence", "note": "Preserved failure", "reference": {"kind": "native-source", "source": reference}}
        created = initialize_record(self.paths, self.config, "case", {"title": "Linked", "question": "Range", "status": "open", "tags": [], "notes": "", "members": [member]}, str(uuid4()))
        clause = {"relation": "case_member", "direction": "outgoing", "reference": None, "target_input_filters": {"schema_version": 1, "clauses": [{"field": "energy", "operator": "eq", "value": "100", "unit": "keV", "layer": None}]}, "target_output_filter": None}
        query = {**deepcopy(EMPTY_QUERY), "kinds": ["case"], "relations": [clause]}
        self.assertEqual(search_knowledge(self.paths, query)["total"], 1)
        self.assertEqual(read_knowledge_links(self.paths, "run", reference, direction="incoming")["links"][0]["origin"], created["reference"])
        clause["target_input_filters"]["clauses"][0]["value"] = "999"
        self.assertEqual(search_knowledge(self.paths, query)["total"], 0)
        (self.created.path / "input/engine/SR.IN").write_bytes(b"tampered")
        self.assertEqual(search_knowledge(self.paths, query)["total"], 0)
        self.assertEqual(read_retrieval_document(self.paths, "case", created["reference"], dependencies=True)["verification"]["dependencies"], "issues")

    def test_search_releases_lock_and_captures_membership(self: TestRetrievalSearch) -> None:
        """Permit real index publication during evidence reads without mixing generations."""
        first = initialize_record(self.paths, self.config, "case", {"title": "Before", "question": "Question", "status": "open", "tags": [], "notes": "", "members": []}, str(uuid4()))
        original = read_retrieval_document
        fired = False

        def interleave(paths: ProjectPaths, kind: str, reference: JSONObject, *, dependencies: bool = False) -> JSONObject:
            """Publish another case while reading reference from the captured search."""
            nonlocal fired
            if not fired:
                fired = True
                initialize_record(paths, self.config, "case", {"title": "After", "question": "Question", "status": "open", "tags": [], "notes": "", "members": []}, str(uuid4()))
            return original(paths, kind, reference, dependencies=dependencies)

        with patch("src.knowledge.retrieval_search.read_retrieval_document", side_effect=interleave):
            result = search_knowledge(self.paths, deepcopy(EMPTY_QUERY))
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["matches"][0]["reference"], first["reference"])
        with self.assertRaisesRegex(ValueError, "generation"):
            search_knowledge(self.paths, deepcopy(EMPTY_QUERY), expected_generation=result["generation"])
        self.assertEqual(search_knowledge(self.paths, deepcopy(EMPTY_QUERY))["total"], 2)

    def test_stale_current_and_legacy_lock_release(self: TestRetrievalSearch) -> None:
        """Exclude missed newer revisions and permit legacy publication during verification."""
        spec = {"title": "Current", "question": "Question", "status": "open", "tags": [], "notes": "", "members": []}
        first = initialize_record(self.paths, self.config, "case", spec, str(uuid4()))
        with patch("src.knowledge.retrieval_index.notify_project_record_published"):
            revise_record(self.paths, self.config, "case", first["record"]["id"], 1, {**spec, "notes": "new"}, str(uuid4()))
        current = search_knowledge(self.paths, deepcopy(EMPTY_QUERY))
        self.assertEqual(current["total"], 0)
        self.assertIn("stale_current", current["excluded"][0]["message"])
        update_index(self.paths, [self.created.path.name])
        original = read_source_record

        def interleave(paths: ProjectPaths, reference: JSONObject) -> JSONObject:
            """Perform a real legacy index update during a selected source read."""
            update_index(paths, [reference["id"]])
            return original(paths, reference)

        with patch("src.knowledge.search.read_source_record", side_effect=interleave):
            self.assertEqual(search_inputs(self.paths)["total"], 1)

    def test_analysis_lifecycle_findings_and_missing_artifacts(self: TestRetrievalSearch) -> None:
        """Index draft, attachment and terminal revisions while retaining damaged narratives."""
        from src.analysis.records import create_analysis, attach_analysis_artifacts, finish_analysis
        from src.knowledge.retrieval_search import get_knowledge
        spec = {"title": "Analysis", "question": "What did we observe?", "method": {"kind": "ad-hoc", "reference": None, "description": "Inspect preserved evidence", "citations": [], "code_artifacts": []}, "inputs": [], "eligibility": {"allow_failed_runs": False, "allow_checkpoint_recovery": False, "require_complete_parser": False}, "parameters": {}, "findings": "", "limitations": ""}
        draft = create_analysis(self.paths, self.config, spec, str(uuid4()))
        report = self.paths.root / "report.txt"
        report.write_text("Observed evidence", encoding="utf-8")
        attached = attach_analysis_artifacts(self.paths, self.config, draft["record"]["id"], 1, [{"name": "report.txt", "role": "report", "source_path": str(report), "media_type": "text/plain"}], str(uuid4()), allowed_roots=(self.paths.root,))
        completed = finish_analysis(self.paths, self.config, draft["record"]["id"], attached["record"]["revision"], "completed", "Helium range increases", "One study only", None, str(uuid4()))
        query = {**deepcopy(EMPTY_QUERY), "kinds": ["analysis"], "text": "helium increases"}
        hit = search_knowledge(self.paths, query)["matches"][0]
        self.assertEqual(hit["reference"], completed["reference"])
        self.assertEqual(hit["status"], "completed")
        artifact = completed["record"]["artifacts"][0]
        (self.paths.postprocessing_memory / draft["record"]["id"] / artifact["path"]).unlink()
        self.assertEqual(search_knowledge(self.paths, query)["total"], 1)
        checked = get_knowledge(self.paths, "analysis", completed["reference"], dependencies=True)
        self.assertEqual(checked["verification"]["dependencies"], "issues")
        self.assertEqual(checked["document"]["status"], "completed")
