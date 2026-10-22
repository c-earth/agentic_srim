"""Adapt explicit scientific operations to existing SRIM APIs without changing physics."""

from __future__ import annotations

import base64
from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import re
import sys
from uuid import UUID, uuid4

from src.backend.paths import create_base_directories
from src.backend.records import CreatedDirectory, CreateRequest, create_directories
from src.backend.resources import ResourceReference, resolve_resource, verify_resource
from src.knowledge.index import initialize_index, rebuild_index, update_index
from src.knowledge.search import search_inputs
from src.knowledge.source import project_simulation_inputs, read_source_record
from src.mcp.common.contracts import ServerContext, canonical_bytes, load_json
from src.mcp.common.files import allowed_source, publish_files, read_verified_chunk, safe_path
from src.srim.cab.strengths import create_cab_variant
from src.srim.cab.cache import CABEntryReference, load_cab_entry
from src.srim.cab.recovery import populate_default_cab
from src.srim.resources.compatibility import read_module_version
from src.srim.resources.defaults import read_sr_defaults, switch_sr_defaults
from src.srim.resources.electronic import create_electronic_variant
from src.srim.sr.execution import run_sr_simulation, verify_sr_runtime
from src.srim.sr.inputs import render_sr_input, validate_sr_engine_request, validate_sr_request
from src.srim.resources.materials import create_material_variant, parse_atomdata, parse_compounds, search_compounds
from src.srim.common.types import JSONObject
from src.srim.common.files import _inventory, _read_json
from src.srim.common.records import read_simulation
from src.srim.resources.nuclear import create_nuclear_variant
from src.srim.sr.queue import PreparedSRTask, run_sr_batch
from src.srim.resources.resolution import _resolve_selection
from src.srim.sr.resolution import resolve_sr_inputs
from src.srim.common.files import _read_regular_bytes
from src.srim.sr.runtime import prepare_sr_simulation
from src.srim.sr.storage import cleanup_sr_batch
from src.srim.trim.defaults import create_trim_damage_variant, parse_trim_damage, read_trim_defaults, read_trim_installation, switch_trim_defaults
from src.srim.trim.dialogs import ENGINES
from src.srim.trim.execution import read_trim_output_availability, run_trim_simulation
from src.srim.trim.inputs import render_trim_events, render_trim_input, validate_trim_engine_request, validate_trim_request
from src.srim.trim.queue import PreparedTRIMTask, run_trim_batch
from src.srim.trim.recovery import export_trim_checkpoint_recovery
from src.srim.trim.resolution import resolve_trim_inputs
from src.srim.trim.runtime import load_trim_input_bundle, prepare_trim_continuation, prepare_trim_simulation, verify_trim_runtime
from src.srim.trim.storage import cleanup_trim_batch, parse_trim_directory, write_trim_input_bundle


def read_operation(name: str, arguments: JSONObject, context: ServerContext) -> JSONObject:
    """Return validated read/validation data for a fixed scientific name/arguments.

    Context supplies configuration and I/O limits. Do not run dependencies or
    mutate scientific records. Invalid state/paths/schema or stale evidence raises
    ValueError/OSError; offline reads do not require a working installation.
    """
    paths, config = context.paths, context.config
    args = deepcopy(arguments)
    pending = [args]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            if value.get("kind") == "file" and set(value) == {"kind", "path"}:
                allowed_source(context, value["path"], directory=False)
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    if name == "environment_get":
        installation, defaults = {}, {}
        try:
            version_path, version_bytes = read_module_version(config.srim_root)
            executable = _read_regular_bytes(config.srim_root / "SR Module" / "SRModule.exe")
            installation["sr"] = {"available": True, "version": version_bytes.decode("ascii").splitlines()[0].strip(), "files": [{"path": "SR Module/SRModule.exe", "sha256": hashlib.sha256(executable).hexdigest(), "size_bytes": len(executable)}, {"path": version_path.relative_to(config.srim_root).as_posix(), "sha256": hashlib.sha256(version_bytes).hexdigest(), "size_bytes": len(version_bytes)}]}
        except (OSError, ValueError) as error:
            installation["sr"] = {"available": False, "reason": str(error)}
        try:
            profile, files = read_trim_installation(config)
            digest = hashlib.sha256(files["TRIM.exe"]).hexdigest()
            installation["trim"] = {"available": True, "profile": profile, "files": [{"path": key, "sha256": hashlib.sha256(payload).hexdigest(), "size_bytes": len(payload)} for key, payload in sorted(files.items())], "reviewed_dialog_policy": digest == ENGINES.get(profile["engine_family"]), "executable_sha256": digest}
        except (OSError, ValueError) as error:
            installation["trim"] = {"available": False, "reason": str(error)}
        for kind in ("sr", "trim"):
            try:
                defaults[kind] = {"ready": True, "generation": read_sr_defaults(paths) if kind == "sr" else read_trim_defaults(paths)}
            except (OSError, ValueError) as error:
                defaults[kind] = {"ready": False, "reason": str(error)}
        return {"python": sys.version, "sdk": version("mcp"), "installation": installation, "defaults": defaults, "limits": context.limits, "cancellation": {"individual_trim": "cooperative", "sr": "timeout-only", "batch": "queued-only", "resolution": "queued-only"}}
    if name == "defaults_get":
        return read_sr_defaults(paths) if args["kind"] == "sr" else read_trim_defaults(paths)
    if name == "request_validate":
        validator = (validate_sr_engine_request if args["explicit"] else validate_sr_request) if args["engine"] == "sr" else (validate_trim_engine_request if args["explicit"] else validate_trim_request)
        validator(args["request"])
        return {"valid": True, "checked": "schema", "not_checked": ["resource resolution", "native feasibility", "independent physical validity"]}
    if name == "input_render":
        files = {"SR.IN": render_sr_input(args["request"])} if args["engine"] == "sr" else {"TRIM.IN": render_trim_input(args["request"])}
        if args["engine"] == "trim":
            events = render_trim_events(args["request"])
            if events is not None:
                files["TRIM.DAT"] = events
        return {"files": [{"name": key, "encoding": "base64", "native_encoding": "ascii" if key != "TRIM.IN" else "cp1252", "content": base64.b64encode(payload).decode("ascii"), "sha256": hashlib.sha256(payload).hexdigest(), "size_bytes": len(payload)} for key, payload in files.items()]}
    if name == "inputs_search":
        return search_inputs(paths, **args)
    if name == "simulations_list":
        matches, issues = [], []
        for target in sorted(paths.simulation_memory.iterdir() if paths.simulation_memory.exists() else []):
            if target.name.startswith("."):
                continue
            try:
                metadata = read_simulation(paths, target.name)
                if (args["engine"] is None or metadata["engine"]["name"] == args["engine"]) and (args["status"] is None or metadata["status"] == args["status"]):
                    matches.append({"id": target.name, "engine": metadata["engine"], "status": metadata["status"], "created_at": metadata["created_at"]})
            except (OSError, ValueError) as error:
                issues.append({"id": target.name, "message": str(error)})
        return {"total": len(matches), "offset": args["offset"], "limit": args["limit"], "matches": matches[args["offset"]:args["offset"] + args["limit"]], "issues": issues}
    if name == "simulation_get":
        return {"metadata": read_simulation(paths, args["simulation_id"]), "verification": "structural metadata only; use source_get and verified file reads"}
    if name == "source_get":
        return project_simulation_inputs(paths, args["simulation_id"])
    if name == "source_read":
        if args["length"] > context.limits["file_chunk_bytes"]:
            raise ValueError("too-large: requested chunk exceeds configured cap")
        record = read_source_record(paths, args["reference"])
        if args["file"] not in record["files"] or args["file"]["owner"] != args["reference"]:
            raise ValueError("integrity: file is not listed for pinned source")
        target = safe_path(paths.simulation_memory / record["reference"]["id"], args["file"]["path"])
        result = read_verified_chunk(target, args["file"]["sha256"], args["file"]["size_bytes"], offset=args["offset"], length=args["length"])
        read_source_record(paths, args["reference"])
        return result
    if name in ("resources_list", "resource_read"):
        if name == "resources_list":
            matches, issues = [], []
            for entry in sorted(paths.resources.glob("*/*/v*/manifest.json") if paths.resources.exists() else []):
                category, resource_name, resource_version = entry.parent.parent.parent.name, entry.parent.parent.name, entry.parent.name
                if args["category"] is not None and category != args["category"] or args["name"] is not None and resource_name != args["name"]:
                    continue
                try:
                    reference = resolve_resource(paths, category, resource_name, resource_version)
                    verify_resource(paths, reference)
                    matches.append(asdict(reference))
                except (ValueError, OSError) as error:
                    issues.append({"path": entry.relative_to(paths.root).as_posix(), "message": str(error)})
            return {"total": len(matches), "offset": args["offset"], "limit": args["limit"], "matches": matches[args["offset"]:args["offset"] + args["limit"]], "issues": issues}
        reference = ResourceReference(**args["reference"])
        source = verify_resource(paths, reference)
        if args["section"] == "manifest":
            return {"reference": asdict(reference), "manifest": _read_json(source.parent / "manifest.json")}
        if args["section"] == "entries":
            entries = []
            for entry in sorted(source.parent.glob("entries/*/*.json")):
                payload = _read_regular_bytes(entry)
                cab_reference = CABEntryReference(reference, entry.parent.name, entry.stem, hashlib.sha256(payload).hexdigest(), len(payload))
                entries.append({"reference": asdict(cab_reference), "entry": load_cab_entry(paths, cab_reference)})
            return {"reference": asdict(reference), "total": len(entries), "offset": args["offset"], "limit": args["limit"], "entries": entries[args["offset"]:args["offset"] + args["limit"]]}
        if reference.category == "material" and reference.name in ("atomdata", "compound", "trim-damage") or reference.category == "cab":
            if reference.size_bytes > context.limits["max_result_bytes"]:
                raise ValueError("too-large: structured resource exceeds parsing cap")
            payload = _read_regular_bytes(source)
            if len(payload) != reference.size_bytes or hashlib.sha256(payload).hexdigest() != reference.sha256:
                raise ValueError("integrity: resource changed during read")
            document = parse_atomdata(payload) if reference.name == "atomdata" else parse_compounds(payload) if reference.name == "compound" else parse_trim_damage(payload) if reference.name == "trim-damage" else load_json(payload)
            rows = document.get("rows", document.get("records", document.get("entries")))
            if isinstance(rows, list):
                return {"reference": asdict(reference), "total": len(rows), "offset": args["offset"], "limit": args["limit"], "rows": rows[args["offset"]:args["offset"] + args["limit"]], "metadata": {key: value for key, value in document.items() if key not in ("rows", "records", "entries", "source")}}
        chunk = read_verified_chunk(source, reference.sha256, reference.size_bytes, offset=args["offset"], length=min(context.limits["file_chunk_bytes"], args["limit"], reference.size_bytes or 1))
        return {"reference": asdict(reference), "payload": chunk, "paging_unit": "bytes; use offset to continue"}
    if name == "compounds_search":
        if args["compound_selection"]["create_version"] or args["atomdata_selection"]["create_version"]:
            raise ValueError("Read-only compound search cannot publish versions")
        defaults = read_sr_defaults(paths) if any(selection.get("version") == "default" for selection in (args["compound_selection"], args["atomdata_selection"])) else {"resources": {}}
        compounds_role, compounds_files = _resolve_selection(paths, config, "compound", args["compound_selection"], defaults)
        atom_role, atom_files = _resolve_selection(paths, config, "atomdata", args["atomdata_selection"], defaults)
        compound_bytes = compounds_files[compounds_role["effective"]["path"].removeprefix("input/")]
        atom_bytes = atom_files[atom_role["effective"]["path"].removeprefix("input/")]
        matches = search_compounds(parse_compounds(compound_bytes), atomdata=parse_atomdata(atom_bytes), **args["filters"])
        return {"source_sha256": hashlib.sha256(compound_bytes).hexdigest(), "atomdata_sha256": hashlib.sha256(atom_bytes).hexdigest(), "total": len(matches), "offset": args["offset"], "limit": args["limit"], "matches": matches[args["offset"]:args["offset"] + args["limit"]]}
    if name in ("result_read", "table_read", "trim_availability_get"):
        simulation_id = args["simulation_id"]
        source = project_simulation_inputs(paths, simulation_id)
        metadata = read_simulation(paths, simulation_id)
        if name == "trim_availability_get":
            return read_trim_output_availability(paths, simulation_id)
        by_path = {file["path"]: file for file in source["files"]}
        result_path = "output/parsed/result.json" if metadata["engine"]["name"] == "sr" else "output/parsed/results.json"
        if name == "result_read" and args["section"] in ("saved-values", "config-integrity", "availability"):
            result_path = "output/parsed/" + args["section"] + ".json"
        if result_path not in by_path:
            raise ValueError("unavailable: requested saved result is absent")
        file = by_path[result_path]
        if file["size_bytes"] > context.limits["max_result_bytes"]:
            raise ValueError("too-large: result JSON exceeds parsing cap; use source_get/source_read")
        payload = read_verified_chunk(safe_path(paths.simulation_memory / simulation_id, result_path), file["sha256"], file["size_bytes"], offset=0, length=max(1, file["size_bytes"]))
        document = load_json(base64.b64decode(payload["content"]))
        scientific = document.get("recovery") if document.get("result_source") == "checkpoint-recovery" else document
        if not isinstance(scientific, dict):
            raise ValueError("unavailable: scientific result recovery failed")
        provenance = {"source": source["reference"], "file": file, "native_status": metadata["status"], "result_source": document.get("result_source", "native-text"), "warnings": scientific.get("warnings", []), "provenance": scientific.get("provenance"), "complete": document.get("complete", True if metadata["engine"]["name"] == "sr" else None)}
        if name == "result_read":
            section = args["section"]
            if section == "manifest":
                data = {"tables": ["sr.rows"] if metadata["engine"]["name"] == "sr" else list(scientific.get("profiles", {})), "sections": list(scientific), "file": file}
            elif section in ("saved-values", "config-integrity", "availability"):
                data = document
            elif section == "summary" and metadata["engine"]["name"] == "sr":
                data = {key: value for key, value in document.items() if key != "rows"}
                data["row_count"] = len(document["rows"])
            elif section in scientific:
                data = scientific[section]
            else:
                raise ValueError("unavailable: saved section absent")
            read_source_record(paths, source["reference"])
            return {"data": data, **provenance}
        table = {"rows": document["rows"], "columns": [], "units": document["stopping_units"]} if args["table"] == "sr.rows" and metadata["engine"]["name"] == "sr" else scientific.get("profiles", {}).get(args["table"])
        if not isinstance(table, dict):
            raise ValueError("not-found: unknown saved table")
        rows = table.get("rows", [])
        result = {key: value for key, value in table.items() if key != "rows"}
        if not rows and not table.get("columns") and table.get("calculated_ions") is None and any("not calculated" in note.casefold() for note in table.get("notes", []) if isinstance(note, str)):
            result.update(availability="not_calculated", reason="; ".join(table["notes"]))
        result.update(total=len(rows), offset=args["offset"], limit=args["limit"], rows=rows[args["offset"]:args["offset"] + args["limit"]])
        read_source_record(paths, source["reference"])
        return {"table": result, **provenance}
    raise ValueError("unsupported: unknown scientific read operation")


def write_operation(name: str, arguments: JSONObject, context: ServerContext, cancel: Callable[[], bool], progress: Callable[[JSONObject], None]) -> JSONObject:
    """Execute a fixed accepted scientific mutation with cancellation/progress callbacks.

    Validate all source roots before allocation; call existing verified APIs and
    return outcome/data/references/warnings. Main/native failures remain metadata;
    operational validation/I/O raises with preserved partial evidence and no retry.
    """
    paths, config, args = context.paths, context.config, deepcopy(arguments)
    pending = [args]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            if value.get("kind") in ("file", "local-file") and "path" in value:
                allowed_source(context, value["path"], directory=False)
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    if cancel():
        return {"outcome": "cancelled", "data": None, "references": [], "warnings": []}
    references, warnings, outcome, data = [], [], "succeeded", None
    if name == "defaults_switch":
        if args["kind"] == "sr" and args["damage_version"] != "builtin":
            raise ValueError("SR defaults do not accept damage_version")
        create_base_directories(paths)
        data = switch_sr_defaults(paths, config) if args["kind"] == "sr" else switch_trim_defaults(paths, config, damage_version=args["damage_version"])
    elif name == "variant_create":
        spec = args["spec"]
        kind = spec["kind"]
        if kind in ("electronic", "nuclear") and ("overrides" in spec or "base_version" in spec and "replacements" not in spec) or kind not in ("electronic", "nuclear") and "replacements" in spec or "source" in spec and "replacements" in spec:
            raise ValueError("Invalid kind-specific variant source/edit mode")
        kwargs = {"base_version": spec["base_version"]} if "base_version" in spec else {}
        if "source" in spec:
            source = spec["source"]
            if source["kind"] == "local-file":
                kwargs["source"] = allowed_source(context, source["path"], directory=False)
            elif source["kind"] == "json":
                if kind in ("electronic", "nuclear", "atomdata"):
                    raise ValueError("JSON source invalid for this resource kind")
                kwargs["source"] = source["value"] if kind in ("compound", "damage") else canonical_bytes(source["value"])
            else:
                kwargs["source"] = source["content"] if source["encoding"] == "utf8" else base64.b64decode(source["content"], validate=True)
        if kind in ("electronic", "nuclear"):
            if "replacements" in spec:
                replacements = {}
                for row in spec["replacements"]:
                    key = tuple(row["key"]) if isinstance(row["key"], list) else row["key"]
                    if key in replacements:
                        raise ValueError("Duplicate coefficient row key")
                    replacements[key] = row["row"]
                kwargs["replacements"] = replacements
            reference = (create_electronic_variant if kind == "electronic" else create_nuclear_variant)(paths, config, **kwargs)
        else:
            kwargs["overrides"] = spec.get("overrides", [])
            if kind in ("atomdata", "compound"):
                reference = create_material_variant(paths, config, kind, **kwargs)
            elif kind in ("core", "bond"):
                reference = create_cab_variant(paths, config, kind, **kwargs)
            else:
                reference = create_trim_damage_variant(paths, config, **kwargs)
        data = asdict(reference)
    elif name in ("sr_resolve", "sr_prepare", "trim_resolve", "trim_prepare", "trim_continue_prepare", "cab_populate"):
        if name in ("sr_resolve", "sr_prepare", "trim_resolve"):
            (validate_trim_request if name == "trim_resolve" else validate_sr_request)(args["request"])
        create_base_directories(paths)
        worker_id = args.get("worker_id")
        if worker_id is None:
            worker = create_directories(paths, config, [CreateRequest("worker")]).completed[0]
        else:
            target = safe_path(paths.workers, worker_id)
            if not target.is_dir():
                raise FileNotFoundError(str(target))
            worker = CreatedDirectory("worker", target, None, int(worker_id[7:]))
        progress({"source": "adapter", "phase": "allocating", "worker_ids": [worker.path.name], "simulation_ids": [], "data": {}})
        if name == "cab_populate":
            entries = [tuple(entry) if isinstance(entry, list) else entry for entry in args["entries"]]
            progress({"source": "adapter", "phase": "resolving", "worker_ids": [], "simulation_ids": [], "data": {}})
            data = {"entries": [asdict(reference) for reference in populate_default_cab(paths, config, worker, args["kind"], entries=entries, capture_timeout_seconds=args["capture_timeout_seconds"], dependency_timeout_seconds=args["dependency_timeout_seconds"], progress=progress)]}
        elif name in ("sr_resolve", "trim_resolve"):
            progress({"source": "adapter", "phase": "resolving", "worker_ids": [], "simulation_ids": [], "data": {}})
            resolved = (resolve_sr_inputs if name == "sr_resolve" else resolve_trim_inputs)(paths, config, worker, args["request"], args["selections"], capture_timeout_seconds=args["capture_timeout_seconds"], dependency_timeout_seconds=args["dependency_timeout_seconds"], progress=progress)
            identifier = "object_" + str(uuid4())
            destination = safe_path(paths.root / "memory" / "mcp" / context.name / "objects", identifier)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if name == "trim_resolve":
                write_trim_input_bundle(destination, resolved)
                payload = _read_regular_bytes(destination / "manifest.json")
                data = {"id": identifier, "manifest_sha256": hashlib.sha256(payload).hexdigest()}
            else:
                object_document = {"id": identifier, "engine": "sr", "request": resolved.request, "selections": resolved.selections, "effective_request": resolved.effective_request, "resolved": resolved.resolved, "snapshots": [{"path": key, "sha256": hashlib.sha256(value).hexdigest(), "size_bytes": len(value)} for key, value in resolved.snapshots], "engine_files": [{"path": key, "sha256": hashlib.sha256(value).hexdigest(), "size_bytes": len(value)} for key, value in resolved.engine_files]}
                files = {"object.json": canonical_bytes(object_document), **{"snapshots/" + key: value for key, value in resolved.snapshots}, **{"engine/" + key: value for key, value in resolved.engine_files}}
                published = publish_files(destination, files)
                data = {"id": identifier, "engine": "sr", "manifest_sha256": published["manifest_sha256"], "effective_request": resolved.effective_request, "replay": "inspection only; sr_prepare resolves supplied selections again"}
            manifest = _read_regular_bytes(destination / "manifest.json")
            references.append({"kind": "object", "id": identifier, "revision": None, "path": (destination / "manifest.json").relative_to(paths.root).as_posix(), "sha256": hashlib.sha256(manifest).hexdigest(), "size_bytes": len(manifest)})
        else:
            created = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            progress({"source": "adapter", "phase": "preparing", "worker_ids": [], "simulation_ids": [created.path.name], "data": {}})
            if name == "sr_prepare":
                runtime = prepare_sr_simulation(paths, config, created, worker, args["request"], args["selections"], capture_timeout_seconds=args["capture_timeout_seconds"], dependency_timeout_seconds=args["dependency_timeout_seconds"], progress=progress)
                engine, manifest_path = "sr", created.path / "input" / "engine" / "runtime.json"
            elif name == "trim_prepare":
                bundle = args["bundle"]
                destination = safe_path(paths.root / "memory" / "mcp" / context.name / "objects", bundle["id"])
                if hashlib.sha256(_read_regular_bytes(destination / "manifest.json")).hexdigest() != bundle["manifest_sha256"]:
                    raise ValueError("integrity: bundle manifest changed")
                inputs = load_trim_input_bundle(destination)
                runtime = prepare_trim_simulation(paths, config, created, worker, inputs, cascade_output=args["cascade_output"])
                engine, manifest_path = "trim", created.path / "input" / "execution" / "runtime.json"
            else:
                runtime = prepare_trim_continuation(paths, config, created, worker, args["parent_id"], total_ions=args["total_ions"], autosave_ions=args["autosave_ions"], events=args["events"], cascade_output=args["cascade_output"])
                engine, manifest_path = "trim", created.path / "input" / "execution" / "runtime.json"
            data = {"engine": engine, "simulation_id": created.path.name, "worker_id": worker.path.name, "runtime_manifest_sha256": hashlib.sha256(_read_regular_bytes(manifest_path)).hexdigest()}
    elif name in ("trim_bundle_load", "trim_parse", "trim_recover"):
        source = args["source"]
        if source["kind"] == "local-directory":
            directory = allowed_source(context, source["path"], directory=True)
        else:
            reference = source["reference"]
            manifest_path = safe_path(paths.root, reference["path"])
            owned = paths.root / "memory" / "mcp" / context.name
            if not manifest_path.is_relative_to(owned / "objects") and not manifest_path.is_relative_to(owned / "artifacts"):
                raise ValueError("unsafe-path: unowned artifact directory")
            read_verified_chunk(manifest_path, reference["sha256"], reference["size_bytes"], offset=0, length=1)
            directory = manifest_path.parent
            manifest = load_json(_read_regular_bytes(manifest_path))
            inventory = manifest.get("files", manifest.get("outputs"))
            if not isinstance(inventory, list):
                raise ValueError("integrity: artifact directory lacks a supported exact inventory")
            for file in inventory:
                read_verified_chunk(safe_path(directory, file["path"]), file["sha256"], file["size_bytes"], offset=0, length=1)
        before = _inventory(directory, directory)
        if name == "trim_bundle_load":
            identifier = "object_" + str(uuid4())
            destination = safe_path(paths.root / "memory" / "mcp" / context.name / "objects", identifier)
            destination.parent.mkdir(parents=True, exist_ok=True)
            inputs = load_trim_input_bundle(directory)
            write_trim_input_bundle(destination, inputs)
            payload = _read_regular_bytes(destination / "manifest.json")
            data = {"id": identifier, "manifest_sha256": hashlib.sha256(payload).hexdigest()}
            reference_kind = "object"
        else:
            identifier = "artifact_" + str(uuid4())
            destination = safe_path(paths.root / "memory" / "mcp" / context.name / "artifacts", identifier)
            destination.parent.mkdir(parents=True, exist_ok=True)
            progress({"source": "adapter", "phase": "parsing", "worker_ids": [], "simulation_ids": [], "data": {}})
            data = export_trim_checkpoint_recovery(directory, destination) if name == "trim_recover" else {"directory": str(parse_trim_directory(directory, destination, request=args["request"]))}
            reference_kind = "artifact"
        if before != _inventory(directory, directory):
            raise ValueError("integrity: source changed during frozen import")
        payload = _read_regular_bytes(destination / "manifest.json")
        references.append({"kind": reference_kind, "id": identifier, "revision": None, "path": (destination / "manifest.json").relative_to(paths.root).as_posix(), "sha256": hashlib.sha256(payload).hexdigest(), "size_bytes": len(payload)})
    elif name in ("run_submit", "batch_submit"):
        prepared = [args["prepared"]] if name == "run_submit" else args["tasks"]
        if len({task["simulation_id"] for task in prepared}) != len(prepared):
            raise ValueError("Duplicate batch simulation identity")
        verified = []
        for task in prepared:
            engine = task["engine"]
            if name == "batch_submit" and engine != args["engine"]:
                raise ValueError("Mixed batch engines")
            if engine == "sr" and args["timeout_seconds"] is None:
                raise ValueError("SR requires finite timeout")
            if name == "batch_submit" and engine == "sr" and (args["worker_timeouts"] is not None or args["cleanup"] is False):
                raise ValueError("SR batches always clean and do not accept worker_timeouts")
            record = safe_path(paths.simulation_memory, task["simulation_id"])
            manifest = record / "input" / ("engine" if engine == "sr" else "execution") / "runtime.json"
            if hashlib.sha256(_read_regular_bytes(manifest)).hexdigest() != task["runtime_manifest_sha256"]:
                raise ValueError("integrity: prepared runtime manifest mismatch")
            runtime = safe_path(paths.workers, task["worker_id"] + "/" + engine + "/" + task["simulation_id"])
            (verify_sr_runtime if engine == "sr" else verify_trim_runtime)(paths, task["simulation_id"], runtime)
            verified.append((task, runtime))
        progress({"source": "adapter", "phase": "executing", "worker_ids": [task["worker_id"] for task in prepared], "simulation_ids": [task["simulation_id"] for task in prepared], "data": {}})
        if name == "run_submit":
            task, runtime = verified[0]
            data = run_sr_simulation(paths, config, task["simulation_id"], runtime, timeout_seconds=args["timeout_seconds"]) if task["engine"] == "sr" else run_trim_simulation(paths, config, task["simulation_id"], runtime, timeout_seconds=args["timeout_seconds"], cancel=cancel)
            outcome = "succeeded" if data["status"] == "succeeded" else "failed"
            if task["engine"] == "trim" and data["status"] == "failed":
                execution = _read_json(record / "output" / "raw" / "execution.json")
                if isinstance(execution.get("failure"), dict) and execution["failure"].get("code") == "cancelled":
                    outcome = "cancelled"
            parsed_path = record / "output" / "parsed" / ("result.json" if task["engine"] == "sr" else "results.json")
            parser_complete = None
            if parsed_path.exists():
                parsed = _read_json(parsed_path)
                parser_complete = parsed.get("complete", True if task["engine"] == "sr" and isinstance(parsed.get("rows"), list) else None)
            progress({"source": "adapter", "phase": "finalizing", "worker_ids": [], "simulation_ids": [], "data": {"native_status": data["status"], "parser_complete": parser_complete}})
        else:
            if args["engine"] == "sr":
                batch = run_sr_batch(paths, config, [PreparedSRTask(task["simulation_id"], runtime) for task, runtime in verified], max_workers=args["max_workers"], timeout_seconds=args["timeout_seconds"])
            else:
                batch = run_trim_batch(paths, config, [PreparedTRIMTask(task["simulation_id"], runtime) for task, runtime in verified], max_workers=args["max_workers"], timeout_seconds=args["timeout_seconds"], worker_timeouts=args["worker_timeouts"], cleanup=False if args["cleanup"] is None else args["cleanup"])
            results = [{"simulation_id": result.task.simulation_id, "metadata": result.metadata, "error": None if result.error is None else {"type": type(result.error).__name__, "message": str(result.error)}, "elapsed_seconds": result.elapsed_seconds} for result in batch.task_results]
            succeeded = sum(result["metadata"] is not None and result["metadata"]["status"] == "succeeded" for result in results)
            outcome = "succeeded" if succeeded == len(results) else "partial" if succeeded else "failed"
            data = {"task_results": results, "elapsed_seconds": batch.elapsed_seconds, "cleanup": {"cleaned_workers": [str(worker) for worker in batch.cleanup.cleaned_workers], "retained_workers": [{"worker": str(item.worker), "reason": item.reason, "error": None if item.error is None else str(item.error)} for item in batch.cleanup.retained_workers]}}
    elif name == "index_update":
        progress({"source": "adapter", "phase": "indexing", "worker_ids": [], "simulation_ids": [], "data": {}})
        if args["operation"] == "initialize":
            if args["simulation_ids"]:
                raise ValueError("Index initialize requires empty identities")
            data = initialize_index(paths)
        else:
            data = (update_index if args["operation"] == "update" else rebuild_index)(paths, args["simulation_ids"])
        if data.get("state") in ("failed", "partial"):
            outcome = data["state"]
    elif name == "workers_cleanup":
        progress({"source": "adapter", "phase": "cleanup", "worker_ids": [], "simulation_ids": [], "data": {}})
        workers = [safe_path(paths.workers, worker) for worker in args["worker_ids"]]
        if len(set(workers)) != len(workers):
            raise ValueError("Duplicate cleanup worker")
        cleanup = (cleanup_sr_batch if args["engine"] == "sr" else cleanup_trim_batch)(paths, workers)
        data = {"cleaned_workers": [str(worker) for worker in cleanup.cleaned_workers], "retained_workers": [{"worker": str(item.worker), "reason": item.reason, "error": None if item.error is None else str(item.error)} for item in cleanup.retained_workers]}
    else:
        raise ValueError("unsupported: unknown scientific mutation")
    return {"outcome": outcome, "data": data, "references": references, "warnings": warnings}
