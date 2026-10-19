"""Register raw nuclear stopping defaults and immutable user variants."""

from collections.abc import Mapping
import hashlib
from pathlib import Path

from src.backend.config import Config
from src.backend.paths import ProjectPaths
from src.backend.resources import ResourceReference, register_resource, resolve_resource, verify_resource


def register_nuclear_default(paths: ProjectPaths, config: Config) -> ResourceReference:
    """Copy config.srim_root/Data/SNUC03.dat to paths' nuclear default.

    Return its immutable reference, preserving exact bytes and existing identical
    registrations. Raise ValueError for invalid configuration or conflicts and
    OSError for missing setup, source files, links, contention, or access failures.
    Writes only resource registration artifacts; never modifies the installation.
    """
    if not isinstance(config.srim_root, Path) or not config.srim_root.is_absolute():
        raise ValueError("SRIM root must be an absolute Path")
    return register_resource(paths, config, "engine", "nuclear", "default", config.srim_root / "Data" / "SNUC03.dat")


def create_nuclear_variant(
    paths: ProjectPaths,
    config: Config,
    *,
    source: Path | str | bytes | None = None,
    base_version: str | None = None,
    replacements: Mapping[tuple[int, int], str | bytes] | None = None,
) -> ResourceReference:
    """Create paths' next nuclear vN using config time and one input mode.

    Supply source as a Path, ASCII str content, or opaque bytes, or supply both
    base_version and a
    nonempty replacements mapping of ordered atomic-number pairs to complete ASCII rows.
    Verify base bytes before editing; return the new immutable resource reference.
    Write only new resource artifacts. Raise ValueError for mode, row, reference,
    or integrity errors, and OSError for missing files, links, or contention.
    """
    if source is not None:
        if base_version is not None or replacements is not None:
            raise ValueError("Source and row replacement modes are mutually exclusive")
        return register_resource(paths, config, "engine", "nuclear", None, source)
    if base_version is None or not isinstance(replacements, Mapping) or not replacements:
        raise ValueError("Provide a source or a base version and nonempty replacements")
    reference = resolve_resource(paths, "engine", "nuclear", base_version)
    payload = verify_resource(paths, reference).read_bytes()
    if (hashlib.sha256(payload).hexdigest(), len(payload)) != (reference.sha256, reference.size_bytes):
        raise ValueError("Base payload changed while reading")
    return register_resource(paths, config, "engine", "nuclear", None, _replace_rows(payload, replacements))


def _replace_rows(payload: bytes, replacements: Mapping[tuple[int, int], str | bytes]) -> bytes:
    """Return payload with complete pair-keyed replacements, without writes.

    Require all 4,278 ASCII rows ordered by first then second atomic number,
    with 1 <= first <= second <= 92 and six fields per row. Keys are tuples
    of two actual integers, excluding bool. Values are matching ASCII str or
    bytes without CR/LF. Preserve untouched bytes and original line endings.
    Raise ValueError for malformed inputs, mismatched pairs, or an empty map.
    """
    if not isinstance(payload, bytes) or not isinstance(replacements, Mapping) or not replacements:
        raise ValueError("Expected payload bytes and nonempty row replacements")
    rows = payload.splitlines(keepends=True)
    pairs = [(first, second) for first in range(1, 93) for second in range(first, 93)]
    if len(rows) != len(pairs):
        raise ValueError("Expected exactly 4,278 nuclear rows")
    for pair, line in zip(pairs, rows):
        row = line.removesuffix(b"\n").removesuffix(b"\r")
        if _row_pair(row) != pair:
            raise ValueError("Base rows must contain every ordered triangular pair")
    positions = {pair: index for index, pair in enumerate(pairs)}
    for pair, replacement in replacements.items():
        if not isinstance(pair, tuple) or len(pair) != 2 or any(type(value) is not int for value in pair) or not 1 <= pair[0] <= pair[1] <= 92:
            raise ValueError("Replacement keys must satisfy 1 <= first <= second <= 92")
        if isinstance(replacement, str):
            try:
                replacement = replacement.encode("ascii")
            except UnicodeError as error:
                raise ValueError("Replacement rows must be ASCII") from error
        if not isinstance(replacement, bytes) or _row_pair(replacement) != pair:
            raise ValueError("Replacement row must match its identifier pair")
        index = positions[pair]
        original = rows[index]
        ending = b"\r\n" if original.endswith(b"\r\n") else b"\n" if original.endswith(b"\n") else b"\r" if original.endswith(b"\r") else b""
        rows[index] = replacement + ending
    return b"".join(rows)


def _row_pair(row: bytes) -> tuple[int, int]:
    """Return row's first two whitespace-separated fields as an ordered pair.

    Require ASCII bytes without CR/LF, six fields, and integer identifiers
    satisfying 1 <= first <= second <= 92. Other fields remain uninterpreted.
    Raise ValueError for invalid types, encoding, structure, or identifiers.
    No filesystem operations or other side effects occur.
    """
    if not isinstance(row, bytes) or b"\r" in row or b"\n" in row:
        raise ValueError("Expected a complete byte row without line endings")
    try:
        fields = row.decode("ascii").split()
        if len(fields) != 6:
            raise ValueError("Expected six fields per row")
        pair = (int(fields[0]), int(fields[1]))
        if not 1 <= pair[0] <= pair[1] <= 92:
            raise ValueError("Expected 1 <= first <= second <= 92")
    except (UnicodeError, ValueError) as error:
        raise ValueError("Invalid nuclear resource row") from error
    return pair
