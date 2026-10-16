"""Accept bounded JSON contracts and artifacts; expose validation and hashing.

All paths supplied in transport are relative bundle/artifact names, never host
paths. Runtime locations are resolved only from configuration and pinned IDs.
"""

import hashlib
import json
import math
from pathlib import Path, PureWindowsPath
import re


class AnalysisError(ValueError):
    """Construct from code/message/details; expose a stable recoverable failure."""

    def __init__(self, code: str, message: str, details: dict | None = None) -> None:
        """Accept an error code, message and optional JSON details; return nothing."""
        super().__init__(message)
        self.code, self.details = code, details or {}


def encode(value: object) -> str:
    """Accept finite JSON data; return canonical UTF-8-compatible JSON text."""
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def digest(value: object) -> str:
    """Accept finite JSON data; return its canonical SHA-256 hexadecimal digest."""
    return hashlib.sha256(encode(value).encode("utf-8")).hexdigest()


def checksum(path: Path) -> str:
    """Accept a file path; return its SHA-256 using bounded reads."""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def unique(pairs: list) -> dict:
    """Accept decoded object pairs; return a dictionary or reject duplicate keys."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise AnalysisError("invalid_json", "Duplicate JSON key", {"key": key})
        result[key] = value
    return result


def bounded(value: object, depth: int = 0) -> None:
    """Accept JSON data and current depth; reject nonfinite/deep data; return nothing."""
    if depth > 32:
        raise AnalysisError("invalid_json", "JSON nesting exceeds 32")
    if type(value) in (dict, list):
        for item in value.values() if isinstance(value, dict) else value:
            bounded(item, depth + 1)
    elif type(value) is float and not math.isfinite(value):
        raise AnalysisError("invalid_json", "Nonfinite JSON number")
    elif type(value) not in (str, int, float, bool, type(None)):
        raise AnalysisError("invalid_json", "Unsupported JSON value")


def loads(text: str) -> object:
    """Accept JSON text; return bounded data with duplicate/nonfinite rejection."""
    try:
        value = json.loads(text, object_pairs_hook=unique)
        bounded(value)
        return value
    except (ValueError, RecursionError) as exc:
        if isinstance(exc, AnalysisError):
            raise
        raise AnalysisError("invalid_json", "Malformed JSON") from exc


def write_json(path: Path, value: object) -> None:
    """Accept a new file path and finite JSON data; write once; return nothing."""
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(encode(value) + "\n")


def safe_path(root: Path, name: str) -> Path:
    """Accept a root/portable relative name; return contained path, rejecting links."""
    if (not isinstance(name, str) or not name or "\\" in name or ":" in name
            or any(part in ("", ".", "..") for part in name.split("/"))
            or PureWindowsPath(name).drive or Path(name).is_absolute()
            or any(part.endswith((" ", ".")) for part in name.split("/"))
            or any(re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?", part) for part in name.split("/"))):
        raise AnalysisError("unsafe_path", "Unsafe relative artifact path")
    path = root / name
    if root.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise AnalysisError("unsafe_path", "Artifact escapes its owner directory")
    if any(parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction())
           for parent in (path, *path.parents) if parent == root or root in parent.parents):
        raise AnalysisError("unsafe_path", "Artifact links/junctions are forbidden")
    return path


def obj(properties: dict, required: list | None = None) -> dict:
    """Accept property schemas/required names; return a strict object schema."""
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required, "additionalProperties": False}


SCHEMA_KEYS = frozenset(("type", "properties", "required", "additionalProperties", "items", "enum",
                         "minimum", "maximum", "minLength", "maxLength", "pattern", "minItems", "maxItems",
                         "minProperties", "maxProperties", "description"))


def check_schema(schema: dict) -> None:
    """Accept a method JSON schema; validate the supported closed subset; return nothing."""
    if not isinstance(schema, dict) or set(schema) - SCHEMA_KEYS or "type" not in schema:
        raise AnalysisError("invalid_schema", "Unsupported schema keyword or missing type")
    kinds = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
    if not kinds or any(kind not in ("object", "array", "string", "integer", "number", "boolean", "null") for kind in kinds):
        raise AnalysisError("invalid_schema", "Unsupported schema type")
    if "object" in kinds:
        if type(schema.get("properties", {})) is not dict or type(schema.get("required", [])) is not list:
            raise AnalysisError("invalid_schema", "Invalid object schema")
        if set(schema.get("required", [])) - schema.get("properties", {}).keys():
            raise AnalysisError("invalid_schema", "Required field has no schema")
        for child in schema.get("properties", {}).values():
            check_schema(child)
        extra = schema.get("additionalProperties", False)
        if isinstance(extra, dict):
            check_schema(extra)
        elif type(extra) is not bool:
            raise AnalysisError("invalid_schema", "Invalid additionalProperties")
    if "array" in kinds:
        check_schema(schema.get("items", {}))
    if "pattern" in schema:
        try:
            re.compile(schema["pattern"])
        except (TypeError, re.error) as exc:
            raise AnalysisError("invalid_schema", "Invalid pattern") from exc
    for key in ("minLength", "maxLength", "minItems", "maxItems", "minProperties", "maxProperties"):
        if key in schema and (type(schema[key]) is not int or schema[key] < 0):
            raise AnalysisError("invalid_schema", "Invalid schema bound", {"keyword": key})
    for key in ("minimum", "maximum"):
        if key in schema and (type(schema[key]) not in (int, float) or not math.isfinite(schema[key])):
            raise AnalysisError("invalid_schema", "Invalid numeric bound")


def validate(value: object, schema: dict, path: str = "arguments") -> None:
    """Accept JSON value/schema/location; validate the closed subset; return nothing."""
    kinds = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
    kind = {dict: "object", list: "array", str: "string", int: "integer", float: "number",
            bool: "boolean", type(None): "null"}.get(type(value))
    if kind not in kinds and not (kind == "integer" and "number" in kinds):
        raise AnalysisError("invalid_input", f"{path} has the wrong type")
    if "enum" in schema and value not in schema["enum"]:
        raise AnalysisError("invalid_input", f"{path} is outside the allowed values")
    if kind == "object":
        props = schema.get("properties", {})
        if set(schema.get("required", [])) - value.keys():
            raise AnalysisError("invalid_input", f"{path} is missing required fields")
        if not schema.get("minProperties", 0) <= len(value) <= schema.get("maxProperties", 10000):
            raise AnalysisError("invalid_input", f"{path} has too many/few fields")
        for key, child in value.items():
            spec = props.get(key, schema.get("additionalProperties", False))
            if spec is False:
                raise AnalysisError("invalid_input", f"{path}.{key} is unknown")
            if isinstance(spec, dict):
                validate(child, spec, f"{path}.{key}")
    elif kind == "array":
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 1000000):
            raise AnalysisError("invalid_input", f"{path} has too many/few items")
        for index, child in enumerate(value):
            validate(child, schema["items"], f"{path}[{index}]")
    elif kind == "string":
        if (not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 16000)
                or "\x00" in value or (schema.get("minLength", 0) and not value.strip())
                or ("pattern" in schema and re.fullmatch(schema["pattern"], value) is None)):
            raise AnalysisError("invalid_input", f"{path} has invalid length/format")
    elif kind in ("integer", "number"):
        if not math.isfinite(value) or not schema.get("minimum", -math.inf) <= value <= schema.get("maximum", math.inf):
            raise AnalysisError("invalid_input", f"{path} has an invalid numeric value")


TEXT = {"type": "string", "minLength": 1, "maxLength": 16000}
SHORT = {**TEXT, "maxLength": 200}
ID = {"type": "string", "pattern": "[0-9a-f]{32}"}
HASH = {"type": "string", "pattern": "[0-9a-f]{64}"}
NAME = {"type": "string", "pattern": "[a-z][a-z0-9_-]{0,63}"}
VERSION = {"type": "integer", "minimum": 1, "maximum": 2147483647}
AUDIT = {"actor": SHORT, "reason": TEXT, "idempotency_key": SHORT}
METHOD = obj({"method_id": NAME, "version": VERSION, "bundle_sha256": HASH})
JSON_OBJECT = {"type": "object", "additionalProperties": True}
