"""Register immutable shared files by category, name, readable version, and SHA-256."""

from __future__ import annotations

from contextlib import nullcontext
from collections.abc import Mapping
from dataclasses import dataclass
from io import BytesIO
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
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
        json.dumps(data, allow_nan=False)
    except (UnicodeError, ValueError) as error:
        raise ValueError(f"Invalid JSON: {path}") from error
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return data


def _write_json(path: Path, document: dict[str, object]) -> None:
    """Persist document atomically at link-free path as synchronized UTF-8 JSON; return None, reporting temporary artifacts on errors."""
    path = path.absolute()
    contents = json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
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


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Return a fresh dictionary from JSON pairs, rejecting duplicate keys with ValueError; perform no I/O."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def _validate_registration_provenance(provenance: Mapping[str, object] | None) -> dict[str, object] | None:
    """Copy and validate provenance's exact origin/source/base/change/entry/baseline/procedure schema.

    Return an independent JSON object or None without I/O. Raise ValueError for
    unsupported fields, non-JSON values, invalid identities or malformed references.
    """
    if provenance is None:
        return None
    if not isinstance(provenance, Mapping):
        raise ValueError("Registration provenance must be a mapping")
    try:
        document = json.loads(json.dumps(dict(provenance), allow_nan=False), object_pairs_hook=_unique_object)
    except (TypeError, ValueError) as error:
        raise ValueError("Registration provenance must contain JSON values") from error
    if set(document) != {"origin", "source", "base", "changes", "copied_entries", "baseline_id", "procedure"} or document["origin"] not in ("installation", "user", "calculated"):
        raise ValueError("Invalid registration provenance schema")
    source = document["source"]
    if not isinstance(source, dict) or set(source) != {"kind", "origin", "sha256", "size_bytes"} or source["kind"] not in ("file", "inline", "version", "recipe"):
        raise ValueError("Invalid provenance source")
    _validate_reference(ResourceReference("source", "source", "v1", source["sha256"], source["size_bytes"]))
    references = [document["base"]] if document["base"] is not None else []
    if source["kind"] == "version":
        references.append(source["origin"])
    elif not isinstance(source["origin"], str) or not source["origin"] or (source["kind"] == "file" and not Path(source["origin"]).is_absolute()):
        raise ValueError("Invalid provenance source origin")
    if source["kind"] in ("inline", "recipe") and source["origin"] != ("inline" if source["kind"] == "inline" else "generated"):
        raise ValueError("Invalid inline/recipe provenance marker")
    if not isinstance(document["changes"], list) or any(not isinstance(change, dict) for change in document["changes"]):
        raise ValueError("Provenance changes must be an operation list")
    for change in document["changes"]:
        operation = change.get("operation")
        if operation not in ("set", "remove", "append"):
            raise ValueError("Invalid provenance change operation")
        expected = {"operation", "value"} if operation == "append" else {"operation", "key", "value"} if operation == "set" else {"operation", "key"}
        if set(change) != expected:
            raise ValueError("Invalid provenance change fields")
        if operation != "append":
            key = change["key"]
            if isinstance(key, list):
                if len(key) not in (2, 3) or any(type(part) not in (int, str) or (type(part) is int and part < 1) or (isinstance(part, str) and not part) for part in key):
                    raise ValueError("Invalid composite provenance change key")
            elif type(key) not in (int, str) or (type(key) is int and key < 1) or (isinstance(key, str) and not key):
                raise ValueError("Invalid provenance change key")
    if not isinstance(document["copied_entries"], list):
        raise ValueError("Provenance copied_entries must be a list")
    seen: set[tuple[str, str, str, str, str]] = set()
    for entry in document["copied_entries"]:
        if not isinstance(entry, dict) or set(entry) != {"dataset", "dependency_key", "entry_id", "sha256", "size_bytes"}:
            raise ValueError("Invalid copied entry reference")
        _validate_reference(ResourceReference("entry", "entry", "v1", entry["sha256"], entry["size_bytes"]))
        if not isinstance(entry["dependency_key"], str) or re.fullmatch(r"[0-9a-f]{64}", entry["dependency_key"]) is None or not isinstance(entry["entry_id"], str) or re.fullmatch(r"z[0-9]{3}(?:-z[0-9]{3}-[sdt])?", entry["entry_id"]) is None:
            raise ValueError("Invalid copied entry identity")
        references.append(entry["dataset"])
        dataset = entry["dataset"]
        if not isinstance(dataset, dict) or set(dataset) != {"category", "name", "version", "sha256", "size_bytes"}:
            raise ValueError("Invalid copied dataset reference")
        identity = (dataset["category"], dataset["name"], dataset["version"], entry["dependency_key"], entry["entry_id"])
        if identity in seen:
            raise ValueError("Duplicate copied entry reference")
        seen.add(identity)
    for reference in references:
        if not isinstance(reference, dict) or set(reference) != {"category", "name", "version", "sha256", "size_bytes"}:
            raise ValueError("Invalid provenance resource reference")
        _validate_reference(ResourceReference(**reference))
        if re.fullmatch(r"v[1-9][0-9]*", reference["version"]) is None:
            raise ValueError("Provenance references must use concrete numbered versions")
    baseline = document["baseline_id"]
    if baseline is not None and (not isinstance(baseline, str) or re.fullmatch(r"[0-9a-f]{64}", baseline) is None):
        raise ValueError("Invalid provenance baseline")
    procedure = document["procedure"]
    if procedure is not None and (not isinstance(procedure, dict) or set(procedure) != {"method", "version"} or not isinstance(procedure["method"], str) or not procedure["method"] or type(procedure["version"]) is not int or procedure["version"] < 1):
        raise ValueError("Invalid provenance procedure")
    return document


def _validate_resource_manifest(document: dict[str, object]) -> None:
    """Validate document's exact legacy/new manifest schema and identities; return None or raise ValueError without I/O."""
    if not isinstance(document, dict) or type(document.get("schema_version")) is not int or document["schema_version"] not in (1, 2):
        raise ValueError("Invalid resource manifest version")
    keys = {"schema_version", "sha256", "size_bytes", "registered_at"}
    if document["schema_version"] == 2:
        keys |= {"provenance", "reuse_key"}
    if set(document) != keys:
        raise ValueError("Invalid resource manifest fields")
    _validate_reference(ResourceReference("manifest", "manifest", "v1", document["sha256"], document["size_bytes"]))
    if not isinstance(document["registered_at"], str):
        raise ValueError("Invalid registration timestamp")
    try:
        if datetime.fromisoformat(document["registered_at"]).utcoffset() is None:
            raise ValueError("Registration timestamp requires an offset")
    except (ValueError, OverflowError) as error:
        raise ValueError("Invalid registration timestamp") from error
    if document["schema_version"] == 2:
        _validate_registration_provenance(document["provenance"])
        key = document["reuse_key"]
        if key is not None and (not isinstance(key, str) or re.fullmatch(r"[0-9a-f]{64}", key) is None):
            raise ValueError("Invalid registration reuse key")


def register_resource(paths: ProjectPaths, config: Config, category: str, name: str, version: str | None, source: Path | str | bytes, *, provenance: Mapping[str, object] | None = None, reuse_key: str | None = None) -> ResourceReference:
    """Register source Path, ASCII str, or bytes under paths/category/name using config time.

    Inputs paths, category, and name identify storage; config supplies time.
    An explicit version is immutable; None allocates one beyond the highest vN
    directory under the publication lock, starting at v1 and never filling gaps.
    Source paths must be regular and link-free; byte inputs are preserved exactly.
    Strings are payload content encoded as ASCII, never filesystem paths.
    Non-ASCII strings raise ValueError before any filesystem writes.
    provenance supplies validated independent registration evidence. reuse_key
    atomically reuses matching bytes for a SHA-256 recipe and requires version None.

    Return an immutable reference, copying and synchronizing source bytes before
    publication under the resource-root lock. Existing labels cannot change.
    Raise ValueError for invalid identities/manifests or label conflicts, and
    OSError for contention, links, or file failures. Partial artifacts remain and
    are identified in exception notes. No resource deletion is performed.
    """
    directory = _resource_directory(paths, category, name)
    if version is not None:
        _validate_identifier(version)
        if re.fullmatch(r"v[1-9][0-9]*", version) is None:
            raise ValueError("New resource versions must be numbered vN labels")
    provenance = _validate_registration_provenance(provenance)
    if reuse_key is not None and (version is not None or not isinstance(reuse_key, str) or re.fullmatch(r"[0-9a-f]{64}", reuse_key) is None):
        raise ValueError("Reuse requires a SHA-256 key and automatic version allocation")
    if isinstance(source, str):
        try:
            source = source.encode("ascii")
        except UnicodeError as error:
            raise ValueError("String payloads must be ASCII") from error
    if not isinstance(source, (Path, bytes)):
        raise ValueError("Source must be a Path, ASCII str, or bytes")
    timestamp = _record_time(config, None).isoformat()
    if isinstance(source, Path):
        source = source.absolute()
        for entry in (*reversed(source.parents), source):
            if entry.is_symlink() or entry.is_junction():
                raise OSError(errno.ELOOP, "Source contains a link or junction", str(entry))
        if not stat.S_ISREG(source.stat().st_mode):
            raise OSError(errno.EINVAL, "Source must be a regular file", str(source))
    artifacts: list[Path] = []
    source_identity = _hash_file(source) if isinstance(source, Path) else (hashlib.sha256(source).hexdigest(), len(source))
    try:
        with _lock_parent(paths.resources):
            directory = _resource_directory(paths, category, name)
            reused: ResourceReference | None = None
            if version is None:
                highest = 0
                if directory.exists():
                    for entry in directory.iterdir():
                        if re.fullmatch(r"v[1-9][0-9]*", entry.name):
                            if entry.is_symlink() or entry.is_junction() or not entry.is_dir():
                                raise OSError(errno.EINVAL, "Numeric version must be a link-free directory", str(entry))
                            highest = max(highest, int(entry.name[1:]))
                            if reuse_key is not None:
                                candidate = _read_json(entry / "manifest.json")
                                _validate_resource_manifest(candidate)
                                if candidate.get("reuse_key") == reuse_key:
                                    if reused is not None:
                                        raise ValueError("Multiple registrations share one reuse key")
                                    reused = ResourceReference(category, name, entry.name, candidate["sha256"], candidate["size_bytes"])
                                    if _hash_file(entry / "payload") != (reused.sha256, reused.size_bytes):
                                        raise ValueError("Matching reusable resource is corrupt")
                                    if source_identity != (reused.sha256, reused.size_bytes):
                                        raise ValueError("Reuse key already identifies different contents")
                if reused is not None:
                    if isinstance(source, Path) and _hash_file(source) != source_identity:
                        raise ValueError("Resource source changed during registration")
                    return reused
                version = f"v{highest + 1}"
                _validate_identifier(version)
            destination = directory / version
            for entry in (directory, destination):
                if entry.is_symlink() or entry.is_junction():
                    raise OSError(errno.ELOOP, "Resource path contains a link or junction", str(entry))
            directory.mkdir(parents=True, exist_ok=True)
            staging = Path(mkdtemp(prefix=".staging-", dir=directory))
            artifacts.append(staging)
            digest = hashlib.sha256()
            size = 0
            with (source.open("rb") if isinstance(source, Path) else nullcontext(BytesIO(source))) as incoming, (staging / "payload").open("xb") as outgoing:
                while chunk := incoming.read(1024 * 1024):
                    outgoing.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                outgoing.flush()
                os.fsync(outgoing.fileno())
            checksum = digest.hexdigest()
            if (checksum, size) != source_identity or (isinstance(source, Path) and _hash_file(source) != source_identity):
                raise ValueError("Resource source changed during registration")
            reference = ResourceReference(category, name, version, checksum, size)
            if os.path.lexists(destination):
                manifest = _read_json(destination / "manifest.json")
                _validate_resource_manifest(manifest)
                existing = ResourceReference(category, name, version, manifest["sha256"], manifest["size_bytes"])
                _validate_reference(existing)
                if _hash_file(destination / "payload") != (existing.sha256, existing.size_bytes):
                    raise ValueError("Existing resource is corrupt")
                if existing != reference:
                    raise ValueError("Version label already identifies different contents")
                (staging / "payload").unlink()
                staging.rmdir()
            else:
                _write_json(staging / "manifest.json", {"schema_version": 2, "sha256": checksum, "size_bytes": size, "registered_at": timestamp, "provenance": provenance, "reuse_key": reuse_key})
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
        _validate_resource_manifest(manifest)
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
