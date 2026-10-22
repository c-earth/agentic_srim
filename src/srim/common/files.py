"""Read, inventory and publish verified simulation evidence without engine execution."""

from __future__ import annotations

import errno
import os
import stat
from operator import itemgetter
from pathlib import Path
from src.backend.resources import _hash_file, _read_json as _read_resource_json, _write_json as _write_resource_json
from src.srim.common.types import JSONObject


def _inventory(directory: Path, record: Path) -> list[JSONObject]:
    """Hash directory's regular link-free descendants beneath record; return sorted manifests or raise ValueError/OSError without writes."""
    if not directory.is_relative_to(record):
        raise ValueError("Inventory must remain beneath the record")
    for entry in (*reversed(directory.parents), directory):
        if entry.is_symlink() or entry.is_junction():
            raise OSError(errno.ELOOP, "Inventory path contains a link", str(entry))
    if not directory.is_dir():
        raise FileNotFoundError(errno.ENOENT, "Inventory directory is missing", str(directory))
    result: list[JSONObject] = []
    pending = [directory]
    while pending:
        for entry in pending.pop().iterdir():
            if entry.is_symlink() or entry.is_junction():
                raise OSError(errno.ELOOP, "Inventory contains a link", str(entry))
            if entry.is_dir():
                pending.append(entry)
            else:
                checksum, size = _hash_file(entry)
                result.append({"path": entry.relative_to(record).as_posix(), "size_bytes": size, "sha256": checksum})
    result.sort(key=itemgetter("path"))
    names = [item["path"].casefold() for item in result]
    if len(names) != len(set(names)):
        raise ValueError("Case-insensitive inventory path collision")
    return result


def _read_json(path: Path) -> JSONObject:
    """Read path as finite JSON object; return independent data or raise ValueError/OSError without data writes."""
    return _read_resource_json(path)


def _write_json(path: Path, document: JSONObject) -> None:
    """Synchronize and atomically replace path with document; return None, preserving/reporting temporary files on OSError."""
    _write_resource_json(path, document)


def _write_input_file(path: Path, contents: bytes) -> None:
    """Create path exclusively with contents, returning None after flush and fsync.

    Require an existing parent and reject links or junctions anywhere in path.
    Create no directories and overwrite nothing. Propagate OSError for filesystem
    failures with a note naming any surviving partial file; contents is unchanged.
    """
    try:
        for entry in (*reversed(path.parents), path):
            if entry.is_symlink() or entry.is_junction():
                raise OSError(errno.ELOOP, "Input path contains a link or junction", str(entry))
        with path.open("xb") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        if os.path.lexists(path):
            error.add_note(f"Surviving input file: {path}")
        raise


def _read_regular_bytes(path: Path) -> bytes:
    """Read regular path as bytes without writes, rejecting links and junctions.

    Check path and all ancestors before reading. Return exact contents, or raise
    OSError for links, missing files, nonregular objects, and access failures.
    """
    for entry in (*reversed(path.parents), path):
        if entry.is_symlink() or entry.is_junction():
            raise OSError(errno.ELOOP, "Runtime source path contains a link or junction", str(entry))
    if not stat.S_ISREG(path.stat().st_mode):
        raise OSError(errno.EINVAL, "Runtime source must be a regular file", str(path))
    return path.read_bytes()
