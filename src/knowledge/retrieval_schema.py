"""Validate unified retrieval documents and match exact saved scientific quantities."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from fractions import Fraction
from datetime import datetime
import hashlib
import json
import re

from src.knowledge.schema import UNITS, _bounded_decimal, _compile_filters, convert_quantity, validate_search_document
from src.srim.common.types import JSONObject

KINDS = ("run", "case", "analysis")
STATUSES = {"run": ("succeeded", "failed"), "case": ("open", "paused", "completed", "archived"), "analysis": ("draft", "completed", "failed")}
SECTIONS = ("title", "question", "tags", "notes", "method", "findings", "limitations", "member_notes", "output_notes")
WEIGHTS = (5, 5, 3, 1, 2, 5, 2, 1, 1)
PROJECTOR_VERSION = "unified-retrieval-v1"
QUERY_KEYS = set(("schema_version", "kinds", "text", "statuses", "tags", "revision_scope", "input_filters", "output_filter", "relations"))
DOCUMENT_KEYS = set(("schema_version", "document_key", "kind", "id", "reference", "status", "title", "created_at", "updated_at", "sections", "attributes", "links", "outputs", "issues", "projector_version"))
OUTPUT_KEYS = set(("state", "result_source", "complete", "result_file", "source_files", "summary", "tables", "warnings", "issues", "projection_version"))
EMPTY_QUERY = {"schema_version": 1, "kinds": list(KINDS), "text": None, "statuses": {kind: None for kind in KINDS}, "tags": None, "revision_scope": "current", "input_filters": None, "output_filter": None, "relations": []}
OUTPUT_FILTER_KEYS = {"result_sources", "complete", "summary", "table_groups", "availability"}


def canonical_document_key(kind: str, reference: JSONObject) -> str:
    """Validate kind/reference identity and return its canonical SHA-256 without I/O.

    Args:
        kind: Unified run, case or analysis kind.
        reference: Existing native or exact project revision reference.
    Returns:
        Lowercase canonical identity digest.
    Raises:
        ValueError: Kind, reference shape or identity is invalid.
    """
    if kind not in KINDS or not isinstance(reference, dict):
        raise ValueError("Invalid retrieval identity")
    if kind == "run":
        validate_search_document(reference, "reference")
    elif set(reference) != {"kind", "id", "revision", "sha256"} or reference["kind"] != kind or type(reference["revision"]) is not int or reference["revision"] < 1 or not isinstance(reference["id"], str) or re.fullmatch(("case" if kind == "case" else "post") + r"_[0-9]{8}_[0-9]{6}_[1-9][0-9]*", reference["id"]) is None or not isinstance(reference["sha256"], str) or re.fullmatch("[0-9a-f]{64}", reference["sha256"]) is None:
        raise ValueError("Invalid project revision reference")
    return hashlib.sha256(json.dumps({"kind": kind, "reference": reference}, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def validate_retrieval_document(document: JSONObject, kind: str) -> None:
    """Validate a closed retrieval envelope recursively without filesystem access.

    Args:
        document: Query, document, projection, result, report, links or plan envelope.
        kind: Envelope discriminator.
    Returns:
        None after validation.
    Raises:
        ValueError: Unknown keys, malformed values or unsupported scientific semantics.
    """
    if not isinstance(document, dict):
        raise ValueError("Retrieval envelope must be an object")
    if kind == "query":
        if set(document) != QUERY_KEYS or type(document["schema_version"]) is not int or document["schema_version"] != 1:
            raise ValueError("Invalid retrieval query envelope")
        kinds = document["kinds"]
        if not isinstance(kinds, list) or not kinds or any(item not in KINDS for item in kinds) or len(set(kinds)) != len(kinds):
            raise ValueError("Invalid retrieval kinds")
        text = document["text"]
        if text is not None and (not isinstance(text, str) or not text.strip() or len(text) > 4096 or "\0" in text):
            raise ValueError("Invalid search text")
        if document["revision_scope"] not in ("current", "history") or not isinstance(document["statuses"], dict) or set(document["statuses"]) != set(KINDS):
            raise ValueError("Invalid revision scope/statuses")
        for key, values in document["statuses"].items():
            if values is not None and (not isinstance(values, list) or not values or any(value not in STATUSES[key] for value in values) or len(set(values)) != len(values)):
                raise ValueError("Invalid kind-specific statuses")
        tags = document["tags"]
        if tags is not None:
            if kinds != ["case"] or not isinstance(tags, dict) or set(tags) != {"operator", "values"} or tags["operator"] not in ("contains_all", "contains_any") or not isinstance(tags["values"], list) or not tags["values"] or any(not isinstance(tag, str) or not 1 <= len(tag) <= 128 for tag in tags["values"]):
                raise ValueError("Tags require case-only kinds and explicit values")
        if document["input_filters"] is not None:
            if kinds != ["run"]:
                raise ValueError("Direct input filters require run-only kinds")
            validate_search_document(document["input_filters"], "filters")
        if document["output_filter"] is not None:
            if kinds != ["run"]:
                raise ValueError("Direct output filters require run-only kinds")
            match_output_filter({"state": "missing", "result_source": None, "complete": None, "summary": [], "tables": []}, document["output_filter"])
        if not isinstance(document["relations"], list):
            raise ValueError("Relations must be a list")
        for clause in document["relations"]:
            if not isinstance(clause, dict) or set(clause) != {"relation", "direction", "reference", "target_input_filters", "target_output_filter"} or clause["relation"] not in ("case_member", "analysis_input", "analysis_artifact") or clause["direction"] not in ("incoming", "outgoing"):
                raise ValueError("Invalid relation clause")
            if all(clause[key] is None for key in ("reference", "target_input_filters", "target_output_filter")):
                raise ValueError("Relation clause requires a reference or target filter")
            if clause["reference"] is not None and not isinstance(clause["reference"], dict):
                raise ValueError("Invalid relation reference")
            for key in ("target_input_filters", "target_output_filter"):
                if clause[key] is not None and (clause["direction"] != "outgoing" or clause["relation"] not in ("case_member", "analysis_input")):
                    raise ValueError("Scientific relationship filters require outgoing run links")
            if clause["target_input_filters"] is not None:
                validate_search_document(clause["target_input_filters"], "filters")
            if clause["target_output_filter"] is not None:
                match_output_filter({"state": "missing", "result_source": None, "complete": None, "summary": [], "tables": []}, clause["target_output_filter"])
    elif kind == "document":
        if set(document) != DOCUMENT_KEYS or type(document["schema_version"]) is not int or document["schema_version"] != 1 or document["document_key"] != canonical_document_key(document["kind"], document["reference"]) or document["id"] != document["reference"]["id"] or document["status"] not in STATUSES[document["kind"]] or document["projector_version"] != PROJECTOR_VERSION:
            raise ValueError("Invalid retrieval document")
        if not isinstance(document["title"], str) or not isinstance(document["sections"], list) or not isinstance(document["attributes"], dict) or not isinstance(document["links"], list) or not isinstance(document["issues"], list):
            raise ValueError("Malformed retrieval document fields")
        for timestamp in (document["created_at"], document["updated_at"]):
            if timestamp is not None and (not isinstance(timestamp, str) or datetime.fromisoformat(timestamp).utcoffset() is None):
                raise ValueError("Invalid retrieval timestamp")
        locators = [section.get("locator") for section in document["sections"] if isinstance(section, dict)] + [link.get("locator") for link in document["links"] if isinstance(link, dict)]
        for locator in locators:
            if not isinstance(locator, dict) or set(locator) != {"reference", "file", "json_pointer"} or locator["reference"] != document["reference"] or not isinstance(locator["json_pointer"], str) or locator["json_pointer"] and not locator["json_pointer"].startswith("/"):
                raise ValueError("Invalid document evidence locator")
            if locator["file"] is not None:
                validate_search_document(locator["file"], "file")
                if locator["file"]["owner"] != document["reference"]:
                    raise ValueError("Locator file belongs to another source")
        for section in document["sections"]:
            if not isinstance(section, dict) or set(section) != {"name", "text", "locator", "weight"} or section["name"] not in SECTIONS or not isinstance(section["text"], str) or type(section["weight"]) is not int or section["weight"] != WEIGHTS[SECTIONS.index(section["name"])]:
                raise ValueError("Invalid text section")
        for link in document["links"]:
            if not isinstance(link, dict) or set(link) != {"relation", "reference", "locator", "role", "note"} or link["relation"] not in ("case_member", "analysis_input", "analysis_artifact") or not isinstance(link["reference"], dict):
                raise ValueError("Invalid document link")
        if document["kind"] == "run":
            if set(document["attributes"]) != {"inputs"} or document["outputs"] is None:
                raise ValueError("Invalid run attributes")
            validate_search_document({"schema_version": 1, "reference": document["reference"], "origin": "native", "execution_status": document["status"], "inputs": document["attributes"]["inputs"], "observations": [], "files": [], "issues": []}, "record")
            validate_retrieval_document(document["outputs"], "projection")
        elif document["outputs"] is not None:
            raise ValueError("Project records cannot carry native output projections")
        elif document["kind"] == "case":
            if set(document["attributes"]) != {"tags"} or not isinstance(document["attributes"]["tags"], list) or any(not isinstance(tag, str) for tag in document["attributes"]["tags"]):
                raise ValueError("Invalid case attributes")
        elif set(document["attributes"]) != {"eligibility", "method_reference"} or not isinstance(document["attributes"]["eligibility"], dict) or set(document["attributes"]["eligibility"]) != {"allow_failed_runs", "allow_checkpoint_recovery", "require_complete_parser"} or any(type(value) is not bool for value in document["attributes"]["eligibility"].values()):
            raise ValueError("Invalid analysis attributes")
    elif kind == "projection":
        if set(document) != OUTPUT_KEYS or document["state"] not in ("available", "partial", "missing", "rejected", "unsupported") or document["result_source"] not in (None, "native-text", "checkpoint-recovery") or document["complete"] is not None and type(document["complete"]) is not bool or document["projection_version"] != PROJECTOR_VERSION:
            raise ValueError("Invalid output projection")
        for name in ("source_files", "summary", "tables", "warnings", "issues"):
            if not isinstance(document[name], list):
                raise ValueError("Invalid output projection collection")
        if document["result_file"] is not None:
            validate_search_document(document["result_file"], "file")
        for source_file in document["source_files"]:
            validate_search_document(source_file, "file")
        for locator in [value.get("locator") for value in document["summary"] if isinstance(value, dict)] + [table.get("locator") for table in document["tables"] if isinstance(table, dict)]:
            if not isinstance(locator, dict) or set(locator) != {"reference", "file", "json_pointer"} or not isinstance(locator["json_pointer"], str) or not locator["json_pointer"].startswith("/") or locator["file"] != document["result_file"]:
                raise ValueError("Invalid output locator")
            validate_search_document(locator["file"], "file")
            if locator["reference"] != locator["file"]["owner"]:
                raise ValueError("Invalid output evidence owner")
        for value in document["summary"]:
            if not isinstance(value, dict) or set(value) != {"name", "value", "unit", "availability", "locator"} or value["availability"] not in ("available", "unavailable", "not_calculated"):
                raise ValueError("Invalid summary value")
            if value["value"] is not None:
                _bounded_decimal(value["value"])
        for table in document["tables"]:
            if not isinstance(table, dict) or set(table) != {"name", "columns", "rows", "availability", "calculated_ions", "precision", "notes", "locator"} or table["availability"] not in ("available", "unavailable", "not_calculated") or not isinstance(table["columns"], list) or not isinstance(table["rows"], list):
                raise ValueError("Invalid output table")
            if any(not isinstance(column, dict) or not isinstance(column.get("unit"), str) for column in table["columns"]):
                raise ValueError("Invalid output column")
            for row in table["rows"]:
                if not isinstance(row, list) or len(row) != len(table["columns"]):
                    raise ValueError("Invalid output row width")
                for value in row:
                    if value is not None:
                        _bounded_decimal(value)
    else:
        shapes = {
            "result": {"schema_version", "generation", "query_sha256", "total", "offset", "limit", "matches", "excluded", "coverage"},
            "report": {"schema_version", "operation_id", "state", "generation", "indexed", "skipped", "rejected", "issues", "completed_at"},
            "links": {"schema_version", "generation", "total", "offset", "limit", "links", "issues"},
            "plan": {"schema_version", "scope", "plan_sha256", "actions", "issues", "report"},
        }
        if kind not in shapes or set(document) != shapes[kind] or type(document["schema_version"]) is not int or document["schema_version"] != 1:
            raise ValueError("Invalid retrieval envelope kind/shape")
    try:
        json.dumps(document, allow_nan=False)
    except (ValueError, TypeError) as error:
        raise ValueError("Retrieval envelopes require finite JSON") from error


def match_output_filter(outputs: JSONObject, filters: JSONObject) -> JSONObject:
    """Validate output predicates and return exact summary and shared-row witnesses.

    Args:
        outputs: Saved scientific output projection, or missing projection for validation.
        filters: Closed availability, provenance, summary and table-group constraints.
    Returns:
        matched, summary locators, row_locators and rows_truncated flag.
    Raises:
        ValueError: Malformed clauses, incompatible units or numeric limits.
    """
    if not isinstance(filters, dict) or set(filters) != OUTPUT_FILTER_KEYS:
        raise ValueError("Invalid output filter envelope")
    sources = filters["result_sources"]
    if sources is not None and (not isinstance(sources, list) or not sources or any(item not in ("native-text", "checkpoint-recovery") for item in sources) or len(set(sources)) != len(sources)):
        raise ValueError("Invalid output result sources")
    if filters["complete"] is not None and type(filters["complete"]) is not bool:
        raise ValueError("Invalid output completeness")
    prepared = []
    if any(not isinstance(filters[key], list) for key in ("summary", "table_groups", "availability")):
        raise ValueError("Output clauses must be lists")
    for clause in filters["summary"]:
        if not isinstance(clause, dict) or set(clause) != {"name", "operator", "value", "unit"} or clause["name"] not in ("calculated_ions", "transmitted_ions", "backscattered_ions", "mean_range", "straggling", "vacancies_per_ion"):
            raise ValueError("Unknown summary field")
        if clause["operator"] != "unknown":
            required_unit = {"calculated_ions": "ions", "transmitted_ions": "ions", "backscattered_ions": "ions", "vacancies_per_ion": "vacancies/ion"}.get(clause["name"])
            if required_unit is not None and clause["unit"] != required_unit or required_unit is None and (not isinstance(clause["unit"], str) or clause["unit"] not in UNITS or UNITS[clause["unit"]][0] != "length"):
                raise ValueError("Incompatible summary unit")
        prepared.append(clause)
    for group in filters["table_groups"]:
        if not isinstance(group, dict) or set(group) != {"table", "clauses"} or not isinstance(group["table"], str) or not group["table"] or not isinstance(group["clauses"], list) or not group["clauses"]:
            raise ValueError("Invalid table predicate group")
        for clause in group["clauses"]:
            if not isinstance(clause, dict) or set(clause) != {"column", "operator", "value", "unit"} or not (type(clause["column"]) is int and clause["column"] >= 0 or isinstance(clause["column"], str) and clause["column"] in ("energy", "requested_energy", "electronic_stopping", "nuclear_stopping", "projected_range", "longitudinal_straggling", "lateral_straggling")):
                raise ValueError("Invalid table column")
            if group["table"] == "sr.rows" and isinstance(clause["column"], str) and clause["operator"] != "unknown":
                if clause["column"] in ("electronic_stopping", "nuclear_stopping"):
                    from src.srim.sr.outputs import STOPPING_LABELS
                    if clause["unit"] not in STOPPING_LABELS:
                        raise ValueError("Invalid stopping unit label")
                else:
                    dimension = "energy" if clause["column"] in ("energy", "requested_energy") else "length"
                    if not isinstance(clause["unit"], str) or clause["unit"] not in UNITS or UNITS[clause["unit"]][0] != dimension:
                        raise ValueError("Incompatible SR column unit")
            prepared.append(clause)
    for clause in prepared:
        if clause["operator"] not in ("eq", "between", "unknown"):
            raise ValueError("Invalid numeric operator")
        if clause["operator"] == "unknown":
            if clause["value"] is not None or clause["unit"] is not None:
                raise ValueError("Unknown requires null value/unit")
            continue
        if not isinstance(clause["unit"], str) or not clause["unit"]:
            raise ValueError("Numeric output query requires explicit unit")
        values = [clause["value"]] if clause["operator"] == "eq" else clause["value"]
        if not isinstance(values, list) or clause["operator"] == "between" and (len(values) != 2 or values == [None, None]):
            raise ValueError("Invalid output bounds")
        for value in values:
            if value is not None:
                _bounded_decimal(value)
        if clause["operator"] == "eq" and values[0] is None or len(values) == 2 and all(value is not None for value in values) and Fraction(values[0]) > Fraction(values[1]):
            raise ValueError("Invalid output equality/bounds")
    for clause in filters["availability"]:
        if not isinstance(clause, dict) or set(clause) != {"table", "state"} or not isinstance(clause["table"], str) or clause["state"] not in ("available", "unavailable", "not_calculated"):
            raise ValueError("Invalid availability clause")
    result = {"matched": False, "summary": [], "row_locators": [], "rows_truncated": False}
    if outputs["state"] not in ("available", "partial") or sources is not None and outputs["result_source"] not in sources or filters["complete"] is not None and outputs["complete"] != filters["complete"]:
        return result
    for clause in filters["availability"]:
        if not any(table["name"] == clause["table"] and table["availability"] == clause["state"] for table in outputs["tables"]):
            return result
    groups = [(None, filters["summary"])] + [(group, group["clauses"]) for group in filters["table_groups"]]
    for group, clauses in groups:
        if not clauses:
            continue
        table = None if group is None else next((item for item in outputs["tables"] if item["name"] == group["table"]), None)
        if group is not None and (table is None or table["availability"] != "available"):
            return result
        witnesses = []
        for row_index, row in enumerate([None] if group is None else table["rows"]):
            matched = True
            summary_locators = []
            for clause in clauses:
                if group is None:
                    item = next((item for item in outputs["summary"] if item["name"] == clause["name"]), None)
                    actual, unit = (None, None) if item is None else (item["value"], item["unit"])
                    if item is not None:
                        summary_locators.append(item["locator"])
                else:
                    column_index = next((i for i, col in enumerate(table["columns"]) if (col.get("name") if isinstance(clause["column"], str) else i) == clause["column"]), None)
                    if column_index is None:
                        matched = False
                        break
                    actual, unit = row[column_index], table["columns"][column_index]["unit"]
                if clause["operator"] == "unknown":
                    success = actual is None
                elif actual is None:
                    success = False
                else:
                    expected = clause["value"]
                    target = clause["unit"]
                    if unit != target:
                        try:
                            actual = convert_quantity(actual, unit, target)
                        except ValueError:
                            success = False
                            matched = False
                            break
                    number = Fraction(_bounded_decimal(actual))
                    success = number == Fraction(expected) if clause["operator"] == "eq" else (expected[0] is None or number >= Fraction(expected[0])) and (expected[1] is None or number <= Fraction(expected[1]))
                if not success:
                    matched = False
                    break
            if matched:
                if group is None:
                    result["summary"].extend(summary_locators)
                else:
                    witnesses.append({**table["locator"], "row": row_index, "table": table["name"]})
        if group is not None and not witnesses or group is None and clauses and not matched:
            return result
        available = max(0, 20 - len(result["row_locators"]))
        result["row_locators"].extend(witnesses[:available])
        result["rows_truncated"] |= len(witnesses) > available
    result["matched"] = True
    return result


def compile_retrieval_query(query: JSONObject) -> Callable[[JSONObject], bool]:
    """Prepare intrinsic query predicates without mutating caller-owned JSON.

    Args:
        query: Complete closed unified query.
    Returns:
        Pure evaluator of validated document intrinsic fields, excluding text/relations.
    Raises:
        ValueError: Invalid query or document.
    """
    validate_retrieval_document(query, "query")
    captured = deepcopy(query)
    inputs = None if captured["input_filters"] is None else _compile_filters(captured["input_filters"])

    def evaluate(document: JSONObject) -> bool:
        """Return intrinsic eligibility of document for privately captured query, without I/O."""
        validate_retrieval_document(document, "document")
        kind = document["kind"]
        if kind not in captured["kinds"] or captured["statuses"][kind] is not None and document["status"] not in captured["statuses"][kind]:
            return False
        if captured["tags"] is not None:
            wanted, actual = set(captured["tags"]["values"]), set(document["attributes"]["tags"])
            if not (wanted <= actual if captured["tags"]["operator"] == "contains_all" else bool(wanted & actual)):
                return False
        if inputs is not None:
            record = {"schema_version": 1, "reference": document["reference"], "origin": "native", "execution_status": document["status"], "inputs": document["attributes"]["inputs"], "observations": [], "files": [], "issues": []}
            if not inputs(record):
                return False
        return captured["output_filter"] is None or match_output_filter(document["outputs"], captured["output_filter"])["matched"]

    return evaluate
