"""Verify durable job admission, worker FIFO, cancellation and restart quarantine."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from threading import Event
import time
from unittest.mock import patch
from uuid import uuid4

from src.mcp.common.contracts import ServerContext
from src.mcp.common.files import publish_json
from src.mcp.common.jobs import JobManager, _requested_worker_ids
from src.srim.common.types import JSONObject
from src.srim.common.files import _read_json
from test_contracts import Fixture


_RENAME = Path.rename


def _request_fixture(context: ServerContext, *, operation: str = "fake", arguments: JSONObject | None = None, sequence: int = 1) -> JSONObject:
    """Return a fresh valid journal for context/operation/copied arguments/sequence.

    Perform no I/O. Each identity is fresh; invalid fixture values are left for
    production validation, and the digest uses the current compact encoding.
    """
    arguments = deepcopy(arguments) if arguments is not None else {}
    digest = hashlib.sha256(json.dumps({"operation": operation, "arguments": arguments}, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
    return {"schema_version": 1, "id": "job_" + str(uuid4()), "server": context.name, "request_id": str(uuid4()), "operation": operation, "arguments": arguments, "request_sha256": digest, "created_at": datetime.now(timezone.utc).isoformat(), "sequence": sequence}


class TestJobs(Fixture):
    """Use controlled callbacks to verify scheduler semantics without native processes."""

    def _publish_file(self: TestJobs, path: Path, document: JSONObject, *, replace: bool = False) -> JSONObject:
        """Publish document/path with replace unless self's selected filename fails.

        Return normal evidence or raise the injected OSError before that write.
        Only temporary test paths are supplied by the scoped publication patch.
        """
        if path.name == self.failed_filename:
            raise OSError("Injected admission write failure")
        return publish_json(path, document, replace=replace)

    def _rename_path(self: TestJobs, source: Path, target: Path | str) -> Path:
        """Rename source to target, failing self's selected final admission boundary.

        Return the real destination Path or raise an injected OSError; all
        ordinary per-file renames in this temporary fixture still occur.
        """
        if source.name.startswith(".admission-"):
            raise OSError("Injected admission directory rename failure")
        return _RENAME(source, target)

    def test_admission_publication_failures(self: TestJobs) -> None:
        """Retain failed initial writes/rename without dispatch, then retry and restart."""
        self.order, self.entered, self.release = [], Event(), Event()
        manager = JobManager(self.science, self.execute)
        manager.start()
        try:
            for filename in ("request.json", "state.json", "rename"):
                with self.subTest(boundary=filename):
                    request_id = str(uuid4())
                    self.failed_filename = filename
                    if filename == "rename":
                        fault = patch.object(Path, "rename", autospec=True, side_effect=self._rename_path)
                    else:
                        fault = patch("src.mcp.common.jobs.publish_json", side_effect=self._publish_file)
                    with fault, self.assertRaises(OSError) as raised:
                        manager.submit("fake", {}, request_id)
                    self.assertIn("Admission staging:", " ".join(raised.exception.__notes__))
                    self.assertEqual(len(manager.requests), 0)
                    self.assertEqual(self.order, [])
                    issue = manager.list()["issues"][-1]
                    self.assertTrue((self.root / issue["path"]).is_dir())
                    self.assertFalse((manager.root / "jobs" / issue["id"]).exists())
            accepted = manager.submit("fake", {}, request_id)
            self.assertTrue(self.entered.wait(3))
        finally:
            manager.shutdown()
        self.assertEqual(self.order, ["fake"])
        recovered = JobManager(self.science, self.execute)
        recovered.start()
        try:
            self.assertEqual(recovered.list()["issues_total"], 3)
            self.assertEqual(recovered.get(accepted["job_id"])["state"], "succeeded")
            self.assertEqual(self.order, ["fake"])
        finally:
            recovered.shutdown()

    def test_restart_after_published_admission(self: TestJobs) -> None:
        """Recover published-before-ack journals without replay, reserving their workers."""
        self.order, self.entered, self.release = [], Event(), Event()
        manager = JobManager(self.science, self.execute)
        manager.start()
        request = _request_fixture(self.science, arguments={"worker_id": "worker_1"})
        state = {"schema_version": 1, "id": request["id"], "state": "queued", "phase": "accepted", "updated_at": request["created_at"], "sequence": 0, "worker_ids": ["worker_1"], "simulation_ids": [], "native_status": None, "parser_complete": None, "cancellation": "queued-only", "progress": None, "result": None, "error": None}
        try:
            with manager.condition:
                manager._publish_admission(request, state)
            self.assertEqual(manager.requests, {})
        finally:
            manager.shutdown()
        recovered = JobManager(self.science, self.execute)
        recovered.start()
        try:
            self.assertEqual(recovered.get(request["id"])["state"], "interrupted")
            accepted = recovered.submit("fake", request["arguments"], request["request_id"])
            self.assertEqual(accepted["job_id"], request["id"])
            self.assertEqual(self.order, [])
            with self.assertRaisesRegex(ValueError, "quarantined"):
                recovered.submit("fake", {"worker_id": "worker_1"}, str(uuid4()))
            recovered.submit("fake", {"worker_id": "worker_2"}, str(uuid4()))
            self.assertTrue(self.entered.wait(3))
        finally:
            recovered.shutdown()
        self.assertEqual(self.order, ["fake"])

    def test_incomplete_admissions_are_reported(self: TestJobs) -> None:
        """List retained empty, temp-only, request-only and staged admissions safely."""
        self.order, self.entered, self.release = [], Event(), Event()
        manager = JobManager(self.science, self.execute)
        manager.start()
        try:
            accepted = manager.submit("fake", {}, str(uuid4()))
            self.assertTrue(self.entered.wait(3))
        finally:
            manager.shutdown()
        jobs = manager.root / "jobs"
        for mode in ("empty", "temp", "request", "stage"):
            request = _request_fixture(self.science, arguments={"worker_id": "worker_3"}, sequence=10)
            directory = jobs / ((".admission-" if mode == "stage" else "") + request["id"])
            directory.mkdir()
            if mode in ("temp", "stage"):
                (directory / ".mcp-fixture").write_bytes(b"unfinished")
            if mode == "request":
                publish_json(directory / "request.json", request)
        before = {path: path.read_bytes() for path in jobs.rglob("*") if path.is_file()}
        recovered = JobManager(self.science, self.execute)
        recovered.start()
        try:
            listing = recovered.list()
            self.assertEqual((listing["total"], listing["issues_total"]), (1, 4))
            self.assertEqual(recovered.get(accepted["job_id"])["state"], "succeeded")
            self.assertEqual(recovered.list(state="failed", offset=1, limit=2)["issues"], listing["issues"][1:3])
            self.assertEqual(recovered.list(state="failed")["matches"], [])
            self.assertEqual(listing["issues"], sorted(listing["issues"], key=lambda issue: issue["path"]))
            for issue in listing["issues"]:
                self.assertEqual(set(issue), {"id", "code", "path", "message", "request_id", "worker_ids"})
            listing["issues"][0]["message"] = "caller mutation"
            self.assertNotEqual(recovered.list()["issues"][0]["message"], "caller mutation")
            self.assertEqual(before, {path: path.read_bytes() for path in jobs.rglob("*") if path.is_file()})
            self.assertEqual(self.order, ["fake"])
        finally:
            recovered.shutdown()

    def test_incomplete_request_reservations(self: TestJobs) -> None:
        """Reserve legacy requests/workers/sequences while accepted retries win overlaps."""
        self.order, self.entered, self.release = [], Event(), Event()
        manager = JobManager(self.science, self.execute)
        manager.start()
        request_id = str(uuid4())
        try:
            accepted = manager.submit("fake", {}, request_id)
            self.assertTrue(self.entered.wait(3))
        finally:
            manager.shutdown()
        incomplete = []
        for sequence, shared in ((1, True), (50, False), (50, False)):
            request = _request_fixture(self.science, arguments={"worker_id": "worker_1", "old_arguments": True}, sequence=sequence)
            request["request_id"] = request_id if shared else incomplete[-1]["request_id"] if incomplete and sequence == incomplete[-1]["sequence"] else str(uuid4())
            directory = manager.root / "jobs" / request["id"]
            directory.mkdir()
            publish_json(directory / "request.json", request)
            incomplete.append(request)
        recovered = JobManager(self.science, self.execute)
        recovered.start()
        try:
            self.assertEqual(recovered.submit("fake", {}, request_id)["job_id"], accepted["job_id"])
            with self.assertRaisesRegex(ValueError, "incomplete admission"):
                recovered.submit("fake", incomplete[-1]["arguments"], incomplete[-1]["request_id"])
            with self.assertRaisesRegex(ValueError, "quarantined"):
                recovered.submit("fake", {"worker_id": "worker_1"}, str(uuid4()))
            self.assertEqual(recovered.list()["issues_total"], 3)
            issue = next(issue for issue in recovered.list()["issues"] if issue["id"] == incomplete[0]["id"])
            self.assertIn(accepted["job_id"], issue["message"])
            new = recovered.submit("fake", {"worker_id": "worker_2"}, str(uuid4()))
            self.assertEqual(recovered.requests[new["job_id"]]["sequence"], 51)
        finally:
            recovered.shutdown()
        self.assertEqual(_requested_worker_ids("batch_submit", {"tasks": [{"worker_id": "worker_2"}, {"worker_id": "worker_2"}]}), ["worker_2"])
        for operation, arguments in (("batch_submit", {"tasks": [None]}), ("workers_cleanup", {"worker_ids": "worker_1"}), ("fake", {"prepared": []}), ("fake", {"worker_id": "worker_01"})):
            with self.subTest(operation=operation, arguments=arguments), self.assertRaises(ValueError):
                _requested_worker_ids(operation, arguments)

    def test_malformed_journals_still_abort_startup(self: TestJobs) -> None:
        """Reject malformed committed evidence and release each failed server lease."""
        self.order, self.entered, self.release = [], Event(), Event()
        for mode in ("request", "hash", "state-only", "result", "events", "state"):
            with self.subTest(mode=mode):
                manager = JobManager(self.science, self.execute)
                manager.root = self.root / "memory" / "mcp" / ("malformed-" + mode)
                jobs = manager.root / "jobs"
                jobs.mkdir(parents=True)
                request = _request_fixture(self.science)
                directory = jobs / request["id"]
                directory.mkdir()
                if mode != "state-only":
                    if mode == "request":
                        request.pop("operation")
                    elif mode == "hash":
                        request["request_sha256"] = "0" * 64
                    publish_json(directory / "request.json", request)
                if mode in ("state-only", "state"):
                    publish_json(directory / "state.json", {})
                if mode == "result":
                    publish_json(directory / "result.json", {})
                if mode == "events":
                    (directory / "events").mkdir()
                for attempt in range(2):
                    with self.subTest(attempt=attempt), self.assertRaises((ValueError, OSError)):
                        manager.start()
                    self.assertIsNone(manager.lease)
        self.assertEqual(self.order, [])

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
