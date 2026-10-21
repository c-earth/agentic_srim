"""Validate native input-search records and evaluate exact scientific filters."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from fractions import Fraction
from datetime import datetime
import os
import re
import uuid

from src.srim.metadata import JSONObject, JSONValue


UNITS = {
    "eV": ("energy", -3), "keV": ("energy", 0), "MeV": ("energy", 3), "GeV": ("energy", 6),
    "A": ("length", -1), "nm": ("length", 0), "um": ("length", 3),
    "mm": ("length", 6), "cm": ("length", 7), "m": ("length", 9),
    "amu": ("mass", 0), "g/cm3": ("density", 0), "deg": ("angle", 0),
}
FIELDS = {
    "engine": ("text", None), "mode": ("text", None), "execution_status": ("text", None),
    "target_label": ("text", None), "projectile.atomic_number": ("integer", None),
    "projectile.mass_amu": ("number", "mass"), "energy": ("number", "energy"),
    "target.elements": ("elements", None), "target.composition": ("composition", None),
    "layer_count": ("integer", None), "layer.thickness": ("number", "length"),
    "layer.density": ("number", "density"), "incidence_angle": ("number", "angle"),
    "total_ions": ("integer", None), "random_seed": ("integer", None),
}
SHAPES = {
    "reference": {"kind", "id", "metadata_sha256", "input_inventory_sha256"},
    "file": {"owner", "path", "sha256", "size_bytes", "role"},
    "issue": {"simulation_id", "code", "field", "message"},
    "observation": {"field", "value", "unit", "evidence"},
    "record": {"schema_version", "reference", "origin", "execution_status", "inputs", "observations", "files", "issues"},
    "filters": {"schema_version", "clauses"},
    "result": {"schema_version", "generation", "total", "offset", "limit", "matches", "excluded"},
    "update-report": {"schema_version", "operation_id", "state", "generation", "attempted", "indexed", "skipped", "rejected", "issues", "completed_at"},
}


def validate_search_document(document: JSONObject, kind: str) -> None:
    """Validate document of kind recursively, returning None without side effects.

    Args:
        document: Exact native-search JSON envelope with no extra fields.
        kind: reference, file, issue, observation, record, filters, result or update-report.

    Returns:
        None when the envelope and all applicable nested invariants are valid.

    Raises:
        ValueError: An unknown kind, malformed field, quantity or invariant is found.
    """
    if not isinstance(kind, str) or kind not in SHAPES or not isinstance(document, dict) or set(document) != SHAPES[kind]:
        raise ValueError(f"{kind}: invalid document fields")
    if "schema_version" in document and (type(document["schema_version"]) is not int or document["schema_version"] != 1):
        raise ValueError(f"{kind}.schema_version: expected 1")
    for key in ("metadata_sha256", "input_inventory_sha256", "sha256"):
        if key in document and (not isinstance(document[key], str) or re.fullmatch(r"[0-9a-f]{64}", document[key]) is None):
            raise ValueError(f"{kind}.{key}: invalid SHA-256")
    if kind == "reference":
        if document["kind"] != "simulation" or not isinstance(document["id"], str) or re.fullmatch(r"srim_[0-9]{8}_[0-9]{6}_[1-9][0-9]*", document["id"]) is None:
            raise ValueError("reference: invalid native identity")
    elif kind == "file":
        validate_search_document(document["owner"], "reference")
        path = document["path"]
        if not isinstance(path, str) or not path or "\\" in path or any(part in ("", ".", "..") or os.path.isreserved(part) or re.search(r'[<>:"|?*\x00-\x1f]', part) or part.endswith((".", " ")) for part in path.split("/")):
            raise ValueError("file.path: unsafe relative path")
        if type(document["size_bytes"]) is not int or document["size_bytes"] < 0 or document["role"] not in ("input", "output", "other"):
            raise ValueError("file: invalid size or role")
    elif kind == "issue":
        if not isinstance(document["simulation_id"], str) or document["code"] not in ("unsupported", "malformed", "missing", "integrity", "conflicting", "stale", "ineligible", "indexing_error") or (document["field"] is not None and not isinstance(document["field"], str)) or not isinstance(document["message"], str):
            raise ValueError("issue: invalid fields")
    elif kind == "observation":
        if not isinstance(document["field"], str) or (document["unit"] is not None and (not isinstance(document["unit"], str) or document["unit"] not in UNITS)) or not isinstance(document["evidence"], list):
            raise ValueError("observation: invalid fields")
        for evidence in document["evidence"]:
            if not isinstance(evidence, dict) or set(evidence) != {"file", "json_pointer"} or (evidence["json_pointer"] is not None and (not isinstance(evidence["json_pointer"], str) or not evidence["json_pointer"].startswith("/"))):
                raise ValueError("observation.evidence: invalid locator")
            validate_search_document(evidence["file"], "file")
    elif kind == "record":
        validate_search_document(document["reference"], "reference")
        if document["origin"] != "native" or document["execution_status"] not in ("succeeded", "failed"):
            raise ValueError("record: invalid origin/status")
        for key, child in (("files", "file"), ("issues", "issue"), ("observations", "observation")):
            if not isinstance(document[key], list):
                raise ValueError(f"record.{key}: expected list")
            for entry in document[key]:
                validate_search_document(entry, child)
                if child == "file" and entry["owner"] != document["reference"]:
                    raise ValueError("record.files: mismatched owner")
        inputs = document["inputs"]
        keys = {"engine", "engine_family", "mode", "mode_code", "projectile", "energy", "target_label", "layers", "incidence_angle_degrees", "total_ions", "random_seed", "events"}
        if not isinstance(inputs, dict) or set(inputs) != keys or inputs["engine"] not in ("sr", "trim") or inputs["engine_family"] not in (None, "2008", "2013") or not isinstance(inputs["mode"], str):
            raise ValueError("record.inputs: invalid envelope")
        if inputs["mode_code"] is not None and (type(inputs["mode_code"]) is not int or inputs["mode_code"] < 1):
            raise ValueError("record.inputs.mode_code: invalid code")
        if inputs["target_label"] is not None and not isinstance(inputs["target_label"], str):
            raise ValueError("record.inputs.target_label: invalid label")
        quantities: list[tuple[JSONValue, bool]] = [(inputs["incidence_angle_degrees"], False)]
        if inputs["projectile"] is not None:
            projectile = inputs["projectile"]
            if not isinstance(projectile, dict) or set(projectile) != {"atomic_number", "mass_amu"} or type(projectile["atomic_number"]) is not int or projectile["atomic_number"] < 1:
                raise ValueError("record.inputs.projectile: invalid identity")
            quantities.append((projectile["mass_amu"], True))
        for key in ("total_ions", "random_seed"):
            value = inputs[key]
            if value is not None and (type(value) is not int or value < (1 if key == "total_ions" else 0)):
                raise ValueError(f"record.inputs.{key}: invalid integer")
        energy = inputs["energy"]
        if energy is not None:
            if not isinstance(energy, dict) or set(energy) != {"kind", "values_keV", "minimum_keV", "maximum_keV"} or energy["kind"] not in ("single", "list", "range") or not isinstance(energy["values_keV"], list):
                raise ValueError("record.inputs.energy: invalid envelope")
            if energy["kind"] == "range":
                if energy["values_keV"] or energy["minimum_keV"] is None or energy["maximum_keV"] is None:
                    raise ValueError("record.inputs.energy: invalid range")
                quantities.extend([(energy["minimum_keV"], True), (energy["maximum_keV"], True)])
            elif not energy["values_keV"] or energy["minimum_keV"] is not None or energy["maximum_keV"] is not None or (energy["kind"] == "single" and len(energy["values_keV"]) != 1):
                raise ValueError("record.inputs.energy: invalid values")
            quantities.extend((value, True) for value in energy["values_keV"])
        if not isinstance(inputs["layers"], list) or not inputs["layers"]:
            raise ValueError("record.inputs.layers: expected nonempty list")
        for index, layer in enumerate(inputs["layers"], 1):
            if not isinstance(layer, dict) or set(layer) != {"index", "label", "phase", "thickness_angstrom", "density_g_cm3", "components"} or type(layer["index"]) is not int or layer["index"] != index or layer["phase"] not in ("solid", "gas") or (layer["label"] is not None and not isinstance(layer["label"], str)) or not isinstance(layer["components"], list) or not layer["components"]:
                raise ValueError("record.inputs.layers: invalid layer")
            quantities.extend([(layer["thickness_angstrom"], True), (layer["density_g_cm3"], True)])
            for component in layer["components"]:
                if not isinstance(component, dict) or set(component) != {"atomic_number", "mass_amu", "atomic_proportion"} or type(component["atomic_number"]) is not int or component["atomic_number"] < 1:
                    raise ValueError("record.inputs.layers.components: invalid identity")
                quantities.extend([(component["mass_amu"], True), (component["atomic_proportion"], True)])
        if inputs["events"] is not None:
            if not isinstance(inputs["events"], list) or not inputs["events"]:
                raise ValueError("record.inputs.events: expected nonempty list")
            for event in inputs["events"]:
                if not isinstance(event, dict) or set(event) != {"id", "atomic_number", "energy_keV"} or not isinstance(event["id"], str) or type(event["atomic_number"]) is not int or event["atomic_number"] < 1:
                    raise ValueError("record.inputs.events: invalid event")
                quantities.append((event["energy_keV"], True))
        for value, positive in quantities:
            if value is not None:
                try:
                    if not isinstance(value, str) or re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", value) is None or not Decimal(value).is_finite() or Decimal(value) < 0 or (positive and Decimal(value) == 0):
                        raise ValueError("record.inputs: invalid decimal quantity")
                except InvalidOperation as error:
                    raise ValueError("record.inputs: invalid decimal") from error
        if energy is not None and energy["kind"] == "range" and Decimal(energy["minimum_keV"]) >= Decimal(energy["maximum_keV"]):
            raise ValueError("record.inputs.energy: unordered range")
    elif kind == "filters":
        if not isinstance(document["clauses"], list):
            raise ValueError("filters.clauses: expected list")
        for clause in document["clauses"]:
            if not isinstance(clause, dict) or set(clause) != {"field", "operator", "value", "unit", "layer"} or not isinstance(clause["field"], str) or clause["field"] not in FIELDS:
                raise ValueError("filters.clause: invalid field/envelope")
            field, operator, value, unit, layer = (clause[key] for key in ("field", "operator", "value", "unit", "layer"))
            category, dimension = FIELDS[field]
            layered = field in ("target.composition", "layer.thickness", "layer.density")
            if (layered and not (layer in ("any", "all") or (type(layer) is int and layer > 0))) or (not layered and layer is not None):
                raise ValueError("filters.layer: explicit valid selector required")
            if operator == "unknown":
                if value is not None or unit is not None:
                    raise ValueError("filters.unknown: value/unit must be null")
                continue
            allowed = {"text": ("eq", "in"), "integer": ("eq", "in", "between"), "number": ("eq", "between"), "elements": ("contains_all", "contains_any"), "composition": ("eq",)}[category]
            if operator not in allowed or (dimension is None and unit is not None) or (dimension is not None and (not isinstance(unit, str) or unit not in UNITS or UNITS[unit][0] != dimension)):
                raise ValueError("filters: incompatible operator/unit")
            values = value if operator in ("in", "between", "contains_all", "contains_any") else [value]
            if not isinstance(values, list) or not values or (operator == "between" and (len(values) != 2 or values == [None, None])):
                raise ValueError("filters.value: invalid list/bounds")
            if category == "composition":
                if not isinstance(value, list) or not value:
                    raise ValueError("filters.composition: nonempty components required")
                masses: set[bool] = set()
                identities: set[tuple[int, Fraction | None]] = set()
                for component in value:
                    if not isinstance(component, dict) or set(component) != {"atomic_number", "mass_amu", "atomic_proportion"} or type(component["atomic_number"]) is not int or component["atomic_number"] < 1:
                        raise ValueError("filters.composition: invalid identity")
                    for key in ("mass_amu", "atomic_proportion"):
                        token = component[key]
                        if token is None and key == "mass_amu":
                            continue
                        if not isinstance(token, str) or re.fullmatch(r"[+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", token) is None or not Decimal(token).is_finite() or Decimal(token) <= 0:
                            raise ValueError("filters.composition: invalid positive decimal")
                    masses.add(component["mass_amu"] is None)
                    identity = (component["atomic_number"], None if component["mass_amu"] is None else Fraction(component["mass_amu"]))
                    if identity in identities:
                        raise ValueError("filters.composition: duplicate identity")
                    identities.add(identity)
                if len(masses) != 1:
                    raise ValueError("filters.composition: mixed mass-specific/elemental query")
            else:
                for item in values:
                    if item is None and operator == "between":
                        continue
                    if category == "text" and (not isinstance(item, str) or not item):
                        raise ValueError("filters.value: expected string")
                    if category in ("integer", "elements") and (type(item) is not int or item < (0 if field == "random_seed" else 1)):
                        raise ValueError("filters.value: expected nonnegative/positive integer")
                    if category == "number":
                        try:
                            if not isinstance(item, str) or re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", item) is None or not Decimal(item).is_finite():
                                raise ValueError("filters.value: expected finite decimal string")
                        except InvalidOperation as error:
                            raise ValueError("filters.value: invalid decimal") from error
                if operator == "between" and all(item is not None for item in values) and Fraction(values[0]) > Fraction(values[1]):
                    raise ValueError("filters.value: unordered bounds")
                if field in ("engine", "execution_status") and any(item not in ({"sr", "trim"} if field == "engine" else {"succeeded", "failed"}) for item in values):
                    raise ValueError("filters.value: invalid engine/status")
    else:
        if document["generation"] is not None:
            try:
                uuid.UUID(document["generation"])
            except (ValueError, TypeError, AttributeError) as error:
                raise ValueError(f"{kind}.generation: invalid UUID") from error
        if kind == "result":
            if any(type(document[key]) is not int or document[key] < 0 for key in ("total", "offset", "limit")) or not 1 <= document["limit"] <= 1000:
                raise ValueError("result: invalid pagination")
            if not isinstance(document["matches"], list) or not isinstance(document["excluded"], list):
                raise ValueError("result: expected matches/excluded lists")
            for entry in document["matches"]:
                validate_search_document(entry, "record")
            for entry in document["excluded"]:
                validate_search_document(entry, "issue")
        elif kind == "update-report":
            if document["state"] not in ("succeeded", "partial", "failed") or not isinstance(document["operation_id"], str) or not document["operation_id"].startswith("update_"):
                raise ValueError("update-report: invalid state/operation")
            try:
                uuid.UUID(document["operation_id"].removeprefix("update_"))
                if not isinstance(document["completed_at"], str) or datetime.fromisoformat(document["completed_at"]).utcoffset() is None:
                    raise ValueError("Missing aware completion timestamp")
            except ValueError as error:
                raise ValueError("update-report.operation_id: invalid UUID") from error
            for key in ("attempted", "indexed", "skipped", "rejected"):
                if not isinstance(document[key], list) or any(not isinstance(item, str) or re.fullmatch(r"srim_[0-9]{8}_[0-9]{6}_[1-9][0-9]*", item) is None for item in document[key]) or document[key] != sorted(set(document[key])):
                    raise ValueError(f"update-report.{key}: invalid ID list")
            if not isinstance(document["issues"], list):
                raise ValueError("update-report.issues: expected list")
            for issue in document["issues"]:
                validate_search_document(issue, "issue")


def convert_quantity(value: str, source_unit: str, target_unit: str) -> str:
    """Return value exactly converted between source_unit and target_unit.

    Args:
        value: Finite decimal string, preserving its input significant digits.
        source_unit: Registered original dimensional unit.
        target_unit: Registered unit of the same dimension.

    Returns:
        Fixed-point decimal string without rounding or filesystem effects.

    Raises:
        ValueError: A quantity/unit is invalid or dimensions differ.
    """
    if not isinstance(value, str) or re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", value) is None or not isinstance(source_unit, str) or not isinstance(target_unit, str) or source_unit not in UNITS or target_unit not in UNITS or UNITS[source_unit][0] != UNITS[target_unit][0]:
        raise ValueError("quantity: invalid value or incompatible units")
    try:
        number = Decimal(value)
        if not number.is_finite():
            raise ValueError("quantity: nonfinite decimal")
    except InvalidOperation as error:
        raise ValueError("quantity: malformed decimal") from error
    parts = number.as_tuple()
    return format(Decimal((parts.sign, parts.digits, parts.exponent + UNITS[source_unit][1] - UNITS[target_unit][1])), "f")


def matches_filters(record: JSONObject, filters: JSONObject) -> bool:
    """Return whether record satisfies all exact filters without I/O or mutation.

    Args:
        record: Validated native SearchRecord, including event/layer inputs.
        filters: Filter envelope with explicit units/operators/selectors.

    Returns:
        True only when every clause matches, with one shared event witness.

    Raises:
        ValueError: Either envelope or a nested clause is malformed.
    """
    validate_search_document(record, "record")
    validate_search_document(filters, "filters")
    inputs = record["inputs"]
    witnesses = set(range(len(inputs["events"]))) if inputs["events"] is not None else None
    for clause in filters["clauses"]:
        field, operator, expected, unit, selector = (clause[key] for key in ("field", "operator", "value", "unit", "layer"))
        values: list[JSONValue] = []
        value_units: list[str | None] = []
        event_indices: list[int] = []
        if field in ("engine", "mode", "target_label", "total_ions", "random_seed"):
            values = [inputs[field]]
        elif field == "execution_status":
            values = [record[field]]
        elif field == "projectile.atomic_number" and inputs["events"] is not None:
            values = [event["atomic_number"] for event in inputs["events"]]
            event_indices = list(range(len(values)))
        elif field.startswith("projectile."):
            values = [None if inputs["projectile"] is None else inputs["projectile"][field.split(".")[1]]]
            value_units = ["amu"]
        elif field == "energy":
            energy = inputs["energy"]
            if energy is not None and energy["kind"] == "range":
                if operator == "unknown":
                    return False
                lower = Fraction(convert_quantity(energy["minimum_keV"], "keV", unit))
                upper = Fraction(convert_quantity(energy["maximum_keV"], "keV", unit))
                if operator == "eq":
                    if not lower <= Fraction(expected) <= upper:
                        return False
                elif (expected[0] is not None and upper < Fraction(expected[0])) or (expected[1] is not None and lower > Fraction(expected[1])):
                    return False
                continue
            values = energy["values_keV"] if energy is not None else [None]
            value_units = ["keV"] * len(values)
            if inputs["events"] is not None:
                event_indices = list(range(len(values)))
        elif field == "incidence_angle":
            values, value_units = [inputs["incidence_angle_degrees"]], ["deg"]
        elif field == "layer_count":
            values = [len(inputs["layers"]) if inputs["engine"] == "trim" else None]
        elif field == "target.elements":
            values = [sorted({component["atomic_number"] for layer in inputs["layers"] for component in layer["components"]})]
        else:
            layers = inputs["layers"] if selector in ("any", "all") else [layer for layer in inputs["layers"] if layer["index"] == selector]
            if not layers:
                values = [None]
            elif field == "target.composition":
                values = [layer["components"] for layer in layers]
            elif field == "layer.thickness":
                values, value_units = [layer["thickness_angstrom"] for layer in layers], ["A"] * len(layers)
            elif field == "layer.density":
                values, value_units = [layer["density_g_cm3"] for layer in layers], ["g/cm3"] * len(layers)
        outcomes: list[bool] = []
        for position, actual in enumerate(values):
            if operator == "unknown":
                outcomes.append(actual is None)
                continue
            if actual is None:
                outcomes.append(False)
                continue
            if field == "target.composition":
                elemental = all(component["mass_amu"] is None for component in expected)
                if not elemental and any(component["mass_amu"] is None for component in actual):
                    outcomes.append(False)
                    continue
                compositions: list[dict[tuple[int, Fraction | None], Fraction]] = []
                for components in (actual, expected):
                    composition: dict[tuple[int, Fraction | None], Fraction] = {}
                    for component in components:
                        identity = (component["atomic_number"], None if elemental else Fraction(component["mass_amu"]))
                        composition[identity] = composition.get(identity, Fraction(0)) + Fraction(component["atomic_proportion"])
                    total = sum(composition.values(), Fraction(0))
                    compositions.append({key: amount / total for key, amount in composition.items()})
                outcomes.append(compositions[0] == compositions[1])
            elif operator in ("contains_all", "contains_any"):
                outcomes.append(set(expected) <= set(actual) if operator == "contains_all" else bool(set(expected) & set(actual)))
            elif FIELDS[field][0] == "text":
                choices = expected if operator == "in" else [expected]
                outcomes.append(actual.casefold() in [choice.casefold() for choice in choices])
            else:
                amount = Fraction(convert_quantity(actual, value_units[position], unit)) if FIELDS[field][0] == "number" else Fraction(actual)
                if operator == "eq":
                    outcomes.append(amount == Fraction(expected))
                elif operator == "in":
                    outcomes.append(amount in [Fraction(choice) for choice in expected])
                else:
                    outcomes.append((expected[0] is None or amount >= Fraction(expected[0])) and (expected[1] is None or amount <= Fraction(expected[1])))
        if event_indices and operator != "unknown":
            witnesses &= {event_indices[index] for index, match in enumerate(outcomes) if match}
            if not witnesses:
                return False
        elif not (all(outcomes) if selector == "all" else any(outcomes)):
            return False
    return True
