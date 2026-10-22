"""Register raw electronic stopping defaults and immutable user variants."""

from collections.abc import Mapping
from dataclasses import asdict
import hashlib
from pathlib import Path
import shlex
from src.backend.config import Config
from src.backend.paths import ProjectPaths
from src.backend.resources import ResourceReference, _hash_file, register_resource, resolve_resource, verify_resource


def create_electronic_variant(
    paths: ProjectPaths,
    config: Config,
    *,
    source: Path | str | bytes | None = None,
    base_version: str | None = None,
    replacements: Mapping[int, str | bytes] | None = None,
) -> ResourceReference:
    """Create paths' next electronic vN using config time and one input mode.

    Supply source as a Path, ASCII str content, or opaque bytes, or supply both
    base_version and a
    nonempty replacements mapping of atomic numbers to complete ASCII rows.
    Verify base bytes before editing; return the new immutable resource reference.
    Write only new resource artifacts. Raise ValueError for mode, row, reference,
    or integrity errors, and OSError for missing files, links, or contention.
    """
    base: ResourceReference | None = None
    if source is not None:
        if base_version is not None or replacements is not None:
            raise ValueError("Source and row replacement modes are mutually exclusive")
        if isinstance(source, Path):
            expected = _hash_file(source)
            payload = source.read_bytes()
            if (hashlib.sha256(payload).hexdigest(), len(payload)) != expected:
                raise ValueError("Source changed while reading")
            origin, source_kind = str(source.absolute()), "file"
        elif isinstance(source, str):
            try:
                payload = source.encode("ascii")
            except UnicodeError as error:
                raise ValueError("Coefficient text must be ASCII") from error
            origin, source_kind = "inline", "inline"
        elif isinstance(source, bytes):
            payload = source
            origin, source_kind = "inline", "inline"
        else:
            raise ValueError("Source must be a Path, ASCII str or bytes")
        effective = payload
        changes: list[dict[str, object]] = []
    else:
        if base_version is None or not isinstance(replacements, Mapping) or not replacements:
            raise ValueError("Provide a source or a base version and nonempty replacements")
        if base_version == "default":
            from src.srim.resources.defaults import read_sr_defaults
            base = ResourceReference(**read_sr_defaults(paths)["resources"]["electronic"])
        else:
            base = resolve_resource(paths, "engine", "electronic", base_version)
        payload = verify_resource(paths, base).read_bytes()
        if (hashlib.sha256(payload).hexdigest(), len(payload)) != (base.sha256, base.size_bytes):
            raise ValueError("Base payload changed while reading")
        effective = _replace_rows(payload, replacements)
        origin, source_kind = asdict(base), "version"
        changes = [{"operation": "set", "key": list(key) if isinstance(key, tuple) else key, "value": value.decode("ascii") if isinstance(value, bytes) else value} for key, value in replacements.items()]
    provenance = {"origin": "user", "source": {"kind": source_kind, "origin": origin, "sha256": hashlib.sha256(payload).hexdigest(), "size_bytes": len(payload)}, "base": asdict(base) if base is not None else None, "changes": changes, "copied_entries": [], "baseline_id": None, "procedure": None}
    return register_resource(paths, config, "engine", "electronic", None, effective, provenance=provenance)


def _replace_rows(payload: bytes, replacements: Mapping[int, str | bytes]) -> bytes:
    """Return payload with replacements applied by atomic number, without writes.

    Require 92 ordered ASCII rows with 95 quote-aware fields. Mapping keys must
    be integers 1 through 92, excluding bool; values are matching complete rows
    as ASCII strings or bytes without CR/LF. Preserve all original line endings
    and untouched bytes. Raise ValueError for malformed inputs or an empty map.
    """
    if not isinstance(payload, bytes) or not isinstance(replacements, Mapping) or not replacements:
        raise ValueError("Expected payload bytes and nonempty row replacements")
    rows = payload.splitlines(keepends=True)
    if len(rows) != 92:
        raise ValueError("Expected exactly 92 element rows")
    for number, line in enumerate(rows, 1):
        row = line.removesuffix(b"\n").removesuffix(b"\r")
        if _row_atomic_number(row) != number:
            raise ValueError("Base rows must be in atomic-number order")
    for number, replacement in replacements.items():
        if type(number) is not int or not 1 <= number <= 92:
            raise ValueError("Replacement keys must be atomic numbers 1 through 92")
        if isinstance(replacement, str):
            try:
                replacement = replacement.encode("ascii")
            except UnicodeError as error:
                raise ValueError("Replacement rows must be ASCII") from error
        if not isinstance(replacement, bytes) or _row_atomic_number(replacement) != number:
            raise ValueError("Replacement row must match its atomic-number key")
        original = rows[number - 1]
        ending = b"\r\n" if original.endswith(b"\r\n") else b"\n" if original.endswith(b"\n") else b"\r" if original.endswith(b"\r") else b""
        rows[number - 1] = replacement + ending
    return b"".join(rows)


def _row_atomic_number(row: bytes) -> int:
    """Return row's third quote-aware field as an atomic number, without writes.

    Require ASCII bytes without CR/LF, exactly 95 fields, and an integer atomic
    number 1 through 92. Disable tokenization comments and interpret no other
    field meanings. Raise ValueError for invalid types, encoding, or structure.
    """
    if not isinstance(row, bytes) or b"\r" in row or b"\n" in row:
        raise ValueError("Expected a complete byte row without line endings")
    try:
        fields = shlex.split(row.decode("ascii"), comments=False)
        if len(fields) != 95:
            raise ValueError("Expected 95 fields per row")
        number = int(fields[2])
        if not 1 <= number <= 92:
            raise ValueError("Atomic number must be 1 through 92")
    except (UnicodeError, ValueError) as error:
        raise ValueError("Invalid electronic resource row") from error
    return number
