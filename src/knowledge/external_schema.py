"""Validate external evidence envelopes and exact input/summary predicates."""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import json
import os
import re
from jsonschema import Draft202012Validator
from src.knowledge.schema import UNITS, _bounded_decimal, convert_quantity
from src.srim.common.types import JSONObject

HASH = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
SOURCE = {"type": "string", "pattern": "^db_[0-9a-f]{64}$"}
CASE = {"type": "string", "pattern": "^SM-[0-9]+_[A-Za-z]+_(p|n|cov)_d[0-9]+nm$"}
TEXT = {"type": "string"}
NULL = {"type": "null"}
INTEGER = {"type": "integer", "minimum": 0}
DECIMAL = {"type": "string", "minLength": 1, "maxLength": 256}
REFERENCE_PROPERTIES = {"schema_version": {"const": 1}, "kind": {"const": "external-run"}, "source_id": SOURCE, "adapter_id": {"const": "snumat-trim-v1"}, "adapter_version": {"const": 1}, "case_id": CASE, "record_sha256": HASH}
LOCATOR_PROPERTIES = {"source_id": SOURCE, "relative_path": TEXT, "member_path": TEXT, "member_sha256": HASH, "size_bytes": INTEGER, "json_pointer": {"type": ["string", "null"]}, "line_start": {"type": ["integer", "null"], "minimum": 1}, "line_end": {"type": ["integer", "null"], "minimum": 1}}
EXTERNAL_REFERENCE_SCHEMA = {"type": "object", "additionalProperties": False, "required": list(REFERENCE_PROPERTIES), "properties": REFERENCE_PROPERTIES}
EXTERNAL_LOCATOR_SCHEMA = {"type": "object", "additionalProperties": False, "required": list(LOCATOR_PROPERTIES), "properties": LOCATOR_PROPERTIES}
QUANTITY_PROPERTIES = {"value": {"oneOf": [DECIMAL, NULL]}, "unit": {"enum": ["keV", "eV", "MeV", "A", "nm", "um", "amu", "g/cm3", "deg", "ions", "vacancies/ion", "1"]}, "raw": {"type": ["string", "null"]}, "availability": {"enum": ["available", "unavailable"]}, "locator": {"oneOf": [EXTERNAL_LOCATOR_SCHEMA, NULL]}}
QUANTITY_SCHEMA = {"type": "object", "additionalProperties": False, "required": list(QUANTITY_PROPERTIES), "properties": QUANTITY_PROPERTIES}
COMPONENT_PROPERTIES = {"symbol": TEXT, "atomic_number": {"type": "integer", "minimum": 1}, "mass_amu": QUANTITY_SCHEMA, "stoichiometry": QUANTITY_SCHEMA}
COMPONENT_SCHEMA = {"type": "object", "additionalProperties": False, "required": list(COMPONENT_PROPERTIES), "properties": COMPONENT_PROPERTIES}
LAYER_PROPERTIES = {"index": {"const": 1}, "label": TEXT, "thickness_A": QUANTITY_SCHEMA, "density_g_cm3": QUANTITY_SCHEMA, "composition": {"type": "array", "minItems": 1, "maxItems": 92, "items": COMPONENT_SCHEMA}, "phase": {"enum": [0, 1]}}
LAYER_SCHEMA = {"type": "object", "additionalProperties": False, "required": list(LAYER_PROPERTIES), "properties": LAYER_PROPERTIES}
INPUT_PROPERTIES = {"engine": {"const": "trim"}, "engine_version": {"const": "SRIM-2013.00"}, "ion_symbol": TEXT, "ion_z": {"type": "integer", "minimum": 1}, "ion_mass_amu": QUANTITY_SCHEMA, "energy_keV": QUANTITY_SCHEMA, "incidence_angle_deg": QUANTITY_SCHEMA, "number_of_ions": {"type": "integer", "minimum": 1}, "random_seed": INTEGER, "calculation_mode": {"const": 1}, "target_label": TEXT, "layers": {"type": "array", "minItems": 1, "maxItems": 1, "items": LAYER_SCHEMA}, "compound_correction": QUANTITY_SCHEMA, **{key: {"type": "array", "items": QUANTITY_SCHEMA} for key in ("displacement_eV", "lattice_binding_eV", "surface_binding_eV")}}
INPUT_SCHEMA = {"type": "object", "additionalProperties": False, "required": list(INPUT_PROPERTIES), "properties": INPUT_PROPERTIES}
SUMMARY_UNITS = {"calculated_ions": "ions", "transmitted_ions": "ions", "backscattered_ions": "ions", "mean_range": "A", "straggling": "A", "vacancies_per_ion": "vacancies/ion"}
PLANNED_UNITS = {"ion_z": "1", "ion_mass_u": "amu", "ion_selection_rank": "1", "ion_selection_score": "1", "requested_depth_nm": "nm", "selected_energy_keV": "keV", "density_g_cm3": "g/cm3", "compound_correction": "1", "target_thickness_nm": "nm", "target_thickness_angstrom": "A", "planned_number_of_ions": "ions", "incidence_angle_deg": "deg", "calculation_mode": "1"}
ESTIMATE_UNITS = {"srmodule_interpolated_rp_nm": "nm", "srmodule_longitudinal_straggling_nm": "nm", "srmodule_lateral_straggling_nm": "nm"}
MANIFEST_FIELDS = ("case_id", "status", "target_id", "target_formula", "target_source", "target_qa_status", "target_qa_flags", "ion_symbol", "ion_z", "ion_mass_u", "ion_selection_type", "ion_selection_source", "ion_selection_coverage_reason", "ion_selection_rank", "ion_selection_score", "ion_selection_score_name", "requested_depth_nm", "selected_energy_keV", "srmodule_interpolated_rp_nm", "srmodule_longitudinal_straggling_nm", "srmodule_lateral_straggling_nm", "density_g_cm3", "reduced_stoichiometry", "compound_correction", "compound_correction_source", "target_thickness_nm", "target_thickness_angstrom", "planned_number_of_ions", "incidence_angle_deg", "calculation_mode", "range3d_enabled", "trim_in_path", "planned_range3d_path", "status_note")
PLANNED_PROPERTIES = {key: QUANTITY_SCHEMA if key in PLANNED_UNITS else TEXT for key in MANIFEST_FIELDS if key not in ESTIMATE_UNITS}
PLANNED_SCHEMA = {"type": "object", "additionalProperties": False, "required": list(PLANNED_PROPERTIES), "properties": PLANNED_PROPERTIES}
ESTIMATE_SCHEMA = {"type": "object", "additionalProperties": False, "required": list(ESTIMATE_UNITS), "properties": {key: QUANTITY_SCHEMA for key in ESTIMATE_UNITS}}
UNAVAILABLE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["name", "state", "reason"], "properties": {"name": TEXT, "state": {"enum": ["not_indexed", "not_published"]}, "reason": TEXT}}
OUTPUT_PROPERTIES = {"state": {"enum": ["available", "partial", "unavailable", "rejected"]}, "summary": {"type": "object", "additionalProperties": False, "required": list(SUMMARY_UNITS), "properties": {key: QUANTITY_SCHEMA for key in SUMMARY_UNITS}}, "unavailable_outputs": {"type": "array", "items": UNAVAILABLE_SCHEMA}}
OUTPUT_SCHEMA = {"type": "object", "additionalProperties": False, "required": list(OUTPUT_PROPERTIES), "properties": OUTPUT_PROPERTIES}
ISSUE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["code", "field", "message", "locators"], "properties": {"code": {"enum": ["missing", "malformed", "integrity", "conflicting", "unsupported", "precision_difference", "publisher_status_conflict"]}, "field": TEXT, "message": TEXT, "locators": {"type": "array", "items": EXTERNAL_LOCATOR_SCHEMA}}}
PROVENANCE_PROPERTIES = {"manifest_status": TEXT, "mapping_status": TEXT, "validation": {"type": "object"}, "run_metadata": {"type": "object"}, "case_mapping": {"type": "object"}, "input_hash_claims": {"type": "object", "additionalProperties": HASH}, "input_fields": {"type": "object", "additionalProperties": EXTERNAL_LOCATOR_SCHEMA}, "selected_members": {"type": "object", "additionalProperties": EXTERNAL_LOCATOR_SCHEMA}, "independent_checks": {"type": "array", "items": TEXT}, "parser_diagnostics": {"type": "array", "items": TEXT}}
PROVENANCE_SCHEMA = {"type": "object", "additionalProperties": False, "required": list(PROVENANCE_PROPERTIES), "properties": PROVENANCE_PROPERTIES}
RECORD_PROPERTIES = {"schema_version": {"const": 1}, "kind": {"const": "external-run"}, "source_id": SOURCE, "adapter_id": {"const": "snumat-trim-v1"}, "adapter_version": {"const": 1}, "case_id": CASE, "target_id": {"type": "string", "pattern": "^SM-[0-9]+$"}, "inputs": INPUT_SCHEMA, "planned": PLANNED_SCHEMA, "estimates": ESTIMATE_SCHEMA, "outputs": OUTPUT_SCHEMA, "provenance": PROVENANCE_SCHEMA, "issues": {"type": "array", "items": ISSUE_SCHEMA}}
EXTERNAL_RECORD_SCHEMA = {"type": "object", "additionalProperties": False, "required": list(RECORD_PROPERTIES), "properties": RECORD_PROPERTIES}
FIELDS = {"inputs.ion_z": ("integer", "1"), "inputs.ion_mass_amu": ("number", "amu"), "inputs.energy_keV": ("number", "keV"), "inputs.incidence_angle_deg": ("number", "deg"), "inputs.number_of_ions": ("integer", "ions"), "inputs.random_seed": ("integer", "1"), "inputs.calculation_mode": ("integer", "1"), "inputs.layer.thickness_A": ("number", "A"), "inputs.layer.density_g_cm3": ("number", "g/cm3"), "inputs.target_label": ("text", None), "inputs.ion_symbol": ("text", None), "inputs.target_elements": ("elements", None), "planned.requested_depth_nm": ("number", "nm"), "planned.selected_energy_keV": ("number", "keV"), "estimates.srmodule_interpolated_rp_nm": ("number", "nm"), **{"outputs." + key: ("integer" if unit == "ions" else "number", unit) for key, unit in SUMMARY_UNITS.items()}}
CLAUSE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["field", "operator", "value", "unit"], "properties": {"field": {"enum": list(FIELDS)}, "operator": {"enum": ["eq", "between", "unknown", "contains_all", "contains_any"]}, "value": {"oneOf": [NULL, DECIMAL, {"type": "array", "maxItems": 92, "items": {"type": ["string", "null"]}}]}, "unit": {"type": ["string", "null"]}}}
QUERY_PROPERTIES = {"schema_version": {"const": 1}, "text": {"type": ["string", "null"], "minLength": 1, "maxLength": 4096}, "revision_scope": {"enum": ["current", "history"]}, "clauses": {"type": "array", "maxItems": 128, "items": CLAUSE_SCHEMA}}
EXTERNAL_QUERY_SCHEMA = {"type": "object", "additionalProperties": False, "required": list(QUERY_PROPERTIES), "properties": QUERY_PROPERTIES}
EMPTY_EXTERNAL_QUERY = {"schema_version": 1, "text": None, "revision_scope": "current", "clauses": []}
FILE_PROPERTIES = {"path": TEXT, "size_bytes": INTEGER, "sha256": HASH}
FINGERPRINT_FILE_SCHEMA = {"type": "object", "additionalProperties": False, "required": list(FILE_PROPERTIES), "properties": FILE_PROPERTIES}
FINGERPRINT_PROPERTIES = {"source_id": SOURCE, "adapter_id": {"const": "snumat-trim-v1"}, "adapter_version": {"const": 1}, "files": {"type": "array", "maxItems": 18, "items": FINGERPRINT_FILE_SCHEMA}}
FINGERPRINT_SCHEMA = {"type": "object", "additionalProperties": False, "required": list(FINGERPRINT_PROPERTIES), "properties": FINGERPRINT_PROPERTIES}
PREVIEW_PROPERTIES = {"schema_version": {"const": 1}, "operation_id": {"type": "string", "format": "uuid"}, "source_id": SOURCE, "adapter_id": {"const": "snumat-trim-v1"}, "adapter_version": {"const": 1}, "base_generation": {"type": ["string", "null"]}, "source_fingerprints": FINGERPRINT_SCHEMA, **{key: INTEGER for key in ("discovered", "accepted", "rejected", "added", "changed", "unchanged", "missing")}, "fields": {"type": "array", "items": TEXT}, "units": {"type": "object", "additionalProperties": {"type": ["string", "null"]}}, "unavailable_outputs": {"type": "array", "items": TEXT}, "diagnostics": {"type": "array", "items": {"type": "object"}}, "storage_bytes": {"type": "object", "additionalProperties": INTEGER}, "plan_sha256": {"oneOf": [HASH, NULL]}, "state": {"enum": ["ready", "failed", "cancelled"]}}
EXTERNAL_PREVIEW_SCHEMA = {"type": "object", "additionalProperties": False, "required": list(PREVIEW_PROPERTIES), "properties": PREVIEW_PROPERTIES}
REPORT_PROPERTIES = {"schema_version": {"const": 1}, "operation_id": {"type": "string", "format": "uuid"}, "state": {"enum": ["committed", "failed", "cancelled"]}, "generation": {"type": ["string", "null"]}, "source_id": SOURCE, **{key: INTEGER for key in ("added", "changed", "skipped", "rejected", "missing")}, "issues": {"type": "array", "items": {"type": "object"}}, "plan_sha256": HASH}
EXTERNAL_REPORT_SCHEMA = {"type": "object", "additionalProperties": False, "required": list(REPORT_PROPERTIES), "properties": REPORT_PROPERTIES}
SCHEMAS = {"reference": EXTERNAL_REFERENCE_SCHEMA, "locator": EXTERNAL_LOCATOR_SCHEMA, "record": EXTERNAL_RECORD_SCHEMA, "query": EXTERNAL_QUERY_SCHEMA, "preview": EXTERNAL_PREVIEW_SCHEMA, "report": EXTERNAL_REPORT_SCHEMA}


def validate_external_document(document: JSONObject, kind: str) -> None:
    """Validate closed document of kind without I/O or mutation.

    document is an external reference, locator, record, query, preview or report.
    Return None for valid structure, identity, decimal precision and predicates.
    Raise ValueError for unsupported kind, unknown fields, malformed quantities,
    unsafe locators, incompatible units or evidence/availability disagreement.
    """
    if kind not in SCHEMAS or not isinstance(document, dict):
        raise ValueError("external: invalid discriminator")
    try:
        Draft202012Validator(SCHEMAS[kind]).validate(document)
        json.dumps(document, allow_nan=False)
    except Exception as error:
        raise ValueError("external: " + str(error).splitlines()[0]) from error
    pending = [document]
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            for key in ("schema_version", "adapter_version"):
                if key in node and (type(node[key]) is not int or node[key] != 1):
                    raise ValueError("external: version must be integer 1")
            if set(node) == set(LOCATOR_PROPERTIES):
                for path in (node["relative_path"], node["member_path"]):
                    if not path or "\\" in path or any(part in ("", ".", "..") or os.path.isreserved(part) or re.search(r'[<>:"|?*\x00-\x1f]', part) or part.endswith((".", " ")) for part in path.split("/")):
                        raise ValueError("external: unsafe evidence path")
                if node["json_pointer"] is not None:
                    if node["json_pointer"] and not node["json_pointer"].startswith("/") or node["line_start"] is not None or node["line_end"] is not None:
                        raise ValueError("external: invalid JSON locator")
                elif node["line_start"] is None and node["line_end"] is not None or node["line_start"] is not None and (type(node["line_start"]) is not int or type(node["line_end"]) is not int or node["line_end"] < node["line_start"]):
                    raise ValueError("external: invalid text locator")
                if kind == "record" and node["source_id"] != document["source_id"]:
                    raise ValueError("external: mismatched source owner")
            if set(node) == set(QUANTITY_PROPERTIES):
                if node["value"] is not None:
                    _bounded_decimal(node["value"])
                if (node["value"] is None) != (node["availability"] == "unavailable") or node["availability"] == "available" and (node["locator"] is None or node["raw"] is None):
                    raise ValueError("external: quantity availability/evidence mismatch")
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)
    if kind == "query":
        if document["text"] is not None and (not document["text"].strip() or "\0" in document["text"]):
            raise ValueError("external: invalid text")
        for clause in document["clauses"]:
            datatype, canonical = FIELDS[clause["field"]]
            operator, value, unit = clause["operator"], clause["value"], clause["unit"]
            if operator == "unknown":
                if value is not None or unit is not None:
                    raise ValueError("external: unknown requires null value/unit")
                continue
            if datatype == "text":
                if operator != "eq" or not isinstance(value, str) or not value or unit is not None:
                    raise ValueError("external: invalid text predicate")
            elif datatype == "elements":
                if operator not in ("contains_all", "contains_any") or not isinstance(value, list) or not value or any(not isinstance(v, str) or re.fullmatch("[A-Z][a-z]?", v) is None for v in value) or len(set(value)) != len(value) or unit is not None:
                    raise ValueError("external: invalid elements predicate")
            else:
                numbers = [value] if operator == "eq" else value
                if operator not in ("eq", "between") or not isinstance(numbers, list) or operator == "between" and (len(numbers) != 2 or numbers == [None, None]):
                    raise ValueError("external: invalid numeric bounds")
                for number in numbers:
                    if number is None and operator == "between":
                        continue
                    parsed = _bounded_decimal(number)
                    if datatype == "integer" and parsed != parsed.to_integral_value():
                        raise ValueError("external: integer predicate must be integral")
                if canonical in ("1", "ions", "vacancies/ion"):
                    if unit not in (None, canonical):
                        raise ValueError("external: invalid scalar unit")
                elif unit not in UNITS or UNITS[unit][0] != UNITS[canonical][0]:
                    raise ValueError("external: incompatible unit")
                if operator == "between" and all(v is not None for v in value) and Fraction(_bounded_decimal(value[0])) > Fraction(_bounded_decimal(value[1])):
                    raise ValueError("external: reversed bounds")
    if kind == "record":
        if document["case_id"].split("_")[0] != document["target_id"]:
            raise ValueError("external: target/case identity conflict")
        count = len(document["inputs"]["layers"][0]["composition"])
        if any(len(document["inputs"][key]) != count for key in ("displacement_eV", "lattice_binding_eV", "surface_binding_eV")):
            raise ValueError("external: target energy cardinality mismatch")
        quantities = [(document["inputs"][key], unit) for key, unit in (("ion_mass_amu", "amu"), ("energy_keV", "keV"), ("incidence_angle_deg", "deg"), ("compound_correction", "1"))]
        quantities.extend((document["planned"][key], unit) for key, unit in PLANNED_UNITS.items())
        quantities.extend((document["estimates"][key], unit) for key, unit in ESTIMATE_UNITS.items())
        for layer in document["inputs"]["layers"]:
            quantities.extend(((layer["thickness_A"], "A"), (layer["density_g_cm3"], "g/cm3")))
            for component in layer["composition"]:
                quantities.extend(((component["mass_amu"], "amu"), (component["stoichiometry"], "1")))
        for key in ("displacement_eV", "lattice_binding_eV", "surface_binding_eV"):
            quantities.extend((value, "eV") for value in document["inputs"][key])
        if any(value["unit"] != unit for value, unit in quantities):
            raise ValueError("external: field quantity unit mismatch")
        for key, unit in SUMMARY_UNITS.items():
            value = document["outputs"]["summary"][key]
            if value["unit"] != unit or value["value"] is not None and (_bounded_decimal(value["value"]) < 0 or unit == "ions" and _bounded_decimal(value["value"]) != _bounded_decimal(value["value"]).to_integral_value()):
                raise ValueError("external: summary count/unit invalid")


def compile_external_query(query: JSONObject) -> JSONObject:
    """Validate query once and return exact canonical internal clauses and text terms.

    query is a closed external query. Return a new internal JSON object without
    I/O or mutation. Invalid fields, bounds or units raise ValueError.
    """
    validate_external_document(query, "query")
    clauses = deepcopy(query["clauses"])
    for clause in clauses:
        datatype, unit = FIELDS[clause["field"]]
        if datatype not in ("number", "integer") or clause["operator"] == "unknown":
            continue
        values = [clause["value"]] if clause["operator"] == "eq" else clause["value"]
        converted = []
        for value in values:
            if value is None:
                converted.append(None)
                continue
            number = Fraction(_bounded_decimal(value))
            if unit not in ("1", "ions", "vacancies/ion"):
                number *= Fraction(10) ** (UNITS[clause["unit"]][1] - UNITS[unit][1])
            converted.append(str(number.numerator) + "/" + str(number.denominator))
        clause["value"] = converted[0] if clause["operator"] == "eq" else converted
        clause["unit"] = unit
    return {"schema_version": 1, "text_terms": [] if query["text"] is None else [term.casefold() for term in query["text"].split()], "revision_scope": query["revision_scope"], "clauses": clauses}


def project_external_record(record: JSONObject) -> JSONObject:
    """Extract exact searchable fields and folded text from an already validated record.

    record supplies immutable inputs, plans, estimates and summaries. Return a
    new projection without I/O, mutation or another schema walk. Invalid internal
    quantities raise ValueError; missing validated fields raise KeyError.
    """
    fields = {}
    for field, (datatype, unit) in FIELDS.items():
        if field == "inputs.target_elements":
            value = [entry["symbol"] for entry in record["inputs"]["layers"][0]["composition"]]
        elif field.startswith("inputs.layer."):
            value = record["inputs"]["layers"][0][field.rsplit(".", 1)[1]]
        elif field.startswith("outputs."):
            value = record["outputs"]["summary"][field.split(".", 1)[1]]
        else:
            group, key = field.split(".", 1)
            value = record[group][key]
        actual_unit = value["unit"] if isinstance(value, dict) else unit
        actual = value["value"] if isinstance(value, dict) else value
        kind = "unknown" if actual is None else "number" if datatype in ("number", "integer") else datatype
        if kind == "number":
            number = Fraction(_bounded_decimal(str(actual)))
            if unit not in ("1", "ions", "vacancies/ion"):
                number *= Fraction(10) ** (UNITS[actual_unit][1] - UNITS[unit][1])
            actual = str(number.numerator) + "/" + str(number.denominator)
        fields[field] = {"kind": kind, "value": actual}
    text = " ".join((record["case_id"], record["target_id"], record["inputs"]["ion_symbol"], record["inputs"]["target_label"], json.dumps(record["planned"], ensure_ascii=False), json.dumps(record["provenance"], ensure_ascii=False))).casefold()
    return {"fields": fields, "text_folded": text}


def match_external_record(record: JSONObject, query: JSONObject) -> JSONObject:
    """Validate record/query and return exact matched flag and source witnesses.

    record and query are closed external documents. Return new matched/witnesses
    data without I/O or input mutation. Invalid envelopes raise ValueError.
    """
    validate_external_document(record, "record")
    return match_compiled_external_record(record, compile_external_query(query))


def match_compiled_external_record(record: JSONObject, compiled: JSONObject) -> JSONObject:
    """Match a validated record against trusted compiled predicates without validation.

    All clauses and literal case-insensitive text terms are ANDed. Physical
    quantities compare as exact rationals after decimal unit shifts. Unknown
    matches missing values only. Inputs remain unchanged; no I/O occurs.
    record is already validated; compiled comes from compile_external_query.
    Return matched/witnesses without I/O or input mutation. Bad internal values
    raise ValueError; caller-supplied unchecked compiled data is unsupported.
    """
    result = {"matched": False, "witnesses": []}
    text = " ".join((record["case_id"], record["target_id"], record["inputs"]["ion_symbol"], record["inputs"]["target_label"], json.dumps(record["planned"], ensure_ascii=False), json.dumps(record["provenance"], ensure_ascii=False))).casefold() if compiled["text_terms"] else ""
    if compiled["text_terms"] and not all(term in text for term in compiled["text_terms"]):
        return result
    for clause in compiled["clauses"]:
        field = clause["field"]
        datatype, canonical = FIELDS[field]
        if field == "inputs.target_elements":
            actual = [entry["symbol"] for entry in record["inputs"]["layers"][0]["composition"]]
            locator = record["provenance"]["input_fields"].get("/inputs/layers/0/composition")
        elif field.startswith("inputs.layer."):
            actual = record["inputs"]["layers"][0][field.rsplit(".", 1)[1]]
            locator = actual["locator"]
        elif field.startswith("outputs."):
            actual = record["outputs"]["summary"][field.split(".", 1)[1]]
            locator = actual["locator"]
        else:
            group, key = field.split(".", 1)
            actual = record[group][key]
            locator = actual.get("locator") if isinstance(actual, dict) else record["provenance"]["input_fields"].get("/" + field.replace(".", "/"))
        if isinstance(actual, dict):
            actual, unit, raw = actual["value"], actual["unit"], actual["raw"]
        else:
            unit, raw = canonical, actual
        operator, expected = clause["operator"], clause["value"]
        if operator == "unknown":
            matched = actual is None
        elif actual is None:
            matched = False
        elif datatype == "text":
            matched = actual == expected
        elif datatype == "elements":
            matched = set(expected) <= set(actual) if operator == "contains_all" else bool(set(expected) & set(actual))
        else:
            value = str(actual)
            if canonical not in ("1", "ions", "vacancies/ion"):
                value = convert_quantity(value, unit, clause["unit"])
            number = Fraction(_bounded_decimal(value))
            matched = number == Fraction(expected) if operator == "eq" else (expected[0] is None or number >= Fraction(expected[0])) and (expected[1] is None or number <= Fraction(expected[1]))
        if not matched:
            return result
        result["witnesses"].append({"field": field, "value": deepcopy(actual), "raw": deepcopy(raw), "unit": unit, "locator": deepcopy(locator)})
    result["matched"] = True
    return result
