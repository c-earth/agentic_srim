"""Register immutable shared files by category, name, readable version, and SHA-256."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from tempfile import NamedTemporaryFile, mkdtemp

from src.backend.config import Config
from src.backend.paths import ProjectPaths, get_project_paths
from src.backend.records import _lock_parent, _record_time


@dataclass(frozen=True)
class ResourceReference:
    """Store category, name, version, sha256, and size_bytes supplied at construction.

    Fields identify one immutable shared file. Construction stores inputs without
    validation or filesystem access; resource APIs validate before using them.
    """

    category: str
    name: str
    version: str
    sha256: str
    size_bytes: int


def _validate_identifier(value: str) -> None:
    """Validate value as a portable lowercase identifier; return None or raise ValueError without writes."""
    reserved = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
    if not isinstance(value, str) or len(value) > 64 or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value) is None or value in reserved:
        raise ValueError(f"Invalid resource identifier: {value!r}")


def _validate_reference(reference: ResourceReference) -> None:
    """Validate reference's identifiers, lowercase SHA-256, and nonnegative integer size; return None or raise ValueError, writing nothing."""
    if not isinstance(reference, ResourceReference):
        raise ValueError("Expected ResourceReference")
    for value in (reference.category, reference.name, reference.version):
        _validate_identifier(value)
    if not isinstance(reference.sha256, str) or re.fullmatch(r"[0-9a-f]{64}", reference.sha256) is None:
        raise ValueError("Invalid SHA-256")
    if type(reference.size_bytes) is not int or reference.size_bytes < 0:
        raise ValueError("Invalid resource size")


def _resource_directory(paths: ProjectPaths, category: str, name: str) -> Path:
    """Validate paths and category/name; return their directory, rejecting links and missing resource roots without writes."""
    for value in (category, name):
        _validate_identifier(value)
    if paths != get_project_paths(paths.root):
        raise ValueError("Project paths must use the agreed layout")
    directory = paths.resources / category / name
    for entry in (*reversed(directory.parents), directory):
        if entry.is_symlink() or entry.is_junction():
            raise OSError(errno.ELOOP, "Resource path contains a link or junction", str(entry))
    if not paths.resources.is_dir():
        raise FileNotFoundError(errno.ENOENT, "Run base-directory setup first", str(paths.resources))
    return directory


def _hash_file(path: Path) -> tuple[str, int]:
    """Read regular link-free path in chunks; return SHA-256 and byte count, raising OSError on invalid paths/access failures."""
    path = path.absolute()
    for entry in (*reversed(path.parents), path):
        if entry.is_symlink() or entry.is_junction():
            raise OSError(errno.ELOOP, "File path contains a link or junction", str(entry))
    if not stat.S_ISREG(path.stat().st_mode):
        raise OSError(errno.EINVAL, "Expected a regular file", str(path))
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _read_json(path: Path) -> dict[str, object]:
    """Read a link-free JSON object from path; return fields or raise ValueError/OSError without writes."""
    path = path.absolute()
    for entry in (*reversed(path.parents), path):
        if entry.is_symlink() or entry.is_junction():
            raise OSError(errno.ELOOP, "JSON path contains a link or junction", str(entry))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        json.dumps(data, allow_nan=False)
    except (UnicodeError, ValueError) as error:
        raise ValueError(f"Invalid JSON: {path}") from error
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return data


def _write_json(path: Path, document: dict[str, object]) -> None:
    """Persist document atomically at link-free path as synchronized UTF-8 JSON; return None, reporting temporary artifacts on errors."""
    path = path.absolute()
    contents = json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    temporary: Path | None = None
    try:
        for entry in (*reversed(path.parents), path):
            if entry.is_symlink() or entry.is_junction():
                raise OSError(errno.ELOOP, "JSON path contains a link or junction", str(entry))
        with NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", prefix=".json-", suffix=".tmp", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as error:
        if temporary is not None and temporary.exists():
            error.add_note(f"Surviving temporary file: {temporary}")
        raise


def register_resource(paths: ProjectPaths, config: Config, category: str, name: str, version: str, source: Path) -> ResourceReference:
    """Register regular source bytes under paths/category/name/version using config time.

    Return an immutable reference, copying and synchronizing source bytes before
    publication under the resource-root lock. Existing labels cannot change.
    Raise ValueError for invalid identities/manifests or label conflicts, and
    OSError for contention, links, or file failures. Partial artifacts remain and
    are identified in exception notes. No resource deletion is performed.
    """
    directory = _resource_directory(paths, category, name)
    _validate_identifier(version)
    timestamp = _record_time(config, None).isoformat()
    source = source.absolute()
    for entry in (*reversed(source.parents), source):
        if entry.is_symlink() or entry.is_junction():
            raise OSError(errno.ELOOP, "Source contains a link or junction", str(entry))
    if not stat.S_ISREG(source.stat().st_mode):
        raise OSError(errno.EINVAL, "Source must be a regular file", str(source))
    artifacts: list[Path] = []
    try:
        with _lock_parent(paths.resources):
            directory = _resource_directory(paths, category, name)
            destination = directory / version
            for entry in (directory, destination):
                if entry.is_symlink() or entry.is_junction():
                    raise OSError(errno.ELOOP, "Resource path contains a link or junction", str(entry))
            directory.mkdir(parents=True, exist_ok=True)
            staging = Path(mkdtemp(prefix=".staging-", dir=directory))
            artifacts.append(staging)
            digest = hashlib.sha256()
            size = 0
            with source.open("rb") as incoming, (staging / "payload").open("xb") as outgoing:
                while chunk := incoming.read(1024 * 1024):
                    outgoing.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                outgoing.flush()
                os.fsync(outgoing.fileno())
            checksum = digest.hexdigest()
            reference = ResourceReference(category, name, version, checksum, size)
            if os.path.lexists(destination):
                manifest = _read_json(destination / "manifest.json")
                if set(manifest) != {"schema_version", "sha256", "size_bytes", "registered_at"} or type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1 or not isinstance(manifest["registered_at"], str) or datetime.fromisoformat(manifest["registered_at"]).utcoffset() is None:
                    raise ValueError("Invalid existing resource manifest")
                existing = ResourceReference(category, name, version, manifest["sha256"], manifest["size_bytes"])
                _validate_reference(existing)
                if _hash_file(destination / "payload") != (existing.sha256, existing.size_bytes):
                    raise ValueError("Existing resource is corrupt")
                if existing != reference:
                    raise ValueError("Version label already identifies different contents")
                (staging / "payload").unlink()
                staging.rmdir()
            else:
                _write_json(staging / "manifest.json", {"schema_version": 1, "sha256": checksum, "size_bytes": size, "registered_at": timestamp})
                staging.rename(destination)
                artifacts.append(destination)
            return reference
    except (OSError, ValueError) as error:
        error.add_note("Resource registration retained artifacts: " + ", ".join(str(path) for path in artifacts if os.path.lexists(path)))
        raise


def resolve_resource(paths: ProjectPaths, category: str, name: str, version: str) -> ResourceReference:
    """Resolve paths/category/name/version under the resource lock; return reference without hashing payload, raising ValueError for unknown versions or invalid data and OSError for file failures."""
    directory = _resource_directory(paths, category, name)
    _validate_identifier(version)
    with _lock_parent(paths.resources):
        destination = directory / version
        if destination.is_symlink() or destination.is_junction():
            raise OSError(errno.ELOOP, "Version directory is a link", str(destination))
        if not os.path.lexists(destination):
            raise ValueError(f"Unknown resource version: {version}")
        manifest = _read_json(destination / "manifest.json")
        if set(manifest) != {"schema_version", "sha256", "size_bytes", "registered_at"} or type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1 or not isinstance(manifest["registered_at"], str) or datetime.fromisoformat(manifest["registered_at"]).utcoffset() is None:
            raise ValueError("Invalid resource manifest")
        reference = ResourceReference(category, name, version, manifest["sha256"], manifest["size_bytes"])
        _validate_reference(reference)
        return reference


def verify_resource(paths: ProjectPaths, reference: ResourceReference) -> Path:
    """Verify reference against paths' version manifest and payload hash; return payload Path or raise ValueError/OSError without data writes."""
    _validate_reference(reference)
    resolved = resolve_resource(paths, reference.category, reference.name, reference.version)
    if resolved != reference:
        raise ValueError("Reference differs from registered version")
    with _lock_parent(paths.resources):
        directory = _resource_directory(paths, reference.category, reference.name)
        payload = directory / reference.version / "payload"
        if _hash_file(payload) != (reference.sha256, reference.size_bytes):
            raise ValueError("Resource payload hash or size mismatch")
        return payload
