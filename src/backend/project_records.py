"""Store independent revisioned case and analysis records without engine lifecycle coupling."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
import errno
import hashlib
import json
import msvcrt
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
import time
from typing import Literal
from uuid import UUID

from src.backend.config import Config
from src.backend.paths import ProjectPaths
from src.backend.records import CreateRequest, _HELD_LOCKS, _record_time, create_directories
from src.knowledge.schema import validate_search_document
from src.srim.common.types import JSONObject
from src.srim.common.files import _read_json
from src.srim.common.files import _read_regular_bytes


def initialize_record(paths: ProjectPaths, config: Config, kind: Literal["case", "analysis"], document: JSONObject, request_id: str, *, allocated_id: str | None = None) -> JSONObject:
    """Allocate or explicitly initialize an empty kind record using document/request_id.

    Return copied revision/reference. Validate first and retain partial allocation
    on publication failure; raise ValueError/OSError without overwriting evidence.
    """
    if kind not in ("case", "analysis") or str(UUID(request_id)) != request_id:
        raise ValueError("Invalid record kind or request UUID")
    allocation_kind = "case" if kind == "case" else "post"
    if allocated_id is not None and re.fullmatch(allocation_kind + r"_[0-9]{8}_[0-9]{6}_[1-9][0-9]*", allocated_id) is None:
        raise ValueError("Invalid existing allocation identity")
    instant = _record_time(config, None).isoformat()
    result = deepcopy(document)
    result.update(schema_version=1, kind=kind, id=allocated_id or allocation_kind + "_20000101_000000_1", revision=1, previous_sha256=None, request_id=request_id, created_at=instant, updated_at=instant)
    validate_record(result, kind)
    if allocated_id is None:
        allocated_id = create_directories(paths, config, [CreateRequest(allocation_kind)]).completed[0].path.name
    parent = paths.cases if kind == "case" else paths.postprocessing_memory
    target = parent / allocated_id
    result["id"] = allocated_id
    with record_lock(paths, kind, allocated_id):
        for entry in (*reversed(target.parents), target):
            if entry.is_symlink() or entry.is_junction():
                raise OSError(errno.ELOOP, "Unsafe record allocation", str(entry))
        if not target.is_dir():
            raise FileNotFoundError(str(target))
        if any(target.iterdir()):
            raise ValueError("conflict: allocation must be explicitly empty")
        (target / "revisions").mkdir()
        written = write_record_json(target / "revisions" / "r000001.json", result)
        write_record_json(target / "current.json", {"schema_version": 1, "kind": kind, "id": allocated_id, "revision": 1, "sha256": written["sha256"]})
    return {"record": result, "reference": {"kind": kind, "id": allocated_id, "revision": 1, "sha256": written["sha256"]}}


def read_record(paths: ProjectPaths, kind: Literal["case", "analysis"], record_id: str, *, revision: int | None = None) -> JSONObject:
    """Read and verify record_id's current or requested immutable revision chain.

    Return record/reference without domain evidence verification or writes.
    Invalid identity/schema/hash/chain raises ValueError; I/O errors propagate.
    """
    prefix = "case" if kind == "case" else "post" if kind == "analysis" else None
    if prefix is None or not isinstance(record_id, str) or re.fullmatch(prefix + r"_[0-9]{8}_[0-9]{6}_[1-9][0-9]*", record_id) is None:
        raise ValueError("Invalid record identity")
    target = (paths.cases if kind == "case" else paths.postprocessing_memory) / record_id
    pointer = _read_json(target / "current.json")
    if set(pointer) != {"schema_version", "kind", "id", "revision", "sha256"} or type(pointer["schema_version"]) is not int or pointer["schema_version"] != 1 or pointer["kind"] != kind or pointer["id"] != record_id or type(pointer["revision"]) is not int or pointer["revision"] < 1 or re.fullmatch(r"[0-9a-f]{64}", str(pointer["sha256"])) is None:
        raise ValueError("integrity: invalid current pointer")
    selected = pointer["revision"] if revision is None else revision
    if type(selected) is not int or not 1 <= selected <= pointer["revision"]:
        raise ValueError("not-found: revision is not published")
    expected = pointer["sha256"]
    result = None
    for number in range(pointer["revision"], 0, -1):
        filename = target / "revisions" / f"r{number:06d}.json"
        contents = _read_regular_bytes(filename)
        digest = hashlib.sha256(contents).hexdigest()
        if digest != expected:
            raise ValueError("integrity: broken record revision chain")
        current = _read_json(filename)
        validate_record(current, kind)
        if current["id"] != record_id or current["revision"] != number:
            raise ValueError("integrity: revision identity mismatch")
        if number == selected:
            result = {"record": current, "reference": {"kind": kind, "id": record_id, "revision": number, "sha256": digest}}
        expected = current["previous_sha256"]
    if expected is not None or result is None:
        raise ValueError("integrity: invalid revision origin")
    return result


def revise_record(paths: ProjectPaths, config: Config, kind: Literal["case", "analysis"], record_id: str, expected_revision: int, document: JSONObject, request_id: str) -> JSONObject:
    """Append document to record_id with CAS and request_id, returning revision evidence.

    Reject stale writes, repeated conflicting UUIDs or terminal analysis mutation.
    Publish immutable revision before pointer; retain partial writes on OSError.
    """
    if str(UUID(request_id)) != request_id or type(expected_revision) is not int or expected_revision < 1:
        raise ValueError("Invalid request UUID or expected revision")
    with record_lock(paths, kind, record_id):
        previous = read_record(paths, kind, record_id)
        old = previous["record"]
        if old["request_id"] == request_id:
            if all(old.get(key) == value for key, value in document.items()):
                return previous
            raise ValueError("conflict: request ID already used")
        if old["revision"] != expected_revision:
            raise ValueError("conflict: stale expected revision")
        if kind == "analysis" and old["status"] != "draft":
            raise ValueError("conflict: terminal analysis is immutable")
        result = deepcopy(document)
        result.update(schema_version=1, kind=kind, id=record_id, revision=expected_revision + 1, previous_sha256=previous["reference"]["sha256"], request_id=request_id, created_at=old["created_at"], updated_at=_record_time(config, None).isoformat())
        validate_record(result, kind)
        target = (paths.cases if kind == "case" else paths.postprocessing_memory) / record_id
        written = write_record_json(target / "revisions" / f"r{result['revision']:06d}.json", result)
        write_record_json(target / "current.json", {"schema_version": 1, "kind": kind, "id": record_id, "revision": result["revision"], "sha256": written["sha256"]}, replace=True)
        return {"record": result, "reference": {"kind": kind, "id": record_id, "revision": result["revision"], "sha256": written["sha256"]}}


def list_records(paths: ProjectPaths, kind: Literal["case", "analysis"], *, status: str | None = None, offset: int = 0, limit: int = 100) -> JSONObject:
    """Return paged record summaries and independent corrupt-record issues.

    Inputs select kind/status and integer paging; invalid filters raise ValueError.
    Read no native engines and never initialize old allocations implicitly.
    """
    states = ("open", "paused", "completed", "archived") if kind == "case" else ("draft", "completed", "failed")
    if kind not in ("case", "analysis") or status is not None and status not in states or type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("Invalid record listing filters")
    parent = paths.cases if kind == "case" else paths.postprocessing_memory
    matches, issues = [], []
    for target in sorted(parent.iterdir() if parent.exists() else []):
        if target.name.startswith(".") or not (target / "current.json").exists():
            continue
        try:
            result = read_record(paths, kind, target.name)
            if status is None or result["record"]["status"] == status:
                matches.append({"reference": result["reference"], "title": result["record"]["title"], "status": result["record"]["status"]})
        except (OSError, ValueError) as error:
            issues.append({"id": target.name, "message": str(error)})
    return {"total": len(matches), "offset": offset, "limit": limit, "matches": matches[offset:offset + limit], "issues": issues}


def validate_record(document: JSONObject, kind: Literal["case", "analysis"]) -> None:
    """Validate exact common and kind-specific revision fields without I/O.

    Reject malformed quantities, identity, timestamps, statuses, notes, methods,
    members or artifacts with ValueError; return None for valid finite JSON.
    """
    from datetime import datetime

    common = {"schema_version", "kind", "id", "revision", "previous_sha256", "request_id", "created_at", "updated_at", "title", "question", "status"}
    extra = {"tags", "notes", "members"} if kind == "case" else {"method", "inputs", "eligibility", "parameters", "artifacts", "findings", "limitations", "failure"}
    if kind not in ("case", "analysis") or not isinstance(document, dict) or set(document) != common | extra or type(document["schema_version"]) is not int or document["schema_version"] != 1 or document["kind"] != kind:
        raise ValueError("Invalid project revision schema")
    pending = [document]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            if any(not isinstance(key, str) for key in value):
                raise ValueError("Project JSON keys must be strings")
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
        elif value is not None and type(value) not in (str, int, float, bool):
            raise ValueError("Unsupported project JSON value")
    json.dumps(document, allow_nan=False)
    if str(UUID(document["request_id"])) != document["request_id"] or type(document["revision"]) is not int or document["revision"] < 1:
        raise ValueError("Invalid revision/request ID")
    prefix = "case" if kind == "case" else "post"
    if re.fullmatch(prefix + r"_[0-9]{8}_[0-9]{6}_[1-9][0-9]*", str(document["id"])) is None:
        raise ValueError("Invalid record ID")
    previous = document["previous_sha256"]
    if document["revision"] == 1 and previous is not None or document["revision"] > 1 and (not isinstance(previous, str) or re.fullmatch(r"[0-9a-f]{64}", previous) is None):
        raise ValueError("Invalid preceding revision hash")
    for key in ("created_at", "updated_at"):
        if not isinstance(document[key], str) or datetime.fromisoformat(document[key]).utcoffset() is None:
            raise ValueError("Record timestamp requires explicit offset")
    for key in ("title", "question"):
        if not isinstance(document[key], str) or not document[key].strip() or "\0" in document[key] or (len(document[key]) > 512 if key == "title" else len(document[key].encode("utf-8")) > 1048576):
            raise ValueError("Invalid record title/question")
    if kind == "case":
        if document["status"] not in ("open", "paused", "completed", "archived") or not isinstance(document["notes"], str) or "\0" in document["notes"] or len(document["notes"].encode("utf-8")) > 1048576:
            raise ValueError("Invalid case state/notes")
        tags = document["tags"]
        if not isinstance(tags, list) or len(tags) > 64 or any(not isinstance(tag, str) or not 1 <= len(tag) <= 128 or "\0" in tag for tag in tags) or len(set(tags)) != len(tags):
            raise ValueError("Invalid case tags")
        if not isinstance(document["members"], list):
            raise ValueError("Invalid members")
        seen = set()
        for member in document["members"]:
            if not isinstance(member, dict) or set(member) != {"member_id", "role", "note", "reference"} or str(UUID(member["member_id"])) != member["member_id"] or member["member_id"] in seen or not isinstance(member["role"], str) or not 1 <= len(member["role"]) <= 128 or not isinstance(member["note"], str) or len(member["note"].encode("utf-8")) > 1048576 or not isinstance(member["reference"], dict):
                raise ValueError("Invalid or duplicate case member")
            seen.add(member["member_id"])
            reference = member["reference"]
            if reference.get("kind") == "native-source" and set(reference) == {"kind", "source"}:
                validate_search_document(reference["source"], "reference")
            elif reference.get("kind") == "analysis" and set(reference) == {"kind", "id", "revision", "sha256"}:
                if re.fullmatch(r"post_[0-9]{8}_[0-9]{6}_[1-9][0-9]*", str(reference["id"])) is None or type(reference["revision"]) is not int or reference["revision"] < 1 or re.fullmatch(r"[0-9a-f]{64}", str(reference["sha256"])) is None:
                    raise ValueError("Invalid analysis member reference")
            elif reference.get("kind") == "job" and set(reference) == {"kind", "server", "job_id", "request_sha256"}:
                if reference["server"] != "srim_mcp" or not isinstance(reference["job_id"], str) or not reference["job_id"].startswith("job_") or str(UUID(reference["job_id"][4:])) != reference["job_id"][4:] or re.fullmatch(r"[0-9a-f]{64}", str(reference["request_sha256"])) is None:
                    raise ValueError("Invalid job member reference")
            else:
                raise ValueError("Invalid case member reference schema")
    else:
        if document["status"] not in ("draft", "completed", "failed"):
            raise ValueError("Invalid analysis status")
        for key in ("findings", "limitations"):
            if not isinstance(document[key], str) or "\0" in document[key] or len(document[key].encode("utf-8")) > 1048576:
                raise ValueError("Invalid analysis narrative")
        method = document["method"]
        if not isinstance(method, dict) or set(method) != {"kind", "reference", "description", "citations", "code_artifacts"} or method["kind"] not in ("ad-hoc", "registered") or not isinstance(method["description"], str) or not method["description"].strip() or not isinstance(method["citations"], list) or not isinstance(method["code_artifacts"], list):
            raise ValueError("Invalid analysis method")
        if method["kind"] == "ad-hoc" and method["reference"] is not None or method["kind"] == "registered" and not isinstance(method["reference"], dict):
            raise ValueError("Invalid method reference")
        if any(not isinstance(citation, dict) or set(citation) != {"title", "locator"} or any(not isinstance(value, str) or not value.strip() for value in citation.values()) for citation in method["citations"]) or any(not isinstance(name, str) for name in method["code_artifacts"]):
            raise ValueError("Invalid method citations/code artifacts")
        if not isinstance(document["inputs"], list) or not isinstance(document["parameters"], dict) or not isinstance(document["artifacts"], list):
            raise ValueError("Invalid analysis input/parameter/artifact fields")
        for selected in document["inputs"]:
            if not isinstance(selected, dict) or set(selected) != {"source", "files", "purpose"} or not isinstance(selected["purpose"], str) or not selected["purpose"].strip() or not isinstance(selected["files"], list) or not selected["files"]:
                raise ValueError("Invalid saved analysis input")
            validate_search_document(selected["source"], "reference")
            for file in selected["files"]:
                validate_search_document(file, "file")
                if file["owner"] != selected["source"]:
                    raise ValueError("Analysis selected file has mismatched owner")
        eligibility = document["eligibility"]
        if not isinstance(eligibility, dict) or set(eligibility) != {"allow_failed_runs", "allow_checkpoint_recovery", "require_complete_parser"} or any(type(value) is not bool for value in eligibility.values()):
            raise ValueError("Analysis eligibility must be explicit booleans")
        failure = document["failure"]
        if document["status"] == "failed":
            if not isinstance(failure, dict) or set(failure) != {"code", "message"} or any(not isinstance(value, str) or not value.strip() for value in failure.values()):
                raise ValueError("Failed analysis requires reason")
        elif failure is not None:
            raise ValueError("Nonfailed analysis cannot have failure")
        seen = set()
        for artifact in document["artifacts"]:
            if not isinstance(artifact, dict) or set(artifact) != {"name", "role", "path", "sha256", "size_bytes", "media_type", "source"} or artifact["role"] not in ("method", "code", "data", "figure", "report", "diagnostic") or not isinstance(artifact["name"], str) or artifact["name"].casefold() in seen or type(artifact["size_bytes"]) is not int or artifact["size_bytes"] < 0 or re.fullmatch(r"[0-9a-f]{64}", str(artifact["sha256"])) is None:
                raise ValueError("Invalid analysis artifact")
            for field in ("name", "path"):
                if "\\" in artifact[field] or any(part in ("", ".", "..") or os.path.isreserved(part) or re.search(r'[<>:"|?*\x00-\x1f]', part) or part.endswith((".", " ")) for part in artifact[field].split("/")):
                    raise ValueError("Unsafe artifact path/name")
            if not artifact["path"].startswith("artifacts/" + artifact["sha256"] + "/") or not isinstance(artifact["media_type"], str) or not isinstance(artifact["source"], str):
                raise ValueError("Invalid artifact provenance")
            seen.add(artifact["name"].casefold())


@contextmanager
def record_lock(paths: ProjectPaths, kind: Literal["case", "analysis"], record_id: str) -> Iterator[None]:
    """Yield None under a bounded kind/record_id Windows lock in paths.

    Validate identities/ancestors, create persistent coordination files, release
    on exit and raise ValueError/OSError on unsafe paths or contention.
    """
    prefix = "case" if kind == "case" else "post" if kind == "analysis" else None
    if prefix is None or re.fullmatch(prefix + r"_[0-9]{8}_[0-9]{6}_[1-9][0-9]*", str(record_id)) is None:
        raise ValueError("Invalid record lock identity")
    target = paths.root / "memory" / "project" / ".locks" / f"{kind}-{record_id}.lock"
    for entry in (*reversed(target.parents), target):
        if entry.is_symlink() or entry.is_junction():
            raise OSError(errno.ELOOP, "Unsafe project lock", str(entry))
    target.parent.mkdir(parents=True, exist_ok=True)
    identity = os.path.normcase(str(target))
    if identity in _HELD_LOCKS.get():
        raise OSError(errno.EDEADLK, "Recursive project record lock")
    with target.open("a+b") as stream:
        if stream.seek(0, os.SEEK_END) == 0:
            stream.write(b"\0")
            stream.flush()
        deadline = time.monotonic() + 30.0
        while True:
            try:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError as error:
                if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK) or time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)
        token = _HELD_LOCKS.set(_HELD_LOCKS.get() | {identity})
        try:
            yield None
        finally:
            try:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            finally:
                _HELD_LOCKS.reset(token)


def write_record_json(path: Path, document: JSONObject, *, replace: bool = False) -> JSONObject:
    """Synchronize canonical document at safe path and return hash/size evidence.

    Replace only when explicitly requested for operational pointers. Retain
    temporary files on I/O failure; reject unsafe ancestors or collisions.
    """
    for entry in (*reversed(path.parents), path):
        if entry.is_symlink() or entry.is_junction():
            raise OSError(errno.ELOOP, "Unsafe project publication path", str(entry))
    payload = (json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=path.parent, prefix=".project-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            temporary.rename(path)
        return {"path": str(path), "sha256": hashlib.sha256(payload).hexdigest(), "size_bytes": len(payload)}
    except BaseException as error:
        error.add_note(f"Project publication retained temporary file: {temporary}")
        raise
