"""Export independently verified TRIM inputs as self-contained immutable-byte bundles."""

from copy import deepcopy
import errno
import hashlib
import json
import os
from pathlib import Path
from tempfile import mkdtemp

from src.srim.metadata import _write_input_file
from src.srim.runtime import _read_regular_bytes
from src.srim.trim_resolution import ResolvedTRIMInputs, verify_trim_resolution


def write_trim_input_bundle(destination: Path, inputs: ResolvedTRIMInputs) -> Path:
    """Verify inputs and exclusively export a complete bundle to absolute destination.

    Require an existing safe parent and absent destination. Copy nested caller
    data, replay it offline, write synchronized files into unique sibling staging,
    verify all hashes, then rename without merging/overwriting. Return destination.
    No worker allocation, lifecycle transition, deletion or native execution.
    Raise ValueError for invalid bundle/path, FileExistsError for a collision and
    OSError for unsafe/I/O failures; identify retained staging artifacts in notes.
    """
    if not isinstance(destination, Path) or not destination.is_absolute() or destination.name in ("", ".", "..") or ".." in destination.parts:
        raise ValueError("Input bundle destination must be an absolute new directory")
    for entry in (*reversed(destination.parents), destination):
        if entry.is_symlink() or entry.is_junction():
            raise OSError(errno.ELOOP, "Input bundle path contains a link or junction", str(entry))
    if os.path.lexists(destination):
        raise FileExistsError(errno.EEXIST, "Input bundle destination already exists", str(destination))
    if not destination.parent.is_dir():
        raise FileNotFoundError(errno.ENOENT, "Input bundle parent must exist", str(destination.parent))
    pinned = deepcopy(inputs)
    verify_trim_resolution(pinned)
    files = {"input/" + name: payload for name, payload in pinned.snapshots}
    for name, document in (("request", pinned.request), ("selections", pinned.selections), ("effective-request", pinned.effective_request), ("resolved", pinned.resolved)):
        files[f"input/{name}.json"] = (json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    manifest = {"schema_version": 1, "engine": "trim", "files": [{"path": name, "sha256": hashlib.sha256(payload).hexdigest(), "size_bytes": len(payload)} for name, payload in sorted(files.items())]}
    staging = Path(mkdtemp(prefix=".trim-input-", dir=destination.parent))
    try:
        for name, payload in sorted(files.items()):
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            _write_input_file(target, payload)
        for item in manifest["files"]:
            payload = _read_regular_bytes(staging / item["path"])
            if (hashlib.sha256(payload).hexdigest(), len(payload)) != (item["sha256"], item["size_bytes"]):
                raise ValueError("Input bundle changed during export verification")
        _write_input_file(staging / "manifest.json", (json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8"))
        for entry in (*reversed(destination.parents), destination):
            if entry.is_symlink() or entry.is_junction():
                raise OSError(errno.ELOOP, "Input bundle destination became unsafe", str(entry))
        if os.path.lexists(destination):
            raise FileExistsError(errno.EEXIST, "Input bundle destination appeared during export", str(destination))
        staging.rename(destination)
        return destination
    except BaseException as error:
        error.add_note(f"TRIM input export retained staging artifacts: {staging}")
        raise
