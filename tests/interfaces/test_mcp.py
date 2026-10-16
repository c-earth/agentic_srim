"""Accept repository fixtures and unittest discovery; expose interfaces/test_mcp.py checks without native execution unless opted in."""

import io
import json
import os
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch
from agentic_srim.capabilities.stopping_range.workflow import execute, submit
from agentic_srim.interfaces.cli import main as cli_main
from agentic_srim.interfaces.mcp import call_tool, handle, serve
from agentic_srim.storage.database import connect, transition
from sr_module.support import IsolatedTest
from trim.support import fixture_request


class MCPTests(IsolatedTest):
    """Construct unittest cases; verify interfaces contracts and report failures."""

    def exchange(self, messages: list[dict]) -> list[dict]:
        """Accept protocol messages; return captured responses from one in-memory session."""
        output = io.StringIO()
        incoming = "".join(json.dumps(message) + "\n" for message in messages)
        with patch("sys.stdin", io.StringIO(incoming)), patch("sys.stdout", output):
            serve(self.settings)
        return [json.loads(line) for line in output.getvalue().splitlines()]

    @unittest.skipUnless(os.environ.get("AGENTIC_SRIM_INTEGRATION") == "1" and os.environ.get("AGENTIC_SRIM_TRIM_INTEGRATION") == "1", "Both native gates explicitly required")
    def test_native_cli_and_fresh_mcp_cover_all_tools_and_output_pages(self) -> None:
        """Accept no inputs; run both backends via CLI/MCP, check nine tools and page native outputs; return nothing."""
        config = self.runtime / "app.toml"
        config.write_text(f"srim_directory = '{self.settings.sr_module_directory.parent.as_posix()}'\nruntime_directory = '{self.runtime.as_posix()}'\n", encoding="utf-8")
        command = [sys.executable, "-B", "-m", "agentic_srim.interfaces.cli", "--config", str(config)]

        def cli(arguments: list[str]) -> dict:
            """Accept CLI arguments; assert bounded subprocess success and return its JSON output."""
            response = subprocess.run(command + arguments, capture_output=True, text=True, timeout=90)
            self.assertEqual(response.returncode, 0, response.stderr)
            return json.loads(response.stdout)

        def wire(calls: list[tuple[str, dict]]) -> list[dict]:
            """Accept tool names/arguments; return successful tool JSON from a fresh bounded stdio server."""
            messages = [{"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}},
                        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}]
            messages.extend({"jsonrpc": "2.0", "id": index + 2, "method": "tools/call",
                             "params": {"name": name, "arguments": arguments}}
                            for index, (name, arguments) in enumerate(calls))
            response = subprocess.run(command + ["mcp"], input="".join(json.dumps(message) + "\n" for message in messages),
                                      capture_output=True, text=True, timeout=90)
            self.assertEqual(response.returncode, 0, response.stderr)
            replies = [json.loads(line) for line in response.stdout.splitlines()]
            self.assertEqual([reply["id"] for reply in replies], list(range(len(messages))))
            self.assertEqual(len(replies[1]["result"]["tools"]), 9)
            results = []
            for reply in replies[2:]:
                result = reply["result"]
                self.assertFalse(result["isError"], result)
                results.append(json.loads(result["content"][0]["text"]))
            return results

        requests = {"stopping_range": self.data, "trim": fixture_request(2).to_dict()}
        for capability, request in requests.items():
            path = self.runtime / f"{capability}.json"
            path.write_text(json.dumps(request), encoding="utf-8")
            cli(["validate", str(path), "--capability", capability])
            record = cli(["run", str(path), "--capability", capability])
            self.assertEqual(record["state"], "CHECKED", record.get("error"))
            self.assertEqual(cli(["show", record["id"]])["state"], "CHECKED")
            self.assertEqual(cli(["cancel", record["id"]])["state"], "CHECKED")
            if capability == "stopping_range":
                self.assertEqual(cli(["output", record["id"]]), record["result"])
            else:
                page = cli(["output", record["id"], "EXYZ.txt", "--capability", "trim", "--limit", "2"])
                self.assertEqual(len(page["rows"]), 2)
        self.assertEqual(cli(["recover"]), {"recovered_runs": []})
        results = wire([(f"{action}_{capability}", {"request": request})
                        for capability, request in requests.items() for action in ("validate", "run")])
        self.assertTrue(results[0]["valid"])
        self.assertTrue(results[2]["valid"])
        sr, trim = results[1], results[3]
        for record in (sr, trim):
            self.assertEqual(record["state"], "CHECKED", record.get("error"))
        pages = [(name, table.get("rows", table.get("records"))) for group in ("profiles", "details")
                 for name, table in trim["result"][group].items()]
        calls = [("get_run", {"run_id": sr["id"]}), ("get_stopping_range_output", {"run_id": sr["id"]}),
                 ("cancel_run", {"run_id": trim["id"]}), ("recover_runs", {})]
        calls.extend(("get_trim_output", {"run_id": trim["id"], "name": name, "offset": 0, "limit": 1})
                     for name, _ in pages)
        inspected = wire(calls)
        self.assertNotIn("result", inspected[0])
        self.assertEqual(inspected[1], sr["result"])
        self.assertEqual(inspected[2]["state"], "CHECKED")
        self.assertEqual(inspected[3], {"recovered_runs": []})
        self.assertEqual(len(pages), 14)
        for page, (_, expected) in zip(inspected[4:], pages):
            count = len(expected) if isinstance(expected, list) else expected
            self.assertEqual(page["total_records"], count)
            self.assertEqual(len(page["rows"]), min(1, count))
            self.assertEqual(page["next_offset"], 1 if count > 1 else None)

    def test_run_tools_distinguish_unfinished_runs_from_completed_run_warnings(self) -> None:
        """Accept no inputs; preserve successful results with worker warnings for both tools; return nothing."""
        cases = [(state, None, []) for state in ("QUEUED", "RUNNING", "COLLECTED", "PARSED", "FAILED", "CANCELLED", "QUARANTINED", "CHECKED")]
        cases.append(("CHECKED", None, ["Worker exited with code 4\nworker cleanup failed"]))
        for capability, request in (("stopping_range", self.data), ("trim", fixture_request().to_dict())):
            for state, error, warnings in cases:
                with self.subTest(capability=capability, state=state, warnings=warnings):
                    record = {"id": "a" * 32, "state": state, "error": error}
                    if warnings:
                        record["warnings"] = warnings
                    with patch("agentic_srim.interfaces.mcp.submit", return_value=record["id"]), patch("agentic_srim.interfaces.mcp.submit_trim", return_value=record["id"]), patch("agentic_srim.interfaces.mcp.dispatch", return_value=record):
                        response = call_tool(f"run_{capability}", {"request": request}, self.settings)
                    self.assertEqual(response["isError"], state != "CHECKED" or bool(error))
                    self.assertEqual(json.loads(response["content"][0]["text"]), record)

    def test_cli_run_distinguishes_unfinished_runs_from_completed_run_warnings(self) -> None:
        """Accept no inputs; return exit 0 with completed result/warnings and 1 for failures; return nothing."""
        cases = (("RUNNING", "Worker exited with code 7", []), ("PARSED", "execution is incomplete", []),
                 ("CHECKED", None, ["Worker exited with code 4"]), ("CHECKED", None, []))
        for capability, request in (("stopping_range", self.data), ("trim", fixture_request().to_dict())):
            path = self.runtime / f"{capability}-request.json"
            path.write_text(json.dumps(request), encoding="utf-8")
            for state, error, warnings in cases:
                with self.subTest(capability=capability, state=state, warnings=warnings):
                    record = {"id": "a" * 32, "state": state, "error": error}
                    if warnings:
                        record["warnings"] = warnings
                    output, diagnostic = io.StringIO(), io.StringIO()
                    with patch("sys.argv", ["agentic-srim", "run", str(path), "--capability", capability]), patch("sys.stdout", output), patch("sys.stderr", diagnostic), patch("agentic_srim.interfaces.cli.Settings.load", return_value=self.settings), patch("agentic_srim.interfaces.cli.submit", return_value=record["id"]), patch("agentic_srim.interfaces.cli.submit_trim", return_value=record["id"]), patch("agentic_srim.interfaces.cli.dispatch", return_value=record):
                        status = cli_main()
                    self.assertEqual(status, 1 if state != "CHECKED" or error else 0)
                    self.assertEqual(json.loads(output.getvalue()), record)
                    self.assertIn(record["id"], diagnostic.getvalue())

    def test_verified_completed_result_succeeds_after_late_worker_exit(self) -> None:
        """Accept no inputs; exercise real dispatch/storage through both interfaces with a mocked late exit; return nothing."""
        path = self.runtime / "request.json"
        path.write_text(json.dumps(self.data), encoding="utf-8")
        for interface in ("mcp", "cli"):
            with self.subTest(interface=interface):
                with patch("agentic_srim.capabilities.stopping_range.workflow.run", side_effect=self.simulate):
                    before = execute(self.runtime, submit(self.request, self.settings))
                self.assertEqual(before["state"], "CHECKED")
                process = MagicMock(returncode=4)
                process.communicate.return_value = (b"", b"worker cleanup failed")
                with patch("agentic_srim.execution.workers.subprocess.Popen", return_value=process):
                    if interface == "mcp":
                        with patch("agentic_srim.interfaces.mcp.submit", return_value=before["id"]):
                            response = call_tool("run_stopping_range", {"request": self.data}, self.settings)
                        self.assertFalse(response["isError"])
                        result = json.loads(response["content"][0]["text"])
                    else:
                        output = io.StringIO()
                        with patch("sys.argv", ["agentic-srim", "run", str(path)]), patch("sys.stdout", output), patch("sys.stderr", io.StringIO()), patch("agentic_srim.interfaces.cli.Settings.load", return_value=self.settings), patch("agentic_srim.interfaces.cli.submit", return_value=before["id"]):
                            self.assertEqual(cli_main(), 0)
                        result = json.loads(output.getvalue())
                self.assertEqual(result["result"], before["result"])
                self.assertEqual(result["state"], "CHECKED")
                self.assertIsNone(result["error"])
                self.assertEqual(result["warnings"], ["Worker exited with code 4\nworker cleanup failed"])
                inspected = call_tool("get_run", {"run_id": before["id"]}, self.settings)
                self.assertFalse(inspected["isError"])
                self.assertEqual(json.loads(inspected["content"][0]["text"])["warnings"], result["warnings"])

    def test_malformed_tool_arguments_fail_before_storage_and_allow_ping(self) -> None:
        """Accept no inputs; reject scalar/shape errors without storage or execution; return nothing."""
        cases = []
        for tool in ("get_run", "get_stopping_range_output", "get_trim_output", "cancel_run"):
            for run_id in ([], {}, None, True, 12, 1.5, "", "a" * 31, "A" * 32, "a" * 32 + "\n"):
                arguments = {"run_id": run_id}
                if tool == "get_trim_output":
                    arguments.update(name="RANGE.txt", offset=0, limit=1)
                cases.append((tool, arguments))
        page = {"run_id": "a" * 32, "name": "RANGE.txt", "offset": 0, "limit": 1}
        for field, invalid_values in (
                ("name", ([], {}, None, True, 12)),
                ("offset", ([], None, True, 1.5, -1, 2147483648, 10 ** 400)),
                ("limit", ({}, None, False, 1.5, 0, 1001, 10 ** 400))):
            cases.extend(("get_trim_output", page | {field: value}) for value in invalid_values)
        cases.extend((name, {}) for name in ([], {}, None, True, 12, "unknown"))
        cases.extend(("get_run", arguments) for arguments in ([], None, {}, {"run_id": "a" * 32, "extra": 1}))
        cases.append(("recover_runs", {"extra": 1}))
        with patch("agentic_srim.storage.database.connect") as storage, patch("agentic_srim.interfaces.mcp.dispatch") as dispatch:
            for name, arguments in cases:
                with self.subTest(name=name, arguments=arguments):
                    responses = self.exchange([
                        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}},
                        {"jsonrpc": "2.0", "id": 2, "method": "ping"},
                    ])
                    self.assertEqual(len(responses), 2)
                    self.assertTrue(responses[0]["result"]["isError"])
                    self.assertEqual(responses[1], {"jsonrpc": "2.0", "id": 2, "result": {}})
            storage.assert_not_called()
            dispatch.assert_not_called()
        self.assertFalse((self.runtime / "metadata.sqlite").exists())

    def test_numeric_overflow_is_a_validation_error_and_allows_followup(self) -> None:
        """Accept no inputs; reject oversized SR/TRIM numbers before execution; return nothing."""
        with patch("agentic_srim.storage.database.connect") as storage, patch("agentic_srim.interfaces.mcp.dispatch") as dispatch:
            for capability, data in (("stopping_range", self.data), ("trim", fixture_request().to_dict())):
                for prefix in ("validate", "run"):
                    with self.subTest(capability=capability, prefix=prefix):
                        responses = self.exchange([
                            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                                "name": f"{prefix}_{capability}", "arguments": {"request": data | {"ion_mass_u": 10 ** 400}}}},
                            {"jsonrpc": "2.0", "id": 2, "method": "ping"},
                            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
                                "name": f"validate_{capability}", "arguments": {"request": data}}},
                        ])
                        self.assertEqual(len(responses), 3)
                        self.assertTrue(responses[0]["result"]["isError"])
                        self.assertIn("finite", responses[0]["result"]["content"][0]["text"])
                        self.assertEqual(responses[1]["result"], {})
                        self.assertFalse(responses[2]["result"]["isError"])
            storage.assert_not_called()
            dispatch.assert_not_called()

    def test_sqlite_failures_return_tool_errors_and_allow_ping(self) -> None:
        """Accept no inputs; keep the session usable after expected SQLite failures; return nothing."""
        for failure in (sqlite3.ProgrammingError("binding failure"), sqlite3.OperationalError("database locked"),
                        sqlite3.DatabaseError("database damaged")):
            with self.subTest(failure=type(failure).__name__), patch("agentic_srim.storage.database.connect", side_effect=failure):
                responses = self.exchange([
                    {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "get_run", "arguments": {"run_id": "a" * 32}}},
                    {"jsonrpc": "2.0", "id": 2, "method": "ping"},
                    {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "validate_stopping_range", "arguments": {"request": self.data}}},
                ])
                self.assertEqual(len(responses), 3)
                self.assertTrue(responses[0]["result"]["isError"])
                self.assertEqual(responses[0]["result"]["content"][0]["text"], str(failure))
                self.assertEqual(responses[1]["result"], {})
                self.assertFalse(responses[2]["result"]["isError"])

    def test_invalid_request_ids_do_not_poison_response_serialization(self) -> None:
        """Accept no inputs; reject invalid IDs while preserving legal IDs and notifications; return nothing."""
        for request_id in ([], {}, True, False, float("inf"), float("nan")):
            with self.subTest(request_id=request_id):
                response = handle({"jsonrpc": "2.0", "id": request_id, "method": "ping"}, self.settings)
                self.assertEqual(response["error"]["code"], -32600)
                self.assertIsNone(response["id"])
                json.dumps(response, allow_nan=False)
        for request_id in (0, "request-1", 1.5, None):
            self.assertEqual(handle({"jsonrpc": "2.0", "id": request_id, "method": "ping"}, self.settings),
                             {"jsonrpc": "2.0", "id": request_id, "result": {}})
        self.assertIsNone(handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, self.settings))

    def test_fresh_stdio_session_survives_malformed_input_lines(self) -> None:
        """Accept no inputs; follow malformed wire inputs with successful requests; return nothing."""
        config = self.runtime / "app.toml"
        runtime = self.runtime / "unused-runtime"
        config.write_text(f"srim_directory = '{self.settings.sr_module_directory.parent.as_posix()}'\nruntime_directory = '{runtime.as_posix()}'\n", encoding="utf-8")
        cases = [
            ('bad-json', -32700),
            ('{"jsonrpc":"2.0","id":NaN,"method":"ping"}', -32700),
            ('{"jsonrpc":"2.0","id":Infinity,"method":"ping"}', -32700),
            ('{"jsonrpc":"2.0","id":1e400,"method":"ping"}', -32600),
            ('[' * 10000 + '0' + ']' * 10000, -32700),
            ('{"jsonrpc":"2.0","id":' + '9' * 5000 + ',"method":"ping"}', -32700),
            (json.dumps({"jsonrpc": "2.0", "id": [], "method": "ping"}), -32600),
            (json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": []}), -32602),
            (json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "get_run", "arguments": {"run_id": []}}}), None),
            (json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                "name": "validate_stopping_range", "arguments": {"request": self.data | {"ion_mass_u": 10 ** 400}}}}), None),
        ]
        lines = [json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}})]
        for index, (line, _) in enumerate(cases, 1):
            lines.extend([line, json.dumps({"jsonrpc": "2.0", "id": index, "method": "ping"})])
        lines.append(json.dumps({"jsonrpc": "2.0", "id": 100, "method": "tools/call", "params": {
            "name": "validate_stopping_range", "arguments": {"request": self.data}}}))
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONINTMAXSTRDIGITS="4300")
        command = [sys.executable, "-B", "-m", "agentic_srim.interfaces.cli", "--config", str(config), "mcp"]
        wire = subprocess.run(command, input="\n".join(lines) + "\n", capture_output=True, text=True, timeout=15, env=environment)
        self.assertEqual(wire.returncode, 0, wire.stderr)
        responses = [json.loads(line) for line in wire.stdout.splitlines()]
        self.assertEqual(len(responses), 2 * len(cases) + 2)
        for index, (_, code) in enumerate(cases):
            failure, ping = responses[2 * index + 1:2 * index + 3]
            if code is None:
                self.assertTrue(failure["result"]["isError"])
            else:
                self.assertEqual(failure["error"]["code"], code, f"Malformed wire case {index}")
            self.assertEqual(ping, {"jsonrpc": "2.0", "id": index + 1, "result": {}})
        self.assertFalse(responses[-1]["result"]["isError"])
        self.assertFalse(runtime.exists())

    def test_mcp_contracts_and_error_responses(self) -> None:
        """Accept no inputs; verify MCP discovery, input checks, and JSON framing."""
        initialized = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}}, self.settings)
        self.assertEqual(initialized["result"]["protocolVersion"], "2024-11-05")
        listed = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, self.settings)
        self.assertEqual(len(listed["result"]["tools"]), 9)
        self.assertIn("get_stopping_range_output", {tool["name"] for tool in listed["result"]["tools"]})
        valid = call_tool("validate_stopping_range", {"request": self.data}, self.settings)
        self.assertFalse(valid["isError"])
        self.assertTrue(call_tool("validate_stopping_range", {"request": {}}, self.settings)["isError"])
        self.assertTrue(call_tool("get_run", {"run_id": "missing", "extra": 1}, self.settings)["isError"])
        messages = 'bad-json\n' + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + '\n' + json.dumps({"jsonrpc": "2.0", "id": 3, "method": "ping"}) + '\n'
        output = io.StringIO()
        with patch("sys.stdin", io.StringIO(messages)), patch("sys.stdout", output):
            serve(self.settings)
        responses = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(responses), 2)
        self.assertEqual(responses[0]["error"]["code"], -32700)
        self.assertEqual(responses[1]["result"], {})

    def test_named_output_tools_share_verified_results_with_cli(self) -> None:
        """Accept no inputs; retrieve a sealed fixture run through real CLI/stdio MCP processes."""
        with patch("agentic_srim.capabilities.stopping_range.workflow.run", side_effect=self.simulate):
            record = execute(self.runtime, submit(self.request, self.settings))
        self.assertEqual(record["state"], "CHECKED")
        result = call_tool("get_stopping_range_output", {"run_id": record["id"]}, self.settings)
        self.assertFalse(result["isError"])
        self.assertEqual(json.loads(result["content"][0]["text"]), record["result"])
        metadata = call_tool("get_run", {"run_id": record["id"]}, self.settings)
        self.assertFalse(metadata["isError"])
        self.assertNotIn("result", json.loads(metadata["content"][0]["text"]))
        self.assertTrue(call_tool("get_trim_output", {"run_id": record["id"], "name": "RANGE.txt", "offset": 0, "limit": 1}, self.settings)["isError"])
        config = self.runtime / "app.toml"
        config.write_text(f"srim_directory = '{self.settings.sr_module_directory.parent.as_posix()}'\nruntime_directory = '{self.runtime.as_posix()}'\n", encoding="utf-8")
        command = [sys.executable, "-B", "-m", "agentic_srim.interfaces.cli", "--config", str(config)]
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        shown = subprocess.run(command + ["show", record["id"]], capture_output=True, text=True, timeout=15, env=environment)
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertNotIn("result", json.loads(shown.stdout))
        output = subprocess.run(command + ["output", record["id"], "--capability", "stopping_range"], capture_output=True, text=True, timeout=15, env=environment)
        self.assertEqual(output.returncode, 0, output.stderr)
        self.assertEqual(json.loads(output.stdout), record["result"])
        invalid = subprocess.run(command + ["output", record["id"], "RANGE.txt"], capture_output=True, text=True, timeout=15, env=environment)
        self.assertEqual(invalid.returncode, 2)
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "get_run", "arguments": {"run_id": record["id"]}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "get_stopping_range_output", "arguments": {"run_id": record["id"]}}},
        ]
        wire = subprocess.run(command + ["mcp"], input="".join(json.dumps(request) + "\n" for request in requests), capture_output=True, text=True, timeout=15, env=environment)
        self.assertEqual(wire.returncode, 0, wire.stderr)
        responses = [json.loads(line)["result"] for line in wire.stdout.splitlines()]
        self.assertEqual(len(responses[1]["tools"]), 9)
        self.assertNotIn("result", json.loads(responses[2]["content"][0]["text"]))
        self.assertEqual(json.loads(responses[3]["content"][0]["text"]), record["result"])

    def test_output_requires_checked_run_and_rejects_archive_tampering(self) -> None:
        """Accept no inputs; inspect failed-run metadata and reject incomplete or altered output."""
        queued = submit(self.request, self.settings)
        self.assertTrue(call_tool("get_stopping_range_output", {"run_id": queued}, self.settings)["isError"])
        with connect(self.runtime) as connection:
            transition(connection, queued, "FAILED", detail="fixture failure")
        metadata = call_tool("get_run", {"run_id": queued}, self.settings)
        self.assertFalse(metadata["isError"])
        self.assertEqual(json.loads(metadata["content"][0]["text"])["state"], "FAILED")
        with patch("agentic_srim.capabilities.stopping_range.workflow.run", side_effect=self.simulate):
            record = execute(self.runtime, submit(self.request, self.settings))
        archive = self.runtime / record["attempts"][0]["artifact_path"]
        (archive / "SR_OUTPUT.txt").write_bytes(b"tampered")
        for tool in ("get_run", "get_stopping_range_output"):
            self.assertTrue(call_tool(tool, {"run_id": record["id"]}, self.settings)["isError"])

    def test_stored_attempt_mismatch_is_rejected_by_cli_and_mcp(self) -> None:
        """Accept no inputs; reject corrupted result linkage through both interfaces; return nothing."""
        with patch("agentic_srim.capabilities.stopping_range.workflow.run", side_effect=self.simulate):
            record = execute(self.runtime, submit(self.request, self.settings))
        self.assertEqual(record["state"], "CHECKED")
        record["result"]["provenance"]["attempt_id"] = "0" * 32
        with connect(self.runtime) as connection:
            connection.execute("UPDATE results SET result_json=? WHERE run_id=?", (json.dumps(record["result"]), record["id"]))
        config = self.runtime / "app.toml"
        config.write_text(f"srim_directory = '{self.settings.sr_module_directory.parent.as_posix()}'\nruntime_directory = '{self.runtime.as_posix()}'\n", encoding="utf-8")
        command = [sys.executable, "-B", "-m", "agentic_srim.interfaces.cli", "--config", str(config)]
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        for operation in ("show", "output"):
            with self.subTest(operation=operation):
                response = subprocess.run(command + [operation, record["id"]], capture_output=True, text=True, timeout=15, env=environment)
                self.assertEqual(response.returncode, 2, response.stdout)
                self.assertIn("provenance", response.stderr)
                self.assertEqual(response.stdout, "")
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "get_run", "arguments": {"run_id": record["id"]}}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "get_stopping_range_output", "arguments": {"run_id": record["id"]}}},
            {"jsonrpc": "2.0", "id": 4, "method": "ping"},
        ]
        wire = subprocess.run(command + ["mcp"], input="".join(json.dumps(request) + "\n" for request in requests), capture_output=True, text=True, timeout=15, env=environment)
        self.assertEqual(wire.returncode, 0, wire.stderr)
        responses = [json.loads(line)["result"] for line in wire.stdout.splitlines()]
        self.assertEqual(len(responses), 4)
        for response in responses[1:3]:
            self.assertTrue(response["isError"])
            self.assertIn("provenance", response["content"][0]["text"])
        self.assertEqual(responses[3], {})
