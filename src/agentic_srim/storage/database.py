"""Store versioned runs, attempts, state events, and results in SQLite.

Functions accept a runtime root or a database connection; expose transactionally
persisted metadata. Scientific results remain separately linked to raw artifacts.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3


def now() -> str:
    """Accept no inputs; return the current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect(runtime: Path):
    """Open/create metadata beneath runtime; yield a transactional connection.

    Applies schema migration 1 and rejects unknown newer versions. Returns a
    context manager; commits on success and rolls back exceptions, then closes.
    """
    runtime.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(runtime / "metadata.sqlite", timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            raise ValueError(f"Unsupported database schema version: {version}")
        if version == 0:
            connection.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    state TEXT NOT NULL, request_json TEXT NOT NULL,
                    physical_hash TEXT NOT NULL, execution_hash TEXT,
                    settings_json TEXT NOT NULL, error TEXT
                );
                CREATE TABLE IF NOT EXISTS attempts (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                    ordinal INTEGER NOT NULL, state TEXT NOT NULL, started_at TEXT NOT NULL,
                    finished_at TEXT, artifact_path TEXT NOT NULL, error TEXT,
                    manifest_sha256 TEXT, UNIQUE(run_id, ordinal)
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                    attempt_id TEXT REFERENCES attempts(id), state TEXT NOT NULL,
                    created_at TEXT NOT NULL, detail TEXT
                );
                CREATE TABLE IF NOT EXISTS results (
                    run_id TEXT PRIMARY KEY REFERENCES runs(id),
                    attempt_id TEXT NOT NULL REFERENCES attempts(id),
                    analysis_version TEXT NOT NULL, result_json TEXT NOT NULL
                );
                PRAGMA user_version=1;
                COMMIT;
            """)
        with connection:
            yield connection
    finally:
        connection.close()


def transition(connection: sqlite3.Connection, run_id: str, state: str,
               attempt_id: str | None = None, detail: str | None = None) -> None:
    """Persist a run state and append its audit event; return nothing.

    Inputs identify an existing run, optional attempt, and diagnostic detail.
    Caller owns the transaction; terminal/phase values are controlled by workflow.
    """
    connection.execute("UPDATE runs SET state=?, updated_at=?, error=? WHERE id=?", (state, now(), detail, run_id))
    connection.execute("INSERT INTO events(run_id,attempt_id,state,created_at,detail) VALUES(?,?,?,?,?)",
                       (run_id, attempt_id, state, now(), detail))


def get_run(runtime: Path, run_id: str, *, include_result_metadata: bool = False) -> dict:
    """Return run/attempt/event metadata for runtime and run_id; raise if absent.

    include_result_metadata adds the result row's attempt ID and analysis version
    as result_metadata for integrity checking independently of its JSON payload.
    Worker warning events also appear in an optional warnings list, separate
    from calculation errors. Records without warnings keep their prior shape.
    One read transaction keeps all rows on the same snapshot while workers
    commit lifecycle changes. This function does not verify files.
    """
    with connect(runtime) as connection:
        connection.execute("BEGIN")
        row = connection.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise ValueError(f"Unknown run: {run_id}")
        result = dict(row)
        result["request"] = json.loads(result.pop("request_json"))
        result["settings"] = json.loads(result.pop("settings_json"))
        result["attempts"] = [dict(r) for r in connection.execute("SELECT * FROM attempts WHERE run_id=? ORDER BY ordinal", (run_id,))]
        result["events"] = [dict(r) for r in connection.execute("SELECT * FROM events WHERE run_id=? ORDER BY id", (run_id,))]
        warnings = [event["detail"] for event in result["events"] if event["state"] == "WORKER_WARNING"]
        if warnings:
            result["warnings"] = warnings
        stored = connection.execute("SELECT attempt_id,analysis_version,result_json FROM results WHERE run_id=?", (run_id,)).fetchone()
        if stored:
            result["result"] = json.loads(stored["result_json"])
            if include_result_metadata:
                result["result_metadata"] = {"attempt_id": stored["attempt_id"], "analysis_version": stored["analysis_version"]}
        return result
