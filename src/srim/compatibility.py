"""Normalize known native installation defects before immutable resource or worker staging."""

import hashlib
import os
from pathlib import Path
import re

from src.backend.resources import _hash_file


def read_module_version(srim_root: Path) -> tuple[Path, bytes]:
    """Return selected source Path and stable version bytes from absolute srim_root.

    Prefer VERSION, falling back to _VERSION only when VERSION is absent. Reject
    unsafe files through verified hashing and invalid ASCII/blank first lines.
    Perform no writes; raise ValueError for invalid/changing input and propagate I/O errors.
    """
    if not isinstance(srim_root, Path) or not srim_root.is_absolute():
        raise ValueError("SRIM installation must be an absolute Path")
    path = srim_root / "SR Module" / "VERSION"
    if not os.path.lexists(path):
        path = path.with_name("_VERSION")
    expected = _hash_file(path)
    contents = path.read_bytes()
    if (hashlib.sha256(contents).hexdigest(), len(contents)) != expected or _hash_file(path) != expected:
        raise ValueError("Module version changed while reading")
    try:
        lines = contents.decode("ascii").splitlines()
    except UnicodeError as error:
        raise ValueError("Module VERSION must be ASCII") from error
    if not lines or not lines[0].strip():
        raise ValueError("Module VERSION requires a nonblank first line")
    return path, contents


def normalize_atomdata(contents: bytes) -> tuple[bytes, list[dict[str, object]]]:
    """Return contents with only native row-37 Ru Rubidium corrected and change evidence.

    Preserve every other byte. Require bytes, perform no I/O and raise ValueError
    for the wrong input type. Full atomic validation remains the caller's responsibility.
    """
    if not isinstance(contents, bytes):
        raise ValueError("ATOMDATA requires bytes")
    lines = contents.splitlines(keepends=True)
    if len(lines) > 38:
        match = re.match(rb'^(\s*37\s+)Ru(?=\s+Rubidium(?:\s|$))', lines[38])
        if match is not None:
            lines[38] = lines[38][:match.end() - 2] + b"Rb" + lines[38][match.end():]
            return b"".join(lines), [{"operation": "set", "key": [37, "symbol"], "value": "Rb"}]
    return contents, []


def normalize_compounds(contents: bytes) -> tuple[bytes, list[dict[str, object]]]:
    """Return strictly valid native contents after only two known comma corrections.

    Insert a missing quoted-name separator and remove one trailing composition
    comma, preserving other bytes. Return record-indexed effective-line changes.
    Perform no I/O; raise ValueError for nonbytes or data invalid after corrections.
    """
    from src.srim.material import parse_compounds
    if not isinstance(contents, bytes):
        raise ValueError("Compound data requires bytes")
    lines = contents.splitlines(keepends=True)
    ordinal = 0
    pending = False
    changes: list[dict[str, object]] = []
    for index, line in enumerate(lines):
        if line.startswith(b"*"):
            ordinal += 1
            pending = True
        elif pending and line.strip() and not line.startswith((b"$", b"#", b"!")):
            pending = False
            effective = re.sub(rb'^(\s*"[^"\r\n]*")(?=\s+[+\-.0-9])', rb'\1,', line, count=1)
            effective = re.sub(rb',([ \t]*(?:\r\n|\n|\r)?$)', rb'\1', effective, count=1)
            if effective != line:
                lines[index] = effective
                changes.append({"operation": "set", "key": [ordinal, "native_composition"], "value": effective.decode("cp1252").rstrip("\r\n")})
    effective_contents = b"".join(lines)
    parse_compounds(effective_contents)
    return effective_contents, changes
