"""Validate version-1 case inputs and expose normalized records and JSON schemas.

Inputs are JSON-compatible case patches, narrative entries and artifact references.
Results preserve explicit limitations; recorded approvals and budgets are evidence,
not execution authorization or budget enforcement. No external data are accessed.
"""

import hashlib
import json
import math
import re

from analysis.artifacts import REFERENCE as PRODUCER_REFERENCE
from analysis.contracts import SCHEMAS as ANALYSIS_SCHEMAS


PENDING_ACTION = {"type": "object", "properties": {
    "schema_version": {"type": "integer", "enum": [1]},
    "action_id": {"type": "string", "pattern": "[0-9a-f]{32}"},
    "kind": {"type": "string", "enum": ["analysis"]},
    "state": {"type": "string", "enum": ["intent", "submitted", "resolved"]},
    "submission": ANALYSIS_SCHEMAS["submit_analysis"],
    "analysis_id": {"type": "string", "pattern": "[0-9a-f]{32}"},
    "output": PRODUCER_REFERENCE},
    "required": ["schema_version", "action_id", "kind", "state", "submission"], "additionalProperties": False}


STATUSES = ("active", "paused", "blocked", "completed", "cancelled")
TEXT = {"type": "string", "minLength": 1, "maxLength": 16000}
SHORT = {"type": "string", "minLength": 1, "maxLength": 200}
ID = {"type": "string", "pattern": "^[0-9a-f]{32}$"}
STRINGS = {"type": "array", "items": TEXT, "maxItems": 100}
REFERENCE = {"type": "object", "properties": {"owner": SHORT, "artifact_id": SHORT},
             "required": ["owner", "artifact_id"], "additionalProperties": False}
ENTRY = {"type": "object", "properties": {
    "text": TEXT, "artifact_refs": {"type": "array", "items": REFERENCE, "maxItems": 100},
    "limitations": STRINGS}, "required": ["text"], "additionalProperties": False}
APPROVAL = {"type": "object", "properties": {
    "scope": TEXT, "decision": {"type": "string", "enum": ["approved", "denied", "revoked"]},
    "source": TEXT}, "required": ["scope", "decision", "source"], "additionalProperties": False}
BUDGET = {"type": "object", "properties": {
    "run_limit": {"type": "integer", "minimum": 0, "maximum": 2147483647},
    "wall_time_seconds": {"type": "number", "minimum": 0, "maximum": 31536000},
    "notes": TEXT}, "additionalProperties": False}
ARTIFACT = {"type": "object", "properties": {
    **REFERENCE["properties"], "kind": SHORT, "purpose": TEXT, "provenance": TEXT,
    "relative_path": {"type": "string", "minLength": 1, "maxLength": 2000},
    "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"}},
    "required": ["owner", "artifact_id", "kind", "purpose", "provenance"],
    "additionalProperties": False}
ARTIFACT["properties"]["reference"] = PRODUCER_REFERENCE
CONTENT = {
    "pending_actions": {"type": "array", "items": PENDING_ACTION, "maxItems": 100},
    "objective": {"type": "string", "maxLength": 16000},
    "success_criteria": STRINGS, "constraints": STRINGS, "assumptions": STRINGS,
    "prohibitions": STRINGS, "approvals": {"type": "array", "items": APPROVAL, "maxItems": 100},
    "budgets": BUDGET, "plan": STRINGS,
    **{name: {"type": "array", "items": ENTRY, "maxItems": 100}
       for name in ("decisions", "findings", "analyses", "answers")},
    "unresolved_issues": STRINGS, "next_action": {"type": "string", "maxLength": 16000},
}
PATCH = {"type": "object", "properties": {
    "title": SHORT, **CONTENT, "status": {"type": "string", "enum": list(STATUSES)},
    "archived": {"type": "boolean"}}, "minProperties": 1, "additionalProperties": False}


class CaseError(ValueError):
    """Construct from an error code/message; expose a recoverable tool failure."""

    def __init__(self, code: str, message: str) -> None:
        """Accept a stable code and human-readable message; initialize the error."""
        super().__init__(message)
        self.code = code


def validate(value: object, schema: dict, path: str = "arguments") -> None:
    """Accept JSON value/schema/path; return nothing or raise a validation error.

Enforces the schema subset used by this service at the server boundary, including
strict integer/boolean distinctions, finite numbers and bounded collections.
"""
    kind = schema["type"]
    if isinstance(kind, list):
        if value is None and "null" in kind:
            return
        kind = next(item for item in kind if item != "null")
    correct = {"object": type(value) is dict, "array": type(value) is list,
               "string": isinstance(value, str), "integer": type(value) is int,
               "number": type(value) in (int, float), "boolean": type(value) is bool}
    if not correct.get(kind, False):
        raise CaseError("invalid_input", f"{path} must be {kind}")
    if "enum" in schema and value not in schema["enum"]:
        raise CaseError("invalid_input", f"{path} must be one of {schema['enum']}")
    if kind == "object":
        properties = schema.get("properties", {})
        if set(schema.get("required", [])) - value.keys():
            raise CaseError("invalid_input", f"{path} is missing required fields")
        if schema.get("additionalProperties") is False and value.keys() - properties.keys():
            raise CaseError("invalid_input", f"{path} has unknown fields")
        if not schema.get("minProperties", 0) <= len(value) <= schema.get("maxProperties", 10000):
            raise CaseError("invalid_input", f"{path} cannot be empty")
        for name, item in value.items():
            if name in properties:
                validate(item, properties[name], f"{path}.{name}")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate(item, schema["additionalProperties"], f"{path}.{name}")
    elif kind == "array":
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 100):
            raise CaseError("invalid_input", f"{path} has too many items")
        for index, item in enumerate(value):
            validate(item, schema["items"], f"{path}[{index}]")
    elif kind == "string":
        minimum = schema.get("minLength", 0)
        if len(value) > schema.get("maxLength", 16000) or (minimum and len(value.strip()) < minimum):
            raise CaseError("invalid_input", f"{path} has invalid length")
        if "\x00" in value or ("pattern" in schema and re.fullmatch(schema["pattern"], value) is None):
            raise CaseError("invalid_input", f"{path} has invalid characters or format")
    elif kind in ("integer", "number"):
        if not math.isfinite(value) or not schema.get("minimum", -math.inf) <= value <= schema.get("maximum", math.inf):
            raise CaseError("invalid_input", f"{path} is outside the supported range")


def encode(value: object) -> str:
    """Accept a JSON-compatible value; return canonical finite UTF-8 JSON text."""
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def fingerprint(value: object) -> str:
    """Accept a JSON-compatible value; return its canonical SHA-256 hex digest."""
    return hashlib.sha256(encode(value).encode("utf-8")).hexdigest()


def empty_content() -> dict:
    """Accept no inputs; return fresh empty version-1 case content."""
    return {name: ("" if spec["type"] == "string" else {} if spec["type"] == "object" else [])
            for name, spec in CONTENT.items()}


def validate_record(record: dict) -> None:
    """Accept a complete case snapshot; validate content/links/size, returning nothing.

Referenced evidence must already be linked to this case. Completion requires a
recorded answer; it does not confer scientific acceptance. Snapshots are at most
256 KiB of UTF-8 JSON, bounding individual tool results and revision storage.
"""
    for name, spec in CONTENT.items():
        validate(record[name], spec, name)
    validate(record["title"], SHORT, "title")
    validate(record["original_request"], TEXT, "original_request")
    validate(record["status"], {"type": "string", "enum": list(STATUSES)}, "status")
    if record["status"] == "completed" and not record["answers"]:
        raise CaseError("invalid_state", "Completion requires a recorded answer with its limitations")
    if record["status"] == "blocked" and not record["unresolved_issues"]:
        raise CaseError("invalid_state", "Blocked cases require an unresolved issue")
    if len(record["artifacts"]) > 100:
        raise CaseError("invalid_input", "A case supports at most 100 artifact links")
    links = {(item["owner"], item["artifact_id"]) for item in record["artifacts"]}
    for section in ("decisions", "findings", "analyses", "answers"):
        for entry in record[section]:
            for reference in entry.get("artifact_refs", []):
                if (reference["owner"], reference["artifact_id"]) not in links:
                    raise CaseError("unlinked_artifact", "Narrative evidence must reference a linked artifact")
    if len(encode(record).encode("utf-8")) > 262144:
        raise CaseError("invalid_input", "Case snapshot exceeds 256 KiB")
