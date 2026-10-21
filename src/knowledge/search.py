"""Search the native input catalog with exact filters and pinned result generations."""

from __future__ import annotations

import json
import sqlite3
import uuid

from src.backend.paths import ProjectPaths
from src.backend.records import _lock_parent, _validate_paths
from src.knowledge.index import PROJECTOR_VERSION
from src.knowledge.schema import matches_filters, validate_search_document
from src.knowledge.source import read_source_record
from src.srim.metadata import JSONObject


def search_inputs(paths: ProjectPaths, filters: JSONObject | None = None, *, offset: int = 0, limit: int = 100, expected_generation: str | None = None) -> JSONObject:
    """Return a stable page of paths' currently verified native input matches.

    Args:
        paths: Existing agreed absolute project directory layout.
        filters: Exact filter envelope or None for all registered terminal runs.
        offset: Nonnegative integer match offset, excluding bool.
        limit: Integer page size from 1 to 1,000, default 100.
        expected_generation: Optional UUID pinning a previously returned catalog.

    Returns:
        Independent SearchResult with valid total, page and excluded-source issues.
        Missing catalog returns an empty result without creating storage.

    Side effects:
        Reads catalog/input evidence; existing-catalog locking may create lock files.
        Never refreshes, scans historical runs, reads outputs or invokes a model.

    Raises:
        ValueError: Filters/pagination/schema/generation are invalid or incompatible.
        OSError: Storage is unsafe, unreadable or the bounded lock is busy.
    """
    _validate_paths(paths)
    filters = {"schema_version": 1, "clauses": []} if filters is None else filters
    validate_search_document(filters, "filters")
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("Invalid search pagination")
    if expected_generation is not None:
        try:
            uuid.UUID(expected_generation)
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError("Invalid expected generation UUID") from error
    result: JSONObject = {"schema_version": 1, "generation": None, "total": 0, "offset": offset, "limit": limit, "matches": [], "excluded": []}
    directory = paths.root / "memory/knowledge"
    database = directory / "index.sqlite"
    for path in (directory, database):
        if path.is_symlink() or path.is_junction():
            raise OSError("Unsafe catalog path")
    if not database.exists():
        if expected_generation is not None:
            raise ValueError("Catalog generation changed or is unavailable")
        return result
    with _lock_parent(directory, timeout_seconds=30.0):
        if database.is_symlink() or database.is_junction():
            raise OSError("Unsafe catalog path")
        for suffix in ("-journal", "-wal", "-shm"):
            path = directory / ("index.sqlite" + suffix)
            if path.is_symlink() or path.is_junction():
                raise OSError("Unsafe SQLite sidecar")
        connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
        try:
            meta = dict(connection.execute("SELECT key,value FROM meta"))
            columns = {table: [row[1] for row in connection.execute(f"PRAGMA table_info({table})")] for table in ("records", "issues", "meta")}
            if connection.execute("PRAGMA user_version").fetchone()[0] != 1 or set(meta) != {"schema_version", "generation", "projector_version"} or meta["schema_version"] != "1" or meta["projector_version"] != PROJECTOR_VERSION or columns != {"records": ["id", "metadata_sha256", "input_inventory_sha256", "engine", "mode", "execution_status", "projection_json"], "issues": ["id", "issue_json"], "meta": ["key", "value"]}:
                raise ValueError("Incompatible native input-search schema")
            uuid.UUID(meta["generation"])
            result["generation"] = meta["generation"]
            if expected_generation is not None and expected_generation != result["generation"]:
                raise ValueError("Catalog generation changed; restart pagination")
            sql = "SELECT id,projection_json FROM records"
            predicates: list[str] = []
            parameters: list[str] = []
            for clause in filters["clauses"]:
                if clause["field"] in ("engine", "execution_status") and clause["operator"] in ("eq", "in"):
                    choices = [clause["value"]] if clause["operator"] == "eq" else clause["value"]
                    predicates.append(clause["field"] + " IN (" + ",".join("?" for _ in choices) + ")")
                    parameters.extend(choices)
            if predicates:
                sql += " WHERE " + " AND ".join(predicates)
            sql += " ORDER BY id"
            cursor = connection.execute(sql, parameters)
            while rows := cursor.fetchmany(100):
                for simulation_id, serialized in rows:
                    try:
                        indexed = json.loads(serialized)
                        validate_search_document(indexed, "record")
                        if indexed["reference"]["id"] != simulation_id:
                            raise ValueError("Catalog identity differs from projection")
                        current = read_source_record(paths, indexed["reference"])
                        if matches_filters(current, filters):
                            if offset <= result["total"] < offset + limit:
                                result["matches"].append(current)
                            result["total"] += 1
                    except (ValueError, OSError, KeyError, TypeError) as error:
                        result["excluded"].append({"simulation_id": simulation_id, "code": "stale", "field": None, "message": str(error)})
            for serialized, in connection.execute("SELECT issue_json FROM issues ORDER BY id"):
                issues = json.loads(serialized)
                if not isinstance(issues, list):
                    raise ValueError("Corrupt catalog issue list")
                for issue in issues:
                    validate_search_document(issue, "issue")
                    result["excluded"].append(issue)
        except sqlite3.DatabaseError as error:
            raise ValueError("Corrupt native input-search catalog") from error
        finally:
            connection.close()
    validate_search_document(result, "result")
    return result
