"""Record analysis provenance and immutable local artifacts without executing methods."""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
import errno
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Literal
from uuid import uuid4

from jsonschema import Draft202012Validator

from src.backend.config import Config
from src.backend.paths import ProjectPaths
from src.backend.project_records import initialize_record, list_records, read_record, revise_record
from src.knowledge.source import read_source_record, read_source_file
from src.srim.common.types import JSONObject
from src.srim.common.files import _read_regular_bytes


def create_analysis(paths: ProjectPaths, config: Config, spec: JSONObject, request_id: str, *, allocated_id: str | None = None) -> JSONObject:
    """Create draft analysis spec with explicit inputs/eligibility using config/request_id.

    Return stored revision/checks; do no scientific computation. Reject malformed
    methods, unavailable/disallowed inputs or publication failures with errors.
    """
    fields = {"title", "question", "method", "inputs", "eligibility", "parameters", "findings", "limitations"}
    if not isinstance(spec, dict) or set(spec) != fields or spec["findings"] != "" or spec["limitations"] != "":
        raise ValueError("New AnalysisSpec requires exact fields and empty narratives")
    checks = check_analysis_inputs(paths, spec["inputs"], spec["eligibility"])
    if spec["method"].get("kind") == "registered":
        from src.plan.catalog import read_capability
        method = read_capability(paths, spec["method"]["reference"])["manifest"]
        errors = list(Draft202012Validator(method["input_schema"]).iter_errors({key: spec[key] for key in ("inputs", "eligibility", "parameters")}))
        if errors:
            raise ValueError("Registered method input schema rejected analysis: " + errors[0].message)
    document = deepcopy(spec)
    document.update(status="draft", artifacts=[], failure=None)
    result = initialize_record(paths, config, "analysis", document, request_id, allocated_id=allocated_id)
    result["checks"] = checks
    return result


def read_analysis(paths: ProjectPaths, analysis_id: str, *, revision: int | None = None) -> JSONObject:
    """Read analysis_id/revision and return immutable content plus live evidence checks.

    Preserve stored findings and report stale input/artifact issues separately;
    malformed record or inaccessible revision raises ValueError/OSError.
    """
    result = read_record(paths, "analysis", analysis_id, revision=revision)
    issues = []
    try:
        checks = check_analysis_inputs(paths, result["record"]["inputs"], result["record"]["eligibility"])
    except (OSError, ValueError) as error:
        checks = []
        issues.append({"field": "inputs", "message": str(error)})
    for artifact in result["record"]["artifacts"]:
        try:
            payload = _read_regular_bytes(paths.postprocessing_memory / analysis_id / artifact["path"])
            if len(payload) != artifact["size_bytes"] or hashlib.sha256(payload).hexdigest() != artifact["sha256"]:
                raise ValueError("Artifact hash/size mismatch")
        except (OSError, ValueError) as error:
            issues.append({"field": artifact["name"], "message": str(error)})
    result.update(checks=checks, issues=issues)
    return result


def revise_analysis(paths: ProjectPaths, config: Config, analysis_id: str, expected_revision: int, spec: JSONObject, request_id: str) -> JSONObject:
    """Append a full draft spec to analysis_id with CAS and request_id.

    Preserve attached artifacts, validate registered method/input schemas and
    return revision/checks; terminal, stale, unsafe or disallowed changes raise.
    """
    if not isinstance(spec, dict) or set(spec) != {"title", "question", "method", "inputs", "eligibility", "parameters", "findings", "limitations"}:
        raise ValueError("Invalid AnalysisSpec")
    old = read_record(paths, "analysis", analysis_id)["record"]
    checks = check_analysis_inputs(paths, spec["inputs"], spec["eligibility"])
    if spec["method"].get("kind") == "registered":
        from src.plan.catalog import read_capability
        method = read_capability(paths, spec["method"]["reference"])["manifest"]
        errors = list(Draft202012Validator(method["input_schema"]).iter_errors({key: spec[key] for key in ("inputs", "eligibility", "parameters")}))
        if errors:
            raise ValueError("Registered method input schema rejected analysis: " + errors[0].message)
    document = deepcopy(spec)
    document.update(status="draft", artifacts=old["artifacts"], failure=None)
    result = revise_record(paths, config, "analysis", analysis_id, expected_revision, document, request_id)
    result["checks"] = checks
    return result


def attach_analysis_artifacts(paths: ProjectPaths, config: Config, analysis_id: str, expected_revision: int, files: list[JSONObject], request_id: str, *, allowed_roots: Sequence[Path]) -> JSONObject:
    """Snapshot files from allowed_roots and attach them to a draft analysis revision.

    Each file specifies name/role/source_path/media_type. Enforce source safety,
    64-MiB file/256-MiB operation caps and stable bytes. Return new revision;
    retain orphan blobs on CAS or publication failure and propagate errors.
    """
    if not isinstance(files, list) or not files or not allowed_roots or any(not isinstance(root, Path) or not root.is_absolute() or ".." in root.parts for root in allowed_roots):
        raise ValueError("Files and explicit safe input roots are required")
    old = read_record(paths, "analysis", analysis_id)["record"]
    if old["status"] != "draft" or old["revision"] != expected_revision:
        raise ValueError("conflict: attachment requires current draft revision")
    seen = {entry["name"].casefold() for entry in old["artifacts"]}
    selected, total = [], 0
    for file in files:
        if not isinstance(file, dict) or set(file) != {"name", "role", "source_path", "media_type"} or not isinstance(file["name"], str) or file["name"].casefold() in seen or file["role"] not in ("method", "code", "data", "figure", "report", "diagnostic") or not isinstance(file["source_path"], str) or not isinstance(file["media_type"], str):
            raise ValueError("Invalid attachment specification")
        name = file["name"]
        if "\\" in name or any(part in ("", ".", "..") or os.path.isreserved(part) or re.search(r'[<>:"|?*\x00-\x1f]', part) or part.endswith((".", " ")) for part in name.split("/")):
            raise ValueError("Unsafe attachment name")
        source = Path(file["source_path"])
        if not source.is_absolute() or ".." in source.parts or not any(source.is_relative_to(root) for root in allowed_roots):
            raise ValueError("unsafe-path: attachment outside allowed roots")
        for entry in (*reversed(source.parents), source):
            if entry.is_symlink() or entry.is_junction():
                raise OSError(errno.ELOOP, "Unsafe attachment source", str(entry))
        size = source.stat().st_size
        total += size
        if size > 67108864 or total > 268435456:
            raise ValueError("too-large: attachment cap exceeded")
        seen.add(name.casefold())
        selected.append((file, source, size))
    artifacts = deepcopy(old["artifacts"])
    for file, source, size in selected:
        before = source.stat()
        payload = _read_regular_bytes(source)
        after = source.stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) or len(payload) != size:
            raise ValueError("integrity: attachment changed during read")
        digest = hashlib.sha256(payload).hexdigest()
        relative = f"artifacts/{digest}/{Path(file['name']).name}"
        target = paths.postprocessing_memory / analysis_id / relative
        for entry in (*reversed(target.parents), target):
            if entry.is_symlink() or entry.is_junction():
                raise OSError(errno.ELOOP, "Unsafe artifact destination", str(entry))
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if _read_regular_bytes(target) != payload:
                raise ValueError("integrity: immutable artifact collision")
        else:
            staging = target.with_name(".artifact-" + str(uuid4()))
            with staging.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            if _read_regular_bytes(staging) != payload or _read_regular_bytes(source) != payload:
                raise ValueError("integrity: artifact source changed before publication")
            staging.rename(target)
        artifacts.append({"name": file["name"], "role": file["role"], "path": relative, "sha256": digest, "size_bytes": size, "media_type": file["media_type"], "source": str(source)})
    document = {key: value for key, value in old.items() if key not in {"schema_version", "kind", "id", "revision", "previous_sha256", "request_id", "created_at", "updated_at"}}
    document["artifacts"] = artifacts
    return revise_record(paths, config, "analysis", analysis_id, expected_revision, document, request_id)


def finish_analysis(paths: ProjectPaths, config: Config, analysis_id: str, expected_revision: int, status: Literal["completed", "failed"], findings: str, limitations: str, failure: JSONObject | None, request_id: str) -> JSONObject:
    """Finalize analysis_id with explicit status/narratives/failure and CAS/request_id.

    Reverify inputs and artifacts; completed requires reproducible method evidence.
    Return terminal revision or raise ValueError/OSError without scientific claims.
    """
    if status not in ("completed", "failed"):
        raise ValueError("Invalid analysis terminal status")
    current = read_analysis(paths, analysis_id)
    old = current["record"]
    if current["issues"]:
        raise ValueError("integrity: analysis evidence is stale or missing")
    if status == "completed":
        names = {artifact["name"] for artifact in old["artifacts"]}
        if not findings.strip() or not names or any(name not in names for name in old["method"]["code_artifacts"]):
            raise ValueError("Completed analysis requires findings and method/artifact evidence")
        if old["method"]["kind"] == "registered":
            from src.plan.catalog import read_capability
            method = read_capability(paths, old["method"]["reference"])["manifest"]
            errors = list(Draft202012Validator(method["input_schema"]).iter_errors({key: old[key] for key in ("inputs", "eligibility", "parameters")}))
            errors.extend(Draft202012Validator(method["output_schema"]).iter_errors({"artifacts": old["artifacts"], "findings": findings, "limitations": limitations}))
            if errors:
                raise ValueError("Registered method schema rejected analysis: " + errors[0].message)
    document = {key: value for key, value in old.items() if key not in {"schema_version", "kind", "id", "revision", "previous_sha256", "request_id", "created_at", "updated_at"}}
    document.update(status=status, findings=findings, limitations=limitations, failure=failure)
    return revise_record(paths, config, "analysis", analysis_id, expected_revision, document, request_id)


def list_analyses(paths: ProjectPaths, *, status: str | None = None, offset: int = 0, limit: int = 100) -> JSONObject:
    """List paths' analysis summaries/issues with status and bounded pagination.

    Return a copied listing without mutation; invalid arguments/I/O propagate.
    """
    return list_records(paths, "analysis", status=status, offset=offset, limit=limit)


def check_analysis_inputs(paths: ProjectPaths, inputs: list[JSONObject], eligibility: JSONObject) -> list[JSONObject]:
    """Verify selected input files against paths and explicit eligibility booleans.

    Return ordered provenance/availability reports, preserving recovery warnings.
    Reject stale evidence, disallowed failed/recovered inputs or incomplete parser
    results with ValueError/OSError; never compute or repair scientific values.
    """
    keys = {"allow_failed_runs", "allow_checkpoint_recovery", "require_complete_parser"}
    if not isinstance(inputs, list) or not isinstance(eligibility, dict) or set(eligibility) != keys or any(type(value) is not bool for value in eligibility.values()):
        raise ValueError("Explicit input eligibility booleans required")
    reports = []
    for selected in inputs:
        if not isinstance(selected, dict) or set(selected) != {"source", "files", "purpose"} or not isinstance(selected["purpose"], str) or not selected["purpose"].strip() or not isinstance(selected["files"], list) or not selected["files"]:
            raise ValueError("Invalid analysis input selection")
        record = read_source_record(paths, selected["source"])
        if record["execution_status"] == "failed" and not eligibility["allow_failed_runs"]:
            raise ValueError("Failed native inputs require explicit eligibility")
        reports_for_files = []
        for file in selected["files"]:
            payload = read_source_file(paths, selected["source"], file)
            recovered, complete, warnings = False, None, []
            if file["path"].endswith(".json") and file["path"].startswith("output/parsed/"):
                parsed = json.loads(payload)
                if isinstance(parsed, dict):
                    recovered = parsed.get("result_source") == "checkpoint-recovery" or parsed.get("format") == "trim-checkpoint-recovery-v2"
                    complete = parsed.get("complete")
                    if record["inputs"]["engine"] == "sr" and parsed.get("schema_version") == 1 and "rows" in parsed:
                        complete = True
                    warnings = parsed.get("warnings", [])
                    if isinstance(parsed.get("recovery"), dict):
                        warnings = parsed["recovery"].get("warnings", [])
            if recovered and not eligibility["allow_checkpoint_recovery"]:
                raise ValueError("Checkpoint recovery requires explicit eligibility")
            reports_for_files.append({"file": file, "recovered": recovered, "complete": complete, "warnings": warnings})
        if eligibility["require_complete_parser"] and not any(file["complete"] is True for file in reports_for_files):
            raise ValueError("Selected input lacks a complete scientific parser result")
        reports.append({"source": selected["source"], "execution_status": record["execution_status"], "files": reports_for_files})
    return reports
