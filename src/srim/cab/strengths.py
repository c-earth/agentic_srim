"""Normalize, store, load, and update sparse versioned CAB strength dictionaries."""

from collections.abc import Mapping, Sequence
from dataclasses import asdict
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from typing import Literal
from src.backend.config import Config
from src.backend.paths import ProjectPaths
from src.backend.resources import ResourceReference, _hash_file, _unique_object, register_resource, resolve_resource, verify_resource
from src.srim.cab.constants import CAB_UNITS
from src.srim.common.types import JSONObject


type CABKind = Literal["core", "bond"]


type ElementKey = int | str


type BondKey = str | tuple[ElementKey, ElementKey, str]


type CABValue = int | float | str | Decimal


type CABDictionary = Mapping[ElementKey | BondKey, CABValue]


type CABSource = Path | str | bytes | CABDictionary


type CABData = dict[int, str] | dict[tuple[int, int, str], str]


_SYMBOLS = tuple("H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og".split())


def create_cab_variant(
    paths: ProjectPaths,
    config: Config,
    kind: CABKind,
    *,
    source: CABSource | None = None,
    base_version: str | None = None,
    overrides: Sequence[JSONObject] = (),
) -> ResourceReference:
    """Publish a frozen user kind dataset in paths using config time and source or base_version.

    source supplies local JSON bytes/text/Path or a strength mapping; base_version
    selects an existing dataset, with default pinned once. overrides applies ordered
    set/remove operations. Return a new immutable reference without changing defaults
    or recovering missing entries. Preserve copied entry provenance. Raise ValueError
    for invalid data/operations and propagate filesystem failures with retained artifacts.
    """
    if (source is None) == (base_version is None):
        raise ValueError("Provide exactly one source or base_version")
    base: ResourceReference | None = None
    copied: list[JSONObject] = []
    if base_version is not None:
        if base_version == "default":
            from src.srim.resources.defaults import read_sr_defaults
            base = ResourceReference(**read_sr_defaults(paths)["resources"][kind])
        else:
            base = resolve_resource(paths, "cab", kind, base_version)
        data = load_cab(paths, kind, base.version)
        payload = verify_resource(paths, base).read_bytes()
        if json.loads(payload, object_pairs_hook=_unique_object).get("origin") == "installation":
            from src.srim.cab.cache import CABEntryReference, load_cab_entry
            data = {}
            for entry_path in sorted(verify_resource(paths, base).parent.glob("entries/*/*.json")):
                checksum, size = _hash_file(entry_path)
                entry_reference = CABEntryReference(base, entry_path.parent.name, entry_path.stem, checksum, size)
                entry = load_cab_entry(paths, entry_reference)
                identifier = entry["entry_id"]
                if kind == "core":
                    key = int(identifier[1:])
                else:
                    left, right, order = identifier.split("-")
                    key = (int(left[1:]), int(right[1:]), {"s": "-", "d": "=", "t": "#"}[order])
                if key in data:
                    raise ValueError("Duplicate available default strength identity")
                data[key] = entry["value"]
                copied.append(asdict(entry_reference))
        origin: object = asdict(base)
        source_kind = "version"
    else:
        data = _read_source(kind, source)
        if isinstance(source, Path):
            expected = _hash_file(source)
            payload = source.read_bytes()
            if (hashlib.sha256(payload).hexdigest(), len(payload)) != expected:
                raise ValueError("CAB source changed while snapshotting")
            data = _decode_payload(kind, payload)
            origin = str(source.absolute())
            source_kind = "file"
        else:
            payload = source.encode("utf-8") if isinstance(source, str) else source if isinstance(source, bytes) else _encode_payload(kind, data)
            origin = "inline"
            source_kind = "inline"
    effective, removed = _apply_cab_overrides(kind, data, overrides)
    provenance = {
        "origin": "user",
        "source": {"kind": source_kind, "origin": origin, "sha256": hashlib.sha256(payload).hexdigest(), "size_bytes": len(payload)},
        "base": asdict(base) if base is not None else None,
        "changes": list(overrides), "copied_entries": copied, "baseline_id": None, "procedure": None,
    }
    return register_resource(paths, config, "cab", kind, None, _encode_payload(kind, effective), provenance=provenance)


def load_cab(paths: ProjectPaths, kind: CABKind, version: str) -> CABData:
    """Return paths' verified kind/version available strengths as a fresh decimal-string mapping.

    Pin default once, verify frozen payload or all published calculated entries and
    perform no recovery or data writes. Raise ValueError for malformed/corrupt data
    and propagate filesystem/lock failures. Future lazy entries do not modify this result.
    """
    _normalize_entries(kind, {})
    if version == "default":
        from src.srim.resources.defaults import read_sr_defaults
        reference = ResourceReference(**read_sr_defaults(paths)["resources"][kind])
    else:
        reference = resolve_resource(paths, "cab", kind, version)
    payload_path = verify_resource(paths, reference)
    payload = payload_path.read_bytes()
    if (hashlib.sha256(payload).hexdigest(), len(payload)) != (reference.sha256, reference.size_bytes):
        raise ValueError("CAB payload changed while reading")
    document = json.loads(payload, object_pairs_hook=_unique_object)
    if isinstance(document, dict) and document.get("origin") == "installation":
        from src.srim.cab.cache import CABEntryReference, load_cab_entry
        result: CABData = {}
        for entry_path in sorted(payload_path.parent.glob("entries/*/*.json")):
            checksum, size = _hash_file(entry_path)
            entry = load_cab_entry(paths, CABEntryReference(reference, entry_path.parent.name, entry_path.stem, checksum, size))
            identifier = entry["entry_id"]
            if kind == "core":
                key = int(identifier[1:])
            else:
                left, right, order = identifier.split("-")
                key = (int(left[1:]), int(right[1:]), {"s": "-", "d": "=", "t": "#"}[order])
            if key in result:
                raise ValueError("Multiple calculated entries identify one CAB strength")
            result.update(_normalize_entries(kind, {key: entry["value"]}))
        return result
    return _decode_payload(kind, payload)


def _atomic_number(element: ElementKey) -> int:
    """Return element's atomic number for an integer or standard symbol.

    Accept integers 1 through 118 or case-sensitive standard symbols; exclude
    bool and other types. Raise ValueError for unsupported elements. No writes
    or other side effects occur.
    """
    if type(element) is int and 1 <= element <= 118:
        return element
    if isinstance(element, str) and element in _SYMBOLS:
        return _SYMBOLS.index(element) + 1
    raise ValueError(f"Unsupported element: {element!r}")


def _normalize_entries(kind: CABKind, entries: CABDictionary) -> CABData:
    """Return fresh normalized entries for kind core or bond, without writes.

    Core keys are elements; bond keys are symbol strings or endpoint/type tuples.
    Sort bond endpoints, reject equivalent duplicates, and preserve nonnegative
    finite decimal values as strings, excluding bool. Empty mappings are valid.
    Raise ValueError for invalid kind, entries, keys, or values.
    """
    if kind not in ("core", "bond") or not isinstance(entries, Mapping):
        raise ValueError("Expected core/bond kind and a mapping")
    result: CABData = {}
    for key, value in entries.items():
        if kind == "core":
            canonical = _atomic_number(key)
        else:
            if isinstance(key, str):
                match = re.fullmatch(r"([A-Z][a-z]?)([-=#])([A-Z][a-z]?)", key)
                if match is None:
                    raise ValueError("Invalid bond string")
                first, bond, second = match.groups()
            elif isinstance(key, tuple) and len(key) == 3:
                first, second, bond = key
            else:
                raise ValueError("Expected a bond string or endpoint/type tuple")
            if not isinstance(bond, str) or bond not in ("-", "=", "#"):
                raise ValueError("Unsupported bond type")
            left, right = sorted((_atomic_number(first), _atomic_number(second)))
            canonical = (left, right, bond)
        if canonical in result:
            raise ValueError("Duplicate equivalent CAB key")
        if type(value) not in (int, float, str, Decimal):
            raise ValueError("CAB strengths must be finite nonnegative decimals")
        spelling = value if isinstance(value, str) else str(value)
        if re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", spelling) is None:
            raise ValueError("Invalid CAB decimal spelling")
        try:
            number = Decimal(spelling)
        except InvalidOperation as error:
            raise ValueError("Invalid CAB decimal") from error
        if not number.is_finite() or number < 0:
            raise ValueError("CAB strengths must be finite and nonnegative")
        result[canonical] = spelling
    return result


def _decode_payload(kind: CABKind, payload: bytes) -> CABData:
    """Return normalized kind strengths from strict legacy numeric or new string payload bytes.

    Require exact schemas, units, identities and unique entries. Parse legacy
    numeric tokens directly as Decimal without binary-float conversion. Perform no
    I/O; raise ValueError for malformed encoding, duplicate fields or invalid values.
    """
    _normalize_entries(kind, {})
    if not isinstance(payload, bytes):
        raise ValueError("Expected UTF-8 JSON bytes")
    try:
        fields = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object, parse_float=Decimal)
    except (UnicodeError, ValueError) as error:
        raise ValueError("Invalid CAB JSON") from error
    if not isinstance(fields, dict) or type(fields.get("schema_version")) is not int or fields["schema_version"] not in (1, 2):
        raise ValueError("Invalid CAB schema")
    expected = {"schema_version", "entries"} if fields["schema_version"] == 1 else {"schema_version", "kind", "units", "entries"}
    if set(fields) != expected or not isinstance(fields["entries"], list):
        raise ValueError("Invalid CAB fields")
    if fields["schema_version"] == 2 and (fields["kind"] != kind or fields["units"] != CAB_UNITS):
        raise ValueError("CAB kind or units mismatch")
    result: CABData = {}
    for item in fields["entries"]:
        expected = {"element", "value"} if kind == "core" else {"elements", "bond", "value"}
        if not isinstance(item, dict) or set(item) != expected:
            raise ValueError("Invalid CAB entry fields")
        if fields["schema_version"] == 2 and not isinstance(item["value"], str):
            raise ValueError("New CAB strengths require decimal strings")
        if fields["schema_version"] == 1 and type(item["value"]) not in (int, Decimal):
            raise ValueError("Legacy CAB strengths require numeric values")
        if kind == "core":
            key = item["element"]
            if type(key) is not int:
                raise ValueError("JSON core elements must be integers")
        else:
            elements = item["elements"]
            if not isinstance(elements, list) or len(elements) != 2 or any(type(element) is not int for element in elements):
                raise ValueError("JSON bond elements must be two integers")
            if not isinstance(item["bond"], str):
                raise ValueError("JSON bond order must be a string")
            key = (elements[0], elements[1], item["bond"])
        normalized = _normalize_entries(kind, {key: item["value"]})
        if result.keys() & normalized.keys():
            raise ValueError("Duplicate equivalent CAB entries")
        result.update(normalized)
    return result


def _read_source(kind: CABKind, source: CABSource) -> CABData:
    """Return normalized kind data from source without filesystem writes.

    Accept Python mappings, regular link-free JSON Paths, UTF-8 JSON strings,
    or JSON bytes. Check path bytes against their hash after reading. Strings
    are content, never paths. Raise ValueError for invalid content/types or
    changed file bytes and OSError for filesystem failures or links.
    """
    _normalize_entries(kind, {})
    if isinstance(source, Mapping):
        return _normalize_entries(kind, source)
    if isinstance(source, Path):
        expected = _hash_file(source)
        payload = source.read_bytes()
        if (hashlib.sha256(payload).hexdigest(), len(payload)) != expected:
            raise ValueError("CAB source changed while reading")
    elif isinstance(source, str):
        try:
            payload = source.encode("utf-8")
        except UnicodeError as error:
            raise ValueError("Expected valid UTF-8 JSON content") from error
    elif isinstance(source, bytes):
        payload = source
    else:
        raise ValueError("Expected a Path, JSON str/bytes, or dictionary")
    return _decode_payload(kind, payload)


def _encode_payload(kind: CABKind, data: CABData) -> bytes:
    """Return canonical UTF-8 JSON bytes for normalized kind data, without writes.

    Validate data, sort core keys numerically or bonds by endpoints and character,
    and emit version-two decimal-string entries with two-space indentation and final newline.
    Raise ValueError for invalid kind, keys, or values.
    """
    normalized = _normalize_entries(kind, data)
    if kind == "core":
        entries = [{"element": key, "value": value} for key, value in sorted(normalized.items())]
    else:
        entries = [{"elements": [key[0], key[1]], "bond": key[2], "value": value} for key, value in sorted(normalized.items())]
    return (json.dumps({"schema_version": 2, "kind": kind, "units": CAB_UNITS, "entries": entries}, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def _apply_cab_overrides(kind: CABKind, data: CABData, overrides: Sequence[JSONObject]) -> tuple[CABData, list[str]]:
    """Copy kind/data, apply overrides and return effective strengths plus removed canonical IDs.

    overrides contains one set/remove per normalized key. Reject malformed or
    duplicate operations and removing absent entries with ValueError. Perform no I/O
    and preserve explicit removals even when their calculation fallback is zero.
    """
    result = _normalize_entries(kind, data)
    if not isinstance(overrides, (list, tuple)):
        raise ValueError("CAB overrides must be an operation sequence")
    seen: set[int | tuple[int, int, str]] = set()
    removed: list[str] = []
    for operation in overrides:
        if not isinstance(operation, dict) or operation.get("operation") not in ("set", "remove"):
            raise ValueError("Invalid CAB override operation")
        expected = {"operation", "key", "value"} if operation["operation"] == "set" else {"operation", "key"}
        if set(operation) != expected:
            raise ValueError("Invalid CAB override fields")
        value = operation.get("value", "0")
        if not isinstance(value, str):
            raise ValueError("Override strength must be a decimal string")
        key_input = operation["key"]
        if not isinstance(key_input, (int, str, tuple)) or (isinstance(key_input, tuple) and any(not isinstance(part, (int, str)) for part in key_input)):
            raise ValueError("Invalid CAB override key")
        normalized = _normalize_entries(kind, {key_input: value})
        key = next(iter(normalized))
        if key in seen:
            raise ValueError("Duplicate normalized CAB override")
        seen.add(key)
        if operation["operation"] == "set":
            result.update(normalized)
        else:
            if key not in result:
                raise ValueError("Cannot remove an absent CAB entry")
            del result[key]
            removed.append(f"z{key:03d}" if kind == "core" else f"z{key[0]:03d}-z{key[1]:03d}-" + {"-": "s", "=": "d", "#": "t"}[key[2]])
    return result, removed
