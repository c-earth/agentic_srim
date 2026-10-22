"""Verify durable job admission, worker FIFO, cancellation and restart quarantine."""

from __future__ import annotations

from collections.abc import Callable
from threading import Event
import time
from uuid import uuid4

from src.mcp.common.contracts import ServerContext
from src.mcp.common.files import publish_json
from src.mcp.common.jobs import JobManager
from src.srim.metadata import JSONObject, _read_json
from test_contracts import Fixture


class TestJobs(Fixture):
    """Use controlled callbacks to verify scheduler semantics without native processes."""

    def execute(self: TestJobs, name: str, arguments: JSONObject, context: ServerContext, cancel: Callable[[], bool], progress: Callable[[JSONObject], None]) -> JSONObject:
        """Run self's gate-controlled fake name/arguments and return a finite outcome.

        Context is unused; progress reports native-like events, cancel is sampled
        cooperatively only for the explicitly modeled TRIM run.
        """
        self.order.append(arguments.get("label", name))
        if name == "run_submit":
            progress({"source": "adapter", "phase": "executing", "worker_ids": [], "simulation_ids": [], "data": {}})
        progress({"completed": 1, "total": 2})
        self.entered.set()
        deadline = time.monotonic() + 5
        while arguments.get("blocked") and not self.release.wait(0.01):
            if name == "run_submit" and cancel():
                return {"outcome": "cancelled", "data": {"owned_stop": True}, "references": [], "warnings": []}
            if time.monotonic() >= deadline:
                raise RuntimeError("Test gate deadline")
        return {"outcome": "succeeded", "data": {"request_id": arguments["request_id"]}, "references": [], "warnings": []}

    def test_fifo_idempotency_and_restart(self: TestJobs) -> None:
        """Serialize one worker, reject conflicting duplicates and quarantine interrupted jobs."""
        self.order, self.entered, self.release = [], Event(), Event()
        manager = JobManager(self.science, self.execute)
        manager.start()
        identity = str(uuid4())
        try:
            first = manager.submit("fake", {"worker_id": "worker_1", "label": "first", "blocked": True}, identity)
            self.assertTrue(self.entered.wait(3))
            same = manager.submit("fake", {"worker_id": "worker_1", "label": "first", "blocked": True}, identity)
            self.assertEqual(first["job_id"], same["job_id"])
            with self.assertRaises(ValueError):
                manager.submit("fake", {"worker_id": "worker_2"}, identity)
            second = manager.submit("fake", {"worker_id": "worker_1", "label": "second"}, str(uuid4()))
            self.assertEqual(manager.get(second["job_id"])["state"], "queued")
            self.release.set()
            deadline = time.monotonic() + 5
            while manager.get(second["job_id"])["state"] not in ("succeeded", "failed") and time.monotonic() < deadline:
                snapshot = manager.get(second["job_id"])
                manager.wait(second["job_id"], snapshot["sequence"], 1)
            self.assertEqual(self.order, ["first", "second"])
            self.assertEqual(manager.get(second["job_id"])["state"], "succeeded")
        finally:
            self.release.set()
            manager.shutdown()
        state_path = manager.root / "jobs" / first["job_id"] / "state.json"
        changed = _read_json(state_path)
        changed["state"] = "running"
        publish_json(state_path, changed, replace=True)
        recovered = JobManager(self.science, self.execute)
        recovered.start()
        try:
            self.assertEqual(recovered.get(first["job_id"])["state"], "interrupted")
            with self.assertRaises(ValueError):
                recovered.submit("fake", {"worker_id": "worker_1"}, str(uuid4()))
            self.assertEqual(self.order, ["first", "second"])
        finally:
            recovered.shutdown()

    def test_queued_and_cooperative_cancellation(self: TestJobs) -> None:
        """Cancel queued work before dispatch and await verified cooperative completion."""
        self.order, self.entered, self.release = [], Event(), Event()
        manager = JobManager(self.science, self.execute)
        manager.start()
        try:
            prepared = {"engine": "trim", "simulation_id": "srim_20000101_000000_1", "worker_id": "worker_1", "runtime_manifest_sha256": "0" * 64}
            first = manager.submit("run_submit", {"prepared": prepared, "blocked": True}, str(uuid4()))
            self.assertTrue(self.entered.wait(3))
            queued = manager.submit("fake", {"worker_id": "worker_1", "label": "never"}, str(uuid4()))
            self.assertTrue(manager.cancel(queued["job_id"])["accepted"])
            self.assertTrue(manager.cancel(first["job_id"])["accepted"])
            deadline = time.monotonic() + 5
            while manager.get(first["job_id"])["state"] not in ("cancelled", "failed") and time.monotonic() < deadline:
                snapshot = manager.get(first["job_id"])
                manager.wait(first["job_id"], snapshot["sequence"], 1)
            self.assertEqual(manager.get(first["job_id"])["state"], "cancelled")
            self.assertNotIn("never", self.order)
        finally:
            self.release.set()
            manager.shutdown()
