"""Dispatch project tools to independent Python record APIs without native execution."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from uuid import UUID

from src.analysis.records import attach_analysis_artifacts, create_analysis, finish_analysis, list_analyses, read_analysis, revise_analysis
from src.backend.paths import create_base_directories
from src.case.records import create_case, list_cases, read_case, revise_case
from src.knowledge.database_sources import list_database_sources, inspect_database_source, read_database_sample
from src.knowledge.external_index import preview_external_import, import_external_preview, reindex_external_database, search_external_database, get_external_record, read_external_status
from src.knowledge.source import project_simulation_inputs
from src.mcp.common.diagnostics import read_run_diagnostics
from src.mcp.common.contracts import ServerContext
from src.mcp.common.files import safe_path
from src.plan.catalog import create_candidate, inventory_repository_skills, list_candidates, list_capabilities, list_collections, read_candidate, read_capability
from src.plan.improvement import create_improvement_proposal, read_improvement_status, record_assessment, record_observation
from src.srim.common.types import JSONObject
from src.knowledge.retrieval_search import search_knowledge, get_knowledge, read_knowledge_links
from src.knowledge.retrieval_index import CANCELLATION, initialize_retrieval_index, update_retrieval_index, reconcile_retrieval_index, read_retrieval_status
from src.srim.common.files import _read_json
from src.srim.common.records import read_simulation


def read_operation(name: str, arguments: JSONObject, context: ServerContext) -> JSONObject:
    """Return project read data for name/arguments under context, without native work.

    Verify explicit identities and preserve integrity issues separately. Unknown
    operation, malformed references, stale evidence or unsafe I/O raises errors.
    """
    paths, args = context.paths, deepcopy(arguments)
    if name in ("database_sources_list", "database_inspect", "database_sample_read"):
        operation = {"database_sources_list": list_database_sources, "database_inspect": inspect_database_source, "database_sample_read": read_database_sample}[name]
        if name == "database_sample_read" and args["reference"].get("sample_size_bytes", 0) > context.limits["file_chunk_bytes"]:
            raise ValueError("too-large: database sample exceeds file chunk cap")
        result = operation(paths, **args)
        samples = result.get("samples", [])
        if any(sample["reference"]["sample_size_bytes"] > context.limits["file_chunk_bytes"] for sample in samples):
            raise ValueError("too-large: inspection sample exceeds file chunk cap")
        if len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")) > context.limits["max_result_bytes"]:
            raise ValueError("too-large: database JSON exceeds result cap")
        return result
    if name in ("database_search", "database_get", "database_status"):
        operation = {"database_search": search_external_database, "database_get": get_external_record, "database_status": read_external_status}[name]
        result = operation(paths, **args)
        if len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")) > context.limits["max_result_bytes"]:
            raise ValueError("too-large: external retrieval JSON exceeds parsing cap; reduce limit or use database_get")
        return result
    if name in ("knowledge_search", "knowledge_get", "knowledge_links", "knowledge_status"):
        operation = {"knowledge_search": search_knowledge, "knowledge_get": get_knowledge, "knowledge_links": read_knowledge_links, "knowledge_status": read_retrieval_status}[name]
        result = operation(paths, **args)
        if len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")) > context.limits["max_result_bytes"]:
            raise ValueError("too-large: retrieval JSON exceeds parsing cap; reduce limit or use knowledge_get")
        return result
    if name == "cases_list":
        return list_cases(paths, **args)
    if name == "case_get":
        return read_case(paths, **args)
    if name == "analyses_list":
        return list_analyses(paths, **args)
    if name == "analysis_get":
        return read_analysis(paths, **args)
    if name == "collections_list":
        return list_collections(paths)
    if name == "collection_entries":
        return list_capabilities(paths, **args)
    if name == "collection_entry_get":
        return read_capability(paths, **args)
    if name == "candidates_list":
        return list_candidates(paths, **args)
    if name == "candidate_get":
        return read_candidate(paths, **args)
    if name == "repository_skills_list":
        return inventory_repository_skills(paths)
    if name == "improvement_status":
        return read_improvement_status(paths, **args)
    if name == "run_source":
        return project_simulation_inputs(paths, args["simulation_id"])
    if name == "run_get":
        metadata = read_simulation(paths, args["simulation_id"])
        verification = {"metadata": "structurally valid", "input_evidence": "not checked", "output_evidence": "selected files require separate hash verification"}
        try:
            source = project_simulation_inputs(paths, args["simulation_id"])
            verification["input_evidence"] = "verified"
        except (ValueError, OSError) as error:
            source = None
            verification["input_evidence"] = str(error)
        return {"metadata": metadata, "verification": verification, "source": source, "diagnostics": read_run_diagnostics(context, args["simulation_id"])}
    if name == "runs_list":
        matches, issues = [], []
        for path in sorted(paths.simulation_memory.iterdir() if paths.simulation_memory.exists() else []):
            if path.name.startswith("."):
                continue
            try:
                metadata = read_simulation(paths, path.name)
                if (args["engine"] is None or args["engine"] == metadata["engine"]["name"]) and (args["status"] is None or args["status"] == metadata["status"]):
                    matches.append({"id": path.name, "engine": metadata["engine"], "status": metadata["status"], "created_at": metadata["created_at"]})
            except (ValueError, OSError) as error:
                issues.append({"id": path.name, "message": str(error)})
        return {"total": len(matches), "offset": args["offset"], "limit": args["limit"], "matches": matches[args["offset"]:args["offset"] + args["limit"]], "issues": issues}
    if name == "physics_job_get":
        identifier = args["job_id"]
        if not identifier.startswith("job_") or str(UUID(identifier[4:])) != identifier[4:]:
            raise ValueError("Invalid physics job identity")
        root = safe_path(paths.root / "memory" / "mcp" / "srim_mcp" / "jobs", identifier)
        request, state = _read_json(root / "request.json"), _read_json(root / "state.json")
        digest = hashlib.sha256(json.dumps({"operation": request["operation"], "arguments": request["arguments"]}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
        if request.get("id") != identifier or state.get("id") != identifier or request.get("server") != "srim_mcp" or request.get("request_sha256") != digest:
            raise ValueError("integrity: physics job request/state mismatch")
        records, issues = [], []
        for simulation_id in state["simulation_ids"]:
            try:
                records.append(read_simulation(paths, simulation_id))
            except (ValueError, OSError) as error:
                issues.append({"simulation_id": simulation_id, "message": str(error)})
        return {"request": request, "snapshot": state, "records": records, "issues": issues, "qualification": "Read-only changing snapshot; live job links are not sealed native evidence"}
    raise ValueError("unsupported: unknown project read operation")


def write_operation(name: str, arguments: JSONObject, context: ServerContext, cancel: Callable[[], bool], progress: Callable[[JSONObject], None]) -> JSONObject:
    """Execute one accepted project mutation with explicit context/callbacks.

    Return outcome/data/references/warnings. Cancel only before publication,
    perform no physics/approval/promotion, and propagate I/O/validation failures.
    """
    args = deepcopy(arguments)
    request_id = args.pop("request_id")
    if cancel():
        return {"outcome": "cancelled", "data": None, "references": [], "warnings": []}
    allowed_roots = (context.paths.root, *(Path(value) for value in context.limits["allowed_input_roots"]))
    progress({"source": "adapter", "phase": "finalizing", "worker_ids": [], "simulation_ids": [], "data": {}})
    if name in ("case_create", "analysis_create"):
        create_base_directories(context.paths)
    if name in ("database_preview", "database_import", "database_reindex"):
        operation = {"database_preview": preview_external_import, "database_import": import_external_preview, "database_reindex": reindex_external_database}[name]
        try:
            data = operation(context.paths, **args, cancel=cancel, progress=progress)
        except InterruptedError:
            return {"outcome": "cancelled", "data": None, "references": [], "warnings": []}
        state = data.get("state")
        if state not in ("ready", "committed", "failed", "cancelled"):
            raise ValueError("Invalid external operation state")
        return {"outcome": "cancelled" if state == "cancelled" else "failed" if state == "failed" else "succeeded", "data": data, "references": [], "warnings": []}
    if name == "knowledge_update":
        mode = args.pop("operation")
        references = args.pop("references", [])
        scope = args.pop("scope", "registered")
        digest = args.pop("expected_plan_sha256", None)
        token = CANCELLATION.set(cancel)
        try:
            if mode == "initialize":
                if references or scope != "registered" or digest is not None:
                    raise ValueError("Initialize accepts no references or reconciliation options")
                data = initialize_retrieval_index(context.paths)
            elif mode == "update":
                if scope != "registered" or digest is not None:
                    raise ValueError("Update accepts only explicit references")
                data = update_retrieval_index(context.paths, references)
            elif mode == "reconcile":
                if references:
                    raise ValueError("Reconciliation accepts no explicit references")
                data = reconcile_retrieval_index(context.paths, scope=scope, expected_plan_sha256=digest)
            else:
                raise ValueError("Invalid knowledge update operation")
        finally:
            CANCELLATION.reset(token)
        failed = data.get("state") == "failed" or isinstance(data.get("report"), dict) and data["report"].get("state") == "failed"
        report = data.get("report") if isinstance(data.get("report"), dict) else data
        cancelled = any(issue.get("code") == "cancelled" for issue in report.get("issues", []))
        return {"outcome": "cancelled" if cancelled else "failed" if failed else "succeeded", "data": data, "references": [], "warnings": []}
    if name == "case_create":
        data = create_case(context.paths, context.config, request_id=request_id, **args)
    elif name == "case_revise":
        data = revise_case(context.paths, context.config, request_id=request_id, **args)
    elif name == "analysis_create":
        data = create_analysis(context.paths, context.config, request_id=request_id, **args)
    elif name == "analysis_revise":
        data = revise_analysis(context.paths, context.config, request_id=request_id, **args)
    elif name == "analysis_attach":
        data = attach_analysis_artifacts(context.paths, context.config, request_id=request_id, allowed_roots=allowed_roots, **args)
    elif name == "analysis_finish":
        data = finish_analysis(context.paths, context.config, request_id=request_id, **args)
    elif name == "candidate_create":
        data = create_candidate(context.paths, context.config, request_id=request_id, allowed_roots=allowed_roots, **args)
    elif name == "improvement_observe":
        data = record_observation(context.paths, context.config, request_id=request_id, **args)
    elif name == "improvement_assess":
        data = record_assessment(context.paths, context.config, request_id=request_id, **args)
    elif name == "improvement_proposal_create":
        data = create_improvement_proposal(context.paths, context.config, request_id=request_id, **args)
    else:
        raise ValueError("unsupported: unknown project mutation")
    return {"outcome": "succeeded", "data": data, "references": [], "warnings": []}
