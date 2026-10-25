"""Publish and reconcile the derived unified catalog independently of source lifecycles."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import json
import logging
import sqlite3
import uuid

from src.backend.paths import ProjectPaths
from src.backend.records import _lock_parent, _validate_paths
from src.backend.project_records import read_record
from src.knowledge.retrieval_schema import KINDS, PROJECTOR_VERSION, SECTIONS, canonical_document_key, validate_retrieval_document
from src.knowledge.retrieval_sources import project_retrieval_document
from src.knowledge.source import project_simulation_inputs
from src.srim.common.files import _read_regular_bytes, _write_json
from src.srim.common.types import JSONObject

CANCELLATION: ContextVar[Callable[[], bool] | None] = ContextVar("retrieval_cancellation", default=None)
TABLE_COLUMNS = {
    "meta": ["key", "value"],
    "documents": ["document_key", "kind", "id", "revision", "reference_json", "status", "projection_json"],
    "heads": ["kind", "id", "reference_json", "state"],
    "links": ["document_key", "ordinal", "relation", "target_json", "link_json"],
    "issues": ["issue_key", "kind", "id", "reference_json", "issue_json"],
    "document_text": ["document_key", *SECTIONS],
}

TABLES = (
    "CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)",
    "CREATE TABLE documents(document_key TEXT PRIMARY KEY,kind TEXT NOT NULL,id TEXT NOT NULL,revision INTEGER,reference_json TEXT NOT NULL,status TEXT NOT NULL,projection_json TEXT NOT NULL)",
    "CREATE TABLE heads(kind TEXT,id TEXT,reference_json TEXT NOT NULL,state TEXT NOT NULL,PRIMARY KEY(kind,id))",
    "CREATE TABLE links(document_key TEXT,ordinal INTEGER,relation TEXT NOT NULL,target_json TEXT NOT NULL,link_json TEXT NOT NULL,PRIMARY KEY(document_key,ordinal),FOREIGN KEY(document_key) REFERENCES documents ON DELETE CASCADE)",
    "CREATE TABLE issues(issue_key TEXT PRIMARY KEY,kind TEXT NOT NULL,id TEXT NOT NULL,reference_json TEXT,issue_json TEXT NOT NULL)",
    "CREATE VIRTUAL TABLE document_text USING fts5(document_key UNINDEXED,title,question,tags,notes,method,findings,limitations,member_notes,output_notes,tokenize='unicode61 remove_diacritics 0')",
    "CREATE INDEX documents_selection ON documents(kind,status,id,revision)",
    "CREATE INDEX links_selection ON links(relation,target_json)",
    "CREATE INDEX heads_selection ON heads(kind,state)",
)


def initialize_retrieval_index(paths: ProjectPaths) -> JSONObject:
    """Create or validate an empty-capable catalog without discovering source records.

    Args:
        paths: Validated absolute project layout.
    Returns:
        schema_version, generation and created indicator.
    Raises:
        ValueError: Incompatible schema/projector version.
        OSError: Unsafe path or storage/lock failure.
        sqlite3.Error: Unsupported FTS5 or invalid database.
    """
    _validate_paths(paths)
    directory = paths.root / "memory/knowledge/retrieval"
    for entry in (*reversed(directory.parents), directory):
        if entry.is_symlink() or entry.is_junction():
            raise OSError("Unsafe retrieval directory")
    directory.mkdir(parents=True, exist_ok=True)
    database = directory / "index.sqlite"
    with _lock_parent(directory, timeout_seconds=30.0):
        for name in ("index.sqlite", "index.sqlite-journal", "index.sqlite-wal", "index.sqlite-shm"):
            if (directory / name).is_symlink() or (directory / name).is_junction():
                raise OSError("Unsafe retrieval SQLite path")
        created = not database.exists()
        connection = sqlite3.connect(database)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA synchronous=FULL")
            if created:
                connection.execute("PRAGMA journal_mode=DELETE")
                with connection:
                    for statement in TABLES:
                        connection.execute(statement)
                    connection.execute("PRAGMA user_version=1")
                    connection.executemany("INSERT INTO meta VALUES (?,?)", [("schema_version", "1"), ("projector_version", PROJECTOR_VERSION), ("generation", str(uuid.uuid4()))])
            meta = dict(connection.execute("SELECT key,value FROM meta"))
            if connection.execute("PRAGMA user_version").fetchone()[0] != 1 or set(meta) != {"schema_version", "projector_version", "generation"} or meta["schema_version"] != "1" or meta["projector_version"] != PROJECTOR_VERSION:
                raise ValueError("Incompatible unified retrieval catalog")
            uuid.UUID(meta["generation"])
            for name, expected in TABLE_COLUMNS.items():
                if [row[1] for row in connection.execute("PRAGMA table_info(" + name + ")")] != expected:
                    raise ValueError("Incompatible retrieval table: " + name)
            return {"schema_version": 1, "generation": meta["generation"], "created": created}
        finally:
            connection.close()


def update_retrieval_index(paths: ProjectPaths, references: Sequence[JSONObject]) -> JSONObject:
    """Project exact references and atomically publish documents, text, links and heads.

    Args:
        paths: Absolute agreed project paths.
        references: Explicit sequence of {kind,reference} identities.
    Returns:
        Update report; operational failures are separate from source outcomes.
    Raises:
        ValueError: Malformed arguments before work.
        BaseException: Interruption after connection/transaction cleanup.
    """
    _validate_paths(paths)
    if isinstance(references, (str, bytes)) or not isinstance(references, Sequence):
        raise ValueError("Expected explicit reference sequence")
    selected = {}
    for item in references:
        if not isinstance(item, dict) or set(item) != {"kind", "reference"}:
            raise ValueError("Invalid retrieval update identity")
        selected[canonical_document_key(item["kind"], item["reference"])] = item
    report = {"schema_version": 1, "operation_id": "update_" + str(uuid.uuid4()), "state": "succeeded", "generation": None, "indexed": [], "skipped": [], "rejected": [], "issues": [], "completed_at": datetime.now(timezone.utc).isoformat()}
    if not selected:
        return report
    directory = paths.root / "memory/knowledge/retrieval"
    try:
        report["generation"] = initialize_retrieval_index(paths)["generation"]
        projections = {}
        observed = {}
        for key, item in sorted(selected.items()):
            try:
                projection = project_retrieval_document(paths, item["kind"], item["reference"])
                if item["kind"] != "run":
                    observed[(item["kind"], item["reference"]["id"])] = read_record(paths, item["kind"], item["reference"]["id"])["reference"]
                projections[key] = projection
            except (ValueError, OSError, KeyError, TypeError) as error:
                report["rejected"].append(item)
                report["issues"].append({"kind": item["kind"], "id": item["reference"]["id"], "reference": item["reference"], "code": "projection_rejected", "message": str(error)})
        with _lock_parent(directory, timeout_seconds=30.0):
            database = directory / "index.sqlite"
            for name in ("index.sqlite", "index.sqlite-journal", "index.sqlite-wal", "index.sqlite-shm"):
                if (directory / name).is_symlink() or (directory / name).is_junction():
                    raise OSError("Unsafe retrieval SQLite path")
            connection = sqlite3.connect(database.as_uri() + "?mode=rw", uri=True)
            try:
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA synchronous=FULL")
                with connection:
                    cancellation = CANCELLATION.get()
                    if cancellation is not None and cancellation():
                        raise InterruptedError("cancelled: retrieval publication cancelled before commit")
                    changed = False
                    for key, item in sorted(selected.items()):
                        kind, reference = item["kind"], item["reference"]
                        ref_json = json.dumps(reference, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                        document = projections.get(key)
                        old = connection.execute("SELECT projection_json FROM documents WHERE document_key=?", (key,)).fetchone()
                        issue = next((entry for entry in report["issues"] if entry["reference"] == reference and entry["kind"] == kind), None)
                        if document is not None:
                            serialized = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                            if old is None or old[0] != serialized:
                                connection.execute("INSERT OR REPLACE INTO documents VALUES (?,?,?,?,?,?,?)", (key, kind, reference["id"], reference.get("revision"), ref_json, document["status"], serialized))
                                connection.execute("DELETE FROM document_text WHERE document_key=?", (key,))
                                fields = ["\n".join(section["text"] for section in document["sections"] if section["name"] == name) for name in SECTIONS]
                                connection.execute("INSERT INTO document_text VALUES (?,?,?,?,?,?,?,?,?,?)", (key, *fields))
                                connection.execute("DELETE FROM links WHERE document_key=?", (key,))
                                for ordinal, link in enumerate(document["links"]):
                                    connection.execute("INSERT INTO links VALUES (?,?,?,?,?)", (key, ordinal, link["relation"], json.dumps(link["reference"], sort_keys=True, separators=(",", ":"), ensure_ascii=False), json.dumps(link, sort_keys=True, separators=(",", ":"), ensure_ascii=False)))
                                report["indexed"].append(item)
                                changed = True
                            else:
                                report["skipped"].append(item)
                            changed |= bool(connection.execute("DELETE FROM issues WHERE issue_key=?", (key,)).rowcount)
                        else:
                            issue_json = json.dumps(issue, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                            old_issue = connection.execute("SELECT issue_json FROM issues WHERE issue_key=?", (key,)).fetchone()
                            if old_issue is None or old_issue[0] != issue_json:
                                connection.execute("INSERT OR REPLACE INTO issues VALUES (?,?,?,?,?)", (key, kind, reference["id"], ref_json, issue_json))
                                changed = True
                            if old is not None:
                                connection.execute("DELETE FROM documents WHERE document_key=?", (key,))
                                connection.execute("DELETE FROM document_text WHERE document_key=?", (key,))
                                changed = True
                        head_reference = observed.get((kind, reference["id"]), reference)
                        head_json = json.dumps(head_reference, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                        head = connection.execute("SELECT reference_json,state FROM heads WHERE kind=? AND id=?", (kind, reference["id"])).fetchone()
                        head_key = canonical_document_key(kind, head_reference)
                        state = "indexed" if connection.execute("SELECT 1 FROM documents WHERE document_key=?", (head_key,)).fetchone() is not None else "rejected"
                        if head is None or kind == "run" or json.loads(head[0])["revision"] <= head_reference["revision"]:
                            if head != (head_json, state):
                                connection.execute("INSERT OR REPLACE INTO heads VALUES (?,?,?,?)", (kind, reference["id"], head_json, state))
                                changed = True
                    if changed:
                        connection.execute("UPDATE meta SET value=? WHERE key='generation'", (str(uuid.uuid4()),))
                    report["generation"] = connection.execute("SELECT value FROM meta WHERE key='generation'").fetchone()[0]
            finally:
                connection.close()
        report["state"] = "partial" if report["rejected"] and (report["indexed"] or report["skipped"]) else "failed" if report["rejected"] else "succeeded"
    except Exception as error:
        report.update(state="failed", indexed=[], skipped=[])
        report["issues"].append({"code": "cancelled" if isinstance(error, InterruptedError) else "indexing_error", "message": str(error)})
    report["completed_at"] = datetime.now(timezone.utc).isoformat()
    try:
        target = directory / "updates"
        for entry in (*reversed(target.parents), target):
            if entry.is_symlink() or entry.is_junction():
                raise OSError("Unsafe retrieval report directory")
        target.mkdir(exist_ok=True)
        _write_json(target / (report["operation_id"] + ".json"), report)
    except Exception as error:
        report["issues"].append({"code": "report_error", "message": str(error)})
    if report["state"] != "succeeded" or report["issues"]:
        logging.getLogger(__name__).warning("Unified retrieval indexing: %s", report)
    validate_retrieval_document(report, "report")
    return report


def notify_project_record_published(paths: ProjectPaths, reference: JSONObject) -> None:
    """Attempt indexing after project publication without changing its return or status.

    Args:
        paths: Project layout.
        reference: Exact newly published case/analysis reference.
    Returns:
        None; ordinary failures are logged separately, interruptions propagate.
    """
    try:
        update_retrieval_index(paths, [{"kind": reference["kind"], "reference": reference}])
    except Exception:
        logging.getLogger(__name__).exception("Project publication succeeded; retrieval notification failed")


def notify_native_records_published(paths: ProjectPaths, simulation_ids: Sequence[str]) -> None:
    """Index only supplied finalized native IDs, independently of scientific execution.

    Args:
        paths: Project layout.
        simulation_ids: Explicit finalized IDs, deduplicated by caller.
    Returns:
        None; ordinary source/index errors are logged, interruptions propagate.
    """
    references = []
    for simulation_id in simulation_ids:
        try:
            references.append({"kind": "run", "reference": project_simulation_inputs(paths, simulation_id)["reference"]})
        except (ValueError, OSError, KeyError, TypeError):
            try:
                from src.srim.common.records import read_simulation
                metadata = read_simulation(paths, simulation_id)
                raw = _read_regular_bytes(paths.simulation_memory / simulation_id / "metadata.json")
                if json.loads(raw) != metadata or metadata["engine"]["name"] not in ("sr", "trim") or metadata["status"] not in ("succeeded", "failed"):
                    raise ValueError("Native terminal metadata changed or is ineligible")
                reference = {"kind": "simulation", "id": simulation_id, "metadata_sha256": hashlib.sha256(raw).hexdigest(), "input_inventory_sha256": hashlib.sha256(json.dumps(sorted(metadata["inputs"], key=lambda item: item["path"]), sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()}
                references.append({"kind": "run", "reference": reference})
            except Exception:
                logging.getLogger(__name__).exception("Native publication succeeded; unified identity unavailable: %s", simulation_id)
    try:
        update_retrieval_index(paths, references)
    except Exception:
        logging.getLogger(__name__).exception("Native publication succeeded; unified notification failed")


def read_retrieval_status(paths: ProjectPaths) -> JSONObject:
    """Read indexed counts and issues without discovering sources or creating storage.

    Args:
        paths: Absolute project layout.
    Returns:
        schema_version, generation, indexed counts and recorded issues.
    Raises:
        ValueError: Corrupt/incompatible catalog.
        OSError: Unsafe paths or bounded lock failure.
    """
    from src.knowledge.retrieval_search import _read_retrieval_snapshot
    from src.knowledge.retrieval_schema import EMPTY_QUERY
    snapshot = _read_retrieval_snapshot(paths, EMPTY_QUERY, None)
    counts = {kind: {"entities": 0, "revisions": 0, "rejected": 0} for kind in KINDS}
    for head in snapshot["heads"]:
        counts[head["kind"]]["entities"] += 1
        counts[head["kind"]]["rejected"] += head["state"] == "rejected"
    for document in snapshot["documents"]:
        counts[document["kind"]]["revisions"] += 1
    return {"schema_version": 1, "generation": snapshot["generation"], "counts": counts, "issues": snapshot["issues"]}


def reconcile_retrieval_index(paths: ProjectPaths, *, scope: str = "registered", expected_plan_sha256: str | None = None) -> JSONObject:
    """Preview or apply exact derived-index repairs after checking the plan digest.

    Args:
        paths: Project layout.
        scope: registered heads only or explicit project publication inventory.
        expected_plan_sha256: Null for preview, exact current plan digest for apply.
    Returns:
        Closed plan envelope and optional update report.
    Raises:
        ValueError: Invalid scope/digest, stale plan or incompatible index.
        OSError: Fatal catalog access failure; source failures remain plan issues.
    """
    from src.knowledge.retrieval_search import _read_retrieval_snapshot
    from src.knowledge.retrieval_schema import EMPTY_QUERY
    if scope not in ("registered", "project") or expected_plan_sha256 is not None and (not isinstance(expected_plan_sha256, str) or len(expected_plan_sha256) != 64):
        raise ValueError("Invalid reconciliation scope/digest")
    snapshot = _read_retrieval_snapshot(paths, EMPTY_QUERY, None)
    identities = {(head["kind"], head["id"]) for head in snapshot["heads"]}
    if scope == "project":
        for kind, parent in (("run", paths.simulation_memory), ("case", paths.cases), ("analysis", paths.postprocessing_memory)):
            if parent.is_symlink() or parent.is_junction():
                raise OSError("Unsafe project source directory")
            if parent.exists():
                identities.update((kind, entry.name) for entry in parent.iterdir() if not entry.name.startswith("."))
    actions, issues = [], []
    indexed = {document["document_key"]: document for document in snapshot["documents"]}
    for kind, identity in sorted(identities):
        try:
            reference = project_simulation_inputs(paths, identity)["reference"] if kind == "run" else read_record(paths, kind, identity)["reference"]
            document = project_retrieval_document(paths, kind, reference)
            if indexed.get(document["document_key"]) != document:
                actions.append({"kind": kind, "reference": reference})
        except (ValueError, OSError, KeyError, TypeError) as error:
            issues.append({"kind": kind, "id": identity, "code": "reconcile_source", "message": str(error)})
    plan = {"schema_version": 1, "scope": scope, "plan_sha256": "", "actions": actions, "issues": issues, "report": None}
    digest = hashlib.sha256(json.dumps({"generation": snapshot["generation"], "scope": scope, "actions": actions, "issues": issues}, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
    plan["plan_sha256"] = digest
    if expected_plan_sha256 is not None:
        if expected_plan_sha256 != digest:
            raise ValueError("conflict: reconciliation plan changed")
        plan["report"] = update_retrieval_index(paths, actions)
    validate_retrieval_document(plan, "plan")
    return plan
