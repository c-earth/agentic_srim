"""Accept unittest discovery; verify durable case transactions, lineage and recovery.

Uses isolated SQLite stores and synthetic local artifacts, without native runs.
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import sqlite3
from unittest.mock import patch

from case.records import CaseError
from case.storage import Store
from cases.support import CaseTest


class PersistenceTests(CaseTest):
    """Construct isolated test cases; verify case persistence and transactional invariants."""

    def test_reads_never_initialize_database_or_simulation_state(self) -> None:
        """Accept no inputs; read an absent store without creating files; return nothing."""
        self.assertEqual(self.store.perform("list_cases", {}), {"cases": [], "next_cursor": None})
        with self.assertRaisesRegex(CaseError, "does not exist"):
            self.store.perform("get_case", {"case_id": "a" * 32})
        self.assertEqual(list(self.runtime.iterdir()), [])

    def test_older_case_and_exact_revisions_survive_new_store(self) -> None:
        """Accept no inputs; recover an older case/checkpoint after restart; return nothing."""
        original = self.create()
        updated = self.change(original, {"objective": "Compare penetration", "plan": ["Inspect archived profiles"],
            "constraints": ["No simulation"], "next_action": "Retrieve profile"})
        self.create(title="Another case")
        reopened = Store(self.runtime)
        self.assertEqual(reopened.perform("get_case", {"case_id": original["id"]})["case"], updated)
        self.assertEqual(reopened.perform("get_case", {"case_id": original["id"], "revision": 1})["case"], original)
        result = reopened.perform("search_cases", {"query": "penetration"})
        self.assertEqual([row["id"] for row in result["cases"]], [original["id"]])
        self.assertEqual(list(self.runtime.iterdir()), [self.runtime / "cases.sqlite"])

    def test_retry_returns_original_response_after_newer_update(self) -> None:
        """Accept no inputs; replay create/update safely and reject changed payloads; return nothing."""
        arguments = self.audit(title="Retry", original_request="Persist safely")
        first = self.store.perform("create_case", arguments)
        self.assertEqual(first, self.store.perform("create_case", arguments))
        record = first["case"]
        update = self.audit(case_id=record["id"], expected_revision=1, patch={"next_action": "Read"})
        saved = self.store.perform("update_case", update)
        self.change(saved["case"], {"next_action": "Review"})
        self.assertEqual(self.store.perform("update_case", update), saved)
        self.assertEqual(self.store.perform("create_case", arguments), first)
        with self.assertRaises(CaseError) as error:
            self.store.perform("create_case", {**arguments, "title": "Changed retry"})
        self.assertEqual(error.exception.code, "idempotency_conflict")
        self.assertEqual(len(self.store.perform("list_cases", {})["cases"]), 1)

    def test_stale_update_rejected_without_consuming_retry_key(self) -> None:
        """Accept no inputs; reject a stale writer and allow its corrected retry; return nothing."""
        first = self.create()
        second = self.change(first, {"next_action": "Read"})
        arguments = self.audit(case_id=first["id"], expected_revision=1, patch={"next_action": "Review"})
        with self.assertRaises(CaseError) as error:
            self.store.perform("update_case", arguments)
        self.assertEqual(error.exception.code, "revision_conflict")
        arguments["expected_revision"] = second["revision"]
        self.assertEqual(self.store.perform("update_case", arguments)["case"]["revision"], 3)

    def test_concurrent_writers_and_concurrent_replays(self) -> None:
        """Accept no inputs; serialize competing revisions/retry keys; return nothing."""
        record = self.create()
        requests = [self.audit(case_id=record["id"], expected_revision=1, patch={"next_action": value})
                    for value in ("Read", "Review")]

        def update(arguments: dict) -> str:
            """Accept one update request; return its success/conflict outcome."""
            try:
                Store(self.runtime).perform("update_case", arguments)
                return "saved"
            except CaseError as exc:
                return exc.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(pool.map(update, requests), ["saved", "revision_conflict"])
        creation = self.audit(title="Concurrent retry", original_request="Only one case")
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: Store(self.runtime).perform("create_case", creation), range(2)))
        self.assertEqual(responses[0], responses[1])
        self.assertEqual(len(self.store.perform("list_cases", {})["cases"]), 2)

    def test_interruption_rolls_back_snapshot_history_and_retry(self) -> None:
        """Accept no inputs; fail after record insertion and recover without partial state; return nothing."""
        original = self.create()
        request = self.audit(case_id=original["id"], expected_revision=1, patch={"next_action": "Read"})
        save = self.store._save

        def fail(*arguments) -> None:
            """Accept save arguments; insert then simulate interruption with no return value."""
            save(*arguments)
            raise RuntimeError("Injected interruption")

        with patch.object(self.store, "_save", side_effect=fail), self.assertRaises(RuntimeError):
            self.store.perform("update_case", request)
        self.assertEqual(Store(self.runtime).perform("get_case", {"case_id": original["id"]})["case"], original)
        self.assertEqual(len(self.store.perform("get_case_history", {"case_id": original["id"]})["history"]), 1)
        self.assertEqual(self.store.perform("update_case", request)["case"]["revision"], 2)

    def test_invalid_first_write_leaves_readable_empty_store(self) -> None:
        """Accept no inputs; roll back an oversized initial snapshot and then create successfully; return nothing."""
        request = self.audit(title="Too large", original_request="Validate bounded storage",
                             content={"plan": ["x" * 16000] * 20})
        with self.assertRaises(CaseError):
            self.store.perform("create_case", request)
        self.assertEqual(self.store.perform("list_cases", {})["cases"], [])
        request["content"] = {"plan": ["Inspect"]}
        self.assertEqual(self.store.perform("create_case", request)["case"]["revision"], 1)

    def test_snapshot_tampering_and_future_schema_are_visible(self) -> None:
        """Accept no inputs; reject corrupt snapshots and unsupported database versions; return nothing."""
        record = self.create()
        with closing(sqlite3.connect(self.store.path)) as connection, connection:
            connection.execute("UPDATE revisions SET record_json='{}'")
        with self.assertRaises(CaseError) as error:
            self.store.perform("get_case", {"case_id": record["id"]})
        self.assertEqual(error.exception.code, "integrity_error")
        with closing(sqlite3.connect(self.store.path)) as connection, connection:
            connection.execute("PRAGMA user_version=2")
        with self.assertRaises(CaseError) as error:
            self.store.perform("list_cases", {})
        self.assertEqual(error.exception.code, "unsupported_schema")

    def test_search_literal_unicode_filters_and_keyset_pages(self) -> None:
        """Accept no inputs; disambiguate duplicate titles and bound literal search pages; return nothing."""
        records = [self.create(content={"objective": "Straße 100%_match"}) for _ in range(4)]
        archived = self.change(records[0], {"archived": True})
        found = self.store.perform("search_cases", {"query": "STRASSE"})
        self.assertEqual(len(found["cases"]), 3)
        self.assertEqual(len(self.store.perform("search_cases", {"query": "%_", "include_archived": True})["cases"]), 4)
        self.assertEqual(self.store.perform("search_cases", {"query": "does-not-exist"})["cases"], [])
        cursor, identities = None, []
        while True:
            page = self.store.perform("list_cases", {"limit": 1, "cursor": cursor, "status": None})
            identities.extend(row["id"] for row in page["cases"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        self.assertEqual(len(set(identities)), 3)
        self.assertNotIn(archived["id"], identities)
        self.assertEqual(identities, sorted(identities))
        self.assertEqual(self.store.perform("list_cases", {"status": "completed"})["cases"], [])

    def test_history_pages_retain_audit_and_progress_is_atomic(self) -> None:
        """Accept no inputs; persist coherent fields and page immutable audit entries; return nothing."""
        record = self.create()
        progress = {"objective": "Review", "plan": ["Inspect", "Compare"],
                    "decisions": [{"text": "Reuse available data", "limitations": ["Software checked only"]}],
                    "unresolved_issues": ["Missing independent reference"], "next_action": "Inspect"}
        record = self.store.perform("record_case_progress", self.audit(
            case_id=record["id"], expected_revision=1, progress=progress))["case"]
        history = self.store.perform("get_case_history", {"case_id": record["id"], "limit": 1})
        self.assertEqual(history["next_revision"], 1)
        next_page = self.store.perform("get_case_history", {"case_id": record["id"], "after_revision": 1})
        self.assertEqual(next_page["history"][0]["operation"], "record_case_progress")
        self.assertEqual(next_page["history"][0]["actor"], "test coordinator")
        self.assertIsNone(next_page["next_revision"])
        for key, value in progress.items():
            self.assertEqual(record[key], value)


class LifecycleTests(CaseTest):
    """Construct isolated cases; verify status, archive, immutable request and branch rules."""

    def test_pause_block_complete_archive_unarchive_and_reopen(self) -> None:
        """Accept no inputs; exercise lifecycle boundaries without losing prior state; return nothing."""
        record = self.change(self.create(), {"status": "paused"})
        with self.assertRaises(CaseError):
            self.change(record, {"status": "blocked"})
        record = self.change(record, {"status": "blocked", "unresolved_issues": ["Need reference"]})
        record = self.change(record, {"status": "active", "unresolved_issues": []})
        with self.assertRaises(CaseError):
            self.change(record, {"status": "completed"})
        record = self.change(record, {"status": "completed", "answers": [{"text": "Inconclusive", "limitations": ["No independent reference"]}]})
        with self.assertRaises(CaseError):
            self.change(record, {"title": "Edit closed case"})
        record = self.change(record, {"archived": True})
        self.assertEqual(record["status"], "completed")
        self.assertEqual(self.store.perform("list_cases", {})["cases"], [])
        with self.assertRaises(CaseError):
            self.change(record, {"archived": False, "status": "active"})
        record = self.change(record, {"archived": False})
        record = self.change(record, {"status": "active"})
        record = self.change(record, {"status": "cancelled"})
        self.assertEqual(record["status"], "cancelled")
        self.assertGreater(len(self.store.perform("get_case_history", {"case_id": record["id"]})["history"]), 5)

    def test_original_request_metadata_and_empty_patches_are_protected(self) -> None:
        """Accept no inputs; reject unauthorized schema fields and no-op mutations; return nothing."""
        record = self.create()
        for change in ({"original_request": "Rewrite"}, {"revision": 88}, {}, {"title": record["title"]}):
            with self.subTest(change=change), self.assertRaises(CaseError):
                self.change(record, change)
        self.assertEqual(self.store.perform("get_case", {"case_id": record["id"]})["case"], record)

    def test_branch_pins_parent_and_does_not_copy_approvals_or_answers(self) -> None:
        """Accept no inputs; branch exact old state with restrictive boundaries preserved; return nothing."""
        parent = self.create(content={"constraints": ["No native execution"], "prohibitions": ["Do not overwrite"],
            "budgets": {"run_limit": 0}, "plan": ["Review"],
            "approvals": [{"scope": "Historical run", "decision": "approved", "source": "Earlier user request"}],
            "answers": [{"text": "Historical answer"}]})
        latest = self.change(parent, {"constraints": ["Changed constraint"]})
        arguments = self.audit(parent_case_id=parent["id"], parent_revision=1,
                               title="Branched question", original_request="Investigate a related question")
        child = self.store.perform("branch_case", arguments)["case"]
        self.assertEqual(child["parent"], {"case_id": parent["id"], "revision": 1})
        self.assertEqual(child["constraints"], parent["constraints"])
        self.assertEqual(child["budgets"], {"run_limit": 0})
        self.assertEqual(child["status"], "active")
        for field in ("approvals", "answers", "plan"):
            self.assertEqual(child[field], [])
        self.assertEqual(self.store.perform("branch_case", arguments)["case"], child)
        self.assertEqual(self.store.perform("get_case", {"case_id": parent["id"]})["case"], latest)


class ArtifactTests(CaseTest):
    """Construct isolated cases; verify owner-qualified shared links and visible integrity failures."""

    def artifact(self, **values) -> dict:
        """Accept link-field overrides; return a synthetic versioned SRIM reference."""
        return {"owner": "srim", "artifact_id": "run-a/attempt-1/profile", "kind": "profile",
                "purpose": "Compare depth", "provenance": "Synthetic regression artifact", **values}

    def link(self, record: dict, artifact: dict) -> dict:
        """Accept a case/link; persist the reference and return its new snapshot."""
        return self.store.perform("link_case_artifact", self.audit(
            case_id=record["id"], expected_revision=record["revision"], artifact=artifact))["case"]

    def test_shared_artifact_available_changed_missing_and_archive_preservation(self) -> None:
        """Accept no inputs; share one producer file and expose changed/missing bytes; return nothing."""
        file = self.runtime / "profile.txt"
        file.write_bytes(b"synthetic profile")
        artifact = self.artifact(relative_path="profile.txt", sha256=hashlib.sha256(file.read_bytes()).hexdigest())
        first = self.link(self.create(), artifact)
        second = self.link(self.create(), artifact)
        self.assertEqual(self.store.perform("get_case", {"case_id": second["id"]})["artifact_checks"][0]["state"], "verified")
        first = self.change(first, {"archived": True})
        self.assertTrue(file.exists())
        file.write_bytes(b"changed")
        self.assertEqual(self.store.perform("get_case", {"case_id": second["id"]})["artifact_checks"][0]["state"], "checksum_mismatch")
        file.unlink()
        self.assertEqual(self.store.perform("get_case", {"case_id": first["id"]})["artifact_checks"][0]["state"], "missing")
        self.assertEqual(second["artifacts"], [artifact])

    def test_unresolved_references_and_unlinked_claims_are_explicit(self) -> None:
        """Accept no inputs; retain unresolved owner IDs and validate claim linkage; return nothing."""
        record = self.create()
        entry = {"text": "Review profile", "artifact_refs": [{"owner": "srim", "artifact_id": "run-a/attempt-1/profile"}]}
        with self.assertRaises(CaseError) as error:
            self.change(record, {"findings": [entry]})
        self.assertEqual(error.exception.code, "unlinked_artifact")
        record = self.link(record, self.artifact())
        record = self.change(record, {"findings": [entry]})
        self.assertEqual(self.store.perform("get_case", {"case_id": record["id"]})["artifact_checks"][0]["state"], "unresolved")
        with self.assertRaises(CaseError) as error:
            self.link(record, self.artifact())
        self.assertEqual(error.exception.code, "artifact_conflict")
        other = self.link(record, self.artifact(owner="analysis"))
        self.assertEqual(len(other["artifacts"]), 2)

    def test_unsafe_paths_and_missing_checksum_locators_are_rejected(self) -> None:
        """Accept no inputs; reject traversal, drive paths and malformed references; return nothing."""
        record = self.create()
        for locator in ("../secret", "D:\\outside.txt", "C:relative.txt", "\\\\host\\share", "/outside", "https://example.com/file"):
            with self.subTest(locator=locator), self.assertRaises(CaseError):
                self.link(record, self.artifact(relative_path=locator))
        with self.assertRaises(CaseError):
            self.link(record, self.artifact(sha256="a" * 64))
        self.assertEqual(self.store.perform("get_case", {"case_id": record["id"]})["case"]["revision"], 1)

    def test_overview_is_complete_revision_stamped_and_read_only(self) -> None:
        """Accept no inputs; render authoritative data safely without writing cached views; return nothing."""
        record = self.create(content={"objective": "Inspect ``` fenced data", "constraints": ["No execution"],
                                     "next_action": "Read archive"})
        record = self.link(record, self.artifact())
        before = self.store.path.read_bytes()
        result = self.store.perform("get_case_overview", {"case_id": record["id"]})
        self.assertIn(f"Revision: {record['revision']}", result["markdown"])
        for text in ("Original request", "Approval evidence", "Artifact index", "No execution", "run-a/attempt-1/profile", "unresolved"):
            self.assertIn(text, result["markdown"])
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(sorted(path.name for path in self.runtime.iterdir()), ["cases.sqlite"])
