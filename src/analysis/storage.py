"""Accept validated tool requests; persist immutable methods, jobs and audit history.

Construction and reads do not create stores. SQLite serializes idempotency,
version allocation, generation pointers and budget decisions across processes.
Filesystem publication precedes metadata; identical registration reconciles an
already published bundle after interrupted metadata commit.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import shutil
import sqlite3
import io
import uuid

from analysis import artifacts
from analysis.config import Settings
from analysis.contracts import READ_TOOLS, SCHEMAS
from analysis.environment import compatible, inspect
from analysis.records import AnalysisError, NAME, bounded, check_schema, checksum, digest, encode, loads, safe_path, validate, write_json


def now() -> str:
    """Accept no inputs; return a UTC ISO timestamp."""
    return datetime.now(timezone.utc).isoformat()


class Store:
    """Construct from settings; expose durable postprocessing operations without automatic work."""

    def __init__(self, settings: Settings) -> None:
        """Accept immutable settings; expose owner database/runtime; perform no writes."""
        self.settings = settings
        self.runtime = settings.runtime_directory
        self.path = self.runtime / "postprocessing.sqlite"

    @contextmanager
    def connection(self, write: bool = False):
        """Accept write flag; yield one transactional connection or absent read; commit writes."""
        safe_path(self.runtime, "postprocessing.sqlite")
        if not write and not self.path.exists():
            yield None
            return
        if write:
            self.runtime.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path if write else self.path.as_uri() + "?mode=ro", uri=not write, timeout=15)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version == 0 and write:
                statements = (
                    "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
                    "CREATE TABLE methods (id TEXT NOT NULL, version INTEGER NOT NULL, bundle_sha256 TEXT NOT NULL, record TEXT NOT NULL, PRIMARY KEY(id,version))",
                    "CREATE TABLE history (sequence INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL, method_id TEXT NOT NULL, version INTEGER NOT NULL, kind TEXT NOT NULL, record TEXT NOT NULL)",
                    "CREATE TABLE retries (key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL)",
                    "CREATE TABLE jobs (id TEXT PRIMARY KEY, kind TEXT NOT NULL, state TEXT NOT NULL, fingerprint TEXT NOT NULL, method_id TEXT NOT NULL, version INTEGER NOT NULL, record TEXT NOT NULL)",
                    "CREATE TABLE generations (fingerprint TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id))",
                    "CREATE TABLE attempts (id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), ordinal INTEGER NOT NULL, record TEXT NOT NULL, UNIQUE(job_id,ordinal))",
                    "CREATE INDEX jobs_listing ON jobs(kind,state,method_id,version,id)",
                )
                for statement in statements:
                    connection.execute(statement)
                connection.execute("INSERT INTO metadata VALUES('store_uuid',?)", (uuid.uuid4().hex,))
                connection.execute("PRAGMA user_version=1")
            elif version != 1:
                raise AnalysisError("unsupported_schema", "Unsupported postprocessing database")
            yield connection
            if write:
                connection.commit()
        finally:
            connection.close()

    def perform(self, name: str, arguments: dict) -> dict:
        """Accept tool/arguments; validate, execute atomic operation and return a bounded result."""
        if name not in SCHEMAS:
            raise AnalysisError("unknown_tool", "Unknown Analysis tool")
        bounded(arguments)
        if len(encode(arguments).encode("utf-8")) > 16 * 1024 * 1024:
            raise AnalysisError("limit_exceeded", "Request exceeds 16 MiB")
        validate(arguments, SCHEMAS[name])
        if name in READ_TOOLS:
            with self.connection() as connection:
                return {"schema_version": 1, **self._read(connection, name, arguments)}
        if name == "recover_analyses":
            from analysis.runner import recover
            return {"schema_version": 1, **recover(self, arguments)}
        with self.connection(True) as connection:
            request_hash = digest({"tool": name, "arguments": arguments})
            replay = connection.execute("SELECT * FROM retries WHERE key=?", (arguments["idempotency_key"],)).fetchone()
            if replay:
                if replay["fingerprint"] != request_hash:
                    raise AnalysisError("idempotency_conflict", "Key already identifies another request")
                result = loads(replay["result"])
            else:
                if name == "register_method":
                    result = self._register(connection, arguments)
                elif name == "record_method_assessment":
                    result = self._assess(connection, arguments)
                elif name in ("submit_analysis", "verify_method"):
                    result = self._submit(connection, name, arguments)
                else:
                    result = self._control(connection, name, arguments)
                connection.execute("INSERT INTO retries VALUES(?,?,?)", (arguments["idempotency_key"], request_hash, encode(result)))
            if "analysis_id" in result:
                result = {**result, **self._status(connection, result["analysis_id"])}
            return {"schema_version": 1, **result}

    def _method(self, connection, reference: dict, verify: bool = True) -> dict:
        """Accept transaction/exact method reference/integrity flag; return immutable record."""
        row = connection.execute("SELECT * FROM methods WHERE id=? AND version=?", (reference["method_id"], reference["version"])).fetchone() if connection else None
        if not row:
            raise AnalysisError("not_found", "Unknown exact method version")
        if row["bundle_sha256"] != reference["bundle_sha256"]:
            raise AnalysisError("integrity_error", "Method checksum does not match registration")
        record = loads(row["record"])
        if verify:
            manifest = artifacts.verify(self.method_path(reference), reference["bundle_sha256"], 2097152)
            if manifest.get("method_id") != reference["method_id"] or manifest.get("version") != reference["version"]:
                raise AnalysisError("integrity_error", "Bundle identity mismatch")
            if loads((self.method_path(reference) / "specification.json").read_text(encoding="utf-8")) != record["specification"]:
                raise AnalysisError("integrity_error", "Method specification differs from sealed bundle")
        return record

    def components(self, connection, specification: dict) -> list[dict]:
        """Accept transaction/specification; verify bounded transitive pinned components; return exact references."""
        pending, result, seen = list(specification["components"]), [], set()
        while pending:
            reference = pending.pop(0)
            key = (reference["method_id"], reference["version"], reference["bundle_sha256"])
            if key in seen:
                continue
            seen.add(key)
            if len(seen) > 32:
                raise AnalysisError("limit_exceeded", "A composition supports at most 32 transitive component versions")
            method = self._method(connection, reference)
            result.append(reference)
            pending.extend(method["specification"]["components"])
        return result

    def method_path(self, reference: dict) -> Path:
        """Accept validated exact method identity; return its configured contained bundle directory."""
        return safe_path(self.runtime, f"methods/{reference['method_id']}/{reference['version']}")

    def _register(self, connection, arguments: dict) -> dict:
        """Accept transaction/registration fields; publish immutable bundle and append history."""
        method_id, version, spec = arguments["method_id"], arguments["version"], arguments["specification"]
        maximum = connection.execute("SELECT COALESCE(MAX(version),0) FROM methods WHERE id=?", (method_id,)).fetchone()[0]
        if version != maximum + 1:
            raise AnalysisError("version_conflict", "Versions advance independently without overwrite", {"next_version": maximum + 1})
        for field in ("input_schema", "parameter_schema", "output_schema"):
            check_schema(spec[field])
        self.components(connection, spec)
        if spec["reproducible"] and not spec["external_state"]:
            raise AnalysisError("invalid_input", "Reproducible methods must explicitly pin seeds/external state or declare none")
        files = arguments["files"]
        if "method.py" not in files or not any(name.startswith("tests/test") and name.endswith(".py") for name in files):
            raise AnalysisError("invalid_input", "Bundle requires method.py and bundled unittest tests")
        if "specification.json" in files or "manifest.json" in files or len({key.casefold() for key in files}) != len(files):
            raise AnalysisError("invalid_input", "Reserved or case-colliding bundle paths")
        if sum(len(value.encode("utf-8")) for value in files.values()) + len(encode(spec).encode("utf-8")) > 2097152:
            raise AnalysisError("limit_exceeded", "Method bundle exceeds 2 MiB")
        parent = safe_path(self.runtime, f"methods/{method_id}")
        parent.mkdir(parents=True, exist_ok=True)
        temporary = parent / ("unpublished-" + uuid.uuid4().hex)
        temporary.mkdir()
        try:
            for name, text in files.items():
                path = safe_path(temporary, name)
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("x", encoding="utf-8", newline="\n") as stream:
                    stream.write(text)
            write_json(temporary / "specification.json", spec)
            artifacts.seal(temporary, {"owner": "analysis-method", "method_id": method_id, "version": version}, 2097152)
            reference = {"method_id": method_id, "version": version, "bundle_sha256": checksum(temporary / "manifest.json")}
            destination = self.method_path(reference)
            if destination.exists():
                artifacts.verify(destination, reference["bundle_sha256"], 2097152)
            else:
                artifacts.publish(temporary, destination)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        record = {**reference, "schema_version": 1, "specification": spec, "created_at": now(),
                  "actor": arguments["actor"], "reason": arguments["reason"]}
        connection.execute("INSERT INTO methods VALUES(?,?,?,?)", (method_id, version, reference["bundle_sha256"], encode(record)))
        self.event(connection, reference, "registration", record)
        return {"method": reference, "verification": "unverified", "assessment": "experimental"}

    def event(self, connection, reference: dict, kind: str, data: dict) -> dict:
        """Accept transaction/method/kind/event data; append a UUID event and return it."""
        record = {"event_id": uuid.uuid4().hex, "kind": kind, "created_at": now(), **data}
        connection.execute("INSERT INTO history(id,method_id,version,kind,record) VALUES(?,?,?,?,?)",
                           (record["event_id"], reference["method_id"], reference["version"], kind, encode(record)))
        return record

    def history(self, connection, reference: dict, kind: str | None = None) -> list[dict]:
        """Accept transaction/method/optional kind; return ordered authoritative event records."""
        if not connection:
            return []
        sql = "SELECT record FROM history WHERE method_id=? AND version=?"
        values = [reference["method_id"], reference["version"]]
        if kind:
            sql += " AND kind=?"
            values.append(kind)
        return [loads(row[0]) for row in connection.execute(sql + " ORDER BY sequence", values)]

    def eligibility(self, connection, reference: dict, source_ids: list[str]) -> dict:
        """Accept method/source scope; return current applicable assessments without upgrading history."""
        assessments = self.history(connection, reference, "assessment")
        current = {}
        for record in assessments:
            current[encode(record["scope"])] = record
        applicable = [record for record in current.values() if not record["scope"]["source_ids"] or set(record["scope"]["source_ids"]) & set(source_ids)]
        status = "experimental"
        for candidate in ("approved", "deprecated", "invalidated"):
            if any(record["assessment"] == candidate and (candidate != "approved" or not record["scope"]["source_ids"] or set(source_ids) <= set(record["scope"]["source_ids"])) for record in applicable):
                status = candidate
        return {"status": status, "revision": len(assessments), "assessment_ids": [r["event_id"] for r in applicable],
                "warnings": [] if status == "approved" else [f"Method is {status}; CHECKED does not establish scientific acceptance."]}

    def _assess(self, connection, arguments: dict) -> dict:
        """Accept transaction/assessment request; enforce evidence/revision and append immutable record."""
        reference = arguments["method"]
        self._method(connection, reference)
        prior = self.history(connection, reference, "assessment")
        if len(prior) != arguments["expected_revision"]:
            raise AnalysisError("revision_conflict", "Assessment revision changed")
        if not arguments["evidence"]:
            raise AnalysisError("invalid_input", "Assessment requires supporting evidence")
        if "replacement" in arguments:
            self._method(connection, arguments["replacement"])
        if arguments["assessment"] == "approved":
            approval = arguments.get("human_approval")
            if (not approval or approval["method"] != reference or approval["scope"] != arguments["scope"]
                    or approval["evidence_id"] not in arguments["evidence"]):
                raise AnalysisError("approval_required", "Exact-version scoped human approval evidence is required")
        event = self.event(connection, reference, "assessment", {key: value for key, value in arguments.items() if key != "idempotency_key"})
        return {"assessment_id": event["event_id"], "revision": len(prior) + 1, "assessment": arguments["assessment"]}

    def _submit(self, connection, name: str, arguments: dict) -> dict:
        """Accept transaction/submission; pin inputs/environment, deduplicate, persist queued work."""
        reference = arguments["method"]
        method = self._method(connection, reference)
        components = self.components(connection, method["specification"])
        environment = inspect(self.settings.python)
        limits = self.settings.effective(arguments["limits"])
        kind = "verification" if name == "verify_method" else "analysis"
        if kind == "analysis":
            compatible(method["specification"], environment)
        inputs, identities, sources = arguments.get("inputs", {}), {}, []
        total_bytes, total_rows = 0, 0
        for key, source in inputs.items():
            validate(key, NAME, "input name")
            directory, manifest = artifacts.resolve(source, self.settings.stores, limits["input_bytes"])
            total_bytes += sum(record["size_bytes"] for record in manifest["files"]) + (directory / "manifest.json").stat().st_size
            total_rows += sum(dataset["row_count"] for dataset in manifest.get("datasets", []))
            if any(len(dataset["columns"]) > limits["columns"] for dataset in manifest.get("datasets", [])):
                raise AnalysisError("limit_exceeded", "Input column limit exceeded")
            identities[key] = {k: v for k, v in manifest.items() if k not in ("export_id", "actor", "reason", "warnings")}
            sources.extend(manifest.get("source_ids", []))
            sources.extend(manifest.get(key) for key in ("run_id", "analysis_id") if manifest.get(key))
        sources = sorted(set(item for item in sources if item))
        if total_bytes > limits["input_bytes"] or total_rows > limits["input_rows"] or len(inputs) > limits["candidates"] + 1:
            raise AnalysisError("limit_exceeded", "Aggregate input limits exceeded")
        eligibility = self.eligibility(connection, reference, sources)
        for component in components:
            if self.eligibility(connection, component, sources)["status"] == "invalidated":
                raise AnalysisError("method_invalidated", "Pinned component is invalidated in this source scope")
        verification = None
        if kind == "analysis":
            validate(inputs, method["specification"]["input_schema"], "inputs")
            validate(arguments["parameters"], method["specification"]["parameter_schema"], "parameters")
            if eligibility["status"] == "invalidated":
                raise AnalysisError("method_invalidated", "Method invalidated in this source scope")
            verified = [event for event in self.history(connection, reference, "verification") if event["environment_fingerprint"] == environment["fingerprint"]]
            if not verified or verified[-1]["state"] != "CHECKED":
                raise AnalysisError("verification_required", "Passing verification in the exact environment is required")
            verification = verified[-1]["analysis_id"]
            self.checked_output(connection, self.job(connection, verification), verified[-1]["attempt_id"])
            for component in components:
                checks = [event for event in self.history(connection, component, "verification") if event["environment_fingerprint"] == environment["fingerprint"]]
                if not checks or checks[-1]["state"] != "CHECKED":
                    raise AnalysisError("verification_required", "Pinned component requires passing exact-environment verification")
                self.checked_output(connection, self.job(connection, checks[-1]["analysis_id"]), checks[-1]["attempt_id"])
        identity = digest({"kind": kind, "method": reference, "inputs": identities,
                           "parameters": arguments.get("parameters", {}), "environment": environment["fingerprint"]})
        reproducible = method["specification"]["reproducible"]
        existing = connection.execute("SELECT job_id FROM generations WHERE fingerprint=?", (identity,)).fetchone() if kind == "analysis" and reproducible else None
        if "recompute_of" in arguments:
            previous = self.job(connection, arguments["recompute_of"])
            if previous["fingerprint"] != identity:
                raise AnalysisError("invalid_input", "Recompute must pin the same numerical identity")
            if reproducible and (not existing or existing[0] != previous["id"]):
                raise AnalysisError("generation_conflict", "Recompute target is no longer the current generation")
        elif existing:
            job = self.job(connection, existing[0])
            if job["state"] == "CHECKED":
                self.checked_output(connection, job)
            self.event(connection, reference, "submission", {"analysis_id": job["id"], "shared": True,
                       "actor": arguments["actor"], "reason": arguments["reason"],
                       "submission_key": arguments["idempotency_key"], "requested_limits": arguments["limits"]})
            return {"analysis_id": job["id"], "shared": True, "reused": job["state"] == "CHECKED"}
        job_id = uuid.uuid4().hex
        job = {"schema_version": 1, "id": job_id, "kind": kind, "state": "QUEUED", "fingerprint": identity,
               "method": reference, "request": arguments, "limits": limits, "environment": environment,
               "source_ids": sources, "verification_id": verification, "original_assessment": eligibility,
               "created_at": now(), "updated_at": now(), "consumed_seconds": 0.0, "attempt_count": 0,
               "cancel_requested": False, "current_attempt": None, "error": None,
               "recompute_of": arguments.get("recompute_of")}
        connection.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?)", (job_id, kind, "QUEUED", identity, reference["method_id"], reference["version"], encode(job)))
        if kind == "analysis" and reproducible:
            connection.execute("INSERT INTO generations VALUES(?,?) ON CONFLICT(fingerprint) DO UPDATE SET job_id=excluded.job_id", (identity, job_id))
        self.event(connection, reference, "submission" if kind == "analysis" else "verification_requested",
                   {"analysis_id": job_id, "shared": False, "actor": arguments["actor"], "reason": arguments["reason"],
                    "submission_key": arguments["idempotency_key"], "requested_limits": arguments["limits"]})
        return {"analysis_id": job_id, **({"verification_id": job_id} if kind == "verification" else {}), "shared": False, "reused": False}

    def job(self, connection, job_id: str) -> dict:
        """Accept transaction/job ID; return durable job record or raise not_found."""
        row = connection.execute("SELECT record FROM jobs WHERE id=?", (job_id,)).fetchone() if connection else None
        if not row:
            raise AnalysisError("not_found", "Unknown analysis/verification ID")
        return loads(row[0])

    def save_job(self, connection, job: dict) -> None:
        """Accept transaction/updated job; persist state and record together; return nothing."""
        job["updated_at"] = now()
        connection.execute("UPDATE jobs SET state=?,record=? WHERE id=?", (job["state"], encode(job), job["id"]))

    def _control(self, connection, name: str, arguments: dict) -> dict:
        """Accept transaction/control request; request cancel or queue explicit bounded retry."""
        job = self.job(connection, arguments["analysis_id"])
        if name == "cancel_analysis":
            if job["state"] in ("QUEUED", "PREPARING", "RUNNING", "VERIFYING"):
                job["cancel_requested"] = True
                if job["state"] == "QUEUED":
                    job["state"] = "CANCELLED"
        else:
            if job["state"] not in ("FAILED", "TIMED_OUT", "CANCELLED"):
                raise AnalysisError("invalid_state", "Retry requires a confirmed terminal failure")
            if job["attempt_count"] >= job["limits"]["max_attempts"] or job["consumed_seconds"] >= job["limits"]["total_seconds"]:
                raise AnalysisError("budget_exhausted", "Original retry/time budget exhausted")
            job.update(state="QUEUED", cancel_requested=False, error=None)
        self.save_job(connection, job)
        self.event(connection, job["method"], name, {"analysis_id": job["id"], "actor": arguments["actor"], "reason": arguments["reason"]})
        return {"analysis_id": job["id"]}

    def attempts(self, connection, job_id: str) -> list[dict]:
        """Accept transaction/job ID; return immutable ordinal-ordered attempt snapshots."""
        return [loads(row[0]) for row in connection.execute("SELECT record FROM attempts WHERE job_id=? ORDER BY ordinal", (job_id,))]

    def checked_output(self, connection, job: dict, attempt_id: str | None = None) -> tuple[Path, dict, dict]:
        """Accept transaction/job/optional attempt ID; verify sealed output and return path/manifest/attempt."""
        attempts = self.attempts(connection, job["id"])
        attempt = next((record for record in attempts if record["id"] == (attempt_id or job["current_attempt"])), None)
        if not attempt or attempt["state"] != "CHECKED" or not attempt.get("output_sha256"):
            raise AnalysisError("output_unavailable", "Requested attempt has no CHECKED output")
        directory = safe_path(self.runtime, f"analyses/{job['id']}/attempts/{attempt['id']}/output")
        manifest = artifacts.verify(directory, attempt["output_sha256"], job["limits"]["output_bytes"])
        if manifest.get("analysis_id") != job["id"] or manifest.get("attempt_id") != attempt["id"] or manifest.get("state") != "CHECKED":
            raise AnalysisError("integrity_error", "Output identity mismatch")
        return directory, manifest, attempt

    def _status(self, connection, job_id: str, limit: int = 20, cursor: str | None = None) -> dict:
        """Accept transaction/job/pagination; return bounded status/current validity/output reference."""
        job = self.job(connection, job_id)
        attempts = self.attempts(connection, job_id)
        start = 0
        if cursor:
            found = [index for index, item in enumerate(attempts) if item["id"] == cursor]
            if not found:
                raise AnalysisError("invalid_cursor", "Attempt cursor is not in this job")
            start = found[0] + 1
        page = attempts[start:start + limit]
        eligibility = self.eligibility(connection, job["method"], job["source_ids"])
        output, integrity_error = None, None
        if job["state"] == "CHECKED":
            try:
                _, manifest, attempt = self.checked_output(connection, job)
                output = {"schema_version": 1, "owner": "analysis", "analysis_id": job_id,
                          "attempt_id": attempt["id"], "manifest_sha256": attempt["output_sha256"]}
            except (ValueError, OSError) as exc:
                integrity_error = str(exc)
        return {"schema_version": 1, "analysis_id": job_id, "state": job["state"], "kind": job["kind"],
                "method": job["method"], "inputs": job["request"].get("inputs", {}), "parameters": job["request"].get("parameters", {}),
                "limits": job["limits"], "created_at": job["created_at"], "updated_at": job["updated_at"],
                "current_attempt": job["current_attempt"], "attempts": page,
                "cancel_requested": job["cancel_requested"],
                "next_attempt_cursor": page[-1]["id"] if page and start + len(page) < len(attempts) else None,
                "consumed_seconds": job["consumed_seconds"], "remaining_seconds": max(0, job["limits"]["total_seconds"] - job["consumed_seconds"]),
                "remaining_attempts": max(0, job["limits"]["max_attempts"] - job["attempt_count"]),
                "verification_id": job["verification_id"], "original_assessment": job["original_assessment"],
                "environment": job["environment"],
                "verification_outcome": {"state": "CHECKED" if job["kind"] == "analysis" else job["state"],
                                         "verification_id": job["verification_id"] or job["id"],
                                         "environment_fingerprint": job["environment"]["fingerprint"]},
                "current_validity": eligibility, "output": output, "integrity_error": integrity_error,
                "error": job["error"], "recompute_of": job["recompute_of"], "warnings": eligibility["warnings"],
                "scientific_limitations": ["Software CHECKED only; scientific acceptance is deferred."]}

    def _page(self, records: list[dict], arguments: dict, identity: dict) -> tuple[list[dict], str | None]:
        """Accept ordered records/page/filter identity; return key-anchored page and filter-bound cursor."""
        token = arguments.get("cursor")
        expected = digest(identity)
        history = "method" in identity
        def key(record: dict) -> list:
            """Accept a summary/event; return its immutable order identity."""
            if history:
                return [record["event_id"]]
            if "method_id" in record:
                return [record["method_id"], record["version"]]
            return [record["analysis_id"]]
        if token:
            cursor = loads(token)
            if (not isinstance(cursor, dict) or set(cursor) != {"after", "filters"} or cursor["filters"] != expected
                    or not isinstance(cursor["after"], list) or len(cursor["after"]) not in (1, 2)
                    or not isinstance(cursor["after"][0], str)
                    or (len(cursor["after"]) == 2 and type(cursor["after"][1]) is not int)):
                raise AnalysisError("invalid_cursor", "Cursor/filter mismatch")
            if history:
                matches = [index for index, record in enumerate(records) if key(record) == cursor["after"]]
                if not matches:
                    raise AnalysisError("invalid_cursor", "Unknown history anchor")
                records = records[matches[0] + 1:]
            else:
                records = [record for record in records if key(record) > cursor["after"]]
        limit = arguments.get("limit", 20)
        page = records[:limit]
        return page, encode({"after": key(page[-1]), "filters": expected}) if len(page) < len(records) else None

    def _read(self, connection, name: str, arguments: dict) -> dict:
        """Accept snapshot/tool/read arguments; return verified bounded data without mutations."""
        if name == "get_analysis":
            return self._status(connection, arguments["analysis_id"], arguments.get("attempt_limit", 20), arguments.get("attempt_cursor"))
        if name == "get_analysis_output":
            return self._output(connection, arguments)
        if name == "list_analyses":
            records = []
            for row in connection.execute("SELECT record FROM jobs ORDER BY id") if connection else []:
                job = loads(row[0])
                values = {"kind": job["kind"], "state": job["state"], "method_id": job["method"]["method_id"], "version": job["method"]["version"]}
                if any(values[key] != arguments[key] for key in values if key in arguments) or ("source_id" in arguments and arguments["source_id"] not in job["source_ids"]):
                    continue
                records.append({"analysis_id": job["id"], "kind": job["kind"], "state": job["state"], "method": job["method"], "created_at": job["created_at"]})
            page, cursor = self._page(records, arguments, {"tool": name, **{k: v for k, v in arguments.items() if k not in ("cursor", "limit")}})
            return {"analyses": page, "next_cursor": cursor}
        if name == "get_method_history":
            self._method(connection, arguments["method"])
            page, cursor = self._page(self.history(connection, arguments["method"]), arguments, {"method": arguments["method"]})
            return {"history": page, "next_cursor": cursor}
        if name == "get_method":
            method = self._method(connection, arguments)
            return self._method_view(connection, method)
        records = []
        for row in connection.execute("SELECT record FROM methods ORDER BY id,version") if connection else []:
            method = loads(row[0])
            if "method_id" in arguments and arguments["method_id"] != method["method_id"]:
                continue
            view = self._method_view(connection, method)
            view["current_validity"] = self.eligibility(connection, method, arguments.get("source_ids", []))
            if "applicability" in arguments and not any(arguments["applicability"].casefold() in item.casefold() for item in method["specification"]["applicability"]):
                continue
            if "assessment" in arguments and arguments["assessment"] != view["current_validity"]["status"]:
                continue
            if "verified" in arguments and arguments["verified"] != bool(view["verifications"] and view["verifications"][-1]["state"] == "CHECKED"):
                continue
            records.append({key: value for key, value in view.items() if key != "specification"})
        page, cursor = self._page(records, arguments, {"tool": name, **{k: v for k, v in arguments.items() if k not in ("cursor", "limit")}})
        return {"methods": page, "next_cursor": cursor}

    def _method_view(self, connection, method: dict) -> dict:
        """Accept transaction/method; return bounded verification summaries and distinct version pointers."""
        verifications = self.history(connection, method, "verification")
        versions = [loads(row[0]) for row in connection.execute("SELECT record FROM methods WHERE id=? ORDER BY version", (method["method_id"],))]
        approved = [record["version"] for record in versions if self.eligibility(connection, record, [])["status"] == "approved"]
        return {**method, "verifications": verifications[-20:], "current_validity": self.eligibility(connection, method, []),
                "assessment_history": self.history(connection, method, "assessment")[-20:],
                "latest_registered_version": versions[-1]["version"], "latest_approved_version": max(approved) if approved else None}

    def _output(self, connection, arguments: dict) -> dict:
        """Accept pinned job/attempt/output query; return bounded table/figure/report data after verification."""
        job = self.job(connection, arguments["analysis_id"])
        if arguments["name"] == "diagnostics":
            attempt = next((item for item in self.attempts(connection, job["id"]) if item["id"] == arguments["attempt_id"]), None)
            if not attempt or not attempt.get("archive_sha256") or attempt["archive_sha256"] != arguments["output_sha256"]:
                raise AnalysisError("output_unavailable", "Diagnostics require an exact sealed attempt archive")
            directory = safe_path(self.runtime, f"analyses/{job['id']}/attempts/{attempt['id']}")
            artifacts.verify(directory, attempt["archive_sha256"], artifacts.archive_limit(job["limits"]))
            offset, limit = arguments.get("offset", 0), arguments.get("limit", 100)
            import itertools
            logs = {}
            for name in ("stdout.log", "stderr.log"):
                path = directory / name
                with path.open(encoding="utf-8", errors="replace") if path.exists() else io.StringIO("") as stream:
                    logs[name] = [line[:4096] for line in itertools.islice(stream, offset, offset + limit)]
            return {"analysis_id": job["id"], "attempt_id": attempt["id"], "state": attempt["state"],
                    "diagnostic_only": True, "error": attempt["error"], "logs": logs, "offset": offset,
                    "verification_summary": attempt.get("verification_summary"),
                    "next_offset": offset + limit if any(len(value) == limit for value in logs.values()) else None,
                    "current_validity": self.eligibility(connection, job["method"], job["source_ids"])}
        directory, manifest, attempt = self.checked_output(connection, job, arguments["attempt_id"])
        if arguments["output_sha256"] != attempt["output_sha256"]:
            raise AnalysisError("integrity_error", "Output cursor identity changed")
        result = loads((directory / "result.json").read_text(encoding="utf-8"))
        name, offset, limit = arguments["name"], arguments.get("offset", 0), arguments.get("limit", 100)
        response = {"analysis_id": job["id"], "attempt_id": attempt["id"], "output_sha256": attempt["output_sha256"],
                    "current_validity": self.eligibility(connection, job["method"], job["source_ids"]),
                    "verification_id": job["verification_id"], "limitations": result.get("limitations", [])}
        if name == "summary":
            return {**response, "summary": result["summary"]}
        if name == "figures":
            files = {item["path"]: item for item in manifest["files"]}
            return {**response, "figures": [{**figure, **files[figure["path"]], "relative_path": (directory / figure["path"]).relative_to(self.runtime).as_posix()} for figure in result.get("figures", [])]}
        table = next((table for table in result.get("tables", []) if table["name"] == name), None)
        if not table:
            raise AnalysisError("not_found", "Unknown output table")
        import itertools
        with safe_path(directory, table["path"]).open(encoding="utf-8") as stream:
            page = [dict(zip((column["name"] for column in table["columns"]), loads(line))) for line in itertools.islice(stream, offset, offset + limit)]
        return {**response, "table": table, "rows": page, "offset": offset,
                "next_offset": offset + len(page) if offset + len(page) < table["row_count"] else None}
