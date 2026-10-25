"""Read registered SNUMAT archives with explicit bounds, hashing and no extraction."""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextvars import ContextVar
import csv
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import tarfile
import time
from uuid import uuid4

from src.backend.paths import ProjectPaths
from src.backend.resources import _unique_object
from src.knowledge.database_sources import list_database_sources
from src.knowledge.external_schema import MANIFEST_FIELDS, validate_external_document
from src.knowledge.schema import _bounded_decimal
from src.srim.common.files import _read_regular_bytes
from src.srim.common.types import JSONObject

ARCHIVES = ("snumat20k_v1_metadata.tar.gz",) + tuple("snumat20k_v1_part" + str(i).zfill(2) + "-of-10.tar.gz" for i in range(1, 11))
DATASET_ROOT = "snumat-20k_SRIM_dataset_v1"
SELECTED = ("case.json", "TRIM.IN", "TDATA.txt", "validation.json", "run_metadata.json", "RANGE.txt")
MAX_COMPRESSED = 1024 * 1024 * 1024
MAX_EXPANDED = 512 * 1024 * 1024
MAX_ENTRIES = 32768
MAX_CASES = 100000
MAX_MEMBER = 1048576
MAX_METADATA = 32 * 1024 * 1024
MAX_SPOOL = 512 * 1024 * 1024
SOURCE_PROGRESS: ContextVar[Callable[[JSONObject], None] | None] = ContextVar("external_source_progress", default=None)
SOURCE_DEADLINE: ContextVar[float | None] = ContextVar("external_source_deadline", default=None)
PAX_MAX_BYTES = 65536
PAX_MAX_RECORDS = 32
PAX_KEYS = {"atime", "ctime", "mtime", "path", "size"}


def resolve_external_source(paths: ProjectPaths, source_id: str) -> Path:
    """Resolve currently registered source_id under paths to an existing directory.

    Reload registrations on every call; return its absolute Path without writes.
    Raise ValueError for an unknown/revoked ID and OSError for unavailable,
    non-directory or linked sources. No missing replacement is created.
    """
    if not isinstance(source_id, str) or re.fullmatch("db_[0-9a-f]{64}", source_id) is None:
        raise ValueError("external: invalid source ID")
    for source in list_database_sources(paths)["sources"]:
        if source["source_id"] == source_id:
            if source["state"] != "configured":
                raise OSError("external: unavailable/unsafe registered source")
            path = Path(source["path"])
            if not path.is_dir():
                raise OSError("external: supported SNUMAT source must be a directory")
            for ancestor in (*reversed(path.parents), path):
                if ancestor.is_symlink() or ancestor.is_junction():
                    raise OSError("external: unsafe source path")
            return path
    raise ValueError("external: unregistered or revoked source")


def fingerprint_external_source(paths: ProjectPaths, source_id: str, *, cancel: Callable[[], bool]) -> JSONObject:
    """Fully hash selected source files, returning sorted source fingerprints.

    paths/source_id identify one registered directory; cancel is checked per
    1 MiB chunk. Verify root SHA256SUMS archive claims, exact v1 archive set and
    stable file stats before/after each read. No writes occur. Raise ValueError
    for integrity/schema/limits, OSError for unsafe/unavailable files and
    InterruptedError for cancellation. The ten-minute timeout honors SOURCE_DEADLINE when the managed operation sets it.
    """
    root = resolve_external_source(paths, source_id)
    deadline = SOURCE_DEADLINE.get() if SOURCE_DEADLINE.get() is not None else time.monotonic() + 600
    entries = list(root.iterdir())
    if len([p for p in entries if p.name.endswith((".gz", ".tar", ".zip"))]) > 16:
        raise ValueError("external: root archive count limit exceeded")
    archive_names = sorted(p.name for p in entries if p.name.endswith((".gz", ".tar", ".zip")))
    if archive_names != sorted(ARCHIVES):
        raise ValueError("external: incomplete/unsupported SNUMAT v1 archive set")
    checksum_path = root / "SHA256SUMS.txt"
    if checksum_path.is_symlink() or checksum_path.is_junction() or not checksum_path.is_file() or checksum_path.stat().st_size > MAX_MEMBER:
        raise OSError("external: unsafe/unavailable checksum file")
    checksum_bytes = _read_regular_bytes(checksum_path)
    claims = {}
    for line in checksum_bytes.decode("ascii").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64}) [ *]([^/\\]+)", line)
        if match is None or match[2] in claims:
            raise ValueError("external: invalid/duplicate archive checksum claim")
        claims[match[2]] = match[1]
    if set(claims) != set(ARCHIVES):
        raise ValueError("external: archive checksum inventory mismatch")
    result = {"source_id": source_id, "adapter_id": "snumat-trim-v1", "adapter_version": 1, "files": []}
    for name in sorted(("README.md", "SHA256SUMS.txt", *ARCHIVES)):
        path = root / name
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or path.is_symlink() or path.is_junction():
            raise OSError("external: unsafe source file")
        limit = MAX_COMPRESSED if name in ARCHIVES else MAX_MEMBER
        if metadata.st_size > limit:
            raise ValueError("external: source file size limit exceeded")
        digest, total = hashlib.sha256(), 0
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            while True:
                if cancel():
                    raise InterruptedError("external: cancelled during fingerprint")
                if time.monotonic() > deadline:
                    raise ValueError("external: fingerprint deadline exceeded")
                chunk = stream.read(1048576)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise ValueError("external: changed source exceeds size cap")
                digest.update(chunk)
            closed = os.fstat(stream.fileno())
        final = path.lstat()
        observed = [(m.st_dev, m.st_ino, m.st_size, m.st_mtime_ns) for m in (metadata, opened, closed, final)]
        if any(item != observed[0] for item in observed[1:]) or total != metadata.st_size:
            raise ValueError("external: source changed during fingerprint")
        value = digest.hexdigest()
        if name in claims and value != claims[name] or name == "SHA256SUMS.txt" and hashlib.sha256(checksum_bytes).hexdigest() != value:
            raise ValueError("external: full source checksum mismatch " + name)
        result["files"].append({"path": name, "size_bytes": total, "sha256": value})
    resolve_external_source(paths, source_id)
    if sorted(p.name for p in root.iterdir() if p.name.endswith((".gz", ".tar", ".zip"))) != archive_names:
        raise ValueError("external: archive set changed during fingerprint")
    return result


def iter_snumat_cases(paths: ProjectPaths, source_id: str, fingerprints: JSONObject, *, cancel: Callable[[], bool], inventory_only: bool = False) -> Iterator[JSONObject]:
    """Yield grouped selected members, locators and issues for a verified source.

    inventory_only must be bool; true hashes and validates the same payloads
    without retaining or spooling physical case bytes. Metadata join witnesses
    and selected-member locators are still yielded.
    paths/source_id select the registered directory and fingerprints pins full
    source hashes. cancel is checked per member/chunk. Sequential bounded gzip/TAR
    reading validates raw headers before payload reads; local PAX headers are bounded
    to 64 KiB/32 records with only times/path/size keys, and sparse/link/global or
    unknown overrides reject. Only selected small case
    evidence is spooled under a fresh local staging operation, never extracted to
    source paths. Metadata joins and checksum claims are compared. Yield case_id,
    members, locators, issues; no per-ion payload is retained. Generated spool is
    removed in finally, while source files remain untouched. Safety, integrity,
    size and deadline failures raise ValueError/OSError; cancel raises
    InterruptedError. Individual malformed cases remain rejection diagnostics. SOURCE_PROGRESS, when
    set by the managed operation, receives one archive-completed event after each
    actual archive passes streaming and full-handle fingerprint checks.
    """
    if type(inventory_only) is not bool:
        raise ValueError("external: inventory_only must be bool")
    root = resolve_external_source(paths, source_id)
    if fingerprints != fingerprint_external_source(paths, source_id, cancel=cancel):
        raise ValueError("external: stale fingerprint before streaming")
    deadline = SOURCE_DEADLINE.get() if SOURCE_DEADLINE.get() is not None else time.monotonic() + 600
    spool = paths.root / "memory/knowledge/external/staging" / str(uuid4()) / "spool"
    for ancestor in (*reversed(spool.parents), spool):
        if ancestor.is_symlink() or ancestor.is_junction():
            raise OSError("external: unsafe staging path")
    spool.mkdir(parents=True, exist_ok=False)
    manifest, mapping, metadata_locators, global_hashes, cases = {}, {}, {}, {}, {}
    spool_bytes = 0
    try:
        for archive_name in ARCHIVES:
            seen, expanded, entries, terminated, pending_pax = set(), 0, 0, False, {}
            archive_path = root / archive_name
            archive_stat = archive_path.lstat()
            if not stat.S_ISREG(archive_stat.st_mode) or archive_path.is_symlink() or archive_path.is_junction():
                raise OSError("external: unsafe archive before stream")
            expected_archive = next(item for item in fingerprints["files"] if item["path"] == archive_name)
            with archive_path.open("rb") as compressed, gzip.GzipFile(fileobj=compressed, mode="rb") as stream:
                while True:
                    if cancel():
                        raise InterruptedError("external: cancelled during archive stream")
                    if time.monotonic() > deadline:
                        raise ValueError("external: archive operation deadline exceeded")
                    header = stream.read(512)
                    expanded += len(header)
                    if not header:
                        if not terminated:
                            raise ValueError("external: TAR has no terminator")
                        break
                    if len(header) != 512 or expanded > MAX_EXPANDED:
                        raise ValueError("external: truncated/oversize TAR")
                    if header == bytes(512):
                        if pending_pax:
                            raise ValueError("external: dangling PAX metadata")
                        terminated = True
                        continue
                    if terminated:
                        raise ValueError("external: nonzero trailing TAR payload")
                    member = tarfile.TarInfo.frombuf(header, "utf-8", "strict")
                    if member.type == tarfile.XHDTYPE:
                        entries += 1
                        if pending_pax or not 1 <= member.size <= PAX_MAX_BYTES or entries > MAX_ENTRIES or expanded + ((member.size + 511) // 512) * 512 > MAX_EXPANDED:
                            raise ValueError("external: chained/oversize PAX metadata")
                        payload = stream.read(member.size)
                        expanded += len(payload)
                        if len(payload) != member.size:
                            raise ValueError("external: truncated PAX metadata")
                        padding = (-member.size) % 512
                        if padding:
                            tail = stream.read(padding)
                            expanded += len(tail)
                            if len(tail) != padding:
                                raise ValueError("external: truncated PAX padding")
                        cursor, record_count = 0, 0
                        while cursor < len(payload):
                            separator = payload.find(b" ", cursor, min(len(payload), cursor + 12))
                            if separator < 0 or not payload[cursor:separator].isdigit():
                                raise ValueError("external: malformed PAX record length")
                            length = int(payload[cursor:separator])
                            if length <= separator - cursor + 2 or cursor + length > len(payload):
                                raise ValueError("external: invalid PAX record boundary")
                            record = payload[separator + 1:cursor + length]
                            if not record.endswith(b"\n") or b"\n" in record[:-1] or b"=" not in record:
                                raise ValueError("external: malformed PAX record")
                            key_bytes, value_bytes = record[:-1].split(b"=", 1)
                            key, value = key_bytes.decode("ascii"), value_bytes.decode("utf-8")
                            if key not in PAX_KEYS or key in pending_pax or "\0" in value:
                                raise ValueError("external: unsupported/duplicate/unsafe PAX key")
                            record_count += 1
                            if record_count > PAX_MAX_RECORDS:
                                raise ValueError("external: PAX record count cap exceeded")
                            if key in ("atime", "ctime", "mtime"):
                                if re.fullmatch(r"[+-]?[0-9]+(?:\.[0-9]+)?", value) is None:
                                    raise ValueError("external: invalid PAX time metadata")
                                _bounded_decimal(value)
                            elif key == "size":
                                if re.fullmatch(r"[0-9]{1,12}", value) is None or int(value) > MAX_EXPANDED:
                                    raise ValueError("external: invalid PAX size override")
                            elif key == "path" and (not value or len(value.encode("utf-8")) > 4096):
                                raise ValueError("external: invalid PAX path size")
                            pending_pax[key] = value
                            cursor += length
                        continue
                    if pending_pax:
                        if "path" in pending_pax:
                            member.name = pending_pax["path"]
                        if "size" in pending_pax:
                            member.size = int(pending_pax["size"])
                        pending_pax = {}
                    name = member.name.rstrip("/") if member.isdir() else member.name
                    if not name or "\\" in name or any(part in ("", ".", "..") or os.path.isreserved(part) or re.search(r'[<>:"|?*\x00-\x1f]', part) or part.endswith((".", " ")) for part in name.split("/")) or name != DATASET_ROOT and not name.startswith(DATASET_ROOT + "/"):
                        raise ValueError("external: unsafe archive member")
                    if name.casefold() in seen or member.sparse is not None or member.type == tarfile.GNUTYPE_SPARSE or not (member.isfile() or member.isdir()) or member.isdir() and member.size != 0:
                        raise ValueError("external: duplicate/linked/sparse/nonregular member")
                    seen.add(name.casefold())
                    entries += 1
                    if entries > MAX_ENTRIES or expanded + ((member.size + 511) // 512) * 512 > MAX_EXPANDED:
                        raise ValueError("external: archive entry/expansion limit exceeded")
                    relative = name[len(DATASET_ROOT) + 1:] if name != DATASET_ROOT else ""
                    pieces = relative.split("/")
                    selected_case = len(pieces) == 4 and pieces[0] == "cases" and pieces[-1] in SELECTED and member.isfile()
                    selected_metadata = archive_name == ARCHIVES[0] and relative in ("manifest.csv", "case_mapping.csv", "SHA256SUMS") and member.isfile()
                    if selected_case and member.size > MAX_MEMBER or selected_metadata and member.size > MAX_METADATA:
                        raise ValueError("external: selected member exceeds cap")
                    digest, remaining, retained = hashlib.sha256(), member.size, bytearray()
                    while remaining:
                        if cancel():
                            raise InterruptedError("external: cancelled during member read")
                        if time.monotonic() > deadline:
                            raise ValueError("external: archive operation deadline exceeded")
                        chunk = stream.read(min(1048576, remaining))
                        if not chunk:
                            raise ValueError("external: truncated TAR member")
                        remaining -= len(chunk)
                        expanded += len(chunk)
                        digest.update(chunk)
                        if selected_metadata or selected_case and not inventory_only:
                            retained.extend(chunk)
                    padding = (-member.size) % 512
                    if padding:
                        tail = stream.read(padding)
                        expanded += len(tail)
                        if len(tail) != padding:
                            raise ValueError("external: truncated TAR padding")
                    value = digest.hexdigest()
                    locator = {"source_id": source_id, "relative_path": archive_name, "member_path": name, "member_sha256": value, "size_bytes": member.size, "json_pointer": "", "line_start": None, "line_end": None}
                    if selected_metadata:
                        metadata_locators[relative] = locator
                        text = retained.decode("utf-8")
                        if relative == "SHA256SUMS":
                            for line in text.splitlines():
                                match = re.fullmatch(r"([0-9a-f]{64}) [ *](.+)", line)
                                if match is None:
                                    raise ValueError("external: invalid/duplicate member checksum")
                                path = match[2]
                                if path.startswith("./"):
                                    path = path[2:]
                                if not path or "\\" in path or any(part in ("", ".", "..") for part in path.split("/")):
                                    raise ValueError("external: unsafe checksum path")
                                if path in global_hashes:
                                    raise ValueError("external: duplicate normalized checksum path")
                                global_hashes[path] = match[1]
                        else:
                            reader = csv.DictReader(io.StringIO(text, newline=""))
                            if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames) or relative == "manifest.csv" and set(reader.fieldnames) != set(MANIFEST_FIELDS):
                                raise ValueError("external: unsupported metadata header")
                            destination = manifest if relative == "manifest.csv" else mapping
                            previous_line = reader.line_num
                            for row in reader:
                                if None in row or any(value is None for value in row.values()):
                                    raise ValueError("external: malformed metadata CSV")
                                case_id = row.get("case_id")
                                if not isinstance(case_id, str) or re.fullmatch(r"SM-[0-9]+_[A-Za-z]+_(p|n|cov)_d[0-9]+nm", case_id) is None:
                                    raise ValueError("external: invalid metadata case ID")
                                if case_id in destination:
                                    destination[case_id]["duplicate"] = True
                                else:
                                    destination[case_id] = {"row": row, "line_start": previous_line + 1, "line_end": reader.line_num, "duplicate": False}
                                previous_line = reader.line_num
                                if len(destination) > MAX_CASES:
                                    raise ValueError("external: metadata case count cap exceeded")
                    elif selected_case:
                        if archive_name == ARCHIVES[0]:
                            raise ValueError("external: case data in metadata archive")
                        target_id, case_id, filename = pieces[1:]
                        if re.fullmatch(r"SM-[0-9]+_[A-Za-z]+_(p|n|cov)_d[0-9]+nm", case_id) is None or target_id != case_id.split("_")[0]:
                            raise ValueError("external: invalid case member identity")
                        entry = cases.setdefault(case_id, {"members": {}, "locators": {}, "issues": []})
                        if filename in entry["locators"]:
                            raise ValueError("external: duplicate case across archives")
                        if len(cases) > MAX_CASES:
                            raise ValueError("external: case count cap exceeded")
                        if not inventory_only:
                            spool_bytes += len(retained)
                            if spool_bytes > MAX_SPOOL:
                                raise ValueError("external: selected evidence spool cap exceeded")
                            destination = spool / (hashlib.sha256((case_id + "/" + filename).encode()).hexdigest() + ".bin")
                            with destination.open("xb") as output:
                                output.write(retained)
                            entry["members"][filename] = destination
                        entry["locators"][filename] = locator
                        if relative not in global_hashes or global_hashes[relative] != value:
                            raise ValueError("external: dataset member checksum missing/mismatched " + relative)
                compressed.seek(0)
                archive_digest, archive_bytes = hashlib.sha256(), 0
                while chunk := compressed.read(1048576):
                    if cancel():
                        raise InterruptedError("external: cancelled verifying archive handle")
                    if time.monotonic() > deadline:
                        raise ValueError("external: archive deadline exceeded")
                    archive_bytes += len(chunk)
                    if archive_bytes > MAX_COMPRESSED:
                        raise ValueError("external: compressed archive cap exceeded")
                    archive_digest.update(chunk)
                after = os.fstat(compressed.fileno())
                if archive_digest.hexdigest() != expected_archive["sha256"] or archive_bytes != expected_archive["size_bytes"] or (archive_stat.st_dev, archive_stat.st_ino, archive_stat.st_size, archive_stat.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                    raise ValueError("external: streamed archive differs from pinned fingerprint")
            if archive_name == ARCHIVES[0]:
                if not {"manifest.csv", "case_mapping.csv", "SHA256SUMS"} <= set(metadata_locators):
                    raise ValueError("external: missing required metadata tables/checksum inventory")
                for filename in ("manifest.csv", "case_mapping.csv"):
                    if global_hashes.get(filename) != metadata_locators[filename]["member_sha256"]:
                        raise ValueError("external: metadata table checksum mismatch")
            progress = SOURCE_PROGRESS.get()
            if progress is not None:
                progress({"source": "adapter", "phase": "indexing", "worker_ids": [], "simulation_ids": [], "data": {"stage": "archive-completed", "archive": archive_name}})
        if fingerprints != fingerprint_external_source(paths, source_id, cancel=cancel):
            raise ValueError("external: source changed while streaming")
        all_ids = sorted(set(cases) | set(manifest) | set(mapping))
        if len(all_ids) > MAX_CASES:
            raise ValueError("external: joined case count cap exceeded")
        for case_id in all_ids:
            if cancel():
                raise InterruptedError("external: cancelled while yielding cases")
            if time.monotonic() > deadline:
                raise ValueError("external: joined-case deadline exceeded")
            entry = cases.get(case_id, {"members": {}, "locators": {}, "issues": []})
            members = {key: _read_regular_bytes(path) for key, path in entry["members"].items()}
            locators, issues = entry["locators"], entry["issues"]
            if sum(len(value) for value in members.values()) > 8 * 1024 * 1024:
                raise ValueError("external: case buffer cap exceeded")
            for table, rows, key in (("manifest.csv", manifest, "__manifest__.json"), ("case_mapping.csv", mapping, "__mapping__.json")):
                row = rows.get(case_id)
                if row is None or row["duplicate"]:
                    issues.append({"code": "conflicting" if row else "missing", "field": table, "message": "Duplicate or missing metadata join for " + case_id, "locators": []})
                else:
                    members[key] = json.dumps(row["row"], sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
                    locators[key] = {**metadata_locators[table], "json_pointer": None, "line_start": row["line_start"], "line_end": row["line_end"]}
            yield {"case_id": case_id, "members": members, "locators": locators, "issues": issues}
    finally:
        if spool.is_dir() and not spool.is_symlink() and not spool.is_junction() and spool.resolve().is_relative_to((paths.root / "memory/knowledge/external/staging").resolve()):
            shutil.rmtree(spool)


def read_external_evidence(paths: ProjectPaths, reference: JSONObject, locator: JSONObject, *, max_bytes: int = 1048576) -> bytes:
    """Return exact pinned archive-member bytes for reference and locator.

    paths selects the derived external catalog; reference identifies an immutable
    record, locator its source member, and max_bytes caps the returned payload.
    Reload source registration and the latest committed binding, verify object,
    full compressed archive hash and member hash, then return bytes. No writes or
    refresh occur. Raise ValueError for invalid/stale/oversize/unowned evidence
    and OSError/sqlite3.Error for unavailable/unsafe/corrupt storage.
    """
    validate_external_document(reference, "reference")
    validate_external_document(locator, "locator")
    if type(max_bytes) is not int or not 0 <= max_bytes <= MAX_METADATA or locator["source_id"] != reference["source_id"] or locator["size_bytes"] > max_bytes:
        raise ValueError("external: invalid evidence ownership/size")
    root = resolve_external_source(paths, reference["source_id"])
    directory = paths.root / "memory/knowledge/external"
    for path in (*reversed(directory.parents), directory, directory / "index.sqlite", directory / "objects", directory / "snapshots"):
        if path.is_symlink() or path.is_junction():
            raise OSError("external: unsafe catalog evidence path")
    payload = _read_regular_bytes(directory / "objects" / (reference["record_sha256"] + ".json"))
    if hashlib.sha256(payload).hexdigest() != reference["record_sha256"]:
        raise ValueError("external: normalized record hash mismatch")
    record = json.loads(payload, object_pairs_hook=_unique_object)
    validate_external_document(record, "record")
    if any(record[key] != reference[key] for key in ("source_id", "adapter_id", "adapter_version", "case_id", "kind", "schema_version")):
        raise ValueError("external: reference record identity mismatch")
    owned, pending = [], [record]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            if {"source_id", "relative_path", "member_path", "member_sha256", "size_bytes", "json_pointer", "line_start", "line_end"} == set(value) and all(value[key] == locator[key] for key in ("source_id", "relative_path", "member_path", "member_sha256", "size_bytes")):
                owned.append(value)
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    if not owned:
        raise ValueError("external: locator is not owned by record")
    connection = sqlite3.connect((directory / "index.sqlite").as_uri() + "?mode=ro", uri=True, timeout=30)
    try:
        connection.enable_load_extension(False)
        connection.execute("PRAGMA query_only=ON")
        binding = connection.execute("SELECT snapshot_sha256 FROM bindings WHERE source_id=? AND case_id=? AND record_sha256=? ORDER BY rowid DESC LIMIT 1", (reference["source_id"], reference["case_id"], reference["record_sha256"])).fetchone()
    finally:
        connection.close()
    if binding is None or re.fullmatch("[0-9a-f]{64}", binding[0]) is None:
        raise ValueError("external: missing/invalid committed snapshot binding")
    snapshot_bytes = _read_regular_bytes(directory / "snapshots" / (binding[0] + ".json"))
    if hashlib.sha256(snapshot_bytes).hexdigest() != binding[0]:
        raise ValueError("external: snapshot hash mismatch")
    snapshot = json.loads(snapshot_bytes, object_pairs_hook=_unique_object)
    fingerprints = snapshot["source_fingerprints"]
    deadline = SOURCE_DEADLINE.get() if SOURCE_DEADLINE.get() is not None else time.monotonic() + 600
    result = _read_external_members(root, fingerprints, [locator], max_bytes=max_bytes, collect=True, deadline=deadline)
    resolve_external_source(paths, reference["source_id"])
    return result[(locator["relative_path"], locator["member_path"], locator["member_sha256"])]


def _read_external_members(root: Path, fingerprints: JSONObject, locators: list[JSONObject], *, max_bytes: int, collect: bool, deadline: float) -> dict[tuple[str, str, str], bytes | None]:
    """Verify grouped owned locators against pinned archive fingerprints.

    root identifies source; fingerprints and locators are validated owned data.
    collect selects bounded returned bytes versus streamed hashes; max_bytes
    caps total retained bytes; deadline is monotonic. Return member-keyed data
    or None values. No writes occur. Unsafe, changed, missing, oversize or expired
    evidence raises ValueError/OSError. Every handle is closed before returning.
    """
    groups = {}
    retained = 0
    for locator in locators:
        archive_name, member_name = locator["relative_path"], locator["member_path"]
        requested = groups.setdefault(archive_name, {})
        old = requested.get(member_name)
        if old is not None:
            if (old["member_sha256"], old["size_bytes"]) != (locator["member_sha256"], locator["size_bytes"]):
                raise ValueError("external: conflicting member locators")
        else:
            requested[member_name] = locator
            retained += locator["size_bytes"]
    if collect and retained > max_bytes:
        raise ValueError("external: requested evidence exceeds size cap")
    results = {}
    for archive_name, requested in sorted(groups.items()):
        expected = next((item for item in fingerprints["files"] if item["path"] == archive_name), None)
        if expected is None or archive_name not in ARCHIVES:
            raise ValueError("external: archive not in committed snapshot")
        path = root / archive_name
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or path.is_symlink() or path.is_junction() or metadata.st_size > MAX_COMPRESSED:
            raise OSError("external: unsafe archive")
        identity = (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns)
        with path.open("rb") as compressed:
            opened = os.fstat(compressed.fileno())
            opened_identity = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)
            if identity[:4] != opened_identity[:4]:
                raise ValueError("external: archive changed before read")
            digest, total = hashlib.sha256(), 0
            while True:
                if time.monotonic() >= deadline:
                    raise ValueError("external: evidence reader deadline exceeded")
                chunk = compressed.read(1048576)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_COMPRESSED:
                    raise ValueError("external: archive exceeds size cap")
                digest.update(chunk)
            if total != expected["size_bytes"] or digest.hexdigest() != expected["sha256"]:
                raise ValueError("external: original archive integrity mismatch")
            compressed.seek(0)
            seen, expanded, terminated, pending_pax, entries = set(), 0, False, {}, 0
            with gzip.GzipFile(fileobj=compressed, mode="rb") as stream:
                while True:
                    if time.monotonic() > deadline:
                        raise ValueError("external: evidence reader deadline exceeded")
                    header = stream.read(512)
                    expanded += len(header)
                    if not header:
                        if not terminated:
                            raise ValueError("external: missing TAR terminator")
                        break
                    if len(header) != 512 or expanded > MAX_EXPANDED:
                        raise ValueError("external: malformed/oversize archive")
                    if header == bytes(512):
                        if pending_pax:
                            raise ValueError("external: dangling PAX metadata")
                        terminated = True
                        continue
                    if terminated:
                        raise ValueError("external: nonzero trailing TAR data")
                    member = tarfile.TarInfo.frombuf(header, "utf-8", "strict")
                    if member.type == tarfile.XHDTYPE:
                        entries += 1
                        if pending_pax or not 1 <= member.size <= PAX_MAX_BYTES or entries > MAX_ENTRIES or expanded + ((member.size + 511) // 512) * 512 > MAX_EXPANDED:
                            raise ValueError("external: chained/oversize PAX metadata")
                        payload = stream.read(member.size)
                        expanded += len(payload)
                        if len(payload) != member.size:
                            raise ValueError("external: truncated PAX metadata")
                        padding = (-member.size) % 512
                        if padding:
                            tail = stream.read(padding)
                            expanded += len(tail)
                            if len(tail) != padding:
                                raise ValueError("external: truncated PAX padding")
                        cursor, record_count = 0, 0
                        while cursor < len(payload):
                            separator = payload.find(b" ", cursor, min(len(payload), cursor + 12))
                            if separator < 0 or not payload[cursor:separator].isdigit():
                                raise ValueError("external: malformed PAX record length")
                            length = int(payload[cursor:separator])
                            if length <= separator - cursor + 2 or cursor + length > len(payload):
                                raise ValueError("external: invalid PAX record boundary")
                            record = payload[separator + 1:cursor + length]
                            if not record.endswith(b"\n") or b"\n" in record[:-1] or b"=" not in record:
                                raise ValueError("external: malformed PAX record")
                            key_bytes, value_bytes = record[:-1].split(b"=", 1)
                            key, value = key_bytes.decode("ascii"), value_bytes.decode("utf-8")
                            if key not in PAX_KEYS or key in pending_pax or "\0" in value:
                                raise ValueError("external: unsupported/duplicate/unsafe PAX key")
                            record_count += 1
                            if record_count > PAX_MAX_RECORDS:
                                raise ValueError("external: PAX record count cap exceeded")
                            if key in ("atime", "ctime", "mtime"):
                                if re.fullmatch(r"[+-]?[0-9]+(?:\.[0-9]+)?", value) is None:
                                    raise ValueError("external: invalid PAX time metadata")
                                _bounded_decimal(value)
                            elif key == "size":
                                if re.fullmatch(r"[0-9]{1,12}", value) is None or int(value) > MAX_EXPANDED:
                                    raise ValueError("external: invalid PAX size override")
                            elif key == "path" and (not value or len(value.encode("utf-8")) > 4096):
                                raise ValueError("external: invalid PAX path size")
                            pending_pax[key] = value
                            cursor += length
                        continue
                    if pending_pax:
                        if "path" in pending_pax:
                            member.name = pending_pax["path"]
                        if "size" in pending_pax:
                            member.size = int(pending_pax["size"])
                        pending_pax = {}
                    name = member.name.rstrip("/") if member.isdir() else member.name
                    if name.casefold() in seen or member.sparse is not None or not (member.isfile() or member.isdir()) or member.isdir() and member.size or not name or "\\" in name or any(part in ("", ".", "..") for part in name.split("/")):
                        raise ValueError("external: unsafe/duplicate archive member")
                    seen.add(name.casefold())
                    entries += 1
                    if entries > MAX_ENTRIES or expanded + ((member.size + 511) // 512) * 512 > MAX_EXPANDED:
                        raise ValueError("external: archive bounds exceeded")
                    locator = requested.get(name)
                    retain = locator is not None
                    if retain and (member.size != locator["size_bytes"] or collect and member.size > max_bytes):
                        raise ValueError("external: requested member size mismatch")
                    remaining, value, member_digest = member.size, bytearray(), hashlib.sha256()
                    while remaining:
                        if time.monotonic() >= deadline:
                            raise ValueError("external: evidence reader deadline exceeded")
                        chunk = stream.read(min(1048576, remaining))
                        if not chunk:
                            raise ValueError("external: truncated member")
                        remaining -= len(chunk)
                        expanded += len(chunk)
                        if retain:
                            member_digest.update(chunk)
                            if collect:
                                value.extend(chunk)
                    padding = (-member.size) % 512
                    if padding:
                        tail = stream.read(padding)
                        expanded += len(tail)
                        if len(tail) != padding:
                            raise ValueError("external: truncated padding")
                    if retain:
                        if member_digest.hexdigest() != locator["member_sha256"]:
                            raise ValueError("external: missing/changed member")
                        key = (archive_name, name, locator["member_sha256"])
                        results[key] = bytes(value) if collect else None
            after = os.fstat(compressed.fileno())
            final = path.lstat()
            for item, expected_identity in ((after, opened_identity), (final, identity)):
                if expected_identity != (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns):
                    raise ValueError("external: archive changed during member read")
        for name, locator in requested.items():
            if (archive_name, name, locator["member_sha256"]) not in results:
                raise ValueError("external: missing/changed member")
    return results


def verify_external_evidence(paths: ProjectPaths, reference: JSONObject, record: JSONObject, snapshot: JSONObject, *, deadline: float) -> None:
    """Verify all owned original members in grouped reads for a validated record.

    paths/reference identify the committed version; record/snapshot have been
    hash/schema checked by get. deadline bounds all hash/TAR work. Return None
    only for current verified bindings. No writes occur; invalid ownership,
    changed bindings, revocation or unavailable evidence raises ValueError/OSError.
    """
    validate_external_document(reference, "reference")
    if any(record[key] != reference[key] for key in ("source_id", "case_id", "adapter_id", "adapter_version", "kind", "schema_version")):
        raise ValueError("external: reference record identity mismatch")
    root = resolve_external_source(paths, reference["source_id"])
    fingerprints = snapshot["source_fingerprints"]
    if snapshot["source_id"] != reference["source_id"] or fingerprints["source_id"] != reference["source_id"]:
        raise ValueError("external: snapshot source mismatch")
    queue, locators = [record], []
    while queue:
        value = queue.pop()
        if isinstance(value, dict):
            if set(value) == {"source_id", "relative_path", "member_path", "member_sha256", "size_bytes", "json_pointer", "line_start", "line_end"}:
                if value["source_id"] != reference["source_id"]:
                    raise ValueError("external: locator ownership mismatch")
                locators.append(value)
            queue.extend(value.values())
        elif isinstance(value, list):
            queue.extend(value)
    _read_external_members(root, fingerprints, locators, max_bytes=0, collect=False, deadline=deadline)
    resolve_external_source(paths, reference["source_id"])
    directory = paths.root / "memory/knowledge/external"
    connection = sqlite3.connect((directory / "index.sqlite").as_uri() + "?mode=ro", uri=True, timeout=30)
    try:
        connection.enable_load_extension(False)
        connection.execute("PRAGMA query_only=ON")
        binding = connection.execute("SELECT snapshot_sha256 FROM bindings WHERE source_id=? AND case_id=? AND record_sha256=? ORDER BY rowid DESC LIMIT 1", (reference["source_id"], reference["case_id"], reference["record_sha256"])).fetchone()
        expected = hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
        if binding is None or binding[0] != expected:
            raise ValueError("external: snapshot binding changed during verification")
    finally:
        connection.close()
