"""Persist idempotent adapter jobs and schedule bounded worker-aware background work."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from datetime import datetime
from functools import partial
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from threading import Condition, Event, RLock, Thread
import time
from uuid import UUID, uuid4

from src.backend.records import _record_time
from src.mcp.common.contracts import ServerContext, canonical_bytes, error_response, load_json
from src.mcp.common.files import acquire_lock, publish_json, read_verified_chunk, safe_path
from src.srim.common.types import JSONObject
from src.srim.common.files import _read_regular_bytes


Progress = Callable[[JSONObject], None]
Cancel = Callable[[], bool]
WriteOperation = Callable[[str, JSONObject, ServerContext, Cancel, Progress], JSONObject]
_TERMINAL = frozenset({"succeeded", "failed", "cancelled", "interrupted"})


def _validate_job_request(request: JSONObject, server: str, job_id: str) -> None:
    """Validate request's schema, identity, server/job_id and argument digest.

    Return None without I/O; malformed fields, nonfinite arguments or mismatched
    evidence raise ValueError. Cross-journal uniqueness belongs to the owner.
    """
    fields = {"schema_version", "id", "server", "request_id", "operation", "arguments", "request_sha256", "created_at", "sequence"}
    if not isinstance(request, dict) or set(request) != fields or type(request["schema_version"]) is not int or request["schema_version"] != 1:
        raise ValueError("Malformed durable job request schema")
    if re.fullmatch(r"job_[0-9a-f-]{36}", job_id) is None or str(UUID(job_id[4:])) != job_id[4:] or request["id"] != job_id or request["server"] != server:
        raise ValueError("Malformed durable job request identity")
    if not isinstance(request["request_id"], str) or str(UUID(request["request_id"])) != request["request_id"] or type(request["sequence"]) is not int or request["sequence"] < 1:
        raise ValueError("Malformed durable job request UUID or sequence")
    if not isinstance(request["created_at"], str) or datetime.fromisoformat(request["created_at"]).utcoffset() is None or not isinstance(request["operation"], str) or not request["operation"] or not isinstance(request["arguments"], dict):
        raise ValueError("Malformed durable job request timestamp or arguments")
    canonical_bytes(request["arguments"])
    digest = hashlib.sha256(json.dumps({"operation": request["operation"], "arguments": request["arguments"]}, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
    if digest != request["request_sha256"]:
        raise ValueError("Malformed durable job request hash")


def _requested_worker_ids(operation: str, arguments: JSONObject) -> list[str]:
    """Extract operation's explicit worker reservations from arguments without I/O.

    Return distinct canonical worker IDs in encounter order. Invalid selected
    containers or worker identities raise ValueError; mutate no caller data.
    """
    workers = []
    if arguments.get("worker_id") is not None:
        workers.append(arguments["worker_id"])
    if "prepared" in arguments:
        prepared = arguments["prepared"]
        if not isinstance(prepared, dict) or "worker_id" not in prepared:
            raise ValueError("Invalid prepared worker reservation")
        workers.append(prepared["worker_id"])
    if operation == "batch_submit":
        tasks = arguments.get("tasks")
        if not isinstance(tasks, list) or any(not isinstance(task, dict) or "worker_id" not in task for task in tasks):
            raise ValueError("Invalid batch worker reservations")
        workers.extend(task["worker_id"] for task in tasks)
    if operation == "workers_cleanup":
        if not isinstance(arguments.get("worker_ids"), list):
            raise ValueError("Invalid cleanup worker reservations")
        workers.extend(arguments["worker_ids"])
    for worker in workers:
        if not isinstance(worker, str) or re.fullmatch(r"worker_[1-9][0-9]*", worker) is None:
            raise ValueError("Invalid reserved worker identity")
    return list(dict.fromkeys(workers))


class JobManager:
    """Own context's durable jobs, executor callback and bounded thread scheduler.

    Construct with context and execute; start acquires ownership and reconciles
    journals. Public snapshots are copied, and native metadata is never rewritten.
    Admission issues describe retained partial directories; incomplete request
    maps reserve legacy identities independently of accepted requests/states.
    """

    def __init__(self: JobManager, context: ServerContext, execute: WriteOperation) -> None:
        """Store context/execute and initialize queue coordination; return None without I/O."""
        self.context = context
        self.execute = execute
        self.root = context.paths.root / "memory" / "mcp" / context.name
        self.condition = Condition(RLock())
        self.states: dict[str, JSONObject] = {}
        self.requests: dict[str, JSONObject] = {}
        self.request_ids: dict[str, str] = {}
        self.admission_issues: list[JSONObject] = []
        self.incomplete_requests: dict[str, JSONObject] = {}
        self.incomplete_request_ids: dict[str, list[str]] = {}
        self.cancels: dict[str, Event] = {}
        self.running: dict[str, int] = {}
        self.quarantined: set[str] = set()
        self.threads: list[Thread] = []
        self.dispatcher: Thread | None = None
        self.lease = None
        self.stopping = False

    def _register_incomplete_admission(self: JobManager, directory: Path, request: JSONObject | None, *, staged: bool) -> None:
        """Record safely classified directory/request under owner synchronization.

        Staged requires no request. Return None after deduplicating diagnostics
        and reserving visible request UUIDs/workers; invalid inputs raise
        ValueError. Retain disk bytes and never schedule or fabricate state.
        """
        identity = directory.name.removeprefix(".admission-") if staged else directory.name
        if staged and request is not None:
            raise ValueError("Unpublished staging cannot reserve a committed request")
        workers = []
        if request is not None:
            _validate_job_request(request, self.context.name, identity)
            workers = _requested_worker_ids(request["operation"], request["arguments"])
            self.incomplete_requests[identity] = deepcopy(request)
            identities = self.incomplete_request_ids.setdefault(request["request_id"], [])
            if identity not in identities:
                identities.append(identity)
            self.quarantined.update(workers)
        message = "Unpublished admission staging retained; it was not scheduled." if staged else "Incomplete admission retained without replay; explicit reconciliation is required."
        if request is not None and request["request_id"] in self.request_ids:
            message += " Accepted request identity: " + self.request_ids[request["request_id"]] + "."
        relative = directory.relative_to(self.context.paths.root).as_posix()
        issue = {"id": identity, "code": "incomplete-admission", "path": relative, "message": message, "request_id": None if request is None else request["request_id"], "worker_ids": workers}
        self.admission_issues[:] = [entry for entry in self.admission_issues if entry["path"] != relative]
        self.admission_issues.append(issue)

    def _publish_admission(self: JobManager, request: JSONObject, state: JSONObject) -> None:
        """Publish validated request/state together under the caller's server lease.

        Return None after same-parent directory rename. Preserve failed staging,
        report it and annotate the original exception; never register accepted
        maps or wake dispatch. Filesystem/validation errors propagate unchanged.
        """
        jobs = self.root / "jobs"
        target = safe_path(jobs, request["id"])
        staging = safe_path(jobs, ".admission-" + request["id"])
        try:
            staging.mkdir()
            publish_json(staging / "request.json", request)
            publish_json(staging / "state.json", state)
            if os.path.lexists(target):
                raise FileExistsError(str(target))
            staging.rename(target)
        except BaseException as error:
            error.add_note("Admission staging: " + str(staging))
            try:
                if staging.is_dir():
                    self._register_incomplete_admission(staging, None, staged=True)
            except (OSError, ValueError) as diagnostic_error:
                error.add_note("Admission diagnostic failed: " + str(diagnostic_error))
            raise

    def start(self: JobManager) -> None:
        """Acquire singleton ownership, verify journals and quarantine unfinished jobs.

        Start the dispatcher, returning None. Malformed state or contention raises
        ValueError/OSError with evidence retained and no automatic re-execution.
        Report recognized incomplete admissions separately, reserving visible
        request-only UUIDs/workers while keeping complete retries authoritative.
        """
        if self.lease is not None:
            raise ValueError("Job manager already started")
        self.lease = acquire_lock(self.root / "server.lock", timeout_seconds=0)
        self.lease.__enter__()
        try:
            jobs = safe_path(self.root, "jobs")
            jobs.mkdir(exist_ok=True)
            incomplete = []
            for candidate in sorted(jobs.iterdir()):
                staged = candidate.name.startswith(".admission-")
                if candidate.name.startswith(".") and not staged:
                    continue
                identity = candidate.name.removeprefix(".admission-") if staged else candidate.name
                if not identity.startswith("job_") or str(UUID(identity[4:])) != identity[4:]:
                    raise ValueError("Malformed job directory")
                directory = safe_path(jobs, candidate.name)
                if not directory.is_dir():
                    raise ValueError("Job allocation must be a directory")
                if staged:
                    incomplete.append((directory, None, True))
                    continue
                request_path = safe_path(directory, "request.json")
                state_path = safe_path(directory, "state.json")
                if not os.path.lexists(state_path):
                    request = load_json(_read_regular_bytes(request_path)) if os.path.lexists(request_path) else None
                    if request is not None:
                        _validate_job_request(request, self.context.name, identity)
                        _requested_worker_ids(request["operation"], request["arguments"])
                    for entry in directory.iterdir():
                        if entry.name == "request.json" and request is not None:
                            continue
                        temporary = safe_path(directory, entry.name)
                        if re.fullmatch(r"\.mcp-[a-z0-9_]+", entry.name) is None or not stat.S_ISREG(temporary.stat().st_mode):
                            raise ValueError("Unexpected evidence in incomplete job admission")
                    incomplete.append((directory, request, False))
                    continue
                request = load_json(_read_regular_bytes(directory / "request.json"))
                state = load_json(_read_regular_bytes(directory / "state.json"))
                _validate_job_request(request, self.context.name, directory.name)
                state_fields = {"schema_version", "id", "state", "phase", "updated_at", "sequence", "worker_ids", "simulation_ids", "native_status", "parser_complete", "cancellation", "progress", "result", "error"}
                if set(state) != state_fields or type(state["schema_version"]) is not int or state["schema_version"] != 1 or state["id"] != directory.name or request["request_id"] in self.request_ids or request["sequence"] in {item["sequence"] for item in self.requests.values()} or state["state"] not in _TERMINAL | {"queued", "running", "cancelling"} or type(state["sequence"]) is not int or state["sequence"] < 0 or not isinstance(state["worker_ids"], list) or not isinstance(state["simulation_ids"], list):
                    raise ValueError("Malformed or conflicting durable job journal")
                if datetime.fromisoformat(request["created_at"]).utcoffset() is None or datetime.fromisoformat(state["updated_at"]).utcoffset() is None or state["phase"] not in (None, "accepted", "allocating", "resolving", "preparing", "executing", "preserving", "parsing", "indexing", "cleanup", "finalizing", "finished") or state["cancellation"] not in ("queued-only", "cooperative", "timeout-only", "unsupported", "complete") or state["native_status"] not in (None, "created", "running", "succeeded", "failed") or state["parser_complete"] is not None and type(state["parser_complete"]) is not bool:
                    raise ValueError("Invalid persisted job scalar state")
                for worker in state["worker_ids"]:
                    if not isinstance(worker, str) or not worker.startswith("worker_") or not worker[7:].isdigit() or int(worker[7:]) < 1 or worker[7:] != str(int(worker[7:])):
                        raise ValueError("Invalid persisted worker identity")
                for simulation in state["simulation_ids"]:
                    if not isinstance(simulation, str) or not simulation.startswith("srim_") or len(simulation.split("_")) != 4 or not all(value.isdigit() for value in simulation.split("_")[1:]):
                        raise ValueError("Invalid persisted simulation identity")
                self.requests[directory.name] = request
                self.states[directory.name] = state
                self.request_ids[request["request_id"]] = directory.name
                self.cancels[directory.name] = Event()
                if state["state"] not in _TERMINAL:
                    self.transition(directory.name, {"state": "interrupted", "phase": "finished", "cancellation": "complete"})
                if self.states[directory.name]["state"] == "interrupted":
                    self.quarantined.update(state["worker_ids"])
                for key in ("result", "error"):
                    reference = state[key]
                    if reference is not None:
                        if not isinstance(reference, dict) or set(reference) != {"kind", "id", "revision", "path", "sha256", "size_bytes"} or reference["kind"] != "job" or reference["id"] != directory.name or reference["revision"] is not None or reference["path"] != (directory / ("result.json" if key == "result" else "error.json")).relative_to(self.context.paths.root).as_posix():
                            raise ValueError("Invalid persisted job result reference")
                        read_verified_chunk(safe_path(self.context.paths.root, reference["path"]), reference["sha256"], reference["size_bytes"], offset=0, length=1)
            for directory, request, staged in incomplete:
                self._register_incomplete_admission(directory, request, staged=staged)
            self.dispatcher = Thread(target=self.dispatch, name=self.context.name + "-scheduler", daemon=False)
            self.dispatcher.start()
        except BaseException:
            self.lease.__exit__(*sys.exc_info())
            self.lease = None
            raise

    def submit(self: JobManager, operation: str, arguments: JSONObject, request_id: str) -> JSONObject:
        """Durably accept operation/arguments once per request_id and return JobAccepted.

        Validate canonical UUID, finite arguments, queue/slot/worker reservations;
        conflicting duplicate or unsafe admission raises before dispatch.
        Reject reserved incomplete request IDs; publish both initial files in
        staging before exposing accepted maps or notifying the scheduler.
        """
        if str(UUID(request_id)) != request_id or not isinstance(operation, str) or not operation or not isinstance(arguments, dict):
            raise ValueError("Invalid operation/request identity")
        canonical_bytes(arguments)
        digest = hashlib.sha256(json.dumps({"operation": operation, "arguments": arguments}, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
        with self.condition:
            if request_id in self.request_ids:
                identity = self.request_ids[request_id]
                if self.requests[identity]["operation"] != operation or self.requests[identity]["request_sha256"] != digest:
                    raise ValueError("conflict: request ID already binds different arguments")
                return {"job_id": identity, "state": self.states[identity]["state"], "operation": operation, "request_sha256": digest}
            if request_id in self.incomplete_request_ids:
                raise ValueError("conflict: request ID has an incomplete admission requiring reconciliation")
            if self.lease is None or self.stopping:
                raise ValueError("unavailable: server is shutting down")
            if sum(state["state"] == "queued" for state in self.states.values()) >= self.context.limits["max_pending"]:
                raise ValueError("queue-full: queued job capacity exhausted")
            workers = _requested_worker_ids(operation, arguments)
            for worker in workers:
                if worker in self.quarantined:
                    raise ValueError("conflict: worker quarantined by an interrupted job or incomplete admission")
            slots = arguments.get("max_workers", 1) if operation == "batch_submit" else 1
            if type(slots) is not int or not 1 <= slots <= self.context.limits["max_parallel"]:
                raise ValueError("Invalid job parallel slot reservation")
            identity = "job_" + str(uuid4())
            sequence = max((request["sequence"] for request in (*self.requests.values(), *self.incomplete_requests.values())), default=0) + 1
            now = _record_time(self.context.config, None).isoformat()
            request = {"schema_version": 1, "id": identity, "server": self.context.name, "request_id": request_id, "operation": operation, "arguments": deepcopy(arguments), "request_sha256": digest, "created_at": now, "sequence": sequence}
            state = {"schema_version": 1, "id": identity, "state": "queued", "phase": "accepted", "updated_at": now, "sequence": 0, "worker_ids": list(dict.fromkeys(workers)), "simulation_ids": [], "native_status": None, "parser_complete": None, "cancellation": "queued-only", "progress": None, "result": None, "error": None}
            self._publish_admission(request, state)
            self.requests[identity] = request
            self.states[identity] = state
            self.request_ids[request_id] = identity
            self.cancels[identity] = Event()
            self.condition.notify_all()
            return {"job_id": identity, "state": "queued", "operation": operation, "request_sha256": digest}

    def get(self: JobManager, job_id: str, *, after_sequence: int = 0) -> JSONObject:
        """Return copied job snapshot and up to 100 durable events after after_sequence.

        Reject unknown IDs, bad sequence or changed stored request/state evidence.
        Read only; no native status or progress is inferred from elapsed time.
        """
        if type(after_sequence) is not int or after_sequence < 0:
            raise ValueError("Invalid progress sequence")
        with self.condition:
            if job_id not in self.states:
                raise ValueError("not-found: unknown job")
            target = self.root / "jobs" / job_id
            if load_json(_read_regular_bytes(target / "request.json")) != self.requests[job_id] or load_json(_read_regular_bytes(target / "state.json")) != self.states[job_id]:
                raise ValueError("integrity: journal changed outside owner")
            result = deepcopy(self.states[job_id])
            result["operation"] = self.requests[job_id]["operation"]
            result["request_sha256"] = self.requests[job_id]["request_sha256"]
            events = []
            for path in sorted((target / "events").glob("*.json")):
                if int(path.stem) > after_sequence:
                    events.append(load_json(_read_regular_bytes(path)))
                    if len(events) == 100:
                        break
            result["events"] = events
            return result

    def list(self: JobManager, *, state: str | None = None, offset: int = 0, limit: int = 100) -> JSONObject:
        """Return this server's creation-ordered snapshots for state/offset/limit.

        Validate state and paging, read no other server, and raise on malformed
        arguments or journal evidence rather than hiding corruption. Include
        copied admission issues independently paged by offset/limit, regardless
        of state; issues_total counts every retained incomplete admission.
        """
        if state is not None and state not in _TERMINAL | {"queued", "running", "cancelling"} or type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Invalid job listing query")
        with self.condition:
            identities = [identity for identity in sorted(self.requests, key=lambda item: self.requests[item]["sequence"]) if state is None or self.states[identity]["state"] == state]
            issues = sorted(self.admission_issues, key=lambda issue: issue["path"])
            return {"total": len(identities), "offset": offset, "limit": limit, "matches": [self.get(identity, after_sequence=self.states[identity]["sequence"]) for identity in identities[offset:offset + limit]], "issues_total": len(issues), "issues": deepcopy(issues[offset:offset + limit])}

    def wait(self: JobManager, job_id: str, after_sequence: int, timeout_seconds: float) -> JSONObject:
        """Wait at most timeout_seconds for a newer job sequence and return its snapshot.

        Bounds are finite 0–30 seconds; shutdown and terminal state wake promptly.
        Unknown identity/invalid bounds raises ValueError; no writes or engine calls.
        """
        if type(timeout_seconds) not in (int, float) or not 0 <= timeout_seconds <= 30 or type(after_sequence) is not int or after_sequence < 0:
            raise ValueError("Invalid job wait bounds")
        deadline = time.monotonic() + timeout_seconds
        with self.condition:
            if job_id not in self.states:
                raise ValueError("not-found: unknown job")
            while not self.stopping and self.states[job_id]["state"] not in _TERMINAL and self.states[job_id]["sequence"] <= after_sequence:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.condition.wait(remaining)
            return self.get(job_id, after_sequence=after_sequence)

    def cancel(self: JobManager, job_id: str) -> JSONObject:
        """Cancel queued work or signal only an actively cooperative TRIM job.

        Return acceptance/state/cancellation/reason; unsupported active work is
        allowed to finish and never falsely reported cancelled. Unknown IDs raise.
        """
        with self.condition:
            if job_id not in self.states:
                raise ValueError("not-found: unknown job")
            state = self.states[job_id]
            accepted, reason = False, "Active operation has no cooperative cancellation callback"
            if state["state"] == "queued":
                self.cancels[job_id].set()
                self.transition(job_id, {"state": "cancelled", "phase": "finished", "cancellation": "complete"})
                accepted, reason = True, "Cancelled before dispatch"
            elif state["state"] in ("running", "cancelling") and state["cancellation"] == "cooperative":
                self.cancels[job_id].set()
                self.transition(job_id, {"state": "cancelling"})
                accepted, reason = True, "Cancellation requested; awaiting owned native shutdown"
            elif state["state"] in _TERMINAL:
                reason = "Job is already terminal"
            return {"accepted": accepted, "state": self.states[job_id]["state"], "cancellation": self.states[job_id]["cancellation"], "reason": reason}

    def shutdown(self: JobManager) -> None:
        """Stop admissions, cancel eligible work and wait for safe finalization.

        Return None after releasing singleton ownership; noncancellable/unlimited
        operations may delay shutdown according to their advertised bounds.
        """
        if self.lease is None:
            return
        with self.condition:
            self.stopping = True
            for identity in tuple(self.states):
                if self.states[identity]["state"] not in _TERMINAL:
                    self.cancel(identity)
            self.condition.notify_all()
        if self.dispatcher is not None:
            self.dispatcher.join()
        for thread in self.threads:
            thread.join()
        self.lease.__exit__(None, None, None)
        self.lease = None

    def dispatch(self: JobManager) -> None:
        """Run the scheduler thread until shutdown, returning None.

        Reserve global slots and FIFO worker identities without holding queue locks
        during backend calls. Durable transition errors stop admissions and log.
        """
        try:
            with self.condition:
                while not self.stopping:
                    dispatched = False
                    blocked = set()
                    reserved = {worker for identity in self.running for worker in self.states[identity]["worker_ids"]}
                    for identity in sorted(self.requests, key=lambda item: self.requests[item]["sequence"]):
                        state = self.states[identity]
                        if state["state"] != "queued":
                            continue
                        request = self.requests[identity]
                        slots = request["arguments"].get("max_workers", 1) if request["operation"] == "batch_submit" else 1
                        workers = set(state["worker_ids"])
                        if workers & (reserved | blocked) or sum(self.running.values()) + slots > self.context.limits["max_parallel"]:
                            blocked.update(workers)
                            continue
                        self.running[identity] = slots
                        reserved.update(workers)
                        self.transition(identity, {"state": "running", "phase": None})
                        thread = Thread(target=self.execute_one, args=(identity,), name=identity, daemon=False)
                        self.threads.append(thread)
                        thread.start()
                        dispatched = True
                    if not dispatched:
                        self.condition.wait()
        except BaseException as error:
            print(f"Scheduler failure: {error!r}", file=sys.stderr)
            with self.condition:
                self.stopping = True
                self.condition.notify_all()

    def execute_one(self: JobManager, job_id: str) -> None:
        """Execute job_id's fixed callback and seal result/error before terminal state.

        Return None after releasing reservations; publication failures retain
        unfinished evidence for restart quarantine and are logged to stderr.
        """
        request = self.requests[job_id]
        target = self.root / "jobs" / job_id
        try:
            callback_arguments = deepcopy(request["arguments"])
            callback_arguments["request_id"] = request["request_id"]
            result = self.execute(request["operation"], callback_arguments, self.context, partial(self.cancelled, job_id), partial(self.progress, job_id))
            if not isinstance(result, dict) or set(result) != {"outcome", "data", "references", "warnings"} or result["outcome"] not in ("succeeded", "failed", "partial", "cancelled"):
                raise ValueError("Invalid write dispatcher result")
            payload = {"schema_version": 1, "operation": request["operation"], **result}
            evidence = publish_json(target / "result.json", payload)
            reference = {"kind": "job", "id": job_id, "revision": None, "path": (target / "result.json").relative_to(self.context.paths.root).as_posix(), "sha256": evidence["sha256"], "size_bytes": evidence["size_bytes"]}
            changes = {"state": "failed" if result["outcome"] in ("failed", "partial") else result["outcome"], "phase": "finished", "cancellation": "complete", "result": reference}
            data = result["data"]
            if isinstance(data, dict) and data.get("status") in ("created", "running", "succeeded", "failed") and "engine" in data:
                changes["native_status"] = data["status"]
            self.transition(job_id, changes)
        except BaseException as error:
            print(f"Job {job_id} failed: {error!r}", file=sys.stderr)
            try:
                document = error_response(error if isinstance(error, Exception) else RuntimeError(type(error).__name__))
                evidence = publish_json(target / "error.json", document)
                reference = {"kind": "job", "id": job_id, "revision": None, "path": (target / "error.json").relative_to(self.context.paths.root).as_posix(), "sha256": evidence["sha256"], "size_bytes": evidence["size_bytes"]}
                self.transition(job_id, {"state": "failed", "phase": "finished", "cancellation": "complete", "error": reference})
            except BaseException as publication_error:
                print(f"Job finalization retained unfinished evidence: {publication_error!r}", file=sys.stderr)
        finally:
            with self.condition:
                self.running.pop(job_id, None)
                self.condition.notify_all()

    def progress(self: JobManager, job_id: str, event: JSONObject) -> None:
        """Persist job_id's exact wrapped event and owned identity/phase updates.

        Validate event shape/identities, copy domain data, return None and wake
        readers; invalid callbacks or failed publication propagate to the backend.
        """
        if isinstance(event, dict) and set(event) != {"source", "phase", "worker_ids", "simulation_ids", "data"}:
            event = {"source": "domain", "phase": self.states[job_id]["phase"], "worker_ids": [], "simulation_ids": [], "data": deepcopy(event)}
        if not isinstance(event, dict) or set(event) != {"source", "phase", "worker_ids", "simulation_ids", "data"} or event["source"] not in ("adapter", "domain") or not isinstance(event["worker_ids"], list) or not isinstance(event["simulation_ids"], list):
            raise ValueError("Invalid progress event")
        canonical_bytes(event)
        with self.condition:
            state = self.states[job_id]
            for worker in event["worker_ids"]:
                if not isinstance(worker, str) or not worker.startswith("worker_") or not worker[7:].isdigit() or int(worker[7:]) < 1 or worker in self.quarantined:
                    raise ValueError("Invalid allocated worker event")
                if event["source"] == "domain" and worker not in state["worker_ids"]:
                    raise ValueError("Domain callback cannot allocate workers")
            sequence = state["sequence"] + 1
            event_path = self.root / "jobs" / job_id / "events" / f"{sequence:09d}.json"
            publish_json(event_path, {"sequence": sequence, **deepcopy(event)})
            changes = {"progress": deepcopy(event), "phase": event["phase"], "worker_ids": list(dict.fromkeys(state["worker_ids"] + event["worker_ids"])), "simulation_ids": list(dict.fromkeys(state["simulation_ids"] + event["simulation_ids"]))}
            if event["source"] == "adapter" and isinstance(event["data"], dict):
                if "native_status" in event["data"]:
                    if event["data"]["native_status"] not in (None, "created", "running", "succeeded", "failed"):
                        raise ValueError("Invalid native status progress")
                    changes["native_status"] = event["data"]["native_status"]
                if "parser_complete" in event["data"]:
                    if event["data"]["parser_complete"] is not None and type(event["data"]["parser_complete"]) is not bool:
                        raise ValueError("Invalid parser completeness progress")
                    changes["parser_complete"] = event["data"]["parser_complete"]
            if event["phase"] == "executing" and self.requests[job_id]["operation"] == "run_submit":
                changes["cancellation"] = "cooperative" if self.requests[job_id]["arguments"]["prepared"]["engine"] == "trim" else "timeout-only"
            self.transition(job_id, changes)

    def cancelled(self: JobManager, job_id: str) -> bool:
        """Return job_id's synchronized cancellation Event value without I/O.

        Unknown job identity raises ValueError; no process is stopped here.
        """
        with self.condition:
            if job_id not in self.cancels:
                raise ValueError("not-found: unknown job")
            return self.cancels[job_id].is_set()

    def transition(self: JobManager, job_id: str, changes: JSONObject) -> None:
        """Validate and atomically publish job_id state changes, returning None.

        Terminal jobs are immutable; phase/state/schema errors or I/O propagate.
        Wake waiting clients only after durable publication succeeds.
        """
        with self.condition:
            if job_id not in self.states or self.states[job_id]["state"] in _TERMINAL:
                raise ValueError("conflict: unknown or terminal job transition")
            old = self.states[job_id]
            if set(changes) - (set(old) - {"schema_version", "id", "sequence", "updated_at"}):
                raise ValueError("Invalid job state fields")
            updated = {**deepcopy(old), **deepcopy(changes)}
            if updated["state"] not in _TERMINAL | {"queued", "running", "cancelling"} or updated["phase"] not in (None, "accepted", "allocating", "resolving", "preparing", "executing", "preserving", "parsing", "indexing", "cleanup", "finalizing", "finished") or updated["cancellation"] not in ("queued-only", "cooperative", "timeout-only", "unsupported", "complete"):
                raise ValueError("Invalid job state or phase")
            updated["sequence"] = old["sequence"] + 1
            updated["updated_at"] = _record_time(self.context.config, None).isoformat()
            publish_json(self.root / "jobs" / job_id / "state.json", updated, replace=True)
            self.states[job_id] = updated
            self.condition.notify_all()
