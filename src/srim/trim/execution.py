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
from src.srim.common.types import JSONObject
from src.srim.common.files import _inventory, _read_json, _write_json
from src.srim.common.records import _simulation_path, begin_execution_evidence, finish_simulation, read_simulation, record_execution_stopped, start_simulation
from src.srim.common.files import _read_regular_bytes
from src.srim.trim.native import execute_trim_process
from src.srim.trim.outputs import PROFILES, compare_trim_saved_values, parse_trim_outputs
from src.srim.trim.results import parse_trim_checkpoint_results
from src.srim.trim.models import ResolvedTRIMInputs
from src.srim.trim.runtime import verify_trim_runtime
from src.srim.trim.storage import preserve_trim_runtime
from src.srim.trim.dialogs import assess_trim_histories, classify_trim_dialog, initial_trim_dialog_state
from src.srim.trim.config import assess_trim_config_change


def _trim_completion(inputs: ResolvedTRIMInputs, process_result: JSONObject, parsed: JSONObject, saved_values: JSONObject, continuation: JSONObject | None, *, availability: JSONObject | None = None) -> JSONObject:
    """Classify pinned inputs/process_result/parsed/saved_values and continuation.

    continuation supplies explicit resume control or None; availability optionally
    adds a required-history check. Return complete,
    reason and checks without effects. Require clean exit or verified checkpoint
    stop, complete selected native/recovered profiles, exact count and no substitutions. Malformed
    report arguments raise ValueError; native differences are reported.
    """
    if not isinstance(inputs, ResolvedTRIMInputs) or not isinstance(process_result, dict) or not isinstance(parsed, dict) or not isinstance(saved_values, dict):
        raise ValueError("Invalid TRIM completion reports")
    expected = continuation["total_ions"] if continuation is not None else inputs.effective_request["parameters"]["total_ions"]
    autosave = continuation["autosave_ions"] if continuation is not None else inputs.effective_request["parameters"]["autosave_ions"]
    checkpoint = parsed.get("checkpoint")
    checks: list[JSONObject] = []
    assertions = [("process", process_result.get("termination") == "exit" and process_result.get("exit_code") == 0 and not process_result.get("popups") and not process_result.get("monitor_errors"), "Clean owned native exit without popup or monitoring failure"), ("parsing", parsed.get("complete") is True, "Complete consistent summary and seven profiles"), ("checkpoint", checkpoint is not None, "Textual native saved state is available"), ("saved_values", not saved_values.get("differences"), "Requested values preserved at native precision")]
    if continuation is not None:
        recovery = parsed.get("recovery")
        checkpoint = recovery["checkpoint"] if isinstance(recovery, dict) else None
        stop = process_result.get("checkpoint_stop")
        clean_exit = process_result.get("termination") == "exit" and process_result.get("exit_code") == 0
        stopped = process_result.get("termination") == "checkpoint-stop" and isinstance(stop, dict) and set(stop) == {"origin", "completion_observed", "completion_prompt", "save_verified", "stable_seconds", "sources", "job_empty"} and stop.get("completion_observed") is True and stop.get("job_empty") is True and type(stop.get("stable_seconds")) in (int, float) and math.isfinite(stop["stable_seconds"]) and stop["stable_seconds"] >= 1.0 and recovery is not None and stop.get("sources") == recovery["provenance"]["sources"]
        process_ok = (clean_exit or stopped) and not process_result.get("popups") and not process_result.get("monitor_errors") and not process_result.get("checkpoint_failure")
        sequence_ok = True
        if continuation.get("dialog_policy") is not None:
            events = process_result.get("dialog_events", [])
            state = process_result.get("dialog_state", {})
            verified = state.get("pending_sequence") is None and state.get("stage") != "failed" and all(event.get("queued") is True and event.get("transition") == "verified" and event.get("error") is None for event in events)
            policy = continuation["dialog_policy"]
            prompt_ok = False
            if stopped and isinstance(stop.get("completion_prompt"), dict):
                try:
                    prompt_state = {**initial_trim_dialog_state(policy), "stage": "checkpoint-check"}
                    prompt_ok = classify_trim_dialog(stop["completion_prompt"], policy, prompt_state)["decision"] == "inspect-checkpoint"
                except (ValueError, KeyError, TypeError):
                    prompt_ok = False
            completions = [i for i, event in enumerate(events) if event.get("stage_before") == "checkpoint-save-required" and event.get("stage_after") == "resume-save" and event.get("control_id") == 6 and event.get("response") == "Yes" and event.get("window") == (stop or {}).get("completion_prompt")]
            saves = [i for i, event in enumerate(events) if event.get("stage_before") == "resume-save" and event.get("stage_after") == "checkpoint-saved" and event.get("control_id") == 3]
            completion_commands = [event for event in events if event.get("window", {}).get("title") in ("End of calculation", "End of TRIM.DAT calculation", "Form1") or event.get("stage_after") in ("resume-save", "resume-event-save", "checkpoint-saved")]
            direct = stopped and prompt_ok and stop["origin"] == "completed-checkpoint" and stop["save_verified"] is False and state.get("stage") == "checkpoint-check" and not completion_commands
            explicit = stopped and prompt_ok and stop["origin"] == "explicit-save" and stop["save_verified"] is True and policy["input_mode"] in (1, 2, 3) and len(completions) == len(saves) == 1 and completions[0] < saves[0] and state.get("stage") == "checkpoint-saved"
            if explicit:
                save_state = {**initial_trim_dialog_state(policy), "stage": "resume-save", "checkpoint_fallback_attempted": True}
                classified = classify_trim_dialog(events[saves[0]].get("window", {}), policy, save_state)
                explicit = classified["decision"] == "respond" and classified["command"] == {key: events[saves[0]].get(key) for key in ("action", "target_handle", "control_id", "response")}
                completion_state = {**save_state, "stage": "checkpoint-save-required"}
                classified = classify_trim_dialog(events[completions[0]].get("window", {}), policy, completion_state)
                explicit = explicit and classified["decision"] == "respond" and classified["command"] == {key: events[completions[0]].get(key) for key in ("action", "target_handle", "control_id", "response")}
            sequence_ok = verified and (clean_exit and stop is None or direct or explicit)
        elif stopped:
            sequence_ok = False
        assertions = [("process", process_ok, "Clean owned exit or verified checkpoint stop without popup or monitoring failure"), ("save_sequence", sequence_ok, "Automatic continuation requires verified checkpoint-first or ordinary save evidence"), ("parsing", parsed.get("schema_version") == 2 and parsed.get("result_source") == "checkpoint-recovery" and parsed.get("complete") is True and recovery is not None, "Complete structurally validated recovered profiles"), ("checkpoint", checkpoint is not None, "Recovered checkpoint is available"), ("saved_values", not saved_values.get("differences"), "Requested values preserved at saved precision")]
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
    propagate after independent best-effort preservation/failure publication
    with notes. Explicitly unresolved process ownership leaves metadata running
    and skips runtime preservation; diagnostics and exception notes flag it.
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
        execution: JSONObject | None = None
        native_entered = False
        stop_attempted = False
        try:
            sealed_control = _read_json(record / "input/execution/control.json")
            if sealed_control.get("schema_version") == 2 and _inventory(record / "input", record) == metadata["inputs"]:
                report["schema_version"] = 2
                report["availability"] = assess_trim_histories({}, sealed_control["dialog_policy"], [])
                report["availability"]["histories"]["status"] = "unverified"
            if sealed_control.get("kind") == "resume":
                report.update({"schema_version": 4, "result_source": "checkpoint-recovery", "checkpoint_verification": None})
            try:
                inputs = verify_trim_runtime(paths, simulation_id, runtime)
            except ValueError as error:
                report["failure"] = {"code": "preflight", "message": str(error)}
                preserve_trim_runtime(paths, simulation_id, runtime)
                preserved = True
                if "availability" in report:
                    _write_json(record / "output/parsed/availability.json", report["availability"])
                _write_json(record / "output/raw/execution.json", report)
                return finish_simulation(paths, config, simulation_id, {"message": str(error), "exit_code": None})
            control = _read_json(record / "input/execution/control.json")
            policy = control.get("dialog_policy")
            if policy is not None:
                report["schema_version"] = 4 if control["kind"] == "resume" else 2
                report["availability"] = assess_trim_histories({}, policy, [])
            initial_manifest = _read_json(record / "input/execution/runtime.json")
            execution = begin_execution_evidence(paths, config, simulation_id, runtime.parent.parent.name)
            start_simulation(paths, config, simulation_id)
            report["stage"] = "native"
            native_entered = True
            result = execute_trim_process(runtime / "TRIM.exe", runtime, hide_window=config.hide_trim_window, timeout_seconds=timeout_seconds, stdout_path=record / "output/raw/stdout.bin", stderr_path=record / "output/raw/stderr.bin", cancel=cancel, dialog_policy=policy)
            report["process"] = result
            stop_attempted = True
            record_execution_stopped(paths, config, simulation_id, execution["execution_id"], reason="timeout" if result["termination"] == "timeout" else "exit" if result["termination"] == "exit" else "controller-stopped", exit_code=result["exit_code"])
            report["stage"] = "preserve"
            preserve_trim_runtime(paths, simulation_id, runtime)
            preserved = True
            permanent = record / "output/raw/runtime"
            names = {name.casefold() for name in ("TDATA.txt", *PROFILES)}
            files = {entry["path"]: _read_regular_bytes(permanent / entry["path"]) for entry in _inventory(permanent, permanent) if entry["path"].casefold() in names | {"srim outputs/" + name for name in names} or entry["path"].casefold().startswith("srim restore/")}
            report["stage"] = "parse"
            comparison_request = deepcopy(inputs.effective_request)
            comparison_request["parameters"]["total_ions"] = control["total_ions"]
            parsed = parse_trim_checkpoint_results(files) if control["kind"] == "resume" else parse_trim_outputs(files, comparison_request)
            comparison: JSONObject = {"schema_version": 1, "engine": "trim", "checks": [], "differences": [], "unverified": [{"field": "checkpoint", "requested": None, "observed": None, "precision": "exact", "status": "unverified"}]}
            checkpoint = parsed["recovery"]["checkpoint"] if control["kind"] == "resume" and parsed["recovery"] is not None else parsed["native"]["checkpoint"] if control["kind"] == "resume" else parsed["checkpoint"]
            if checkpoint is not None:
                comparison = compare_trim_saved_values(checkpoint, inputs, resumed=control["kind"] == "resume")
            _write_json(record / "output/parsed/results.json", parsed)
            _write_json(record / "output/parsed/saved-values.json", comparison)
            report["stage"] = "verify"
            integrity_errors: list[str] = []
            config_assessment = None
            if control["kind"] == "resume":
                original_config = _read_regular_bytes(record / "input/installation/Data/TRIM.cfg")
                config_assessment = assess_trim_config_change(original_config, _read_regular_bytes(permanent / "Data/TRIM.cfg"), engine_sha256=hashlib.sha256(dict(inputs.engine_files)["TRIM.exe"]).hexdigest())
                baseline = next(entry for entry in initial_manifest["files"] if entry["path"] == "Data/TRIM.cfg")
                if (config_assessment["before"]["sha256"], config_assessment["before"]["size_bytes"]) != (baseline["sha256"], baseline["size_bytes"]):
                    config_assessment["status"] = "different"
                    config_assessment["reason"] = "Sealed config differs from initial runtime manifest"
                    integrity_errors.append("Data/TRIM.cfg baseline differs from initial manifest")
                _write_json(record / "output/parsed/config-integrity.json", config_assessment)
                sources = [{"path": name.split("/", 1)[1], "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)} for name, data in sorted(files.items()) if name.casefold().startswith("srim restore/")]
                stop = result.get("checkpoint_stop")
                live_match = stop.get("sources") == sources if isinstance(stop, dict) else None
                report["checkpoint_verification"] = {"sources": sources, "live_sources_match": live_match, "atomicity": "not established for binary native arrays"}
                if live_match is False:
                    integrity_errors.append("SRIM Restore changed after live checkpoint verification")
            for entry in initial_manifest["files"]:
                data = _read_regular_bytes(permanent / entry["path"])
                if (hashlib.sha256(data).hexdigest(), len(data)) != (entry["sha256"], entry["size_bytes"]):
                    if entry["path"] != "Data/TRIM.cfg" or config_assessment is None or config_assessment["status"] != "qualified":
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
            if result.get("monitor_errors") and result["termination"] != "launch-error":
                code, message = "monitor", result["checkpoint_failure"]["message"] if result.get("checkpoint_failure") else "TRIM monitoring failed"
            elif result.get("popups"):
                code, message = "popup", "TRIM stopped with an unexpected popup"
            elif result["termination"] == "checkpoint-unavailable":
                code, message = "checkpoint-required", result["checkpoint_failure"]["message"]
            elif result["termination"] not in (("exit", "checkpoint-stop") if control["kind"] == "resume" else ("exit",)):
                code = {"popup": "popup", "timeout": "timeout", "cancelled": "cancelled", "monitor-error": "monitor", "launch-error": "launch"}.get(result["termination"], "launch")
                message = f"TRIM stopped: {result['termination']}"
            elif result["termination"] == "exit" and result["exit_code"] != 0:
                code, message = "launch", f"TRIM exited with code {result['exit_code']}"
            elif integrity_errors:
                code, message = "integrity", "Native runtime inputs changed: " + ", ".join(integrity_errors)
            elif comparison["differences"]:
                code, message = "substitution", "Native saved values differ from requested values"
            elif not parsed["complete"]:
                code, message = ("checkpoint-recovery", "Checkpoint recovery is missing, invalid or inconsistent") if control["kind"] == "resume" else ("parse", "Native summary/profile output is missing, invalid or inconsistent")
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
            stopped = getattr(error, "trim_job_stopped", True)
            if stopped and native_entered and not stop_attempted and execution is not None:
                try:
                    record_execution_stopped(paths, config, simulation_id, execution["execution_id"], reason="controller-stopped", exit_code=None)
                except BaseException as evidence_error:
                    error.add_note(f"TRIM stopped evidence publication failed: {evidence_error}")
            report["failure"] = {"code": "monitor" if not stopped else "cancelled" if not isinstance(error, Exception) else "preservation" if report["stage"] == "preserve" else "integrity", "message": str(error) or type(error).__name__}
            if stopped:
                try:
                    if not preserved:
                        preserve_trim_runtime(paths, simulation_id, runtime)
                except BaseException as preservation_error:
                    error.add_note(f"TRIM runtime preservation failed: {preservation_error}")
            else:
                error.add_note(f"TRIM process ownership unresolved; running record {record} requires recovery before finalization")
            try:
                if "availability" in report and not (record / "output/parsed/availability.json").exists():
                    _write_json(record / "output/parsed/availability.json", report["availability"])
            except BaseException as availability_error:
                error.add_note(f"TRIM availability publication failed: {availability_error}")
            try:
                if not (record / "output/raw/execution.json").exists():
                    _write_json(record / "output/raw/execution.json", report)
            except BaseException as diagnostic_error:
                error.add_note(f"TRIM execution diagnostics failed: {diagnostic_error}")
            if stopped:
                try:
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
