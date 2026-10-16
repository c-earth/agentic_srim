"""Run queued calculations in a dedicated Python process; expose process dispatch."""

import os
from pathlib import Path
import subprocess
import sys

from agentic_srim.capabilities.stopping_range.workflow import execute
from agentic_srim.execution.jobs import cancel, read_verified
from agentic_srim.storage.database import connect, get_run, now, transition


def record_worker_failure(runtime: Path, run_id: str, detail: str) -> None:
    """Persist a dispatch failure for runtime/run_id with detail; return nothing.

    Append a lifecycle event and retain any existing run error. Only an unstarted
    QUEUED run without attempts becomes FAILED. Attempt rows and sealed artifacts
    remain untouched so explicit recovery can reconcile interrupted work. A
    CHECKED run receives a separate WORKER_WARNING event without changing its
    state or error; dispatch still verifies its result before returning success.
    """
    with connect(runtime) as connection:
        record = connection.execute("SELECT state,error FROM runs WHERE id=?", (run_id,)).fetchone()
        if record is None:
            raise ValueError(f"Unknown run: {run_id}")
        attempt = connection.execute("SELECT id FROM attempts WHERE run_id=? ORDER BY ordinal DESC LIMIT 1", (run_id,)).fetchone()
        if record["state"] == "CHECKED":
            timestamp = now()
            connection.execute("UPDATE runs SET updated_at=? WHERE id=?", (timestamp, run_id))
            connection.execute("INSERT INTO events(run_id,attempt_id,state,created_at,detail) VALUES(?,?,?,?,?)",
                               (run_id, attempt["id"] if attempt else None, "WORKER_WARNING", timestamp, detail))
            return
        state = "FAILED" if record["state"] == "QUEUED" and attempt is None else record["state"]
        error = f"{record['error']}\n{detail}" if record["error"] else detail
        transition(connection, run_id, state, attempt["id"] if attempt else None, error)


def dispatch(runtime: Path, run_id: str) -> dict:
    """Start a worker for runtime/run_id and return its verified execution record.

    Ctrl-C writes a cancellation request and waits for bounded native shutdown.
    Abnormal exits retain the exit code and last 4000 decoded stderr characters
    in event history before verification. Unfinished attempts keep their state
    for recovery and receive a run error. After a CHECKED commit, the diagnostic
    is a separate warning; a verified completed result remains successful.
    """
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        process = subprocess.Popen([sys.executable, "-m", __name__, str(runtime), run_id],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, creationflags=flags)
    except OSError as exc:
        record_worker_failure(runtime, run_id, f"Worker startup failed: {exc}")
        return read_verified(runtime, run_id)
    try:
        _, stderr = process.communicate()
    except KeyboardInterrupt:
        cancel(runtime, run_id)
        _, stderr = process.communicate(timeout=10)
    diagnostic = stderr.decode("utf-8", errors="replace")[-4000:]
    if process.returncode:
        detail = f"Worker exited with code {process.returncode}"
        record_worker_failure(runtime, run_id, f"{detail}\n{diagnostic}" if diagnostic else detail)
    elif get_run(runtime, run_id)["state"] not in ("CHECKED", "FAILED", "CANCELLED", "QUARANTINED"):
        detail = "Worker exited with code 0 without a terminal run state; execution is incomplete"
        record_worker_failure(runtime, run_id, f"{detail}\n{diagnostic}" if diagnostic else detail)
    return read_verified(runtime, run_id)


def main() -> None:
    """Read runtime/run ID from process arguments, execute once, and return nothing."""
    runtime, run_id = Path(sys.argv[1]).resolve(), sys.argv[2]
    capability = get_run(runtime, run_id)["settings"].get("capability", "stopping_range")
    if capability == "trim":
        from agentic_srim.capabilities.trim.workflow import execute as execute_trim
        execute_trim(runtime, run_id)
    elif capability == "stopping_range":
        execute(runtime, run_id)
    else:
        raise ValueError(f"Unknown queued capability: {capability}")


if __name__ == "__main__":
    main()
