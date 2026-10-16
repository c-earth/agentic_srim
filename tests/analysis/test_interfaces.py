"""Accept unittest discovery; verify every Analysis tool over independent live stdio."""

import io
import json
import os
import queue
import subprocess
import sys
import threading
import time
from unittest.mock import patch

from analysis.builtins import bundle
from analysis.contracts import tool_definitions
from analysis.mcp import serve
from analysis_support import AnalysisTest


class InterfaceTests(AnalysisTest):
    """Construct independent server sessions; verify tool framing, recovery and durable continuation."""

    def test_wire_parse_errors_recover_without_creating_store(self) -> None:
        """Accept no inputs; reject duplicates/nonfinite/deep values and continue at ping; return nothing."""
        incoming = '{"x":1,"x":2}\n{"x":NaN}\n' + '[' * 34 + '0' + ']' * 34 + '\n'
        incoming += json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n"
        output = io.StringIO()
        with patch("sys.stdin", io.StringIO(incoming)), patch("sys.stdout", output):
            serve(self.store)
        replies = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertTrue(all(reply["error"]["code"] == -32700 for reply in replies[:3]))
        self.assertEqual(len(replies[-1]["result"]["tools"]), 13)
        self.assertFalse(self.store.path.exists())

    def test_all_thirteen_tools_over_responsive_stdio_and_fresh_cli(self) -> None:
        """Accept no inputs; register/verify/submit/read/control/assess through stdio and reread via CLI; return nothing."""
        config = self.runtime / "analysis.toml"
        config.write_text("runtime_directory = '.'\n[stores]\nlocal = '.'\n", encoding="utf-8")
        command = [sys.executable, "-B", "-m", "analysis", "--config", str(config)]
        process = subprocess.Popen(command + ["mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding="utf-8", env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "AGENTIC_SRIM_INTEGRATION": "0", "AGENTIC_SRIM_TRIM_INTEGRATION": "0"})
        responses = queue.Queue()
        def reader() -> None:
            """Accept no inputs; enqueue server lines until EOF; return nothing."""
            for line in process.stdout:
                responses.put(json.loads(line))
        threading.Thread(target=reader, daemon=True).start()
        request_id = [0]
        seen = set()
        def rpc(method: str, params: dict | None = None) -> dict:
            """Accept RPC method/params; exchange one bounded-time response and return it."""
            request_id[0] += 1
            message = {"jsonrpc": "2.0", "id": request_id[0], "method": method, "params": params or {}}
            process.stdin.write(json.dumps(message) + "\n")
            process.stdin.flush()
            result = responses.get(timeout=10)
            self.assertEqual(result["id"], request_id[0])
            return result["result"]
        def tool(name: str, arguments: dict, error: bool = False) -> dict:
            """Accept tool/arguments/expected failure; return decoded result after protocol checks."""
            seen.add(name)
            result = rpc("tools/call", {"name": name, "arguments": arguments})
            self.assertEqual(result["isError"], error, result)
            return json.loads(result["content"][0]["text"])
        def wait(job_id: str) -> dict:
            """Accept job ID; poll responsive wire status until terminal within ten seconds; return it."""
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                status = tool("get_analysis", {"analysis_id": job_id})
                if status["state"] not in ("QUEUED", "PREPARING", "RUNNING", "VERIFYING"):
                    return status
                time.sleep(0.03)
            self.fail("Wire job timeout")
        try:
            self.assertEqual(rpc("initialize")["serverInfo"]["name"], "analysis")
            self.assertEqual(len(rpc("tools/list")["tools"]), 13)
            self.assertEqual(tool("list_methods", {})["methods"], [])
            spec, files = bundle("dataset_summary")
            registered = tool("register_method", self.audit(schema_version=1, method_id="wire_summary", version=1, specification=spec, files=files))
            method = registered["method"]
            tool("get_method", method)
            verification = tool("verify_method", self.audit(schema_version=1, method=method, limits={}))
            checked = wait(verification["analysis_id"])
            self.assertEqual(checked["state"], "CHECKED", checked["error"])
            source = self.source()
            submission = self.summary_request(method, source)
            job = tool("submit_analysis", submission)
            result = wait(job["analysis_id"])
            self.assertEqual(result["state"], "CHECKED", result["error"])
            tool("get_analysis_output", {"analysis_id": result["analysis_id"], "attempt_id": result["current_attempt"],
                                         "output_sha256": result["output"]["manifest_sha256"], "name": "summary"})
            tool("list_analyses", {"kind": "analysis", "state": "CHECKED", "method_id": "wire_summary", "version": 1})
            tool("cancel_analysis", self.audit(analysis_id=result["analysis_id"]))
            tool("retry_analysis", self.audit(analysis_id=result["analysis_id"]), error=True)
            tool("recover_analyses", self.audit())
            tool("record_method_assessment", self.audit(method=method, assessment="experimental", scope={"source_ids": []}, evidence=["Non-native tests"], expected_revision=0))
            tool("get_method_history", {"method": method, "limit": 100})
            self.assertEqual(seen, {item["name"] for item in tool_definitions()})
        finally:
            process.stdin.close()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            process.stdout.close()
            stderr = process.stderr.read()
            process.stderr.close()
        self.assertEqual(process.returncode, 0, stderr)
        fresh = subprocess.run(command + ["show", job["analysis_id"]], capture_output=True, text=True, timeout=10)
        self.assertEqual(fresh.returncode, 0, fresh.stderr)
        self.assertEqual(json.loads(fresh.stdout)["state"], "CHECKED")
