"""Provide safe bounded verified reads, exclusive publication and local server locks."""

from __future__ import annotations

import base64
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
import errno
import hashlib
import msvcrt
import os
from pathlib import Path
import re
import stat
from tempfile import NamedTemporaryFile, mkdtemp
import time

from src.mcp.common.contracts import ServerContext, canonical_bytes
from src.srim.common.types import JSONObject


_HELD: ContextVar[frozenset[str]] = ContextVar("mcp_held_locks", default=frozenset())


def safe_path(root: Path, relative: str) -> Path:
    """Return root/relative after safe-name and existing-ancestor validation.

    Reject traversal, Windows device/stream names, case collisions and links with
    ValueError/OSError. Root must be absolute; create no paths.
    """
    if not isinstance(root, Path) or not root.is_absolute() or ".." in root.parts or not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError("unsafe-path: expected safe owner-relative path")
    parts = relative.split("/")
    if any(part in ("", ".", "..") or os.path.isreserved(part) or part.endswith((".", " ")) or re.search(r'[<>:"|?*\x00-\x1f]', part) for part in parts):
        raise ValueError("unsafe-path: invalid relative path component")
    target = root.joinpath(*parts)
    for entry in (*reversed(target.parents), target):
        if entry.is_symlink() or entry.is_junction():
            raise OSError(errno.ELOOP, "unsafe-path: link or junction", str(entry))
    cursor = root
    for part in parts:
        if cursor.is_dir():
            matches = [item.name for item in cursor.iterdir() if item.name.casefold() == part.casefold()]
            if matches and matches != [part]:
                raise ValueError("unsafe-path: case-insensitive path collision")
        cursor /= part
    return target


def allowed_source(context: ServerContext, path: str, *, directory: bool) -> Path:
    """Validate path against context's read-only input roots and expected kind.

    Return an absolute safe existing source, or raise ValueError/OSError without
    writes. The configured native installation grants no arbitrary file access.
    """
    if not isinstance(path, str) or not Path(path).is_absolute() or ".." in Path(path).parts:
        raise ValueError("unsafe-path: source must be absolute")
    target = Path(path)
    roots = (context.paths.root, *(Path(item) for item in context.limits["allowed_input_roots"]))
    for root in roots:
        if target.is_relative_to(root):
            if target == root:
                safe_path(root.parent, root.name)
            else:
                safe_path(root, target.relative_to(root).as_posix())
            if directory and target.is_dir():
                return target
            if not directory and target.is_file() and stat.S_ISREG(target.stat().st_mode):
                return target
            raise FileNotFoundError(str(target))
    raise ValueError("unsafe-path: source outside approved roots")


def read_verified_chunk(path: Path, sha256: str, size_bytes: int, *, offset: int, length: int) -> JSONObject:
    """Stream-verify path against sha256/size_bytes and return the selected chunk.

    Offset/length are actual nonnegative/positive integers. Reject unsafe or
    changed source files with ValueError/OSError; bounded memory and no writes.
    """
    safe_path(path.parent, path.name)
    if not isinstance(sha256, str) or re.fullmatch(r"[0-9a-f]{64}", sha256) is None or type(size_bytes) is not int or size_bytes < 0 or type(offset) is not int or offset < 0 or type(length) is not int or length < 1:
        raise ValueError("Invalid verified chunk arguments")
    before_path = path.stat()
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise OSError(errno.EINVAL, "Source must be regular", str(path))
        digest = hashlib.sha256()
        total = 0
        selected = bytearray()
        while block := stream.read(262144):
            digest.update(block)
            left, right = max(0, offset - total), min(len(block), offset + length - total)
            if right > left:
                selected.extend(block[left:right])
            total += len(block)
        after = os.fstat(stream.fileno())
    final = path.stat()
    identities = [(item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns) for item in (before_path, before, after, final)]
    stable_times = before.st_ctime_ns == after.st_ctime_ns and before_path.st_ctime_ns == final.st_ctime_ns
    safe_path(path.parent, path.name)
    if len(set(identities)) != 1 or not stable_times or total != size_bytes or digest.hexdigest() != sha256:
        raise ValueError("integrity: file changed or hash/size mismatch")
    return {"encoding": "base64", "content": base64.b64encode(selected).decode("ascii"), "offset": offset, "size_bytes": total, "eof": offset + len(selected) >= total}


def publish_json(path: Path, document: JSONObject, *, replace: bool = False) -> JSONObject:
    """Exclusively publish document at path, replacing only explicit state files.

    Return path/hash/size evidence; validate safe ancestors and retain synchronized
    staging on ValueError/OSError. Never overwrite when replace is false.
    """
    payload = canonical_bytes(document)
    safe_path(path.parent, path.name)
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        safe_path(path.parent, path.name)
        with NamedTemporaryFile(dir=path.parent, prefix=".mcp-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        safe_path(path.parent, path.name)
        if replace:
            os.replace(temporary, path)
        else:
            temporary.rename(path)
        return {"path": str(path), "sha256": hashlib.sha256(payload).hexdigest(), "size_bytes": len(payload)}
    except BaseException as error:
        error.add_note(f"Publication retained temporary path: {temporary}")
        raise


def publish_files(destination: Path, files: Mapping[str, bytes]) -> JSONObject:
    """Publish files and a hash manifest in a new destination directory.

    Validate safe unique relative names, synchronize and reverify exact bytes,
    return manifest evidence, and preserve staging on errors without overwrite.
    """
    safe_path(destination.parent, destination.name)
    if destination.exists():
        raise FileExistsError(str(destination))
    if "manifest.json" in files or len({name.casefold() for name in files}) != len(files):
        raise ValueError("Invalid or colliding file inventory")
    for name, payload in files.items():
        safe_path(destination, name)
        if not isinstance(payload, bytes):
            raise ValueError("File payload must be bytes")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(mkdtemp(prefix=".mcp-files-", dir=destination.parent))
    try:
        inventory = []
        for name, payload in sorted(files.items()):
            target = safe_path(staging, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            digest = hashlib.sha256(payload).hexdigest()
            read_verified_chunk(target, digest, len(payload), offset=0, length=1)
            inventory.append({"path": name, "sha256": digest, "size_bytes": len(payload)})
        manifest = {"schema_version": 1, "files": inventory}
        evidence = publish_json(staging / "manifest.json", manifest)
        safe_path(destination.parent, destination.name)
        staging.rename(destination)
        return {"path": str(destination), "manifest_sha256": evidence["sha256"], "files": inventory}
    except BaseException as error:
        error.add_note(f"File publication retained staging: {staging}")
        raise


@contextmanager
def acquire_lock(path: Path, *, timeout_seconds: float = 30.0) -> Iterator[None]:
    """Yield None under path's byte lock until context exit, then release it.

    Validate finite nonnegative timeout_seconds and reject recursive/unsafe locks.
    Create only the persistent lock/parent; contention raises OSError.
    """
    if type(timeout_seconds) not in (int, float) or not 0 <= timeout_seconds < float("inf"):
        raise ValueError("Invalid lock timeout")
    safe_path(path.parent, path.name)
    identity = os.path.normcase(str(path))
    if identity in _HELD.get():
        raise OSError(errno.EDEADLK, "Recursive MCP lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_path(path.parent, path.name)
    with path.open("a+b") as stream:
        if stream.seek(0, os.SEEK_END) == 0:
            stream.write(b"\0")
            stream.flush()
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError as error:
                remaining = deadline - time.monotonic()
                if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK) or remaining <= 0:
                    raise
                time.sleep(min(remaining, 0.01))
        token = _HELD.set(_HELD.get() | {identity})
        try:
            yield None
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            _HELD.reset(token)
