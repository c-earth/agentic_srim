"""Persist independent cases beneath a supplied runtime and expose validated tools.

SQLite snapshots, audit history and idempotency responses commit atomically to
cases.sqlite. Reads do not create a store. Retention is indefinite in version 1;
archiving hides a case by default and never deletes revisions or producer data.
"""

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PureWindowsPath
import sqlite3
import uuid

from case.contracts import READ_TOOLS, SCHEMAS
from case.records import CaseError, empty_content, encode, fingerprint, validate, validate_record


class Store:
    """Construct from a runtime path; provide validated independent case operations.

Construction performs no writes. Mutations serialize through SQLite transactions;
multiple server processes may share one runtime. Actor/source fields are recorded
claims, not authenticated identities. No simulator modules are imported.
"""

    def __init__(self, runtime: Path, stores: dict | None = None) -> None:
        """Accept the shared runtime root; expose its case-only database path."""
        self.runtime = Path(runtime).resolve()
        self.path = self.runtime / "cases.sqlite"
        self.stores = stores if stores is not None else {"local": self.runtime}

    @contextmanager
    def connection(self, write: bool = False):
        """Accept a write flag; yield a snapshot transaction or None for absent reads.

Writes create schema version 1 under a reserved lock. Exceptions roll back schema,
record, history and retry changes together. Read-only mode never initializes files.
"""
        if not self.path.resolve().is_relative_to(self.runtime):
            raise CaseError("unsafe_store", "Case database must remain within its runtime")
        if not write and not self.path.exists():
            yield None
            return
        if write:
            self.runtime.mkdir(parents=True, exist_ok=True)
        uri = self.path.as_uri() + "?mode=ro"
        connection = sqlite3.connect(self.path if write else uri, uri=not write, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version == 0 and write:
                statements = (
                    "CREATE TABLE cases (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL, archived INTEGER NOT NULL, updated_at TEXT NOT NULL, search_text TEXT NOT NULL)",
                    "CREATE TABLE revisions (case_id TEXT NOT NULL REFERENCES cases(id), revision INTEGER NOT NULL, record_json TEXT NOT NULL, sha256 TEXT NOT NULL, actor TEXT NOT NULL, reason TEXT NOT NULL, operation TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(case_id, revision))",
                    "CREATE TABLE retries (key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result_json TEXT NOT NULL)",
                    "CREATE INDEX case_listing ON cases(archived, status, id)",
                )
                for statement in statements:
                    connection.execute(statement)
                connection.execute("PRAGMA user_version=1")
            elif version == 0 and not write and not connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
                yield None
                return
            elif version != 1:
                raise CaseError("unsupported_schema", f"Unsupported case database schema {version}")
            yield connection
            if write:
                connection.commit()
        finally:
            connection.close()

    def perform(self, name: str, arguments: dict) -> dict:
        """Accept a tool name/arguments; return its result or raise a coded error.

Validation applies to direct Python callers as well as MCP. Retry keys are global
within this store; reuse with a different operation/payload is rejected. A replay
returns its original committed result even after newer case revisions exist.
"""
        if name not in SCHEMAS:
            raise CaseError("unknown_tool", "Unknown Case MCP tool")
        validate(arguments, SCHEMAS[name])
        if name in READ_TOOLS:
            with self.connection() as connection:
                if name in ("list_cases", "search_cases"):
                    return self._list(connection, arguments)
                if name == "get_case_history":
                    return self._history(connection, arguments)
                record = self._get(connection, arguments["case_id"], arguments.get("revision"))
                checks = self.check_artifacts(record)
                if name == "get_case_overview":
                    from case.views import overview
                    return {"case_id": record["id"], "revision": record["revision"],
                            "markdown": overview(record, checks), "artifact_checks": checks}
                return {"case": record, "artifact_checks": checks}
        with self.connection(write=True) as connection:
            request_hash = fingerprint({"tool": name, "arguments": arguments})
            replay = connection.execute("SELECT * FROM retries WHERE key=?", (arguments["idempotency_key"],)).fetchone()
            if replay is not None:
                if replay["fingerprint"] != request_hash:
                    raise CaseError("idempotency_conflict", "Retry key was already used with a different request")
                return json.loads(replay["result_json"])
            if name in ("create_case", "branch_case"):
                record = self._create(connection, name, arguments)
            else:
                record = self._change(connection, name, arguments)
            validate_record(record)
            self._save(connection, name, arguments, record)
            result = {"case": record}
            connection.execute("INSERT INTO retries VALUES(?,?,?)",
                               (arguments["idempotency_key"], request_hash, encode(result)))
            return result

    def _get(self, connection: sqlite3.Connection | None, case_id: str, revision: int | None = None) -> dict:
        """Accept a snapshot connection/ID/optional revision; return verified content."""
        if connection is None:
            raise CaseError("not_found", f"Case {case_id} does not exist")
        if revision is None:
            row = connection.execute("SELECT r.* FROM revisions r JOIN cases c ON c.id=r.case_id AND c.revision=r.revision WHERE c.id=?", (case_id,)).fetchone()
        else:
            row = connection.execute("SELECT * FROM revisions WHERE case_id=? AND revision=?", (case_id, revision)).fetchone()
        if row is None:
            raise CaseError("not_found", "Case or requested revision does not exist")
        if hashlib.sha256(row["record_json"].encode("utf-8")).hexdigest() != row["sha256"]:
            raise CaseError("integrity_error", "Case snapshot checksum mismatch")
        record = json.loads(row["record_json"])
        record.setdefault("pending_actions", [])
        return record

    def _create(self, connection: sqlite3.Connection, name: str, arguments: dict) -> dict:
        """Accept a transaction/tool/validated arguments; return an unsaved new case."""
        content = empty_content()
        artifacts = []
        parent = None
        if name == "branch_case":
            source = self._get(connection, arguments["parent_case_id"], arguments["parent_revision"])
            parent = {"case_id": source["id"], "revision": source["revision"]}
            for field in ("constraints", "assumptions", "prohibitions", "budgets"):
                content[field] = deepcopy(source[field])
            artifacts = deepcopy(source["artifacts"])
        else:
            content.update(deepcopy(arguments.get("content", {})))
            self._pending([], content["pending_actions"])
        timestamp = datetime.now(timezone.utc).isoformat()
        return {"schema_version": 1, "id": uuid.uuid4().hex, "revision": 1,
                "title": arguments["title"], "original_request": arguments["original_request"],
                "created_at": timestamp, "updated_at": timestamp,
                "created_by": arguments["actor"], "updated_by": arguments["actor"],
                "status": "active", "archived": False, "parent": parent,
                **content, "artifacts": artifacts}

    def _change(self, connection: sqlite3.Connection, name: str, arguments: dict) -> dict:
        """Accept a transaction/tool/arguments; return a validated lifecycle candidate."""
        record = self._get(connection, arguments["case_id"])
        if record["revision"] != arguments["expected_revision"]:
            raise CaseError("revision_conflict", f"Expected revision {arguments['expected_revision']}; current revision is {record['revision']}")
        patch = deepcopy(arguments.get("patch", arguments.get("progress", {})))
        if name == "link_case_artifact" and "pending_actions" in patch:
            raise CaseError("invalid_input", "Artifact progress cannot replace pending actions")
        if record["archived"]:
            if name != "update_case" or patch != {"archived": False}:
                raise CaseError("archived_case", "Unarchive the case in a separate mutation before editing")
        elif record["status"] in ("completed", "cancelled"):
            if name != "update_case" or patch not in ({"status": "active"}, {"archived": True}):
                raise CaseError("closed_case", "Reopen the case in a separate mutation before editing")
        if "archived" in patch and len(patch) != 1:
            raise CaseError("invalid_state", "Archive/unarchive must be a separate mutation")
        if patch and all(record[key] == value for key, value in patch.items()):
            raise CaseError("no_change", "Patch does not change the case")
        if name == "link_case_artifact":
            artifact = deepcopy(arguments["artifact"])
            reference = artifact.get("reference")
            if reference:
                from analysis.artifacts import resolve
                from analysis.records import AnalysisError
                try:
                    resolve(reference, self.stores, 536870912, for_execution=False)
                except (AnalysisError, OSError) as exc:
                    raise CaseError("artifact_unavailable", str(exc)) from exc
                producer_id = reference.get("analysis_id", reference.get("export_id"))
                valid_ids = {producer_id, producer_id + ":" + reference["attempt_id"]} if "analysis_id" in reference else {producer_id}
                if artifact["owner"] != reference["owner"] or artifact["artifact_id"] not in valid_ids:
                    raise CaseError("invalid_input", "Artifact and producer reference identities differ")
            self._artifact_path(artifact)
            if any((item["owner"], item["artifact_id"]) == (artifact["owner"], artifact["artifact_id"])
                   for item in record["artifacts"]):
                raise CaseError("artifact_conflict", "Artifact is already linked; use a new identity for a new version")
            record["artifacts"].append(artifact)
            record.update(patch)
            if "resolve_action_id" in arguments:
                action = next((item for item in record["pending_actions"] if item["action_id"] == arguments["resolve_action_id"]), None)
                if not action or action["state"] != "submitted" or not reference or action["analysis_id"] != reference.get("analysis_id"):
                    raise CaseError("invalid_state", "Resolution requires a submitted action and matching verified output")
                action.update(state="resolved", output=reference)
        else:
            if "pending_actions" in patch:
                self._pending(record["pending_actions"], patch["pending_actions"])
            record.update(patch)
        record["revision"] += 1
        record["updated_at"] = datetime.now(timezone.utc).isoformat()
        record["updated_by"] = arguments["actor"]
        return record

    def _pending(self, previous: list[dict], proposed: list[dict]) -> None:
        """Accept old/new typed actions; preserve intent/IDs and require atomic link resolution; return nothing."""
        old = {item["action_id"]: item for item in previous}
        new = {item["action_id"]: item for item in proposed}
        if len(new) != len(proposed) or old.keys() - new.keys():
            raise CaseError("invalid_state", "Pending actions cannot be duplicated or removed")
        for action_id, item in new.items():
            prior = old.get(action_id)
            if prior is None:
                if item["state"] != "intent" or "analysis_id" in item or "output" in item:
                    raise CaseError("invalid_state", "New actions must checkpoint intent before submission")
            else:
                if any(item.get(key) != prior.get(key) for key in ("submission", "kind", "schema_version")):
                    raise CaseError("invalid_state", "Action submission identity is immutable")
                if prior["state"] == "resolved" and item != prior:
                    raise CaseError("invalid_state", "Resolved action is immutable")
                if prior.get("analysis_id") and item.get("analysis_id") != prior["analysis_id"]:
                    raise CaseError("invalid_state", "Submitted analysis identity is immutable")
                if item["state"] == "resolved" and prior["state"] != "resolved":
                    raise CaseError("invalid_state", "Resolve actions with atomic artifact attachment")
                if item["state"] == "submitted" and not item.get("analysis_id"):
                    raise CaseError("invalid_state", "Submitted actions require a durable analysis ID")
                if prior["state"] == "submitted" and item["state"] == "intent":
                    raise CaseError("invalid_state", "Action cannot return to pre-submission state")

    def _save(self, connection: sqlite3.Connection, operation: str, arguments: dict, record: dict) -> None:
        """Accept a transaction/audit arguments/record; persist its snapshot/index, returning nothing."""
        values = (record["id"], record["revision"], record["title"], record["status"],
                  int(record["archived"]), record["updated_at"], encode(record).casefold())
        connection.execute("INSERT INTO cases VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,title=excluded.title,status=excluded.status,archived=excluded.archived,updated_at=excluded.updated_at,search_text=excluded.search_text", values)
        connection.execute("INSERT INTO revisions VALUES(?,?,?,?,?,?,?,?)",
                           (record["id"], record["revision"], encode(record), fingerprint(record),
                            arguments["actor"], arguments["reason"], operation, record["updated_at"]))

    def _list(self, connection: sqlite3.Connection | None, arguments: dict) -> dict:
        """Accept a snapshot/filter arguments; return bounded summaries and an ID cursor.

Cursor ordering is by immutable case ID, not title or update time. Each call is a
coherent snapshot; pagination does not freeze updates across separate calls.
"""
        if connection is None:
            return {"cases": [], "next_cursor": None}
        clauses, values = [], []
        if not arguments.get("include_archived", False):
            clauses.append("archived=0")
        if arguments.get("status") is not None:
            clauses.append("status=?")
            values.append(arguments["status"])
        if arguments.get("cursor") is not None:
            clauses.append("id>?")
            values.append(arguments["cursor"])
        if "query" in arguments:
            clauses.append("instr(search_text,?)>0")
            values.append(arguments["query"].casefold())
        limit = arguments.get("limit", 20)
        condition = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = connection.execute("SELECT id,revision,title,status,archived,updated_at FROM cases" + condition + " ORDER BY id LIMIT ?", (*values, limit + 1)).fetchall()
        return {"cases": [{**dict(row), "archived": bool(row["archived"])} for row in rows[:limit]],
                "next_cursor": rows[limit - 1]["id"] if len(rows) > limit else None}

    def _history(self, connection: sqlite3.Connection | None, arguments: dict) -> dict:
        """Accept a snapshot/ID/page arguments; return bounded immutable audit metadata."""
        self._get(connection, arguments["case_id"])
        limit = arguments.get("limit", 20)
        rows = connection.execute("SELECT revision,actor,reason,operation,created_at,sha256 FROM revisions WHERE case_id=? AND revision>? ORDER BY revision LIMIT ?",
                                  (arguments["case_id"], arguments.get("after_revision", 0), limit + 1)).fetchall()
        return {"history": [dict(row) for row in rows[:limit]],
                "next_revision": rows[limit - 1]["revision"] if len(rows) > limit else None}

    def _artifact_path(self, artifact: dict) -> Path | None:
        """Accept an artifact link; return a safe runtime-relative locator or None.

Remote/absolute paths, drive-relative paths and traversal are rejected. Resolving
symlinks must stay within the runtime. Missing locators are explicit unresolved
owner references; this service never contacts their producer or reruns work.
"""
        locator = artifact.get("relative_path")
        if locator is None:
            if "sha256" in artifact:
                raise CaseError("invalid_input", "A checksum requires a relative_path locator")
            return None
        windows = PureWindowsPath(locator)
        path = Path(locator)
        if windows.drive or windows.root or path.is_absolute() or ".." in windows.parts or ":" in locator:
            raise CaseError("unsafe_path", "Artifact locator must be a relative path inside runtime")
        resolved = (self.runtime / path).resolve()
        if not resolved.is_relative_to(self.runtime) or resolved == self.runtime:
            raise CaseError("unsafe_path", "Artifact locator escapes its runtime")
        return resolved

    def check_artifacts(self, record: dict) -> list[dict]:
        """Accept a snapshot; return current local availability/checksum observations.

Checks never modify recorded provenance or validation status. File hashes are
streamed; files larger than 64 MiB are explicitly unverified to bound read work.
Owner-qualified references without locators remain unresolved, not falsely valid.
"""
        results = []
        remaining_bytes = 64 * 1024 * 1024
        for artifact in record["artifacts"]:
            result = {"owner": artifact["owner"], "artifact_id": artifact["artifact_id"]}
            try:
                if "reference" in artifact:
                    from analysis.artifacts import resolve
                    _, manifest = resolve(artifact["reference"], self.stores, remaining_bytes, for_execution=False)
                    remaining_bytes -= sum(item["size_bytes"] for item in manifest["files"])
                    result["state"] = "verified"
                    if artifact["reference"].get("kind") == "attempt":
                        result["diagnostic_only"] = True
                    results.append(result)
                    continue
                path = self._artifact_path(artifact)
                if path is None:
                    state = "unresolved"
                elif not path.exists():
                    state = "missing"
                elif not path.is_file() or "sha256" not in artifact:
                    state = "unverified"
                elif path.stat().st_size > remaining_bytes:
                    state = "unverified_size_limit"
                else:
                    digest = hashlib.sha256()
                    with path.open("rb") as stream:
                        while chunk := stream.read(min(1048576, remaining_bytes + 1)):
                            remaining_bytes -= len(chunk)
                            if remaining_bytes < 0:
                                break
                            digest.update(chunk)
                    state = ("unverified_size_limit" if remaining_bytes < 0 else
                             "verified" if digest.hexdigest() == artifact["sha256"] else "checksum_mismatch")
                result["state"] = state
            except (OSError, ValueError, RuntimeError) as exc:
                result.update(state="unavailable", detail=str(exc))
            results.append(result)
        return results
