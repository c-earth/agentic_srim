"""Accept exact CHECKED run selections; publish producer-owned immutable JSONL exports.

Export intent precedes file publication. Replaying an interrupted intent uses the
same export ID and reconciles already published bytes without altering native
archives or starting a simulator. Export indexing is additive to metadata v1.
"""

from itertools import islice
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import uuid

from agentic_srim.execution.jobs import read_verified
from analysis import artifacts
from analysis.records import AUDIT, ID, NAME, SHORT, AnalysisError, checksum, digest, encode, loads, obj, safe_path, validate


SELECTION = obj({"name": SHORT, "columns": {"type": "array", "items": SHORT, "minItems": 1, "maxItems": 64},
                 "offset": {"type": "integer", "minimum": 0}, "count": {"type": "integer", "minimum": 1}}, ["name"])
EXPORT_SCHEMA = obj({"schema_version": {"type": "integer", "enum": [1]}, "run_id": ID, "attempt_id": ID,
                     "store_id": NAME, "datasets": {"type": "array", "items": SELECTION, "minItems": 1, "maxItems": 32}, **AUDIT})


def detail_records(path: Path, offset: int, count: int | None):
    """Accept sealed JSONL path/selection; yield bounded selected detail records without loading the file."""
    with path.open(encoding="utf-8") as stream:
        total, rows = 0, 0
        for line in islice(stream, offset, offset + count if count is not None else None):
            total += len(line.encode("utf-8"))
            rows += 1
            if total > 67108864 or rows > 1000000:
                raise AnalysisError("limit_exceeded", "Detail selection exceeds export bounds")
            yield loads(line)


def export_run_data(runtime: Path, arguments: dict) -> dict:
    """Accept runtime/exact run-attempt selections/audit; return immutable reference and bounded summary."""
    validate(arguments, EXPORT_SCHEMA)
    database = safe_path(runtime, "metadata.sqlite")
    if not database.exists():
        raise AnalysisError("not_found", "Producer store does not exist")
    request_hash = digest(arguments)
    with closing(sqlite3.connect(database, timeout=15)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("CREATE TABLE IF NOT EXISTS exports (id TEXT PRIMARY KEY, key TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL, state TEXT NOT NULL, result TEXT)")
        prior = connection.execute("SELECT id,fingerprint,state,result FROM exports WHERE key=?", (arguments["idempotency_key"],)).fetchone()
        if prior:
            if prior[1] != request_hash:
                raise AnalysisError("idempotency_conflict", "Export key identifies another request")
            export_id = prior[0]
            if prior[2] == "PUBLISHED":
                result = loads(prior[3])
                artifacts.verify(safe_path(runtime, f"exports/{export_id}"), result["reference"]["manifest_sha256"], 67108864)
                return result
        else:
            export_id = uuid.uuid4().hex
            connection.execute("INSERT INTO exports VALUES(?,?,?,'PUBLISHING',NULL)", (export_id, arguments["idempotency_key"], request_hash))
    record = read_verified(runtime, arguments["run_id"])
    result = record.get("result", {})
    if record["state"] != "CHECKED" or result.get("provenance", {}).get("attempt_id") != arguments["attempt_id"]:
        raise AnalysisError("invalid_state", "Export requires the exact sealed CHECKED result attempt")
    attempt = next(item for item in record["attempts"] if item["id"] == arguments["attempt_id"])
    archive = safe_path(runtime, attempt["artifact_path"].replace("\\", "/"))
    parent = safe_path(runtime, "exports")
    parent.mkdir(exist_ok=True)
    temporary = parent / ("unpublished-" + uuid.uuid4().hex)
    temporary.mkdir()
    datasets, selected_names, total_rows, total_bytes = [], set(), 0, 0
    try:
        for index, selection in enumerate(arguments["datasets"]):
            name = selection["name"]
            if name in selected_names:
                raise AnalysisError("invalid_input", "Duplicate export dataset")
            selected_names.add(name)
            normalization = {}
            preselected = False
            if record["settings"].get("capability", "stopping_range") == "stopping_range":
                if name != "stopping_range":
                    raise AnalysisError("not_found", "Unknown SR dataset; select stopping_range")
                data = result["rows"]
                units = {key: result["units"]["energy" if key == "energy_keV" else "length" if key.endswith("_nm") else "stopping"] for key in data[0]}
            elif name in result.get("profiles", {}):
                profile = result["profiles"][name]
                units = dict(zip(profile["columns"], profile["units"]))
                data = (dict(zip(profile["columns"], row)) for row in profile["rows"])
                normalization = {"native_ion_normalization": profile["native_ion_normalization"], "native_units": profile["native_units"]}
            elif name in result.get("details", {}):
                detail = result["details"][name]
                detail_path = safe_path(archive, detail["artifact"])
                fields = set()
                for row in detail_records(detail_path, selection.get("offset", 0), selection.get("count")):
                    fields.update(row)
                units = {field: ("eV/nm" if field == "electronic_stopping_eV_nm" else "nm" if field.endswith("_nm")
                                else "keV" if field.endswith("_keV") else "eV" if field.endswith("_eV")
                                else "dimensionless" if field == "direction" else "") for field in sorted(fields)}
                data = detail_records(detail_path, selection.get("offset", 0), selection.get("count"))
                preselected = True
            else:
                raise AnalysisError("not_found", "Unknown parsed TRIM dataset")
            columns = selection.get("columns", list(units))
            if len(columns) != len(set(columns)) or not set(columns) <= units.keys():
                raise AnalysisError("invalid_input", "Unknown/duplicate export columns")
            offset = selection.get("offset", 0)
            selected = data if preselected else islice(data, offset, offset + selection["count"] if "count" in selection else None)
            path = f"dataset_{index + 1}.jsonl"
            count = 0
            types = {column: set() for column in columns}
            nullable = {column: False for column in columns}
            with (temporary / path).open("x", encoding="utf-8", newline="\n") as stream:
                for row in selected:
                    values = [row.get(column) for column in columns]
                    for column, value in zip(columns, values):
                        if value is None:
                            nullable[column] = True
                        else:
                            types[column].add({int: "integer", float: "number", str: "string", bool: "boolean", dict: "object", list: "array"}[type(value)])
                    text = encode(values) + "\n"
                    total_bytes += len(text.encode("utf-8"))
                    total_rows += 1
                    if total_bytes > 67108864 or total_rows > 1000000:
                        raise AnalysisError("limit_exceeded", "Export exceeds input byte/row bounds")
                    stream.write(text)
                    count += 1
            if not count:
                raise AnalysisError("invalid_input", "Export selection is empty")
            datasets.append({"name": name, "path": path, "row_count": count,
                             "columns": [{"name": column, "unit": units[column],
                                          "type": (next(iter(types[column])) if len(types[column]) == 1 else "number" if types[column] and types[column] <= {"integer", "number"} else "mixed" if types[column] else "null"),
                                          "nullable": nullable[column]} for column in columns], "normalization": normalization})
        manifest = artifacts.seal(temporary, {"owner": "srim", "export_id": export_id, "run_id": record["id"], "attempt_id": attempt["id"],
                    "source_ids": [record["id"]], "source_manifest_sha256": attempt["manifest_sha256"],
                    "capability": record["settings"].get("capability", "stopping_range"), "source_schema_version": result.get("schema_version", 1),
                    "validation": result.get("validation", {"format": "checked", "scientific_accuracy": "not_validated"}),
                    "source_detail_units": {selection["name"]: result["details"][selection["name"]]["units"] for selection in arguments["datasets"] if selection["name"] in result.get("details", {})},
                    "parser_version": result["analysis_version"], "physical_hash": record["physical_hash"],
                    "execution_hash": record["execution_hash"],
                    "conditions": record["request"], "selection": arguments["datasets"], "datasets": datasets,
                    "limitations": result.get("limitations", ["Scientific accuracy not validated."]), "warnings": record.get("warnings", []),
                    "actor": arguments["actor"], "reason": arguments["reason"]}, 67108864)
        manifest_hash = checksum(temporary / "manifest.json")
        artifacts.verify(temporary, manifest_hash, 67108864)
        destination = safe_path(runtime, f"exports/{export_id}")
        try:
            artifacts.publish(temporary, destination)
        except (FileExistsError, AnalysisError):
            artifacts.verify(destination, manifest_hash, 67108864)
        response = {"schema_version": 1, "reference": {"schema_version": 1, "owner": "srim", "store_id": arguments["store_id"],
                    "export_id": export_id, "manifest_sha256": manifest_hash}, "datasets": datasets,
                    "warnings": manifest["warnings"], "limitations": manifest["limitations"]}
        with closing(sqlite3.connect(database, timeout=15)) as connection, connection:
            connection.execute("UPDATE exports SET state='PUBLISHED',result=? WHERE id=?", (encode(response), export_id))
        return response
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
