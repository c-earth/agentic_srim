"""Declare project-management tools and schemas independently of scientific execution."""

from __future__ import annotations

from src.knowledge.database_sources import SOURCE_LIST_SCHEMA, INSPECTION_SCHEMA, SAMPLE_REFERENCE_SCHEMA, SAMPLE_READ_SCHEMA

from copy import deepcopy
import hashlib

from jsonschema import Draft202012Validator

from src.mcp.common.contracts import COMMON_PARAMETERS, RUN_DIAGNOSTICS_SCHEMA, HASH_SCHEMA, JOB_SCHEMA, LIMIT_SCHEMA, NATIVE_FILE_SCHEMA, NATIVE_SOURCE_SCHEMA, OFFSET_SCHEMA, REFERENCE_SCHEMA, RESPONSE_SCHEMA, UUID_SCHEMA, ServerContext, canonical_bytes
from src.srim.common.types import JSONObject
from src.knowledge.retrieval_schema import EMPTY_QUERY
from src.knowledge.external_schema import EXTERNAL_REFERENCE_SCHEMA, EXTERNAL_QUERY_SCHEMA, EXTERNAL_PREVIEW_SCHEMA, EXTERNAL_REPORT_SCHEMA, EXTERNAL_RECORD_SCHEMA, EMPTY_EXTERNAL_QUERY


def build_catalog(context: ServerContext) -> JSONObject:
    """Return copied project tool/schema/example declarations for context.

    Validate static schemas without native execution or publishing records.
    Invalid schema raises ValueError; no filesystem state is changed.
    """
    text = {"type": "string"}
    nonblank = {"type": "string", "minLength": 1}
    positive = {"type": "integer", "minimum": 1}
    null = {"type": "null"}
    analysis_reference = {"type": "object", "additionalProperties": False, "required": ["kind", "id", "revision", "sha256"], "properties": {"kind": {"const": "analysis"}, "id": {"type": "string", "pattern": r"^post_[0-9]{8}_[0-9]{6}_[1-9][0-9]*$"}, "revision": positive, "sha256": HASH_SCHEMA}}
    member_reference = {"oneOf": [analysis_reference, {"type": "object", "additionalProperties": False, "required": ["kind", "source"], "properties": {"kind": {"const": "native-source"}, "source": NATIVE_SOURCE_SCHEMA}}, {"type": "object", "additionalProperties": False, "required": ["kind", "server", "job_id", "request_sha256"], "properties": {"kind": {"const": "job"}, "server": {"const": "srim_mcp"}, "job_id": {"type": "string", "pattern": r"^job_[0-9a-f-]{36}$"}, "request_sha256": HASH_SCHEMA}}]}
    member = {"type": "object", "additionalProperties": False, "required": ["member_id", "role", "note", "reference"], "properties": {"member_id": UUID_SCHEMA, "role": {"type": "string", "minLength": 1, "maxLength": 128}, "note": text, "reference": member_reference}}
    case_spec = {"type": "object", "additionalProperties": False, "required": ["title", "question", "status", "tags", "notes", "members"], "properties": {"title": {"type": "string", "minLength": 1, "maxLength": 512}, "question": nonblank, "status": {"enum": ["open", "paused", "completed", "archived"]}, "tags": {"type": "array", "maxItems": 64, "uniqueItems": True, "items": {"type": "string", "minLength": 1, "maxLength": 128}}, "notes": text, "members": {"type": "array", "items": member}}}
    method = {"type": "object", "additionalProperties": False, "required": ["kind", "reference", "description", "citations", "code_artifacts"], "properties": {"kind": {"const": "ad-hoc"}, "reference": null, "description": nonblank, "citations": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["title", "locator"], "properties": {"title": nonblank, "locator": nonblank}}}, "code_artifacts": {"type": "array", "items": text}}}
    inputs = {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["source", "files", "purpose"], "properties": {"source": NATIVE_SOURCE_SCHEMA, "files": {"type": "array", "minItems": 1, "items": NATIVE_FILE_SCHEMA}, "purpose": nonblank}}}
    eligibility = {"type": "object", "additionalProperties": False, "required": ["allow_failed_runs", "allow_checkpoint_recovery", "require_complete_parser"], "properties": {key: {"type": "boolean"} for key in ("allow_failed_runs", "allow_checkpoint_recovery", "require_complete_parser")}}
    analysis_spec = {"type": "object", "additionalProperties": False, "required": ["title", "question", "method", "inputs", "eligibility", "parameters", "findings", "limitations"], "properties": {"title": {"type": "string", "minLength": 1, "maxLength": 512}, "question": nonblank, "method": method, "inputs": inputs, "eligibility": eligibility, "parameters": {"type": "object", "description": "Explicit finite JSON parameters for the described analysis method."}, "findings": text, "limitations": text}}
    file_spec = {"type": "object", "additionalProperties": False, "required": ["name", "role", "source_path", "media_type"], "properties": {"name": nonblank, "role": {"enum": ["method", "code", "data", "figure", "report", "diagnostic"]}, "source_path": nonblank, "media_type": nonblank}}
    failure = {"oneOf": [null, {"type": "object", "additionalProperties": False, "required": ["code", "message"], "properties": {"code": nonblank, "message": nonblank}}]}
    revision = {"oneOf": [positive, null], "default": None}
    nullable_id = {"type": ["string", "null"], "default": None}
    params = deepcopy(COMMON_PARAMETERS)
    params.update({
        "cases_list": {"status": {"enum": [None, "open", "paused", "completed", "archived"], "default": None}, "offset": OFFSET_SCHEMA, "limit": LIMIT_SCHEMA},
        "case_get": {"case_id": nonblank, "revision": revision}, "case_create": {"spec": case_spec, "allocated_id": nullable_id}, "case_revise": {"case_id": nonblank, "expected_revision": positive, "spec": case_spec},
        "runs_list": {"engine": {"enum": [None, "sr", "trim", "sr-capture"], "default": None}, "status": {"enum": [None, "created", "running", "succeeded", "failed"], "default": None}, "offset": OFFSET_SCHEMA, "limit": LIMIT_SCHEMA},
        "run_get": {"simulation_id": nonblank}, "run_source": {"simulation_id": nonblank}, "physics_job_get": {"job_id": {"type": "string", "pattern": r"^job_[0-9a-f-]{36}$"}},
        "analyses_list": {"status": {"enum": [None, "draft", "completed", "failed"], "default": None}, "offset": OFFSET_SCHEMA, "limit": LIMIT_SCHEMA}, "analysis_get": {"analysis_id": nonblank, "revision": revision},
        "analysis_create": {"spec": analysis_spec, "allocated_id": nullable_id}, "analysis_revise": {"analysis_id": nonblank, "expected_revision": positive, "spec": analysis_spec}, "analysis_attach": {"analysis_id": nonblank, "expected_revision": positive, "files": {"type": "array", "minItems": 1, "items": file_spec}},
        "analysis_finish": {"analysis_id": nonblank, "expected_revision": positive, "status": {"enum": ["completed", "failed"]}, "findings": text, "limitations": text, "failure": failure},
        })
    scalar = {"oneOf": [{"type": "string", "maxLength": 256}, null]}
    bounds = {"oneOf": [scalar, {"type": "array", "minItems": 2, "maxItems": 2, "items": scalar}]}
    numeric = {"operator": {"enum": ["eq", "between", "unknown"]}, "value": bounds, "unit": {"type": ["string", "null"]}}
    summary_clause = {"type": "object", "additionalProperties": False, "required": ["name", *numeric], "properties": {"name": {"enum": ["calculated_ions", "transmitted_ions", "backscattered_ions", "mean_range", "straggling", "vacancies_per_ion"]}, **numeric}}
    table_clause = {"type": "object", "additionalProperties": False, "required": ["column", *numeric], "properties": {"column": {"oneOf": [{"type": "integer", "minimum": 0}, {"enum": ["energy", "requested_energy", "electronic_stopping", "nuclear_stopping", "projected_range", "longitudinal_straggling", "lateral_straggling"]}]}, **numeric}}
    output_filter = {"type": "object", "additionalProperties": False, "required": ["result_sources", "complete", "summary", "table_groups", "availability"], "properties": {
        "result_sources": {"oneOf": [null, {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"enum": ["native-text", "checkpoint-recovery"]}}]},
        "complete": {"type": ["boolean", "null"]},
        "summary": {"type": "array", "items": summary_clause},
        "table_groups": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["table", "clauses"], "properties": {"table": nonblank, "clauses": {"type": "array", "minItems": 1, "items": table_clause}}}},
        "availability": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["table", "state"], "properties": {"table": nonblank, "state": {"enum": ["available", "unavailable", "not_calculated"]}}}},
    }}
    from src.mcp.srim_mcp.catalog import build_catalog as scientific_catalog
    input_filter = scientific_catalog(context)["tools"]["inputs_search"]["inputSchema"]["properties"]["filters"]
    project_reference = {"type": "object", "additionalProperties": False, "required": ["kind", "id", "revision", "sha256"], "properties": {"kind": {"enum": ["case", "analysis"]}, "id": nonblank, "revision": positive, "sha256": HASH_SCHEMA}}
    retrieval_reference = {"oneOf": [NATIVE_SOURCE_SCHEMA, project_reference]}
    artifact_reference = {"type": "object", "additionalProperties": False, "required": ["name", "role", "path", "sha256", "size_bytes", "media_type", "source"], "properties": {"name": nonblank, "role": file_spec["properties"]["role"], "path": nonblank, "sha256": HASH_SCHEMA, "size_bytes": {"type": "integer", "minimum": 0}, "media_type": text, "source": nonblank}}
    relation_reference = {"oneOf": [null, NATIVE_SOURCE_SCHEMA, project_reference, *member_reference["oneOf"][1:], artifact_reference]}
    relation_clause = {"type": "object", "additionalProperties": False, "required": ["relation", "direction", "reference", "target_input_filters", "target_output_filter"], "properties": {
        "relation": {"enum": ["case_member", "analysis_input", "analysis_artifact"]}, "direction": {"enum": ["incoming", "outgoing"]}, "reference": relation_reference,
        "target_input_filters": input_filter, "target_output_filter": {"oneOf": [output_filter, null]},
    }}
    query_schema = {"type": "object", "additionalProperties": False, "required": list(EMPTY_QUERY), "properties": {
        "schema_version": {"const": 1}, "kinds": {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"enum": ["run", "case", "analysis"]}},
        "text": {"type": ["string", "null"], "minLength": 1, "maxLength": 4096},
        "statuses": {"type": "object", "additionalProperties": False, "required": ["run", "case", "analysis"], "properties": {kind: {"oneOf": [null, {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"enum": values}}]} for kind, values in {"run": ["succeeded", "failed"], "case": ["open", "paused", "completed", "archived"], "analysis": ["draft", "completed", "failed"]}.items()}},
        "tags": {"oneOf": [null, {"type": "object", "additionalProperties": False, "required": ["operator", "values"], "properties": {"operator": {"enum": ["contains_all", "contains_any"]}, "values": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1, "maxLength": 128}}}}]},
        "revision_scope": {"enum": ["current", "history"]}, "input_filters": input_filter, "output_filter": {"oneOf": [output_filter, null]}, "relations": {"type": "array", "items": relation_clause},
    }}
    kind_schema = {"enum": ["run", "case", "analysis"]}
    params.update({
        "knowledge_search": {"query": {**query_schema, "default": EMPTY_QUERY}, "offset": OFFSET_SCHEMA, "limit": LIMIT_SCHEMA, "expected_generation": {"type": ["string", "null"], "default": None}},
        "knowledge_get": {"kind": kind_schema, "reference": retrieval_reference, "table": {"type": ["string", "null"], "default": None}, "offset": OFFSET_SCHEMA, "limit": LIMIT_SCHEMA, "dependencies": {"type": "boolean", "default": False}},
        "knowledge_links": {"kind": kind_schema, "reference": retrieval_reference, "direction": {"enum": ["outgoing", "incoming"], "default": "outgoing"}, "offset": OFFSET_SCHEMA, "limit": LIMIT_SCHEMA},
        "knowledge_status": {},
        "knowledge_update": {"operation": {"enum": ["initialize", "update", "reconcile"]}, "references": {"type": "array", "default": [], "items": {"type": "object", "additionalProperties": False, "required": ["kind", "reference"], "properties": {"kind": kind_schema, "reference": retrieval_reference}}}, "scope": {"enum": ["registered", "project"], "default": "registered"}, "expected_plan_sha256": {"type": ["string", "null"], "default": None}},
    })
    params.update({
        "database_sources_list": {},
        "database_inspect": {"source_id": {"type": "string", "pattern": "^db_[0-9a-f]{64}$"}},
        "database_sample_read": {"reference": SAMPLE_REFERENCE_SCHEMA},
        "database_preview": {"source_id": {"type": "string", "pattern": "^db_[0-9a-f]{64}$"}, "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 3600, "default": 600}, "resume_operation_id": {"type": ["string", "null"], "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", "default": None}},
        "database_import": {"source_id": {"type": "string", "pattern": "^db_[0-9a-f]{64}$"}, "expected_plan_sha256": HASH_SCHEMA, "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 3600, "default": 600}},
        "database_reindex": {"source_id": {"type": "string", "pattern": "^db_[0-9a-f]{64}$"}, "expected_generation": {"type": "string", "format": "uuid"}, "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 3600, "default": 600}},
        "database_search": {"source_id": {"type": "string", "pattern": "^db_[0-9a-f]{64}$"}, "query": {**EXTERNAL_QUERY_SCHEMA, "default": EMPTY_EXTERNAL_QUERY}, "offset": OFFSET_SCHEMA, "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20}, "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 240, "default": 60}, "expected_generation": {"type": ["string", "null"], "default": None}},
        "database_get": {"reference": EXTERNAL_REFERENCE_SCHEMA, "verify_evidence": {"type": "boolean", "default": True}, "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 240, "default": 240}},
        "database_status": {"source_id": {"type": "string", "pattern": "^db_[0-9a-f]{64}$"}},
    })
    mutations = {"analysis_attach", "analysis_create", "analysis_finish", "analysis_revise", "case_create", "case_revise", "database_import", "database_preview", "database_reindex", "knowledge_update"}
    tools, capabilities = {}, {}
    descriptions = {"run_get": "Read run metadata, input verification and structured diagnostics. Investigate failures before exclusion; report independent findings and unresolved causes.", "case_create": "Create one case per user-defined scientific objective, including several within one query. Honor explicit case count or separation before case-specific work; shared paths or evidence do not merge cases. Share evidence by reference. No implicit current case or native execution.", "case_revise": "Append a full case revision using explicit case_id and expected_revision. Track its scientific objective and status independently; shared workflow paths or run/analysis references do not merge cases.", "analysis_create": "Record an analysis method/input plan; no scientific method executes.", "analysis_finish": "Seal findings/artifact provenance after verification; completion does not certify scientific correctness.", "physics_job_get": "Read a changing srim_mcp on-disk job snapshot; cannot launch, cancel, retry or edit that job."}
    descriptions.update({
        "knowledge_search": "Search pinned run inputs/outputs, case objectives and analysis narratives. Exact numeric row witnesses, literal text AND ranking, explicit statuses/revisions; relevance is not scientific confidence.",
        "knowledge_get": "Read an exact pinned document, output table manifests or paged rows; optionally verify all declared dependencies. Intact findings retain missing-artifact issues.",
        "knowledge_links": "Read explicit outgoing links or verified incoming indexed origins; no transitive inference or automatic sealing of job members.",
        "knowledge_status": "Read catalog-only entity/revision/rejection counts; this is not a full filesystem inventory.",
        "knowledge_update": "Initialize, update exact supplied references, or preview/apply explicit reconciliation using an unchanged plan digest. Never modify scientific/project source records.",
    })
    descriptions.update({
        "database_preview": "Explicitly verify and normalize one registered supported source into a staged import plan. Report fingerprints, counts, units and storage costs; never publish searchable records.",
        "database_import": "Atomically publish the exact approved staged plan after fresh source/generation verification. Explicit refresh versions changes, skips unchanged records and preserves missing-record history.",
        "database_reindex": "Build a generation-pinned exact search projection from imported records as a managed job. No archive reads, import or source edits.",
        "database_search": "Search imported inputs and summaries through exact indexes. Schema-v2 bounded compact hits verify local records only; originals are not_checked. Use database_get for full records and original evidence. Reindex required after import; no automatic rebuild.",
        "database_get": "Read an exact immutable external record and optionally verify current original archive/member evidence. Source registration remains required; no automatic refresh.",
        "database_status": "Read external catalog counts, generation, staged plans and registration availability without scanning or importing source contents.",
        "database_sources_list": "Reload explicitly registered external paths; stat only. No automatic loading or refresh.",
        "database_inspect": "Inspect one registered source within byte/entry/time limits; report packaging and sampled evidence. Unknown scientific formats require external-database-onboarding. No import.",
        "database_sample_read": "Re-read an exact registered prefix reference, verifying metadata and sample hash only. Changed evidence fails integrity; no automatic refresh.",
    })
    for name, properties in params.items():
        properties = deepcopy(properties)
        if name in mutations:
            properties["request_id"] = UUID_SCHEMA
        family = "discovery" if name in COMMON_PARAMETERS else "databases" if name.startswith("database_") else "search" if name.startswith("knowledge_") else "cases" if name.startswith("case") else "analysis" if name.startswith("analys") else "results"
        description = descriptions.get(name, name.replace("_", " ").capitalize() + "; validate provenance and use explicit immutable references.")
        schema = {"type": "object", "additionalProperties": False, "required": [key for key, value in properties.items() if "default" not in value], "properties": properties}
        tools[name] = {"name": name, "family": family, "description": description, "mutating": name in mutations, "inputSchema": schema, "outputSchema": RESPONSE_SCHEMA}
        identity = "capability_" + name.replace("_", "-")
        capabilities[identity] = {"id": identity, "title": name.replace("_", " ").capitalize(), "family": family, "summary": description, "tool_names": [name], "implementation": "implemented", "exposure": "mcp", "input_schema_ids": ["tool." + name], "output_schema_ids": ["mcp.response.v1"], "parameters": [{"pointer": "/" + key, "description": key.replace("_", " "), "value_type": value.get("type", "union"), "unit": None, "required": key in schema["required"], "default": value.get("default"), "enum": value.get("enum"), "minimum": value.get("minimum"), "maximum": value.get("maximum"), "bounds_kind": "schema", "reason": "Explicit record/reference validation", "source": "doc/guides/project-management.md"} for key, value in properties.items()], "units": {}, "defaults": {key: value["default"] for key, value in properties.items() if "default" in value}, "effects": ["Durable revision/publication job"] if name in mutations else ["Verified read; no scientific execution"], "compatibility": {"workspace": "local Windows project", "sessions": "multiple explicit case IDs; no session-wide current case"}, "automation": "No native execution or arbitrary code interpreter", "availability": "Explicit local records and configured sources", "evidence": [{"claim": "Provenance/approval boundaries follow the accepted project contract", "kind": "source", "reference": "doc/guides/project-management.md", "scope": "Organizational records, not independent scientific validation"}], "limitations": ["Case status is an organizational label", "Analysis completion verifies provenance, not physics"], "example_ids": [], "python_apis": ["src.knowledge.external_index"] if name in ("database_preview", "database_import", "database_reindex", "database_search", "database_get", "database_status") else ["src.knowledge.database_sources"] if name.startswith("database_") else ["src.knowledge.retrieval_search", "src.knowledge.retrieval_index"] if name.startswith("knowledge_") else ["src.case.records", "src.analysis.records"]}
    schemas = {"project.case-spec.v1": case_spec, "project.analysis-spec.v1": analysis_spec, "mcp.run-diagnostics.v1": RUN_DIAGNOSTICS_SCHEMA, "mcp.response.v1": RESPONSE_SCHEMA, "mcp.reference.v1": REFERENCE_SCHEMA, "mcp.job.v1": JOB_SCHEMA, "native.source.v1": NATIVE_SOURCE_SCHEMA, "native.file.v1": NATIVE_FILE_SCHEMA, **{"tool." + name: tool["inputSchema"] for name, tool in tools.items()}}
    schemas.update({"database.source-list.v1": SOURCE_LIST_SCHEMA, "database.inspection.v1": INSPECTION_SCHEMA, "database.sample-reference.v1": SAMPLE_REFERENCE_SCHEMA, "database.sample-read.v1": SAMPLE_READ_SCHEMA})
    schemas.update({"database.external-reference.v1": EXTERNAL_REFERENCE_SCHEMA, "database.external-query.v1": EXTERNAL_QUERY_SCHEMA, "database.external-preview.v1": EXTERNAL_PREVIEW_SCHEMA, "database.external-report.v1": EXTERNAL_REPORT_SCHEMA, "database.external-record.v1": EXTERNAL_RECORD_SCHEMA})
    schemas["knowledge.query.v1"] = query_schema
    schemas["knowledge.output-filter.v1"] = output_filter
    revision_header = {"schema_version": {"const": 1}, "kind": {"enum": ["case", "analysis"]}, "id": nonblank, "revision": positive, "previous_sha256": {"oneOf": [HASH_SCHEMA, null]}, "request_id": UUID_SCHEMA, "created_at": nonblank, "updated_at": nonblank}
    schemas["project.case.v1"] = {"type": "object", "additionalProperties": False, "required": list(revision_header) + case_spec["required"], "properties": {**revision_header, **case_spec["properties"], "kind": {"const": "case"}}}
    stored_artifact = {"type": "object", "additionalProperties": False, "required": ["name", "role", "path", "sha256", "size_bytes", "media_type", "source"], "properties": {"name": nonblank, "role": file_spec["properties"]["role"], "path": nonblank, "sha256": HASH_SCHEMA, "size_bytes": {"type": "integer", "minimum": 0}, "media_type": text, "source": nonblank}}
    schemas["project.analysis.v1"] = {"type": "object", "additionalProperties": False, "required": list(revision_header) + analysis_spec["required"] + ["status", "artifacts", "failure"], "properties": {**revision_header, **analysis_spec["properties"], "kind": {"const": "analysis"}, "status": {"enum": ["draft", "completed", "failed"]}, "artifacts": {"type": "array", "items": stored_artifact}, "failure": failure}}
    examples = {"database-discovery": {"id": "database-discovery", "tool": "database_sources_list", "arguments": {}, "purpose": "Discover registered sources, including unavailable paths, before bounded unknown-format inspection", "prerequisites": ["Register local paths in settings.toml; registration alone does not import data"], "expected": "Registered-source list with availability issues", "limitations": ["No source content scan or pool update; unknown formats need an evidence-based adapter proposal"]}}
    for index, (title, question) in enumerate((("Stopping study", "Compare stopping across selected energies"), ("Range trend study", "How does projected range change across the selected energies?")), 1):
        identifier = "case-create-" + str(index)
        examples[identifier] = {"id": identifier, "tool": "case_create", "arguments": {"request_id": f"00000000-0000-0000-0000-{index:012d}", "spec": {"title": title, "question": question, "status": "open", "tags": [], "notes": "", "members": []}}, "purpose": "Create one of two user-defined scientific objectives in one query; eligible native evidence may be shared", "prerequisites": ["Identify the user-defined objectives and honor any requested count or separation before case-specific work; two is only an example"], "expected": "Job reference, followed by independent immutable case reference", "limitations": ["Keep returned case IDs and revisions explicit; no automatic current case"]}
    examples["analysis-ad-hoc"] = {"id": "analysis-ad-hoc", "tool": "analysis_create", "arguments": {"request_id": "00000000-0000-0000-0000-000000000003", "spec": {"title": "Evidence inspection", "question": "What does the selected evidence establish?", "method": {"kind": "ad-hoc", "reference": None, "description": "Document the explicitly chosen inspection method and attach reproducible evidence", "citations": [], "code_artifacts": []}, "inputs": [], "eligibility": {"allow_failed_runs": False, "allow_checkpoint_recovery": False, "require_complete_parser": True}, "parameters": {}, "findings": "", "limitations": ""}}, "purpose": "Record an explicit method plan", "prerequisites": ["Choose eligible pinned input files before scientific analysis"], "expected": "Draft analysis reference", "limitations": ["No computation or scientific conclusion is inferred"]}
    for identity, example in examples.items():
        Draft202012Validator(tools[example["tool"]]["inputSchema"]).validate(example["arguments"])
        capabilities["capability_" + example["tool"].replace("_", "-")]["example_ids"].append(identity)
    for schema in schemas.values():
        Draft202012Validator.check_schema(schema)
    result = {"server": context.name, "tools": tools, "schemas": schemas, "capabilities": capabilities, "examples": examples, "limits": deepcopy(context.limits), "instructions": "Use one MCP case per user-defined scientific objective. A single query may contain several cases. Honor any explicitly requested case count or separation before starting case-specific work. Shared workflow paths or evidence do not merge cases. Track each case independently and share workflow steps, native evidence and analyses through explicit references where appropriate. Use explicit case IDs/revisions and map each objective to its case before proceeding. Reuse matching existing cases; clarify ambiguous boundaries. If existing records combine explicitly separated objectives, report the mismatch and propose a correction without waiting for cleanup. MCP does not infer objectives from query text. Review run_get diagnostics before excluding failed runs; distinguish recorded findings from unresolved causes. Coordinate native execution through srim_mcp. Record methods and pinned evidence without treating provenance completion as scientific validation."}
    result["generation"] = hashlib.sha256(canonical_bytes(result)).hexdigest()
    return deepcopy(result)
