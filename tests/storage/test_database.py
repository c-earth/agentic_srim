"""Accept repository fixtures and unittest discovery; expose storage/test_database.py checks without native execution unless opted in."""

from contextlib import contextmanager
import json
from pathlib import Path
import uuid
from unittest.mock import patch

from agentic_srim.capabilities.stopping_range.workflow import submit
from agentic_srim.execution.jobs import read_verified
from agentic_srim.storage.artifacts import checksum, seal, write_json
from agentic_srim.storage.database import connect, get_run, now, transition
from sr_module.support import IsolatedTest


class DatabaseTests(IsolatedTest):
    """Construct unittest cases; verify storage contracts and report failures."""

    def test_database_rejects_unknown_schema(self) -> None:
        """Accept no inputs; verify a newer metadata version cannot be mutated."""
        with connect(self.runtime) as connection:
            connection.execute("PRAGMA user_version=99")
        with self.assertRaisesRegex(ValueError, "schema version"):
            with connect(self.runtime):
                pass

    def test_concurrent_completion_keeps_reads_on_one_snapshot(self) -> None:
        """Accept no inputs; verify coherent reads across a concurrent commit; return nothing.

        A second SQLite connection commits completion after the run-row read.
        Test-only WAL permits that commit while the reader retains its snapshot;
        production journal settings remain unchanged. Temporary sealed results
        allow both metadata and archive-verified retrieval to be checked.
        """
        with connect(self.runtime) as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
        for reader in (get_run, read_verified):
            with self.subTest(reader=reader.__name__):
                run_id = submit(self.request, self.settings)
                attempt_id = uuid.uuid4().hex
                archive = self.runtime / "runs" / run_id / "attempts" / attempt_id
                archive.mkdir(parents=True)
                result = {"analysis_version": "sr-table-v1", "values": [1, 2, 3],
                          "provenance": {"run_id": run_id, "attempt_id": attempt_id}}
                write_json(archive / "result.json", result)
                write_json(archive / "outcome.json", {"state": "CHECKED", "error": None})
                seal(archive)
                manifest_hash = checksum(archive / "manifest.json")
                with connect(self.runtime) as connection:
                    connection.execute("INSERT INTO attempts(id,run_id,ordinal,state,started_at,artifact_path) VALUES(?,?,?,?,?,?)",
                                       (attempt_id, run_id, 1, "RUNNING", now(), str(archive.relative_to(self.runtime))))
                    transition(connection, run_id, "RUNNING", attempt_id)
                committed = []
                failures = []
                with connect(self.runtime) as writer:

                    def complete_between_reads(statement: str) -> None:
                        """Accept a traced SQL statement; commit one completion between reads; return nothing."""
                        if not statement.startswith("SELECT * FROM attempts") or committed or failures:
                            return
                        try:
                            with writer:
                                writer.execute("UPDATE attempts SET state=?,finished_at=?,manifest_sha256=? WHERE id=?",
                                               ("CHECKED", now(), manifest_hash, attempt_id))
                                transition(writer, run_id, "CHECKED", attempt_id)
                                writer.execute("INSERT INTO results VALUES(?,?,?,?)",
                                               (run_id, attempt_id, result["analysis_version"], json.dumps(result)))
                            committed.append(run_id)
                        except Exception as exc:
                            failures.append(exc)

                    @contextmanager
                    def interleaved_connection(runtime: Path):
                        """Accept test runtime; yield a real reader with a scheduled competing commit."""
                        with connect(runtime) as connection:
                            connection.set_trace_callback(complete_between_reads)
                            yield connection

                    with patch("agentic_srim.storage.database.connect", interleaved_connection):
                        before = reader(self.runtime, run_id)
                self.assertEqual(failures, [])
                self.assertEqual(committed, [run_id])
                self.assertEqual(before["state"], "RUNNING")
                self.assertEqual(before["attempts"][0]["state"], "RUNNING")
                self.assertIsNone(before["attempts"][0]["manifest_sha256"])
                self.assertEqual([event["state"] for event in before["events"]], ["QUEUED", "RUNNING"])
                self.assertNotIn("result", before)
                after = read_verified(self.runtime, run_id)
                self.assertEqual(after["state"], "CHECKED")
                self.assertEqual(after["attempts"][0]["state"], "CHECKED")
                self.assertEqual(after["attempts"][0]["manifest_sha256"], manifest_hash)
                self.assertEqual([event["state"] for event in after["events"]], ["QUEUED", "RUNNING", "CHECKED"])
                self.assertEqual(after["result"], result)
