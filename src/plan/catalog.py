"""Discover immutable reusable capabilities and preserve unapproved candidates safely."""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
import errno
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Literal
from uuid import UUID, uuid4

from jsonschema import Draft202012Validator

from src.backend.config import Config
from src.backend.paths import ProjectPaths
from src.backend.project_records import write_record_json
from src.backend.records import _lock_parent, _record_time
from src.srim.metadata import JSONObject, _read_json
from src.srim.runtime import _read_regular_bytes


def list_collections(paths: ProjectPaths) -> JSONObject:
    """Return paths' three collection counts/issues without creating missing storage.

    Read safe manifests only; independent corrupt entries remain visible as issues.
    """
    collections = []
    for collection in ("workflow", "analysis", "skill"):
        result = list_capabilities(paths, collection)
        collections.append({"collection": collection, "total": result["total"], "issues": result["issues"]})
    return {"collections": collections}


def list_capabilities(paths: ProjectPaths, collection: Literal["workflow", "analysis", "skill"], *, offset: int = 0, limit: int = 100) -> JSONObject:
    """List immutable capability versions for collection with integer pagination.

    Return verified references and issues; malformed filters raise ValueError.
    Missing storage is empty and never initialized as a side effect.
    """
    if collection not in ("workflow", "analysis", "skill") or type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("Invalid reusable collection query")
    root = paths.root / "memory" / "project" / "capabilities" / collection
    matches, issues = [], []
    for entry in sorted(root.glob("*/v*/manifest.json") if root.exists() else []):
        try:
            payload = _read_regular_bytes(entry)
            reference = {"collection": collection, "id": entry.parent.parent.name, "version": entry.parent.name, "manifest_sha256": hashlib.sha256(payload).hexdigest()}
            result = read_capability(paths, reference)
            matches.append({"reference": reference, "title": result["manifest"]["title"], "description": result["manifest"]["description"]})
        except (OSError, ValueError) as error:
            issues.append({"path": str(entry.relative_to(paths.root)), "message": str(error)})
    return {"collection": collection, "total": len(matches), "offset": offset, "limit": limit, "matches": matches[offset:offset + limit], "issues": issues}


def read_capability(paths: ProjectPaths, reference: JSONObject) -> JSONObject:
    """Verify reference's exact immutable manifest, files and adoption evidence in paths.

    Return manifest/reference without executing its procedure. Invalid identity,
    approval/hash/schema/source evidence raises ValueError/OSError.
    """
    if not isinstance(reference, dict) or set(reference) != {"collection", "id", "version", "manifest_sha256"} or reference["collection"] not in ("workflow", "analysis", "skill") or not isinstance(reference["id"], str) or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", reference["id"]) is None or len(reference["id"]) > 64 or os.path.isreserved(reference["id"]) or re.fullmatch(r"v[1-9][0-9]*", str(reference["version"])) is None or re.fullmatch(r"[0-9a-f]{64}", str(reference["manifest_sha256"])) is None:
        raise ValueError("Invalid reusable capability reference")
    root = paths.root / "memory" / "project" / "capabilities" / reference["collection"] / reference["id"] / reference["version"]
    payload = _read_regular_bytes(root / "manifest.json")
    if hashlib.sha256(payload).hexdigest() != reference["manifest_sha256"]:
        raise ValueError("integrity: reusable manifest mismatch")
    manifest = _read_json(root / "manifest.json")
    validate_capability(manifest)
    if any(manifest[key] != reference[key] for key in ("collection", "id", "version")):
        raise ValueError("integrity: capability identity mismatch")
    for file in manifest["files"]:
        content = _read_regular_bytes(root / "files" / file["path"])
        if len(content) != file["size_bytes"] or hashlib.sha256(content).hexdigest() != file["sha256"]:
            raise ValueError("integrity: capability file mismatch")
    approval = manifest["approval_reference"]
    event_path = paths.root / ".cache" / "workflow-improvement" / "events" / (approval["approval_event_id"] + ".json")
    event_bytes = _read_regular_bytes(event_path)
    from src.plan.improvement import validate_improvement_event
    event = _read_json(event_path)
    validate_improvement_event(event)
    if hashlib.sha256(event_bytes).hexdigest() != approval["approval_event_sha256"] or event["kind"] != "approval" or event["proposal_id"] != approval["proposal_id"] or event["details"]["gate"] != "adoption" or event["details"]["decision"] != "approved" or event["details"]["revision"] != approval["proposal_revision_sha256"]:
        raise ValueError("integrity: capability adoption evidence mismatch")
    proposal = paths.root / ".cache" / "workflow-improvement" / "proposals" / approval["proposal_id"] / ("proposal-" + approval["proposal_revision_sha256"] + ".md")
    if not proposal.exists():
        proposal = proposal.with_name("proposal.md")
    proposal_bytes = _read_regular_bytes(proposal)
    if hashlib.sha256(proposal_bytes).hexdigest() != approval["proposal_revision_sha256"]:
        raise ValueError("integrity: adopted proposal bytes missing or changed")
    candidate_ids = set(re.findall(r"candidate_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", proposal_bytes.decode("utf-8")))
    matching_candidates = []
    for candidate_id in candidate_ids:
        candidate = read_candidate(paths, candidate_id)
        if candidate["reference"]["sha256"] in proposal_bytes.decode("utf-8") and {key: value for key, value in manifest.items() if key not in ("version", "approval_reference")} == candidate["candidate"]["proposed_manifest"]:
            matching_candidates.append(candidate_id)
    if len(matching_candidates) != 1:
        raise ValueError("integrity: published capability differs from adopted candidate")
    return {"manifest": manifest, "reference": deepcopy(reference)}


def validate_capability(manifest: JSONObject) -> None:
    """Validate a closed declarative capability manifest without I/O or execution.

    Check schemas, safe file inventory, identity, procedure and approval reference;
    malformed values raise ValueError and no approval is inferred from text.
    """
    fields = {"schema_version", "collection", "id", "version", "title", "description", "input_schema", "output_schema", "prerequisites", "procedure", "evidence", "limitations", "files", "approval_reference"}
    if not isinstance(manifest, dict) or set(manifest) != fields or type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1 or manifest["collection"] not in ("workflow", "analysis", "skill") or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", str(manifest["id"])) is None or len(manifest["id"]) > 64 or os.path.isreserved(manifest["id"]) or re.fullmatch(r"v[1-9][0-9]*", str(manifest["version"])) is None:
        raise ValueError("Invalid reusable capability schema/identity")
    json.dumps(manifest, allow_nan=False)
    if any(not isinstance(manifest[key], str) or not manifest[key].strip() for key in ("title", "description")):
        raise ValueError("Capability needs title/description")
    for key in ("prerequisites", "evidence", "limitations"):
        if not isinstance(manifest[key], list) or any(not isinstance(value, str) for value in manifest[key]):
            raise ValueError("Invalid capability descriptive lists")
    for key in ("input_schema", "output_schema"):
        schema = manifest[key]
        if not isinstance(schema, dict) or schema.get("type") != "object" or schema.get("additionalProperties") is not False:
            raise ValueError("Capability schemas must define closed objects")
        try:
            Draft202012Validator.check_schema(schema)
        except Exception as error:
            raise ValueError("Invalid capability JSON Schema") from error
        pending = [schema]
        while pending:
            item = pending.pop()
            if isinstance(item, dict):
                for reference_key in ("$ref", "$dynamicRef"):
                    if reference_key in item and (not isinstance(item[reference_key], str) or not item[reference_key].startswith("#")):
                        raise ValueError("Remote schema references are forbidden")
                pending.extend(item.values())
            elif isinstance(item, list):
                pending.extend(item)
    if not isinstance(manifest["procedure"], list) or not manifest["procedure"]:
        raise ValueError("Capability needs a declarative procedure")
    seen = set()
    for step in manifest["procedure"]:
        if not isinstance(step, dict) or set(step) != {"step_id", "instruction", "tool_names"} or not isinstance(step["step_id"], str) or not step["step_id"] or step["step_id"] in seen or not isinstance(step["instruction"], str) or not step["instruction"].strip() or not isinstance(step["tool_names"], list) or any(not isinstance(value, str) for value in step["tool_names"]):
            raise ValueError("Invalid procedure step")
        seen.add(step["step_id"])
    seen = set()
    if not isinstance(manifest["files"], list):
        raise ValueError("Invalid capability file inventory")
    for file in manifest["files"]:
        if not isinstance(file, dict) or set(file) != {"path", "sha256", "size_bytes"} or not isinstance(file["path"], str) or "\\" in file["path"] or any(part in ("", ".", "..") or os.path.isreserved(part) or re.search(r'[<>:"|?*\x00-\x1f]', part) or part.endswith((".", " ")) for part in file["path"].split("/")) or file["path"].casefold() in seen or type(file["size_bytes"]) is not int or file["size_bytes"] < 0 or re.fullmatch(r"[0-9a-f]{64}", str(file["sha256"])) is None:
            raise ValueError("Invalid capability file")
        seen.add(file["path"].casefold())
    approval = manifest["approval_reference"]
    if not isinstance(approval, dict) or set(approval) != {"proposal_id", "proposal_revision_sha256", "approval_event_id", "approval_event_sha256"}:
        raise ValueError("Invalid capability approval reference")
    for field in ("proposal_id", "approval_event_id"):
        if str(UUID(approval[field])) != approval[field]:
            raise ValueError("Invalid approval UUID")
    for field in ("proposal_revision_sha256", "approval_event_sha256"):
        if re.fullmatch(r"[0-9a-f]{64}", str(approval[field])) is None:
            raise ValueError("Invalid approval hash")


def create_candidate(paths: ProjectPaths, config: Config, spec: JSONObject, files: list[JSONObject], request_id: str, *, allowed_roots: Sequence[Path]) -> JSONObject:
    """Publish an immutable unapproved spec and copied files under paths/config.

    Validate request_id, allowed_roots, candidate schemas and 64/256-MiB copy
    bounds. Return candidate evidence; no promotion or execution. Errors retain
    partial paths and propagate as ValueError/OSError.
    """
    if str(UUID(request_id)) != request_id or not isinstance(spec, dict) or set(spec) != {"collection", "proposed_manifest", "rationale", "evidence_references"} or spec["collection"] not in ("workflow", "analysis", "skill") or not isinstance(spec["rationale"], str) or not spec["rationale"].strip() or not isinstance(spec["evidence_references"], list) or not isinstance(files, list) or not allowed_roots:
        raise ValueError("Invalid candidate request")
    proposed = deepcopy(spec["proposed_manifest"])
    if not isinstance(proposed, dict) or "approval_reference" in proposed or "version" in proposed or proposed.get("collection") != spec["collection"]:
        raise ValueError("Candidate cannot claim approval/version")
    trial = deepcopy(proposed)
    trial.update(version="v1", approval_reference={"proposal_id": str(UUID(int=0)), "proposal_revision_sha256": "0" * 64, "approval_event_id": str(UUID(int=0)), "approval_event_sha256": "0" * 64})
    validate_capability(trial)
    for reference in spec["evidence_references"]:
        if not isinstance(reference, dict) or set(reference) != {"kind", "id", "revision", "path", "sha256", "size_bytes"} or not isinstance(reference["path"], str) or "\\" in reference["path"] or any(part in ("", ".", "..") or os.path.isreserved(part) or ":" in part for part in reference["path"].split("/")) or re.fullmatch(r"[0-9a-f]{64}", str(reference["sha256"])) is None or type(reference["size_bytes"]) is not int or not 0 <= reference["size_bytes"] <= 67108864:
            raise ValueError("Invalid candidate evidence reference")
        evidence_bytes = _read_regular_bytes(paths.root / reference["path"])
        if len(evidence_bytes) != reference["size_bytes"] or hashlib.sha256(evidence_bytes).hexdigest() != reference["sha256"]:
            raise ValueError("integrity: candidate evidence reference changed")
    selected, inventory, total, seen = [], [], 0, set()
    for file in files:
        if not isinstance(file, dict) or set(file) != {"name", "role", "source_path", "media_type"} or not isinstance(file["name"], str) or file["name"].casefold() in seen:
            raise ValueError("Invalid candidate file specification")
        name = file["name"]
        if "\\" in name or any(part in ("", ".", "..") or os.path.isreserved(part) or re.search(r'[<>:"|?*\x00-\x1f]', part) or part.endswith((".", " ")) for part in name.split("/")):
            raise ValueError("Unsafe candidate file name")
        source = Path(file["source_path"])
        if not source.is_absolute() or ".." in source.parts or not any(isinstance(root, Path) and root.is_absolute() and source.is_relative_to(root) for root in allowed_roots):
            raise ValueError("unsafe-path: candidate source outside allowed roots")
        size = source.stat().st_size
        total += size
        if size > 67108864 or total > 268435456:
            raise ValueError("too-large: candidate file cap exceeded")
        before = source.stat()
        payload = _read_regular_bytes(source)
        if len(payload) != size or source.stat() != before:
            raise ValueError("integrity: candidate source changed")
        selected.append((name, payload, source))
        inventory.append({"path": name, "sha256": hashlib.sha256(payload).hexdigest(), "size_bytes": len(payload)})
        seen.add(name.casefold())
    if sorted(inventory, key=lambda item: item["path"]) != sorted(proposed["files"], key=lambda item: item["path"]):
        raise ValueError("Candidate supporting files differ from proposed inventory")
    identifier = "candidate_" + str(uuid4())
    root = paths.root / "memory" / "project" / "candidates" / identifier
    for entry in (*reversed(root.parents), root):
        if entry.is_symlink() or entry.is_junction():
            raise OSError(errno.ELOOP, "Unsafe candidate destination", str(entry))
    root.mkdir(parents=True)
    try:
        for name, payload, source in selected:
            target = root / "files" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            if _read_regular_bytes(target) != payload or _read_regular_bytes(source) != payload:
                raise ValueError("integrity: candidate file changed")
        result = {"schema_version": 1, "id": identifier, "collection": spec["collection"], "proposed_manifest": proposed, "rationale": spec["rationale"], "evidence_references": deepcopy(spec["evidence_references"]), "created_at": _record_time(config, None).isoformat(), "request_id": request_id, "files": inventory}
        evidence = write_record_json(root / "candidate.json", result)
        return {"candidate": result, "reference": {"kind": "capability", "id": identifier, "revision": None, "path": str((root / "candidate.json").relative_to(paths.root)).replace("\\", "/"), "sha256": evidence["sha256"], "size_bytes": evidence["size_bytes"]}}
    except BaseException as error:
        error.add_note(f"Candidate evidence retained at {root}")
        raise


def list_candidates(paths: ProjectPaths, *, collection: str | None = None, offset: int = 0, limit: int = 100) -> JSONObject:
    """List paths' pending candidates by collection and pagination with separate issues.

    Return independent summaries without promotion/writes; invalid query raises.
    """
    if collection not in (None, "workflow", "analysis", "skill") or type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("Invalid candidate query")
    root = paths.root / "memory" / "project" / "candidates"
    matches, issues = [], []
    for entry in sorted(root.glob("candidate_*/candidate.json") if root.exists() else []):
        try:
            result = read_candidate(paths, entry.parent.name)
            if collection is None or result["candidate"]["collection"] == collection:
                matches.append({"id": entry.parent.name, "collection": result["candidate"]["collection"], "reference": result["reference"]})
        except (ValueError, OSError) as error:
            issues.append({"id": entry.parent.name, "message": str(error)})
    return {"total": len(matches), "offset": offset, "limit": limit, "matches": matches[offset:offset + limit], "issues": issues}


def read_candidate(paths: ProjectPaths, candidate_id: str) -> JSONObject:
    """Read candidate_id with exact supporting file verification and return evidence.

    Reject malformed identity/schema, missing or changed immutable files; no writes.
    """
    if not isinstance(candidate_id, str) or not candidate_id.startswith("candidate_") or str(UUID(candidate_id[10:])) != candidate_id[10:]:
        raise ValueError("Invalid candidate identity")
    root = paths.root / "memory" / "project" / "candidates" / candidate_id
    payload = _read_regular_bytes(root / "candidate.json")
    document = _read_json(root / "candidate.json")
    if set(document) != {"schema_version", "id", "collection", "proposed_manifest", "rationale", "evidence_references", "created_at", "request_id", "files"} or type(document["schema_version"]) is not int or document["schema_version"] != 1 or document["id"] != candidate_id or document["collection"] != document["proposed_manifest"]["collection"]:
        raise ValueError("Invalid candidate document")
    proposed = deepcopy(document["proposed_manifest"])
    if "version" in proposed or "approval_reference" in proposed:
        raise ValueError("Candidate illegally claims approval")
    proposed.update(version="v1", approval_reference={"proposal_id": str(UUID(int=0)), "proposal_revision_sha256": "0" * 64, "approval_event_id": str(UUID(int=0)), "approval_event_sha256": "0" * 64})
    validate_capability(proposed)
    if sorted(document["files"], key=lambda item: item["path"]) != sorted(document["proposed_manifest"]["files"], key=lambda item: item["path"]):
        raise ValueError("Candidate inventory mismatch")
    for reference in document["evidence_references"]:
        if not isinstance(reference, dict) or set(reference) != {"kind", "id", "revision", "path", "sha256", "size_bytes"} or not isinstance(reference["path"], str) or "\\" in reference["path"] or any(part in ("", ".", "..") or os.path.isreserved(part) or ":" in part for part in reference["path"].split("/")):
            raise ValueError("Invalid candidate evidence path")
        content = _read_regular_bytes(paths.root / reference["path"])
        if len(content) != reference["size_bytes"] or hashlib.sha256(content).hexdigest() != reference["sha256"]:
            raise ValueError("integrity: candidate evidence changed")
    for file in document["files"]:
        content = _read_regular_bytes(root / "files" / file["path"])
        if len(content) != file["size_bytes"] or hashlib.sha256(content).hexdigest() != file["sha256"]:
            raise ValueError("integrity: candidate file changed")
    return {"candidate": document, "reference": {"kind": "capability", "id": candidate_id, "revision": None, "path": (root / "candidate.json").relative_to(paths.root).as_posix(), "sha256": hashlib.sha256(payload).hexdigest(), "size_bytes": len(payload)}}


def publish_capability(paths: ProjectPaths, config: Config, candidate_id: str, approval_reference: JSONObject, *, expected_candidate_sha256: str) -> JSONObject:
    """Publish candidate_id as a new immutable vN under explicit approved evidence.

    Verify expected_candidate_sha256 and adoption evidence mechanically; the
    coordinating human-authorized workflow remains responsible for real consent.
    Return version reference, never edit skills or execute procedures; errors retain data.
    """
    candidate = read_candidate(paths, candidate_id)
    if candidate["reference"]["sha256"] != expected_candidate_sha256:
        raise ValueError("conflict: candidate changed")
    manifest = deepcopy(candidate["candidate"]["proposed_manifest"])
    manifest.update(version="v1", approval_reference=deepcopy(approval_reference))
    validate_capability(manifest)
    from src.plan.improvement import read_improvement_status
    status = read_improvement_status(paths, proposal_id=approval_reference["proposal_id"])
    if status["issues"]:
        raise ValueError("Conflicting or malformed improvement evidence")
    matching = [event for event in status["events"] if event["event_id"] == approval_reference["approval_event_id"] and event["kind"] == "approval" and event["details"]["gate"] == "adoption" and event["details"]["decision"] == "approved" and event["details"]["revision"] == approval_reference["proposal_revision_sha256"]]
    if len(matching) != 1:
        raise ValueError("Matching explicit adoption approval is required")
    evidence_root = paths.root / ".cache" / "workflow-improvement"
    approval_bytes = _read_regular_bytes(evidence_root / "events" / (approval_reference["approval_event_id"] + ".json"))
    if hashlib.sha256(approval_bytes).hexdigest() != approval_reference["approval_event_sha256"]:
        raise ValueError("integrity: approval event hash mismatch")
    proposal_path = evidence_root / "proposals" / approval_reference["proposal_id"] / ("proposal-" + approval_reference["proposal_revision_sha256"] + ".md")
    if not proposal_path.exists():
        proposal_path = proposal_path.with_name("proposal.md")
    proposal_bytes = _read_regular_bytes(proposal_path)
    if hashlib.sha256(proposal_bytes).hexdigest() != approval_reference["proposal_revision_sha256"] or candidate_id not in proposal_bytes.decode("utf-8") or expected_candidate_sha256 not in proposal_bytes.decode("utf-8"):
        raise ValueError("integrity: adoption proposal does not bind exact candidate")
    root = paths.root / "memory" / "project" / "capabilities" / manifest["collection"] / manifest["id"]
    for entry in (*reversed(root.parents), root):
        if entry.is_symlink() or entry.is_junction():
            raise OSError(errno.ELOOP, "Unsafe capability destination", str(entry))
    root.mkdir(parents=True, exist_ok=True)
    with _lock_parent(root, timeout_seconds=30.0):
        versions = [int(entry.name[1:]) for entry in root.iterdir() if re.fullmatch(r"v[1-9][0-9]*", entry.name)]
        manifest["version"] = "v" + str(max(versions, default=0) + 1)
        target = root / manifest["version"]
        target.mkdir()
        for file in manifest["files"]:
            payload = _read_regular_bytes(paths.root / "memory" / "project" / "candidates" / candidate_id / "files" / file["path"])
            if len(payload) != file["size_bytes"] or hashlib.sha256(payload).hexdigest() != file["sha256"]:
                raise ValueError("integrity: candidate file changed before publication")
            destination = target / "files" / file["path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            if _read_regular_bytes(destination) != payload:
                raise ValueError("integrity: capability copy changed")
        if read_candidate(paths, candidate_id)["reference"]["sha256"] != expected_candidate_sha256 or hashlib.sha256(_read_regular_bytes(proposal_path)).hexdigest() != approval_reference["proposal_revision_sha256"] or hashlib.sha256(_read_regular_bytes(evidence_root / "events" / (approval_reference["approval_event_id"] + ".json"))).hexdigest() != approval_reference["approval_event_sha256"]:
            raise ValueError("integrity: candidate or approval evidence changed before publication")
        evidence = write_record_json(target / "manifest.json", manifest)
        reference = {"collection": manifest["collection"], "id": manifest["id"], "version": manifest["version"], "manifest_sha256": evidence["sha256"]}
        return read_capability(paths, reference)


def inventory_repository_skills(paths: ProjectPaths) -> JSONObject:
    """Read fixed repository skill/agent metadata and hashes without registration.

    Return inventories/issues; unsafe, malformed independent entries remain issues.
    Never edit instructions, start a trial or spawn an agent.
    """
    matches, issues = [], []
    files = sorted((paths.root / ".agents" / "skills").glob("*/SKILL.md")) + sorted((paths.root / ".codex" / "agents").glob("*.toml"))
    for path in files:
        try:
            payload = _read_regular_bytes(path)
            text = payload.decode("utf-8")
            if path.suffix == ".toml":
                import tomllib
                metadata = tomllib.loads(text)
                name, description = metadata.get("name", path.stem), metadata.get("description", "")
            else:
                normalized = text.replace("\r\n", "\n")
                if not normalized.startswith("---\n") or "\n---\n" not in normalized[4:]:
                    raise ValueError("Missing closed skill frontmatter")
                frontmatter = normalized[4:].split("\n---\n", 1)[0]
                name_match = re.search(r"(?m)^name:\s*(.+)$", frontmatter)
                description_match = re.search(r"(?m)^description:\s*(.+)$", frontmatter)
                if name_match is None or description_match is None:
                    raise ValueError("Missing skill frontmatter")
                name, description = name_match[1].strip("\"'"), description_match[1].strip("\"'")
            if not isinstance(name, str) or not name.strip() or not isinstance(description, str):
                raise ValueError("Invalid skill/agent name or description")
            matches.append({"name": name, "description": description, "path": path.relative_to(paths.root).as_posix(), "sha256": hashlib.sha256(payload).hexdigest(), "size_bytes": len(payload), "registered": False})
        except (OSError, ValueError, UnicodeError) as error:
            issues.append({"path": path.relative_to(paths.root).as_posix(), "message": str(error)})
    return {"entries": matches, "issues": issues}
