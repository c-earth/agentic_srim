"""Maintain an incremental native-input catalog without changing simulation outcomes."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import hashlib
import logging
import os
from pathlib import Path
import re
import sqlite3
from threading import Lock
import uuid

from src.backend.paths import ProjectPaths
from src.backend.records import _lock_parent, _validate_paths
from src.knowledge.schema import validate_search_document
from src.knowledge.source import project_simulation_inputs
from src.srim.common.types import JSONObject
from src.srim.common.files import _inventory, _read_json, _write_json
from src.srim.common.records import _simulation_path
from src.srim.common.files import _read_regular_bytes


PROJECTOR_VERSION = "native-input-search-v1"
LOGGER = logging.getLogger(__name__)
COLLECTORS: ContextVar[dict[str, object] | None] = ContextVar("native_search_collectors", default=None)
TABLES = (
    "CREATE TABLE records(id TEXT PRIMARY KEY,metadata_sha256 TEXT,input_inventory_sha256 TEXT,engine TEXT,mode TEXT,execution_status TEXT,projection_json TEXT)",
    "CREATE TABLE issues(id TEXT PRIMARY KEY,issue_json TEXT)",
    "CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)",
)


def initialize_index(paths: ProjectPaths) -> JSONObject:
    """Create an empty catalog under paths or verify the existing catalog.

    Args:
        paths: Existing agreed absolute project directory layout.

    Returns:
        schema_version, generation and created flag; never discovers old runs.

    Side effects:
        Creates knowledge storage, coordination lock and an empty SQLite database.

    Raises:
        ValueError: Existing database schema/projector version is incompatible.
        OSError: A path is unsafe, inaccessible or its bounded lock is busy.
        sqlite3.Error: Existing database is corrupt or initialization fails.
    """
    _validate_paths(paths)
    directory = paths.root / "memory/knowledge"
    if directory.is_symlink() or directory.is_junction():
        raise OSError("Unsafe knowledge directory")
    directory.mkdir(exist_ok=True)
    database = directory / "index.sqlite"
    with _lock_parent(directory, timeout_seconds=30.0):
        for path in (database, Path(str(database) + "-journal"), Path(str(database) + "-wal"), Path(str(database) + "-shm")):
            if path.is_symlink() or path.is_junction():
                raise OSError("Unsafe SQLite path")
        created = not database.exists()
        connection = sqlite3.connect(database)
        try:
            connection.execute("PRAGMA synchronous=FULL")
            if created:
                connection.execute("PRAGMA journal_mode=DELETE")
                with connection:
                    for statement in TABLES:
                        connection.execute(statement)
                    connection.execute("PRAGMA user_version=1")
                    connection.executemany("INSERT INTO meta VALUES (?,?)", (("schema_version", "1"), ("generation", str(uuid.uuid4())), ("projector_version", PROJECTOR_VERSION)))
            meta = dict(connection.execute("SELECT key,value FROM meta"))
            columns = {table: [row[1] for row in connection.execute(f"PRAGMA table_info({table})")] for table in ("records", "issues", "meta")}
            if connection.execute("PRAGMA user_version").fetchone()[0] != 1 or meta.get("schema_version") != "1" or meta.get("projector_version") != PROJECTOR_VERSION or set(meta) != {"schema_version", "generation", "projector_version"} or columns != {"records": ["id", "metadata_sha256", "input_inventory_sha256", "engine", "mode", "execution_status", "projection_json"], "issues": ["id", "issue_json"], "meta": ["key", "value"]}:
                raise ValueError("Incompatible native input-search schema")
            uuid.UUID(meta["generation"])
            return {"schema_version": 1, "generation": meta["generation"], "created": created}
        finally:
            connection.close()


def update_index(paths: ProjectPaths, simulation_ids: Sequence[str]) -> JSONObject:
    """Incrementally index only simulation_ids under paths and report separately.

    Args:
        paths: Existing agreed absolute project directory layout.
        simulation_ids: Non-string sequence of canonical native simulation IDs.

    Returns:
        UpdateReport with committed/indexed/skipped/rejected IDs and diagnostics.

    Side effects:
        Initializes/updates catalog and writes a separate best-effort update report.
        Never changes permanent simulations or scans historical/unrelated records.

    Raises:
        ValueError: Caller supplies malformed paths/IDs before work starts.
        BaseException: Interruption propagates after closing the SQLite connection.
        Ordinary operational failures are represented in the returned report.
    """
    _validate_paths(paths)
    if isinstance(simulation_ids, (str, bytes)) or not isinstance(simulation_ids, Sequence) or any(not isinstance(item, str) or re.fullmatch(r"srim_[0-9]{8}_[0-9]{6}_[1-9][0-9]*", item) is None for item in simulation_ids):
        raise ValueError("Expected sequence of canonical simulation IDs")
    ids = sorted(set(simulation_ids))
    report: JSONObject = {"schema_version": 1, "operation_id": "update_" + str(uuid.uuid4()), "state": "succeeded", "generation": None, "attempted": ids, "indexed": [], "skipped": [], "rejected": [], "issues": [], "completed_at": datetime.now(timezone.utc).isoformat()}
    if not ids:
        return report
    directory = paths.root / "memory/knowledge"
    try:
        report["generation"] = initialize_index(paths)["generation"]
        cached: dict[str, JSONObject] = {}
        with _lock_parent(directory, timeout_seconds=30.0):
            connection = sqlite3.connect((directory / "index.sqlite").as_uri() + "?mode=ro", uri=True)
            try:
                for simulation_id in ids:
                    row = connection.execute("SELECT projection_json FROM records WHERE id=?", (simulation_id,)).fetchone()
                    if row is not None:
                        entry = json.loads(row[0])
                        validate_search_document(entry, "record")
                        cached[simulation_id] = entry
            finally:
                connection.close()
        projected: dict[str, JSONObject] = {}
        failures: dict[str, list[JSONObject]] = {}
        for simulation_id in ids:
            try:
                entry = cached.get(simulation_id)
                record_path = _simulation_path(paths, simulation_id)
                if entry is not None and hashlib.sha256(_read_regular_bytes(record_path / "metadata.json")).hexdigest() == entry["reference"]["metadata_sha256"] and _inventory(record_path / "input", record_path) == _read_json(record_path / "metadata.json")["inputs"]:
                    projected[simulation_id] = entry
                else:
                    projected[simulation_id] = project_simulation_inputs(paths, simulation_id)
            except (ValueError, OSError, KeyError, TypeError) as error:
                code = str(error).split(":", 1)[0]
                failures[simulation_id] = [{"simulation_id": simulation_id, "code": code if code in ("unsupported", "malformed", "missing", "integrity", "conflicting", "stale", "ineligible") else "malformed", "field": None, "message": str(error)}]
        with _lock_parent(directory, timeout_seconds=30.0):
            database = directory / "index.sqlite"
            if database.is_symlink() or database.is_junction():
                raise OSError("Unsafe catalog path")
            connection = sqlite3.connect(database)
            try:
                connection.execute("PRAGMA synchronous=FULL")
                with connection:
                    changed = False
                    for simulation_id in ids:
                        record = projected.get(simulation_id)
                        if record is not None:
                            try:
                                record_path = _simulation_path(paths, simulation_id)
                                raw = _read_regular_bytes(record_path / "metadata.json")
                                if hashlib.sha256(raw).hexdigest() != record["reference"]["metadata_sha256"] or _inventory(record_path / "input", record_path) != json.loads(raw)["inputs"]:
                                    raise ValueError("stale: input changed before index publication")
                            except (ValueError, OSError, KeyError, TypeError) as error:
                                failures[simulation_id] = [{"simulation_id": simulation_id, "code": "stale", "field": None, "message": str(error)}]
                                record = None
                        if record is None:
                            issue_json = json.dumps(failures[simulation_id], sort_keys=True, ensure_ascii=False, allow_nan=False)
                            old = connection.execute("SELECT issue_json FROM issues WHERE id=?", (simulation_id,)).fetchone()
                            removed = connection.execute("DELETE FROM records WHERE id=?", (simulation_id,)).rowcount
                            if old is None or old[0] != issue_json or removed:
                                connection.execute("INSERT OR REPLACE INTO issues VALUES (?,?)", (simulation_id, issue_json))
                                changed = True
                            report["rejected"].append(simulation_id)
                            report["issues"].extend(failures[simulation_id])
                            continue
                        projection = json.dumps(record, sort_keys=True, ensure_ascii=False, allow_nan=False)
                        old = connection.execute("SELECT projection_json FROM records WHERE id=?", (simulation_id,)).fetchone()
                        if old is not None and old[0] == projection:
                            report["skipped"].append(simulation_id)
                            continue
                        ref, inputs = record["reference"], record["inputs"]
                        connection.execute("INSERT OR REPLACE INTO records VALUES (?,?,?,?,?,?,?)", (simulation_id, ref["metadata_sha256"], ref["input_inventory_sha256"], inputs["engine"], inputs["mode"], record["execution_status"], projection))
                        connection.execute("DELETE FROM issues WHERE id=?", (simulation_id,))
                        report["indexed"].append(simulation_id)
                        changed = True
                    if changed:
                        connection.execute("UPDATE meta SET value=? WHERE key='generation'", (str(uuid.uuid4()),))
                    report["generation"] = connection.execute("SELECT value FROM meta WHERE key='generation'").fetchone()[0]
            finally:
                connection.close()
        report["state"] = "partial" if report["rejected"] and (report["indexed"] or report["skipped"]) else "failed" if report["rejected"] else "succeeded"
    except Exception as error:
        report["state"] = "failed"
        report["indexed"] = []
        report["skipped"] = []
        report["issues"].append({"simulation_id": ids[0], "code": "indexing_error", "field": None, "message": str(error)})
    report["completed_at"] = datetime.now(timezone.utc).isoformat()
    try:
        _write_update_report(paths, report)
    except Exception as error:
        report["issues"].append({"simulation_id": ids[0], "code": "indexing_error", "field": None, "message": "Diagnostic publication failed: " + str(error)})
    if report["state"] != "succeeded" or report["issues"]:
        _log_index_failure(report)
    return report


def rebuild_index(paths: ProjectPaths, simulation_ids: Sequence[str]) -> JSONObject:
    """Replace catalog membership with explicitly selected simulation_ids under paths.

    Args:
        paths: Existing absolute project layout.
        simulation_ids: Nonempty sequence of canonical IDs, never discovered implicitly.

    Returns:
        Separate UpdateReport; failed publication leaves the previous catalog intact.

    Side effects:
        Stages a database and atomically replaces the catalog under a bounded lock.
        Retains failed staging; permanent simulation evidence is never modified.

    Raises:
        ValueError: The supplied ID sequence/project layout is invalid.
        BaseException: Interruption propagates with retained staging path notes.
    """
    _validate_paths(paths)
    if isinstance(simulation_ids, (str, bytes)) or not isinstance(simulation_ids, Sequence) or not simulation_ids or any(not isinstance(item, str) or re.fullmatch(r"srim_[0-9]{8}_[0-9]{6}_[1-9][0-9]*", item) is None for item in simulation_ids):
        raise ValueError("Rebuild requires a nonempty explicit canonical ID sequence")
    ids = sorted(set(simulation_ids))
    report: JSONObject = {"schema_version": 1, "operation_id": "update_" + str(uuid.uuid4()), "state": "succeeded", "generation": None, "attempted": ids, "indexed": [], "skipped": [], "rejected": [], "issues": [], "completed_at": datetime.now(timezone.utc).isoformat()}
    directory = paths.root / "memory/knowledge"
    staged: Path | None = None
    try:
        if directory.is_symlink() or directory.is_junction():
            raise OSError("Unsafe knowledge directory")
        directory.mkdir(exist_ok=True)
        database = directory / "index.sqlite"
        with _lock_parent(directory, timeout_seconds=30.0):
            if database.is_symlink() or database.is_junction():
                raise OSError("Unsafe catalog path")
            before = None if not database.exists() else _read_regular_bytes(database)
        staging = directory / ".staging"
        if staging.is_symlink() or staging.is_junction():
            raise OSError("Unsafe staging directory")
        staging.mkdir(exist_ok=True)
        staged = staging / ("rebuild_" + str(uuid.uuid4()) + ".sqlite")
        connection = sqlite3.connect(staged)
        try:
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("PRAGMA synchronous=FULL")
            with connection:
                for statement in TABLES:
                    connection.execute(statement)
                generation = str(uuid.uuid4())
                connection.execute("PRAGMA user_version=1")
                connection.executemany("INSERT INTO meta VALUES (?,?)", (("schema_version", "1"), ("generation", generation), ("projector_version", PROJECTOR_VERSION)))
                for simulation_id in ids:
                    try:
                        record = project_simulation_inputs(paths, simulation_id)
                        ref, inputs = record["reference"], record["inputs"]
                        connection.execute("INSERT INTO records VALUES (?,?,?,?,?,?,?)", (simulation_id, ref["metadata_sha256"], ref["input_inventory_sha256"], inputs["engine"], inputs["mode"], record["execution_status"], json.dumps(record, sort_keys=True, ensure_ascii=False, allow_nan=False)))
                        report["indexed"].append(simulation_id)
                    except (ValueError, OSError, KeyError, TypeError) as error:
                        issue = {"simulation_id": simulation_id, "code": "integrity", "field": None, "message": str(error)}
                        connection.execute("INSERT INTO issues VALUES (?,?)", (simulation_id, json.dumps([issue], sort_keys=True)))
                        report["rejected"].append(simulation_id)
                        report["issues"].append(issue)
        finally:
            connection.close()
        with _lock_parent(directory, timeout_seconds=30.0):
            current = None if not database.exists() else _read_regular_bytes(database)
            if current != before:
                raise ValueError("Catalog changed during rebuild; retry explicitly")
            os.replace(staged, database)
        report["generation"] = generation
        report["state"] = "partial" if report["rejected"] and report["indexed"] else "failed" if report["rejected"] else "succeeded"
    except Exception as error:
        report["state"] = "failed"
        report["indexed"] = []
        report["issues"].append({"simulation_id": ids[0], "code": "indexing_error", "field": None, "message": f"{error}; retained staging: {staged}"})
    except BaseException as error:
        error.add_note(f"Retained rebuild staging: {staged}")
        raise
    report["completed_at"] = datetime.now(timezone.utc).isoformat()
    try:
        _write_update_report(paths, report)
    except Exception as error:
        report["issues"].append({"simulation_id": ids[0], "code": "indexing_error", "field": None, "message": str(error)})
    if report["state"] != "succeeded" or report["issues"]:
        _log_index_failure(report)
    return report


def notify_simulation_finished(paths: ProjectPaths, metadata: JSONObject) -> None:
    """Collect or index paths' just-published metadata independently of engine outcomes.

    Args:
        paths: Existing project layout of the finalized simulation.
        metadata: Unmodified terminal metadata returned by native finalization.

    Returns:
        None; ordinary index/report/log failures are contained and logged separately.
        Interruption is not swallowed. No simulation evidence is changed.
    """
    try:
        if metadata["engine"]["name"] not in ("sr", "trim") or metadata["status"] not in ("succeeded", "failed"):
            return
        collectors = COLLECTORS.get()
        key = os.path.normcase(str(paths.root))
        if collectors is not None and key in collectors:
            collector = collectors[key]
            with collector["lock"]:
                collector["ids"].add(metadata["id"])
        else:
            _notify_indexes(paths, [metadata["id"]])
    except Exception as error:
        _log_index_failure({"state": "failed", "operation_id": "notification", "issues": [{"message": str(error)}]})


@contextmanager
def collect_index_updates(paths: ProjectPaths) -> Iterator[None]:
    """Yield a paths-specific batch collection context and flush finalized IDs once.

    Args:
        paths: Project whose batch propagates copied contexts to worker threads.

    Yields:
        None; an owning context updates its deduplicated IDs on exit.

    Side effects:
        Uses a context-local thread-safe collector and writes only catalog/reports.
        Nested same-project contexts share ownership. Restore context on all exits.

    Raises:
        BaseException: Original batch interruption/error is preserved; an additional
            flush interruption is attached to it instead of masking it.
    """
    key = os.path.normcase(str(paths.root))
    previous = COLLECTORS.get()
    if previous is not None and key in previous:
        yield
        return
    collector = {"lock": Lock(), "ids": set()}
    token = COLLECTORS.set({**(previous or {}), key: collector})
    original: BaseException | None = None
    try:
        yield
    except BaseException as error:
        original = error
        raise
    finally:
        COLLECTORS.reset(token)
        try:
            with collector["lock"]:
                ids = sorted(collector["ids"])
            if ids:
                _notify_indexes(paths, ids)
        except Exception as error:
            _log_index_failure({"state": "failed", "operation_id": "batch", "issues": [{"message": str(error)}]})
        except BaseException as error:
            if original is not None:
                original.add_note(f"Index flush also interrupted: {error}")
            else:
                raise


def _write_update_report(paths: ProjectPaths, report: JSONObject) -> None:
    """Persist report beneath paths' knowledge updates without changing simulations.

    Args:
        paths: Agreed existing project layout.
        report: Exact validated UpdateReport with a unique operation ID.

    Returns:
        None after atomic JSON publication; no original evidence changes.

    Raises:
        ValueError: Report schema is invalid.
        OSError: Unsafe paths, collision or publication failure occurs.
    """
    validate_search_document(report, "update-report")
    directory = paths.root / "memory/knowledge/updates"
    for path in (*reversed(directory.parents), directory):
        if path.is_symlink() or path.is_junction():
            raise OSError("Unsafe update report path")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (report["operation_id"] + ".json")
    if path.exists():
        raise FileExistsError(str(path))
    _write_json(path, report)


def _log_index_failure(report: JSONObject) -> None:
    """Log report's operational state/issues without modifying evidence or raising.

    Args:
        report: Index diagnostic containing operation_id, state and issue messages.

    Returns:
        None; ordinary custom logging-handler errors are contained. No logger,
        filesystem or network configuration is changed.
    """
    try:
        LOGGER.warning("Input indexing %s: %s; %s", report["operation_id"], report["state"], report["issues"])
    except Exception:
        pass


def _notify_indexes(paths: ProjectPaths, simulation_ids: Sequence[str]) -> None:
    """Attempt legacy and unified indexing independently after publication.

    Args:
        paths: Published simulation project.
        simulation_ids: Explicit finalized run identities.
    Returns:
        None; ordinary failures are logged, interruption propagates.
    """
    from src.knowledge.retrieval_index import notify_native_records_published
    for operation in (update_index, notify_native_records_published):
        try:
            operation(paths, simulation_ids)
        except Exception:
            LOGGER.exception("Native publication succeeded; independent catalog notification failed")
