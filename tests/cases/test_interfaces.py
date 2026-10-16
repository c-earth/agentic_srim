"""Accept unittest discovery; verify Case MCP validation, wire recovery and fresh CLI sessions.

Subprocess tests explicitly disable native gates and suppress bytecode. Requests
use synthetic case data and independent stores; simulation modules are absent.
"""

import io
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from case.config import Settings
from case.contracts import SCHEMAS, tool_definitions
from case.mcp import call_tool, handle, serve
from cases.support import CaseTest, ROOT


class ContractTests(CaseTest):
    """Construct isolated tests; verify bounded schemas and server-side validation."""

    def test_invalid_arguments_never_mutate_or_create_store(self) -> None:
        """Accept no inputs; reject missing/extra/wrong fields before storage writes; return nothing."""
        valid = self.audit(title="Valid", original_request="Review")
        invalid = [{**valid, "unknown": True}, {**valid, "title": " "},
                   {**valid, "content": {"budgets": {"run_limit": True}}},
                   {**valid, "content": {"budgets": {"wall_time_seconds": float("nan")}}},
                   {**valid, "content": {"approvals": [{"scope": "Run", "decision": "yes", "source": "User"}]}},
                   {**valid, "content": {"constraints": "not a list"}},
                   {**valid, "content": {"plan": ["Step"] * 101}}, {}]
        for arguments in invalid:
            with self.subTest(arguments=arguments):
                result = call_tool("create_case", arguments, self.store)
                self.assertTrue(result["isError"])
                self.assertEqual(json.loads(result["content"][0]["text"])["error"]["code"], "invalid_input")
        self.assertFalse(self.store.path.exists())

    def test_pagination_ids_and_revisions_are_strictly_bounded(self) -> None:
        """Accept no inputs; reject invalid IDs/booleans/unbounded pagination; return nothing."""
        calls = [("list_cases", {"limit": value}) for value in (0, 101, True, 1.5)]
        calls.extend(("get_case", {"case_id": value}) for value in ("../x", "A" * 32, "a" * 31, 1))
        calls.extend(("get_case", {"case_id": "a" * 32, "revision": value}) for value in (0, True, "1"))
        calls.extend((("list_cases", {"include_archived": 1}), ("search_cases", {"query": " "})))
        for name, arguments in calls:
            with self.subTest(name=name, arguments=arguments):
                self.assertTrue(call_tool(name, arguments, self.store)["isError"])
        self.assertFalse(self.store.path.exists())

    def test_discovery_has_ten_strict_tools_and_accurate_effect_hints(self) -> None:
        """Accept no inputs; verify discoverable tool schemas and read-only effects; return nothing."""
        definitions = tool_definitions()
        self.assertEqual(len(definitions), 10)
        self.assertEqual({item["name"] for item in definitions}, set(SCHEMAS))
        for item in definitions:
            self.assertFalse(item["inputSchema"]["additionalProperties"])
            self.assertTrue(item["annotations"]["idempotentHint"])
        lookup = {item["name"]: item for item in definitions}
        self.assertTrue(lookup["get_case"]["annotations"]["readOnlyHint"])
        self.assertFalse(lookup["create_case"]["annotations"]["readOnlyHint"])
        self.assertNotIn("agentic_srim", Path(sys.modules["case.storage"].__file__).read_text(encoding="utf-8"))

    def test_case_config_is_independent_strict_and_file_relative(self) -> None:
        """Accept no inputs; load case-only paths and reject simulator/unknown settings; return nothing."""
        config = self.runtime / "case.toml"
        config.write_text("runtime_directory = 'data'\n", encoding="utf-8")
        self.assertEqual(Settings.load(config).runtime_directory, self.runtime / "data")
        self.assertFalse((self.runtime / "data").exists())
        for content in ("runtime_directory = ''", "runtime_directory = 1", "srim_directory = 'x'", "runtime_directory = 'data'\nunknown = 1"):
            config.write_text(content, encoding="utf-8")
            with self.subTest(content=content), self.assertRaises(ValueError):
                Settings.load(config)


class ProtocolTests(CaseTest):
    """Construct isolated tests; verify protocol recovery and state-change boundaries."""

    def test_malformed_messages_notifications_and_unknown_methods_do_not_write(self) -> None:
        """Accept no inputs; reject invalid RPC and ignore notifications without side effects; return nothing."""
        for value in ([], None, {"jsonrpc": "1.0", "id": 1, "method": "ping"},
                      {"jsonrpc": "2.0", "id": True, "method": "ping"}):
            self.assertEqual(handle(value, self.store)["error"]["code"], -32600)
        self.assertEqual(handle({"jsonrpc": "2.0", "id": 2, "method": "unknown"}, self.store)["error"]["code"], -32601)
        self.assertEqual(handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": []}, self.store)["error"]["code"], -32602)
        notify = {"jsonrpc": "2.0", "method": "tools/call", "params": {
            "name": "create_case", "arguments": self.audit(title="Ignored", original_request="Notification")}}
        self.assertIsNone(handle(notify, self.store))
        self.assertFalse(self.store.path.exists())

    def test_parse_errors_duplicate_keys_oversized_lines_and_tool_errors_recover(self) -> None:
        """Accept no inputs; recover within one session after multiple invalid inputs; return nothing."""
        incoming = "{broken\n{\"x\":NaN}\n{\"x\":1,\"x\":2}\n" + "x" * 524289 + "\n"
        incoming += json.dumps({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "unknown"}}) + "\n"
        incoming += json.dumps({"jsonrpc": "2.0", "id": "healthy", "method": "ping"}) + "\n"
        output = io.StringIO()
        with patch("sys.stdin", io.StringIO(incoming)), patch("sys.stdout", output):
            serve(self.store)
        responses = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(responses), 6)
        self.assertTrue(all(item["error"]["code"] == -32700 for item in responses[:4]))
        self.assertTrue(responses[4]["result"]["isError"])
        self.assertEqual(responses[-1], {"jsonrpc": "2.0", "id": "healthy", "result": {}})
        self.assertFalse(self.store.path.exists())

    def test_storage_failures_are_coded_and_next_call_succeeds(self) -> None:
        """Accept no inputs; report a storage error without terminating the service; return nothing."""
        import sqlite3
        with patch.object(self.store, "perform", side_effect=sqlite3.OperationalError("locked")):
            result = call_tool("list_cases", {}, self.store)
        self.assertEqual(json.loads(result["content"][0]["text"])["error"]["code"], "storage_error")
        self.assertFalse(call_tool("list_cases", {}, self.store)["isError"])


class SubprocessTests(CaseTest):
    """Construct isolated tests; verify real UTF-8 stdio sessions and CLI continuation."""

    def command(self) -> tuple[list[str], dict]:
        """Accept no inputs; return an independent CLI command and non-native UTF-8 environment."""
        config = self.runtime / "case.toml"
        config.write_text(f"runtime_directory = '{self.runtime.as_posix()}'\n", encoding="utf-8")
        environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(ROOT / "src"),
                       "AGENTIC_SRIM_INTEGRATION": "0", "AGENTIC_SRIM_TRIM_INTEGRATION": "0"}
        return [sys.executable, "-B", "-m", "case", "--config", str(config)], environment

    def exchange(self, calls: list[tuple[str, dict]]) -> list[dict]:
        """Accept tool requests; run a fresh bounded stdio session and return successful results."""
        command, environment = self.command()
        messages = [{"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "regression", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}]
        messages.extend({"jsonrpc": "2.0", "id": index + 2, "method": "tools/call", "params": {
            "name": name, "arguments": arguments}} for index, (name, arguments) in enumerate(calls))
        response = subprocess.run(command + ["mcp"], input="".join(json.dumps(item) + "\n" for item in messages),
                                  capture_output=True, encoding="utf-8", timeout=20, env=environment, cwd=ROOT)
        self.assertEqual(response.returncode, 0, response.stderr)
        replies = [json.loads(line) for line in response.stdout.splitlines()]
        self.assertEqual(len(replies), len(calls) + 2)
        self.assertEqual(replies[0]["result"]["serverInfo"]["name"], "case")
        self.assertEqual(len(replies[1]["result"]["tools"]), 10)
        results = []
        for reply in replies[2:]:
            self.assertFalse(reply["result"]["isError"], reply)
            results.append(json.loads(reply["result"]["content"][0]["text"]))
        return results

    def test_all_tools_across_fresh_processes_and_human_cli(self) -> None:
        """Accept no inputs; create/checkpoint/link/branch/search/history/overview after restart; return nothing."""
        creation = self.audit(title="氦 profile", original_request="Compare reusable archived profiles")
        first = self.exchange([("create_case", creation)])[0]["case"]
        self.exchange([("create_case", self.audit(title="Later case", original_request="Another question"))])
        saved = self.exchange([("record_case_progress", self.audit(case_id=first["id"], expected_revision=1,
            progress={"plan": ["Inspect archive"], "constraints": ["No execution"], "next_action": "Review"}))])[0]["case"]
        artifact = {"owner": "analysis", "artifact_id": "analysis-older/result-v1", "kind": "table",
                    "purpose": "Cross-case reuse", "provenance": "Synthetic owner reference"}
        linked = self.exchange([("link_case_artifact", self.audit(case_id=first["id"], expected_revision=2,
                                                                   artifact=artifact))])[0]["case"]
        archived = self.exchange([("update_case", self.audit(case_id=first["id"], expected_revision=3,
                                                              patch={"archived": True}))])[0]["case"]
        results = self.exchange([
            ("get_case", {"case_id": first["id"]}),
            ("get_case", {"case_id": first["id"], "revision": 2}),
            ("list_cases", {"include_archived": True}),
            ("search_cases", {"query": "氦", "include_archived": True}),
            ("get_case_history", {"case_id": first["id"]}),
            ("get_case_overview", {"case_id": first["id"]}),
            ("branch_case", self.audit(parent_case_id=first["id"], parent_revision=3,
                title="New branch", original_request="Related investigation")),
        ])
        self.assertEqual(results[0]["case"], archived)
        self.assertEqual(results[1]["case"], saved)
        self.assertEqual(len(results[2]["cases"]), 2)
        self.assertEqual(results[3]["cases"][0]["id"], first["id"])
        self.assertEqual(len(results[4]["history"]), 4)
        self.assertIn("No execution", results[5]["markdown"])
        self.assertEqual(results[6]["case"]["artifacts"], linked["artifacts"])
        command, environment = self.command()
        response = subprocess.run(command + ["overview", first["id"], "--revision", "2"],
                                  capture_output=True, encoding="utf-8", timeout=20, env=environment, cwd=ROOT)
        self.assertEqual(response.returncode, 0, response.stderr)
        self.assertIn("Revision: 2", response.stdout)
        self.assertIn("氦", response.stdout)
        self.assertFalse((self.runtime / "metadata.sqlite").exists())
        self.assertFalse((self.runtime / "workers").exists())

    def test_fresh_process_imports_no_simulator_modules(self) -> None:
        """Accept no inputs; import Case MCP independently of simulator modules; return nothing."""
        _, environment = self.command()
        response = subprocess.run([sys.executable, "-B", "-c",
            "import case.mcp, sys; assert not any(k == 'agentic_srim' or k.startswith('agentic_srim.') for k in sys.modules)"],
            capture_output=True, encoding="utf-8", timeout=20, env=environment, cwd=ROOT)
        self.assertEqual(response.returncode, 0, response.stderr)

    def test_process_exit_rolls_back_and_retry_can_continue(self) -> None:
        """Accept no inputs; interrupt a real SQLite writer and recover its retry safely; return nothing."""
        record = self.create()
        request = self.audit(case_id=record["id"], expected_revision=1, patch={"next_action": "Inspect"})
        _, environment = self.command()
        code = "\n".join((
            "import json, os, sys",
            "from pathlib import Path",
            "from case.storage import Store",
            "store = Store(Path(sys.argv[1]))",
            "save = store._save",
            "store._save = lambda *args: (save(*args), os._exit(23))",
            "store.perform('update_case', json.loads(sys.argv[2]))",
        ))
        response = subprocess.run([sys.executable, "-B", "-c", code, str(self.runtime), json.dumps(request)],
                                  capture_output=True, encoding="utf-8", timeout=20, env=environment, cwd=ROOT)
        self.assertEqual(response.returncode, 23, response.stderr)
        self.assertEqual(self.store.perform("get_case", {"case_id": record["id"]})["case"], record)
        recovered = self.store.perform("update_case", request)["case"]
        self.assertEqual(recovered["revision"], 2)
        self.assertEqual(len(self.store.perform("get_case_history", {"case_id": record["id"]})["history"]), 2)
