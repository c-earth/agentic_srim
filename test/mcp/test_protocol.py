"""Exercise both real SDK stdio entry points in one isolated multi-case session."""

from __future__ import annotations

import base64
import asyncio
import json
import subprocess
import time
import unittest
from uuid import uuid4

from mcp import Client
from mcp.client.stdio import StdioServerParameters

from test_contracts import Fixture


class TestProtocol(Fixture, unittest.IsolatedAsyncioTestCase):
    """Use SDK clients against synthetic workspaces; never launch native SRIM/TRIM."""

    async def test_two_servers_and_multiple_cases(self: TestProtocol) -> None:
        """Verify cases, local observation reads and rejection of old-root ownership."""
        science_parameters = StdioServerParameters(command="D:\\projects\\agentic_srim\\.venv\\Scripts\\python.exe", args=["-W", "error", "-m", "src.mcp.srim_mcp", "--root", str(self.root)], cwd=str(self.repository))
        project_parameters = StdioServerParameters(command="D:\\projects\\agentic_srim\\.venv\\Scripts\\python.exe", args=["-W", "error", "-m", "src.mcp.project_mcp", "--root", str(self.root)], cwd=str(self.repository))
        async with Client(science_parameters, read_timeout_seconds=15) as science, Client(project_parameters, read_timeout_seconds=15) as project:
            self.assertEqual(len((await science.list_tools()).tools), 38)
            self.assertEqual(len((await project.list_tools()).tools), 32)
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
