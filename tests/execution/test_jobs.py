"""Use isolated SQLite records and sealed synthetic results to test verified retrieval.

Inputs are unittest discovery and temporary test storage. Tests expose integrity
failures without launching simulators or modifying production runtime data.
"""

import json
import uuid

from agentic_srim.capabilities.stopping_range.workflow import submit
from agentic_srim.execution.jobs import read_metadata, read_verified
from agentic_srim.storage.artifacts import checksum, seal, write_json
from agentic_srim.storage.database import connect, get_run, now, transition
from sr_module.support import IsolatedTest


class ResultVerificationTests(IsolatedTest):
    """Construct isolated unittest cases; verify stored-result/archive linkage."""

    def checked_record(self, capability: str = "stopping_range") -> dict:
        """Accept a capability name; persist and return a sealed synthetic CHECKED run.

        Creates only test records and JSON artifacts, with no native execution.
        The SR case retains the legacy settings without a capability marker.
        """
        run_id = submit(self.request, self.settings)
        attempt_id = uuid.uuid4().hex
        archive = self.runtime / "runs" / run_id / "attempts" / attempt_id
        archive.mkdir(parents=True)
        version = "trim-v1" if capability == "trim" else "sr-table-v1"
        result = {"analysis_version": version, "values": [1, 2, 3],
                  "provenance": {"run_id": run_id, "attempt_id": attempt_id}}
        write_json(archive / "result.json", result)
        write_json(archive / "outcome.json", {"state": "CHECKED", "error": None})
        seal(archive)
        with connect(self.runtime) as connection:
            connection.execute("INSERT INTO attempts(id,run_id,ordinal,state,started_at,artifact_path,manifest_sha256) VALUES(?,?,?,?,?,?,?)",
                               (attempt_id, run_id, 1, "CHECKED", now(), str(archive.relative_to(self.runtime)), checksum(archive / "manifest.json")))
            transition(connection, run_id, "CHECKED", attempt_id)
            connection.execute("INSERT INTO results VALUES(?,?,?,?)", (run_id, attempt_id, version, json.dumps(result)))
            if capability == "trim":
                connection.execute("UPDATE runs SET settings_json=? WHERE id=?", (json.dumps({"capability": "trim"}), run_id))
        return get_run(self.runtime, run_id)

    def store_payload(self, run_id: str, payload: object) -> None:
        """Replace a test run's stored JSON payload to model corruption; return nothing."""
        with connect(self.runtime) as connection:
            connection.execute("UPDATE results SET result_json=? WHERE run_id=?", (json.dumps(payload), run_id))

    def test_valid_results_keep_public_response_shape(self) -> None:
        """Accept no inputs; verify SR/TRIM linkage without exposing internal metadata; return nothing."""
        for capability in ("stopping_range", "trim"):
            with self.subTest(capability=capability):
                record = self.checked_record(capability)
                self.assertNotIn("result_metadata", record)
                self.assertEqual(read_verified(self.runtime, record["id"]), record)
                metadata = read_metadata(self.runtime, record["id"])
                self.assertNotIn("result", metadata)
                self.assertNotIn("result_metadata", metadata)
                internal = get_run(self.runtime, record["id"], include_result_metadata=True)
                self.assertEqual(internal["result_metadata"], {
                    "attempt_id": record["attempts"][0]["id"], "analysis_version": record["result"]["analysis_version"]})

    def test_payload_provenance_cannot_skip_archive_comparison(self) -> None:
        """Accept no inputs; reject altered or absent payload linkage; return nothing."""
        for field in ("attempt_id", "run_id", "missing_attempt_id", "missing_provenance", "invalid_provenance"):
            with self.subTest(field=field):
                record = self.checked_record()
                result = record["result"]
                result["values"] = [999]
                if field == "missing_provenance":
                    result.pop("provenance")
                elif field == "invalid_provenance":
                    result["provenance"] = None
                elif field == "missing_attempt_id":
                    result["provenance"].pop("attempt_id")
                else:
                    result["provenance"][field] = "0" * 32
                self.store_payload(record["id"], result)
                for reader in (read_verified, read_metadata):
                    with self.assertRaises(ValueError):
                        reader(self.runtime, record["id"])

    def test_matching_link_does_not_hide_altered_analysis(self) -> None:
        """Accept no inputs; compare payload values even with valid provenance; return nothing."""
        record = self.checked_record()
        record["result"]["values"] = [999]
        self.store_payload(record["id"], record["result"])
        with self.assertRaisesRegex(ValueError, "archived analysis"):
            read_verified(self.runtime, record["id"])

    def test_result_link_cannot_reference_another_runs_attempt(self) -> None:
        """Accept no inputs; reject a foreign-run attempt despite consistent payload IDs; return nothing."""
        record = self.checked_record()
        other = self.checked_record()
        foreign_attempt = other["attempts"][0]["id"]
        record["result"]["provenance"]["attempt_id"] = foreign_attempt
        self.store_payload(record["id"], record["result"])
        with connect(self.runtime) as connection:
            connection.execute("UPDATE results SET attempt_id=? WHERE run_id=?", (foreign_attempt, record["id"]))
        with self.assertRaisesRegex(ValueError, "matching sealed CHECKED attempt"):
            read_verified(self.runtime, record["id"])

    def test_result_requires_seal_metadata_and_manifest(self) -> None:
        """Accept no inputs; reject missing seal hashes or manifest files; return nothing."""
        for damage in ("null_hash", "empty_hash", "missing_manifest"):
            with self.subTest(damage=damage):
                record = self.checked_record()
                attempt = record["attempts"][0]
                if damage == "missing_manifest":
                    (self.runtime / attempt["artifact_path"] / "manifest.json").unlink()
                else:
                    with connect(self.runtime) as connection:
                        connection.execute("UPDATE attempts SET manifest_sha256=? WHERE id=?",
                                           (None if damage == "null_hash" else "", attempt["id"]))
                with self.assertRaises((ValueError, OSError)):
                    read_verified(self.runtime, record["id"])

    def test_result_json_must_be_covered_by_the_seal(self) -> None:
        """Accept no inputs; reject an unmanifested analysis even when its bytes agree; return nothing."""
        record = self.checked_record()
        attempt = record["attempts"][0]
        manifest_path = self.runtime / attempt["artifact_path"] / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["artifacts"] = [item for item in manifest["artifacts"] if item["path"] != "result.json"]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with connect(self.runtime) as connection:
            connection.execute("UPDATE attempts SET manifest_sha256=? WHERE id=?", (checksum(manifest_path), attempt["id"]))
        with self.assertRaisesRegex(ValueError, "covered by its manifest"):
            read_verified(self.runtime, record["id"])

    def test_checked_run_requires_a_result(self) -> None:
        """Accept no inputs; reject CHECKED metadata after its result row is lost; return nothing."""
        record = self.checked_record()
        with connect(self.runtime) as connection:
            connection.execute("DELETE FROM results WHERE run_id=?", (record["id"],))
        with self.assertRaisesRegex(ValueError, "missing its stored result"):
            read_verified(self.runtime, record["id"])

    def test_result_requires_checked_run_and_attempt(self) -> None:
        """Accept no inputs; reject result links to unfinished or failed states; return nothing."""
        for table in ("runs", "attempts"):
            for state in ("RUNNING", "FAILED"):
                with self.subTest(table=table, state=state):
                    record = self.checked_record()
                    identity = record["id"] if table == "runs" else record["attempts"][0]["id"]
                    with connect(self.runtime) as connection:
                        connection.execute(f"UPDATE {table} SET state=? WHERE id=?", (state, identity))
                    with self.assertRaises(ValueError):
                        read_verified(self.runtime, record["id"])

    def test_analysis_version_must_agree_with_result_row(self) -> None:
        """Accept no inputs; reject mismatches in either stored version representation; return nothing."""
        for representation in ("payload", "row"):
            with self.subTest(representation=representation):
                record = self.checked_record()
                if representation == "payload":
                    record["result"]["analysis_version"] = "changed-version"
                    self.store_payload(record["id"], record["result"])
                else:
                    with connect(self.runtime) as connection:
                        connection.execute("UPDATE results SET analysis_version=? WHERE run_id=?", ("changed-version", record["id"]))
                with self.assertRaisesRegex(ValueError, "analysis version"):
                    read_verified(self.runtime, record["id"])

    def test_nonterminal_and_failed_metadata_without_results_remains_readable(self) -> None:
        """Accept no inputs; preserve inspection of ordinary incomplete/failed runs; return nothing."""
        run_id = submit(self.request, self.settings)
        for state in ("QUEUED", "RUNNING", "FAILED", "CANCELLED", "QUARANTINED"):
            with self.subTest(state=state):
                with connect(self.runtime) as connection:
                    transition(connection, run_id, state)
                record = read_metadata(self.runtime, run_id)
                self.assertEqual(record["state"], state)
                self.assertNotIn("result", record)
