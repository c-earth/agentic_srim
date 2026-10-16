"""Coordinate explicit stopping/range requests through durable single-worker runs.

Inputs are validated requests and operational settings. Outputs are run metadata
and versioned results with artifact lineage; scientific accuracy stays unvalidated.
"""

import hashlib
from pathlib import Path
import shutil
import uuid

from agentic_srim import __version__
from agentic_srim.backends.sr_module.input import OUTPUT_NAME, render
from agentic_srim.backends.sr_module.output import parse
from agentic_srim.backends.sr_module.runner import prepare, run
from agentic_srim.capabilities.stopping_range.inputs import Request
from agentic_srim.domain.specifications import canonical, digest
from agentic_srim.config import Settings
from agentic_srim.execution.workspaces import locked
from agentic_srim.execution.processes import CancelledError
from agentic_srim.execution.jobs import cancellation_path, read_verified
from agentic_srim.storage.artifacts import checksum, seal, write_json
from agentic_srim.storage.database import connect, get_run, now, transition


def submit(request: Request, settings: Settings) -> str:
    """Persist a validated request/settings pair; return its newly allocated run ID."""
    run_id = uuid.uuid4().hex
    execution = {"sr_module_directory": str(settings.sr_module_directory),
                 "timeout_seconds": settings.timeout_seconds, "max_attempts": settings.max_attempts}
    with connect(settings.runtime_directory) as connection:
        connection.execute("INSERT INTO runs(id,created_at,updated_at,state,request_json,physical_hash,settings_json) VALUES(?,?,?,?,?,?,?)",
                           (run_id, now(), now(), "QUEUED", canonical(request.to_dict()), digest(request.physical_spec()), canonical(execution)))
        transition(connection, run_id, "QUEUED")
    return run_id


def execute(runtime: Path, run_id: str) -> dict:
    """Execute a queued run in a locked workspace; return terminal run metadata.

    Called inside a dedicated Python worker process. Attempts share the run ID;
    failures retain sealed artifacts. Parser failures quarantine without retries.
    Native execution failures retry only within the explicitly configured bound.
    """
    cancellation = cancellation_path(runtime, run_id)
    with locked(runtime) as worker:
        workspace = worker / "sr_module"
        with connect(runtime) as connection:
            if connection.execute("SELECT 1 FROM attempts WHERE state='RUNNING' LIMIT 1").fetchone():
                raise RuntimeError("An interrupted attempt requires recovery before worker reuse")
        record = get_run(runtime, run_id)
        if record["state"] != "QUEUED":
            raise ValueError("Only QUEUED runs may execute")
        request = Request.from_dict(record["request"])
        settings = Settings(Path(record["settings"]["sr_module_directory"]), runtime,
                            record["settings"]["timeout_seconds"], record["settings"]["max_attempts"])
        try:
            software = prepare(settings.sr_module_directory, workspace)
        except Exception as exc:
            with connect(runtime) as connection:
                transition(connection, run_id, "FAILED", detail=str(exc))
            return get_run(runtime, run_id)
        execution_hash = digest({"physical_hash": record["physical_hash"], "software": software,
                                 "application_version": __version__, "analysis_version": "sr-table-v1",
                                 "stopping_unit": request.stopping_unit,
                                 "native_input_sha256": hashlib.sha256(render(request).encode("ascii")).hexdigest(),
                                 "timeout_seconds": settings.timeout_seconds, "max_attempts": settings.max_attempts})
        with connect(runtime) as connection:
            connection.execute("UPDATE runs SET execution_hash=? WHERE id=?", (execution_hash, run_id))
        for ordinal in range(1, settings.max_attempts + 1):
            attempt_id = uuid.uuid4().hex
            archive = runtime / "runs" / run_id / "attempts" / attempt_id
            archive.mkdir(parents=True)
            with connect(runtime) as connection:
                connection.execute("INSERT INTO attempts(id,run_id,ordinal,state,started_at,artifact_path) VALUES(?,?,?,?,?,?)",
                                   (attempt_id, run_id, ordinal, "RUNNING", now(), str(archive.relative_to(runtime))))
                transition(connection, run_id, "RUNNING", attempt_id)
            state, error, result = "FAILED", None, None
            output_cleared = False
            try:
                (workspace / OUTPUT_NAME).unlink(missing_ok=True)
                output_cleared = True
                (worker / "active_attempt.json").write_text(canonical({"attempt_id": attempt_id}), encoding="utf-8")
                payload = render(request).encode("ascii")
                (archive / "SR.IN").write_bytes(payload)
                (workspace / "SR.IN").write_bytes(payload)
                write_json(archive / "request.json", request.to_dict())
                write_json(archive / "provenance.json", {
                    "schema_version": 1, "run_id": run_id, "attempt_id": attempt_id,
                    "physical_hash": record["physical_hash"], "execution_hash": execution_hash,
                    "software_sha256": software, "source_directory": str(settings.sr_module_directory),
                    "application_version": __version__, "seed": None, "actual_ion_count": None,
                    "sampling": "deterministic SR Module energy-interval table",
                    "timeout_seconds": settings.timeout_seconds,
                })
                code = run(workspace, archive, settings.timeout_seconds, cancellation)
                write_json(archive / "process.json", {"exit_code": code})
                if code != 0:
                    raise RuntimeError(f"SR Module exited with code {code}")
                if cancellation.exists():
                    raise CancelledError("Cancellation requested")
                native = workspace / OUTPUT_NAME
                if not native.is_file():
                    raise ValueError("SR Module did not produce the expected table")
                shutil.copyfile(native, archive / OUTPUT_NAME)
                with connect(runtime) as connection:
                    transition(connection, run_id, "COLLECTED", attempt_id)
                table = parse((archive / OUTPUT_NAME).read_text(encoding="cp1252"), request)
                result = table.to_dict()
                result["provenance"] = {"run_id": run_id, "attempt_id": attempt_id,
                                        "physical_hash": record["physical_hash"], "execution_hash": execution_hash,
                                        "raw_artifact": f"runs/{run_id}/attempts/{attempt_id}/{OUTPUT_NAME}",
                                        "raw_sha256": checksum(archive / OUTPUT_NAME)}
                write_json(archive / "result.json", result)
                with connect(runtime) as connection:
                    transition(connection, run_id, "PARSED", attempt_id)
                state = "CHECKED"
            except CancelledError as exc:
                state, error = "CANCELLED", str(exc)
            except ValueError as exc:
                state, error = "QUARANTINED", str(exc)
            except Exception as exc:
                state, error = "FAILED", f"{type(exc).__name__}: {exc}"
            finally:
                if output_cleared and (workspace / OUTPUT_NAME).exists() and not (archive / OUTPUT_NAME).exists():
                    shutil.copyfile(workspace / OUTPUT_NAME, archive / OUTPUT_NAME)
                write_json(archive / "outcome.json", {"state": state, "error": error, "finished_at": now()})
                seal(archive)
                with connect(runtime) as connection:
                    connection.execute("UPDATE attempts SET state=?,finished_at=?,error=?,manifest_sha256=? WHERE id=?",
                                       (state, now(), error, checksum(archive / "manifest.json"), attempt_id))
                    transition(connection, run_id, state, attempt_id, error)
                    if state == "CHECKED":
                        connection.execute("INSERT INTO results VALUES(?,?,?,?)", (run_id, attempt_id, "sr-table-v1", canonical(result)))
            if state != "FAILED" or ordinal == settings.max_attempts:
                break
        return get_run(runtime, run_id)


def read_output(runtime: Path, run_id: str) -> dict:
    """Return the verified stopping/range table for an existing CHECKED SR run.

    Inputs identify the runtime and run. Output includes rows, units, provenance,
    format checks and scientific limitations. Rejects other capabilities and
    incomplete/failed runs; archive verification starts no simulator.
    """
    record = read_verified(runtime, run_id)
    if record["state"] != "CHECKED" or record["settings"].get("capability", "stopping_range") != "stopping_range":
        raise ValueError("Output requires a CHECKED stopping_range run")
    return record["result"]


