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


def _trim_completion(inputs: ResolvedTRIMInputs, process_result: JSONObject, parsed: JSONObject, saved_values: JSONObject, continuation: JSONObject | None) -> JSONObject:
    """Classify pinned inputs/process_result/parsed/saved_values and continuation.

    continuation supplies explicit resume control or None. Return complete,
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
    for field, equal, detail in assertions:
        checks.append({"field": field, "status": "equal" if equal else "different", "detail": detail})
    failures = [item for item in checks if item["status"] == "different"]
    return {"complete": not failures, "reason": failures[0]["detail"] if failures else None, "checks": checks}


def run_trim_simulation(paths: ProjectPaths, config: Config, simulation_id: str, runtime: Path, *, timeout_seconds: float = 60.0, cancel: Callable[[], bool] | None = None) -> JSONObject:
    """Execute matching prepared simulation_id/runtime under paths/config ownership.

    timeout_seconds bounds native execution; cancel optionally requests stopping.
    Hold worker lease through validation, process ownership, complete stopped
    runtime preservation, parsing and terminal metadata. Return succeeded/failed
    shared metadata for expected native failures. Never retry, click or delete.
    Invalid caller/state/collision raises before launch. I/O and BaseException
    propagate after best-effort preservation/failure publication with notes.
    """
    if type(timeout_seconds) not in (int, float) or timeout_seconds <= 0 or not math.isfinite(timeout_seconds) or type(config.hide_trim_window) is not bool or (cancel is not None and not callable(cancel)):
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
        report: JSONObject = {"schema_version": 1, "engine": "trim", "simulation_id": simulation_id, "runtime_path": runtime.relative_to(paths.root).as_posix(), "timeout_seconds": float(timeout_seconds), "stage": "preflight", "process": None, "completion": None, "failure": None}
        preserved = False
        try:
            try:
                inputs = verify_trim_runtime(paths, simulation_id, runtime)
            except ValueError as error:
                report["failure"] = {"code": "preflight", "message": str(error)}
                preserve_trim_runtime(paths, simulation_id, runtime)
                preserved = True
                _write_json(record / "output/raw/execution.json", report)
                return finish_simulation(paths, config, simulation_id, {"message": str(error), "exit_code": None})
            control = _read_json(record / "input/execution/control.json")
            initial_manifest = _read_json(record / "input/execution/runtime.json")
            start_simulation(paths, config, simulation_id)
            report["stage"] = "native"
            result = execute_trim_process(runtime / "TRIM.exe", runtime, hide_window=config.hide_trim_window, timeout_seconds=timeout_seconds, stdout_path=record / "output/raw/stdout.bin", stderr_path=record / "output/raw/stderr.bin", cancel=cancel)
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
            completion = _trim_completion(inputs, result, parsed, comparison, control if control["kind"] == "resume" else None)
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
                if not (record / "output/raw/execution.json").exists():
                    _write_json(record / "output/raw/execution.json", report)
                current = read_simulation(paths, simulation_id)
                if current["status"] in ("created", "running"):
                    finish_simulation(paths, config, simulation_id, {"message": str(error) or type(error).__name__, "exit_code": report["process"]["exit_code"] if report["process"] else None})
            except BaseException as final_error:
                error.add_note(f"TRIM finalization failed: {final_error}")
            raise
