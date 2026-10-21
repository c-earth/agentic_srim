"""Verify empty initialization, incremental transactions, recovery and collection."""

from __future__ import annotations

from contextvars import copy_context
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import uuid

import test_source as fixtures
from src.knowledge.index import collect_index_updates, initialize_index, notify_simulation_finished, rebuild_index, update_index
from src.knowledge.search import search_inputs
from src.srim.metadata import read_simulation


class TestIndex(fixtures.NativeFixture):
    """Use cache-owned native evidence and never launch a numerical engine."""

    def test_empty_start_and_incremental_noop(self: TestIndex) -> None:
        """Do not backfill existing native records; update only supplied IDs; return None."""
        self.assertEqual(search_inputs(self.paths)["total"], 0)
        self.assertFalse((self.paths.root / "memory/knowledge").exists())
        initial = initialize_index(self.paths)
        self.assertEqual(search_inputs(self.paths)["total"], 0)
        with patch("src.knowledge.index.project_simulation_inputs", wraps=fixtures.project_simulation_inputs) as project:
            report = update_index(self.paths, [self.created.path.name])
        self.assertEqual({call.args[1] for call in project.call_args_list}, {self.created.path.name})
        self.assertEqual(report["state"], "succeeded")
        self.assertNotEqual(initial["generation"], report["generation"])
        repeated = update_index(self.paths, [self.created.path.name])
        self.assertEqual(repeated["generation"], report["generation"])
        self.assertEqual(repeated["skipped"], [self.created.path.name])

    def test_reject_tamper_and_explicit_recovery(self: TestIndex) -> None:
        """Remove stale searchable records and recover a corrupt catalog explicitly; return None."""
        update_index(self.paths, [self.created.path.name])
        database = self.paths.root / "memory/knowledge/index.sqlite"
        database.write_bytes(b"corrupt database")
        self.assertEqual(update_index(self.paths, [self.created.path.name])["state"], "failed")
        recovered = rebuild_index(self.paths, [self.created.path.name])
        self.assertEqual(recovered["state"], "succeeded")
        (self.created.path / "input/engine/SR.IN").write_bytes(b"tamper")
        self.assertEqual(update_index(self.paths, [self.created.path.name])["rejected"], [self.created.path.name])
        self.assertEqual(search_inputs(self.paths)["total"], 0)
        self.assertTrue(search_inputs(self.paths)["excluded"])

    def test_batch_context_threads_and_exception(self: TestIndex) -> None:
        """Share copied worker contexts and flush once despite an original error; return None."""
        metadata = read_simulation(self.paths, self.created.path.name)
        with patch("src.knowledge.index.update_index") as update:
            with self.assertRaisesRegex(RuntimeError, "original"):
                with collect_index_updates(self.paths):
                    with collect_index_updates(self.paths):
                        with ThreadPoolExecutor(max_workers=2) as executor:
                            futures = [executor.submit(copy_context().run, notify_simulation_finished, self.paths, metadata) for _ in range(3)]
                            for future in futures:
                                future.result()
                        update.assert_not_called()
                    raise RuntimeError("original")
            update.assert_called_once_with(self.paths, [self.created.path.name])
        with patch("src.knowledge.index.update_index", side_effect=OSError("storage")):
            notify_simulation_finished(self.paths, metadata)
        self.assertEqual(read_simulation(self.paths, self.created.path.name), metadata)

    def test_diagnostic_failure_does_not_undo_commit(self: TestIndex) -> None:
        """A report-write error leaves the committed record searchable; return None."""
        with patch("src.knowledge.index._write_update_report", side_effect=OSError("report storage")):
            report = update_index(self.paths, [self.created.path.name])
        self.assertEqual(report["state"], "succeeded")
        self.assertEqual(search_inputs(self.paths)["total"], 1)

    def test_transaction_rollback_and_unchanged_projection_skip(self: TestIndex) -> None:
        """Rollback interrupted row changes and skip replaying unchanged evidence; return None."""
        initial = initialize_index(self.paths)
        with patch("src.knowledge.index.uuid.uuid4", side_effect=[uuid.uuid4(), RuntimeError("transaction failure")]):
            report = update_index(self.paths, [self.created.path.name])
        self.assertEqual(report["state"], "failed")
        self.assertEqual(report["indexed"], [])
        self.assertEqual(search_inputs(self.paths)["total"], 0)
        self.assertEqual(search_inputs(self.paths)["generation"], initial["generation"])
        update_index(self.paths, [self.created.path.name])
        with patch("src.knowledge.index.project_simulation_inputs", side_effect=AssertionError("unchanged replay")):
            report = update_index(self.paths, [self.created.path.name])
        self.assertEqual(report["state"], "succeeded")
        self.assertEqual(report["skipped"], [self.created.path.name])

    def test_rebuild_rejects_concurrent_generation_change(self: TestIndex) -> None:
        """Reject staged publication over a concurrently committed record; return None."""
        initialize_index(self.paths)

        def concurrent(project: object, simulation_id: str) -> dict[str, object]:
            """Project project/simulation_id after committing another catalog generation."""
            with patch("src.knowledge.index.project_simulation_inputs", wraps=fixtures.project_simulation_inputs):
                update_index(self.paths, [self.created.path.name])
            return fixtures.project_simulation_inputs(self.paths, simulation_id)

        with patch("src.knowledge.index.project_simulation_inputs", side_effect=concurrent):
            report = rebuild_index(self.paths, [self.created.path.name])
        self.assertEqual(report["state"], "failed")
        self.assertIn("changed during rebuild", report["issues"][-1]["message"])
        self.assertEqual(search_inputs(self.paths)["total"], 1)
