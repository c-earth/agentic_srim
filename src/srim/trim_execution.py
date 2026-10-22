"""Execute prepared TRIM runs with sealed provenance, popup reports and native evidence."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
import hashlib
import math
from pathlib import Path

from src.backend.config import Config
from src.backend.paths import ProjectPaths
from src.backend.records import _lock_worker, _record_time
from src.srim.metadata import JSONObject, _inventory, _read_json, _simulation_path, _write_json, finish_simulation, read_simulation, start_simulation
from src.srim.runtime import _read_regular_bytes
from src.srim.trim_native import execute_trim_process
from src.srim.trim_outputs import PROFILES, compare_trim_saved_values, parse_trim_outputs
from src.srim.trim_resolution import ResolvedTRIMInputs
from src.srim.trim_runtime import verify_trim_runtime
from src.srim.trim_storage import preserve_trim_runtime
from src.srim.trim_dialogs import assess_trim_histories


def _trim_completion(inputs: ResolvedTRIMInputs, process_result: JSONObject, parsed: JSONObject, saved_values: JSONObject, continuation: JSONObject | None, *, availability: JSONObject | None = None) -> JSONObject:
    """Classify pinned inputs/process_result/parsed/saved_values and continuation.

    continuation supplies explicit resume control or None; availability optionally
    adds a required-history check. Return complete,
    reason and checks without effects. Require clean exit, full consistent
    summary/profiles, exact achieved count and no substitutions. Malformed
    report arguments raise ValueError; native differences are reported.
    """
    if not isinstance(inputs, ResolvedTRIMInputs) or not isinstance(process_result, dict) or not isinstance(parsed, dict) or not isinstance(saved_values, dict):
        raise ValueError("Invalid TRIM completion reports")
    expected = continuation["total_ions"] if continuation is not None else inputs.effective_request["parameters"]["total_ions"]
    autosave = continuation["autosave_ions"] if continuation is not None else inputs.effective_request["parameters"]["autosave_ions"]
    checkpoint = parsed.get("checkpoint")
    checks: list[JSONObject] = []
    assertions = [("process", process_result.get("termination") == "exit" and process_result.get("exit_code") == 0 and not process_result.get("popups") and not process_result.get("monitor_errors"), "Clean owned native exit without popup or monitoring failure"), ("parsing", parsed.get("complete") is True, "Complete consistent summary and seven profiles"), ("checkpoint", checkpoint is not None, "Textual native saved state is available"), ("saved_values", not saved_values.get("differences"), "Requested values preserved at native precision")]
    if checkpoint is not None:
        assertions.extend((("counts", checkpoint["counts"]["current"] == checkpoint["counts"]["total"] == expected, f"Saved current/total must equal requested {expected}"), ("autosave", checkpoint["counts"]["autosave"] == autosave, f"Saved autosave must equal requested {autosave}")))
    if availability is not None:
        assertions.append(("histories", availability["histories"]["status"] in ("not-requested", "available", "native-unavailable"), "Requested histories available or explicitly qualified by verified native limitation"))
    for field, equal, detail in assertions:
        checks.append({"field": field, "status": "equal" if equal else "different", "detail": detail})
    failures = [item for item in checks if item["status"] == "different"]
    return {"complete": not failures, "reason": failures[0]["detail"] if failures else None, "checks": checks}


def run_trim_simulation(paths: ProjectPaths, config: Config, simulation_id: str, runtime: Path, *, timeout_seconds: float | None = 60.0, cancel: Callable[[], bool] | None = None) -> JSONObject:
    """Execute matching prepared simulation_id/runtime under paths/config ownership.

    timeout_seconds bounds native execution, or None disables its deadline.
    cancel optionally requests stopping; monitoring and cleanup remain active.
    Hold worker lease through validation, process ownership, complete stopped
    runtime preservation, parsing and terminal metadata. Return succeeded/failed
    shared metadata for expected native failures. Never retry, click or delete.
    Invalid caller/state/collision raises before launch. I/O and BaseException
    propagate after best-effort preservation/failure publication with notes.
    """
    if (timeout_seconds is not None and (type(timeout_seconds) not in (int, float) or timeout_seconds <= 0 or not math.isfinite(timeout_seconds))) or type(config.hide_trim_window) is not bool or (cancel is not None and not callable(cancel)):
        raise ValueError("Invalid TRIM execution configuration")
    _record_time(config, None)
    record = _simulation_path(paths, simulation_id)
    if not isinstance(runtime, Path) or not runtime.is_absolute() or runtime != paths.workers / runtime.parent.parent.name / "trim" / simulation_id:
        raise ValueError("Invalid TRIM runtime owner")
    with _lock_worker(paths, runtime.parent.parent):
        metadata = read_simulation(paths, simulation_id)
        if metadata["status"] != "created" or metadata["engine"]["name"] != "trim":
            raise ValueError("Only a created TRIM simulation can execute")
        if _inventory(record / "output", record):
            raise FileExistsError("TRIM output directories must be empty")
        report: JSONObject = {"schema_version": 1, "engine": "trim", "simulation_id": simulation_id, "runtime_path": runtime.relative_to(paths.root).as_posix(), "timeout_seconds": None if timeout_seconds is None else float(timeout_seconds), "stage": "preflight", "process": None, "completion": None, "failure": None}
        preserved = False
        try:
            sealed_control = _read_json(record / "input/execution/control.json")
            if sealed_control.get("schema_version") == 2 and _inventory(record / "input", record) == metadata["inputs"]:
                report["schema_version"] = 2
                report["availability"] = assess_trim_histories({}, sealed_control["dialog_policy"], [])
                report["availability"]["histories"]["status"] = "unverified"
            try:
                inputs = verify_trim_runtime(paths, simulation_id, runtime)
            except ValueError as error:
                report["failure"] = {"code": "preflight", "message": str(error)}
                preserve_trim_runtime(paths, simulation_id, runtime)
                preserved = True
                if report["schema_version"] == 2:
                    _write_json(record / "output/parsed/availability.json", report["availability"])
                _write_json(record / "output/raw/execution.json", report)
                return finish_simulation(paths, config, simulation_id, {"message": str(error), "exit_code": None})
            control = _read_json(record / "input/execution/control.json")
            policy = control.get("dialog_policy")
            if policy is not None:
                report["schema_version"] = 2
                report["availability"] = assess_trim_histories({}, policy, [])
            initial_manifest = _read_json(record / "input/execution/runtime.json")
            start_simulation(paths, config, simulation_id)
            report["stage"] = "native"
            result = execute_trim_process(runtime / "TRIM.exe", runtime, hide_window=config.hide_trim_window, timeout_seconds=timeout_seconds, stdout_path=record / "output/raw/stdout.bin", stderr_path=record / "output/raw/stderr.bin", cancel=cancel, dialog_policy=policy)
            report["process"] = result
            report["stage"] = "preserve"
            preserve_trim_runtime(paths, simulation_id, runtime)
            preserved = True
            permanent = record / "output/raw/runtime"
            names = {name.casefold() for name in ("TDATA.txt", *PROFILES)}
            files = {entry["path"]: _read_regular_bytes(permanent / entry["path"]) for entry in _inventory(permanent, permanent) if entry["path"].casefold() in names | {"srim outputs/" + name for name in names} | {"srim restore/tdata.sav", "srim restore/trimin.sav"}}
            report["stage"] = "parse"
            comparison_request = deepcopy(inputs.effective_request)
            comparison_request["parameters"]["total_ions"] = control["total_ions"]
            parsed = parse_trim_outputs(files, comparison_request)
            comparison: JSONObject = {"schema_version": 1, "engine": "trim", "checks": [], "differences": [], "unverified": [{"field": "checkpoint", "requested": None, "observed": None, "precision": "exact", "status": "unverified"}]}
            if parsed["checkpoint"] is not None:
                comparison = compare_trim_saved_values(parsed["checkpoint"], inputs, resumed=control["kind"] == "resume")
            _write_json(record / "output/parsed/results.json", parsed)
            _write_json(record / "output/parsed/saved-values.json", comparison)
            report["stage"] = "verify"
            integrity_errors: list[str] = []
            for entry in initial_manifest["files"]:
                data = _read_regular_bytes(permanent / entry["path"])
                if (hashlib.sha256(data).hexdigest(), len(data)) != (entry["sha256"], entry["size_bytes"]):
                    integrity_errors.append(entry["path"])
            availability = None
            if policy is not None:
                histories = {entry["path"]: _read_regular_bytes(permanent / entry["path"]) for entry in _inventory(permanent, permanent) if entry["path"].casefold() == "srim outputs/collison.txt"}
                availability = assess_trim_histories({**files, **histories}, policy, result.get("dialog_events", []))
                core = _trim_completion(inputs, result, parsed, comparison, control if control["kind"] == "resume" else None)
                availability["core_verified"] = core["complete"] and not integrity_errors
                if not availability["core_verified"] and availability["histories"]["status"] == "native-unavailable":
                    availability["histories"]["status"] = "unverified"
                report["availability"] = availability
                _write_json(record / "output/parsed/availability.json", availability)
            completion = _trim_completion(inputs, result, parsed, comparison, control if control["kind"] == "resume" else None, availability=availability)
            report["completion"] = completion
            code: str | None = None
            message: str | None = None
            if result["termination"] != "exit":
                code = {"popup": "popup", "timeout": "timeout", "cancelled": "cancelled", "monitor-error": "monitor", "launch-error": "launch"}.get(result["termination"], "launch")
                message = f"TRIM stopped: {result['termination']}"
            elif result["exit_code"] != 0:
                code, message = "launch", f"TRIM exited with code {result['exit_code']}"
            elif integrity_errors:
                code, message = "integrity", "Native runtime inputs changed: " + ", ".join(integrity_errors)
            elif comparison["differences"]:
                code, message = "substitution", "Native saved values differ from requested values"
            elif not parsed["complete"]:
                code, message = "parse", "Native summary/profile output is missing, invalid or inconsistent"
            elif availability is not None and availability["histories"]["status"] not in ("not-requested", "available", "native-unavailable"):
                code, message = "output-requirement", "Requested cascade histories are missing, inconsistent or unverified"
            elif not completion["complete"]:
                code, message = "incomplete", str(completion["reason"])
            report["stage"] = "finished"
            report["failure"] = {"code": code, "message": message} if code is not None else None
            _write_json(record / "output/raw/execution.json", report)
            return finish_simulation(paths, config, simulation_id, {"message": message, "exit_code": result["exit_code"]} if code is not None else None)
        except BaseException as error:
            error.add_note(f"TRIM evidence retained at runtime {runtime}; record {record}")
            try:
                if not preserved:
                    preserve_trim_runtime(paths, simulation_id, runtime)
                report["failure"] = {"code": "cancelled" if not isinstance(error, Exception) else "preservation" if report["stage"] == "preserve" else "integrity", "message": str(error) or type(error).__name__}
                if report["schema_version"] == 2 and not (record / "output/parsed/availability.json").exists():
                    _write_json(record / "output/parsed/availability.json", report["availability"])
                if not (record / "output/raw/execution.json").exists():
                    _write_json(record / "output/raw/execution.json", report)
                current = read_simulation(paths, simulation_id)
                if current["status"] in ("created", "running"):
                    finish_simulation(paths, config, simulation_id, {"message": str(error) or type(error).__name__, "exit_code": report["process"]["exit_code"] if report["process"] else None})
            except BaseException as final_error:
                error.add_note(f"TRIM finalization failed: {final_error}")
            raise


def read_trim_output_availability(paths: ProjectPaths, simulation_id: str) -> JSONObject:
    """Return verified terminal simulation_id's availability under paths.

    Check sealed output inventory without writes. Raise ValueError on wrong
    engine/state/schema or tampering, FileNotFoundError for older absent reports,
    and OSError on unsafe I/O. Return an independent JSON object.
    """
    record = _simulation_path(paths, simulation_id)
    metadata = read_simulation(paths, simulation_id)
    if metadata["engine"]["name"] != "trim" or metadata["status"] not in ("succeeded", "failed") or _inventory(record / "output", record) != metadata["outputs"]:
        raise ValueError("TRIM availability requires sealed terminal outputs")
    result = _read_json(record / "output/parsed/availability.json")
    keys = {"schema_version", "engine", "cascade_output", "core_verified", "histories", "limitations"}
    if set(result) != keys or type(result["schema_version"]) is not int or result["schema_version"] != 1 or result["engine"] != "trim" or result["cascade_output"] not in ("none", "statistics", "detailed") or type(result["core_verified"]) is not bool or not isinstance(result["histories"], dict) or set(result["histories"]) != {"path", "requested", "status", "evidence"} or result["histories"]["path"] != "SRIM Outputs/COLLISON.txt" or result["histories"]["requested"] != result["cascade_output"] or result["histories"]["status"] not in ("not-requested", "available", "native-unavailable", "missing", "inconsistent", "unverified") or not isinstance(result["histories"]["evidence"], list) or not isinstance(result["limitations"], list):
        raise ValueError("Invalid TRIM availability schema")
    for limitation in result["limitations"]:
        if not isinstance(limitation, dict) or set(limitation) != {"code", "message", "requested", "observed", "evidence"} or limitation["code"] not in ("native-kp-no-trajectories", "native-2013-full-detail-unavailable", "native-signed-energy-transfer") or not isinstance(limitation["evidence"], list):
            raise ValueError("Invalid TRIM availability limitation")
    return deepcopy(result)
