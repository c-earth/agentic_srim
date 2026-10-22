"""Parse, edit, search and construct SR targets from preserved native and JSON materials."""

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict
from decimal import Decimal, localcontext
from fractions import Fraction
import csv
import hashlib
import json
from pathlib import Path
import re
from typing import Literal
from src.backend.config import Config
from src.backend.paths import ProjectPaths
from src.backend.resources import ResourceReference, _hash_file, _unique_object, register_resource, resolve_resource, verify_resource
from src.srim.cab.strengths import ElementKey, _SYMBOLS, _atomic_number, _normalize_entries
from src.srim.cab.constants import CAB_PRECISION, CAB_ROUNDING, SR_BONDS
from src.srim.common.types import JSONObject


def parse_atomdata(contents: bytes) -> JSONObject:
    """Return validated 92-row native contents with lexical fields and exact source spans.

    contents must be ASCII bytes with two header lines and ordered fixed element
    identities. Preserve header/footer and all original spelling. Perform no I/O;
    raise ValueError for encoding, row structure, identity or numeric constraints.
    """
    if not isinstance(contents, bytes):
        raise ValueError("ATOMDATA requires bytes")
    try:
        text = contents.decode("ascii")
    except UnicodeError as error:
        raise ValueError("ATOMDATA must be ASCII") from error
    lines = contents.splitlines(keepends=True)
    if len(lines) < 94:
        raise ValueError("ATOMDATA requires two headers and 92 element rows")
    fields = ("atomic_number", "symbol", "name", "isotope_mass_number", "isotope_mass_amu", "natural_mass_amu", "solid_density_g_cm3", "solid_atomic_density_cm3", "fermi_velocity", "sublimation_energy_eV", "gas_density_g_cm3", "gas_atomic_density_cm3")
    rows: list[JSONObject] = []
    offset = sum(map(len, lines[:2]))
    for number, line in enumerate(lines[2:94], 1):
        tokens = line.decode("ascii").split()
        if len(tokens) != 12 or re.fullmatch(r"[0-9]+", tokens[0]) is None or int(tokens[0]) != number or tokens[1] != _SYMBOLS[number - 1]:
            raise ValueError(f"Invalid ATOMDATA identity at row {number}")
        row: JSONObject = dict(zip(fields, tokens))
        row["atomic_number"] = number
        if re.fullmatch(r"[0-9]+", tokens[3]) is None or int(tokens[3]) < 1:
            raise ValueError("Isotope mass number must be a positive integer")
        row["isotope_mass_number"] = int(tokens[3])
        name = tokens[2]
        if not 1 <= len(name) <= 13 or any(ord(char) < 33 or ord(char) > 126 or char == '"' for char in name):
            raise ValueError("ATOMDATA name must be a printable ASCII token of at most 13 characters")
        for field in fields[4:]:
            value = row[field]
            if re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", value) is None:
                raise ValueError(f"Invalid ATOMDATA decimal: {field}")
            number_value = Decimal(value)
            if not number_value.is_finite() or number_value < 0 or (number_value == 0 and field != "sublimation_energy_eV"):
                raise ValueError(f"Invalid ATOMDATA range: {field}")
        row["provenance"] = {"source_line": number + 2, "source_span": {"offset": offset, "size_bytes": len(line), "sha256": hashlib.sha256(line).hexdigest()}}
        rows.append(row)
        offset += len(line)
    return {"schema_version": 1, "rows": rows, "source": {"format": "native", "encoding": "ascii", "text": text, "sha256": hashlib.sha256(contents).hexdigest(), "size_bytes": len(contents)}}


def parse_compounds(contents: bytes) -> JSONObject:
    """Detect contents' native or extended JSON format and return fresh validated records.

    Preserve exact source bytes through decoded source text and encoding. Perform
    no I/O; raise ValueError for invalid type, encoding, structure or numeric data.
    """
    if not isinstance(contents, bytes):
        raise ValueError("Compound data requires bytes")
    return _parse_json_compounds(contents) if contents.lstrip().startswith((b"{", b"[")) else _parse_native_compounds(contents)


def _parse_native_compounds(contents: bytes) -> JSONObject:
    """Parse native contents' starred records, CSV composition and 18 bond columns.

    Retain names, comments, source spans and blank lines without repairing bytes.
    Return ordered canonical records and original/effective mapping without I/O.
    Raise ValueError for malformed structure, duplicate elements or invalid values.
    """
    try:
        text = contents.decode("cp1252")
    except UnicodeError as error:
        raise ValueError("Invalid native compound encoding") from error
    lines = contents.splitlines(keepends=True)
    starts = [index for index, line in enumerate(lines) if line.startswith(b"*")]
    if not starts:
        raise ValueError("Native compounds require at least one starred record")
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    records: list[JSONObject] = []
    for ordinal, start in enumerate(starts, 1):
        end = starts[ordinal] if ordinal < len(starts) else len(lines)
        numerical = [line.decode("cp1252").strip() for line in lines[start + 1:end] if line.strip() and not line.startswith((b"$", b"#", b"!"))]
        if len(numerical) != 2:
            raise ValueError(f"Compound record {ordinal} requires composition and bonds")
        name_match = re.fullmatch(r'"([^"\r\n]*)"\s*,\s*(.+)', numerical[0])
        if name_match is None:
            raise ValueError("Native compound requires a quoted internal name")
        try:
            tokens = [name_match[1], *next(csv.reader([name_match[2]], skipinitialspace=True, strict=True))]
        except csv.Error as error:
            raise ValueError(f"Invalid compound CSV at record {ordinal}") from error
        tokens = [token.strip() for token in tokens]
        if len(tokens) < 3 or re.fullmatch(r"[0-9]+", tokens[2]) is None:
            raise ValueError("Invalid native compound element count")
        count = int(tokens[2])
        if not 1 <= count <= 8 or len(tokens) != 3 + 2 * count:
            raise ValueError("Native compound count must match 1 through 8 elements")
        mass = tokens[0].startswith("%")
        elements: list[JSONObject] = []
        for index in range(count):
            z = tokens[3 + 2 * index]
            if re.fullmatch(r"[0-9]+", z) is None:
                raise ValueError("Native compound atomic number must be an integer")
            elements.append({"atomic_number": int(z), "mass_proportion" if mass else "stoichiometry": tokens[4 + 2 * index]})
        bonds = numerical[1].split()
        if len(bonds) != 18:
            raise ValueError("Native compounds require 18 bond columns")
        bond_map = {f"{_SYMBOLS[left - 1]}{order}{_SYMBOLS[right - 1]}": value for (left, right, order), value in zip(SR_BONDS, bonds)}
        record = {"name": lines[start].decode("cp1252").strip()[1:].strip(), "composition_basis": "mass" if mass else "atomic", "density_g_cm3": tokens[1], "elements": elements, "bonds": bond_map}
        checked = _parse_json_compounds((json.dumps({"schema_version": 1, "records": [record]}, ensure_ascii=False) + "\n").encode("utf-8"))["records"][0]
        checked["record_index"] = ordinal
        span = contents[offsets[start]:offsets[end]]
        checked["provenance"] = {"format": "native", "original_index": ordinal, "raw_display_name": lines[start].decode("cp1252").rstrip("\r\n")[1:], "raw_internal_name": tokens[0], "display_name": record["name"], "internal_name": tokens[0][1:] if mass else tokens[0], "source_line": start + 1, "source_span": {"offset": offsets[start], "size_bytes": len(span), "sha256": hashlib.sha256(span).hexdigest()}}
        records.append(checked)
    return {"schema_version": 1, "records": records, "source": {"format": "native", "encoding": "cp1252", "text": text, "sha256": hashlib.sha256(contents).hexdigest(), "size_bytes": len(contents)}, "index_map": [{"original_index": number, "effective_index": number} for number in range(1, len(records) + 1)]}


def _parse_json_compounds(contents: bytes) -> JSONObject:
    """Decode exact extended JSON contents and return basis-aware ordered records and provenance.

    Validate names, positive density/amounts, unique elements and normalized bond
    counts. Mass records reject nonzero bonds. Preserve spelling and duplicate
    names. Perform no I/O; raise ValueError for schema, type or numeric failures.
    """
    try:
        text = contents.decode("utf-8")
        document = json.loads(text, object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError) as error:
        raise ValueError("Invalid extended compound JSON") from error
    if not isinstance(document, dict) or set(document) != {"schema_version", "records"} or type(document["schema_version"]) is not int or document["schema_version"] != 1 or not isinstance(document["records"], list):
        raise ValueError("Invalid extended compound schema")
    records: list[JSONObject] = []
    for index, record in enumerate(document["records"], 1):
        if not isinstance(record, dict) or set(record) != {"name", "composition_basis", "density_g_cm3", "elements", "bonds"}:
            raise ValueError("Invalid compound record fields")
        if not isinstance(record["name"], str) or not record["name"] or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in record["name"]):
            raise ValueError("Compound name must be nonempty without control characters")
        if record["composition_basis"] not in ("atomic", "mass"):
            raise ValueError("Compound basis must be atomic or mass")
        density = record["density_g_cm3"]
        if not isinstance(density, str):
            raise ValueError("Compound density must be a decimal string")
        _normalize_entries("core", {1: density})
        if Decimal(density) <= 0:
            raise ValueError("Compound density must be positive")
        if not isinstance(record["elements"], list) or not 1 <= len(record["elements"]) <= 118:
            raise ValueError("Compound elements must be a nonempty list")
        field = "stoichiometry" if record["composition_basis"] == "atomic" else "mass_proportion"
        seen: set[int] = set()
        for element in record["elements"]:
            if not isinstance(element, dict) or set(element) != {"atomic_number", field} or type(element["atomic_number"]) is not int:
                raise ValueError("Invalid compound element fields")
            z = _atomic_number(element["atomic_number"])
            if z in seen or not isinstance(element[field], str):
                raise ValueError("Duplicate compound element or non-string amount")
            seen.add(z)
            _normalize_entries("core", {z: element[field]})
            if Decimal(element[field]) <= 0:
                raise ValueError("Compound amounts must be positive")
        if not isinstance(record["bonds"], dict):
            raise ValueError("Compound bonds require an explicit mapping")
        normalized = _normalize_entries("bond", record["bonds"])
        if any(not isinstance(value, str) for value in record["bonds"].values()):
            raise ValueError("Bond counts must be decimal strings")
        if record["composition_basis"] == "mass" and any(Decimal(value) != 0 for value in normalized.values()):
            raise ValueError("Mass-based compounds cannot contain nonzero bonds")
        result = deepcopy(record)
        result["bonds"] = {f"{_SYMBOLS[left - 1]}{order}{_SYMBOLS[right - 1]}": value for (left, right, order), value in normalized.items()}
        result["record_index"] = index
        result["provenance"] = {"format": "json", "original_index": index, "raw_display_name": record["name"], "raw_internal_name": None, "display_name": record["name"], "internal_name": None, "source_line": None, "source_span": None}
        records.append(result)
    return {"schema_version": 1, "records": records, "source": {"format": "json", "encoding": "utf-8", "text": text, "sha256": hashlib.sha256(contents).hexdigest(), "size_bytes": len(contents)}, "index_map": [{"original_index": index, "effective_index": index} for index in range(1, len(records) + 1)]}


def apply_material_overrides(kind: Literal['atomdata', 'compound'], data: JSONObject, overrides: Sequence[JSONObject]) -> JSONObject:
    """Return a copied kind/data database after validated overrides without I/O.

    ATOMDATA set edits existing named fields only. Compound set/remove addresses
    original indices; append uses patch order. Preserve original/effective mapping.
    Raise ValueError for duplicate targets, malformed edits or prohibited removals.
    """
    if kind not in ("atomdata", "compound") or not isinstance(overrides, (list, tuple)):
        raise ValueError("Invalid material override kind or sequence")
    result = deepcopy(data)
    seen: set[int] = set()
    appended: list[JSONObject] = []
    replaced: dict[int, JSONObject | None] = {}
    for operation in overrides:
        if not isinstance(operation, dict) or operation.get("operation") not in ("set", "remove", "append"):
            raise ValueError("Invalid material operation")
        action = operation["operation"]
        expected = {"operation", "value"} if action == "append" else {"operation", "key", "value"} if action == "set" else {"operation", "key"}
        if set(operation) != expected:
            raise ValueError("Invalid material operation fields")
        if kind == "atomdata":
            if action != "set":
                raise ValueError("ATOMDATA permits only set operations")
            key = _atomic_number(operation["key"])
            if key > 92 or key in seen:
                raise ValueError("Duplicate or absent ATOMDATA identity")
            seen.add(key)
            fields = operation["value"]
            editable = set(result["rows"][key - 1]) - {"atomic_number", "symbol", "provenance"}
            if not isinstance(fields, dict) or not fields or not set(fields) <= editable:
                raise ValueError("Invalid ATOMDATA editable fields")
            if "isotope_mass_number" in fields and (type(fields["isotope_mass_number"]) is not int or fields["isotope_mass_number"] < 1):
                raise ValueError("Invalid isotope mass number")
            if any(not isinstance(value, str) for field, value in fields.items() if field != "isotope_mass_number"):
                raise ValueError("ATOMDATA lexical fields must be strings")
            result["rows"][key - 1].update(deepcopy(fields))
        else:
            if action != "append":
                key = operation["key"]
                if type(key) is not int or key not in range(1, len(data["records"]) + 1) or key in seen:
                    raise ValueError("Compound patch requires a unique original index")
                seen.add(key)
            if action == "remove":
                replaced[key] = None
            else:
                record = _parse_json_compounds(json.dumps({"schema_version": 1, "records": [operation["value"]]}, ensure_ascii=False).encode("utf-8"))["records"][0]
                record["provenance"]["original_index"] = None if action == "append" else key
                if action == "append":
                    appended.append(record)
                else:
                    replaced[key] = record
    if kind == "atomdata":
        _encode_atomdata(result)
    else:
        records: list[JSONObject] = []
        mapping: list[JSONObject] = []
        for index, original in enumerate(result["records"], 1):
            record = replaced.get(index, original)
            if record is None:
                mapping.append({"original_index": index, "effective_index": None})
                continue
            record["record_index"] = len(records) + 1
            records.append(record)
            mapping.append({"original_index": index, "effective_index": len(records)})
        for record in appended:
            record["record_index"] = len(records) + 1
            records.append(record)
            mapping.append({"original_index": None, "effective_index": len(records)})
        result["records"], result["index_map"] = records, mapping
    return result


def _encode_atomdata(data: JSONObject) -> bytes:
    """Encode validated data into native bytes preserving untouched lines and edited token spacing.

    Return ASCII bytes without I/O. Raise ValueError for missing/invalid rows or
    edits that change identities, lexical field types, order or numeric constraints.
    """
    if not isinstance(data, dict) or not isinstance(data.get("rows"), list) or len(data["rows"]) != 92:
        raise ValueError("ATOMDATA requires 92 rows")
    original = data["source"]["text"].encode("ascii")
    parsed = parse_atomdata(original)
    fields = ("atomic_number", "symbol", "name", "isotope_mass_number", "isotope_mass_amu", "natural_mass_amu", "solid_density_g_cm3", "solid_atomic_density_cm3", "fermi_velocity", "sublimation_energy_eV", "gas_density_g_cm3", "gas_atomic_density_cm3")
    lines = original.splitlines(keepends=True)
    for index, row in enumerate(data["rows"]):
        if not isinstance(row, dict) or set(row) != set(parsed["rows"][index]) or type(row["atomic_number"]) is not int or row["atomic_number"] != index + 1 or row["symbol"] != _SYMBOLS[index] or type(row["isotope_mass_number"]) is not int:
            raise ValueError("Invalid ATOMDATA row identity or fields")
        for field in fields[2:]:
            if field != "isotope_mass_number" and not isinstance(row[field], str):
                raise ValueError("ATOMDATA numeric/name fields must retain strings")
        if any(row[field] != parsed["rows"][index][field] for field in fields):
            pieces = re.split(rb"(\s+)", lines[index + 2])
            positions = [position for position, piece in enumerate(pieces) if piece and not piece.isspace()]
            for field, position in zip(fields, positions):
                value = str(row[field])
                if not value or any(char.isspace() for char in value):
                    raise ValueError("ATOMDATA fields must be single tokens")
                try:
                    pieces[position] = value.encode("ascii")
                except UnicodeError as error:
                    raise ValueError("ATOMDATA fields must be ASCII") from error
            lines[index + 2] = b"".join(pieces)
    result = b"".join(lines)
    parse_atomdata(result)
    return result


def _encode_compounds(data: JSONObject) -> bytes:
    """Return canonical extended JSON bytes from data retaining source basis and quantities.

    Strip internal provenance/index fields and validate the external records before
    returning bytes. Perform no I/O; raise ValueError for invalid effective records.
    """
    if not isinstance(data, dict) or not isinstance(data.get("records"), list):
        raise ValueError("Expected parsed compound records")
    fields = ("name", "composition_basis", "density_g_cm3", "elements", "bonds")
    document = {"schema_version": 1, "records": [{field: record[field] for field in fields} for record in data["records"]]}
    contents = (json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    _parse_json_compounds(contents)
    return contents


def search_compounds(data: JSONObject, *, name: str | None = None, name_mode: Literal['exact', 'contains'] = 'exact', composition: Mapping[ElementKey, str] | Sequence[ElementKey] | None = None, composition_mode: Literal['contains', 'elements', 'ratio'] = 'contains', atomdata: JSONObject | None = None) -> list[JSONObject]:
    """Return independent data records matching name AND composition filters in file order.

    name_mode selects case-sensitive exact or casefold partial display/internal
    names. composition_mode selects contains/elements/ratio; ratio mappings use
    exact atomic proportions and atomdata masses for mass records. Empty matches
    return an empty list. Invalid filters/missing required masses raise ValueError.
    Perform no I/O and never normalize stored quantities.
    """
    if name_mode not in ("exact", "contains") or composition_mode not in ("contains", "elements", "ratio") or (name is not None and not isinstance(name, str)):
        raise ValueError("Invalid compound search filter")
    if not isinstance(data, dict) or not isinstance(data.get("records"), list):
        raise ValueError("Expected parsed compound data")
    ratio: dict[int, str] = {}
    if composition is not None:
        if not isinstance(composition, (Mapping, list, tuple)) or not composition:
            raise ValueError("Composition search requires nonempty unique elements")
        for key in composition:
            z = _atomic_number(key)
            if z in ratio:
                raise ValueError("Duplicate composition search identity")
            value = composition[key] if isinstance(composition, Mapping) else "1"
            if not isinstance(value, str):
                raise ValueError("Search proportions require decimal strings")
            _normalize_entries("core", {z: value})
            if Decimal(value) <= 0:
                raise ValueError("Search proportions must be positive")
            ratio[z] = value
        if composition_mode == "ratio" and not isinstance(composition, Mapping):
            raise ValueError("Ratio search requires amounts")
    matches: list[JSONObject] = []
    for record in data["records"]:
        names = [record["name"], record["provenance"].get("internal_name")]
        if name is not None and not any(value is not None and (value == name if name_mode == "exact" else name.casefold() in value.casefold()) for value in names):
            continue
        elements = {element["atomic_number"] for element in record["elements"]}
        if ratio and (not set(ratio) <= elements if composition_mode == "contains" else set(ratio) != elements):
            continue
        if ratio and composition_mode == "ratio" and not _match_ratio(record, ratio, atomdata):
            continue
        matches.append(deepcopy(record))
    return matches


def _match_ratio(record: JSONObject, ratio: Mapping[int, str], atomdata: JSONObject | None) -> bool:
    """Compare record against positive atomic ratio exactly using supplied atomdata masses.

    Return False for an element-set or proportional mismatch. Use exact rational
    finite decimals, perform no I/O and raise ValueError for invalid amounts or
    missing required mass data rather than skipping a mass-based candidate.
    """
    field = "stoichiometry" if record["composition_basis"] == "atomic" else "mass_proportion"
    amounts = {element["atomic_number"]: Fraction(element[field]) for element in record["elements"]}
    if set(amounts) != set(ratio):
        return False
    if field == "mass_proportion":
        if atomdata is None:
            raise ValueError("Mass-based ratio search requires selected ATOMDATA")
        masses = {row["atomic_number"]: row["natural_mass_amu"] for row in atomdata["rows"]}
        for z in amounts:
            if z not in masses or Fraction(masses[z]) <= 0:
                raise ValueError("Missing positive natural mass for ratio search")
            amounts[z] /= Fraction(masses[z])
    reference = next(iter(amounts))
    return all(amounts[z] * Fraction(ratio[reference]) == amounts[reference] * Fraction(ratio[z]) for z in amounts)


def _atomic_composition(record: JSONObject, atomdata: JSONObject) -> list[JSONObject]:
    """Return record atomic counts using atomdata natural masses, preserving atomic spelling.

    Divide mass proportions in a local Decimal context, without I/O. Raise
    ValueError for nonzero mass-record bonds, missing masses or invalid quantities.
    """
    if record["composition_basis"] == "atomic":
        return deepcopy(record["elements"])
    if any(Decimal(value) != 0 for value in record["bonds"].values()):
        raise ValueError("Mass-based records cannot have nonzero bonds")
    masses = {row["atomic_number"]: row["natural_mass_amu"] for row in atomdata["rows"]}
    result: list[JSONObject] = []
    with localcontext() as context:
        context.prec, context.rounding = CAB_PRECISION, CAB_ROUNDING
        for element in record["elements"]:
            z = element["atomic_number"]
            if z not in masses or Decimal(masses[z]) <= 0:
                raise ValueError("Missing positive natural mass")
            result.append({"atomic_number": z, "stoichiometry": format(Decimal(element["mass_proportion"]) / Decimal(masses[z]), "f")})
    return result


def construct_sr_target(target: JSONObject, atomdata: JSONObject, compounds: JSONObject | None) -> JSONObject:
    """Construct target's numeric SR data, composition, bonds and provenance from effective materials.

    target is complete direct input or an indexed compound selector with explicit
    phase. atomdata supplies masses/names; compounds is required for record input.
    Return fresh target/composition/bonds/provenance without I/O. Reject unsupported
    Z, incomplete data, invalid phase or mass/bond scales with ValueError.
    """
    if not isinstance(target, dict) or target.get("source") not in ("direct", "compound") or target.get("phase") not in ("solid", "gas"):
        raise ValueError("Target requires source and explicit phase")
    expected = {"source", "phase", "record_index"} if target["source"] == "compound" else {"source", "phase", "density_g_cm3", "elements", "bonds"}
    if set(target) != expected:
        raise ValueError("Invalid target source fields")
    if target["source"] == "compound":
        index = target["record_index"]
        if compounds is None or type(index) is not int or not 1 <= index <= len(compounds["records"]):
            raise ValueError("Missing selected effective compound record")
        record = deepcopy(compounds["records"][index - 1])
        mapping = deepcopy(compounds["index_map"])
    else:
        record = _parse_json_compounds(json.dumps({"schema_version": 1, "records": [{"name": "Direct", "composition_basis": "atomic", **{field: target[field] for field in ("density_g_cm3", "elements", "bonds")}}]}).encode("utf-8"))["records"][0]
        mapping = []
    if any(element["atomic_number"] > 92 for element in record["elements"]):
        raise ValueError("SR target supports atomic numbers 1 through 92")
    composition = _atomic_composition(record, atomdata)
    rows = {row["atomic_number"]: row for row in atomdata["rows"]}
    elements: list[JSONObject] = []
    for element in composition:
        z = element["atomic_number"]
        if z not in rows:
            raise ValueError("Target element missing from effective ATOMDATA")
        elements.append({"atomic_number": z, "name": rows[z]["name"], "stoichiometry": format(Decimal(element["stoichiometry"]), "f"), "mass_amu": format(Decimal(rows[z]["natural_mass_amu"]), "f")})
    span = record["provenance"].get("source_span")
    external = {field: record[field] for field in ("name", "composition_basis", "density_g_cm3", "elements", "bonds")}
    digest = span["sha256"] if span is not None else hashlib.sha256((json.dumps(external, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")).hexdigest()
    provenance = {"source": target["source"], "record_index": target.get("record_index"), "original_record_index": record["provenance"].get("original_index") if target["source"] == "compound" else None, "record_name": record["name"] if target["source"] == "compound" else None, "composition_basis": record["composition_basis"], "original_elements": deepcopy(record["elements"]), "atomic_composition": deepcopy(composition), "masses": [{"atomic_number": element["atomic_number"], "mass_amu": element["mass_amu"]} for element in elements], "record_sha256": digest if target["source"] == "compound" else None, "mapping": mapping}
    return {"target": {"phase": target["phase"], "density_g_cm3": format(Decimal(record["density_g_cm3"]), "f"), "elements": elements}, "composition": composition, "bonds": [{"identifier": key, "count": value} for key, value in record["bonds"].items()], "provenance": provenance}


def create_material_variant(paths: ProjectPaths, config: Config, kind: Literal['atomdata', 'compound'], *, source: Path | str | bytes | JSONObject | None = None, base_version: str | None = None, overrides: Sequence[JSONObject] = ()) -> ResourceReference:
    """Publish a numbered material kind variant in paths using config time, source/base and overrides.

    Require exactly one local/native/JSON source or base_version, pin defaults once,
    validate and preserve original bytes for unchanged data. Edited compounds use
    canonical JSON. Return an immutable reference with source/edit provenance and
    no alias changes. Raise ValueError for invalid data and propagate I/O failures.
    """
    if kind not in ("atomdata", "compound") or (source is None) == (base_version is None):
        raise ValueError("Provide material kind and exactly source or base_version")
    base: ResourceReference | None = None
    if base_version is not None:
        if base_version == "default":
            from src.srim.resources.defaults import read_sr_defaults
            base = ResourceReference(**read_sr_defaults(paths)["resources"][kind])
        else:
            base = resolve_resource(paths, "material", kind, base_version)
        source = verify_resource(paths, base)
    if isinstance(source, Path):
        expected = _hash_file(source)
        contents = source.read_bytes()
        if (hashlib.sha256(contents).hexdigest(), len(contents)) != expected:
            raise ValueError("Material source changed while reading")
    elif isinstance(source, bytes):
        contents = source
    elif isinstance(source, str):
        contents = source.encode("ascii" if kind == "atomdata" else "utf-8")
    elif isinstance(source, dict) and kind == "compound":
        contents = (json.dumps(source, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    else:
        raise ValueError("Unsupported material source")
    data = parse_atomdata(contents) if kind == "atomdata" else parse_compounds(contents)
    effective = apply_material_overrides(kind, data, overrides)
    payload = contents if not overrides else _encode_atomdata(effective) if kind == "atomdata" else _encode_compounds(effective)
    source_kind = "version" if base is not None else "file" if isinstance(source, Path) else "inline"
    origin = asdict(base) if base is not None else str(source.absolute()) if isinstance(source, Path) else "inline"
    provenance = {"origin": "user", "source": {"kind": source_kind, "origin": origin, "sha256": hashlib.sha256(contents).hexdigest(), "size_bytes": len(contents)}, "base": asdict(base) if base is not None else None, "changes": list(overrides), "copied_entries": [], "baseline_id": None, "procedure": None}
    return register_resource(paths, config, "material", kind, None, payload, provenance=provenance)
