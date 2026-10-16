"""Accept durable queued jobs; supervise one trusted child per runtime and seal results."""

import os
from pathlib import Path
import shutil
import sqlite3
import threading
import time
import uuid

from analysis import artifacts
from analysis.environment import compatible, inspect
from analysis.processes import locked, run
from analysis.records import AnalysisError, checksum, digest, encode, loads, safe_path, validate, write_json
from analysis.storage import now


def validate_output(directory: Path, job: dict, specification: dict) -> dict:
    """Accept output directory/job/specification; independently validate all datasets/figures; return result."""
    path = safe_path(directory, "result.json")
    if path.stat().st_size > 2097152:
        raise AnalysisError("limit_exceeded", "Result manifest exceeds 2 MiB")
    result = loads(path.read_text(encoding="utf-8"))
    if job["kind"] == "analysis":
        validate(result, specification["output_schema"], "result")
    if not isinstance(result, dict) or set(result) != {"schema_version", "summary", "tables", "figures", "limitations"} or result["schema_version"] != 1:
        raise AnalysisError("invalid_output", "Result must follow the version-1 output envelope")
    if type(result["summary"]) is not dict or type(result["tables"]) is not list or type(result["figures"]) is not list or type(result["limitations"]) is not list:
        raise AnalysisError("invalid_output", "Malformed output envelope")
    if len(encode(result["summary"]).encode("utf-8")) > 262144 or any(type(value) is not str for value in result["limitations"]):
        raise AnalysisError("invalid_output", "Unbounded summary or malformed limitations")
    names, paths, rows = set(), {"result.json"}, 0
    for table in result["tables"]:
        validate(table, artifacts.DATASET, "result table")
        if table["name"] in names or table["path"] in paths:
            raise AnalysisError("invalid_output", "Duplicated table identity/path")
        names.add(table["name"])
        paths.add(table["path"])
        rows += len(artifacts.rows(directory, table, {**job["limits"], "input_bytes": job["limits"]["output_bytes"]}))
    if rows > job["limits"]["input_rows"]:
        raise AnalysisError("limit_exceeded", "Retained table row limit exceeded")
    if len(result["figures"]) > job["limits"]["figure_files"]:
        raise AnalysisError("limit_exceeded", "Figure count exceeds accepted bound")
    for figure in result["figures"]:
        if type(figure) is not dict or set(figure) != {"path", "format", "width", "height"} or figure["format"] not in ("png", "svg"):
            raise AnalysisError("invalid_output", "Malformed figure descriptor")
        if any(type(figure[key]) is not int or not 1 <= figure[key] <= job["limits"]["figure_pixels"] for key in ("width", "height")):
            raise AnalysisError("limit_exceeded", "Figure dimensions exceed accepted bound")
        if figure["path"] in paths:
            raise AnalysisError("invalid_output", "Duplicated figure path")
        paths.add(figure["path"])
        file = safe_path(directory, figure["path"])
        if figure["format"] == "png":
            import struct
            with file.open("rb") as stream:
                header = stream.read(24)
            if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or struct.unpack(">II", header[16:24]) != (figure["width"], figure["height"]):
                raise AnalysisError("invalid_output", "PNG dimensions/signature mismatch")
            try:
                from PIL import Image
            except ImportError as exc:
                raise AnalysisError("plotting_dependency_required", "PNG validation requires the optional plotting extra") from exc
            with Image.open(file) as picture:
                picture.verify()
            with Image.open(file) as picture:
                picture.load()
        else:
            import xml.etree.ElementTree as ET
            root = ET.parse(file).getroot()
            if root.tag != "{http://www.w3.org/2000/svg}svg":
                raise AnalysisError("invalid_output", "Invalid SVG root")
            width, height = (root.get(key, "").removesuffix("px") for key in ("width", "height"))
            if tuple(map(float, (width, height))) != (figure["width"], figure["height"]):
                raise AnalysisError("invalid_output", "SVG viewport dimensions differ")
            if list(map(float, root.get("viewBox", "").split())) != [0, 0, figure["width"], figure["height"]]:
                raise AnalysisError("invalid_output", "SVG viewBox differs from bounded dimensions")
    if job["kind"] == "analysis" and job["method"]["method_id"] == "render_figures" and job["method"]["version"] == 1:
        parameters = job["request"]["parameters"]
        if result["summary"].get("requested_options") != parameters:
            raise AnalysisError("invalid_output", "Renderer did not preserve requested options")
        source_path = directory.parent / "inputs" / parameters["input"] / "result.json"
        numeric = loads(source_path.read_text(encoding="utf-8"))["summary"]
        columns = parameters["columns"] or numeric["value_columns"]
        count = len(parameters["candidates"] or numeric.get("candidates", []))
        if parameters["layout"] == "panels":
            number = 1
        elif parameters["layout"] == "overlay":
            number = len(columns) * len(parameters["curves"])
        else:
            number = len(columns) * sum((count + 1 if curve == "original" else count) if count else 1 for curve in parameters["curves"])
        expected = {f"figure_{index + 1}.{format}" for index in range(number) for format in parameters["formats"]}
        if {figure["path"] for figure in result["figures"]} != expected:
            raise AnalysisError("invalid_output", "Requested figure set is incomplete")
    actual = {item["path"] for item in artifacts.inventory(directory, job["limits"]["output_bytes"])}
    if actual != paths:
        raise AnalysisError("invalid_output", "Output files are missing or undeclared")
    return result


class Dispatcher:
    """Construct from a store; poll durable queue while preserving responsive tool reads."""

    def __init__(self, store) -> None:
        """Accept store; create stop/wake events without starting work; return nothing."""
        self.store = store
        self.stopping = threading.Event()
        self.wake = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)

    def start(self) -> None:
        """Accept no inputs; start queue polling; return nothing."""
        self.thread.start()

    def stop(self) -> None:
        """Accept no inputs; cancel an owned child and join dispatcher; return nothing."""
        self.stopping.set()
        self.wake.set()
        self.thread.join(timeout=10)
        if self.thread.is_alive():
            raise AnalysisError("recovery_required", "Dispatcher did not confirm shutdown")

    def _loop(self) -> None:
        """Accept no inputs; drain queued work under one runtime lock until stopped; return nothing."""
        while not self.stopping.is_set():
            try:
                with self.store.connection() as connection:
                    exists = connection and connection.execute("SELECT 1 FROM jobs WHERE state='QUEUED'").fetchone()
                if exists:
                    with locked(self.store.runtime):
                        while not self.stopping.is_set() and self.run_one():
                            pass
            except (OSError, ValueError, sqlite3.Error):
                pass
            self.wake.wait(0.1)
            self.wake.clear()

    def run_one(self) -> bool:
        """Accept no inputs; claim/run one queued job while caller owns lock; return whether claimed."""
        store = self.store
        with store.connection(True) as connection:
            if connection.execute("SELECT 1 FROM jobs WHERE state IN ('PREPARING','RUNNING','VERIFYING','RECOVERY_REQUIRED')").fetchone():
                return False
            row = connection.execute("SELECT id FROM jobs WHERE state='QUEUED' ORDER BY rowid LIMIT 1").fetchone()
            if not row:
                return False
            job = store.job(connection, row[0])
            attempt_id = uuid.uuid4().hex
            job.update(state="PREPARING", current_attempt=attempt_id, attempt_count=job["attempt_count"] + 1)
            attempt = {"id": attempt_id, "ordinal": job["attempt_count"], "state": "PREPARING", "started_at": now(),
                       "finished_at": None, "elapsed_seconds": 0, "owner": None, "output_sha256": None, "error": None}
            connection.execute("INSERT INTO attempts VALUES(?,?,?,?)", (attempt_id, job["id"], attempt["ordinal"], encode(attempt)))
            store.save_job(connection, job)
        start = time.monotonic()
        deadline = start + min(job["limits"]["wall_seconds"], max(0, job["limits"]["total_seconds"] - job["consumed_seconds"]))
        directory = safe_path(store.runtime, f"analyses/{job['id']}/attempts/{attempt_id}")
        state, error, manifest_hash = "FAILED", None, None
        def phase(value: str) -> None:
            """Accept preparation stage; checkpoint bounded progress for recovery/inspection; return nothing."""
            attempt["phase"] = value
            with store.connection(True) as connection:
                connection.execute("UPDATE attempts SET record=? WHERE id=?", (encode(attempt), attempt_id))
        try:
            directory.mkdir(parents=True)
            for name in ("inputs", "output", "scratch", "bundle"):
                (directory / name).mkdir()
            phase("method_validation")
            with store.connection() as connection:
                method = store._method(connection, job["method"])
                component_references = store.components(connection, method["specification"])
                if job["kind"] == "analysis" and store.eligibility(connection, job["method"], job["source_ids"])["status"] == "invalidated":
                    raise AnalysisError("method_invalidated", "Method invalidated while job was queued")
                if job["kind"] == "analysis" and any(store.eligibility(connection, component, job["source_ids"])["status"] == "invalidated" for component in component_references):
                    raise AnalysisError("method_invalidated", "Pinned component invalidated while queued")
            phase("environment_validation")
            if inspect(store.settings.python, max(0.001, deadline - time.monotonic()))["fingerprint"] != job["environment"]["fingerprint"]:
                raise AnalysisError("environment_error", "Environment changed since acceptance")
            compatible(method["specification"], job["environment"])
            bundle_source = store.method_path(job["method"])
            phase("input_snapshot")
            shutil.copytree(bundle_source, directory / "bundle", dirs_exist_ok=True)
            artifacts.verify(directory / "bundle", job["method"]["bundle_sha256"], 2097152)
            components = {}
            for component in component_references:
                target = directory / "components" / f"{component['method_id']}-{component['version']}"
                shutil.copytree(store.method_path(component), target)
                artifacts.verify(target, component["bundle_sha256"], 2097152)
                components[f"{component['method_id']}@{component['version']}"] = {"method": component, "directory": str(target)}
            inputs, input_context = {}, {}
            for name, reference in job["request"].get("inputs", {}).items():
                target = directory / "inputs" / name
                inputs[name] = artifacts.snapshot(reference, store.settings.stores, target, job["limits"])
                for dataset in inputs[name].get("datasets", []):
                    artifacts.rows(target, dataset, job["limits"])
                input_context[name] = str(target)
            context = {"schema_version": 1, "analysis_id": job["id"], "attempt_id": attempt_id,
                       "input_references": job["request"].get("inputs", {}),
                       "components": components,
                       "input_directories": input_context, "output_directory": str(directory / "output"),
                       "scratch_directory": str(directory / "scratch"), "limits": job["limits"],
                       "environment": job["environment"], "method": job["method"]}
            write_json(directory / "job.json", {"kind": job["kind"], "bundle": str(directory / "bundle"), "inputs": inputs,
                       "parameters": job["request"].get("parameters", {}), "context": context})
            phase("child_startup")
            def cancelled() -> bool:
                """Accept no inputs; return whether shutdown/cancel is durably requested."""
                with store.connection() as connection:
                    return self.stopping.is_set() or store.job(connection, job["id"])["cancel_requested"]
            def record_owner(owner: dict) -> None:
                """Accept gated process ownership; durably checkpoint before code starts; return nothing."""
                attempt["owner"] = owner
                attempt["state"] = "RUNNING"
                with store.connection(True) as connection:
                    connection.execute("UPDATE attempts SET record=? WHERE id=?", (encode(attempt), attempt_id))
                    current = store.job(connection, job["id"])
                    current["state"] = "RUNNING"
                    store.save_job(connection, current)
                write_json(directory / "owner.json", owner)
            code = run([str(store.settings.python), "-B", "-m", "analysis.child", str(directory / "job.json"), attempt_id],
                       directory, attempt_id, deadline, cancelled, record_owner, job["limits"]["log_bytes"])
            if code:
                child_error = directory / "method_error.json"
                if child_error.is_file() and child_error.stat().st_size <= 131072:
                    diagnostic = loads(child_error.read_text(encoding="utf-8"))
                    raise AnalysisError("method_failed", diagnostic["message"], {"exit_code": code, "exception": diagnostic["type"]})
                raise AnalysisError("child_failed", "Trusted local child failed", {"exit_code": code})
            if cancelled():
                raise AnalysisError("cancelled", "Cancellation requested")
            attempt["state"] = "VERIFYING"
            with store.connection(True) as connection:
                connection.execute("UPDATE attempts SET record=? WHERE id=?", (encode(attempt), attempt_id))
                current = store.job(connection, job["id"])
                current["state"] = "VERIFYING"
                store.save_job(connection, current)
            result = validate_output(directory / "output", job, method["specification"])
            if time.monotonic() >= deadline:
                raise AnalysisError("timed_out", "Preparation/output validation exceeded accepted wall time")
            artifacts.seal(directory / "output", {"owner": "analysis", "analysis_id": job["id"], "attempt_id": attempt_id,
                           "state": "CHECKED", "method": job["method"], "source_ids": job["source_ids"],
                           "datasets": result["tables"], "limitations": result["limitations"],
                           "inputs": job["request"].get("inputs", {}), "parameters": job["request"].get("parameters", {}),
                           "limits": job["limits"],
                           "environment_fingerprint": job["environment"]["fingerprint"]}, job["limits"]["output_bytes"])
            manifest_hash = checksum(directory / "output" / "manifest.json")
            if time.monotonic() >= deadline:
                raise AnalysisError("timed_out", "Output sealing exceeded the accepted wall-time budget")
            state = "CHECKED"
        except Exception as exc:
            error = {"code": getattr(exc, "code", "execution_error"), "message": str(exc), "details": getattr(exc, "details", {})}
            state = {"timed_out": "TIMED_OUT", "cancelled": "CANCELLED", "recovery_required": "RECOVERY_REQUIRED"}.get(error["code"], "FAILED")
        elapsed = time.monotonic() - start
        attempt.update(state=state, finished_at=now(), elapsed_seconds=elapsed, error=error, output_sha256=manifest_hash)
        if job["kind"] == "verification":
            report = directory / "output" / "result.json"
            if report.is_file() and report.stat().st_size <= 262144:
                try:
                    test_summary = loads(report.read_text(encoding="utf-8"))["summary"]
                    if set(test_summary) == {"tests_run", "failures", "errors", "skipped"} and all(type(value) is int and 0 <= value <= 1000000 for value in test_summary.values()):
                        attempt["verification_summary"] = test_summary
                except (ValueError, KeyError, TypeError):
                    pass
        try:
            if directory.exists():
                write_json(directory / "outcome.json", attempt)
                self._bound_failure(directory, job["limits"])
                artifacts.seal(directory, {"owner": "analysis-attempt", "analysis_id": job["id"], "attempt_id": attempt_id,
                               "state": state}, artifacts.archive_limit(job["limits"]))
                attempt["archive_sha256"] = checksum(directory / "manifest.json")
            with store.connection(True) as connection:
                current = store.job(connection, job["id"])
                current.update(state=state, error=error, consumed_seconds=current["consumed_seconds"] + elapsed)
                connection.execute("UPDATE attempts SET record=? WHERE id=?", (encode(attempt), attempt_id))
                store.save_job(connection, current)
                if job["kind"] == "verification":
                    store.event(connection, job["method"], "verification", {"analysis_id": job["id"], "attempt_id": attempt_id, "state": state,
                                "environment_fingerprint": job["environment"]["fingerprint"], "actor": job["request"]["actor"], "reason": job["request"]["reason"]})
        except Exception:
            with store.connection(True) as connection:
                current = store.job(connection, job["id"])
                current["state"] = "RECOVERY_REQUIRED"
                store.save_job(connection, current)
        return True

    def _bound_failure(self, directory: Path, limits: dict) -> None:
        """Accept attempt/limits; bound failed output retention and remove disposable scratch; return nothing."""
        scratch = safe_path(directory, "scratch")
        if scratch.exists():
            shutil.rmtree(scratch)
        output = safe_path(directory, "output")
        if not (output / "manifest.json").exists():
            retained, removed = 0, []
            for path in sorted(output.rglob("*")):
                safe_path(output, path.relative_to(output).as_posix())
                if path.is_file():
                    size = path.stat().st_size
                    if retained + size > limits["output_bytes"]:
                        removed.append({"path": path.relative_to(output).as_posix(), "size_bytes": size})
                        path.unlink()
                    else:
                        retained += size
            if removed:
                write_json(directory / "retention.json", {"removed_over_limit": removed})


def recover(store, arguments: dict) -> dict:
    """Accept store/audit; reconcile abandoned jobs under lock without restarting computation."""
    from agentic_srim.execution.processes import is_process_alive
    result = []
    with locked(store.runtime):
        with store.connection(True) as connection:
            request_hash = digest({"tool": "recover_analyses", "arguments": arguments})
            replay = connection.execute("SELECT * FROM retries WHERE key=?", (arguments["idempotency_key"],)).fetchone()
            if replay:
                if replay["fingerprint"] != request_hash:
                    raise AnalysisError("idempotency_conflict", "Recovery key conflict")
                return loads(replay["result"])
            for row in connection.execute("SELECT id FROM jobs WHERE state IN ('PREPARING','RUNNING','VERIFYING','RECOVERY_REQUIRED')").fetchall():
                job = store.job(connection, row[0])
                attempt = store.attempts(connection, job["id"])[-1]
                directory = safe_path(store.runtime, f"analyses/{job['id']}/attempts/{attempt['id']}")
                owner = attempt.get("owner")
                ambiguous = False
                if owner:
                    try:
                        ambiguous = is_process_alive(owner["pid"])
                        if os.name != "nt":
                            try:
                                os.killpg(owner["group"], 0)
                                ambiguous = True
                            except ProcessLookupError:
                                pass
                    except OSError:
                        ambiguous = True
                if ambiguous:
                    job["state"] = "RECOVERY_REQUIRED"
                    job["error"] = {"code": "ownership_ambiguous", "message": "Potential owned process remains; no process killed"}
                else:
                    outcome = directory / "outcome.json"
                    if outcome.exists():
                        recovered = loads(outcome.read_text(encoding="utf-8"))
                        if recovered.get("id") != attempt["id"]:
                            raise AnalysisError("integrity_error", "Recovered attempt identity differs")
                        attempt = recovered
                        if (directory / "manifest.json").exists():
                            archive_hash = checksum(directory / "manifest.json")
                            artifacts.verify(directory, archive_hash, artifacts.archive_limit(job["limits"]))
                            attempt["archive_sha256"] = archive_hash
                        if attempt["state"] == "CHECKED":
                            artifacts.verify(directory / "output", attempt["output_sha256"], job["limits"]["output_bytes"])
                    else:
                        attempt.update(state="FAILED", finished_at=now(), error={"code": "interrupted", "message": "Supervisor interrupted; no rerun"},
                                       elapsed_seconds=job["limits"]["wall_seconds"])
                        directory.mkdir(parents=True, exist_ok=True)
                        write_json(outcome, attempt)
                    if not (directory / "manifest.json").exists():
                        Dispatcher(store)._bound_failure(directory, job["limits"])
                        artifacts.seal(directory, {"owner": "analysis-attempt", "analysis_id": job["id"], "attempt_id": attempt["id"], "state": attempt["state"]},
                                       artifacts.archive_limit(job["limits"]))
                        attempt["archive_sha256"] = checksum(directory / "manifest.json")
                    job.update(state=attempt["state"], error=attempt["error"], consumed_seconds=min(job["limits"]["total_seconds"], job["consumed_seconds"] + attempt["elapsed_seconds"]))
                    connection.execute("UPDATE attempts SET record=? WHERE id=?", (encode(attempt), attempt["id"]))
                    if job["kind"] == "verification" and not any(event["attempt_id"] == attempt["id"] for event in store.history(connection, job["method"], "verification")):
                        store.event(connection, job["method"], "verification", {"analysis_id": job["id"], "attempt_id": attempt["id"], "state": job["state"],
                                    "environment_fingerprint": job["environment"]["fingerprint"], "actor": arguments["actor"], "reason": arguments["reason"]})
                store.save_job(connection, job)
                result.append({"analysis_id": job["id"], "state": job["state"]})
            response = {"recovered": result}
            connection.execute("INSERT INTO retries VALUES(?,?,?)", (arguments["idempotency_key"], request_hash, encode(response)))
            return response
