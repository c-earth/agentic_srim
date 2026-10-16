"""Expose a minimal MCP stdio server without application-side AI orchestration.

Input is newline-delimited JSON-RPC on stdin; output is protocol JSON on stdout.
Supports protocol 2024-11-05, explicit calculation tools, and run inspection.
Calls are sequential; cancellation during a run uses another CLI process.
"""

import json
import math
import re
import sqlite3
import sys

from agentic_srim import __version__
from agentic_srim.capabilities.stopping_range.inputs import REQUEST_SCHEMA, Request
from agentic_srim.capabilities.stopping_range.workflow import submit, read_output as read_stopping_range_output
from agentic_srim.domain.specifications import digest, integer
from agentic_srim.execution.jobs import cancel, read_metadata, recover
from agentic_srim.config import Settings
from agentic_srim.execution.workers import dispatch
from agentic_srim.capabilities.trim.inputs import REQUEST_SCHEMA as TRIM_SCHEMA, Request as TrimRequest
from agentic_srim.capabilities.trim.workflow import submit as submit_trim, read_output
from agentic_srim.storage.exports import EXPORT_SCHEMA, export_run_data
from analysis.records import loads


def tool_definitions() -> list[dict]:
    """Accept no inputs; return MCP contracts with explicit inputs and effect hints.

    Validation computes without state changes and is labelled read-only. Result
    inspection can initialize the metadata database through get_run, so it is
    conservatively not labelled strictly read-only. Simulation, cancellation,
    and recovery remain explicitly state-changing. Hints never grant permission.
    """
    request_schema = {"type": "object", "properties": {"request": REQUEST_SCHEMA},
                      "required": ["request"], "additionalProperties": False}
    run_schema = {"type": "object", "properties": {"run_id": {"type": "string", "pattern": "^[0-9a-f]{32}$"}},
                  "required": ["run_id"], "additionalProperties": False}
    trim_schema = {"type": "object", "properties": {"request": TRIM_SCHEMA}, "required": ["request"], "additionalProperties": False}
    output_schema = {"type": "object", "properties": {**run_schema["properties"], "name": {"type": "string"},
                     "offset": {"type": "integer", "minimum": 0, "maximum": 2147483647}, "limit": {"type": "integer", "minimum": 1, "maximum": 1000}},
                     "required": ["run_id", "name", "offset", "limit"], "additionalProperties": False}
    definitions = [
        {"name": "export_run_data", "description": "Publish immutable selected JSONL data from an exact CHECKED run attempt; starts no simulation.", "inputSchema": EXPORT_SCHEMA},
        {"name": "validate_stopping_range", "description": "Validate explicit inputs without simulation.", "inputSchema": request_schema},
        {"name": "run_stopping_range", "description": "Execute one bounded stopping/range run. Scientific accuracy is not validated.", "inputSchema": request_schema},
        {"name": "get_stopping_range_output", "description": "Read a verified stopping, range and straggling table with units and provenance.", "inputSchema": run_schema},
        {"name": "validate_trim", "description": "Validate explicit TRIM inputs and mode/output compatibility without simulation.", "inputSchema": trim_schema},
        {"name": "run_trim", "description": "Execute one bounded TRIM run with audited native dialog handling. Scientific accuracy is not validated.", "inputSchema": trim_schema},
        {"name": "get_trim_output", "description": "Read a bounded page of verified normalized TRIM output records.", "inputSchema": output_schema},
        {"name": "get_run", "description": "Read verified run metadata and lifecycle history.", "inputSchema": run_schema},
        {"name": "cancel_run", "description": "Request cooperative cancellation.", "inputSchema": run_schema},
        {"name": "recover_runs", "description": "Recover abandoned attempts after prior workers exit.",
         "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    ]
    for tool in definitions:
        name = tool["name"]
        tool["annotations"] = {
            "readOnlyHint": name in ("validate_stopping_range", "validate_trim"),
            "destructiveHint": name in ("run_stopping_range", "run_trim", "cancel_run", "recover_runs"),
            "idempotentHint": name not in ("run_stopping_range", "run_trim"),
            "openWorldHint": False,
        }
    return definitions


def call_tool(name: str, arguments: dict, settings: Settings) -> dict:
    """Execute named tool with arguments/settings; return MCP text content.

    Rejects unknown/extra fields and malformed scalar arguments before storage
    access, independently of client-side JSON Schema checks. Expected input and
    storage failures return isError, keeping the protocol session usable.
    """
    try:
        if name == "export_run_data":
            result = export_run_data(settings.runtime_directory, arguments)
            return {"content": [{"type": "text", "text": json.dumps(result, allow_nan=False)}], "isError": False}
        expected = {t["name"]: set(t["inputSchema"]["properties"]) for t in tool_definitions()}
        if not isinstance(name, str) or name not in expected or not isinstance(arguments, dict) or set(arguments) != expected[name]:
            raise ValueError("Unknown tool or invalid argument fields")
        if "run_id" in arguments:
            run_id = arguments["run_id"]
            if not isinstance(run_id, str) or re.fullmatch(r"[0-9a-f]{32}", run_id) is None:
                raise ValueError("Run ID must be 32 lowercase hexadecimal characters")
        if name == "get_trim_output":
            if not isinstance(arguments["name"], str):
                raise ValueError("TRIM output name must be a string")
            integer(arguments["offset"], "offset", 0, 2147483647)
            integer(arguments["limit"], "limit", 1, 1000)
        runtime = settings.runtime_directory
        if name in ("validate_stopping_range", "run_stopping_range"):
            request = Request.from_dict(arguments["request"])
            if name == "validate_stopping_range":
                result = {"valid": True, "physical_hash": digest(request.physical_spec())}
            else:
                result = dispatch(runtime, submit(request, settings))
        elif name == "get_run":
            result = read_metadata(runtime, arguments["run_id"])
        elif name == "get_stopping_range_output":
            result = read_stopping_range_output(runtime, arguments["run_id"])
        elif name in ("validate_trim", "run_trim"):
            request = TrimRequest.from_dict(arguments["request"])
            if name == "validate_trim":
                result = {"valid": True, "physical_hash": digest(request.physical_spec())}
            else:
                result = dispatch(runtime, submit_trim(request, settings))
        elif name == "get_trim_output":
            result = read_output(runtime, arguments["run_id"], arguments["name"], arguments["offset"], arguments["limit"])
        elif name == "cancel_run":
            result = cancel(runtime, arguments["run_id"])
        else:
            result = {"recovered_runs": recover(runtime)}
        return {"content": [{"type": "text", "text": json.dumps(result, allow_nan=False)}],
                "isError": name in ("run_stopping_range", "run_trim") and (result.get("state") != "CHECKED" or bool(result.get("error")))}
    except (ValueError, OSError, RuntimeError, KeyError, TypeError, OverflowError, sqlite3.Error) as exc:
        if name == "export_run_data":
            return {"isError": True, "content": [{"type": "text", "text": json.dumps({"error": {
                "code": getattr(exc, "code", "storage_error"), "message": str(exc), "details": getattr(exc, "details", {})}})}]}
        return {"isError": True, "content": [{"type": "text", "text": str(exc)}]}


def handle(message: dict, settings: Settings) -> dict | None:
    """Handle one JSON-RPC input with settings; return a response or None for notifications."""
    request_id = message.get("id") if isinstance(message, dict) else None
    valid_id = (request_id is None or type(request_id) in (str, int)
                or (type(request_id) is float and math.isfinite(request_id)))
    if (not isinstance(message, dict) or message.get("jsonrpc") != "2.0"
            or not isinstance(message.get("method"), str) or not valid_id):
        return {"jsonrpc": "2.0", "id": request_id if valid_id else None,
                "error": {"code": -32600, "message": "Invalid Request"}}
    if "id" not in message:
        return None
    method, params = message["method"], message.get("params", {})
    if not isinstance(params, dict):
        return {"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32602, "message": "Invalid params"}}
    if method == "initialize":
        result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {"listChanged": False}},
                  "serverInfo": {"name": "agentic-srim", "version": __version__}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": tool_definitions()}
    elif method == "tools/call":
        result = call_tool(params.get("name"), params.get("arguments", {}), settings)
    else:
        return {"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": "Method not found"}}
    return {"jsonrpc": "2.0", "id": message["id"], "result": result}


def reject_json_constant(value: str) -> None:
    """Reject a non-JSON numeric constant token with ValueError; return no value."""
    raise ValueError(f"Invalid JSON numeric constant: {value}")


def serve(settings: Settings) -> None:
    """Read JSON-RPC stdin lines using settings; write responses and return at EOF.

    Invalid JSON, integer conversion limits and excessive nesting yield parse
    errors for that line; subsequent requests continue through the same session.
    """
    maximum = 16 * 1024 * 1024
    while line := sys.stdin.readline(maximum + 1):
        if len(line.encode("utf-8")) > maximum:
            while not line.endswith("\n"):
                line = sys.stdin.readline(maximum + 1)
                if not line:
                    break
            print(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Message exceeds 16 MiB"}}), flush=True)
            continue
        try:
            message = loads(line)
        except (ValueError, RecursionError):
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            response = handle(message, settings)
        if response is not None:
            print(json.dumps(response, allow_nan=False), flush=True)
