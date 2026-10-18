"""Create numbered project directories and preserve durable deletion identities."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import errno
import json
import msvcrt
import os
from pathlib import Path
import re
import shutil
from tempfile import NamedTemporaryFile
from typing import Literal

from src.backend.config import Config
from src.backend.paths import ProjectPaths, get_project_paths


RecordKind = Literal["srim", "post", "case", "worker"]


@dataclass(frozen=True)
class CreateRequest:
    """Describe a kind and optional aware timestamp to create; construction only stores inputs."""

    kind: RecordKind
    timestamp: datetime | None = None


@dataclass(frozen=True)
class CreatedDirectory:
    """Store the created kind, path, converted timestamp (None for workers), and suffix."""

    kind: RecordKind
    path: Path
    timestamp: datetime | None
    suffix: int


@dataclass(frozen=True)
class DeleteRequest:
    """Describe a kind, directory basename, and optional deletion reason without validation."""

    kind: RecordKind
    name: str
    reason: str | None = None


@dataclass(frozen=True)
class DeletedDirectory:
    """Store the deleted kind and path and its tombstone path, or None for a worker."""

    kind: RecordKind
    path: Path
    tombstone: Path | None


@dataclass(frozen=True)
class BatchReport:
    """Store ordered successes, zero-based failed/unattempted indices, and surviving partial paths."""

    completed: tuple[CreatedDirectory | DeletedDirectory, ...]
    failed_index: int | None
    unattempted_indices: tuple[int, ...]
    partial_paths: tuple[Path, ...]


class BatchOperationError(Exception):
    """Expose a batch report supplied at construction and retain the operational error as cause."""

    report: BatchReport

    def __init__(self: BatchOperationError, message: str, report: BatchReport) -> None:
        """Store message and report on self; return None without filesystem effects."""
        super().__init__(message)
        self.report = report


def _validate_paths(paths: ProjectPaths) -> None:
    """Validate paths' absolute agreed layout and existing link-free bases; raise ValueError/OSError, write nothing."""
    if paths != get_project_paths(paths.root):
        raise ValueError("Project paths must use the agreed layout")
    for target in (paths.root, paths.simulation_memory, paths.postprocessing_memory, paths.cases, paths.workers, paths.resources):
        for ancestor in (*reversed(target.parents), target):
            if ancestor.is_symlink() or ancestor.is_junction():
                raise OSError(errno.ELOOP, "Links and junctions are not allowed", str(ancestor))
        if not target.is_dir():
            raise FileNotFoundError(errno.ENOENT, "Base directory is missing or is not a directory", str(target))


def _validate_create_request(request: CreateRequest) -> None:
    """Validate request's kind and aware timestamp; return None or raise ValueError without side effects."""
    if not isinstance(request, CreateRequest) or request.kind not in ("srim", "post", "case", "worker"):
        raise ValueError("Invalid creation request or kind")
    if request.kind == "worker" and request.timestamp is not None:
        raise ValueError("Workers do not accept timestamps")
    if request.timestamp is not None and (
        not isinstance(request.timestamp, datetime) or request.timestamp.utcoffset() is None
    ):
        raise ValueError("Timestamp must be an aware datetime")


def _validate_delete_request(request: DeleteRequest) -> None:
    """Validate request's kind, canonical dated basename, and reason; return None or raise ValueError, writing nothing."""
    if not isinstance(request, DeleteRequest) or request.kind not in ("srim", "post", "case", "worker"):
        raise ValueError("Invalid deletion request or kind")
    if request.reason is not None and not isinstance(request.reason, str):
        raise ValueError("Deletion reason must be a string or None")
    pattern = r"worker_[1-9][0-9]*" if request.kind == "worker" else rf"{request.kind}_([0-9]{{8}}_[0-9]{{6}})_[1-9][0-9]*"
    match = re.fullmatch(pattern, request.name) if isinstance(request.name, str) else None
    if match is None:
        raise ValueError("Invalid directory basename")
    if request.kind != "worker":
        datetime.strptime(match.group(1), "%Y%m%d_%H%M%S")


def _record_time(config: Config, supplied: datetime | None) -> datetime:
    """Convert supplied aware time or current time using config; return aware datetime or raise ValueError, writing nothing."""
    setting = config.timezone
    if not isinstance(setting, str) or (setting not in ("UTC", "system") and re.fullmatch(r"UTC[+-](?:[01][0-9]|2[0-3]):[0-5][0-9]", setting) is None):
        raise ValueError("Invalid configured timezone")
    if supplied is not None and (not isinstance(supplied, datetime) or supplied.utcoffset() is None):
        raise ValueError("Timestamp must be an aware datetime")
    instant = datetime.now(timezone.utc) if supplied is None else supplied
    if setting == "system":
        return instant.astimezone()
    offset = timedelta()
    if setting != "UTC":
        offset = timedelta(hours=int(setting[4:6]), minutes=int(setting[7:9]))
        if setting[3] == "-":
            offset = -offset
    return instant.astimezone(timezone(offset))


@contextmanager
def _lock_parent(parent: Path) -> Iterator[None]:
    """Yield exclusive nonblocking Windows access to parent; create persistent lock file and raise OSError on contention/failure."""
    lock_path = parent / ".records.lock"
    if lock_path.is_symlink() or lock_path.is_junction():
        raise OSError(errno.ELOOP, "Lock path is a link", str(lock_path))
    with lock_path.open("a+b") as stream:
        if os.fstat(stream.fileno()).st_size == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def _write_tombstone(path: Path, data: dict[str, str | int | None]) -> None:
    """Atomically persist data as synchronized UTF-8 JSON at path; return None, raising errors with surviving temporary paths."""
    temporary: Path | None = None
    try:
        if path.is_symlink() or path.is_junction():
            raise OSError(errno.ELOOP, "Tombstone is a link", str(path))
        with NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=path.parent, prefix=".tombstone-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as error:
        error.partial_paths = (temporary,) if temporary is not None and temporary.exists() else ()
        raise


def _read_tombstone(path: Path) -> dict[str, str | int | None]:
    """Read path's exact version-one schema and identity; return fields or raise ValueError/OSError without writes."""
    if path.is_symlink() or path.is_junction():
        raise OSError(errno.ELOOP, "Tombstone is a link", str(path))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Malformed tombstone: {path}") from error
    fields = {"schema_version", "kind", "name", "state", "requested_at", "deleted_at", "reason"}
    if not isinstance(data, dict) or set(data) != fields or type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ValueError("Unsupported tombstone schema")
    _validate_delete_request(DeleteRequest(data["kind"], data["name"], data["reason"]))
    if data["kind"] == "worker" or path.name != data["name"] + ".json" or path.parent.parent.name != {"srim": "srim", "post": "post", "case": "case"}[data["kind"]]:
        raise ValueError("Tombstone identity does not match its location")
    if data["state"] not in ("pending", "deleted"):
        raise ValueError("Invalid tombstone state")
    for key in ("requested_at", "deleted_at"):
        value = data[key]
        if key == "deleted_at" and data["state"] == "pending":
            if value is not None:
                raise ValueError("Pending tombstone has a deletion time")
        elif not isinstance(value, str) or datetime.fromisoformat(value).utcoffset() is None:
            raise ValueError("Tombstone time must have an explicit offset")
    return data


def _create_one(paths: ProjectPaths, config: Config, request: CreateRequest) -> CreatedDirectory:
    """Create request under paths using config and a parent lock; return result or raise with surviving partial directories."""
    parent = {"srim": paths.simulation_memory, "post": paths.postprocessing_memory, "case": paths.cases, "worker": paths.workers}[request.kind]
    instant = None if request.kind == "worker" else _record_time(config, request.timestamp)
    prefix = "worker" if instant is None else f"{request.kind}_{instant.year:04d}{instant.month:02d}{instant.day:02d}_{instant.hour:02d}{instant.minute:02d}{instant.second:02d}"
    partial: list[Path] = []
    try:
        with _lock_parent(parent):
            deleted = parent / ".deleted"
            if deleted.is_symlink() or deleted.is_junction():
                raise OSError(errno.ELOOP, "Deletion storage is a link", str(deleted))
            suffix = 1
            while True:
                target = parent / f"{prefix}_{suffix}"
                tombstone = deleted / f"{target.name}.json"
                if request.kind != "worker" and os.path.lexists(tombstone):
                    suffix += 1
                    continue
                try:
                    target.mkdir()
                except FileExistsError:
                    suffix += 1
                    continue
                partial.append(target)
                if request.kind == "worker":
                    for name in ("sr", "trim"):
                        child = target / name
                        child.mkdir()
                        partial.append(child)
                return CreatedDirectory(request.kind, target, instant, suffix)
    except OSError as error:
        error.partial_paths = tuple(partial)
        raise


def _delete_one(paths: ProjectPaths, config: Config, request: DeleteRequest) -> DeletedDirectory:
    """Delete request from paths, synchronizing record intent with config time; return result or raise with surviving artifacts."""
    parent = {"srim": paths.simulation_memory, "post": paths.postprocessing_memory, "case": paths.cases, "worker": paths.workers}[request.kind]
    target = parent / request.name
    tombstone = None if request.kind == "worker" else parent / ".deleted" / f"{request.name}.json"
    artifacts: list[Path] = []
    try:
        with _lock_parent(parent):
            if tombstone is not None:
                if tombstone.parent.is_symlink() or tombstone.parent.is_junction():
                    raise OSError(errno.ELOOP, "Deletion storage is a link", str(tombstone.parent))
                if os.path.lexists(tombstone):
                    data = _read_tombstone(tombstone)
                    artifacts.append(tombstone)
                    if data["state"] == "deleted":
                        if os.path.lexists(target):
                            raise FileExistsError("Completed deletion identity has been recreated")
                        return DeletedDirectory(request.kind, target, tombstone)
                else:
                    if not os.path.lexists(target):
                        raise FileNotFoundError(errno.ENOENT, "Record does not exist", str(target))
                    data = {"schema_version": 1, "kind": request.kind, "name": request.name, "state": "pending", "requested_at": _record_time(config, None).isoformat(), "deleted_at": None, "reason": request.reason}
                    tombstone.parent.mkdir(exist_ok=True)
                    artifacts.append(tombstone.parent)
                    _write_tombstone(tombstone, data)
                    artifacts.append(tombstone)
            if os.path.lexists(target):
                if target.is_symlink() or target.is_junction():
                    raise OSError(errno.ELOOP, "Deletion target is a link", str(target))
                if not target.is_dir():
                    raise NotADirectoryError(errno.ENOTDIR, "Deletion target is not a directory", str(target))
                pending = [target]
                while pending:
                    for entry in pending.pop().iterdir():
                        if entry.is_symlink() or entry.is_junction():
                            raise OSError(errno.ELOOP, "Deletion target contains a link", str(entry))
                        if entry.is_dir():
                            pending.append(entry)
                shutil.rmtree(target)
            elif tombstone is None:
                raise FileNotFoundError(errno.ENOENT, "Worker does not exist", str(target))
            if tombstone is not None:
                data["state"] = "deleted"
                data["deleted_at"] = _record_time(config, None).isoformat()
                _write_tombstone(tombstone, data)
            return DeletedDirectory(request.kind, target, tombstone)
    except (OSError, ValueError) as error:
        candidates = (*artifacts, target, *getattr(error, "partial_paths", ()))
        error.partial_paths = tuple(dict.fromkeys(path for path in candidates if os.path.lexists(path)))
        raise


def create_directories(paths: ProjectPaths, config: Config, requests: Sequence[CreateRequest]) -> BatchReport:
    """Validate all requests then create in order using paths/config; return successes or raise BatchOperationError with partial work and cause."""
    items = tuple(requests)
    for request in items:
        _validate_create_request(request)
    _record_time(config, None)
    completed: list[CreatedDirectory | DeletedDirectory] = []
    for index, request in enumerate(items):
        try:
            _validate_paths(paths)
            completed.append(_create_one(paths, config, request))
        except (OSError, ValueError) as error:
            report = BatchReport(tuple(completed), index, tuple(range(index + 1, len(items))), getattr(error, "partial_paths", ()))
            raise BatchOperationError(f"Creation failed at request {index}", report) from error
    return BatchReport(tuple(completed), None, (), ())


def delete_directories(paths: ProjectPaths, config: Config, requests: Sequence[DeleteRequest]) -> BatchReport:
    """Validate all requests then delete in order using paths/config; return successes or raise BatchOperationError preserving partial work and cause."""
    items = tuple(requests)
    for request in items:
        _validate_delete_request(request)
    _record_time(config, None)
    completed: list[CreatedDirectory | DeletedDirectory] = []
    for index, request in enumerate(items):
        try:
            _validate_paths(paths)
            completed.append(_delete_one(paths, config, request))
        except (OSError, ValueError) as error:
            report = BatchReport(tuple(completed), index, tuple(range(index + 1, len(items))), getattr(error, "partial_paths", ()))
            raise BatchOperationError(f"Deletion failed at request {index}", report) from error
    return BatchReport(tuple(completed), None, (), ())
