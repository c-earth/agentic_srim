"""Accept discovery imports; expose thirteen strict version-1 Analysis MCP tools."""

from analysis.artifacts import REFERENCE
from analysis.config import LIMIT_SCHEMA
from analysis.records import AUDIT, HASH, ID, JSON_OBJECT, METHOD, NAME, SHORT, TEXT, VERSION, obj


STRINGS = {"type": "array", "items": SHORT, "maxItems": 100}
SCHEMA = JSON_OBJECT
SPEC = obj({"description": TEXT, "applicability": STRINGS, "input_schema": SCHEMA,
            "parameter_schema": SCHEMA, "output_schema": SCHEMA,
            "requirements": {"type": "object", "additionalProperties": SHORT, "maxProperties": 64},
            "python": SHORT, "reproducible": {"type": "boolean"}, "external_state": JSON_OBJECT,
            "components": {"type": "array", "items": METHOD, "maxItems": 32}})
FILES = {"type": "object", "additionalProperties": {"type": "string", "maxLength": 2097152}, "minProperties": 2, "maxProperties": 64}
PAGE = {"limit": {"type": "integer", "minimum": 1, "maximum": 100},
        "cursor": {"type": ["string", "null"], "maxLength": 16000}}
JOB = {"analysis_id": ID}
SCOPE = obj({"source_ids": {"type": "array", "items": ID, "maxItems": 100}})
APPROVAL = obj({"decision": {"type": "string", "enum": ["approved"]}, "method": METHOD, "scope": SCOPE,
                "evidence_id": ID, "source": TEXT, "statement": TEXT})
ASSESSMENT = {"type": "string", "enum": ["experimental", "approved", "deprecated", "invalidated"]}
SCHEMAS = {
    "list_methods": obj({**PAGE, "method_id": NAME, "assessment": ASSESSMENT, "verified": {"type": "boolean"},
                         "source_ids": {"type": "array", "items": ID, "maxItems": 100}, "applicability": SHORT}, []),
    "get_method": METHOD,
    "register_method": obj({"schema_version": {"type": "integer", "enum": [1]}, "method_id": NAME, "version": VERSION,
                            "specification": SPEC, "files": FILES, **AUDIT}),
    "verify_method": obj({"schema_version": {"type": "integer", "enum": [1]}, "method": METHOD, "limits": LIMIT_SCHEMA, **AUDIT}),
    "submit_analysis": obj({"schema_version": {"type": "integer", "enum": [1]}, "method": METHOD,
                            "inputs": {"type": "object", "additionalProperties": REFERENCE, "minProperties": 1, "maxProperties": 33},
                            "parameters": JSON_OBJECT, "limits": LIMIT_SCHEMA, "recompute_of": ID, **AUDIT},
                           ["schema_version", "method", "inputs", "parameters", "limits", *AUDIT]),
    "get_analysis": obj({**JOB, "attempt_limit": PAGE["limit"], "attempt_cursor": ID}, ["analysis_id"]),
    "list_analyses": obj({**PAGE, "kind": {"type": "string", "enum": ["analysis", "verification"]},
                          "state": {"type": "string", "enum": ["QUEUED", "PREPARING", "RUNNING", "VERIFYING", "CHECKED", "FAILED", "TIMED_OUT", "CANCELLED", "RECOVERY_REQUIRED"]},
                          "method_id": NAME, "version": VERSION, "source_id": ID}, []),
    "get_analysis_output": obj({**JOB, "attempt_id": ID, "output_sha256": HASH, "name": SHORT,
                               "offset": {"type": "integer", "minimum": 0, "maximum": 2147483647},
                               "limit": {"type": "integer", "minimum": 1, "maximum": 1000}},
                              ["analysis_id", "attempt_id", "output_sha256", "name"]),
    "cancel_analysis": obj({**JOB, **AUDIT}),
    "retry_analysis": obj({**JOB, **AUDIT}),
    "recover_analyses": obj(AUDIT),
    "record_method_assessment": obj({"method": METHOD, "assessment": ASSESSMENT, "scope": SCOPE,
                                     "evidence": STRINGS, "replacement": METHOD, "human_approval": APPROVAL,
                                     "expected_revision": {"type": "integer", "minimum": 0}, **AUDIT},
                                    ["method", "assessment", "scope", "evidence", "expected_revision", *AUDIT]),
    "get_method_history": obj({"method": METHOD, **PAGE}, ["method"]),
}
READ_TOOLS = frozenset(("list_methods", "get_method", "get_analysis", "list_analyses", "get_analysis_output", "get_method_history"))


def tool_definitions() -> list[dict]:
    """Accept no inputs; return strict tool schemas and effect annotations."""
    return [{"name": name, "description": name.replace("_", " ") + "; trusted local postprocessing, software verification only.",
             "inputSchema": schema, "annotations": {"readOnlyHint": name in READ_TOOLS,
             "destructiveHint": name == "cancel_analysis", "idempotentHint": True, "openWorldHint": False}}
            for name, schema in SCHEMAS.items()]
