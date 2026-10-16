"""Coordinate validated TRIM requests through durable single-worker execution.

Inputs are explicit scientific requests and local operational settings. Outputs
are run records with checked formats, sealed native files, and normalized data;
independent scientific validation remains deferred.
"""

import hashlib
from itertools import islice
import json
from pathlib import Path
import uuid

from agentic_srim import __version__
from agentic_srim.backends.trim.input import render, render_particles
from agentic_srim.backends.trim.output import parse
from agentic_srim.backends.trim.runner import clear_outputs, collect, prepare, run
from agentic_srim.capabilities.trim.inputs import Request
from agentic_srim.config import Settings
from agentic_srim.domain.specifications import canonical, digest, integer
from agentic_srim.execution.jobs import cancellation_path, read_verified
from agentic_srim.execution.processes import CancelledError
from agentic_srim.execution.workspaces import locked
from agentic_srim.storage.artifacts import checksum, seal, write_json
from agentic_srim.storage.database import connect, get_run, now, transition


def submit(request: Request, settings: Settings) -> str:
    """Persist typed request/settings without simulation; return a new queued run ID."""
    if settings.trim_directory is None:
        raise ValueError("trim_directory must be configured for TRIM execution")
    request = Request.from_dict(request.to_dict())
    run_id = uuid.uuid4().hex
    execution = {"capability": "trim", "trim_directory": str(settings.trim_directory),
                 "timeout_seconds": settings.timeout_seconds, "max_attempts": settings.max_attempts}
    with connect(settings.runtime_directory) as connection:
        connection.execute("INSERT INTO runs(id,created_at,updated_at,state,request_json,physical_hash,settings_json) VALUES(?,?,?,?,?,?,?)",
                           (run_id, now(), now(), "QUEUED", canonical(request.to_dict()), digest(request.physical_spec()), canonical(execution)))
        transition(connection, run_id, "QUEUED")
    return run_id


def execute(runtime: Path, run_id: str) -> dict:
    """Execute one queued TRIM run under the shared worker lock; return terminal metadata.

    Retries are bounded fresh attempts under the same run. Parser failures are
    quarantined without retry. All current native artifacts and snapshots survive
    failures; stale native outputs never satisfy a new attempt.
    """
    cancellation = cancellation_path(runtime, run_id)
    with locked(runtime) as worker:
        with connect(runtime) as connection:
            if connection.execute("SELECT 1 FROM attempts WHERE state='RUNNING' LIMIT 1").fetchone():
                raise RuntimeError("An interrupted attempt requires recovery before worker reuse")
        record = get_run(runtime, run_id)
        if record["state"] != "QUEUED" or record["settings"].get("capability") != "trim":
            raise ValueError("Only queued TRIM runs may execute here")
        request = Request.from_dict(record["request"])
        execution = record["settings"]
        workspace = worker / "trim"
        try:
            source = Path(execution["trim_directory"])
            settings = Settings(source / "SR Module", runtime, execution["timeout_seconds"], execution["max_attempts"], source)
            software = prepare(source, workspace)
        except Exception as exc:
            with connect(runtime) as connection:
                transition(connection, run_id, "FAILED", detail=str(exc))
            return get_run(runtime, run_id)
        inputs = {"TRIM.IN": render(request).encode("ascii"), "TRIMAUTO": b"1\r\n\r\n"}
        if request.particles:
            inputs["TRIM.DAT"] = render_particles(request)
        hashes = {name: hashlib.sha256(value).hexdigest() for name, value in inputs.items()}
        execution_hash = digest({"physical_hash": record["physical_hash"], "software": software,
                                 "application_version": __version__, "analysis_version": "trim-v1",
                                 "inputs_sha256": hashes, "timeout_seconds": settings.timeout_seconds,
                                 "max_attempts": settings.max_attempts,
                                 "collision_records": request.outputs.collision_records,
                                 "dialog_policy": "exact-completion-and-requested-collision-records-v2"})
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
            cleared = False
            try:
                if cancellation.exists():
                    raise CancelledError("Cancellation requested")
                clear_outputs(workspace)
                cleared = True
                (worker / "active_attempt.json").write_text(canonical({"attempt_id": attempt_id}), encoding="utf-8")
                for name, value in inputs.items():
                    (workspace / name).write_bytes(value)
                    (archive / name).write_bytes(value)
                write_json(archive / "request.json", request.to_dict())
                write_json(archive / "provenance.json", {"schema_version": 1, "capability": "trim", "run_id": run_id,
                           "attempt_id": attempt_id, "physical_hash": record["physical_hash"], "execution_hash": execution_hash,
                           "source_directory": str(source), "software_sha256": software, "inputs_sha256": hashes,
                           "requested_random_seed": request.random_seed, "requested_ion_count": request.ion_count,
                           "application_version": __version__, "timeout_seconds": settings.timeout_seconds,
                           "dialog_policy": "exact-completion-and-requested-collision-records-v2",
                           "collision_records": request.outputs.collision_records})
                code = run(workspace, archive, settings.timeout_seconds, cancellation,
                           request.ion_count if request.calculation_mode >= 4 else None,
                           request.outputs.collisions if request.calculation_mode >= 6 else None,
                           request.calculation_mode, request.outputs.collision_records)
                write_json(archive / "process.json", {"exit_code": code})
                if code:
                    raise RuntimeError(f"TRIM exited with code {code}")
                if cancellation.exists():
                    raise CancelledError("Cancellation requested")
                collect(workspace, archive)
                with connect(runtime) as connection:
                    transition(connection, run_id, "COLLECTED", attempt_id)
                result = parse(archive, request)
                raw = archive / "native" / "TDATA.txt"
                result["provenance"] = {"run_id": run_id, "attempt_id": attempt_id,
                                        "physical_hash": record["physical_hash"], "execution_hash": execution_hash,
                                        "raw_artifact": raw.relative_to(runtime).as_posix(), "raw_sha256": checksum(raw)}
                write_json(archive / "result.json", result)
                with connect(runtime) as connection:
                    transition(connection, run_id, "PARSED", attempt_id)
                state = "CHECKED"
            except CancelledError as exc:
                state, error = "CANCELLED", str(exc)
            except (ValueError, FileNotFoundError) as exc:
                state, error = "QUARANTINED", str(exc)
            except Exception as exc:
                state, error = "FAILED", f"{type(exc).__name__}: {exc}"
            finally:
                if cleared:
                    try:
                        collect(workspace, archive)
                    except Exception as exc:
                        state, error = "QUARANTINED", f"Artifact collection failed: {exc}"
                write_json(archive / "outcome.json", {"state": state, "error": error, "finished_at": now()})
                seal(archive)
                with connect(runtime) as connection:
                    connection.execute("UPDATE attempts SET state=?,finished_at=?,error=?,manifest_sha256=? WHERE id=?",
                                       (state, now(), error, checksum(archive / "manifest.json"), attempt_id))
                    transition(connection, run_id, state, attempt_id, error)
                    if state == "CHECKED":
                        connection.execute("INSERT INTO results VALUES(?,?,?,?)", (run_id, attempt_id, "trim-v1", canonical(result)))
            if state != "FAILED" or ordinal == settings.max_attempts:
                break
        return get_run(runtime, run_id)


def read_output(runtime: Path, run_id: str, name: str, offset: int, limit: int) -> dict:
    """Return a bounded page of verified profile/detail records for an existing TRIM run.

    name must be a parsed native filename, never an arbitrary path. offset is a
    zero-based row index; limit is 1..1000. Manifests/results are verified first.
    """
    integer(offset, "offset", 0, 2147483647)
    integer(limit, "limit", 1, 1000)
    record = read_verified(runtime, run_id)
    if record["state"] != "CHECKED" or record["settings"].get("capability") != "trim":
        raise ValueError("Output pages require a CHECKED TRIM run")
    result = record["result"]
    if name in result["profiles"]:
        table = result["profiles"][name]
        rows = table["rows"][offset:offset + limit]
        count = len(table["rows"])
        metadata = {key: value for key, value in table.items() if key != "rows"}
    elif name in result["details"]:
        metadata = result["details"][name]
        attempt = next(a for a in record["attempts"] if a["id"] == result["provenance"]["attempt_id"])
        with (runtime / attempt["artifact_path"] / metadata["artifact"]).open(encoding="utf-8") as source:
            rows = [json.loads(line) for line in islice(source, offset, offset + limit)]
        count = metadata["records"]
    else:
        raise ValueError("Unknown parsed TRIM output name")
    return {"run_id": run_id, "name": name, "offset": offset, "total_records": count,
            "next_offset": offset + len(rows) if offset + len(rows) < count else None,
            "metadata": metadata, "rows": rows}
