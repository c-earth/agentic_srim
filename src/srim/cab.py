"""Normalize, store, load, and update sparse versioned CAB strength dictionaries."""

from collections.abc import Mapping
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Literal

from src.backend.config import Config
from src.backend.paths import ProjectPaths
from src.backend.resources import ResourceReference, _hash_file, register_resource, resolve_resource, verify_resource


type CABKind = Literal["core", "bond"]
type ElementKey = int | str
type BondKey = str | tuple[ElementKey, ElementKey, str]
type CABDictionary = Mapping[ElementKey | BondKey, int | float]
type CABSource = Path | str | bytes | CABDictionary
type CABData = dict[int, float] | dict[tuple[int, int, str], float]

_SYMBOLS = tuple("H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og".split())


def register_cab_default(paths: ProjectPaths, config: Config, kind: CABKind, source: CABSource) -> ResourceReference:
    """Normalize source and register paths' cab/kind/default using config time.

    Source is a JSON Path, UTF-8 JSON str/bytes, or Python dictionary. Return
    the immutable reference; equivalent normalized defaults succeed repeatedly.
    Write resource artifacts only. Raise ValueError for invalid data/conflicts
    and OSError for filesystem failures, links, missing setup, or contention.
    """
    data = _read_source(kind, source)
    return register_resource(paths, config, "cab", kind, "default", _encode_payload(kind, data))


def create_cab_variant(
    paths: ProjectPaths,
    config: Config,
    kind: CABKind,
    *,
    source: CABSource | None = None,
    base_version: str | None = None,
    updates: CABDictionary | None = None,
) -> ResourceReference:
    """Create paths' next cab/kind vN using config time and exactly one mode.

    Supply source as JSON Path/str/bytes or a dictionary, or both base_version
    and nonempty updates. Updates add or replace entries without deletion or
    cross-dataset checks. Return an immutable reference, including for no-op
    updates. Write only new resource artifacts. Raise ValueError for invalid
    modes/data/integrity and OSError for filesystem failures or contention.
    """
    if source is not None:
        if base_version is not None or updates is not None:
            raise ValueError("Source and update modes are mutually exclusive")
        data = _read_source(kind, source)
    else:
        if base_version is None or not isinstance(updates, Mapping) or not updates:
            raise ValueError("Provide source or base_version and nonempty updates")
        normalized = _normalize_entries(kind, updates)
        data = load_cab(paths, kind, base_version)
        data.update(normalized)
    return register_resource(paths, config, "cab", kind, None, _encode_payload(kind, data))


def load_cab(paths: ProjectPaths, kind: CABKind, version: str) -> CABData:
    """Load paths' cab/kind/version as a fresh normalized Python dictionary.

    Validate kind, resolve and verify the resource, and check read bytes against
    its reference before decoding. Core keys are integers; bond keys are sorted
    integer endpoint tuples plus a bond character. No resource data is written.
    Raise ValueError for invalid data or integrity and propagate filesystem and
    lock errors as OSError; unknown versions raise ValueError.
    """
    _normalize_entries(kind, {})
    reference = resolve_resource(paths, "cab", kind, version)
    payload = verify_resource(paths, reference).read_bytes()
    if (hashlib.sha256(payload).hexdigest(), len(payload)) != (reference.sha256, reference.size_bytes):
        raise ValueError("CAB payload changed while reading")
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
    Sort bond endpoints, reject equivalent duplicates, and convert nonnegative
    finite int/float values to floats, excluding bool. Empty mappings are valid.
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
        if type(value) not in (int, float):
            raise ValueError("CAB strengths must be finite nonnegative numbers")
        try:
            number = float(value)
        except (ValueError, OverflowError) as error:
            raise ValueError("CAB strength cannot be represented as a float") from error
        if not math.isfinite(number) or number < 0:
            raise ValueError("CAB strengths must be finite and nonnegative")
        result[canonical] = number
    return result


def _decode_payload(kind: CABKind, payload: bytes) -> CABData:
    """Decode UTF-8 JSON payload into normalized kind-specific data, without writes.

    Require exact schema_version/entries fields and version one. Core entries
    have integer element/value fields; bond entries have two integer elements,
    bond, and value fields. Normalize reversed pairs. Reject duplicate object
    fields and equivalent entries, malformed structures, and invalid values
    with ValueError. Empty entry arrays are valid.
    """
    _normalize_entries(kind, {})
    if not isinstance(payload, bytes):
        raise ValueError("Expected UTF-8 JSON bytes")
    try:
        document = json.loads(payload.decode("utf-8"), object_pairs_hook=tuple)
    except (UnicodeError, ValueError) as error:
        raise ValueError("Invalid CAB JSON") from error
    if not isinstance(document, tuple):
        raise ValueError("Expected CAB JSON object")
    fields = dict(document)
    if len(fields) != len(document) or set(fields) != {"schema_version", "entries"} or type(fields["schema_version"]) is not int or fields["schema_version"] != 1 or not isinstance(fields["entries"], list):
        raise ValueError("Invalid CAB schema")
    result: CABData = {}
    for entry in fields["entries"]:
        if not isinstance(entry, tuple):
            raise ValueError("Expected CAB entry object")
        item = dict(entry)
        expected = {"element", "value"} if kind == "core" else {"elements", "bond", "value"}
        if len(item) != len(entry) or set(item) != expected:
            raise ValueError("Invalid or duplicate CAB entry fields")
        if kind == "core":
            key = item["element"]
            if type(key) is not int:
                raise ValueError("JSON core elements must be integers")
        else:
            elements = item["elements"]
            if not isinstance(elements, list) or len(elements) != 2 or any(type(element) is not int for element in elements):
                raise ValueError("JSON bond elements must be two integers")
            if not isinstance(item["bond"], str):
                raise ValueError("JSON bond type must be a string")
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
    and emit version-one entries with two-space indentation and final newline.
    Raise ValueError for invalid kind, keys, or values.
    """
    normalized = _normalize_entries(kind, data)
    if kind == "core":
        entries = [{"element": key, "value": value} for key, value in sorted(normalized.items())]
    else:
        entries = [{"elements": [key[0], key[1]], "bond": key[2], "value": value} for key, value in sorted(normalized.items())]
    return (json.dumps({"schema_version": 1, "entries": entries}, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
