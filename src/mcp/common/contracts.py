"""Validate MCP configuration, finite JSON documents, schemas and response envelopes."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import errno
import json
import math
import os
from pathlib import Path
import tomllib
from typing import Literal

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from src.backend.config import Config, load_config
from src.backend.paths import ProjectPaths, get_project_paths
from src.srim.metadata import JSONObject, JSONValue


ServerName = Literal["srim_mcp", "project_mcp"]

UUID_SCHEMA = {"type": "string", "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"}
HASH_SCHEMA = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
OFFSET_SCHEMA = {"type": "integer", "minimum": 0, "default": 0}
LIMIT_SCHEMA = {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}
NATIVE_SOURCE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["kind", "id", "metadata_sha256", "input_inventory_sha256"], "properties": {"kind": {"const": "simulation"}, "id": {"type": "string", "pattern": "^srim_[0-9]{8}_[0-9]{6}_[1-9][0-9]*$"}, "metadata_sha256": HASH_SCHEMA, "input_inventory_sha256": HASH_SCHEMA}}
NATIVE_FILE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["owner", "path", "sha256", "size_bytes", "role"], "properties": {"owner": NATIVE_SOURCE_SCHEMA, "path": {"type": "string", "minLength": 1}, "sha256": HASH_SCHEMA, "size_bytes": {"type": "integer", "minimum": 0}, "role": {"enum": ["input", "output", "other"]}}}
REFERENCE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["kind", "id", "revision", "path", "sha256", "size_bytes"], "properties": {"kind": {"enum": ["job", "object", "artifact", "simulation", "case", "analysis", "capability", "improvement-event", "proposal"]}, "id": {"type": "string", "minLength": 1}, "revision": {"type": ["integer", "null"], "minimum": 1}, "path": {"type": ["string", "null"]}, "sha256": {"oneOf": [HASH_SCHEMA, {"type": "null"}]}, "size_bytes": {"type": ["integer", "null"], "minimum": 0}}}
WARNING_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["code", "message", "source"], "properties": {"code": {"type": "string"}, "message": {"type": "string"}, "source": {"type": ["string", "null"]}}}
ERROR_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["code", "message", "fields", "retryable", "partial_references"], "properties": {"code": {"type": "string"}, "message": {"type": "string"}, "fields": {"type": "array", "items": {"type": "string"}}, "retryable": {"type": "boolean"}, "partial_references": {"type": "array", "items": REFERENCE_SCHEMA}}}
RESPONSE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["schema_version", "ok", "data", "error", "warnings", "references"], "properties": {"schema_version": {"const": 1}, "ok": {"type": "boolean"}, "data": {}, "error": {"oneOf": [ERROR_SCHEMA, {"type": "null"}]}, "warnings": {"type": "array", "items": WARNING_SCHEMA}, "references": {"type": "array", "items": REFERENCE_SCHEMA}}}
COMMON_PARAMETERS = {
    "capabilities_list": {"family": {"type": ["string", "null"], "default": None}, "query": {"type": ["string", "null"], "default": None}, "offset": OFFSET_SCHEMA, "limit": LIMIT_SCHEMA, "expected_generation": {"type": ["string", "null"], "default": None}},
    "capability_get": {"capability_id": {"type": "string"}},
    "schema_get": {"schema_id": {"type": "string"}},
    "example_get": {"example_id": {"type": "string"}},
    "job_get": {"job_id": {"type": "string", "pattern": "^job_[0-9a-f-]{36}$"}, "after_sequence": {"type": "integer", "minimum": 0, "default": 0}, "wait_seconds": {"type": "number", "minimum": 0, "maximum": 30, "default": 0.0}},
    "jobs_list": {"state": {"enum": [None, "queued", "running", "cancelling", "succeeded", "failed", "cancelled", "interrupted"], "default": None}, "offset": OFFSET_SCHEMA, "limit": LIMIT_SCHEMA},
    "job_cancel": {"job_id": {"type": "string", "pattern": "^job_[0-9a-f-]{36}$"}},
    "artifact_read": {"reference": REFERENCE_SCHEMA, "offset": OFFSET_SCHEMA, "length": {"type": "integer", "minimum": 1, "default": 262144}},
}
JOB_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["schema_version", "id", "state", "phase", "updated_at", "sequence", "worker_ids", "simulation_ids", "native_status", "parser_complete", "cancellation", "progress", "result", "error"], "properties": {"schema_version": {"const": 1}, "id": {"type": "string", "pattern": "^job_[0-9a-f-]{36}$"}, "state": {"enum": ["queued", "running", "cancelling", "succeeded", "failed", "cancelled", "interrupted"]}, "phase": {"enum": [None, "accepted", "allocating", "resolving", "preparing", "executing", "preserving", "parsing", "indexing", "cleanup", "finalizing", "finished"]}, "updated_at": {"type": "string"}, "sequence": {"type": "integer", "minimum": 0}, "worker_ids": {"type": "array", "items": {"type": "string", "pattern": "^worker_[1-9][0-9]*$"}}, "simulation_ids": {"type": "array", "items": {"type": "string", "pattern": "^srim_[0-9]{8}_[0-9]{6}_[1-9][0-9]*$"}}, "native_status": {"enum": [None, "created", "running", "succeeded", "failed"]}, "parser_complete": {"type": ["boolean", "null"]}, "cancellation": {"enum": ["queued-only", "cooperative", "timeout-only", "unsupported", "complete"]}, "progress": {"type": ["object", "null"], "description": "Exact wrapped adapter/domain callback; scientific progress is not invented."}, "result": {"oneOf": [REFERENCE_SCHEMA, {"type": "null"}]}, "error": {"oneOf": [REFERENCE_SCHEMA, {"type": "null"}]}}}


@dataclass(frozen=True)
class ServerContext:
    """Store server name, project paths, native config and validated adapter limits.

    Construction stores name, paths, config and limits without I/O. Use
    load_context to validate filesystem and configuration inputs first.
    """

    name: ServerName
    paths: ProjectPaths
    config: Config
    limits: JSONObject


def load_context(root: Path, server: ServerName) -> ServerContext:
    """Read root's explicit settings and MCP limits for server, returning context.

    Reject relative/unsafe roots, unknown configuration or invalid numeric/root
    limits with ValueError/OSError. Read no native installation and create nothing.
    """
    if not isinstance(root, Path) or not root.is_absolute() or ".." in root.parts or server not in ("srim_mcp", "project_mcp"):
        raise ValueError("Invalid absolute workspace root or server name")
    for entry in (*reversed(root.parents), root, root / "settings.toml", root / "mcp.toml"):
        if entry.is_symlink() or entry.is_junction():
            raise OSError(errno.ELOOP, "Configuration path contains a link", str(entry))
    config = load_config(root / "settings.toml")
    with (root / "mcp.toml").open("rb") as stream:
        document = tomllib.load(stream)
    keys = {"max_parallel", "max_pending", "max_request_bytes", "max_response_bytes", "max_result_bytes", "file_chunk_bytes", "allowed_input_roots"}
    if set(document) != {"schema_version", "srim_mcp", "project_mcp"} or type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise ValueError("Invalid MCP configuration envelope")
    for name in ("srim_mcp", "project_mcp"):
        values = document[name]
        if not isinstance(values, dict) or set(values) != keys:
            raise ValueError("Unknown or missing MCP limits")
        if any(type(values[key]) is not int or values[key] < 1 for key in keys - {"allowed_input_roots"}):
            raise ValueError("MCP numeric limits must be positive integers")
        if values["max_parallel"] > 32 or values["max_result_bytes"] < values["max_response_bytes"] or values["file_chunk_bytes"] > values["max_response_bytes"] // 4:
            raise ValueError("Inconsistent MCP size or parallel limits")
        roots = values["allowed_input_roots"]
        if not isinstance(roots, list) or any(not isinstance(item, str) for item in roots):
            raise ValueError("Input roots must be absolute directory strings")
        seen = {os.path.normcase(str(root))}
        for item in roots:
            path = Path(item)
            identity = os.path.normcase(str(path))
            if not path.is_absolute() or ".." in path.parts or identity in seen or not path.is_dir():
                raise ValueError("Invalid or duplicate input root")
            for entry in (*reversed(path.parents), path):
                if entry.is_symlink() or entry.is_junction():
                    raise OSError(errno.ELOOP, "Input root contains a link", str(entry))
            seen.add(identity)
    return ServerContext(server, get_project_paths(root), config, deepcopy(document[server]))


def load_json(contents: bytes) -> JSONObject:
    """Decode contents as a finite UTF-8 object, rejecting duplicate keys.

    Return independent JSON data without I/O; malformed encodings, repeated keys,
    nonfinite numbers, or a non-object root raise ValueError.
    """
    if not isinstance(contents, bytes):
        raise ValueError("JSON input requires bytes")
    try:
        decoder = json.JSONDecoder(object_pairs_hook=list)
        raw = decoder.decode(contents.decode("utf-8"))
        pending: list[object] = [raw]
        while pending:
            value = pending.pop()
            if isinstance(value, list):
                if value and all(isinstance(pair, tuple) and len(pair) == 2 and isinstance(pair[0], str) for pair in value):
                    names = [pair[0] for pair in value]
                    if len(set(names)) != len(names):
                        raise ValueError("Duplicate JSON key")
                    pending.extend(pair[1] for pair in value)
                else:
                    pending.extend(value)
            elif isinstance(value, float) and not math.isfinite(value):
                raise ValueError("Nonfinite JSON number")
        result = json.loads(contents.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Invalid UTF-8 JSON") from error
    if not isinstance(result, dict):
        raise ValueError("Expected JSON object")
    canonical_bytes(result)
    return result


def canonical_bytes(document: JSONObject) -> bytes:
    """Encode document as sorted two-space finite UTF-8 JSON with final LF.

    Validate recursive string-keyed JSON types without conversion or I/O; reject
    unsupported objects, cycles or nonfinite quantities with ValueError.
    """
    if not isinstance(document, dict):
        raise ValueError("Expected JSON object")
    pending: list[object] = [document]
    visited: set[int] = set()
    while pending:
        value = pending.pop()
        if isinstance(value, (dict, list)):
            if id(value) in visited:
                continue
            visited.add(id(value))
            if isinstance(value, dict):
                if any(not isinstance(key, str) for key in value):
                    raise ValueError("JSON object keys must be strings")
                pending.extend(value.values())
            else:
                pending.extend(value)
        elif value is not None and type(value) not in (str, int, float, bool):
            raise ValueError("Unsupported JSON value")
    try:
        return (json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    except (ValueError, TypeError, RecursionError) as error:
        raise ValueError("Invalid finite JSON document") from error


def validate_document(document: JSONObject, schema: JSONObject) -> None:
    """Validate finite document against a Draft 2020-12 schema; return None.

    Schema or value failures raise ValueError with a JSON-pointer location.
    Neither input is mutated and no files are accessed.
    """
    canonical_bytes(document)
    try:
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        for error in validator.iter_errors(document):
            location = "/" + "/".join(str(item).replace("~", "~0").replace("/", "~1") for item in error.absolute_path)
            raise ValueError(f"{location}: {error.message}")
    except (TypeError, KeyError, SchemaError) as error:
        raise ValueError("Invalid schema") from error


def make_response(data: JSONValue = None, *, error: JSONObject | None = None, warnings: list[JSONObject] | None = None, references: list[JSONObject] | None = None) -> JSONObject:
    """Return copied finite response data/error/warnings/references without I/O.

    Error suppresses data and sets ok false; invalid JSON values raise ValueError.
    """
    result = deepcopy({"schema_version": 1, "ok": error is None, "data": data if error is None else None, "error": error, "warnings": warnings or [], "references": references or []})
    canonical_bytes(result)
    return result


def error_response(error: Exception, *, integrity: bool = False, references: list[JSONObject] | None = None) -> JSONObject:
    """Map error to a bounded public response, using integrity context/references.

    Preserve safe partial references but never exception stacks or arbitrary
    attached objects. Return an error envelope without filesystem effects.
    """
    code = "integrity" if integrity else "invalid-argument" if isinstance(error, ValueError) else "io" if isinstance(error, OSError) else "internal"
    if isinstance(error, FileNotFoundError):
        code = "not-found"
    elif isinstance(error, FileExistsError):
        code = "conflict"
    elif isinstance(error, OSError) and error.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK) and error.filename is None:
        code = "busy"
    for prefix in ("not-found", "incompatible", "unavailable", "unsafe-path", "integrity", "conflict", "busy", "queue-full", "too-large", "unsupported", "interrupted"):
        if str(error).startswith(prefix + ":"):
            code = prefix
            break
    message = str(error)[:4096] if code != "internal" else "Unexpected server failure; inspect server diagnostics"
    return make_response(error={"code": code, "message": message, "fields": [], "retryable": code in ("busy", "queue-full"), "partial_references": references or []})
