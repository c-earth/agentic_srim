"""Accept newline-delimited MCP JSON-RPC; expose independent case-management tools.

The stdio transport negotiates the existing application's 2024-11-05 protocol
baseline. Tool failures are structured results; malformed requests do not mutate
cases and do not prevent subsequent requests. No simulator code is imported.
"""

import json
import math
import sqlite3
import sys

from case import __version__
from case.contracts import tool_definitions
from case.records import CaseError
from case.storage import Store


def call_tool(name: str, arguments: dict, store: Store) -> dict:
    """Accept a tool/arguments/store; return MCP text containing a result or coded error."""
    try:
        if not isinstance(name, str):
            raise CaseError("unknown_tool", "Tool name must be a string")
        result = store.perform(name, arguments)
        error = False
    except CaseError as exc:
        result, error = {"error": {"code": exc.code, "message": str(exc)}}, True
    except (OSError, sqlite3.Error) as exc:
        result, error = {"error": {"code": "storage_error", "message": str(exc)}}, True
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        result, error = {"error": {"code": "invalid_input", "message": str(exc)}}, True
    return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, allow_nan=False)}],
            "isError": error}


def handle(message: object, store: Store) -> dict | None:
    """Accept one decoded JSON-RPC value/store; return a response or None for notifications."""
    request_id = message.get("id") if isinstance(message, dict) else None
    valid_id = request_id is None or type(request_id) in (str, int) or (
        type(request_id) is float and math.isfinite(request_id))
    if (not isinstance(message, dict) or message.get("jsonrpc") != "2.0"
            or not isinstance(message.get("method"), str) or not valid_id):
        return {"jsonrpc": "2.0", "id": request_id if valid_id else None,
                "error": {"code": -32600, "message": "Invalid Request"}}
    if "id" not in message:
        return None
    method, params = message["method"], message.get("params", {})
    if not isinstance(params, dict):
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "Invalid params"}}
    if method == "initialize":
        result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {"listChanged": False}},
                  "serverInfo": {"name": "case", "version": __version__}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": tool_definitions()}
    elif method == "tools/call":
        if set(params) - {"name", "arguments", "_meta"}:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "Invalid tool-call params"}}
        result = call_tool(params.get("name"), params.get("arguments", {}), store)
    else:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}}
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def reject_constant(value: str) -> None:
    """Accept a nonfinite JSON token; raise ValueError and return no value."""
    raise ValueError(f"Invalid JSON number {value}")


def serve(store: Store) -> None:
    """Accept a store; read stdin messages, write flushed stdout responses, return at EOF.

Messages are capped at 512 Ki characters; oversized lines are drained in bounded
chunks. Nonfinite numbers, duplicate keys and excessive nesting are parse errors.
"""
    maximum = 524288
    while line := sys.stdin.readline(maximum + 1):
        oversized = len(line) > maximum
        if oversized:
            while not line.endswith("\n"):
                line = sys.stdin.readline(maximum + 1)
                if not line:
                    break
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Message exceeds size limit"}}
        else:
            try:
                message = json.loads(line, parse_constant=reject_constant, object_pairs_hook=unique_object)
            except (ValueError, RecursionError):
                response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
            else:
                response = handle(message, store)
        if response is not None:
            print(json.dumps(response, allow_nan=False), flush=True)


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    """Accept decoded JSON key/value pairs; return an object or reject duplicate keys."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key")
        result[key] = value
    return result
