"""Exercise both real SDK stdio entry points in one isolated multi-case session."""

from __future__ import annotations

import base64
from functools import partial
import asyncio
import json
import subprocess
import time
import unittest
from uuid import uuid4

from mcp import Client
from mcp.client.stdio import StdioServerParameters

from src.mcp.common.files import publish_json
from src.mcp.common.protocol import ProtocolAdapter
from src.mcp.srim_mcp.catalog import build_catalog
from src.mcp.srim_mcp.operations import read_operation, write_operation, reconcile_job_runs
from src.mcp.project_mcp.catalog import build_catalog as project_catalog
from src.mcp.project_mcp.operations import read_operation as project_read, write_operation as project_write
from mcp.types import CallToolRequestParams
from test_contracts import Fixture
from test_jobs import _request_fixture


class TestProtocol(Fixture, unittest.IsolatedAsyncioTestCase):
    """Check SDK numeric limits and journals in temporary workspaces without native runs."""

    async def test_numeric_filter_limits(self: TestProtocol) -> None:
        """Reject costly numeric filters through self's real SDK server before catalog access."""
        parameters = StdioServerParameters(command="D:\\projects\\agentic_srim\\.venv\\Scripts\\python.exe", args=["-W", "error", "-m", "src.mcp.srim_mcp", "--root", str(self.root)], cwd=str(self.repository))
        ordinary = {"field": "energy", "operator": "eq", "value": "100", "unit": "keV", "layer": None}
        async with Client(parameters, read_timeout_seconds=15) as science:
            for value in ("100", "1e1024", "1e-1024", "1" * 256):
                response = (await science.call_tool("inputs_search", {"filters": {"schema_version": 1, "clauses": [{**ordinary, "value": value}]}})).structured_content
                self.assertTrue(response["ok"], response)
                self.assertEqual(response["data"]["total"], 0)
            clauses = [
                {**ordinary, "value": "1e1000000"},
                {**ordinary, "value": "1e-1000000"},
                {**ordinary, "value": "1" * 257},
                {**ordinary, "operator": "between", "value": ["1", "1e1000000"]},
                {"field": "target.composition", "operator": "eq", "value": [{"atomic_number": 8, "mass_amu": None, "atomic_proportion": "1e1000000"}], "unit": None, "layer": "any"},
            ]
            for clause in clauses:
                with self.subTest(clause=clause):
                    response = (await science.call_tool("inputs_search", {"filters": {"schema_version": 1, "clauses": [clause]}})).structured_content
                    self.assertFalse(response["ok"], response)
                    self.assertEqual(response["error"]["code"], "invalid-argument")
                    expected = "exceeds 256 characters" if isinstance(clause["value"], str) and len(clause["value"]) > 256 else "exponent outside -1024..1024"
                    self.assertIn(expected, response["error"]["message"])
            self.assertTrue((await science.call_tool("inputs_search", {"filters": {"schema_version": 1, "clauses": [ordinary]}})).structured_content["ok"])
        self.assertFalse((self.root / "memory/knowledge/index.sqlite").exists())
        self.assertFalse((self.root / "no-native-installation").exists())

    async def test_incomplete_admission_visibility(self: TestProtocol) -> None:
        """Expose legacy partial admissions through SDK calls and reject unsafe retries."""
        arguments = {"engine": "sr", "worker_ids": ["worker_1"]}
        request = _request_fixture(self.science, operation="workers_cleanup", arguments=arguments)
        directory = self.root / "memory" / "mcp" / "srim_mcp" / "jobs" / request["id"]
        directory.mkdir(parents=True)
        publish_json(directory / "request.json", request)
        parameters = StdioServerParameters(command="D:\\projects\\agentic_srim\\.venv\\Scripts\\python.exe", args=["-W", "error", "-m", "src.mcp.srim_mcp", "--root", str(self.root)], cwd=str(self.repository))
        async with Client(parameters, read_timeout_seconds=15) as science:
            listing = (await science.call_tool("jobs_list", {})).structured_content
            self.assertTrue(listing["ok"], listing)
            self.assertEqual(listing["data"]["total"], 0)
            self.assertEqual(listing["data"]["issues_total"], 1)
            issue = listing["data"]["issues"][0]
            self.assertEqual(issue["request_id"], request["request_id"])
            self.assertEqual(issue["worker_ids"], ["worker_1"])
            self.assertTrue((await science.call_tool("capabilities_list", {"family": "sr"})).structured_content["ok"])
            for identity, message in ((request["request_id"], "incomplete admission"), (str(uuid4()), "quarantined")):
                rejected = (await science.call_tool("workers_cleanup", {**arguments, "request_id": identity})).structured_content
                self.assertFalse(rejected["ok"], rejected)
                self.assertEqual(rejected["error"]["code"], "conflict")
                self.assertIn(message, rejected["error"]["message"])
            self.assertEqual((await science.call_tool("jobs_list", {})).structured_content["data"]["total"], 0)
        self.assertEqual([path.name for path in directory.iterdir()], ["request.json"])
        self.assertFalse((self.root / "no-native-installation").exists())

    async def test_two_servers_and_multiple_cases(self: TestProtocol) -> None:
        """Verify cases, local observation reads and rejection of old-root ownership."""
        science_parameters = StdioServerParameters(command="D:\\projects\\agentic_srim\\.venv\\Scripts\\python.exe", args=["-W", "error", "-m", "src.mcp.srim_mcp", "--root", str(self.root)], cwd=str(self.repository))
        project_parameters = StdioServerParameters(command="D:\\projects\\agentic_srim\\.venv\\Scripts\\python.exe", args=["-W", "error", "-m", "src.mcp.project_mcp", "--root", str(self.root)], cwd=str(self.repository))
        async with Client(science_parameters, read_timeout_seconds=15) as science, Client(project_parameters, read_timeout_seconds=15) as project:
            self.assertEqual(len((await science.list_tools()).tools), 40)
            self.assertEqual(len((await project.list_tools()).tools), 33)
            duplicate_process = await asyncio.to_thread(subprocess.run, ["D:\\projects\\agentic_srim\\.venv\\Scripts\\python.exe", "-W", "error", "-m", "src.mcp.project_mcp", "--root", str(self.root)], cwd=str(self.repository), input=b"", stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
            self.assertEqual(duplicate_process.returncode, 1)
            self.assertEqual(duplicate_process.stdout, b"")
            discovery = await science.call_tool("capabilities_list", {"family": "sr"})
            self.assertTrue(discovery.structured_content["ok"])
            self.assertGreaterEqual(discovery.structured_content["data"]["total"], 4)
            resources = await project.list_resources()
            self.assertEqual(len(resources.resources), 3)
            rendered = await project.read_resource("project://schemas/project.case-spec.v1")
            self.assertTrue(json.loads(rendered.contents[0].text)["ok"])
            identities = []
            for title in ("A", "B"):
                request_id = str(uuid4())
                arguments = {"request_id": request_id, "spec": {"title": title, "question": "Independent task?", "status": "open", "tags": [], "notes": "", "members": []}}
                accepted = await project.call_tool("case_create", arguments)
                self.assertTrue(accepted.structured_content["ok"], accepted)
                job_id = accepted.structured_content["data"]["job_id"]
                duplicate = await project.call_tool("case_create", arguments)
                self.assertEqual(duplicate.structured_content["data"]["job_id"], job_id)
                deadline = time.monotonic() + 10
                sequence = 0
                while True:
                    job = (await project.call_tool("job_get", {"job_id": job_id, "after_sequence": sequence, "wait_seconds": 1})).structured_content["data"]
                    sequence = job["sequence"]
                    if job["state"] in ("succeeded", "failed", "interrupted", "cancelled") or time.monotonic() >= deadline:
                        break
                self.assertEqual(job["state"], "succeeded", job)
                result = await project.call_tool("artifact_read", {"reference": job["result"]})
                self.assertTrue(result.structured_content["ok"], result)
                decoded = json.loads(base64.b64decode(result.structured_content["data"]["content"]))
                identities.append(decoded["data"]["record"]["id"])
            self.assertNotEqual(*identities)
            listing = await project.call_tool("cases_list", {})
            self.assertEqual(listing.structured_content["data"]["total"], 2)
            observation = await project.call_tool("improvement_observe", {"request_id": str(uuid4()), "details": {"problem": "Synthetic protocol test", "evidence": "Local immutable event read", "sources": [], "criteria": ["quality"], "measurements": [], "uncertainty": "Fixture only"}})
            self.assertTrue(observation.structured_content["ok"], observation)
            observation_job_id = observation.structured_content["data"]["job_id"]
            deadline = time.monotonic() + 10
            sequence = 0
            while True:
                observation_job = (await project.call_tool("job_get", {"job_id": observation_job_id, "after_sequence": sequence, "wait_seconds": 1})).structured_content["data"]
                sequence = observation_job["sequence"]
                if observation_job["state"] in ("succeeded", "failed", "interrupted", "cancelled") or time.monotonic() >= deadline:
                    break
            self.assertEqual(observation_job["state"], "succeeded", observation_job)
            observation_result = await project.call_tool("artifact_read", {"reference": observation_job["result"]})
            self.assertTrue(observation_result.structured_content["ok"], observation_result)
            observation_data = json.loads(base64.b64decode(observation_result.structured_content["data"]["content"]))["data"]
            event_reference = observation_data["reference"]
            self.assertTrue(event_reference["path"].startswith(".local/workflow-improvement/events/"))
            event_read = await project.call_tool("artifact_read", {"reference": event_reference})
            self.assertTrue(event_read.structured_content["ok"], event_read)
            event_bytes = base64.b64decode(event_read.structured_content["data"]["content"])
            self.assertEqual(json.loads(event_bytes), observation_data["event"])
            forged_reference = {**event_reference, "path": event_reference["path"].replace(".local/", ".cache/", 1)}
            old_path = self.root / forged_reference["path"]
            old_path.parent.mkdir(parents=True)
            old_path.write_bytes(event_bytes)
            rejected = await project.call_tool("artifact_read", {"reference": forged_reference})
            self.assertTrue(rejected.is_error)
            self.assertFalse(rejected.structured_content["ok"])
            invalid = await project.call_tool("case_create", {"request_id": str(uuid4()), "spec": {"title": "incomplete"}})
            self.assertTrue(invalid.is_error)
            self.assertFalse((self.root / "no-native-installation").exists())

    async def test_reconcile_callback_wiring(self: TestProtocol) -> None:
        """Expose self's explicit closeout/reconcile tools through normal SDK dispatch; return None."""
        catalog = build_catalog(self.science)
        adapter = ProtocolAdapter(self.science, catalog, read_operation, write_operation, reconcile=partial(reconcile_job_runs, self.science))
        project = ProtocolAdapter(self.project, project_catalog(self.project), project_read, project_write)
        self.assertIsNone(project.jobs.reconcile)
        self.assertNotIn("run_closeout", project.catalog["tools"])
        adapter.jobs.start()
        try:
            self.assertTrue(adapter.jobs.recovery_report["closeout"]["complete"])
            identity = "srim_20261025_000000_999"
            response = await adapter.call_tool(None, CallToolRequestParams(name="run_closeout", arguments={"simulation_ids": [identity]}))
            self.assertTrue(response.structured_content["ok"])
            self.assertFalse(response.structured_content["data"]["complete"])
            self.assertEqual(response.structured_content["data"]["rows"][0]["classification"], "invalid")
            accepted = await adapter.call_tool(None, CallToolRequestParams(name="run_reconcile", arguments={"simulation_ids": [identity], "request_id": str(uuid4())}))
            self.assertTrue(accepted.structured_content["ok"], accepted.structured_content)
            job_id = accepted.structured_content["data"]["job_id"]
            deadline = time.monotonic() + 5
            while adapter.jobs.get(job_id)["state"] not in ("succeeded", "failed") and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            state = adapter.jobs.get(job_id)
            self.assertEqual(state["state"], "failed")
            self.assertIsNotNone(state["result"])
        finally:
            adapter.jobs.shutdown()
