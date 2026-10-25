"""Read sealed SR/TRIM diagnostics without execution, replay, or cause inference."""

from __future__ import annotations

import base64
from copy import deepcopy
import hashlib

from src.mcp.common.contracts import RUN_DIAGNOSTICS_SCHEMA, ServerContext, load_json, validate_document
from src.mcp.common.files import read_verified_chunk, safe_path
from src.srim.common.files import _read_regular_bytes
from src.srim.common.records import read_simulation
from src.srim.common.types import JSONObject


_PATHS = ("output/raw/execution.json", "output/parsed/result.json", "output/parsed/results.json", "output/parsed/saved-values.json", "output/parsed/config-integrity.json", "output/parsed/availability.json")


def _read_diagnostic_file(context: ServerContext, simulation_id: str, metadata: JSONObject, relative_path: str) -> JSONObject:
    """Read one inventory-pinned diagnostic JSON for context, identity and metadata.

    Return reference/document/issue, converting per-file read, format and integrity
    errors into issues. Read no unlisted contents and write nothing. Caller must
    recheck metadata stability. Invalid record-level arguments may propagate.
    """
    item = next((item for item in metadata["outputs"] if item["path"] == relative_path), None)
    if item is None:
        return {"reference": None, "document": None, "issue": {"path": relative_path, "code": "missing", "message": "No sealed inventory entry for diagnostic file"}}
    reference = {"kind": "simulation", "id": simulation_id, "revision": None, "path": (context.paths.simulation_memory / simulation_id / relative_path).relative_to(context.paths.root).as_posix(), "sha256": item["sha256"], "size_bytes": item["size_bytes"]}
    try:
        if item["size_bytes"] > context.limits["max_result_bytes"]:
            raise ValueError("too-large: diagnostic exceeds configured result limit")
        target = safe_path(context.paths.simulation_memory, f"{simulation_id}/{relative_path}")
        chunk = read_verified_chunk(target, item["sha256"], item["size_bytes"], offset=0, length=max(1, item["size_bytes"]))
        document = load_json(base64.b64decode(chunk["content"]))
        versions = (1, 2, 3, 4) if relative_path == "output/raw/execution.json" and metadata["engine"]["name"] == "trim" else (1, 2) if relative_path == "output/parsed/results.json" else (1,)
        if type(document.get("schema_version")) is not int or document["schema_version"] not in versions:
            return {"reference": reference, "document": None, "issue": {"path": relative_path, "code": "unsupported", "message": "Unsupported or missing diagnostic schema version"}}
        if document.get("simulation_id", simulation_id) != simulation_id or document.get("engine", metadata["engine"]["name"]) != metadata["engine"]["name"]:
            return {"reference": reference, "document": None, "issue": {"path": relative_path, "code": "integrity", "message": "Diagnostic identity or engine does not match its run"}}
        required = {"output/parsed/result.json": {"rows": list}, "output/parsed/results.json": {"complete": bool, "issues": list}, "output/parsed/saved-values.json": {"differences": list}, "output/parsed/config-integrity.json": {"status": str}, "output/parsed/availability.json": {"histories": dict}}.get(relative_path, {"stage": str})
        if any(key not in document or type(document[key]) is not expected for key, expected in required.items()):
            return {"reference": reference, "document": None, "issue": {"path": relative_path, "code": "malformed", "message": "Diagnostic report lacks required typed fields"}}
        if relative_path == "output/raw/execution.json":
            if metadata["engine"]["name"] == "sr":
                valid = {"message", "exit_code", "timed_out"} <= document.keys() and (document["message"] is None or isinstance(document["message"], str)) and (document["exit_code"] is None or type(document["exit_code"]) is int) and type(document["timed_out"]) is bool
            else:
                valid = all(key in document and (document[key] is None or isinstance(document[key], dict)) for key in ("process", "completion", "failure"))
                process = document.get("process")
                if isinstance(process, dict):
                    valid = valid and isinstance(process.get("termination"), str) and (process.get("exit_code") is None or type(process["exit_code"]) is int) and all(isinstance(process.get(key, []), list) for key in ("monitor_errors", "popups"))
            if not valid:
                return {"reference": reference, "document": None, "issue": {"path": relative_path, "code": "malformed", "message": "Malformed execution diagnostic structure"}}
        return {"reference": reference, "document": document, "issue": None}
    except (ValueError, OSError) as error:
        code = "too-large" if str(error).startswith("too-large:") else "integrity" if str(error).startswith("integrity:") else "unreadable"
        return {"reference": None, "document": None, "issue": {"path": relative_path, "code": code, "message": (str(error) if len(str(error)) <= 512 else str(error)[:497] + "... [truncated]")}}


def _collect_sr_observations(documents: JSONObject) -> JSONObject:
    """Extract structured SR findings from verified document wrappers without I/O.

    Return observations and unresolved gaps; do not classify by message phrases.
    Malformed/unsupported documents produce explicit gaps, never guessed causes.
    """
    observations: list[JSONObject] = []
    unresolved: list[str] = []
    wrapper = documents.get("output/raw/execution.json", {})
    report = wrapper.get("document")
    if not isinstance(report, dict):
        return {"observations": [], "unresolved": ["Execution diagnostics are unavailable; failure causes are unresolved."]}
    if report.get("schema_version") != 1 or not {"stage", "message", "exit_code", "timed_out"} <= report.keys() or not isinstance(report["stage"], str) or type(report["timed_out"]) is not bool or report["message"] is not None and not isinstance(report["message"], str) or report["exit_code"] is not None and type(report["exit_code"]) is not int:
        return {"observations": [], "unresolved": ["Unsupported or malformed SR execution report; failure causes are unresolved."]}
    stage = report["stage"]
    category = {"preflight": "input-validation", "launch": "native", "exit": "native", "timeout": "timeout", "parse": "parser", "output": "output-requirement", "preserve": "preservation", "integrity": "input-integrity"}.get(stage, "unknown")
    if report["message"] is not None:
        observations.append({"category": category, "code": stage[:512], "message": report["message"] if len(report["message"]) <= 512 else report["message"][:497] + "... [truncated]", "evidence": [{"file": wrapper["reference"], "json_pointer": "/message"}], "cause_status": "unresolved" if category == "unknown" else "recorded"})
        unresolved.append("The execution report records a failure stage; its underlying cause is not independently established.")
    if report["exit_code"] is not None:
        observations.append({"category": "native", "code": "exit", "message": f"Recorded native exit code: {report['exit_code']}; this does not establish scientific validity.", "evidence": [{"file": wrapper["reference"], "json_pointer": "/exit_code"}], "cause_status": "recorded"})
    if report["timed_out"]:
        observations.append({"category": "timeout", "code": "timeout", "message": "The execution report records a timeout.", "evidence": [{"file": wrapper["reference"], "json_pointer": "/timed_out"}], "cause_status": "recorded"})
    return {"observations": observations, "unresolved": unresolved}


def _collect_trim_observations(documents: JSONObject) -> JSONObject:
    """Extract independent TRIM findings from verified wrappers without effects.

    Return observations and explicit unresolved gaps for unknown/malformed formats.
    Use structured codes, process values and checks, never diagnostic wording.
    """
    observations: list[JSONObject] = []
    unresolved: list[str] = []

    def emit(path: str, pointer: str, category: str, code: str | None, message: str, *, known: bool = True) -> None:
        """Append a bounded observation from path/pointer/category/code/message.

        known controls producer attribution; return None and mutate only the local
        observations list. The outer reader supplies verified file references.
        """
        observations.append({"category": category, "code": None if code is None else code[:512], "message": message if len(message) <= 512 else message[:497] + "... [truncated]", "evidence": [{"file": documents[path]["reference"], "json_pointer": pointer}], "cause_status": "recorded" if known else "unresolved"})

    categories = {"monitor": "monitoring", "popup": "native", "launch": "native", "preflight": "input-validation", "timeout": "timeout", "cancelled": "cancellation", "integrity": "input-integrity", "substitution": "saved-value-mismatch", "parse": "parser", "checkpoint-recovery": "parser", "preservation": "preservation", "output-requirement": "output-requirement", "checkpoint-required": "output-requirement", "incomplete": "output-requirement"}
    for path, wrapper in documents.items():
        report = wrapper.get("document")
        if report is None:
            continue
        if not isinstance(report, dict) or type(report.get("schema_version")) is not int or report["schema_version"] not in ((1, 2, 3, 4) if path.endswith("raw/execution.json") else (1, 2)):
            unresolved.append(f"Unsupported or malformed diagnostic report: {path}")
            continue
        if path.endswith("raw/execution.json"):
            if not {"stage", "process", "completion", "failure"} <= report.keys():
                unresolved.append(f"Malformed execution report: {path}")
                continue
            failure = report.get("failure")
            if failure is not None:
                if isinstance(failure, dict) and isinstance(failure.get("code"), str) and isinstance(failure.get("message"), str):
                    code = failure["code"]
                    emit(path, "/failure", categories.get(code, "unknown"), code, failure["message"], known=code in categories)
                    unresolved.append("The primary failure code is producer-attributed; the underlying cause is not independently established.")
                else:
                    unresolved.append("Malformed TRIM primary failure details.")
            process = report.get("process")
            if isinstance(process, dict):
                termination = process.get("termination")
                if isinstance(termination, str):
                    category = {"exit": "native", "checkpoint-stop": "native", "popup": "native", "launch-error": "native", "monitor-error": "monitoring", "timeout": "timeout", "cancelled": "cancellation", "checkpoint-unavailable": "output-requirement"}.get(termination, "unknown")
                    exit_code = process.get("exit_code")
                    emit(path, "/process/termination", category, termination, f"Recorded termination: {termination}; exit code: {exit_code}. This does not establish scientific validity.", known=category != "unknown")
                    if category == "unknown":
                        unresolved.append("Unknown native termination classification.")
                else:
                    unresolved.append("Missing or malformed native termination details.")
                for key, category in (("monitor_errors", "monitoring"), ("popups", "native")):
                    entries = process.get(key, [])
                    if not isinstance(entries, list):
                        unresolved.append(f"Malformed process {key}.")
                        continue
                    for index, entry in enumerate(entries):
                        emit(path, f"/process/{key}/{index}", category, key, str(entry))
                if process.get("checkpoint_failure") is not None:
                    emit(path, "/process/checkpoint_failure", "output-requirement", "checkpoint", str(process["checkpoint_failure"]))
            elif process is not None:
                unresolved.append("Malformed native process report.")
            completion = report.get("completion")
            if isinstance(completion, dict):
                checks = completion.get("checks", [])
                if not isinstance(checks, list):
                    unresolved.append("Malformed completion checks.")
                    continue
                for index, check in enumerate(checks):
                    if not isinstance(check, dict):
                        unresolved.append("Malformed completion check.")
                    elif check.get("status") == "different":
                        field = check.get("field")
                        category = {"parsing": "parser", "saved_values": "saved-value-mismatch"}.get(str(field), "output-requirement")
                        emit(path, f"/completion/checks/{index}", category, str(field), str(check.get("detail", "Recorded completion check differs")))
            elif completion is not None:
                unresolved.append("Malformed completion report.")
        elif path.endswith("results.json"):
            if type(report.get("complete")) is not bool or not isinstance(report.get("issues"), list):
                unresolved.append(f"Malformed parsed result report: {path}")
                continue
            emit(path, "/complete", "parser", "completeness", f"Recorded parsed result completeness: {report['complete']}.")
            for index, issue in enumerate(report["issues"]):
                if isinstance(issue, dict) and isinstance(issue.get("message"), str):
                    emit(path, f"/issues/{index}", "parser", str(issue.get("code", "issue")), issue["message"])
                else:
                    unresolved.append("Malformed parser issue.")
            native = report.get("native")
            if isinstance(native, dict) and isinstance(native.get("issues"), list):
                for index, issue in enumerate(native["issues"]):
                    if isinstance(issue, dict) and isinstance(issue.get("message"), str):
                        emit(path, f"/native/issues/{index}", "parser", str(issue.get("code", "issue")), "Native-text diagnostic retained alongside selected checkpoint recovery: " + issue["message"])
                    else:
                        unresolved.append("Malformed nested native parser issue.")
            if report["complete"] is False:
                unresolved.append("The reason native data were missing or rejected is not independently established by parser findings.")
        elif path.endswith("saved-values.json"):
            differences = report.get("differences")
            if not isinstance(differences, list):
                unresolved.append("Malformed saved-value comparison.")
                continue
            for index, difference in enumerate(differences):
                emit(path, f"/differences/{index}", "saved-value-mismatch", "difference", str(difference))
        elif path.endswith("config-integrity.json"):
            if report.get("status") not in ("equal", "qualified", "different"):
                unresolved.append("Unknown configuration integrity status.")
            elif report["status"] == "different":
                emit(path, "/status", "input-integrity", "config", str(report.get("reason", "Recorded configuration integrity difference")))
        elif path.endswith("availability.json"):
            histories = report.get("histories")
            if not isinstance(histories, dict) or not isinstance(histories.get("status"), str):
                unresolved.append("Malformed history availability report.")
            elif histories["status"] not in ("not-requested", "available", "native-unavailable"):
                emit(path, "/histories/status", "output-requirement", "histories", f"Recorded history availability: {histories['status']}")
    if not documents.get("output/raw/execution.json", {}).get("document"):
        unresolved.append("Execution diagnostics are unavailable; failure causes are unresolved.")
    return {"observations": observations, "unresolved": unresolved}


def read_run_diagnostics(context: ServerContext, simulation_id: str) -> JSONObject:
    """Read context's run diagnostics for simulation_id without native execution.

    Return a schema-validated bounded report independent of input replay. Preserve
    per-file issues, producer attribution and unresolved causes. Invalid identity,
    unsafe metadata, concurrent change or metadata I/O raises ValueError/OSError.
    Existing metadata reads may acquire/create their normal lock file.
    """
    record = safe_path(context.paths.simulation_memory, simulation_id)
    metadata = read_simulation(context.paths, simulation_id)
    metadata_bytes = _read_regular_bytes(safe_path(record, "metadata.json"))
    if load_json(metadata_bytes) != metadata:
        raise ValueError("stale: simulation metadata changed during diagnostics")
    engine = metadata["engine"]["name"]
    if engine not in ("sr", "trim"):
        raise ValueError("unsupported: diagnostic engine")
    terminal = metadata["status"] in ("succeeded", "failed")
    documents: JSONObject = {}
    issues: list[JSONObject] = []
    evidence = [{"kind": "simulation", "id": simulation_id, "revision": None, "path": (record / "metadata.json").relative_to(context.paths.root).as_posix(), "sha256": hashlib.sha256(metadata_bytes).hexdigest(), "size_bytes": len(metadata_bytes)}]
    if terminal:
        required = {"output/raw/execution.json"}
        selected = {item["path"] for item in metadata["outputs"]} & set(_PATHS)
        if engine == "sr":
            selected &= {"output/raw/execution.json", "output/parsed/result.json"}
        else:
            selected.discard("output/parsed/result.json")
        for path in sorted(selected | required):
            wrapper = _read_diagnostic_file(context, simulation_id, metadata, path)
            documents[path] = wrapper
            if wrapper["issue"] is not None:
                issues.append(wrapper["issue"])
            if wrapper["reference"] is not None:
                evidence.append(wrapper["reference"])
    collected = (_collect_sr_observations if engine == "sr" else _collect_trim_observations)(documents) if terminal else {"observations": [], "unresolved": ["Run is not terminal; final execution evidence is not sealed."]}
    unresolved = collected["unresolved"]
    for issue in issues:
        unresolved.append(f"Diagnostic evidence unavailable: {issue['path']} ({issue['code']}).")
    if metadata["status"] == "failed" and not collected["observations"]:
        unresolved.append("The failed status alone does not identify native, parser, monitoring or input root cause.")
    if _read_regular_bytes(safe_path(record, "metadata.json")) != metadata_bytes:
        raise ValueError("stale: simulation metadata changed during diagnostics")
    result = {"schema_version": 1, "simulation_id": simulation_id, "run_status": metadata["status"], "investigation_status": "not-terminal" if not terminal else "incomplete" if issues or unresolved else "no-recorded-failure" if metadata["status"] == "succeeded" else "reported", "recorded_failure": deepcopy(metadata["failure"]), "observations": collected["observations"][:64], "unresolved": list(dict.fromkeys((message if len(message) <= 512 else message[:497] + "... [truncated]") for message in unresolved))[:64], "evidence": evidence, "issues": issues, "omitted_observations": max(0, len(collected["observations"]) - 64)}
    validate_document(result, RUN_DIAGNOSTICS_SCHEMA)
    return result
