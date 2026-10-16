"""Accept pinned local producer references; verify manifests and snapshot bytes.

Exports use JSON manifests and JSONL dataset files. Methods and output seals use
the same file inventory. Publication uses an unpublished sibling then rename;
database owners reconcile published immutable files after interrupted commits.
"""

import os
from pathlib import Path
import shutil
import sqlite3
from contextlib import closing

from analysis.records import AnalysisError, HASH, ID, JSON_OBJECT, NAME, SHORT, VERSION, checksum, digest, loads, obj, safe_path, validate, write_json


FILE = obj({"path": SHORT, "sha256": HASH, "size_bytes": {"type": "integer", "minimum": 0}})
COLUMN = obj({"name": SHORT, "unit": {"type": "string", "maxLength": 200},
              "type": {"type": "string", "enum": ["number", "integer", "string", "boolean", "object", "array", "null", "mixed"]},
              "nullable": {"type": "boolean"}})
DATASET = obj({"name": SHORT, "path": SHORT, "columns": {"type": "array", "items": COLUMN, "minItems": 1, "maxItems": 64},
               "normalization": JSON_OBJECT, "row_count": {"type": "integer", "minimum": 0}})
REFERENCE = obj({"schema_version": {"type": "integer", "enum": [1]}, "owner": {"type": "string", "enum": ["srim", "analysis"]},
                 "store_id": NAME, "export_id": ID, "analysis_id": ID, "attempt_id": ID, "manifest_sha256": HASH,
                 "kind": {"type": "string", "enum": ["output", "attempt"]}},
                ["schema_version", "owner", "store_id", "manifest_sha256"])


def archive_limit(limits: dict) -> int:
    """Accept effective limits; return audit ceiling including snapshots, copied bundles and job metadata."""
    return 2 * limits["input_bytes"] + limits["output_bytes"] + limits["log_bytes"] + 90 * 1024 * 1024


def inventory(directory: Path, maximum: int) -> list[dict]:
    """Accept artifact directory/byte ceiling; return contained file records within it."""
    result, total = [], 0
    for path in sorted(directory.rglob("*")):
        relative = path.relative_to(directory).as_posix()
        safe_path(directory, relative)
        if path.is_file() and relative != "manifest.json":
            size = path.stat().st_size
            total += size
            if total > maximum:
                raise AnalysisError("limit_exceeded", "Retained artifact byte ceiling exceeded")
            result.append({"path": relative, "sha256": checksum(path), "size_bytes": size})
    return result


def seal(directory: Path, identity: dict, maximum: int) -> dict:
    """Accept directory/identity/byte ceiling; write immutable file inventory; return manifest."""
    result = {"schema_version": 1, **identity, "files": inventory(directory, maximum)}
    write_json(directory / "manifest.json", result)
    return result


def verify(directory: Path, expected_hash: str, maximum: int = 268435456) -> dict:
    """Accept directory/pinned manifest checksum/byte ceiling; return verified manifest."""
    manifest_path = safe_path(directory, "manifest.json")
    if not manifest_path.is_file() or manifest_path.stat().st_size > 2097152 or checksum(manifest_path) != expected_hash:
        raise AnalysisError("integrity_error", "Missing or damaged manifest")
    manifest = loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1 or not isinstance(manifest.get("files"), list):
        raise AnalysisError("unsupported_schema", "Unsupported artifact manifest")
    names, total = set(), 0
    for record in manifest["files"]:
        validate(record, FILE, "manifest file")
        if record["path"] in names or record["path"] == "manifest.json":
            raise AnalysisError("integrity_error", "Duplicate/self-referencing manifest entry")
        names.add(record["path"])
        total += record["size_bytes"]
        if total > maximum:
            raise AnalysisError("limit_exceeded", "Artifact input byte ceiling exceeded")
        path = safe_path(directory, record["path"])
        if not path.is_file() or path.stat().st_size != record["size_bytes"] or checksum(path) != record["sha256"]:
            raise AnalysisError("integrity_error", "Missing or damaged artifact", {"path": record["path"]})
    return manifest


def publish(temporary: Path, destination: Path) -> None:
    """Accept unpublished/destination directories; atomically publish without replacement; return nothing."""
    if destination.exists():
        raise AnalysisError("identity_conflict", "Published identity already exists")
    os.rename(temporary, destination)


def resolve(reference: dict, stores: dict, maximum: int, for_execution: bool = True) -> tuple[Path, dict]:
    """Accept reference/aliases/byte ceiling/execution flag; return verified source path/manifest.

Execution rejects diagnostic attempts and invalidated upstream methods. Historical
inspection may read either, preserving software validity warnings separately.
"""
    validate(reference, REFERENCE, "reference")
    expected = {"schema_version", "owner", "store_id", "manifest_sha256"}
    expected |= {"export_id"} if reference["owner"] == "srim" else {"analysis_id", "attempt_id"}
    if "kind" in reference:
        expected.add("kind")
    if set(reference) != expected:
        raise AnalysisError("invalid_input", "Reference identity fields do not match its owner")
    root = stores.get(reference["store_id"])
    if root is None:
        raise AnalysisError("unknown_store", "Unconfigured producer store")
    diagnostic = reference.get("kind") == "attempt"
    if diagnostic and (reference["owner"] != "analysis" or for_execution):
        raise AnalysisError("invalid_state", "Diagnostic attempts cannot be numerical inputs")
    relative = f"exports/{reference['export_id']}" if reference["owner"] == "srim" else f"analyses/{reference['analysis_id']}/attempts/{reference['attempt_id']}" + ("" if diagnostic else "/output")
    directory = safe_path(root, relative)
    manifest = verify(directory, reference["manifest_sha256"], maximum)
    if manifest.get("owner") != ("analysis-attempt" if diagnostic else reference["owner"]) or any(manifest.get(key) != reference[key] for key in expected - {"schema_version", "store_id", "manifest_sha256", "owner", "kind"}):
        raise AnalysisError("integrity_error", "Source identity differs from reference")
    if reference["owner"] == "analysis" and not diagnostic and manifest.get("state") != "CHECKED":
        raise AnalysisError("invalid_state", "Only sealed CHECKED output is an analysis input")
    if reference["owner"] == "srim":
        database = safe_path(root, "metadata.sqlite")
        if not database.is_file():
            raise AnalysisError("integrity_error", "Source producer index is missing")
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
            row = connection.execute("SELECT result FROM exports WHERE id=? AND state='PUBLISHED'", (reference["export_id"],)).fetchone()
        if not row or loads(row[0])["reference"]["manifest_sha256"] != reference["manifest_sha256"]:
            raise AnalysisError("integrity_error", "Export does not match its producer index")
        from agentic_srim.execution.jobs import read_verified
        run = read_verified(root, manifest["run_id"])
        if run["state"] != "CHECKED" or run["result"]["provenance"]["attempt_id"] != manifest["attempt_id"]:
            raise AnalysisError("integrity_error", "Export source is no longer intact")
    else:
        from analysis.config import Settings
        from analysis.storage import Store
        source_store = Store(Settings(root))
        with source_store.connection() as connection:
            job = source_store.job(connection, reference["analysis_id"])
            if diagnostic:
                attempt = next((item for item in source_store.attempts(connection, job["id"]) if item["id"] == reference["attempt_id"]), None)
            else:
                _, _, attempt = source_store.checked_output(connection, job, reference["attempt_id"])
            if not attempt or attempt.get("archive_sha256" if diagnostic else "output_sha256") != reference["manifest_sha256"]:
                raise AnalysisError("integrity_error", "Output differs from producer index")
            if for_execution and source_store.eligibility(connection, job["method"], job["source_ids"])["status"] == "invalidated":
                raise AnalysisError("method_invalidated", "Input result method is currently invalidated")
    if not isinstance(manifest.get("datasets", []), list):
        raise AnalysisError("invalid_schema", "Invalid source datasets")
    names = set()
    files = {record["path"] for record in manifest["files"]}
    for dataset in manifest.get("datasets", []):
        validate(dataset, DATASET, "dataset")
        if dataset["path"] not in files or dataset["name"] in names:
            raise AnalysisError("invalid_schema", "Dataset is duplicated or unsealed")
        names.add(dataset["name"])
    return directory, manifest


def snapshot(reference: dict, stores: dict, destination: Path, limits: dict) -> dict:
    """Accept reference/aliases/new snapshot path/limits; copy verified bytes; return source manifest."""
    source, manifest = resolve(reference, stores, limits["input_bytes"])
    destination.mkdir(parents=True)
    shutil.copyfile(source / "manifest.json", destination / "manifest.json")
    for record in manifest["files"]:
        target = safe_path(destination, record["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(safe_path(source, record["path"]), target)
    verify(destination, reference["manifest_sha256"], limits["input_bytes"])
    return manifest


def rows(directory: Path, dataset: dict, limits: dict) -> list[dict]:
    """Accept verified source directory/dataset/limits; return rectangular bounded JSONL records."""
    validate(dataset, DATASET, "dataset")
    columns = [column["name"] for column in dataset["columns"]]
    if len(columns) != len(set(columns)) or len(columns) > limits["columns"]:
        raise AnalysisError("invalid_data", "Duplicate/excessive dataset columns")
    result = []
    with safe_path(directory, dataset["path"]).open(encoding="utf-8") as stream:
        while line := stream.readline(limits["input_bytes"] + 1):
            if len(line.encode("utf-8")) > limits["input_bytes"]:
                raise AnalysisError("limit_exceeded", "Dataset row exceeds input byte limit")
            record = loads(line)
            if not isinstance(record, list) or len(record) != len(columns):
                raise AnalysisError("invalid_data", "Malformed dataset row")
            for value, column in zip(record, dataset["columns"]):
                if value is None:
                    if not column["nullable"] and column["type"] != "null":
                        raise AnalysisError("invalid_data", "Unexpected null column value")
                elif column["type"] != "mixed":
                    if column["type"] in ("array", "object"):
                        if type(value) is not (list if column["type"] == "array" else dict):
                            raise AnalysisError("invalid_data", "Column has wrong structured value type")
                    else:
                        validate(value, {"type": column["type"]}, "dataset value")
            result.append(dict(zip(columns, record)))
            if len(result) > limits["input_rows"]:
                raise AnalysisError("limit_exceeded", "Dataset row ceiling exceeded")
    if len(result) != dataset["row_count"]:
        raise AnalysisError("integrity_error", "Dataset row count differs from manifest")
    return result
