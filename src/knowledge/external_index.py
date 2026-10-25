"""Stage explicit external imports and publish immutable versioned input/summary catalogs."""
from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
from uuid import UUID, uuid4
from time import monotonic

from src.backend.paths import ProjectPaths, get_project_paths
from src.backend.records import _lock_parent, _record_time
from src.backend.config import load_config
from src.backend.resources import _unique_object
from src.knowledge.external_schema import FIELDS, match_external_record, validate_external_document, compile_external_query, project_external_record, match_compiled_external_record
from src.knowledge.external_sources import SOURCE_DEADLINE, SOURCE_PROGRESS, fingerprint_external_source, iter_snumat_cases, read_external_evidence, resolve_external_source, verify_external_evidence
from src.knowledge.snumat_adapter import normalize_snumat_case
from src.srim.common.files import _read_regular_bytes, _write_input_file, _write_json
from src.srim.common.types import JSONObject

TABLES = (
    "CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)",
    "CREATE TABLE records(source_id TEXT,case_id TEXT,record_sha256 TEXT,payload_json TEXT,PRIMARY KEY(source_id,case_id,record_sha256))",
    "CREATE TABLE heads(source_id TEXT,case_id TEXT,record_sha256 TEXT,presence TEXT,PRIMARY KEY(source_id,case_id))",
    "CREATE TABLE bindings(source_id TEXT,case_id TEXT,record_sha256 TEXT,snapshot_sha256 TEXT,PRIMARY KEY(source_id,case_id,record_sha256,snapshot_sha256))",
    "CREATE TABLE diagnostics(operation_id TEXT,ordinal INTEGER,payload_json TEXT,PRIMARY KEY(operation_id,ordinal))",
)
TABLE_COLUMNS = {
    "meta": ["key", "value"],
    "records": ["source_id", "case_id", "record_sha256", "payload_json"],
    "heads": ["source_id", "case_id", "record_sha256", "presence"],
    "bindings": ["source_id", "case_id", "record_sha256", "snapshot_sha256"],
    "diagnostics": ["operation_id", "ordinal", "payload_json"],
}


def preview_external_import(paths: ProjectPaths, source_id: str, *, cancel: Callable[[], bool], progress: Callable[[JSONObject], None], timeout_seconds: int = 600, resume_operation_id: str | None = None) -> JSONObject:
    """Stage a fully verified source preview without searchable publication.

    resume_operation_id optionally selects a canonical UUID for a complete failed
    preview. Cached objects and current inventory must match exactly; only derived
    immutable record files are hardlinked into a new operation. No normalization
    is repeated and original failed evidence remains untouched.
    timeout_seconds is an integer in 1..3600 controlling the whole operation.
    paths/source_id select a registration; cancel and progress are cooperative
    job callbacks. Return a closed ready/failed/cancelled preview with counts,
    fingerprints and storage measurements. One explicit timeout_seconds deadline (600 seconds by default) spans all
    source passes and staging work; timeouts produce failed previews. Staging survives for review. Source
    and catalog are unchanged. Invalid/unsafe paths or catalogs raise ValueError,
    OSError or sqlite3.Error; source processing errors become failed diagnostics.
    """
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 3600:
        raise ValueError('external: timeout_seconds must be an integer in 1..3600')
    if resume_operation_id is not None and (not isinstance(resume_operation_id, str) or re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", resume_operation_id) is None or str(UUID(resume_operation_id)) != resume_operation_id):
        raise ValueError("external: resume_operation_id must be a canonical UUID")
    operation_deadline = monotonic() + timeout_seconds
    deadline_token = SOURCE_DEADLINE.set(operation_deadline)
    try:
        if paths != get_project_paths(paths.root) or not paths.root.is_dir():
            raise ValueError("external: invalid project layout")
        if monotonic() >= operation_deadline:
            raise ValueError("external: operation timeout exceeded")
        resolve_external_source(paths, source_id)
        directory = paths.root / "memory/knowledge/external"
        for entry in (*reversed(directory.parents), directory):
            if entry.is_symlink() or entry.is_junction():
                raise OSError("external: unsafe catalog directory")
        status = read_external_status(paths, source_id)
        operation_id = str(uuid4())
        stage = directory / "staging" / operation_id
        for entry in (directory / "staging", stage, stage / "records"):
            if entry.is_symlink() or entry.is_junction():
                raise OSError("external: unsafe staging directory")
        (stage / "records").mkdir(parents=True, exist_ok=False)
        preview = {
            "schema_version": 1, "operation_id": operation_id, "source_id": source_id,
            "adapter_id": "snumat-trim-v1", "adapter_version": 1,
            "base_generation": status["generation"],
            "source_fingerprints": {"source_id": source_id, "adapter_id": "snumat-trim-v1", "adapter_version": 1, "files": []},
            "discovered": 0, "accepted": 0, "rejected": 0, "added": 0, "changed": 0, "unchanged": 0,
            "missing": 0, "fields": list(FIELDS), "units": {key: value[1] for key, value in FIELDS.items()},
            "unavailable_outputs": [], "diagnostics": [], "storage_bytes": {},
            "plan_sha256": None, "state": "failed",
        }
        heads, discovered, accepted, unavailable = {}, set(), [], set()
        database = directory / "index.sqlite"
        if database.exists():
            connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=30)
            try:
                connection.enable_load_extension(False)
                connection.execute("PRAGMA query_only=ON")
                heads = {row[0]: row[1] for row in connection.execute("SELECT case_id,record_sha256 FROM heads WHERE source_id=?", (source_id,))}
            finally:
                connection.close()
        progress_token = SOURCE_PROGRESS.set(progress)
        try:
            if cancel():
                raise InterruptedError("external: cancelled before preview")
            progress({"source": "adapter", "phase": "indexing", "worker_ids": [], "simulation_ids": [], "data": {"stage": "fingerprinting", "operation_id": operation_id}})
            if monotonic() >= operation_deadline:
                raise ValueError("external: operation timeout exceeded")
            fingerprints = fingerprint_external_source(paths, source_id, cancel=cancel)
            preview["source_fingerprints"] = fingerprints
            if monotonic() >= operation_deadline:
                raise ValueError("external: operation timeout exceeded")
            cached, rejected_ids = {}, set()
            if resume_operation_id is not None:
                origin = directory / "staging" / resume_operation_id
                for entry in (origin, origin / "records", origin / "preview.json"):
                    if entry.is_symlink() or entry.is_junction():
                        raise OSError("external: unsafe resume path")
                prior = json.loads(_read_regular_bytes(origin / "preview.json"), object_pairs_hook=_unique_object)
                validate_external_document(prior, "preview")
                if any(prior[key] != preview[key] for key in ("schema_version", "source_id", "adapter_id", "adapter_version", "base_generation")) or prior["operation_id"] != resume_operation_id or prior["state"] != "failed" or prior["plan_sha256"] is not None or prior["source_fingerprints"] != fingerprints:
                    raise ValueError("external: stale or incompatible resume preview")
                rejected = [issue for issue in prior["diagnostics"] if issue.get("code") == "rejected"]
                for issue in rejected:
                    case_id = issue.get("case_id")
                    if not isinstance(case_id, str) or re.fullmatch(r"SM-[0-9]+_[A-Za-z]+_(p|n|cov)_d[0-9]+nm", case_id) is None or case_id in rejected_ids:
                        raise ValueError("external: duplicate or invalid resume rejection identity")
                    rejected_ids.add(case_id)
                for entry in (origin / "records").iterdir():
                    if cancel():
                        raise InterruptedError("external: cancelled validating resume cache")
                    if monotonic() >= operation_deadline:
                        raise ValueError("external: operation timeout exceeded")
                    if entry.is_symlink() or entry.is_junction() or re.fullmatch(r"[0-9a-f]{64}\.json", entry.name) is None:
                        raise ValueError("external: unsafe or unexpected resume object")
                    payload = _read_regular_bytes(entry)
                    digest = hashlib.sha256(payload).hexdigest()
                    if entry.name != digest + ".json":
                        raise ValueError("external: corrupt resume object digest")
                    record = json.loads(payload, object_pairs_hook=_unique_object)
                    validate_external_document(record, "record")
                    case_id = record["case_id"]
                    if any(record[key] != preview[key] for key in ("source_id", "adapter_id", "adapter_version")) or case_id in cached or case_id in rejected_ids:
                        raise ValueError("external: incompatible or duplicate resume object identity")
                    cached[case_id] = {"path": entry, "digest": digest, "members": record["provenance"]["selected_members"], "size": len(payload)}
                    if len(cached) % 100 == 0:
                        progress({"source": "adapter", "phase": "indexing", "worker_ids": [], "simulation_ids": [], "data": {"stage": "validating-resume-cache", "accepted": len(cached)}})
                if prior["accepted"] != len(cached) or prior["rejected"] != len(rejected_ids) or prior["discovered"] != len(cached) + len(rejected_ids) or sum(prior[key] for key in ("added", "changed", "unchanged")) != len(cached):
                    raise ValueError("external: incomplete resume normalization")
                preview["diagnostics"].append({"code": "resume_origin", "operation_id": resume_operation_id, "message": "Reused complete normalized records after fresh source and cache verification"})
            for number, candidate in enumerate(iter_snumat_cases(paths, source_id, fingerprints, cancel=cancel, inventory_only=resume_operation_id is not None), 1):
                if monotonic() >= operation_deadline:
                    raise ValueError("external: operation timeout exceeded")
                if cancel():
                    raise InterruptedError("external: cancelled during preview")
                case_id = candidate["case_id"]
                if case_id in discovered:
                    raise ValueError("external: duplicate case identity yielded")
                discovered.add(case_id)
                preview["discovered"] += 1
                if resume_operation_id is not None and case_id in rejected_ids:
                    preview["rejected"] += 1
                    preview["diagnostics"].extend(deepcopy(issue) for issue in rejected if issue["case_id"] == case_id)
                    continue
                if resume_operation_id is not None:
                    if case_id not in cached:
                        raise ValueError("external: source inventory exceeds resume cache")
                    item = cached[case_id]
                    physical = {key: value for key, value in candidate["locators"].items() if not key.startswith("__")}
                    if physical != item["members"]:
                        raise ValueError("external: resume member inventory mismatch")
                    payload = _read_regular_bytes(item["path"])
                    if hashlib.sha256(payload).hexdigest() != item["digest"]:
                        raise ValueError("external: resume object changed during inventory")
                    record = json.loads(payload, object_pairs_hook=_unique_object)
                    destination = stage / "records" / (item["digest"] + ".json")
                    if item["path"].parent.is_symlink() or item["path"].parent.is_junction() or destination.parent.is_symlink() or destination.parent.is_junction():
                        raise OSError("external: unsafe hardlink parent")
                    os.link(item["path"], destination, follow_symlinks=False)
                    if hashlib.sha256(_read_regular_bytes(destination)).hexdigest() != item["digest"]:
                        raise ValueError("external: hardlinked resume object changed")
                try:
                    if resume_operation_id is None:
                        record = normalize_snumat_case(source_id, case_id, candidate["members"], candidate["locators"])
                        record["issues"].extend(candidate["issues"])
                        validate_external_document(record, "record")
                        payload = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
                        digest = hashlib.sha256(payload).hexdigest()
                        _write_input_file(stage / "records" / (digest + ".json"), payload)
                    else:
                        digest = cached[case_id]["digest"]
                    action = "added" if case_id not in heads else "unchanged" if heads[case_id] == digest else "changed"
                    accepted.append({"case_id": case_id, "record_sha256": digest, "action": action})
                    preview["accepted"] += 1
                    preview[action] += 1
                    unavailable.update(value["name"] for value in record["outputs"]["unavailable_outputs"])
                    preview["diagnostics"].extend({"case_id": case_id, **deepcopy(issue)} for issue in record["issues"])
                except (ValueError, KeyError, TypeError, UnicodeError) as error:
                    preview["rejected"] += 1
                    preview["diagnostics"].append({"case_id": case_id, "code": "rejected", "message": str(error)})
                if number % 100 == 0:
                    progress({"source": "adapter", "phase": "indexing", "worker_ids": [], "simulation_ids": [], "data": {"stage": "reusing-records" if resume_operation_id is not None else "normalizing", "discovered": number}})
            if monotonic() >= operation_deadline:
                raise ValueError("external: operation timeout exceeded")
            if resume_operation_id is not None and discovered != set(cached) | rejected_ids:
                raise ValueError("external: source inventory does not cover resume cache")
            if fingerprints != fingerprint_external_source(paths, source_id, cancel=cancel):
                raise ValueError("external: source changed after preview")
            if read_external_status(paths, source_id)["generation"] != preview["base_generation"]:
                raise ValueError("external: catalog generation changed during preview")
            missing_ids = sorted(set(heads) - discovered)
            preview["missing"] = len(missing_ids)
            preview["unavailable_outputs"] = sorted(unavailable)
            object_bytes = sum((stage / "records" / (item["record_sha256"] + ".json")).stat().st_size for item in accepted)
            scratch = stage / "catalog-preview.sqlite"
            scratch_connection = sqlite3.connect(scratch)
            try:
                scratch_connection.enable_load_extension(False)
                scratch_connection.execute("PRAGMA journal_mode=DELETE")
                scratch_connection.execute("PRAGMA synchronous=FULL")
                if database.exists():
                    old = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=30)
                    try:
                        old.backup(scratch_connection)
                    finally:
                        old.close()
                else:
                    for statement in TABLES:
                        scratch_connection.execute(statement)
                    scratch_connection.execute("PRAGMA user_version=1")
                with scratch_connection:
                    if monotonic() >= operation_deadline:
                        raise ValueError("external: operation timeout exceeded")
                    for item in accepted:
                        if cancel():
                            raise InterruptedError("external: cancelled building preview catalog")
                        if monotonic() >= operation_deadline:
                            raise ValueError("external: operation timeout exceeded")
                        payload_text = _read_regular_bytes(stage / "records" / (item["record_sha256"] + ".json")).decode("utf-8")
                        scratch_connection.execute("INSERT OR IGNORE INTO records VALUES (?,?,?,?)", (source_id, item["case_id"], item["record_sha256"], payload_text))
                        scratch_connection.execute("INSERT OR REPLACE INTO heads VALUES (?,?,?,?)", (source_id, item["case_id"], item["record_sha256"], "present"))
                        scratch_connection.execute("INSERT OR IGNORE INTO bindings VALUES (?,?,?,?)", (source_id, item["case_id"], item["record_sha256"], "0" * 64))
                    scratch_connection.executemany("INSERT OR REPLACE INTO diagnostics VALUES (?,?,?)", [(operation_id, i, json.dumps(issue, sort_keys=True, separators=(",", ":"), ensure_ascii=False)) for i, issue in enumerate(preview["diagnostics"])])
            finally:
                scratch_connection.close()
            catalog_bytes = scratch.stat().st_size
            scratch.unlink()
            snapshot = {"schema_version": 1, "source_id": source_id, "source_fingerprints": fingerprints}
            snapshot_bytes = len(json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8"))
            preview["storage_bytes"] = {"staged_objects": object_bytes, "selected_evidence_spool_retained": 0, "disposable_catalog_build": catalog_bytes, "snapshot": snapshot_bytes, "estimated_commit_journal_and_temporary": catalog_bytes * 2, "estimated_additional_commit": object_bytes + snapshot_bytes + catalog_bytes * 3, "free_space_observed": shutil.disk_usage(stage).free}
            if cancel():
                raise InterruptedError("external: cancelled finalizing preview")
            if read_external_status(paths, source_id)["generation"] != preview["base_generation"]:
                raise ValueError("external: catalog generation changed during preview")
            preview["state"] = "ready"
            plan = {"schema_version": 1, "preview": deepcopy(preview), "records": accepted, "missing_ids": missing_ids, "discovered_ids": sorted(discovered)}
            if monotonic() >= operation_deadline:
                raise ValueError("external: operation timeout exceeded")
            plan_bytes = json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
            digest = hashlib.sha256(plan_bytes).hexdigest()
            _write_input_file(stage / "plan.json", plan_bytes)
            preview["plan_sha256"] = digest
            validate_external_document(preview, "preview")
            _write_json(stage / "preview.json", preview)
            progress({"source": "adapter", "phase": "finalizing", "worker_ids": [], "simulation_ids": [], "data": {"stage": "preview-ready", "operation_id": operation_id, "plan_sha256": digest}})
            return preview
        except (InterruptedError, ValueError, OSError, sqlite3.Error) as error:
            preview["state"] = "cancelled" if isinstance(error, InterruptedError) else "failed"
            preview["plan_sha256"] = None
            preview["diagnostics"].append({"code": preview["state"], "message": str(error)})
            _write_json(stage / "preview.json", preview)
            validate_external_document(preview, "preview")
            return preview
        finally:
            SOURCE_PROGRESS.reset(progress_token)
    finally:
        SOURCE_DEADLINE.reset(deadline_token)


def import_external_preview(paths: ProjectPaths, source_id: str, expected_plan_sha256: str, *, cancel: Callable[[], bool], progress: Callable[[JSONObject], None], timeout_seconds: int = 600) -> JSONObject:
    """Commit one exact staged plan after renewed source and generation checks.

    timeout_seconds is an integer in 1..3600 controlling the whole operation.
    paths/source_id select a registration; expected_plan_sha256 pins a ready
    preview. One explicit timeout_seconds deadline (600 seconds by default) spans verification and publication; timeout
    before commit raises ValueError and rolls back. cancel and progress are cooperative job callbacks. Return a closed
    committed/cancelled report. Cancel before commit rolls back; after commit
    remains committed. Source bytes never change. Invalid/stale plans raise
    ValueError, unsafe/storage failures OSError, and catalog errors sqlite3.Error.
    Immutable objects may survive interrupted transactions without publication.
    """
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 3600:
        raise ValueError('external: timeout_seconds must be an integer in 1..3600')
    operation_deadline = monotonic() + timeout_seconds
    deadline_token = SOURCE_DEADLINE.set(operation_deadline)
    try:
        if paths != get_project_paths(paths.root) or not paths.root.is_dir():
            raise ValueError("external: invalid project layout")
        if monotonic() >= operation_deadline:
            raise ValueError("external: operation timeout exceeded")
        resolve_external_source(paths, source_id)
        if not isinstance(expected_plan_sha256, str) or re.fullmatch("[0-9a-f]{64}", expected_plan_sha256) is None:
            raise ValueError("external: invalid expected plan hash")
        directory = paths.root / "memory/knowledge/external"
        for entry in (*reversed(directory.parents), directory, directory / "staging", directory / "index.sqlite"):
            if entry.is_symlink() or entry.is_junction():
                raise OSError("external: unsafe catalog path")
        selected = []
        if monotonic() >= operation_deadline:
            raise ValueError("external: operation timeout exceeded")
        for entry in sorted((directory / "staging").iterdir()):
            if entry.is_symlink() or entry.is_junction():
                raise OSError("external: unsafe staging entry")
            if not entry.is_dir() or not (entry / "plan.json").is_file():
                continue
            raw = _read_regular_bytes(entry / "plan.json")
            if hashlib.sha256(raw).hexdigest() == expected_plan_sha256:
                selected.append((entry, raw))
        if len(selected) != 1:
            raise ValueError("external: exact staged plan not found or ambiguous")
        stage, plan_bytes = selected[0]
        plan = json.loads(plan_bytes, object_pairs_hook=_unique_object)
        if set(plan) != {"schema_version", "preview", "records", "missing_ids", "discovered_ids"} or plan["schema_version"] != 1:
            raise ValueError("external: invalid internal plan")
        preview = plan["preview"]
        validate_external_document(preview, "preview")
        if preview["source_id"] != source_id or preview["state"] != "ready" or preview["plan_sha256"] is not None or preview["adapter_id"] != "snumat-trim-v1" or preview["adapter_version"] != 1 or preview["operation_id"] != stage.name:
            raise ValueError("external: plan identity/state mismatch")
        UUID(stage.name)
        operation_id = str(uuid4())
        report = {"schema_version": 1, "operation_id": operation_id, "state": "cancelled", "generation": preview["base_generation"], "source_id": source_id, "added": 0, "changed": 0, "skipped": 0, "rejected": preview["rejected"], "missing": preview["missing"], "issues": [], "plan_sha256": expected_plan_sha256}
        try:
            if cancel():
                raise InterruptedError("external: cancelled before import")
            if monotonic() >= operation_deadline:
                raise ValueError("external: operation timeout exceeded")
            fingerprints = fingerprint_external_source(paths, source_id, cancel=cancel)
            if fingerprints != preview["source_fingerprints"]:
                raise ValueError("external: source differs from preview")
            records, cases = [], set()
            if monotonic() >= operation_deadline:
                raise ValueError("external: operation timeout exceeded")
            for item in plan["records"]:
                if monotonic() >= operation_deadline:
                    raise ValueError("external: operation timeout exceeded")
                if not isinstance(item, dict) or set(item) != {"case_id", "record_sha256", "action"} or item["action"] not in ("added", "changed", "unchanged") or not isinstance(item["record_sha256"], str) or re.fullmatch("[0-9a-f]{64}", item["record_sha256"]) is None:
                    raise ValueError("external: invalid staged descriptor")
                if item["case_id"] in cases:
                    raise ValueError("external: duplicate staged case")
                cases.add(item["case_id"])
                payload = _read_regular_bytes(stage / "records" / (item["record_sha256"] + ".json"))
                if hashlib.sha256(payload).hexdigest() != item["record_sha256"]:
                    raise ValueError("external: staged object integrity mismatch")
                record = json.loads(payload, object_pairs_hook=_unique_object)
                validate_external_document(record, "record")
                if record["source_id"] != source_id or record["case_id"] != item["case_id"]:
                    raise ValueError("external: staged ownership mismatch")
                records.append((item, payload))
            if len(records) != preview["accepted"] or preview["discovered"] != len(plan["discovered_ids"]) or len(set(plan["discovered_ids"])) != len(plan["discovered_ids"]) or set(plan["missing_ids"]) & set(plan["discovered_ids"]) or len(plan["missing_ids"]) != preview["missing"] or preview["accepted"] + preview["rejected"] != preview["discovered"]:
                raise ValueError("external: inconsistent preview counts")
            if any(sum(item["action"] == key for item, _ in records) != preview[key] for key in ("added", "changed", "unchanged")):
                raise ValueError("external: inconsistent preview actions")
            snapshot = {"schema_version": 1, "source_id": source_id, "source_fingerprints": fingerprints}
            snapshot_bytes = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
            snapshot_digest = hashlib.sha256(snapshot_bytes).hexdigest()
            for name in ("objects", "snapshots", "reports"):
                target = directory / name
                if target.is_symlink() or target.is_junction():
                    raise OSError("external: unsafe publication directory")
                target.mkdir(exist_ok=True)
            database = directory / "index.sqlite"
            if monotonic() >= operation_deadline:
                raise ValueError("external: operation timeout exceeded")
            with _lock_parent(directory, timeout_seconds=30.0):
                if monotonic() >= operation_deadline:
                    raise ValueError("external: operation timeout exceeded")
                resolve_external_source(paths, source_id)
                for name in ("index.sqlite", "index.sqlite-journal", "index.sqlite-wal", "index.sqlite-shm"):
                    target = directory / name
                    if target.is_symlink() or target.is_junction():
                        raise OSError("external: unsafe SQLite path")
                current = read_external_status(paths, source_id)
                if current["generation"] != preview["base_generation"]:
                    raise ValueError("external: stale catalog generation")
                if monotonic() >= operation_deadline:
                    raise ValueError("external: operation timeout exceeded")
                if fingerprints != fingerprint_external_source(paths, source_id, cancel=cancel):
                    raise ValueError("external: source changed before commit")
                if monotonic() >= operation_deadline:
                    raise ValueError("external: operation timeout exceeded")
                for item, payload in records:
                    if monotonic() >= operation_deadline:
                        raise ValueError("external: operation timeout exceeded")
                    target = directory / "objects" / (item["record_sha256"] + ".json")
                    if target.exists():
                        if _read_regular_bytes(target) != payload:
                            raise ValueError("external: immutable object collision")
                    else:
                        _write_input_file(target, payload)
                snapshot_path = directory / "snapshots" / (snapshot_digest + ".json")
                if snapshot_path.exists():
                    if _read_regular_bytes(snapshot_path) != snapshot_bytes:
                        raise ValueError("external: immutable snapshot collision")
                else:
                    _write_input_file(snapshot_path, snapshot_bytes)
                created = not database.exists()
                connection = sqlite3.connect(database, timeout=30)
                committed = False
                try:
                    connection.enable_load_extension(False)
                    connection.execute("PRAGMA foreign_keys=ON")
                    connection.execute("PRAGMA synchronous=FULL")
                    connection.execute("PRAGMA journal_mode=DELETE")
                    connection.execute("BEGIN IMMEDIATE")
                    if created:
                        for statement in TABLES:
                            connection.execute(statement)
                        connection.execute("PRAGMA user_version=1")
                        connection.execute("INSERT INTO meta VALUES ('schema_version','1')")
                    if monotonic() >= operation_deadline:
                        raise ValueError("external: operation timeout exceeded")
                    for ordinal, (item, payload) in enumerate(records):
                        if monotonic() >= operation_deadline:
                            raise ValueError("external: operation timeout exceeded")
                        if cancel():
                            raise InterruptedError("external: cancelled before publication")
                        old = connection.execute("SELECT record_sha256 FROM heads WHERE source_id=? AND case_id=?", (source_id, item["case_id"])).fetchone()
                        actual = "added" if old is None else "unchanged" if old[0] == item["record_sha256"] else "changed"
                        if actual != item["action"]:
                            raise ValueError("external: preview action changed")
                        connection.execute("INSERT OR IGNORE INTO records VALUES (?,?,?,?)", (source_id, item["case_id"], item["record_sha256"], payload.decode("utf-8")))
                        connection.execute("INSERT OR REPLACE INTO heads VALUES (?,?,?,?)", (source_id, item["case_id"], item["record_sha256"], "present"))
                        connection.execute("DELETE FROM bindings WHERE source_id=? AND case_id=? AND record_sha256=? AND snapshot_sha256=?", (source_id, item["case_id"], item["record_sha256"], snapshot_digest))
                        connection.execute("INSERT INTO bindings VALUES (?,?,?,?)", (source_id, item["case_id"], item["record_sha256"], snapshot_digest))
                        report["skipped" if item["action"] == "unchanged" else item["action"]] += 1
                        if (ordinal + 1) % 100 == 0:
                            progress({"source": "adapter", "phase": "indexing", "worker_ids": [], "simulation_ids": [], "data": {"stage": "publishing", "records": ordinal + 1}})
                    for row in connection.execute("SELECT case_id FROM heads WHERE source_id=?", (source_id,)).fetchall():
                        if row[0] in plan["missing_ids"]:
                            connection.execute("UPDATE heads SET presence='missing' WHERE source_id=? AND case_id=?", (source_id, row[0]))
                    connection.executemany("INSERT INTO diagnostics VALUES (?,?,?)", [(operation_id, ordinal, json.dumps(issue, sort_keys=True, separators=(",", ":"), ensure_ascii=False)) for ordinal, issue in enumerate(preview["diagnostics"])])
                    if cancel():
                        raise InterruptedError("external: cancelled before commit")
                    if monotonic() >= operation_deadline:
                        raise ValueError("external: operation timeout exceeded")
                    resolve_external_source(paths, source_id)
                    generation = str(uuid4())
                    connection.execute("INSERT OR REPLACE INTO meta VALUES ('generation',?)", (generation,))
                    if monotonic() >= operation_deadline:
                        raise ValueError("external: operation timeout exceeded")
                    connection.commit()
                    committed = True
                    report["state"] = "committed"
                    report["generation"] = generation
                finally:
                    if not committed:
                        connection.rollback()
                    connection.close()
                    if created and not committed and database.exists():
                        database.unlink()
            validate_external_document(report, "report")
            try:
                _write_json(directory / "reports" / (operation_id + ".json"), report)
            except OSError as error:
                report["issues"].append({"code": "reporting_error", "message": str(error)})
            return report
        except InterruptedError as error:
            if report["state"] == "committed":
                report["issues"].append({"code": "reporting_error", "message": str(error)})
                return report
            report["state"] = "cancelled"
            report["added"] = report["changed"] = report["skipped"] = 0
            report["issues"].append({"code": "cancelled", "message": str(error)})
            validate_external_document(report, "report")
            return report
    finally:
        SOURCE_DEADLINE.reset(deadline_token)


def _compare_external_rationals(left: str, right: str) -> int:
    """Compare canonical left/right n/d strings exactly; return -1/0/1 or ValueError.

    No I/O occurs. Noncanonical or unbounded persisted representations fail.
    """
    parsed = []
    for value in (left, right):
        if not isinstance(value, str) or len(value) > 4600 or re.fullmatch(r"-?(?:0|[1-9][0-9]*)/[1-9][0-9]*", value) is None:
            raise ValueError("external: projection_integrity_error: invalid rational")
        number = Fraction(value)
        if value != str(number.numerator) + "/" + str(number.denominator):
            raise ValueError("external: projection_integrity_error: noncanonical rational")
        parsed.append(number)
    return (parsed[0] > parsed[1]) - (parsed[0] < parsed[1])


def _open_external_projection(path: Path, *, readonly: bool) -> sqlite3.Connection:
    """Open safe path with exact collation; caller closes returned connection.

    readonly selects read-only/query-only mode. Reject linked parents/sidecars
    with OSError; connection/schema failures propagate as SQLite errors.
    """
    for entry in (*reversed(path.parents), path, *(path.with_name(path.name + suffix) for suffix in ("-journal", "-wal", "-shm"))):
        if entry.is_symlink() or entry.is_junction():
            raise OSError("external: unsafe projection path")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=30) if readonly else sqlite3.connect(path, timeout=30)
    try:
        connection.enable_load_extension(False)
        connection.create_collation("EXTERNAL_RATIONAL_V1", _compare_external_rationals)
        if readonly:
            connection.execute("PRAGMA query_only=ON")
        else:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("PRAGMA page_size=4096")
            connection.execute("PRAGMA max_page_count=524288")
        return connection
    except BaseException:
        connection.close()
        raise


def _external_projection_predicate(compiled: JSONObject) -> tuple[str, list[str]]:
    """Compile trusted compiled query into parameterized SQL and string bindings.

    Return WHERE text for documents alias d without I/O or mutation. Unsupported
    internal operators raise ValueError; unchecked external JSON is unsupported.
    """
    parts = ["d.presence='present'"] if compiled["revision_scope"] == "current" else ["1=1"]
    bindings = []
    for term in compiled["text_terms"]:
        parts.append("instr(d.text_folded,?)>0")
        bindings.append(term)
    for clause in compiled["clauses"]:
        field, operator, value = clause["field"], clause["operator"], clause["value"]
        datatype = FIELDS[field][0]
        if operator in ("contains_all", "contains_any"):
            if operator == "contains_all":
                for symbol in value:
                    parts.append("EXISTS (SELECT 1 FROM elements e WHERE e.field=? AND e.symbol=? AND e.case_id=d.case_id AND e.record_sha256=d.record_sha256)")
                    bindings.extend((field, symbol))
            else:
                parts.append("EXISTS (SELECT 1 FROM elements e WHERE e.field=? AND e.symbol IN (" + ",".join("?" for _ in value) + ") AND e.case_id=d.case_id AND e.record_sha256=d.record_sha256)")
                bindings.extend((field, *value))
            continue
        terms = ["v.field=?", "v.case_id=d.case_id", "v.record_sha256=d.record_sha256"]
        bindings.append(field)
        if operator == "unknown":
            terms.append("v.value_kind='unknown'")
        elif datatype == "text":
            terms.extend(("v.value_kind='text'", "v.text_value=?"))
            bindings.append(value)
        elif operator == "eq":
            terms.extend(("v.value_kind='number'", "v.number COLLATE EXTERNAL_RATIONAL_V1=?"))
            bindings.append(value)
        elif operator == "between":
            terms.append("v.value_kind='number'")
            for bound, comparison in zip(value, (">=", "<=")):
                if bound is not None:
                    terms.append("v.number COLLATE EXTERNAL_RATIONAL_V1" + comparison + "?")
                    bindings.append(bound)
        else:
            raise ValueError("external: invalid compiled operator")
        parts.append("EXISTS (SELECT 1 FROM values_by_field v WHERE " + " AND ".join(terms) + ")")
    return " AND ".join(parts), bindings


def _compact_external_hit(reference: JSONObject, record: JSONObject, snapshot_sha256: str) -> JSONObject:
    """Return bounded identity/summary hit for validated record and snapshot digest.

    reference is copied; no I/O or mutation occurs. Full raw/locator data remains
    accessible through get_external_record; invalid internal fields raise KeyError.
    """
    summary = {"target_id": record["target_id"], "ion_symbol": record["inputs"]["ion_symbol"], "target_label": record["inputs"]["target_label"][:256], "label_truncated": len(record["inputs"]["target_label"]) > 256, "number_of_ions": record["inputs"]["number_of_ions"], "issue_counts": {}}
    for key, quantity in (("energy_keV", record["inputs"]["energy_keV"]), ("calculated_ions", record["outputs"]["summary"]["calculated_ions"]), ("mean_range", record["outputs"]["summary"]["mean_range"])):
        summary[key] = {"value": quantity["value"], "unit": quantity["unit"]}
    for issue in record["issues"]:
        summary["issue_counts"][issue["code"]] = summary["issue_counts"].get(issue["code"], 0) + 1
    return {"reference": deepcopy(reference), "snapshot_sha256": snapshot_sha256, "summary": summary, "verification": {"record": "verified", "original_evidence": "not_checked"}}


def reindex_external_database(paths: ProjectPaths, source_id: str, *, expected_generation: str, cancel: Callable[[], bool], progress: Callable[[JSONObject], None], timeout_seconds: int = 600) -> JSONObject:
    """Build/publish a generation-pinned derived predicate index without archive reads.

    paths/source_id identify imported records; expected_generation pins them.
    cancel/progress implement managed lifecycle; timeout_seconds is 1..3600.
    Return committed/failed/cancelled report, preserving failed build files.
    Invalid arguments raise ValueError before building; operational errors are
    reported. Only projection and report files are written, never source/catalog.
    """
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 3600:
        raise ValueError("external: invalid reindex timeout")
    if not isinstance(expected_generation, str) or str(UUID(expected_generation)) != expected_generation:
        raise ValueError("external: invalid expected generation")
    resolve_external_source(paths, source_id)
    status = read_external_status(paths, source_id)
    if status["state"] != "loaded" or status["generation"] != expected_generation:
        raise ValueError("external: stale catalog generation")
    created_at = _record_time(load_config(paths.root / "settings.toml"), None).isoformat()
    directory = paths.root / "memory/knowledge/external"
    search = directory / "search"
    if search.is_symlink() or search.is_junction():
        raise OSError("external: unsafe projection directory")
    search.mkdir(exist_ok=True)
    operation_id, deadline = str(uuid4()), monotonic() + timeout_seconds
    build = search / (operation_id + ".building.sqlite")
    target = search / (source_id + ".sqlite")
    report = {"schema_version": 1, "operation_id": operation_id, "source_id": source_id, "state": "failed", "generation": expected_generation, "records": 0, "current": 0, "size_bytes": 0, "issues": []}
    original = connection = None
    try:
        if shutil.disk_usage(search).free < 2 * 1024 ** 3:
            raise OSError("external: insufficient projection free space")
        original = sqlite3.connect((directory / "index.sqlite").as_uri() + "?mode=ro", uri=True, timeout=30)
        original.enable_load_extension(False)
        original.execute("PRAGMA query_only=ON")
        original.set_progress_handler(lambda: int(cancel() or monotonic() >= deadline), 1000)
        original.execute("BEGIN")
        if dict(original.execute("SELECT key,value FROM meta"))["generation"] != expected_generation:
            raise ValueError("external: stale catalog generation")
        connection = _open_external_projection(build, readonly=False)
        connection.set_progress_handler(lambda: int(cancel() or monotonic() >= deadline), 1000)
        connection.executescript("""
            CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE documents(case_id TEXT NOT NULL,record_sha256 TEXT NOT NULL,presence TEXT NOT NULL,text_folded TEXT NOT NULL,projection_sha256 TEXT NOT NULL,PRIMARY KEY(case_id,record_sha256));
            CREATE TABLE values_by_field(case_id TEXT NOT NULL,record_sha256 TEXT NOT NULL,field TEXT NOT NULL,value_kind TEXT NOT NULL,number TEXT COLLATE EXTERNAL_RATIONAL_V1,text_value TEXT,PRIMARY KEY(case_id,record_sha256,field));
            CREATE TABLE elements(case_id TEXT NOT NULL,record_sha256 TEXT NOT NULL,field TEXT NOT NULL,symbol TEXT NOT NULL,PRIMARY KEY(case_id,record_sha256,field,symbol));
            CREATE INDEX document_presence ON documents(presence,case_id,record_sha256);
            CREATE INDEX field_number ON values_by_field(field,value_kind,number COLLATE EXTERNAL_RATIONAL_V1,case_id,record_sha256);
            CREATE INDEX field_text ON values_by_field(field,value_kind,text_value,case_id,record_sha256);
            CREATE INDEX field_elements ON elements(field,symbol,case_id,record_sha256);
            PRAGMA user_version=1;
        """)
        connection.execute("BEGIN")
        statement = "SELECT r.case_id,r.record_sha256,r.payload_json,CASE WHEN h.record_sha256=r.record_sha256 THEN h.presence ELSE 'historical' END FROM records r LEFT JOIN heads h ON h.source_id=r.source_id AND h.case_id=r.case_id WHERE r.source_id=? ORDER BY r.case_id,r.record_sha256"
        for case_id, digest, payload_text, presence in original.execute(statement, (source_id,)):
            if cancel():
                raise InterruptedError("external: reindex cancelled")
            if monotonic() >= deadline:
                raise ValueError("external: reindex deadline exceeded")
            payload = payload_text.encode("utf-8")
            if hashlib.sha256(payload).hexdigest() != digest or _read_regular_bytes(directory / "objects" / (digest + ".json")) != payload:
                raise ValueError("external: normalized record integrity mismatch")
            record = json.loads(payload, object_pairs_hook=_unique_object)
            validate_external_document(record, "record")
            if record["source_id"] != source_id or record["case_id"] != case_id or presence not in ("present", "missing", "historical"):
                raise ValueError("external: record/head ownership mismatch")
            projection = project_external_record(record)
            projection_hash = hashlib.sha256(json.dumps(projection, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
            connection.execute("INSERT INTO documents VALUES (?,?,?,?,?)", (case_id, digest, presence, projection["text_folded"], projection_hash))
            for field, value in projection["fields"].items():
                kind, actual = value["kind"], value["value"]
                connection.execute("INSERT INTO values_by_field VALUES (?,?,?,?,?,?)", (case_id, digest, field, kind, actual if kind == "number" else None, actual if kind == "text" else None))
                if kind == "elements":
                    connection.executemany("INSERT INTO elements VALUES (?,?,?,?)", ((case_id, digest, field, symbol) for symbol in set(actual)))
            report["records"] += 1
            report["current"] += int(presence == "present")
            if report["records"] % 100 == 0:
                progress({"source": "adapter", "phase": "indexing", "worker_ids": [], "simulation_ids": [], "data": {"stage": "search-projection", "records": report["records"]}})
        counts = original.execute("SELECT COUNT(*) FROM records WHERE source_id=?", (source_id,)).fetchone()[0]
        current = original.execute("SELECT COUNT(*) FROM heads WHERE source_id=? AND presence='present'", (source_id,)).fetchone()[0]
        if counts != report["records"] or current != report["current"]:
            raise ValueError("external: projection coverage mismatch")
        metadata = {"schema_version": "1", "source_id": source_id, "catalog_generation": expected_generation, "operation_id": operation_id, "record_count": str(counts), "current_count": str(current), "created_at": created_at}
        connection.executemany("INSERT INTO meta VALUES (?,?)", metadata.items())
        if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("external: projection_integrity_error")
        if cancel():
            raise InterruptedError("external: reindex cancelled")
        if monotonic() >= deadline:
            raise ValueError("external: reindex deadline exceeded")
        connection.commit()
        connection.close()
        connection = None
        original.close()
        original = None
        report["size_bytes"] = build.stat().st_size
        if report["size_bytes"] > 2 * 1024 ** 3:
            raise ValueError("external: projection exceeds size cap")
        with build.open("r+b") as stream:
            os.fsync(stream.fileno())
        with _lock_parent(directory, timeout_seconds=30.0):
            if cancel():
                raise InterruptedError("external: reindex cancelled")
            if monotonic() >= deadline:
                raise ValueError("external: reindex deadline exceeded")
            resolve_external_source(paths, source_id)
            if read_external_status(paths, source_id)["generation"] != expected_generation:
                raise ValueError("external: stale catalog generation")
            for entry in (target, *(target.with_name(target.name + suffix) for suffix in ("-journal", "-wal", "-shm"))):
                if entry.is_symlink() or entry.is_junction():
                    raise OSError("external: unsafe projection publication")
            os.replace(build, target)
            report["state"] = "committed"
    except (ValueError, OSError, sqlite3.Error, InterruptedError) as error:
        report["state"] = "cancelled" if isinstance(error, InterruptedError) or cancel() else "failed"
        report["issues"].append({"code": "reindex_failed", "message": "external: reindex deadline exceeded" if monotonic() >= deadline else str(error)})
    finally:
        if connection is not None:
            connection.close()
        if original is not None:
            original.close()
    try:
        reports = directory / "reports"
        if reports.is_symlink() or reports.is_junction():
            raise OSError("external: unsafe reports directory")
        reports.mkdir(exist_ok=True)
        _write_json(reports / (operation_id + ".json"), report)
    except OSError as error:
        report["issues"].append({"code": "reporting_error", "message": str(error)})
    return report


def search_external_database(paths: ProjectPaths, source_id: str, query: JSONObject, *, offset: int = 0, limit: int = 20, expected_generation: str | None = None, timeout_seconds: int = 60) -> JSONObject:
    """Search a ready exact projection and return schema-v2 compact verified hits.

    paths/source_id identify imported records; query is validated once. offset,
    limit, expected_generation pin pagination; timeout_seconds bounds CPU/I/O.
    Return bounded results with originals explicitly not_checked. No writes or
    archive reads occur. Invalid, stale, corrupt or expired reads raise ValueError;
    I/O/SQLite failures propagate. Totals describe derived predicate matches.
    """
    compiled = compile_external_query(query)
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("external: invalid pagination")
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 240:
        raise ValueError("external: invalid search timeout")
    if expected_generation is not None and (not isinstance(expected_generation, str) or str(UUID(expected_generation)) != expected_generation):
        raise ValueError("external: invalid expected generation")
    deadline = monotonic() + timeout_seconds
    resolve_external_source(paths, source_id)
    status = read_external_status(paths, source_id)
    if expected_generation is not None and status["generation"] != expected_generation:
        raise ValueError("external: stale catalog generation")
    result = {"schema_version": 2, "state": status["state"], "generation": status["generation"], "total": 0, "offset": offset, "limit": limit, "returned": 0, "next_offset": None, "has_more": False, "matches": [], "excluded_on_page": 0, "issues": [], "verification": {"record": "verified_page", "original_evidence": "not_checked", "total": "derived_index_predicate_count"}}
    if status["state"] == "not_loaded":
        return result
    if status["search_index"]["state"] != "ready":
        raise ValueError("external: reindex_required: " + status["search_index"]["state"])
    directory = paths.root / "memory/knowledge/external"
    projection = _open_external_projection(directory / "search" / (source_id + ".sqlite"), readonly=True)
    catalog = None
    try:
        projection.set_progress_handler(lambda: int(monotonic() >= deadline), 1000)
        projection.execute("BEGIN")
        meta = dict(projection.execute("SELECT key,value FROM meta"))
        if meta["catalog_generation"] != status["generation"] or meta["source_id"] != source_id:
            raise ValueError("external: reindex_required: stale")
        catalog = sqlite3.connect((directory / "index.sqlite").as_uri() + "?mode=ro", uri=True, timeout=30)
        catalog.enable_load_extension(False)
        catalog.execute("PRAGMA query_only=ON")
        catalog.set_progress_handler(lambda: int(monotonic() >= deadline), 1000)
        catalog.execute("BEGIN")
        if dict(catalog.execute("SELECT key,value FROM meta"))["generation"] != status["generation"]:
            raise ValueError("external: catalog changed during search")
        predicate, parameters = _external_projection_predicate(compiled)
        result["total"] = projection.execute("SELECT COUNT(*) FROM documents d WHERE " + predicate, parameters).fetchone()[0]
        rows = projection.execute("SELECT d.case_id,d.record_sha256,d.projection_sha256 FROM documents d WHERE " + predicate + " ORDER BY d.case_id,d.record_sha256 LIMIT ? OFFSET ?", (*parameters, limit, offset))
        consumed = offset
        for case_id, digest, projection_hash in rows:
            if monotonic() >= deadline:
                raise ValueError("external: search deadline exceeded")
            if re.fullmatch("[0-9a-f]{64}", digest) is None:
                raise ValueError("external: projection_integrity_error: record digest")
            hit, issue = None, None
            row = catalog.execute("SELECT payload_json FROM records WHERE source_id=? AND case_id=? AND record_sha256=?", (source_id, case_id, digest)).fetchone()
            if row is None:
                raise ValueError("external: projection_integrity_error: record absent")
            payload = row[0].encode("utf-8")
            if hashlib.sha256(payload).hexdigest() != digest:
                raise ValueError("external: catalog payload hash mismatch")
            record = json.loads(payload, object_pairs_hook=_unique_object)
            validate_external_document(record, "record")
            if record["source_id"] != source_id or record["case_id"] != case_id:
                raise ValueError("external: catalog record ownership mismatch")
            expected_projection = project_external_record(record)
            actual_hash = hashlib.sha256(json.dumps(expected_projection, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
            if actual_hash != projection_hash or not match_compiled_external_record(record, compiled)["matched"]:
                raise ValueError("external: projection_integrity_error: predicate disagreement")
            try:
                if _read_regular_bytes(directory / "objects" / (digest + ".json")) != payload:
                    raise ValueError("external: normalized object integrity mismatch")
                binding = catalog.execute("SELECT snapshot_sha256 FROM bindings WHERE source_id=? AND case_id=? AND record_sha256=? ORDER BY rowid DESC LIMIT 1", (source_id, case_id, digest)).fetchone()
                if binding is None or re.fullmatch("[0-9a-f]{64}", binding[0]) is None:
                    raise ValueError("external: missing snapshot binding")
                snapshot_bytes = _read_regular_bytes(directory / "snapshots" / (binding[0] + ".json"))
                if hashlib.sha256(snapshot_bytes).hexdigest() != binding[0]:
                    raise ValueError("external: snapshot integrity mismatch")
                snapshot = json.loads(snapshot_bytes, object_pairs_hook=_unique_object)
                if snapshot["source_id"] != source_id or snapshot["source_fingerprints"]["source_id"] != source_id:
                    raise ValueError("external: snapshot source mismatch")
                reference = {key: record[key] for key in ("schema_version", "kind", "source_id", "adapter_id", "adapter_version", "case_id")}
                reference["record_sha256"] = digest
                hit = _compact_external_hit(reference, record, binding[0])
            except (ValueError, OSError, KeyError, TypeError) as error:
                message = str(error)
                issue = {"case_id": case_id, "code": "stale_or_unavailable", "message": message[:512], "message_truncated": len(message) > 512}
            trial = deepcopy(result)
            if hit is not None:
                trial["matches"].append(hit)
            else:
                trial["issues"].append(issue)
                trial["excluded_on_page"] += 1
            trial["returned"] = len(trial["matches"])
            trial["has_more"] = consumed + 1 < trial["total"]
            trial["next_offset"] = consumed + 1 if trial["has_more"] else None
            if len((json.dumps(trial, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")) > 131072:
                if consumed == offset:
                    raise ValueError("too-large: single external hit exceeds page budget")
                break
            result = trial
            consumed += 1
        result["has_more"] = consumed < result["total"]
        result["next_offset"] = consumed if result["has_more"] else None
    except sqlite3.OperationalError as error:
        if monotonic() >= deadline:
            raise ValueError("external: search deadline exceeded") from error
        raise
    finally:
        projection.close()
        if catalog is not None:
            catalog.close()
    resolve_external_source(paths, source_id)
    if monotonic() >= deadline:
        raise ValueError("external: search deadline exceeded")
    if read_external_status(paths, source_id)["generation"] != status["generation"]:
        raise ValueError("external: catalog changed during search")
    return result

def get_external_record(paths: ProjectPaths, reference: JSONObject, *, verify_evidence: bool = True, timeout_seconds: int = 240) -> JSONObject:
    """Read an exact immutable record and optionally verify all original members.

    paths/reference identify a committed version; timeout_seconds is an actual
    integer in 1..240 bounding cooperative read work. verify_evidence=False checks
    normalized bytes only; True verifies latest committed snapshot members with
    full archive reads and never silently falls back. Return record/binding and
    verification/issues. Revoked sources return no record. No writes/refresh.
    Invalid references or corrupt objects raise ValueError; catalogs may raise
    sqlite3.Error. Missing originals are reported explicitly as unavailable.
    """
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 240:
        raise ValueError("external: invalid get timeout")
    deadline = monotonic() + timeout_seconds
    if paths != get_project_paths(paths.root) or not paths.root.is_dir():
        raise ValueError("external: invalid project layout")
    validate_external_document(reference, "reference")
    if type(verify_evidence) is not bool:
        raise ValueError("external: verify_evidence must be boolean")
    result = {"schema_version": 1, "reference": deepcopy(reference), "record": None, "snapshot_sha256": None, "verification": {"record": "unavailable", "original_evidence": "not_checked"}, "issues": []}
    try:
        resolve_external_source(paths, reference["source_id"])
    except (ValueError, OSError) as error:
        result["verification"]["original_evidence"] = "unavailable"
        result["issues"].append({"code": "source_unavailable", "message": str(error)})
        return result
    status = read_external_status(paths, reference["source_id"])
    if status["state"] == "not_loaded":
        result["issues"].append({"code": "not_loaded", "message": "External catalog not loaded"})
        return result
    directory = paths.root / "memory/knowledge/external"
    connection = sqlite3.connect((directory / "index.sqlite").as_uri() + "?mode=ro", uri=True, timeout=30)
    try:
        connection.enable_load_extension(False)
        connection.execute("PRAGMA query_only=ON")
        row = connection.execute("SELECT payload_json FROM records WHERE source_id=? AND case_id=? AND record_sha256=?", (reference["source_id"], reference["case_id"], reference["record_sha256"])).fetchone()
        binding = connection.execute("SELECT snapshot_sha256 FROM bindings WHERE source_id=? AND case_id=? AND record_sha256=? ORDER BY rowid DESC LIMIT 1", (reference["source_id"], reference["case_id"], reference["record_sha256"])).fetchone()
    finally:
        connection.close()
    if row is None or binding is None:
        raise ValueError("external: exact record or binding absent")
    payload = _read_regular_bytes(directory / "objects" / (reference["record_sha256"] + ".json"))
    if hashlib.sha256(payload).hexdigest() != reference["record_sha256"] or payload.decode("utf-8") != row[0]:
        raise ValueError("external: normalized record integrity mismatch")
    record = json.loads(payload, object_pairs_hook=_unique_object)
    validate_external_document(record, "record")
    if any(record[key] != reference[key] for key in ("source_id", "case_id", "adapter_id", "adapter_version", "kind", "schema_version")):
        raise ValueError("external: record ownership mismatch")
    snapshot_digest = binding[0]
    if not isinstance(snapshot_digest, str) or re.fullmatch("[0-9a-f]{64}", snapshot_digest) is None:
        raise ValueError("external: invalid committed snapshot")
    snapshot_bytes = _read_regular_bytes(directory / "snapshots" / (snapshot_digest + ".json"))
    if hashlib.sha256(snapshot_bytes).hexdigest() != snapshot_digest:
        raise ValueError("external: snapshot integrity mismatch")
    snapshot = json.loads(snapshot_bytes, object_pairs_hook=_unique_object)
    if set(snapshot) != {"schema_version", "source_id", "source_fingerprints"} or type(snapshot["schema_version"]) is not int or snapshot["schema_version"] != 1 or snapshot["source_id"] != reference["source_id"] or snapshot["source_fingerprints"]["source_id"] != reference["source_id"]:
        raise ValueError("external: snapshot ownership mismatch")
    result["record"] = record
    result["snapshot_sha256"] = snapshot_digest
    result["verification"]["record"] = "verified"
    if verify_evidence:
        try:
            verify_external_evidence(paths, reference, record, snapshot, deadline=deadline)
            resolve_external_source(paths, reference["source_id"])
            latest_connection = sqlite3.connect((directory / "index.sqlite").as_uri() + "?mode=ro", uri=True, timeout=30)
            try:
                latest_connection.enable_load_extension(False)
                latest_connection.execute("PRAGMA query_only=ON")
                latest = latest_connection.execute("SELECT snapshot_sha256 FROM bindings WHERE source_id=? AND case_id=? AND record_sha256=? ORDER BY rowid DESC LIMIT 1", (reference["source_id"], reference["case_id"], reference["record_sha256"])).fetchone()
            finally:
                latest_connection.close()
            if latest is None or latest[0] != snapshot_digest:
                raise ValueError("external: snapshot binding changed during verification")
            result["verification"]["original_evidence"] = "verified"
        except (ValueError, OSError) as error:
            if monotonic() >= deadline:
                raise ValueError("external: get deadline exceeded") from error
            result["verification"]["original_evidence"] = "unavailable"
            result["issues"].append({"code": "stale_or_unavailable", "message": str(error)})
    resolve_external_source(paths, reference["source_id"])
    if monotonic() >= deadline:
        raise ValueError("external: get deadline exceeded")
    return result


def read_external_status(paths: ProjectPaths, source_id: str) -> JSONObject:
    """Read catalog counts, generation, valid plans and registration availability.

    paths/source_id select retained derived state. Return not_loaded if absent,
    or loaded counts with source registration availability even after revocation.
    Reload settings without source-content scanning. search_index separately
    reports missing/ready/stale/corrupt derived projection metadata and counts.
    No files/imports are created.
    Invalid IDs/schema raise ValueError, unsafe paths OSError, and corrupt SQLite
    sqlite3.Error. Only staging entries containing valid plan.json are listed.
    """
    if paths != get_project_paths(paths.root) or not paths.root.is_dir():
        raise ValueError("external: invalid project layout")
    if not isinstance(source_id, str) or re.fullmatch("db_[0-9a-f]{64}", source_id) is None:
        raise ValueError("external: invalid source ID")
    registration, issues = "configured", []
    try:
        resolve_external_source(paths, source_id)
    except ValueError as error:
        registration = "revoked"
        issues.append({"code": "source_revoked", "message": str(error)})
    except OSError as error:
        registration = "unavailable"
        issues.append({"code": "source_unavailable", "message": str(error)})
    directory = paths.root / "memory/knowledge/external"
    database = directory / "index.sqlite"
    for entry in (*reversed(directory.parents), directory, database, directory / "staging", directory / "objects", directory / "snapshots"):
        if entry.is_symlink() or entry.is_junction():
            raise OSError("external: unsafe catalog path")
    result = {"schema_version": 1, "source_id": source_id, "state": "not_loaded", "generation": None, "registration": registration, "records": 0, "current": 0, "missing": 0, "plans": [], "issues": issues, "search_index": {"state": "missing", "schema_version": None, "generation": None, "records": None, "current": None, "issues": []}}
    if (directory / "staging").exists():
        for entry in sorted((directory / "staging").iterdir()):
            if entry.is_symlink() or entry.is_junction():
                raise OSError("external: unsafe staging entry")
            if not entry.is_dir() or not (entry / "plan.json").is_file():
                continue
            raw = _read_regular_bytes(entry / "plan.json")
            plan = json.loads(raw, object_pairs_hook=_unique_object)
            if not isinstance(plan, dict) or set(plan) != {"schema_version", "preview", "records", "missing_ids", "discovered_ids"}:
                raise ValueError("external: malformed staged plan")
            validate_external_document(plan["preview"], "preview")
            if plan["preview"]["source_id"] == source_id:
                result["plans"].append({"operation_id": entry.name, "plan_sha256": hashlib.sha256(raw).hexdigest(), "state": plan["preview"]["state"]})
    if not database.exists():
        return result
    for name in ("index.sqlite-journal", "index.sqlite-wal", "index.sqlite-shm"):
        if (directory / name).is_symlink() or (directory / name).is_junction():
            raise OSError("external: unsafe SQLite sidecar")
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=30)
    try:
        connection.enable_load_extension(False)
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise ValueError("external: incompatible catalog version")
        for name, expected in TABLE_COLUMNS.items():
            if [row[1] for row in connection.execute("PRAGMA table_info(" + name + ")")] != expected:
                raise ValueError("external: incompatible catalog table " + name)
        meta = dict(connection.execute("SELECT key,value FROM meta"))
        if set(meta) != {"schema_version", "generation"} or meta["schema_version"] != "1" or str(UUID(meta["generation"])) != meta["generation"]:
            raise ValueError("external: incompatible catalog metadata")
        result["generation"] = meta["generation"]
        result["state"] = "loaded"
        result["records"] = connection.execute("SELECT COUNT(*) FROM records WHERE source_id=?", (source_id,)).fetchone()[0]
        result["current"] = connection.execute("SELECT COUNT(*) FROM heads WHERE source_id=? AND presence='present'", (source_id,)).fetchone()[0]
        result["missing"] = connection.execute("SELECT COUNT(*) FROM heads WHERE source_id=? AND presence='missing'", (source_id,)).fetchone()[0]
        if connection.execute("SELECT COUNT(*) FROM heads WHERE presence NOT IN ('present','missing')").fetchone()[0]:
            raise ValueError("external: malformed head presence")
    finally:
        connection.close()
    projection_path = directory / "search" / (source_id + ".sqlite")
    if projection_path.is_symlink() or projection_path.is_junction() or projection_path.parent.is_symlink() or projection_path.parent.is_junction():
        raise OSError("external: unsafe projection path")
    if projection_path.exists():
        connection = None
        try:
            connection = _open_external_projection(projection_path, readonly=True)
            connection.execute("BEGIN")
            metadata = dict(connection.execute("SELECT key,value FROM meta"))
            expected_keys = {"schema_version", "source_id", "catalog_generation", "operation_id", "record_count", "current_count", "created_at"}
            if set(metadata) != expected_keys or metadata["schema_version"] != "1" or metadata["source_id"] != source_id or connection.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise ValueError("external: incompatible projection metadata")
            if str(UUID(metadata["catalog_generation"])) != metadata["catalog_generation"] or str(UUID(metadata["operation_id"])) != metadata["operation_id"]:
                raise ValueError("external: invalid projection generation")
            count = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            current = connection.execute("SELECT COUNT(*) FROM documents WHERE presence='present'").fetchone()[0]
            if count != int(metadata["record_count"]) or current != int(metadata["current_count"]):
                raise ValueError("external: projection count mismatch")
            result["search_index"] = {"state": "ready" if metadata["catalog_generation"] == result["generation"] else "stale", "schema_version": 1, "generation": metadata["catalog_generation"], "records": count, "current": current, "issues": []}
        except (ValueError, OSError, sqlite3.Error) as error:
            result["search_index"]["state"] = "corrupt"
            result["search_index"]["issues"].append({"code": "projection_integrity_error", "message": str(error)})
        finally:
            if connection is not None:
                connection.close()
    return result