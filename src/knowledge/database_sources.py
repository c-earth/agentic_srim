"""Inspect explicitly registered local sources with bounded, read-only prefix evidence."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import errno
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import struct
import tarfile
import time
from typing import BinaryIO
from uuid import uuid4
import zipfile
import zlib

from jsonschema import Draft202012Validator

from src.backend.config import load_config
from src.backend.paths import ProjectPaths, get_project_paths
from src.knowledge.database_adapters import probe_database_adapters
from src.srim.common.types import JSONObject


LIMITS = {"entries": 256, "depth": 2, "samples": 8, "sample_size_bytes": 65536, "sample_bytes": 262144, "raw_bytes": 16777216, "expanded_bytes": 16777216, "seconds": 5, "zip_directory_bytes": 8388608}
STRING = {"type": "string"}
ISSUES = {"type": "array", "maxItems": 512, "items": {"type": "string", "maxLength": 4096}}
NATURAL = {"type": "integer", "minimum": 0}
SOURCE_ID = {"type": "string", "pattern": "^db_[0-9a-f]{64}$"}
RELATIVE = {"type": "string", "maxLength": 4096}
CONTAINER = {"enum": ["directory", "file", "zip", "tar", "tar_gzip", "gzip", "sqlite", "text", "unknown"]}
SOURCE_PROPERTIES = {"source_id": SOURCE_ID, "path": STRING, "state": {"enum": ["configured", "unavailable", "unsafe"]}, "issues": ISSUES}
SOURCE_SCHEMA = {"type": "object", "additionalProperties": False, "required": list(SOURCE_PROPERTIES), "properties": SOURCE_PROPERTIES}
REFERENCE_PROPERTIES = {"schema_version": {"const": 1}, "source_id": SOURCE_ID, "relative_path": RELATIVE, "member_path": {"type": ["string", "null"], "maxLength": 4096}, "container": CONTAINER, "file_size_bytes": NATURAL, "file_mtime_ns": NATURAL, "sample_offset": {"const": 0}, "sample_size_bytes": {"type": "integer", "minimum": 0, "maximum": 65536}, "sample_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"}}
SAMPLE_REFERENCE_SCHEMA = {"type": "object", "additionalProperties": False, "required": list(REFERENCE_PROPERTIES), "properties": REFERENCE_PROPERTIES}
SAMPLE_PROPERTIES = {"reference": SAMPLE_REFERENCE_SCHEMA, "encoding": {"enum": ["utf-8", "base64"]}, "content": {"type": "string", "maxLength": 87384}, "structure": {"type": ["object", "null"], "additionalProperties": {"type": "array", "maxItems": 64, "items": {"type": "string", "maxLength": 256}}}}
SAMPLE_SCHEMA = {"type": "object", "additionalProperties": False, "required": list(SAMPLE_PROPERTIES), "properties": SAMPLE_PROPERTIES}
ENTRY_PROPERTIES = {"relative_path": RELATIVE, "container": CONTAINER, "member_path": {"type": ["string", "null"], "maxLength": 4096}, "kind": {"enum": ["regular", "directory", "unsupported"]}, "size_bytes": NATURAL}
ENTRY_SCHEMA = {"type": "object", "additionalProperties": False, "required": list(ENTRY_PROPERTIES), "properties": ENTRY_PROPERTIES}
CANDIDATE_PROPERTIES = {"adapter_id": {"type": "string", "pattern": "^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$"}, "adapter_version": {"type": "integer", "minimum": 1}, "result": {"enum": ["match", "no_match", "insufficient_evidence"]}, "evidence": {"type": "array", "maxItems": 32, "items": {"type": "string", "maxLength": 2048}}}
CANDIDATE_SCHEMA = {"type": "object", "additionalProperties": False, "required": list(CANDIDATE_PROPERTIES), "properties": CANDIDATE_PROPERTIES}
DETECTION_PROPERTIES = {"state": {"enum": ["known", "unknown", "ambiguous", "insufficient_evidence"]}, "candidates": {"type": "array", "items": CANDIDATE_SCHEMA}}
DETECTION_SCHEMA = {"type": "object", "additionalProperties": False, "required": list(DETECTION_PROPERTIES), "properties": DETECTION_PROPERTIES}
LIST_PROPERTIES = {"schema_version": {"const": 1}, "sources": {"type": "array", "items": SOURCE_SCHEMA}, "issues": ISSUES}
SOURCE_LIST_SCHEMA = {"type": "object", "additionalProperties": False, "required": list(LIST_PROPERTIES), "properties": LIST_PROPERTIES}
READ_PROPERTIES = {"schema_version": {"const": 1}, "reference": SAMPLE_REFERENCE_SCHEMA, "encoding": SAMPLE_PROPERTIES["encoding"], "content": SAMPLE_PROPERTIES["content"], "verification": {"const": "sample_only"}}
SAMPLE_READ_SCHEMA = {"type": "object", "additionalProperties": False, "required": list(READ_PROPERTIES), "properties": READ_PROPERTIES}
REPORT_PROPERTIES = {"schema_version": {"const": 1}, "inspection_id": {"type": "string", "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"}, "source": SOURCE_SCHEMA, "inspection_state": {"enum": ["inspected", "limited", "unavailable", "unsafe", "unsupported_container"]}, "inventory": {"type": "array", "maxItems": 256, "items": ENTRY_SCHEMA}, "samples": {"type": "array", "maxItems": 8, "items": SAMPLE_SCHEMA}, "format_detection": DETECTION_SCHEMA, "limits": {"type": "object", "additionalProperties": False, "required": list(LIMITS), "properties": {key: {"const": value} for key, value in LIMITS.items()}}, "usage": {"type": "object", "additionalProperties": False, "required": ["raw_bytes", "expanded_bytes", "entries", "samples", "sample_bytes"], "properties": {key: {"type": "integer", "minimum": 0, "maximum": LIMITS[key]} for key in ("raw_bytes", "expanded_bytes", "entries", "samples", "sample_bytes")}}, "issues": ISSUES, "recommended_skill": {"enum": [None, "external-database-onboarding"]}}
INSPECTION_SCHEMA = {"type": "object", "additionalProperties": False, "required": list(REPORT_PROPERTIES), "properties": REPORT_PROPERTIES}
SAMPLE_PROPERTIES["structure"] = {"type": ["object", "null"], "additionalProperties": False, "properties": {key: {"type": "array", "maxItems": 64, "items": {"type": "string", "maxLength": 256}} for key in ("json_keys", "header_candidates")}}


@dataclass
class InspectionBudget:
    """Track per-request byte/entry/sample usage and a five-second monotonic deadline.

    Optional counters initialize usage; deadline defaults to now plus five seconds.
    Instances never access files and must not be shared between requests.
    """

    raw_bytes: int = 0
    expanded_bytes: int = 0
    entries: int = 0
    samples: int = 0
    sample_bytes: int = 0
    deadline: float = field(default_factory=lambda: time.monotonic() + 5)


class BudgetedReader:
    """Wrap source with shared budget using raw or expanded channel, without owning it."""

    def __init__(self: BudgetedReader, source: BinaryIO, budget: InspectionBudget, channel: str) -> None:
        """Retain source/budget/channel; reject invalid channel with ValueError; return None."""
        if channel not in ("raw", "expanded"):
            raise ValueError("Invalid budget channel")
        self.source, self.budget, self.channel = source, budget, channel

    def read(self: BudgetedReader, size: int = -1) -> bytes:
        """Read size bytes within budget; update counters or raise ValueError/OSError.

        Negative one requests the remaining budget, never an unbounded stream read.
        Deadline checks are cooperative, including after a blocking read returns.
        """
        if type(size) is not int or size < -1:
            raise ValueError("Invalid read size")
        key = self.channel + "_bytes"
        remaining = LIMITS[key] - getattr(self.budget, key)
        if time.monotonic() > self.budget.deadline or remaining <= 0 or size > remaining:
            raise ValueError("budget-exhausted: read or deadline")
        data = self.source.read(remaining if size == -1 else size)
        setattr(self.budget, key, getattr(self.budget, key) + len(data))
        if time.monotonic() > self.budget.deadline:
            raise ValueError("budget-exhausted: deadline")
        return data

    def tell(self: BudgetedReader) -> int:
        """Return underlying source position without reading; propagate unsupported position."""
        return self.source.tell()

    def seekable(self: BudgetedReader) -> bool:
        """Return whether raw source supports seeking; expanded sources never do."""
        if self.channel != "raw" or not self.source.seekable():
            return False
        try:
            return stat.S_ISREG(os.fstat(self.source.fileno()).st_mode)
        except (OSError, io.UnsupportedOperation):
            return False

    def seek(self: BudgetedReader, offset: int, whence: int = 0) -> int:
        """Seek raw source to validated offset/whence; reject invalid or expanded seeks."""
        if not self.seekable():
            raise io.UnsupportedOperation("Expanded streams are not seekable")
        if type(offset) is not int or type(whence) is not int or whence not in (0, 1, 2):
            raise ValueError("Invalid seek")
        if time.monotonic() > self.budget.deadline:
            raise ValueError("budget-exhausted: deadline")
        if whence == 2:
            position = os.fstat(self.source.fileno()).st_size + offset
        else:
            position = offset + (self.tell() if whence == 1 else 0)
        if position < 0:
            raise ValueError("Negative seek")
        return self.source.seek(position)


def validate_database_document(document: JSONObject, kind: str) -> None:
    """Validate document against closed kind schema, finite JSON and safe relative locators.

    Supported kinds are source-list, inspection, sample-reference, sample-read,
    candidate and detection. No I/O occurs; malformed documents raise ValueError.
    """
    schemas = {"source-list": SOURCE_LIST_SCHEMA, "inspection": INSPECTION_SCHEMA, "sample-reference": SAMPLE_REFERENCE_SCHEMA, "sample-read": SAMPLE_READ_SCHEMA, "candidate": CANDIDATE_SCHEMA, "detection": DETECTION_SCHEMA}
    if kind not in schemas or not isinstance(document, dict):
        raise ValueError("Invalid database document kind or value")
    try:
        json.dumps(document, allow_nan=False)
        Draft202012Validator(schemas[kind]).validate(document)
    except Exception as error:
        raise ValueError("Invalid database " + kind + " document") from error
    pending = [document]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            for key, value in item.items():
                if key in ("relative_path", "member_path") and value is not None:
                    if not isinstance(value, str) or "\\" in value or "\0" in value or (value and (value.startswith("/") or any(part in ("", ".", "..") or os.path.isreserved(part) or any(character in '<>:"|?*' or ord(character) < 32 for character in part) for part in value.split("/")))) or (key == "member_path" and value == ""):
                        raise ValueError("Unsafe database locator")
                pending.append(value)
        elif isinstance(item, list):
            pending.extend(item)
    if kind == "sample-reference":
        member = document["member_path"]
        if member is not None and document["container"] not in ("zip", "tar", "tar_gzip"):
            raise ValueError("Invalid member container")
        if member is None and document["container"] in ("zip", "tar", "tar_gzip", "directory"):
            raise ValueError("Archive member required")


def list_database_sources(paths: ProjectPaths) -> JSONObject:
    """Reload settings for paths and stat registered sources only; return SourceList.

    Unsafe ancestors and unavailable paths become explicit issues. No children are
    scanned, no source is created and no index is updated. Config errors propagate.
    """
    if paths != get_project_paths(paths.root):
        raise ValueError("Invalid project paths")
    sources = []
    for path in load_config(paths.root / "settings.toml").database_paths:
        identity = "db_" + hashlib.sha256(os.path.normcase(os.path.normpath(str(path))).encode("utf-8")).hexdigest()
        source = {"source_id": identity, "path": str(path), "state": "configured", "issues": []}
        try:
            for ancestor in (*reversed(path.parents), path):
                metadata = ancestor.lstat()
                if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    source["state"] = "unsafe"
                    source["issues"].append("Filesystem reparse points are not allowed")
                    break
            if source["state"] == "configured" and not (path.is_file() or path.is_dir()):
                source["state"] = "unsafe"
                source["issues"].append("Source must be a regular file or directory")
        except OSError as error:
            source["state"] = "unavailable"
            source["issues"].append(f"Unavailable: {type(error).__name__}")
        sources.append(source)
    result = {"schema_version": 1, "sources": sources, "issues": []}
    validate_database_document(result, "source-list")
    return result


def _resolve_database_source(paths: ProjectPaths, source_id: str) -> Path:
    """Reload paths registration and return exact safe source_id path; perform no writes.

    Unknown/removed IDs raise ValueError, unsafe sources OSError and unavailable
    paths FileNotFoundError. Source access never falls back to caller path strings.
    """
    if not isinstance(source_id, str) or re.fullmatch(r"db_[0-9a-f]{64}", source_id) is None:
        raise ValueError("Invalid source_id")
    for source in list_database_sources(paths)["sources"]:
        if source["source_id"] == source_id:
            if source["state"] == "unsafe":
                raise OSError(errno.ELOOP, "Unsafe registered source")
            if source["state"] != "configured":
                raise FileNotFoundError("Unavailable registered source")
            return Path(source["path"])
    raise ValueError("not-found: unconfigured source_id")


def _read_inspection_sample(path: Path, reference: JSONObject | None, budget: InspectionBudget) -> JSONObject:
    """Return bounded prefix Sample from regular path or supplied archive locator.

    None constructs a plain-file reference. Internal locators without a digest
    construct evidence; public pinned references verify size/mtime/hash. Reads use
    shared budget, reject unsafe/ambiguous members, check ancestors and before/after
    metadata, and raise ValueError on integrity/budget failures or OSError on links.
    No extraction or source mutation occurs; syntax summaries carry no semantics.
    """
    reference = dict(reference or {"relative_path": "", "member_path": None, "container": "file", "source_id": "db_" + "0" * 64})
    for ancestor in (*reversed(path.parents), path):
        metadata = ancestor.lstat()
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise OSError(errno.ELOOP, "Unsafe source ancestor")
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        raise OSError("Source sample requires a regular file")
    pinned = "sample_sha256" in reference
    if pinned and (before.st_size != reference["file_size_bytes"] or before.st_mtime_ns != reference["file_mtime_ns"]):
        raise ValueError("integrity: source metadata changed")
    if budget.samples >= LIMITS["samples"] or budget.sample_bytes >= LIMITS["sample_bytes"]:
        raise ValueError("budget-exhausted: samples")
    size = reference["sample_size_bytes"] if pinned else min(LIMITS["sample_size_bytes"], LIMITS["sample_bytes"] - budget.sample_bytes)
    member, container = reference["member_path"], reference["container"]
    if container == "sqlite":
        size = min(size, 16)
    data = None
    with path.open("rb") as source:
        opened = os.fstat(source.fileno())
        if (opened.st_size, opened.st_mtime_ns, opened.st_ino) != (before.st_size, before.st_mtime_ns, before.st_ino):
            raise ValueError("integrity: source replaced before read")
        raw = BudgetedReader(source, budget, "raw")
        if container == "zip":
            raw.seek(max(0, before.st_size - 65557))
            tail = raw.read(min(before.st_size, 65557))
            offset = tail.rfind(b"PK\x05\x06")
            if offset < 0 or len(tail) - offset < 22:
                raise ValueError("unsupported-container: ZIP end record")
            fields = struct.unpack_from("<4s4H2LH", tail, offset)
            if fields[1] or fields[2] or fields[3] != fields[4] or fields[4] > 256 or fields[5] > LIMITS["zip_directory_bytes"] or fields[5] == 0xffffffff or fields[6] == 0xffffffff or b"PK\x06\x07" in tail[max(0, offset - 20):offset]:
                raise ValueError("unsupported-container: ZIP limits, ZIP64 or multidisk")
            raw.seek(0)
            with zipfile.ZipFile(raw) as archive:
                if len(archive.infolist()) != fields[4]:
                    raise ValueError("unsupported-container: inconsistent ZIP entry count")
                seen = set()
                selected = None
                for item in archive.infolist():
                    if time.monotonic() > budget.deadline:
                        raise ValueError("budget-exhausted: deadline")
                    name = item.filename.rstrip("/") if item.is_dir() else item.filename
                    validate_database_document({**reference, "schema_version": 1, "member_path": name, "file_size_bytes": before.st_size, "file_mtime_ns": before.st_mtime_ns, "sample_offset": 0, "sample_size_bytes": 0, "sample_sha256": "0" * 64}, "sample-reference")
                    if name.casefold() in seen or stat.S_IFMT(item.external_attr >> 16) not in (0, stat.S_IFREG, stat.S_IFDIR) or item.flag_bits & 1:
                        raise ValueError("unsafe: duplicate, linked or encrypted ZIP member")
                    seen.add(name.casefold())
                    if name == member and not item.is_dir():
                        selected = item
                if selected is None:
                    raise ValueError("integrity: archive member unavailable")
                with archive.open(selected) as stream:
                    data = BudgetedReader(stream, budget, "expanded").read(min(size, selected.file_size))
        elif container in ("tar", "tar_gzip"):
            expanded_source = gzip.GzipFile(fileobj=raw) if container == "tar_gzip" else raw
            try:
                expanded = BudgetedReader(expanded_source, budget, "expanded")
                with tarfile.open(fileobj=expanded, mode="r|", bufsize=512) as archive:
                    seen = set()
                    for item in archive:
                        if len(seen) >= LIMITS["entries"]:
                            raise ValueError("budget-exhausted: archive entries")
                        name = item.name.rstrip("/") if item.isdir() else item.name
                        validate_database_document({**reference, "schema_version": 1, "member_path": name, "file_size_bytes": before.st_size, "file_mtime_ns": before.st_mtime_ns, "sample_offset": 0, "sample_size_bytes": 0, "sample_sha256": "0" * 64}, "sample-reference")
                        if name.casefold() in seen or not (item.isfile() or item.isdir()):
                            raise ValueError("unsafe: duplicate or nonregular TAR member")
                        seen.add(name.casefold())
                        if item.size > LIMITS["expanded_bytes"] - budget.expanded_bytes:
                            raise ValueError("budget-exhausted: TAR payload skip")
                        if name == member and item.isfile():
                            stream = archive.extractfile(item)
                            if stream is not None:
                                with stream:
                                    data = stream.read(min(size, item.size))
            finally:
                if container == "tar_gzip":
                    expanded_source.close()
            if data is None:
                raise ValueError("integrity: archive member unavailable")
        elif container == "gzip":
            with gzip.GzipFile(fileobj=raw) as stream:
                data = BudgetedReader(stream, budget, "expanded").read(size)
        else:
            data = raw.read(size)
        after_open = os.fstat(source.fileno())
    after = path.stat()
    for ancestor in (*reversed(path.parents), path):
        metadata = ancestor.lstat()
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise OSError(errno.ELOOP, "Unsafe source ancestor after read")
    if any((value.st_size, value.st_mtime_ns, value.st_ino) != (before.st_size, before.st_mtime_ns, before.st_ino) for value in (after_open, after)):
        raise ValueError("integrity: source changed during read")
    digest = hashlib.sha256(data).hexdigest()
    if pinned and (digest != reference["sample_sha256"] or len(data) != reference["sample_size_bytes"]):
        raise ValueError("integrity: sampled prefix changed")
    budget.samples += 1
    budget.sample_bytes += len(data)
    ref = {"schema_version": 1, "source_id": reference["source_id"], "relative_path": reference["relative_path"], "member_path": member, "container": container, "file_size_bytes": before.st_size, "file_mtime_ns": before.st_mtime_ns, "sample_offset": 0, "sample_size_bytes": len(data), "sample_sha256": digest}
    structure = None
    try:
        content = data.decode("utf-8")
        if "\0" in content:
            raise UnicodeError("Binary NUL")
        encoding = "utf-8"
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                structure = {"json_keys": [str(key)[:256] for key in list(parsed)[:64]]}
        except (ValueError, RecursionError):
            first = content.split("\n", 1)[0][:4096]
            try:
                parsed = json.loads(first)
                if isinstance(parsed, dict):
                    structure = {"json_keys": [str(key)[:256] for key in list(parsed)[:64]]}
            except (ValueError, RecursionError):
                pass
            separator = "\t" if "\t" in first else "," if "," in first else None
            if structure is None and separator:
                structure = {"header_candidates": [value[:256] for value in first.split(separator)[:64]]}
    except UnicodeError:
        encoding, content = "base64", base64.b64encode(data).decode("ascii")
    return {"reference": ref, "encoding": encoding, "content": content, "structure": structure}


def _inspect_container(path: Path, relative_path: str, budget: InspectionBudget) -> JSONObject:
    """Inspect path beneath relative_path within shared budget; return inventory/samples/issues/limited.

    Recursion is at most two directory levels. Archive metadata and prefix samples
    are read without extraction. Unsafe paths, unsupported packaging and exhausted
    budgets become explicit issues and partial coverage, never scientific mappings.
    """
    result = {"inventory": [], "samples": [], "issues": [], "limited": False}
    try:
        if time.monotonic() > budget.deadline or budget.entries >= LIMITS["entries"]:
            raise ValueError("budget-exhausted: entries or deadline")
        for ancestor in (*reversed(path.parents), path):
            metadata = ancestor.lstat()
            if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise OSError(errno.ELOOP, "Unsafe filesystem reparse point")
        metadata = path.stat()
        validate_database_document({"schema_version": 1, "source_id": "db_" + "0" * 64, "relative_path": relative_path, "member_path": None, "container": "file", "file_size_bytes": 0, "file_mtime_ns": 0, "sample_offset": 0, "sample_size_bytes": 0, "sample_sha256": "0" * 64}, "sample-reference")
        budget.entries += 1
        if stat.S_ISDIR(metadata.st_mode):
            if relative_path and len(relative_path.split("/")) > LIMITS["depth"]:
                raise ValueError("budget-exhausted: directory depth")
            result["inventory"].append({"relative_path": relative_path, "member_path": None, "container": "directory", "kind": "directory", "size_bytes": 0})
            children = []
            with os.scandir(path) as entries:
                for entry in entries:
                    if len(children) >= LIMITS["entries"] - budget.entries:
                        result["limited"] = True
                        result["issues"].append("budget-exhausted: directory inventory; only scanned subset sorted")
                        break
                    children.append(entry.name)
            for name in sorted(children, key=lambda value: (value.casefold(), value)):
                child_relative = relative_path + "/" + name if relative_path else name
                child = _inspect_container(path / name, child_relative, budget)
                for key in ("inventory", "samples", "issues"):
                    result[key].extend(child[key])
                result["limited"] = result["limited"] or child["limited"]
                if budget.entries >= LIMITS["entries"] or time.monotonic() > budget.deadline:
                    result["limited"] = True
                    break
            return result
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError("Unsafe nonregular source")
        container = "unknown"
        with path.open("rb") as source:
            raw = BudgetedReader(source, budget, "raw")
            header = raw.read(min(16, metadata.st_size))
            if not header.startswith(b"SQLite format 3\0"):
                header += raw.read(min(496, max(0, metadata.st_size - len(header))))
            if header.startswith(b"SQLite format 3\0"):
                container = "sqlite"
            elif header.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
                container = "zip"
            elif header.startswith(b"\x1f\x8b"):
                container = "gzip"
                raw.seek(0)
                with gzip.GzipFile(fileobj=raw) as compressed:
                    expanded_header = BudgetedReader(compressed, budget, "expanded").read(512)
                try:
                    tarfile.TarInfo.frombuf(expanded_header, "utf-8", "strict")
                    container = "tar_gzip"
                except (tarfile.HeaderError, UnicodeError):
                    pass
            else:
                try:
                    tarfile.TarInfo.frombuf(header, "utf-8", "strict")
                    container = "tar"
                except (tarfile.HeaderError, UnicodeError):
                    try:
                        if b"\0" not in header:
                            header.decode("utf-8")
                            container = "text"
                    except UnicodeError:
                        pass
        result["inventory"].append({"relative_path": relative_path, "member_path": None, "container": container, "kind": "regular", "size_bytes": metadata.st_size})
        locator = {"source_id": "db_" + "0" * 64, "relative_path": relative_path, "member_path": None, "container": container}
        members = []
        if container in ("zip", "tar", "tar_gzip"):
            with path.open("rb") as source:
                raw = BudgetedReader(source, budget, "raw")
                if container == "zip":
                    raw.seek(max(0, metadata.st_size - 65557))
                    tail = raw.read(min(metadata.st_size, 65557))
                    offset = tail.rfind(b"PK\x05\x06")
                    if offset < 0 or len(tail) - offset < 22:
                        raise ValueError("unsupported-container: ZIP end record")
                    fields = struct.unpack_from("<4s4H2LH", tail, offset)
                    if fields[1] or fields[2] or fields[3] != fields[4] or fields[4] > LIMITS["entries"] - budget.entries or fields[5] > LIMITS["zip_directory_bytes"] or fields[5] == 0xffffffff or fields[6] == 0xffffffff or b"PK\x06\x07" in tail[max(0, offset - 20):offset]:
                        raise ValueError("unsupported-container: ZIP limits, ZIP64 or multidisk")
                    raw.seek(0)
                    archive = zipfile.ZipFile(raw)
                    entries = archive.infolist()
                    if len(entries) != fields[4]:
                        archive.close()
                        raise ValueError("unsupported-container: inconsistent ZIP entry count")
                else:
                    expanded_source = gzip.GzipFile(fileobj=raw) if container == "tar_gzip" else raw
                    archive = tarfile.open(fileobj=BudgetedReader(expanded_source, budget, "expanded"), mode="r|", bufsize=512)
                    entries = archive
                seen = set()
                try:
                    for item in entries:
                        if time.monotonic() > budget.deadline or budget.entries >= LIMITS["entries"]:
                            raise ValueError("budget-exhausted: archive entries/deadline")
                        is_dir = item.is_dir() if container == "zip" else item.isdir()
                        name = (item.filename if container == "zip" else item.name).rstrip("/") if is_dir else item.filename if container == "zip" else item.name
                        validate_database_document({**locator, "schema_version": 1, "member_path": name, "file_size_bytes": 0, "file_mtime_ns": 0, "sample_offset": 0, "sample_size_bytes": 0, "sample_sha256": "0" * 64}, "sample-reference")
                        unsafe = stat.S_IFMT(item.external_attr >> 16) not in (0, stat.S_IFREG, stat.S_IFDIR) or item.flag_bits & 1 if container == "zip" else not (item.isfile() or item.isdir())
                        if unsafe or name.casefold() in seen:
                            raise ValueError("unsafe: duplicate, linked, encrypted or nonregular archive member")
                        seen.add(name.casefold())
                        size = item.file_size if container == "zip" else item.size
                        if container != "zip" and size > LIMITS["expanded_bytes"] - budget.expanded_bytes:
                            raise ValueError("budget-exhausted: TAR payload skip")
                        budget.entries += 1
                        result["inventory"].append({"relative_path": relative_path, "member_path": name, "container": container, "kind": "directory" if is_dir else "regular", "size_bytes": size})
                        if not is_dir:
                            members.append(name)
                finally:
                    archive.close()
                    if container == "tar_gzip":
                        expanded_source.close()
            for name in members:
                if budget.samples >= LIMITS["samples"] or budget.sample_bytes >= LIMITS["sample_bytes"]:
                    result["limited"] = True
                    result["issues"].append("budget-exhausted: sample selection")
                    break
                result["samples"].append(_read_inspection_sample(path, {**locator, "member_path": name}, budget))
        else:
            if budget.samples < LIMITS["samples"] and budget.sample_bytes < LIMITS["sample_bytes"]:
                result["samples"].append(_read_inspection_sample(path, locator, budget))
            else:
                result["limited"] = True
                result["issues"].append("budget-exhausted: sample selection")
        after = path.stat()
        if (after.st_size, after.st_mtime_ns, after.st_ino) != (metadata.st_size, metadata.st_mtime_ns, metadata.st_ino):
            result["samples"] = []
            raise ValueError("integrity: source changed during inspection")
    except (OSError, ValueError, EOFError, tarfile.TarError, zipfile.BadZipFile, zlib.error, NotImplementedError, RuntimeError) as error:
        result["limited"] = True
        result["issues"].append(str(error)[:4096] or type(error).__name__)
        if "unsafe" in str(error).lower() or "locator" in str(error).lower() or isinstance(error, OSError) and error.errno == errno.ELOOP:
            result["samples"] = []
    return result


def inspect_database_source(paths: ProjectPaths, source_id: str) -> JSONObject:
    """Reload configured source_id under paths and return bounded InspectionReport.

    Unknown IDs raise ValueError; unavailable/unsafe registered sources return
    reports. No files, index records or scientific jobs are created. Samples pin
    prefixes only; packaging detection does not recognize a scientific schema.
    """
    listed = list_database_sources(paths)
    source = next((item for item in listed["sources"] if item["source_id"] == source_id), None)
    if source is None:
        raise ValueError("not-found: unconfigured source_id")
    budget = InspectionBudget()
    report = {"schema_version": 1, "inspection_id": str(uuid4()), "source": source, "inspection_state": source["state"], "inventory": [], "samples": [], "format_detection": {"state": "unknown", "candidates": []}, "limits": dict(LIMITS), "usage": {}, "issues": list(source["issues"]), "recommended_skill": "external-database-onboarding"}
    if source["state"] == "configured":
        path = _resolve_database_source(paths, source_id)
        inspected = _inspect_container(path, "", budget)
        report.update({key: inspected[key] for key in ("inventory", "samples", "issues")})
        for sample in report["samples"]:
            sample["reference"]["source_id"] = source_id
        report["inspection_state"] = "limited" if inspected["limited"] else "inspected"
        if any("unsafe" in issue.lower() or "locator" in issue.lower() for issue in report["issues"]):
            report["inspection_state"] = "unsafe"
        elif any("unsupported-container:" in issue for issue in report["issues"]):
            report["inspection_state"] = "unsupported_container"
        report["format_detection"] = probe_database_adapters({"inventory": report["inventory"], "samples": report["samples"], "issues": report["issues"]})
    report["usage"] = {key: getattr(budget, key) for key in ("raw_bytes", "expanded_bytes", "entries", "samples", "sample_bytes")}
    validate_database_document(report, "inspection")
    return report


def read_database_sample(paths: ProjectPaths, reference: JSONObject) -> JSONObject:
    """Return sample-only verified evidence for pinned reference under paths registrations.

    Re-resolve registration and safe descendants each call. Removed references,
    changed stat/hash or malformed locators raise ValueError; unsafe files raise
    OSError. Source contents and index are never changed or refreshed.
    """
    validate_database_document(reference, "sample-reference")
    root = _resolve_database_source(paths, reference["source_id"])
    relative = reference["relative_path"]
    if root.is_file() and relative or root.is_dir() and not relative:
        raise ValueError("Invalid registered source-relative sample")
    path = root.joinpath(*relative.split("/")) if relative else root
    sample = _read_inspection_sample(path, reference, InspectionBudget())
    result = {"schema_version": 1, "reference": sample["reference"], "encoding": sample["encoding"], "content": sample["content"], "verification": "sample_only"}
    validate_database_document(result, "sample-read")
    return result
