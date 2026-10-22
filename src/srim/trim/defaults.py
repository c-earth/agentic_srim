"""Manage immutable TRIM damage resources and pin installed native input settings."""

from collections.abc import Sequence
from copy import deepcopy
from dataclasses import asdict
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
from src.backend.config import Config
from src.backend.paths import ProjectPaths
from src.backend.records import _lock_parent
from src.backend.resources import ResourceReference, _hash_file, _read_json, _unique_object, _write_json, register_resource, resolve_resource, verify_resource
from src.srim.common.types import JSONObject
from src.srim.common.files import _read_regular_bytes
from src.srim.trim.inputs import ENERGIES, _decimal


def parse_trim_damage(contents: bytes) -> JSONObject:
    """Return validated 92-row damage JSON from contents without mutation or I/O.

    Require UTF-8, unique keys, ordered Z=1–92, nonnegative Disp/Latt strings
    and optional direct Surf. Preserve numeric spellings. Raise ValueError for
    malformed schemas, duplicate/missing rows and invalid numeric data.
    """
    if not isinstance(contents, bytes):
        raise ValueError("Damage resource requires bytes")
    try:
        document = json.loads(contents.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError) as error:
        raise ValueError("Invalid damage JSON") from error
    if not isinstance(document, dict) or set(document) != {"schema_version", "units", "rows"} or type(document["schema_version"]) is not int or document["schema_version"] != 1 or document["units"] != "eV" or not isinstance(document["rows"], list) or len(document["rows"]) != 92:
        raise ValueError("Damage resource requires schema_version 1, eV and 92 ordered rows")
    for z, row in enumerate(document["rows"], 1):
        if not isinstance(row, dict) or not {"atomic_number", *ENERGIES[:2]} <= set(row) or set(row) - {"atomic_number", *ENERGIES} or type(row["atomic_number"]) is not int or row["atomic_number"] != z:
            raise ValueError(f"Damage row {z}: invalid fields/identity")
        for field in set(row) - {"atomic_number"}:
            _decimal(row[field], f"damage[{z}].{field}", minimum=Decimal(0))
    return document


def apply_trim_damage_overrides(data: JSONObject, overrides: Sequence[JSONObject]) -> JSONObject:
    """Return independent damage data with explicit set/remove overrides applied.

    data is a complete table; overrides use [Z, energy_field] keys. Only optional
    Surf can be removed. Reject duplicates/invalid edits with ValueError; perform
    no I/O and never mutate data or overrides.
    """
    if not isinstance(overrides, (list, tuple)):
        raise ValueError("Damage overrides require a sequence")
    result = parse_trim_damage(json.dumps(data, allow_nan=False).encode("utf-8"))
    seen: set[tuple[int, str]] = set()
    for operation in overrides:
        if not isinstance(operation, dict) or operation.get("operation") not in ("set", "remove") or set(operation) != ({"operation", "key", "value"} if operation["operation"] == "set" else {"operation", "key"}):
            raise ValueError("Invalid damage override fields")
        key = operation["key"]
        if not isinstance(key, list) or len(key) != 2 or type(key[0]) is not int or not 1 <= key[0] <= 92 or not isinstance(key[1], str) or key[1] not in ENERGIES:
            raise ValueError("Damage override key requires [Z, energy_field]")
        identity = (key[0], key[1])
        if identity in seen:
            raise ValueError("Duplicate damage override")
        seen.add(identity)
        if operation["operation"] == "remove":
            if key[1] != "surface_binding_eV":
                raise ValueError("Only optional Surf can be removed")
            result["rows"][key[0] - 1].pop(key[1], None)
        else:
            _decimal(operation["value"], f"damage[{key[0]}].{key[1]}", minimum=Decimal(0))
            result["rows"][key[0] - 1][key[1]] = operation["value"]
    return result


def _encode_trim_damage(data: JSONObject) -> bytes:
    """Return validated data as canonical UTF-8 damage JSON without I/O.

    Require the exact damage schema; retain numeric strings/row order and use
    sorted keys, two-space indentation and final LF. Raise ValueError on errors.
    """
    validated = parse_trim_damage(json.dumps(data, allow_nan=False).encode("utf-8"))
    return (json.dumps(validated, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def create_trim_damage_variant(paths: ProjectPaths, config: Config, *, source: Path | str | bytes | JSONObject | None = None, base_version: str | None = None, overrides: Sequence[JSONObject] = ()) -> ResourceReference:
    """Publish one immutable damage version from source or base_version in paths.

    config supplies registration time. Source is an absolute safe Path, JSON
    text/bytes or object; base_version is default or vN. Snapshot/verify, apply
    overrides and return ResourceReference. Raise ValueError for mode/data/hash
    errors and propagate OSError with backend artifact notes. Never switch aliases.
    """
    if (source is None) == (base_version is None):
        raise ValueError("Provide exactly one damage source or base_version")
    base: ResourceReference | None = None
    if base_version is not None:
        base = ResourceReference(**read_trim_defaults(paths)["damage"]) if base_version == "default" else resolve_resource(paths, "material", "trim-damage", base_version)
        payload = _read_regular_bytes(verify_resource(paths, base))
        if (hashlib.sha256(payload).hexdigest(), len(payload)) != (base.sha256, base.size_bytes):
            raise ValueError("Damage base changed while reading")
        kind, origin = "version", asdict(base)
    elif isinstance(source, Path):
        if not source.is_absolute():
            raise ValueError("Damage source path must be absolute")
        expected = _hash_file(source)
        payload = _read_regular_bytes(source)
        if (hashlib.sha256(payload).hexdigest(), len(payload)) != expected:
            raise ValueError("Damage source changed while reading")
        kind, origin = "file", str(source)
    else:
        if isinstance(source, str):
            payload = source.encode("utf-8")
        elif isinstance(source, bytes):
            payload = source
        elif isinstance(source, dict):
            payload = (json.dumps(source, allow_nan=False, ensure_ascii=False) + "\n").encode("utf-8")
        else:
            raise ValueError("Invalid damage source type")
        kind, origin = "inline", "inline"
    effective = _encode_trim_damage(apply_trim_damage_overrides(parse_trim_damage(payload), overrides))
    provenance = {"origin": "user", "source": {"kind": kind, "origin": origin, "sha256": hashlib.sha256(payload).hexdigest(), "size_bytes": len(payload)}, "base": asdict(base) if base is not None else None, "changes": deepcopy(list(overrides)), "copied_entries": [], "baseline_id": None, "procedure": None}
    return register_resource(paths, config, "material", "trim-damage", None, effective, provenance=provenance)


def read_trim_defaults(paths: ProjectPaths) -> JSONObject:
    """Read paths' TRIM damage pointer and verify its immutable payload.

    Return a fresh exact schema-one document. Existing resource verification may
    create lock files; no pointer/data is written. Raise ValueError for corrupt
    identity/schema/hash and OSError for missing/unsafe files or lock failures.
    """
    document = _read_json(paths.resources / "trim-damage-defaults.json")
    if set(document) != {"schema_version", "damage"} or type(document["schema_version"]) is not int or document["schema_version"] != 1 or not isinstance(document["damage"], dict) or set(document["damage"]) != {"category", "name", "version", "sha256", "size_bytes"}:
        raise ValueError("Invalid TRIM damage-default pointer")
    reference = ResourceReference(**document["damage"])
    if reference.category != "material" or reference.name != "trim-damage" or not isinstance(reference.version, str) or re.fullmatch(r"v[1-9][0-9]*", reference.version) is None:
        raise ValueError("TRIM defaults must reference material/trim-damage/vN")
    payload = _read_regular_bytes(verify_resource(paths, reference))
    if (hashlib.sha256(payload).hexdigest(), len(payload)) != (reference.sha256, reference.size_bytes):
        raise ValueError("TRIM default payload changed")
    parse_trim_damage(payload)
    return document


def switch_trim_defaults(paths: ProjectPaths, config: Config, *, damage_version: str = "builtin") -> JSONObject:
    """Select builtin seed or existing damage_version as paths' TRIM default.

    config supplies version timestamp. Register/reuse the canonical seed when
    requested, then atomically switch the pointer under the resource lock only
    if its prior identity is unchanged. Return verified pointer; propagate
    ValueError/OSError and retain explicit versions on failure. SR aliases stay intact.
    """
    pointer = paths.resources / "trim-damage-defaults.json"
    prior = _hash_file(pointer) if os.path.lexists(pointer) else None
    if damage_version == "builtin":
        payload = _read_regular_bytes(Path(__file__).parent / "data" / "trim-damage-defaults.json")
        payload = _encode_trim_damage(parse_trim_damage(payload))
        digest = hashlib.sha256(payload).hexdigest()
        provenance = {"origin": "user", "source": {"kind": "recipe", "origin": "generated", "sha256": digest, "size_bytes": len(payload)}, "base": None, "changes": [], "copied_entries": [], "baseline_id": None, "procedure": {"method": "measured-trim-damage-defaults", "version": 1}}
        reference = register_resource(paths, config, "material", "trim-damage", None, payload, provenance=provenance, reuse_key=digest)
    else:
        if not isinstance(damage_version, str) or re.fullmatch(r"v[1-9][0-9]*", damage_version) is None:
            raise ValueError("Damage default must be builtin or vN")
        reference = resolve_resource(paths, "material", "trim-damage", damage_version)
        parse_trim_damage(_read_regular_bytes(verify_resource(paths, reference)))
    document = {"schema_version": 1, "damage": asdict(reference)}
    try:
        with _lock_parent(paths.resources):
            if (_hash_file(pointer) if os.path.lexists(pointer) else None) != prior:
                raise ValueError("TRIM damage default changed concurrently")
            _write_json(pointer, document)
        return read_trim_defaults(paths)
    except (ValueError, OSError) as error:
        error.add_note(f"TRIM default switch retained damage version {reference.version}")
        raise


def read_trim_installation(config: Config) -> tuple[JSONObject, dict[str, bytes]]:
    """Read config's installed TRIM identity and fixed fields into pinned bytes.

    Return profile and exact four-file source map after stable safe hash checks.
    Copy legacy Bragg, Reminders and stopping selector from installed TRIM.IN;
    derive family from Data/VERSION. No writes/execution. Raise ValueError for
    unparseable/changed/unknown-family data and OSError for unsafe/missing files.
    """
    if not isinstance(config.srim_root, Path) or not config.srim_root.is_absolute():
        raise ValueError("TRIM installation root must be absolute")
    files: dict[str, bytes] = {}
    identities: list[JSONObject] = []
    for name in ("TRIM.exe", "TRIM.IN", "Data/TRIM.cfg", "Data/VERSION"):
        path = config.srim_root / name
        expected = _hash_file(path)
        files[name] = _read_regular_bytes(path)
        if (hashlib.sha256(files[name]).hexdigest(), len(files[name])) != expected:
            raise ValueError(f"Installed TRIM file changed: {name}")
        identities.append({"path": name, "sha256": expected[0], "size_bytes": expected[1]})
    try:
        version = files["Data/VERSION"].decode("ascii").splitlines()[0].strip()
        match = re.fullmatch(r"SRIM-(2008|2013)\.[0-9]+", version)
        if match is None:
            raise ValueError("Installed TRIM family has no supported mode map")
        lines = files["TRIM.IN"].decode("cp1252").splitlines()
        ion = lines[2].split()
        calculation = lines[4].split()
        stopping = [line for line in lines if line.strip()][-1].split()
        if len(ion) != 7 or len(calculation) != 3 or len(stopping) != 1:
            raise ValueError("Installed TRIM.IN fixed records are unparseable")
        _decimal(ion[5], "installed.legacy_bragg")
        native = {"legacy_bragg": ion[5], "reminders": int(calculation[2]), "stopping_version": int(stopping[0])}
    except (IndexError, UnicodeError, ValueError) as error:
        raise ValueError(f"Invalid installed TRIM profile: {error}") from error
    if not files["TRIM.exe"]:
        raise ValueError("Installed TRIM executable is empty")
    for identity in identities:
        if _hash_file(config.srim_root / identity["path"]) != (identity["sha256"], identity["size_bytes"]):
            raise ValueError("Installation changed during TRIM profile collection")
    return {"schema_version": 1, "engine_family": match.group(1), "version_text": version, "native_fields": native, "files": identities}, files
