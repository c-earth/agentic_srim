"""Inspect and preserve self-improvement evidence without manufacturing approval authority."""

from __future__ import annotations

from datetime import datetime
import hashlib
import math
import os
from pathlib import Path
import re
from uuid import UUID, uuid4

from src.backend.config import Config
from src.backend.paths import ProjectPaths
from src.backend.project_records import write_record_json
from src.backend.records import _record_time
from src.srim.common.types import JSONObject
from src.srim.common.files import _read_json
from src.srim.common.files import _read_regular_bytes


def read_improvement_status(paths: ProjectPaths, *, proposal_id: str | None = None) -> JSONObject:
    """Inspect validated event graphs and hashes in paths' local improvement store.

    Optional proposal_id filters a UUID. Return observed states/events/issues;
    preserve malformed/conflicting evidence and never mutate lifecycle state.
    Return additive assessment summaries and pending findings. Validate all events
    before filtering; per-record failures become issues, unsafe store paths raise
    OSError. Assessment filtering follows valid proposal-observation links.
    """
    if proposal_id is not None and str(UUID(proposal_id)) != proposal_id:
        raise ValueError("Invalid proposal UUID")
    root = paths.root / ".local" / "workflow-improvement"
    for entry in (*reversed(root.parents), root):
        if entry.is_symlink() or entry.is_junction():
            raise OSError("Unsafe improvement store path")
    events, issues, hashes = [], [], {}
    for path in sorted((root / "events").glob("*.json")):
        try:
            payload = _read_regular_bytes(path)
            event = _read_json(path)
            validate_improvement_event(event)
            if path.stem != event["event_id"]:
                raise ValueError("Event filename identity mismatch")
            events.append(event)
            hashes[event["event_id"]] = hashlib.sha256(payload).hexdigest()
        except (ValueError, OSError) as error:
            issues.append({"path": path.relative_to(paths.root).as_posix(), "message": str(error)})
    by_id = {event["event_id"]: event for event in events}
    states = []
    for identity in sorted({event["proposal_id"] for event in events if event["proposal_id"] is not None}):
        related = [event for event in events if event["proposal_id"] == identity]
        transitions = {event["event_id"]: event for event in related if event["kind"] == "transition"}
        successors: dict[str | None, list[str]] = {}
        for event in transitions.values():
            successors.setdefault(event["details"]["predecessor_event_id"], []).append(event["event_id"])
        current, previous, visited = None, None, set()
        valid = True
        while successors.get(previous):
            next_ids = successors[previous]
            if len(next_ids) != 1 or next_ids[0] in visited:
                issues.append({"proposal_id": identity, "message": "Conflicting or cyclic transition successors"})
                valid = False
                break
            event = transitions[next_ids[0]]
            if event["details"]["previous_state"] != current:
                issues.append({"proposal_id": identity, "message": "Transition previous state mismatch"})
                valid = False
                break
            current = event["details"]["next_state"]
            previous = event["event_id"]
            visited.add(previous)
        if len(visited) != len(transitions):
            issues.append({"proposal_id": identity, "message": "Orphaned transition predecessor"})
            valid = False
        for event in related:
            details = event["details"]
            if event["kind"] == "proposal":
                try:
                    relative = details["path"]
                    if not isinstance(relative, str) or "\\" in relative or any(part in ("", ".", "..") or os.path.isreserved(part) or ":" in part for part in relative.split("/")):
                        raise ValueError("Unsafe proposal path")
                    source = paths.root / relative
                    if hashlib.sha256(_read_regular_bytes(source)).hexdigest() != details["revision"]:
                        preserved = source.with_name("proposal-" + details["revision"] + ".md")
                        if hashlib.sha256(_read_regular_bytes(preserved)).hexdigest() != details["revision"]:
                            raise ValueError("Proposal revision hash mismatch")
                    for reference in details["observation_ids"]:
                        observation = by_id.get(reference)
                        if observation is None:
                            observation = _read_json(root / "events" / (reference + ".json"))
                        validate_improvement_event(observation)
                        if observation["kind"] != "observation" or observation["event_id"] != reference:
                            raise ValueError("Missing or invalid observation reference")
                except (ValueError, OSError) as error:
                    issues.append({"proposal_id": identity, "message": str(error)})
                    valid = False
        for manifest in (root / "proposals" / identity / "snapshots").glob("*/manifest.json"):
            try:
                snapshot = _read_json(manifest)
                if set(snapshot) != {"schema_version", "proposal_revision", "approval_event_id", "files"} or snapshot["schema_version"] != 1 or not isinstance(snapshot["files"], list):
                    raise ValueError("Invalid snapshot manifest")
                for file in snapshot["files"]:
                    if set(file) != {"path", "original_exists", "trial_exists", "original_sha256", "trial_sha256", "original_snapshot", "trial_snapshot"}:
                        raise ValueError("Invalid snapshot entry")
                    for stage in ("original", "trial"):
                        if file[stage + "_exists"]:
                            relative = file[stage + "_snapshot"]
                            if not isinstance(relative, str) or "\\" in relative or any(part in ("", ".", "..") or ":" in part for part in relative.split("/")) or hashlib.sha256(_read_regular_bytes(manifest.parent / relative)).hexdigest() != file[stage + "_sha256"]:
                                raise ValueError("Snapshot bytes/hash mismatch")
            except (ValueError, OSError, KeyError) as error:
                issues.append({"proposal_id": identity, "message": str(error)})
                valid = False
        revisions = {event["details"]["revision"] for event in related if event["kind"] == "proposal"}
        for event in related:
            if event["kind"] == "approval" and event["details"]["revision"] not in revisions:
                issues.append({"proposal_id": identity, "message": "Approval does not bind a recorded exact proposal revision"})
                valid = False
        edges = {None: {"proposed"}, "proposed": {"trial_approved", "rejected", "withdrawn"}, "trial_approved": {"trial_active", "withdrawn", "restoration_blocked"}, "trial_active": {"awaiting_adoption", "restoration_blocked"}, "awaiting_adoption": {"adopted", "declined", "trial_approved"}, "restoration_blocked": {"awaiting_adoption"}, "adopted": set(), "declined": set(), "rejected": set(), "withdrawn": set()}
        for event in transitions.values():
            details = event["details"]
            if details["next_state"] not in edges[details["previous_state"]]:
                issues.append({"proposal_id": identity, "message": "Invalid lifecycle transition edge"})
                valid = False
            gate = "trial" if details["next_state"] == "trial_approved" else "adoption" if details["next_state"] == "adopted" else None
            if gate is not None and not any(approval["kind"] == "approval" and approval["details"]["gate"] == gate and approval["details"]["decision"] == "approved" and approval["details"]["revision"] in revisions for approval in related):
                issues.append({"proposal_id": identity, "message": "Lifecycle transition lacks a matching explicit approval event"})
                valid = False
        trial_tasks = [event for event in related if event["kind"] == "trial_task"]
        completed = {event["details"]["task_id"] for event in trial_tasks if event["details"]["status"] == "completed"}
        started = {event["details"]["task_id"] for event in trial_tasks if event["details"]["status"] == "started"}
        if not completed <= started:
            issues.append({"proposal_id": identity, "message": "Trial completion lacks a recorded start"})
            valid = False
        states.append({"proposal_id": identity, "state": current if valid else None, "valid": valid, "transition_event_id": previous, "trial_started_ids": sorted(started), "trial_completed_ids": sorted(completed), "completed_task_count": len(completed), "restoration": None if previous is None else transitions[previous]["details"]["restoration"], "boundary_qualification": "Evaluate eligibility, task maximum, early stops and current source bytes against the human-reviewed plan; no autonomous expiry is claimed", "enforcement": "instruction-based; no automatic trial execution or restoration"})
    assessments, pending, assessment_issues = _summarize_assessments(by_id, states, proposal_id)
    issues.extend(assessment_issues)
    selected = events if proposal_id is None else [event for event in events if event["proposal_id"] == proposal_id]
    selected_hashes = {event["event_id"]: hashes[event["event_id"]] for event in selected}
    selected_states = states if proposal_id is None else [state for state in states if state["proposal_id"] == proposal_id]
    return {"events": selected, "event_hashes": selected_hashes, "proposals": selected_states, "issues": issues, "assessments": assessments, "pending_assessments": pending}


def record_observation(paths: ProjectPaths, config: Config, details: JSONObject, request_id: str) -> JSONObject:
    """Preserve details as a local observation using paths, config and request_id.

    Return exact event/reference; validate protocol fields without judging evidence
    sufficiency. Reference paths use .local/workflow-improvement; invalid fields
    raise ValueError and filesystem errors propagate. No approvals/transitions
    or every-call telemetry are produced.
    """
    if str(UUID(request_id)) != request_id:
        raise ValueError("Invalid request UUID")
    event = {"schema_version": 1, "event_id": str(uuid4()), "recorded_at": _record_time(config, None).isoformat(), "kind": "observation", "proposal_id": None, "task_ref": "MCP observation " + request_id, "details": details}
    validate_improvement_event(event)
    path = paths.root / ".local" / "workflow-improvement" / "events" / (event["event_id"] + ".json")
    evidence = write_record_json(path, event)
    return {"event": event, "reference": {"kind": "improvement-event", "id": event["event_id"], "revision": None, "path": path.relative_to(paths.root).as_posix(), "sha256": evidence["sha256"], "size_bytes": evidence["size_bytes"]}}


def create_improvement_proposal(paths: ProjectPaths, config: Config, proposal_id: str, observation_ids: list[str], markdown: str, request_id: str) -> JSONObject:
    """Preserve exact markdown and a local event for the supplied proposal inputs.

    Validate UUIDs/sections/references and use config time. Return both references;
    reference paths under paths' .local/workflow-improvement store. Invalid inputs
    raise ValueError and filesystem errors propagate. No overwrite, approval,
    skill edit or trial occurs. Partial publication survives errors.
    """
    if str(UUID(request_id)) != request_id or str(UUID(proposal_id)) != proposal_id or not isinstance(observation_ids, list) or not observation_ids or len(set(observation_ids)) != len(observation_ids) or not isinstance(markdown, str):
        raise ValueError("Invalid proposal request")
    required = ("identity", "evidence", "change", "tradeoff", "trial", "restor", "decision")
    if any(term not in markdown.casefold() for term in required) or proposal_id not in markdown:
        raise ValueError("Proposal lacks required protocol sections/identity")
    root = paths.root / ".local" / "workflow-improvement"
    for identity in observation_ids:
        if str(UUID(identity)) != identity:
            raise ValueError("Invalid observation UUID")
        observation = _read_json(root / "events" / (identity + ".json"))
        validate_improvement_event(observation)
        if observation["kind"] != "observation" or observation["event_id"] != identity:
            raise ValueError("Proposal references a non-observation event")
    path = root / "proposals" / proposal_id / "proposal.md"
    for entry in (*reversed(path.parents), path):
        if entry.is_symlink() or entry.is_junction():
            raise OSError("Unsafe improvement proposal path")
    path.parent.mkdir(parents=True, exist_ok=False)
    payload = markdown.encode("utf-8")
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    digest = hashlib.sha256(payload).hexdigest()
    event = {"schema_version": 1, "event_id": str(uuid4()), "recorded_at": _record_time(config, None).isoformat(), "kind": "proposal", "proposal_id": proposal_id, "task_ref": "MCP proposal " + request_id, "details": {"path": path.relative_to(paths.root).as_posix(), "revision": digest, "observation_ids": observation_ids}}
    validate_improvement_event(event)
    try:
        event_path = root / "events" / (event["event_id"] + ".json")
        evidence = write_record_json(event_path, event)
        return {"proposal": {"kind": "proposal", "id": proposal_id, "revision": None, "path": path.relative_to(paths.root).as_posix(), "sha256": digest, "size_bytes": len(payload)}, "event": {"kind": "improvement-event", "id": event["event_id"], "revision": None, "path": event_path.relative_to(paths.root).as_posix(), "sha256": evidence["sha256"], "size_bytes": evidence["size_bytes"]}}
    except BaseException as error:
        error.add_note(f"Exact proposal bytes retained at {path}")
        raise


def validate_improvement_event(event: JSONObject) -> None:
    """Validate existing protocol event schemas, UUIDs, hashes and measurements.

    Return None without I/O; malformed or nonfinite fields raise ValueError.
    Version 1 retains existing kinds; version 2 permits only assessments.
    Validation does not authenticate an approval or authorize a lifecycle change.
    """
    fields = {"schema_version", "event_id", "recorded_at", "kind", "proposal_id", "task_ref", "details"}
    if not isinstance(event, dict) or set(event) != fields or type(event["schema_version"]) is not int or event["schema_version"] not in (1, 2) or not isinstance(event["event_id"], str) or not isinstance(event["kind"], str) or str(UUID(event["event_id"])) != event["event_id"] or not isinstance(event["task_ref"], str) or not isinstance(event["recorded_at"], str) or datetime.fromisoformat(event["recorded_at"]).utcoffset() is None:
        raise ValueError("Invalid improvement event envelope")
    kind, details = event["kind"], event["details"]
    if event["schema_version"] == 2:
        if kind != "assessment" or event["proposal_id"] is not None:
            raise ValueError("Version 2 is reserved for unassigned assessments")
        _validate_assessment_details(details)
        return
    required = {"observation": {"problem", "evidence", "sources", "criteria", "measurements", "uncertainty"}, "proposal": {"path", "revision", "observation_ids"}, "approval": {"gate", "decision", "approval_ref", "revision"}, "trial_task": {"task_id", "eligibility", "measurements", "quality_outcome", "deviations", "status"}, "transition": {"previous_state", "next_state", "reason", "restoration", "predecessor_event_id"}, "evaluation": {"evidence_ids", "results", "limitations", "recommendation"}}
    if kind not in required or not isinstance(details, dict) or set(details) != required[kind]:
        raise ValueError("Invalid improvement event details")
    if event["proposal_id"] is None:
        if kind != "observation":
            raise ValueError("Only unassigned observations permit null proposal")
    elif str(UUID(event["proposal_id"])) != event["proposal_id"]:
        raise ValueError("Invalid proposal UUID")
    if kind == "observation":
        if any(not isinstance(details[key], str) for key in ("problem", "evidence", "uncertainty")) or not details["problem"].strip() or not details["evidence"].strip() or not isinstance(details["sources"], list) or any(not isinstance(source, str) for source in details["sources"]) or not isinstance(details["criteria"], list) or any(criterion not in ("time", "cost", "user_effort", "quality") for criterion in details["criteria"]):
            raise ValueError("Invalid observation evidence")
    if kind in ("observation", "trial_task"):
        if not isinstance(details["measurements"], list):
            raise ValueError("Invalid measurements")
        for measurement in details["measurements"]:
            if not isinstance(measurement, dict) or set(measurement) != {"criterion", "value", "unit", "method", "basis"} or measurement["criterion"] not in ("time", "cost", "user_effort", "quality") or type(measurement["value"]) not in (int, float) or type(measurement["value"]) is float and not math.isfinite(measurement["value"]) or measurement["basis"] not in ("measured", "estimated") or any(not isinstance(measurement[key], str) for key in ("unit", "method")):
                raise ValueError("Invalid efficiency measurement")
    if kind in ("proposal", "approval") and re.fullmatch(r"[0-9a-f]{64}", str(details["revision"])) is None:
        raise ValueError("Invalid proposal revision hash")
    if kind == "proposal":
        if not isinstance(details["path"], str) or not isinstance(details["observation_ids"], list) or any(str(UUID(identity)) != identity for identity in details["observation_ids"]):
            raise ValueError("Invalid proposal links")
    elif kind == "approval":
        if details["gate"] not in ("trial", "adoption") or details["decision"] not in ("approved", "declined", "more_data") or details["decision"] == "more_data" and details["gate"] != "adoption" or not isinstance(details["approval_ref"], str) or not details["approval_ref"].strip():
            raise ValueError("Invalid approval event")
    elif kind == "trial_task":
        if str(UUID(details["task_id"])) != details["task_id"] or details["status"] not in ("started", "completed", "interrupted") or not isinstance(details["deviations"], list) or any(not isinstance(value, str) for value in details["deviations"]) or any(not isinstance(details[key], str) for key in ("eligibility", "quality_outcome")):
            raise ValueError("Invalid trial task event")
    elif kind == "transition":
        states = ("proposed", "trial_approved", "trial_active", "awaiting_adoption", "adopted", "declined", "rejected", "withdrawn", "restoration_blocked")
        if details["previous_state"] not in (None, *states) or details["next_state"] not in states or details["restoration"] not in ("not_required", "pending", "restored", "conflict") or not isinstance(details["reason"], str) or details["predecessor_event_id"] is not None and str(UUID(details["predecessor_event_id"])) != details["predecessor_event_id"]:
            raise ValueError("Invalid improvement transition")
    elif kind == "evaluation":
        if not isinstance(details["evidence_ids"], list) or any(str(UUID(identity)) != identity for identity in details["evidence_ids"]) or not isinstance(details["results"], list) or not isinstance(details["limitations"], str) or details["recommendation"] not in ("adopt", "decline", "more_data"):
            raise ValueError("Invalid improvement evaluation")
        for result in details["results"]:
            if not isinstance(result, dict) or set(result) != {"criterion", "baseline", "trial", "conclusion"} or result["criterion"] not in ("time", "cost", "user_effort", "quality") or any(not isinstance(value, str) for value in result.values()):
                raise ValueError("Invalid evaluation result")

def _validate_assessment_details(details: JSONObject) -> None:
    """Validate closed assessment details without I/O; return None.

    details must contain canonical UUID arrays, a supported disposition,
    public rationale, uncertainty, follow-up and an optional predecessor.
    Raise ValueError for malformed values; this checks no referenced files.
    """
    fields = {"observation_ids", "disposition", "rationale", "uncertainty", "follow_up", "related_proposal_ids", "predecessor_assessment_id"}
    if not isinstance(details, dict) or set(details) != fields:
        raise ValueError("Invalid assessment details")
    for key in ("observation_ids", "related_proposal_ids"):
        values = details[key]
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise ValueError("Invalid assessment UUID array")
        if len(values) != len(set(values)) or any(str(UUID(value)) != value for value in values):
            raise ValueError("Invalid assessment UUID array")
    predecessor = details["predecessor_assessment_id"]
    if predecessor is not None and (not isinstance(predecessor, str) or str(UUID(predecessor)) != predecessor):
        raise ValueError("Invalid assessment predecessor")
    if not details["observation_ids"] or details["disposition"] not in ("proposal_needed", "deferred_insufficient_evidence", "already_addressed", "no_reusable_change"):
        raise ValueError("Invalid assessment disposition")
    if any(not isinstance(details[key], str) for key in ("rationale", "uncertainty", "follow_up")) or not details["rationale"].strip():
        raise ValueError("Invalid assessment explanation")
    if details["disposition"] in ("proposal_needed", "deferred_insufficient_evidence") and not details["follow_up"].strip():
        raise ValueError("Assessment requires follow-up")
    if (details["disposition"] == "already_addressed") != bool(details["related_proposal_ids"]):
        raise ValueError("Only already-addressed assessments link existing proposals")


def _summarize_assessments(
    events: dict[str, JSONObject],
    proposal_states: list[JSONObject],
    proposal_id: str | None,
) -> tuple[list[JSONObject], list[JSONObject], list[JSONObject]]:
    """Derive assessments, pending findings and issues without I/O or writes.

    events contains structurally validated UUID-keyed events; proposal_states
    contains existing hash/lifecycle verification results. proposal_id optionally
    limits summaries to observations linked by valid proposals with that UUID.
    Return three lists; invalid references, overlapping groups and broken chains
    are reported as issues and conflict summaries rather than accepted decisions.
    """
    valid_ids = {state["proposal_id"] for state in proposal_states if state["valid"]}
    links: dict[str, set[str]] = {}
    for event in events.values():
        if event["kind"] == "proposal" and event["proposal_id"] in valid_ids:
            links.setdefault(event["proposal_id"], set()).update(event["details"]["observation_ids"])
    selected = None if proposal_id is None else links.get(proposal_id, set())
    groups: dict[tuple[str, ...], list[JSONObject]] = {}
    for event in events.values():
        if event["kind"] == "assessment":
            key = tuple(sorted(event["details"]["observation_ids"]))
            groups.setdefault(key, []).append(event)
    summaries: list[JSONObject] = []
    pending: list[JSONObject] = []
    issues: list[JSONObject] = []
    assigned = {identity for key in groups for identity in key}
    for key, records in sorted(groups.items()):
        identities = {event["event_id"] for event in records}
        errors: list[str] = []
        if any(events.get(identity, {}).get("kind") != "observation" for identity in key):
            errors.append("Assessment references a missing or invalid observation")
        if any(set(key).intersection(other) for other in groups if other != key):
            errors.append("Overlapping assessment observation groups")
        successors: dict[str | None, list[str]] = {}
        for event in records:
            details = event["details"]
            predecessor = details["predecessor_assessment_id"]
            if predecessor is not None and predecessor not in identities:
                errors.append("Assessment predecessor is missing or belongs to another group")
            successors.setdefault(predecessor, []).append(event["event_id"])
            related = details["related_proposal_ids"]
            if related and (any(identity not in links for identity in related) or not set(key) <= set().union(*(links.get(identity, set()) for identity in related))):
                errors.append("Assessment proposal links do not validly cover the observations")
        visited: set[str] = set()
        current = None
        latest = None
        while successors.get(current):
            children = successors[current]
            if len(children) != 1 or children[0] in visited:
                errors.append("Conflicting or cyclic assessment successors")
                break
            current = children[0]
            visited.add(current)
            latest = events[current]
        if visited != identities:
            errors.append("Orphaned or cyclic assessment chain")
        if selected is not None and not set(key).intersection(selected):
            visible = False
        else:
            visible = True
        if errors:
            issues.append({"assessment_event_ids": sorted(identities), "message": "; ".join(sorted(set(errors)))})
            if visible:
                summaries.append({"observation_ids": list(key), "event_id": None, "valid": False, "disposition": None, "rationale": "", "uncertainty": "", "follow_up": "", "related_proposal_ids": [], "covering_proposal_ids": []})
                pending.append({"observation_ids": list(key), "assessment_event_id": None, "reason": "conflict"})
            continue
        if latest is None:
            continue
        details = latest["details"]
        covering = sorted(identity for identity, observations in links.items() if set(key).intersection(observations))
        covered = set().union(*(links[identity] for identity in covering)) if covering else set()
        reason = "proposal_required" if details["disposition"] == "proposal_needed" and not set(key) <= covered else "deferred" if details["disposition"] == "deferred_insufficient_evidence" else None
        if visible:
            summaries.append({"observation_ids": list(key), "event_id": latest["event_id"], "valid": True, "disposition": details["disposition"], "rationale": details["rationale"], "uncertainty": details["uncertainty"], "follow_up": details["follow_up"], "related_proposal_ids": details["related_proposal_ids"], "covering_proposal_ids": covering})
            if reason is not None:
                pending.append({"observation_ids": list(key), "assessment_event_id": latest["event_id"], "reason": reason})
    for identity, event in sorted(events.items()):
        if event["kind"] == "observation" and identity not in assigned and (selected is None or identity in selected):
            pending.append({"observation_ids": [identity], "assessment_event_id": None, "reason": "unassessed"})
    return summaries, pending, issues


def record_assessment(paths: ProjectPaths, config: Config, details: JSONObject, request_id: str) -> JSONObject:
    """Preserve one immutable assessment of existing observations.

    paths selects the local store, config supplies record time, details follows
    assessment v2, and request_id is a canonical UUID for traceability. Return
    event/reference using the existing observation result shape. Validate the
    candidate against current references/chains before writing. Raise ValueError
    for malformed inputs or conflicting evidence; propagate filesystem errors
    while preserving partial output. Never approve, apply or activate a change.
    This primary-writer procedure provides no cross-process transaction lock.
    """
    if not isinstance(request_id, str) or str(UUID(request_id)) != request_id:
        raise ValueError("Invalid request UUID")
    event = {"schema_version": 2, "event_id": str(uuid4()), "recorded_at": _record_time(config, None).isoformat(), "kind": "assessment", "proposal_id": None, "task_ref": "MCP assessment " + request_id, "details": details}
    validate_improvement_event(event)
    status = read_improvement_status(paths)
    events = {item["event_id"]: item for item in status["events"]}
    events[event["event_id"]] = event
    summaries, pending, issues = _summarize_assessments(events, status["proposals"], None)
    if any(event["event_id"] in issue["assessment_event_ids"] for issue in issues):
        raise ValueError("Assessment references or chain conflict")
    if not any(summary["event_id"] == event["event_id"] and summary["valid"] for summary in summaries):
        raise ValueError("Assessment does not extend an unambiguous chain")
    path = paths.root / ".local" / "workflow-improvement" / "events" / (event["event_id"] + ".json")
    evidence = write_record_json(path, event)
    return {"event": event, "reference": {"kind": "improvement-event", "id": event["event_id"], "revision": None, "path": path.relative_to(paths.root).as_posix(), "sha256": evidence["sha256"], "size_bytes": evidence["size_bytes"]}}
