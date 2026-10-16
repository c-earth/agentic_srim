"""Accept run IDs/runtime paths; expose shared cancellation, verification, and recovery.

Operations preserve immutable artifacts and reconcile interrupted attempts without
starting simulations. Legacy SR runs remain readable without a capability marker.
"""

import json
from pathlib import Path
import shutil

from agentic_srim.execution.workspaces import locked
from agentic_srim.execution.processes import is_process_alive
from agentic_srim.storage.artifacts import checksum, seal, verify, write_json
from agentic_srim.storage.database import connect, get_run, now, transition

OUTPUT_NAME = "SR_OUTPUT.txt"


def cancellation_path(runtime: Path, run_id: str) -> Path:
    """Validate run ID input; return its cancellation marker path beneath runtime."""
    if len(run_id) != 32 or any(c not in "0123456789abcdef" for c in run_id):
        raise ValueError("Run ID must be 32 lowercase hexadecimal characters")
    return runtime / "cancellations" / run_id


def cancel(runtime: Path, run_id: str) -> dict:
    """Request cancellation of an existing run; return its current metadata.

    Marker creation is idempotent. The worker owns terminal state transitions.
    """
    record = get_run(runtime, run_id)
    marker = cancellation_path(runtime, run_id)
    if record["state"] in ("QUEUED", "RUNNING", "COLLECTED", "PARSED"):
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch(exist_ok=True)
    return record


def read_verified(runtime: Path, run_id: str) -> dict:
    """Return run data only after verifying sealed manifests and stored results.

    Inputs identify an existing run. Nonterminal runs expose metadata only.
    Failed attempts are checked as well as successful results. Stored results
    must belong to a CHECKED run and a sealed CHECKED attempt selected by the
    database relationship, with matching provenance and archived analysis.
    Internal result-row metadata is not exposed in the returned record.
    """
    record = get_run(runtime, run_id, include_result_metadata=True)
    metadata = record.pop("result_metadata", None)
    result_attempt = None
    if "result" in record:
        if record["state"] != "CHECKED" or not isinstance(metadata, dict):
            raise ValueError("Stored result requires a CHECKED run and result metadata")
        matches = [attempt for attempt in record["attempts"] if attempt["id"] == metadata["attempt_id"]]
        if len(matches) != 1 or matches[0]["state"] != "CHECKED" or not matches[0]["manifest_sha256"]:
            raise ValueError("Stored result requires a matching sealed CHECKED attempt")
        result_attempt = matches[0]
        result = record["result"]
        if not isinstance(result, dict) or not isinstance(result.get("provenance"), dict):
            raise ValueError("Stored result has invalid provenance")
        provenance = result["provenance"]
        if (provenance.get("run_id") != record["id"]
                or provenance.get("attempt_id") != result_attempt["id"]
                or result.get("analysis_version") != metadata["analysis_version"]):
            raise ValueError("Stored result provenance or analysis version differs from database metadata")
    elif record["state"] == "CHECKED":
        raise ValueError("CHECKED run is missing its stored result")
    for attempt in record["attempts"]:
        if attempt["manifest_sha256"]:
            directory = runtime / attempt["artifact_path"]
            if checksum(directory / "manifest.json") != attempt["manifest_sha256"]:
                raise ValueError("Manifest checksum mismatch")
            manifest = verify(directory)
            if result_attempt is not None and attempt["id"] == result_attempt["id"]:
                if sum(artifact["path"] == "result.json" for artifact in manifest["artifacts"]) != 1:
                    raise ValueError("Stored result requires archived analysis covered by its manifest")
                if json.loads((directory / "result.json").read_text(encoding="utf-8")) != record["result"]:
                    raise ValueError("Stored result differs from archived analysis")
    return record


def read_metadata(runtime: Path, run_id: str) -> dict:
    """Return verified metadata/history for runtime/run_id, excluding parsed output.

    Verifies archived artifacts and stored analysis through read_verified before
    returning request, settings, state, attempts, events, and hashes. Starts no
    simulator; missing runs or damaged artifacts raise an error.
    """
    record = read_verified(runtime, run_id)
    record.pop("result", None)
    return record


def recover(runtime: Path) -> list[str]:
    """Recover abandoned active attempts while holding the worker lock.

    Returns recovered run IDs. Sealed attempts are reconciled from outcome files;
    interrupted attempts preserve worker output and become FAILED, never accepted.
    Run this only after the old worker/native processes have exited.
    """
    recovered = []
    with locked(runtime) as worker:
        with connect(runtime) as connection:
            attempts = connection.execute("SELECT * FROM attempts WHERE state='RUNNING'").fetchall()
        for attempt in attempts:
            archive = runtime / attempt["artifact_path"]
            archive.mkdir(parents=True, exist_ok=True)
            process_record = archive / "native_process.json"
            if process_record.exists():
                pid = json.loads(process_record.read_text(encoding="utf-8"))["pid"]
                try:
                    alive = is_process_alive(pid)
                except OSError as exc:
                    raise RuntimeError("Cannot confirm native process exited; recovery refused") from exc
                if alive:
                    raise RuntimeError(f"Native process {pid} still exists; recovery refused")
            outcome = archive / "outcome.json"
            manifest = archive / "manifest.json"
            if not manifest.exists():
                active = worker / "active_attempt.json"
                owns_workspace = active.exists() and json.loads(active.read_text(encoding="utf-8")).get("attempt_id") == attempt["id"]
                capability = get_run(runtime, attempt["run_id"])["settings"].get("capability", "stopping_range")
                workspace = worker / "sr_module"
                if capability == "stopping_range" and owns_workspace and process_record.exists() and (workspace / OUTPUT_NAME).exists() and not (archive / OUTPUT_NAME).exists():
                    shutil.copyfile(workspace / OUTPUT_NAME, archive / OUTPUT_NAME)
                if owns_workspace and capability == "trim":
                    from agentic_srim.backends.trim.runner import collect
                    collect(worker / "trim", archive)
                if not outcome.exists():
                    write_json(outcome, {"state": "FAILED", "error": "Interrupted attempt recovered", "finished_at": now()})
                seal(archive)
            verify(archive)
            status = json.loads(outcome.read_text(encoding="utf-8"))
            state, error = status["state"], status["error"]
            with connect(runtime) as connection:
                connection.execute("UPDATE attempts SET state=?,finished_at=?,error=?,manifest_sha256=? WHERE id=?",
                                   (state, now(), error, checksum(manifest), attempt["id"]))
                transition(connection, attempt["run_id"], state, attempt["id"], error)
                if state == "CHECKED":
                    result = json.loads((archive / "result.json").read_text(encoding="utf-8"))
                    connection.execute("INSERT OR REPLACE INTO results VALUES(?,?,?,?)",
                                       (attempt["run_id"], attempt["id"], result["analysis_version"], json.dumps(result, sort_keys=True, allow_nan=False)))
            recovered.append(attempt["run_id"])
    return recovered
