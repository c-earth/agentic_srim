"""Accept SR installation/workspace paths and bounds; prepare files and run SR Module."""

import json
from pathlib import Path
import shutil

from agentic_srim.execution.processes import run as run_native
from agentic_srim.storage.artifacts import checksum, write_json


SUPPORT_FILES = ("SRModule.exe", "SCOEF03.dat", "SNUC03.dat", "VERSION")


def prepare(source: Path, workspace: Path) -> dict:
    """Copy SR support files into its workspace once; return their SHA-256 hashes.

    source is the original SR Module installation and is never modified.
    workspace is the worker's sr_module directory, created if absent. Rejects
    changed installation fingerprints or altered prepared files before execution.
    """
    workspace.mkdir(parents=True, exist_ok=True)
    fingerprint = {name: checksum(source / name) for name in SUPPORT_FILES}
    marker = workspace / "installation.json"
    if marker.exists():
        previous = json.loads(marker.read_text(encoding="utf-8"))
        if previous != fingerprint:
            raise ValueError("Installation changed; select a fresh runtime directory")
    else:
        for name in SUPPORT_FILES:
            shutil.copy2(source / name, workspace / name)
        write_json(marker, fingerprint)
    if any(checksum(workspace / name) != expected for name, expected in fingerprint.items()):
        raise ValueError("Worker executable/reference data checksum mismatch")
    return fingerprint


def run(workspace: Path, archive: Path, timeout_seconds: float, cancellation: Path) -> int:
    """Run prepared SRModule.exe in workspace; archive diagnostics and return its exit code.

    timeout_seconds bounds execution; cancellation is the cooperative stop-marker
    path. Shared process control terminates/reaps the native process on failure.
    """
    return run_native(workspace, archive, timeout_seconds, cancellation, executable="SRModule.exe")
