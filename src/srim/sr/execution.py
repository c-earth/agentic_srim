"""Verify and execute isolated SRModule runtimes with durable results and lifecycle tracking."""

import errno
import hashlib
import math
import os
from pathlib import Path
import re
import subprocess
from src.backend.config import Config
from src.backend.paths import ProjectPaths
from src.backend.records import _lock_worker, _record_time
from src.backend.resources import ResourceReference, verify_resource
from src.srim.sr.inputs import render_sr_input
from src.srim.common.types import JSONObject
from src.srim.common.records import _collect_resource_references, _simulation_path, _validate_document, begin_execution_evidence, finish_simulation, read_simulation, record_execution_stopped, start_simulation
from src.srim.common.files import _inventory, _read_json, _write_input_file, _write_json
from src.srim.sr.outputs import parse_sr_output
from src.srim.sr.storage import preserve_sr_runtime
from src.srim.sr.runtime import _make_runtime_manifest, _read_selected_resource
from src.srim.common.files import _read_regular_bytes


def verify_sr_runtime(paths: ProjectPaths, simulation_id: str, runtime: Path) -> JSONObject:
    """Return the independent validated request for paths' created SR simulation.

    Require absolute runtime at worker/worker_N/sr/simulation_id, five regular
    link-free files, unchanged initialized record inputs, exact manifest, matching
    input/version/executable provenance, and verified selected coefficients.
    Raise ValueError for identity, state, schema, or integrity errors and propagate
    OSError for files and locks. Write no simulation data; readers may create
    persistent lock files. The caller must serialize execution separately.
    """
    record = _simulation_path(paths, simulation_id)
    if not isinstance(runtime, Path) or not runtime.is_absolute() or runtime.name != simulation_id or runtime.parent.name != "sr" or runtime.parent.parent.parent != paths.workers or re.fullmatch(r"worker_[1-9][0-9]*", runtime.parent.parent.name) is None:
        raise ValueError("Runtime does not match the allocated worker and simulation")
    metadata = read_simulation(paths, simulation_id)
    if metadata["status"] != "created" or metadata["engine"]["name"] != "sr":
        raise ValueError("Only a created SR simulation can execute")
    if _inventory(record / "input", record) != metadata["inputs"]:
        raise ValueError("Initialized simulation inputs were changed")
    request = _read_json(record / "input" / "request.json")
    resolved = _read_json(record / "input" / "resolved.json")
    _validate_document(resolved, "resolved")
    if resolved["schema_version"] == 1:
        if resolved["parameters"] != request["parameters"] or resolved["cab"] != request["parameters"]["correction"] or len(resolved["resources"]) != 2:
            raise ValueError("Resolved SR inputs differ from request or selections")
    else:
        from src.srim.sr.resolution import verify_sr_resolution
        selections = _read_json(record / "input" / "selections.json")
        snapshots = {item["path"][6:]: _read_regular_bytes(record / item["path"]) for item in metadata["inputs"] if item["path"].startswith(("input/resources/", "input/cab/"))}
        request = verify_sr_resolution(paths, request, selections, resolved, snapshots)
        if request != _read_json(record / "input" / "effective-request.json") or metadata["defaults_generation"] != resolved["defaults"]:
            raise ValueError("Saved effective request/defaults differ from resolution")
        from dataclasses import asdict
        from src.srim.cab.cache import CABEntryReference, load_cab_entry
        found: dict[str, JSONObject] = {}
        import json
        pending: list[object] = [resolved["cab"]]
        while pending:
            item = pending.pop()
            if isinstance(item, dict):
                if set(item) == {"dataset", "dependency_key", "entry_id", "sha256", "size_bytes"}:
                    reference = CABEntryReference(ResourceReference(**item["dataset"]), item["dependency_key"], item["entry_id"], item["sha256"], item["size_bytes"])
                    load_cab_entry(paths, reference)
                    found[json.dumps(item, sort_keys=True)] = asdict(reference)
                else:
                    pending.extend(item.values())
            elif isinstance(item, list):
                pending.extend(item)
        if metadata["entry_references"] != [found[key] for key in sorted(found)]:
            raise ValueError("Metadata entry references differ from resolution")
    expected_input = render_sr_input(request)
    for entry in (*reversed(runtime.parents), runtime):
        if entry.is_symlink() or entry.is_junction():
            raise OSError(errno.ELOOP, "Runtime path contains a link or junction", str(entry))
    names = {"SRModule.exe", "SR.IN", "VERSION", "SCOEF03.dat", "SNUC03.dat"}
    if {entry.name for entry in runtime.iterdir()} != names:
        raise ValueError("Prepared runtime must contain exactly five files without stale output")
    files = {name: _read_regular_bytes(runtime / name) for name in names}
    manifest = _read_json(record / "input" / "engine" / "runtime.json")
    if set(manifest) != {"schema_version", "files"} or type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1 or not isinstance(manifest["files"], list):
        raise ValueError("Invalid runtime manifest schema")
    for item in manifest["files"]:
        if not isinstance(item, dict) or set(item) != {"filename", "size_bytes", "sha256"} or not isinstance(item["filename"], str) or type(item["size_bytes"]) is not int or item["size_bytes"] < 0 or not isinstance(item["sha256"], str) or re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) is None:
            raise ValueError("Invalid runtime manifest entry")
    if _make_runtime_manifest(files) != manifest:
        raise ValueError("Runtime bytes differ from saved manifest")
    if files["SR.IN"] != expected_input or files["SR.IN"] != _read_regular_bytes(record / "input" / "engine" / "SR.IN") or files["VERSION"] != _read_regular_bytes(record / "input" / "engine" / "VERSION"):
        raise ValueError("Runtime input or VERSION differs from preserved input")
    try:
        version_lines = files["VERSION"].decode("ascii").splitlines()
    except UnicodeError as error:
        raise ValueError("Runtime VERSION must be ASCII") from error
    if not version_lines or not version_lines[0].strip():
        raise ValueError("Runtime VERSION requires a nonblank first line")
    version = version_lines[0].strip()
    executable_hash = hashlib.sha256(files["SRModule.exe"]).hexdigest()
    if metadata["engine"] != {"name": "sr", "version": version, "executable": {"name": "SRModule.exe", "sha256": executable_hash}}:
        raise ValueError("Runtime executable or version differs from engine identity")
    expected_sources = [
        {"id": "sr-module-executable", "name": "SRModule.exe", "version": version, "sha256": executable_hash, "citation": None},
        {"id": "sr-module-version", "name": "VERSION", "version": version, "sha256": hashlib.sha256(files["VERSION"]).hexdigest(), "citation": None},
    ]
    if resolved["sources"] != expected_sources:
        raise ValueError("Runtime source provenance differs from prepared sources")
    if resolved["schema_version"] == 1:
        for selection, name, filename in zip(resolved["resources"], ("electronic", "nuclear"), ("SCOEF03.dat", "SNUC03.dat"), strict=True):
            if _read_selected_resource(paths, selection, name, filename) != files[filename]:
                raise ValueError("Runtime coefficients differ from selected resources")
    else:
        for role, filename in (("electronic", "SCOEF03.dat"), ("nuclear", "SNUC03.dat")):
            evidence = resolved["resources"][role]["effective"]
            if _read_regular_bytes(record / evidence["path"]) != files[filename]:
                raise ValueError("Runtime coefficients differ from effective snapshots")
    references = _collect_resource_references(resolved)
    if references != metadata["resources"]:
        raise ValueError("Metadata resource references differ from selections")
    for reference in metadata["resources"]:
        verify_resource(paths, ResourceReference(**reference))
    return request


def _preserve_sr_output(runtime: Path, record: Path) -> bytes | None:
    """Copy runtime's stopping.txt exclusively into record's raw tree and return bytes.

    Return None if no output exists. Require regular link-free source and safe
    existing destination ancestors; preserve exact bytes with flush/fsync. Raise
    OSError for links, collisions, access, or writes, retaining original/partial
    files and naming their locations in exception notes. Never overwrite data.
    """
    source = runtime / "stopping.txt"
    try:
        if not os.path.lexists(source):
            return None
        contents = _read_regular_bytes(source)
        _write_input_file(record / "output" / "raw" / "stopping.txt", contents)
        return contents
    except OSError as error:
        error.add_note(f"SR output retained at runtime {runtime} and record {record}")
        raise


def run_sr_simulation(
    paths: ProjectPaths,
    config: Config,
    simulation_id: str,
    runtime: Path,
    *,
    timeout_seconds: float = 60.0,
) -> JSONObject:
    """Execute paths' prepared simulation_id at runtime using config lifecycle time.

    timeout_seconds defaults to 60 and must be a finite positive int/float,
    excluding bool. Require matching created SR identity and empty output trees.
    config.hide_sr_window must be boolean and selects hidden startup or the
    executable's normal display behavior. The independent TRIM setting is unused.
    Hold the worker-specific execution lease through verification, shell-free execution,
    exact binary diagnostics/output preservation, parsing, and terminal metadata.
    subprocess.run kills and waits for the child on timeout or interruption.

    Return terminal metadata, including failed metadata for expected preflight,
    launch, timeout, nonzero-exit, integrity, missing-output, or parsing failures.
    Invalid caller/state/collision checks and worker contention raise before
    ownership. Propagate unexpected errors after independent best-effort evidence
    preservation and failed finalization, retaining secondary failures in notes.
    Interruptions are re-raised after the same finalization attempt. Normal
    completion preserves the exact runtime before terminal publication; failure
    publication is attempted even if preservation fails. Never retry or delete
    a worker here.
    """
    if type(config.hide_sr_window) is not bool:
        raise ValueError("config.hide_sr_window must be a boolean")
    if type(timeout_seconds) not in (int, float) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be a finite positive number")
    try:
        timeout = float(timeout_seconds)
    except OverflowError as error:
        raise ValueError("timeout_seconds must be finite") from error
    if not math.isfinite(timeout):
        raise ValueError("timeout_seconds must be finite")
    _record_time(config, None)
    record = _simulation_path(paths, simulation_id)
    if not isinstance(runtime, Path) or not runtime.is_absolute() or runtime.name != simulation_id or runtime.parent.name != "sr" or runtime.parent.parent.parent != paths.workers or re.fullmatch(r"worker_[1-9][0-9]*", runtime.parent.parent.name) is None:
        raise ValueError("Runtime does not match the allocated worker and simulation")
    with _lock_worker(paths, runtime.parent.parent):
        metadata = read_simulation(paths, simulation_id)
        if metadata["status"] != "created" or metadata["engine"]["name"] != "sr":
            raise ValueError("Only a created SR simulation can execute")
        if _inventory(record / "output", record) or {entry.name for entry in (record / "output").iterdir()} != {"raw", "parsed"}:
            raise FileExistsError("SR execution requires empty raw and parsed output directories")
        for directory in (record / "output" / "raw", record / "output" / "parsed"):
            if not directory.is_dir() or any(directory.iterdir()):
                raise FileExistsError("SR execution requires empty raw and parsed output directories")
        diagnostic: JSONObject = {
            "schema_version": 1, "simulation_id": simulation_id,
            "runtime_path": runtime.relative_to(paths.root).as_posix(),
            "timeout_seconds": timeout, "stage": "preflight", "exit_code": None,
            "timed_out": False, "message": None,
        }
        execution_path = record / "output" / "raw" / "execution.json"
        output_path = record / "output" / "raw" / "stopping.txt"
        preservation_attempted = False
        try:
            try:
                request = verify_sr_runtime(paths, simulation_id, runtime)
            except ValueError as error:
                diagnostic["message"] = str(error)
            if diagnostic["message"] is None:
                saved = _read_json(record / "input" / "engine" / "runtime.json")
                execution = begin_execution_evidence(paths, config, simulation_id, runtime.parent.parent.name)
                started = start_simulation(paths, config, simulation_id)
                native_stopped = False
                diagnostic["stage"] = "launch"
                with (record / "output" / "raw" / "stdout.bin").open("xb") as stdout, (record / "output" / "raw" / "stderr.bin").open("xb") as stderr:
                    try:
                        startupinfo: subprocess.STARTUPINFO | None = None
                        if config.hide_sr_window:
                            startupinfo = subprocess.STARTUPINFO()
                            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                            startupinfo.wShowWindow = subprocess.SW_HIDE
                        completed = subprocess.run([str(runtime / "SRModule.exe")], cwd=runtime, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, timeout=timeout, check=False, creationflags=subprocess.CREATE_NO_WINDOW, startupinfo=startupinfo)
                        native_stopped = True
                        diagnostic["exit_code"] = completed.returncode
                        diagnostic["stage"] = "exit"
                        if completed.returncode != 0:
                            diagnostic["message"] = f"SRModule exited with code {completed.returncode}"
                    except subprocess.TimeoutExpired:
                        native_stopped = True
                        diagnostic["stage"] = "timeout"
                        diagnostic["timed_out"] = True
                        diagnostic["message"] = f"SRModule exceeded {timeout} seconds"
                    except OSError as error:
                        diagnostic["message"] = f"SRModule launch failed: {error}"
                    finally:
                        stdout.flush()
                        os.fsync(stdout.fileno())
                        stderr.flush()
                        os.fsync(stderr.fileno())
                if native_stopped:
                    record_execution_stopped(paths, config, simulation_id, execution["execution_id"], reason="timeout" if diagnostic["timed_out"] else "exit", exit_code=diagnostic["exit_code"])
                previous_stage = diagnostic["stage"]
                diagnostic["stage"] = "output"
                preservation_attempted = True
                contents = _preserve_sr_output(runtime, record)
                diagnostic["stage"] = previous_stage
                previous_stage = diagnostic["stage"]
                previous_message = diagnostic["message"]
                diagnostic["stage"] = "integrity"
                try:
                    actual = _make_runtime_manifest({item["filename"]: _read_regular_bytes(runtime / item["filename"]) for item in saved["files"]})
                    if actual != saved:
                        raise ValueError("Runtime files changed during execution")
                    if _inventory(record / "input", record) != started["inputs"]:
                        raise ValueError("Sealed simulation inputs were changed")
                except ValueError as error:
                    diagnostic["message"] = str(error) if previous_message is None else f"{previous_message}; integrity: {error}"
                if previous_message is not None:
                    diagnostic["stage"] = previous_stage
                if diagnostic["message"] is None and contents is None:
                    diagnostic["stage"] = "output"
                    diagnostic["message"] = "SRModule produced no stopping.txt"
                if diagnostic["message"] is None:
                    diagnostic["stage"] = "parse"
                    try:
                        parsed = parse_sr_output(contents, request)
                    except ValueError as error:
                        diagnostic["message"] = str(error)
                    else:
                        diagnostic["stage"] = "output"
                        parsed_path = record / "output" / "parsed" / "result.json"
                        if os.path.lexists(parsed_path):
                            raise FileExistsError(str(parsed_path))
                        _write_json(parsed_path, parsed)
                        diagnostic["stage"] = "completed"
            if os.path.lexists(execution_path):
                raise FileExistsError(str(execution_path))
            _write_json(execution_path, diagnostic)
            failure = None if diagnostic["message"] is None else {"message": f'{diagnostic["stage"]}: {diagnostic["message"]}', "exit_code": diagnostic["exit_code"]}
            preserve_sr_runtime(paths, simulation_id, runtime)
            return finish_simulation(paths, config, simulation_id, failure)
        except BaseException as error:
            if isinstance(error, OSError) and diagnostic["stage"] == "preflight" and error.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK) and error.filename is None:
                raise
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                diagnostic["stage"] = "interrupted"
            diagnostic["message"] = str(error) or type(error).__name__
            try:
                if not preservation_attempted and not os.path.lexists(output_path):
                    _preserve_sr_output(runtime, record)
            except BaseException as preservation_error:
                error.add_note(f"Output preservation also failed: {preservation_error}")
            try:
                if not os.path.lexists(execution_path):
                    _write_json(execution_path, diagnostic)
            except BaseException as diagnostic_error:
                error.add_note(f"Execution diagnostics also failed: {diagnostic_error}")
            try:
                preserve_sr_runtime(paths, simulation_id, runtime)
            except BaseException as preservation_error:
                error.add_note(f"Runtime preservation also failed: {preservation_error}")
            try:
                current = read_simulation(paths, simulation_id)
                if current["status"] in ("created", "running"):
                    finish_simulation(paths, config, simulation_id, {"message": f'{diagnostic["stage"]}: {diagnostic["message"]}', "exit_code": diagnostic["exit_code"]})
            except BaseException as finalization_error:
                error.add_note(f"Failed finalization also failed: {finalization_error}")
            error.add_note(f"SR execution retained runtime {runtime} and record {record}")
            raise
