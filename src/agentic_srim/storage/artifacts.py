"""Preserve per-attempt input/output artifacts and verify their SHA-256 manifests."""

import hashlib
import json
from pathlib import Path


def checksum(path: Path) -> str:
    """Return the SHA-256 of the file at path, reading bounded chunks."""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path: Path, value: object) -> None:
    """Write a new JSON artifact at path from value; never overwrite; return nothing."""
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def seal(directory: Path) -> dict:
    """Manifest files already in an attempt directory; return the saved manifest.

    The manifest excludes itself. Artifacts are application-immutable after seal;
    checksums detect later changes, but this is not operating-system WORM storage.
    """
    paths = sorted(directory.rglob("*"))
    if any(p.is_symlink() for p in paths):
        raise ValueError("Artifact symlinks are forbidden")
    records = [{"path": p.relative_to(directory).as_posix(), "sha256": checksum(p), "size_bytes": p.stat().st_size}
               for p in paths if p.is_file() and p != directory / "manifest.json"]
    result = {"schema_version": 1, "artifacts": records}
    write_json(directory / "manifest.json", result)
    return result


def verify(directory: Path) -> dict:
    """Read an attempt directory; return its manifest or raise on artifact damage."""
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise ValueError("Unsupported artifact manifest")
    for record in manifest["artifacts"]:
        name = record["path"]
        relative = Path(name)
        if relative.is_absolute() or relative.drive or ".." in relative.parts or "\\" in name or ":" in name or name in (".", ".."):
            raise ValueError("Unsafe artifact manifest path")
        path = directory / name
        if path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()):
            raise ValueError("Unsafe artifact location")
        if path.stat().st_size != record["size_bytes"] or checksum(path) != record["sha256"]:
            raise ValueError(f"Artifact checksum mismatch: {name}")
    return manifest
