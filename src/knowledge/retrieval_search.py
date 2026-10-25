"""Search unified catalog snapshots and read pinned documents without long publication locks."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import sqlite3
import uuid

from src.backend.paths import ProjectPaths
from src.backend.project_records import read_record
from src.backend.records import _lock_parent, _validate_paths
from src.knowledge.retrieval_schema import EMPTY_QUERY, KINDS, PROJECTOR_VERSION, SECTIONS, WEIGHTS, canonical_document_key, compile_retrieval_query, match_output_filter, validate_retrieval_document
from src.knowledge.retrieval_index import TABLE_COLUMNS
from src.knowledge.retrieval_sources import project_retrieval_document, read_retrieval_document
from src.knowledge.schema import _compile_filters
from src.knowledge.source import read_source_record
from src.srim.common.types import JSONObject


def _read_retrieval_snapshot(paths: ProjectPaths, query: JSONObject, expected_generation: str | None) -> JSONObject:
    """Capture catalog rows, exact links and FTS ranks before source verification.

    Args:
        paths: Absolute agreed layout.
        query: Closed unified query.
        expected_generation: Optional pinned catalog UUID.
    Returns:
        Independent snapshot; absent catalog has null generation and no rows.
    Raises:
        ValueError: Invalid query, generation, schema or database.
        OSError: Unsafe path or bounded lock contention.
    """
    _validate_paths(paths)
    validate_retrieval_document(query, "query")
    if expected_generation is not None:
        try:
            uuid.UUID(expected_generation)
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError("Invalid expected generation") from error
    directory = paths.root / "memory/knowledge/retrieval"
    database = directory / "index.sqlite"
    for entry in (*reversed(database.parents), database):
        if entry.is_symlink() or entry.is_junction():
            raise OSError("Unsafe retrieval catalog path")
    result = {"generation": None, "documents": [], "heads": [], "links": [], "issues": [], "ranks": {}, "tokens": []}
    tokenizer = sqlite3.connect(":memory:")
    try:
        tokenizer.execute("CREATE VIRTUAL TABLE query_tokens USING fts5(text,tokenize='unicode61 remove_diacritics 0')")
        tokenizer.execute("CREATE VIRTUAL TABLE query_vocab USING fts5vocab(query_tokens,'instance')")
        if query["text"] is not None:
            tokenizer.execute("INSERT INTO query_tokens VALUES (?)", (query["text"],))
            result["tokens"] = [row[0] for row in tokenizer.execute("SELECT term FROM query_vocab ORDER BY offset")]
            if not result["tokens"] or len(result["tokens"]) > 64:
                raise ValueError("Text requires 1..64 searchable tokens")
    finally:
        tokenizer.close()
    if not database.exists():
        if expected_generation is not None:
            raise ValueError("Catalog generation changed or is unavailable")
        return result
    with _lock_parent(directory, timeout_seconds=30.0):
        for suffix in ("", "-journal", "-wal", "-shm"):
            entry = directory / ("index.sqlite" + suffix)
            if entry.is_symlink() or entry.is_junction():
                raise OSError("Unsafe retrieval SQLite file")
        connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
        try:
            meta = dict(connection.execute("SELECT key,value FROM meta"))
            if connection.execute("PRAGMA user_version").fetchone()[0] != 1 or set(meta) != {"schema_version", "projector_version", "generation"} or meta["schema_version"] != "1" or meta["projector_version"] != PROJECTOR_VERSION:
                raise ValueError("Incompatible retrieval catalog")
            for name, expected in TABLE_COLUMNS.items():
                if [row[1] for row in connection.execute("PRAGMA table_info(" + name + ")")] != expected:
                    raise ValueError("Incompatible retrieval table: " + name)
            uuid.UUID(meta["generation"])
            result["generation"] = meta["generation"]
            if expected_generation is not None and expected_generation != result["generation"]:
                raise ValueError("Catalog generation changed; restart pagination")
            for key, kind, identity, serialized in connection.execute("SELECT document_key,kind,id,projection_json FROM documents ORDER BY kind,id,revision,document_key"):
                document = json.loads(serialized)
                validate_retrieval_document(document, "document")
                if (document["document_key"], document["kind"], document["id"]) != (key, kind, identity):
                    raise ValueError("Catalog projection identity differs from row")
                result["documents"].append(document)
            for kind, identity, reference, state in connection.execute("SELECT kind,id,reference_json,state FROM heads ORDER BY kind,id"):
                reference = json.loads(reference)
                canonical_document_key(kind, reference)
                if reference["id"] != identity or state not in ("indexed", "rejected"):
                    raise ValueError("Invalid catalog current head")
                result["heads"].append({"kind": kind, "id": identity, "reference": reference, "state": state})
            for key, ordinal, serialized in connection.execute("SELECT document_key,ordinal,link_json FROM links ORDER BY document_key,ordinal"):
                result["links"].append({"document_key": key, "ordinal": ordinal, "link": json.loads(serialized)})
            for serialized, in connection.execute("SELECT issue_json FROM issues ORDER BY issue_key"):
                result["issues"].append(json.loads(serialized))
            if query["text"] is not None:
                expression = " AND ".join('"' + token.replace('"', '""') + '"' for token in result["tokens"])
                statement = "SELECT document_key,bm25(document_text,0," + ",".join(str(weight) for weight in WEIGHTS) + ") FROM document_text WHERE document_text MATCH ?"
                result["ranks"] = dict(connection.execute(statement, (expression,)))
        except sqlite3.DatabaseError as error:
            raise ValueError("Corrupt unified retrieval catalog") from error
        finally:
            connection.close()
    return result


def _match_retrieval_relations(paths: ProjectPaths, document: JSONObject, clauses: list[JSONObject], snapshot: JSONObject) -> JSONObject:
    """Evaluate direct exact-reference links with shared target-run witnesses.

    Args:
        paths: Project layout for pinned reads.
        document: Fresh verified origin/candidate.
        clauses: Closed relation constraints.
        snapshot: Captured indexed origins for incoming queries.
    Returns:
        matched flag, matching links and per-target issues.
    Raises:
        ValueError: Malformed relation query.
    """
    result = {"matched": True, "links": [], "issues": []}
    cache = {}
    for clause in clauses:
        matches = []
        if clause["direction"] == "outgoing":
            candidates = [(document, link) for link in document["links"]]
        else:
            candidates = [(origin, link) for origin in snapshot["documents"] for link in origin["links"]]
        for origin, link in candidates:
            if link["relation"] != clause["relation"]:
                continue
            target = link["reference"].get("source") if link["reference"].get("kind") == "native-source" else link["reference"]
            if clause["direction"] == "incoming":
                if target != document["reference"]:
                    continue
                if clause["reference"] is not None and origin["reference"] != clause["reference"]:
                    continue
                try:
                    key = origin["document_key"]
                    if key not in cache:
                        cache[key] = project_retrieval_document(paths, origin["kind"], origin["reference"])
                    if link not in cache[key]["links"]:
                        continue
                except (ValueError, OSError) as error:
                    result["issues"].append({"reference": origin["reference"], "message": str(error)})
                    continue
            elif clause["reference"] is not None:
                wanted = clause["reference"].get("source") if clause["reference"].get("kind") == "native-source" else clause["reference"]
                if target != wanted:
                    continue
            if clause["target_input_filters"] is not None or clause["target_output_filter"] is not None:
                if target.get("kind") != "simulation":
                    continue
                try:
                    key = canonical_document_key("run", target)
                    if key not in cache:
                        cache[key] = read_source_record(paths, target)
                    if clause["target_input_filters"] is not None and not _compile_filters(clause["target_input_filters"])(cache[key]):
                        continue
                    if clause["target_output_filter"] is not None:
                        output_key = key + ":outputs"
                        if output_key not in cache:
                            cache[output_key] = project_retrieval_document(paths, "run", target)["outputs"]
                        if not match_output_filter(cache[output_key], clause["target_output_filter"])["matched"]:
                            continue
                except (ValueError, OSError) as error:
                    result["issues"].append({"reference": target, "message": str(error)})
                    continue
            matches.append({"origin": origin["reference"], "link": link, "target": target})
        if not matches:
            result["matched"] = False
        result["links"].extend(matches)
    return result


def search_knowledge(paths: ProjectPaths, query: JSONObject, *, offset: int = 0, limit: int = 100, expected_generation: str | None = None) -> JSONObject:
    """Return a deterministic verified page of unified input/output/text/link matches.

    Args:
        paths: Project layout.
        query: Complete unified retrieval query.
        offset: Nonnegative verified match offset.
        limit: Page size 1..1000.
        expected_generation: Optional pinned catalog generation.
    Returns:
        SearchResult with query-scoped exclusions and catalog-only coverage.
    Raises:
        ValueError: Invalid arguments, schema or changed generation.
        OSError: Fatal catalog access/lock failure.
    """
    query = deepcopy(query)
    evaluate = compile_retrieval_query(query)
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("Invalid retrieval pagination")
    snapshot = _read_retrieval_snapshot(paths, query, expected_generation)
    digest = hashlib.sha256(json.dumps(query, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
    result = {"schema_version": 1, "generation": snapshot["generation"], "query_sha256": digest, "total": 0, "offset": offset, "limit": limit, "matches": [], "excluded": [], "coverage": {"entities": len(snapshot["heads"]), "revisions": len(snapshot["documents"]), "rejected": sum(head["state"] == "rejected" for head in snapshot["heads"]), "scope": "indexed only"}}
    heads = {(head["kind"], head["id"]): head for head in snapshot["heads"]}
    candidates = []
    for indexed in snapshot["documents"]:
        if indexed["kind"] not in query["kinds"]:
            continue
        head = heads.get((indexed["kind"], indexed["id"]))
        if query["revision_scope"] == "current" and (head is None or head["state"] != "indexed" or head["reference"] != indexed["reference"]):
            continue
        if query["text"] is not None and indexed["document_key"] not in snapshot["ranks"]:
            continue
        statuses = query["statuses"][indexed["kind"]]
        if statuses is not None and indexed["status"] not in statuses:
            continue
        candidates.append(indexed)
    candidates.sort(key=lambda item: (snapshot["ranks"].get(item["document_key"], 0), item["kind"], item["id"], item["reference"].get("revision", 0), item["document_key"]))
    for indexed in candidates:
        try:
            if query["revision_scope"] == "current" and indexed["kind"] != "run":
                current = read_record(paths, indexed["kind"], indexed["id"])["reference"]
                if current != indexed["reference"]:
                    raise ValueError("stale_current: indexed revision is not current; reconcile")
            fresh = read_retrieval_document(paths, indexed["kind"], indexed["reference"])
            document = fresh["document"]
            if query["text"] is not None:
                text_check = sqlite3.connect(":memory:")
                try:
                    text_check.execute("CREATE VIRTUAL TABLE current_text USING fts5(text,tokenize='unicode61 remove_diacritics 0')")
                    text_check.execute("INSERT INTO current_text VALUES (?)", ("\n".join(section["text"] for section in document["sections"]),))
                    expression = " AND ".join('"' + token.replace('"', '""') + '"' for token in snapshot["tokens"])
                    if text_check.execute("SELECT 1 FROM current_text WHERE current_text MATCH ?", (expression,)).fetchone() is None:
                        raise ValueError("stale: indexed text has no current source witness; reconcile")
                finally:
                    text_check.close()
            if not evaluate(document):
                continue
            relations = _match_retrieval_relations(paths, document, query["relations"], snapshot)
            if not relations["matched"]:
                result["excluded"].extend(relations["issues"])
                continue
            if offset <= result["total"] < offset + limit:
                excerpts = []
                for section in sorted(document["sections"], key=lambda item: -item["weight"]):
                    folded = section["text"].casefold()
                    positions = [folded.find(token) for token in snapshot["tokens"] if token in folded]
                    if positions:
                        start = max(0, min(positions) - 80)
                        excerpts.append({"name": section["name"], "text": section["text"][start:start + 320], "locator": section["locator"]})
                    if len(excerpts) == 3:
                        break
                outputs = None if document["outputs"] is None else {"result_source": document["outputs"]["result_source"], "complete": document["outputs"]["complete"], "state": document["outputs"]["state"], "warnings": document["outputs"]["warnings"], "issues": document["outputs"]["issues"], "matches": None if query["output_filter"] is None else match_output_filter(document["outputs"], query["output_filter"])}
                verification = fresh["verification"]
                if query["output_filter"] is not None or any(clause["target_input_filters"] is not None or clause["target_output_filter"] is not None for clause in query["relations"]):
                    verification["dependencies"] = "issues" if relations["issues"] else "verified"
                    verification["issues"].extend(relations["issues"])
                result["matches"].append({"kind": document["kind"], "id": document["id"], "reference": document["reference"], "title": document["title"], "status": document["status"], "score": snapshot["ranks"].get(document["document_key"]) if query["text"] is not None else None, "sections": excerpts, "output_matches": outputs, "relation_matches": relations["links"], "verification": verification})
            result["total"] += 1
        except (ValueError, OSError, KeyError, TypeError) as error:
            result["excluded"].append({"kind": indexed["kind"], "id": indexed["id"], "reference": indexed["reference"], "code": "stale", "message": str(error)})
    result["excluded"].extend(issue for issue in snapshot["issues"] if issue.get("kind") in query["kinds"])
    for head in snapshot["heads"]:
        if head["kind"] in query["kinds"] and head["state"] == "rejected":
            result["excluded"].append({"kind": head["kind"], "id": head["id"], "reference": head["reference"], "code": "rejected_current", "message": "Latest observed publication is not indexed"})
    validate_retrieval_document(result, "result")
    return result


def get_knowledge(paths: ProjectPaths, kind: str, reference: JSONObject, *, table: str | None = None, offset: int = 0, limit: int = 100, dependencies: bool = False) -> JSONObject:
    """Read an exact source and optionally page one saved scientific table.

    Args:
        paths: Project layout.
        kind: Unified source kind.
        reference: Pinned exact source reference.
        table: Null for manifests, or canonical output table name.
        offset: Nonnegative row offset.
        limit: Row page size 1..1000.
        dependencies: Check all declared dependencies if true.
    Returns:
        Document with row-free table manifests, verification and optional paged table.
    Raises:
        ValueError: Invalid arguments or unavailable table/primary source.
        OSError: Unsafe or inaccessible evidence.
    """
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 1000 or table is not None and (kind != "run" or not isinstance(table, str)):
        raise ValueError("Invalid knowledge read arguments")
    result = read_retrieval_document(paths, kind, reference, dependencies=dependencies)
    selected = None
    output = result["document"]["outputs"]
    if table is not None:
        item = next((item for item in output["tables"] if item["name"] == table), None)
        if item is None:
            raise ValueError("unavailable: unknown output table")
        precision = deepcopy(item["precision"])
        if isinstance(precision, dict) and "original_rows" in precision:
            precision["original_rows"] = precision["original_rows"][offset:offset + limit]
        selected = {"name": table, "total": len(item["rows"]), "offset": offset, "limit": limit, "columns": item["columns"], "rows": item["rows"][offset:offset + limit], "availability": item["availability"], "precision": precision, "notes": item["notes"]}
    if output is not None:
        for item in output["tables"]:
            item["rows"] = []
            if isinstance(item["precision"], dict):
                item["precision"].pop("original_rows", None)
    return {"schema_version": 1, **result, "table": selected}


def read_knowledge_links(paths: ProjectPaths, kind: str, reference: JSONObject, *, direction: str = "outgoing", offset: int = 0, limit: int = 100) -> JSONObject:
    """Read direct source links or indexed verified incoming origins without recursion.

    Args:
        paths: Project layout.
        kind: Unified entity kind.
        reference: Exact source reference.
        direction: outgoing or incoming.
        offset: Nonnegative link offset.
        limit: Page size 1..1000.
    Returns:
        Closed link result; incoming completeness is limited to catalog membership.
    Raises:
        ValueError: Invalid arguments/reference or source integrity.
        OSError: Unsafe/unavailable primary evidence/catalog.
    """
    if direction not in ("incoming", "outgoing") or type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("Invalid link read arguments")
    document = project_retrieval_document(paths, kind, reference)
    snapshot = _read_retrieval_snapshot(paths, EMPTY_QUERY, None)
    if direction == "outgoing":
        links, issues = document["links"], []
    else:
        clauses = [{"relation": relation, "direction": "incoming", "reference": None, "target_input_filters": None, "target_output_filter": None} for relation in ("case_member", "analysis_input")]
        checked = _match_retrieval_relations(paths, document, clauses, snapshot)
        links, issues = checked["links"], checked["issues"]
    result = {"schema_version": 1, "generation": snapshot["generation"], "total": len(links), "offset": offset, "limit": limit, "links": links[offset:offset + limit], "issues": issues}
    validate_retrieval_document(result, "links")
    return result
