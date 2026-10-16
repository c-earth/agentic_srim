"""Accept bounded newline JSON-RPC; expose responsive Analysis tools over stdio."""

import json
import math
import sqlite3
import sys

from analysis import __version__
from analysis.contracts import tool_definitions
from analysis.records import AnalysisError, loads


def call_tool(name: str, arguments: dict, store) -> dict:
    """Accept tool/arguments/store; return structured text result or stable coded error."""
    try:
        if not isinstance(name, str):
            raise AnalysisError("unknown_tool", "Tool name must be a string")
        result = store.perform(name, arguments)
        error = False
    except (ValueError, OSError, sqlite3.Error, KeyError, TypeError, OverflowError, RecursionError) as exc:
        result = {"error": {"code": getattr(exc, "code", "storage_error" if isinstance(exc, (OSError, sqlite3.Error)) else "invalid_input"),
                            "message": str(exc), "details": getattr(exc, "details", {})}}
        error = True
    return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, allow_nan=False)}], "isError": error}


def handle(message: object, store) -> dict | None:
    """Accept decoded RPC/store; return response or ignore notifications without side effects."""
    request_id = message.get("id") if isinstance(message, dict) else None
    valid_id = request_id is None or type(request_id) in (str, int) or (type(request_id) is float and math.isfinite(request_id))
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str) or not valid_id:
        return {"jsonrpc": "2.0", "id": request_id if valid_id else None, "error": {"code": -32600, "message": "Invalid Request"}}
    if "id" not in message:
        return None
    method, params = message["method"], message.get("params", {})
    if not isinstance(params, dict):
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "Invalid params"}}
    if method == "initialize":
        result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "analysis", "version": __version__}}
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


def serve(store) -> None:
    """Accept store; read bounded messages/write flushed RPC replies; return at EOF."""
    maximum = 16 * 1024 * 1024
    while line := sys.stdin.readline(maximum + 1):
        if len(line.encode("utf-8")) > maximum:
            while not line.endswith("\n"):
                line = sys.stdin.readline(maximum + 1)
                if not line:
                    break
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Message exceeds 16 MiB"}}
        else:
            try:
                response = handle(loads(line), store)
            except (ValueError, RecursionError):
                response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        if response is not None:
            print(json.dumps(response, allow_nan=False), flush=True)
