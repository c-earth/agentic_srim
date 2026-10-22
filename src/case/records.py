"""Manage research cases with immutable revisions and explicit evidence membership."""

from __future__ import annotations

import hashlib
import re
from uuid import UUID

from src.backend.config import Config
from src.backend.paths import ProjectPaths
from src.backend.project_records import initialize_record, list_records, read_record, revise_record
from src.knowledge.source import read_source_record
from src.srim.common.types import JSONObject
from src.srim.common.files import _read_json


def create_case(paths: ProjectPaths, config: Config, spec: JSONObject, request_id: str, *, allocated_id: str | None = None) -> JSONObject:
    """Initialize spec as a case under paths/config with request_id and optional allocation.

    Verify introduced members, return revision/reference/checks and retain partial
    publication on errors. Invalid schema/evidence raises ValueError; I/O propagates.
    """
    if not isinstance(spec, dict) or set(spec) != {"title", "question", "status", "tags", "notes", "members"}:
        raise ValueError("Invalid CaseSpec")
    checks = check_case_members(paths, spec["members"])
    if any(check["state"] not in ("verified", "live") for check in checks):
        raise ValueError("Invalid newly introduced case evidence")
    result = initialize_record(paths, config, "case", spec, request_id, allocated_id=allocated_id)
    result["checks"] = checks
    return result


def read_case(paths: ProjectPaths, case_id: str, *, revision: int | None = None) -> JSONObject:
    """Read case_id/revision and return stored content with independent member checks.

    Never refresh references or rewrite history. Invalid record raises
    ValueError/OSError; individual stale/missing members remain reported issues.
    """
    result = read_record(paths, "case", case_id, revision=revision)
    result["checks"] = check_case_members(paths, result["record"]["members"])
    return result


def revise_case(paths: ProjectPaths, config: Config, case_id: str, expected_revision: int, spec: JSONObject, request_id: str) -> JSONObject:
    """Append full spec to case_id with expected_revision/request_id and config time.

    Retain historical stale members but reject new invalid references. Return the
    revision/checks; stale CAS, invalid input or I/O raises without merging writes.
    """
    if not isinstance(spec, dict) or set(spec) != {"title", "question", "status", "tags", "notes", "members"}:
        raise ValueError("Invalid CaseSpec")
    old = read_record(paths, "case", case_id)["record"]
    previous = {member["member_id"]: member["reference"] for member in old["members"]}
    checks = check_case_members(paths, spec["members"])
    for member, check in zip(spec["members"], checks, strict=True):
        if previous.get(member["member_id"]) != member["reference"] and check["state"] not in ("verified", "live"):
            raise ValueError("Invalid newly introduced case evidence")
    result = revise_record(paths, config, "case", case_id, expected_revision, spec, request_id)
    result["checks"] = checks
    return result


def list_cases(paths: ProjectPaths, *, status: str | None = None, offset: int = 0, limit: int = 100) -> JSONObject:
    """Return paths' case summaries filtered by status and integer pagination.

    Reads only, returning independent issues; ValueError/OSError propagates for
    invalid arguments or inaccessible listing roots.
    """
    return list_records(paths, "case", status=status, offset=offset, limit=limit)


def check_case_members(paths: ProjectPaths, members: list[JSONObject]) -> list[JSONObject]:
    """Return ordered verification states for members' explicit references in paths.

    Validate member/reference shapes and hashes without mutation or execution.
    Malformed arguments raise ValueError; stale/missing targets become check issues.
    """
    if not isinstance(members, list):
        raise ValueError("Members must be a list")
    checks, seen = [], set()
    for member in members:
        if not isinstance(member, dict) or set(member) != {"member_id", "role", "note", "reference"} or str(UUID(member["member_id"])) != member["member_id"] or member["member_id"] in seen:
            raise ValueError("Invalid case member identity")
        seen.add(member["member_id"])
        reference = member["reference"]
        if not isinstance(reference, dict):
            raise ValueError("Invalid member reference")
        kind = reference.get("kind")
        if kind == "native-source":
            if set(reference) != {"kind", "source"}:
                raise ValueError("Invalid native member reference")
        elif kind == "analysis":
            if set(reference) != {"kind", "id", "revision", "sha256"} or type(reference["revision"]) is not int or reference["revision"] < 1 or re.fullmatch(r"[0-9a-f]{64}", str(reference["sha256"])) is None:
                raise ValueError("Invalid analysis member reference")
        elif kind == "job":
            if set(reference) != {"kind", "server", "job_id", "request_sha256"} or reference["server"] != "srim_mcp" or not isinstance(reference["job_id"], str) or not reference["job_id"].startswith("job_") or str(UUID(reference["job_id"][4:])) != reference["job_id"][4:] or re.fullmatch(r"[0-9a-f]{64}", str(reference["request_sha256"])) is None:
                raise ValueError("Invalid physics job member reference")
        else:
            raise ValueError("Unsupported case member kind")
        state, reason = "verified", None
        try:
            if kind == "native-source":
                read_source_record(paths, reference["source"])
            elif kind == "analysis":
                result = read_record(paths, "analysis", reference["id"], revision=reference["revision"])
                if result["reference"]["sha256"] != reference["sha256"]:
                    raise ValueError("Analysis hash no longer matches")
            else:
                root = paths.root / "memory" / "mcp" / "srim_mcp" / "jobs" / reference["job_id"]
                request = _read_json(root / "request.json")
                status = _read_json(root / "state.json")
                if request.get("id") != reference["job_id"] or request.get("server") != "srim_mcp" or request.get("request_sha256") != reference["request_sha256"] or status.get("id") != reference["job_id"]:
                    raise ValueError("Physics job reference mismatch")
                import json
                digest = hashlib.sha256(json.dumps({"operation": request["operation"], "arguments": request["arguments"]}, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
                if digest != reference["request_sha256"]:
                    raise ValueError("Physics job request hash mismatch")
                state, reason = "live", "Job links remain unsealed; attach a terminal source explicitly"
        except FileNotFoundError as error:
            state, reason = "missing", str(error)
        except (OSError, ValueError) as error:
            state, reason = "stale", str(error)
        checks.append({"member_id": member["member_id"], "state": state, "reason": reason})
    return checks
