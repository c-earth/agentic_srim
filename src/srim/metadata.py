"""Persist simulation provenance, shared references, and execution lifecycle metadata.

Request documents contain schema_version and an opaque parameters object.
Resolved documents contain schema_version, parameters, sources, resources, and
cab. Each engine resource selection contains a ResourceReference-shaped reference,
source description/version/citation, and a runtime_filename. Optional transformation
provenance contains original reference, method, version, and parameters. Sources
contain id, name, nullable version/citation, and sha256.

Derived CAB snapshots reference independent core_dataset and bond_dataset objects;
core_entries and bond_entries identify selected dataset rows without copying their
original contributions. Composition uses atomic_number, atomic_mass, and atom_count.
Bonds use identifier/count. Overrides use dataset, entry_id, value, units, and reason.
Reference conditions and calculation provenance are validated structurally; input
builders retain responsibility for physical compatibility and CAB calculations.

Local input files are inventoried on initialization and sealed when execution
starts. Shared payloads remain in the resource store. Metadata APIs do not run an
engine, copy runtime resources, generate engine inputs, or parse outputs. JSON
schemas are exact version-one objects; engine-specific parameters remain opaque.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
import errno
import json
import os
from operator import itemgetter
from pathlib import Path, PurePosixPath
import re

from src.backend.config import Config
from src.backend.paths import ProjectPaths
from src.backend.records import CreatedDirectory, DeleteRequest, _lock_parent, _record_time, _validate_delete_request, _validate_paths
from src.backend.resources import ResourceReference, _hash_file, _read_json as _read_resource_json, _validate_reference, _write_json as _write_resource_json, verify_resource


type JSONValue = None | bool | int | float | str | list[JSONValue] | dict[str, JSONValue]
type JSONObject = dict[str, JSONValue]


def _validate_document(document: JSONObject, document_type: str) -> None:
    """Validate document against document_type's version-one schema; return None or raise ValueError without filesystem effects.

    Nested references, selections, manifests, CAB snapshots, continuation links,
    and lifecycle timestamps are structurally validated here. Engine physics,
    CAB dataset interpretation, and numerical calculation are intentionally
    delegated to input preparation. All JSON keys must be strings and numbers finite.
    """
    if not isinstance(document, dict):
        raise ValueError(f"Expected {document_type} object")
    try:
        json.dumps(document, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError("Expected finite JSON data") from error
    pending: list[object] = [document]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            if any(not isinstance(key, str) for key in value):
                raise ValueError("JSON object keys must be strings")
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
        elif value is not None and type(value) not in (str, bool, int, float):
            raise ValueError("Unsupported JSON value")
    if document_type in ("request", "resolved", "metadata"):
        if type(document.get("schema_version")) is not int or document["schema_version"] != 1:
            raise ValueError("Unsupported simulation schema")
    if document_type == "reference":
        if set(document) != {"category", "subcategory", "name", "version", "sha256", "size_bytes"}:
            raise ValueError("Invalid resource reference fields")
        _validate_reference(ResourceReference(**document))
    elif document_type == "selection":
        if not {"reference", "source", "runtime_filename"} <= set(document) or set(document) - {"reference", "source", "runtime_filename", "transformation"}:
            raise ValueError("Invalid engine resource selection fields")
        _validate_document(document["reference"], "reference")
        if document["reference"]["category"] != "engine":
            raise ValueError("Engine selections require engine resources")
        source = document["source"]
        if not isinstance(source, dict) or set(source) != {"description", "version", "citation"} or not isinstance(source["description"], str) or not source["description"].strip() or any(source[key] is not None and not isinstance(source[key], str) for key in ("version", "citation")):
            raise ValueError("Invalid resource source")
        name = document["runtime_filename"]
        if not isinstance(name, str) or not name or name in (".", "..") or any(ord(character) < 32 or character in '<>:"/\\|?*' for character in name) or name.endswith((".", " ")) or os.path.isreserved(name):
            raise ValueError("Invalid runtime filename")
        if "transformation" in document:
            transformation = document["transformation"]
            if not isinstance(transformation, dict) or set(transformation) != {"original", "method", "version", "parameters"} or not isinstance(transformation["parameters"], dict) or any(not isinstance(transformation[key], str) or not transformation[key].strip() for key in ("method", "version")):
                raise ValueError("Invalid transformation provenance")
            _validate_document(transformation["original"], "reference")
    elif document_type == "engine":
        if set(document) != {"name", "version", "executable"} or document["name"] not in ("sr", "trim") or not isinstance(document["version"], str) or not document["version"].strip():
            raise ValueError("Invalid engine identity")
        executable = document["executable"]
        if not isinstance(executable, dict) or set(executable) != {"name", "sha256"} or not isinstance(executable["name"], str) or not executable["name"] or executable["name"] in (".", "..") or any(ord(character) < 32 or character in '<>:"/\\|?*' for character in executable["name"]) or executable["name"].endswith((".", " ")) or os.path.isreserved(executable["name"]) or not isinstance(executable["sha256"], str) or re.fullmatch(r"[a-f0-9]{64}", executable["sha256"]) is None:
            raise ValueError("Invalid executable provenance")
    elif document_type == "manifest":
        if set(document) != {"path", "size_bytes", "sha256"}:
            raise ValueError("Invalid file manifest fields")
        name = document["path"]
        if not isinstance(name, str) or not name or "\\" in name or ":" in name or PurePosixPath(name).is_absolute() or any(part in ("", ".", "..") for part in name.split("/")):
            raise ValueError("Manifest path must be a normalized record-relative path")
        if type(document["size_bytes"]) is not int or document["size_bytes"] < 0 or not isinstance(document["sha256"], str) or re.fullmatch(r"[a-f0-9]{64}", document["sha256"]) is None:
            raise ValueError("Invalid file manifest size or hash")
    elif document_type == "continuation":
        if set(document) != {"parent_id", "files"} or not isinstance(document["files"], list):
            raise ValueError("Invalid continuation fields")
        _validate_delete_request(DeleteRequest("srim", document["parent_id"]))
        names: list[str] = []
        for item in document["files"]:
            _validate_document(item, "manifest")
            if not item["path"].startswith("input/continuation/"):
                raise ValueError("Continuation files must be inside input/continuation")
            names.append(item["path"].casefold())
        if len(names) != len(set(names)):
            raise ValueError("Duplicate continuation file")
    elif document_type == "cab":
        mode = document.get("mode")
        fields = {"mode", "applied_factor"}
        if mode == "supplied":
            fields |= {"source", "reason"}
        elif mode == "derived":
            fields |= {"core_dataset", "bond_dataset", "core_entries", "bond_entries", "composition", "bonds", "overrides", "reference", "calculation"}
        elif mode != "none":
            raise ValueError("Invalid CAB mode")
        if set(document) != fields:
            raise ValueError("Invalid CAB snapshot fields")
        quantities: list[tuple[object, bool]] = [(document["applied_factor"], True)]
        if mode == "none" and document["applied_factor"] != "1.0":
            raise ValueError("No correction requires applied_factor 1.0")
        if mode == "supplied" and any(not isinstance(document[key], str) or not document[key].strip() for key in ("source", "reason")):
            raise ValueError("Supplied factors require source and reason")
        if mode == "derived":
            for part in ("core", "bond"):
                _validate_document(document[f"{part}_dataset"], "reference")
                if document[f"{part}_dataset"]["category"] != "cab" or document[f"{part}_dataset"]["subcategory"] != part:
                    raise ValueError("CAB dataset category/subcategory mismatch")
                entries = document[f"{part}_entries"]
                if not isinstance(entries, list) or any(not isinstance(item, str) or not item.strip() for item in entries) or len(entries) != len(set(entries)):
                    raise ValueError("Invalid CAB entry identifiers")
            if not isinstance(document["composition"], list) or not document["composition"] or not isinstance(document["bonds"], list) or not isinstance(document["overrides"], list):
                raise ValueError("Invalid CAB composition, bonds, or overrides")
            for atom in document["composition"]:
                if not isinstance(atom, dict) or set(atom) != {"atomic_number", "atomic_mass", "atom_count"} or type(atom["atomic_number"]) is not int or not 1 <= atom["atomic_number"] <= 118:
                    raise ValueError("Invalid CAB atom")
                quantities.extend(((atom["atomic_mass"], True), (atom["atom_count"], True)))
            bond_ids: list[str] = []
            for bond in document["bonds"]:
                if not isinstance(bond, dict) or set(bond) != {"identifier", "count"} or not isinstance(bond["identifier"], str) or not bond["identifier"].strip():
                    raise ValueError("Invalid CAB bond")
                bond_ids.append(bond["identifier"])
                quantities.append((bond["count"], False))
            if len(bond_ids) != len(set(bond_ids)):
                raise ValueError("Duplicate CAB bond")
            override_ids: list[tuple[str, str]] = []
            for override in document["overrides"]:
                if not isinstance(override, dict) or set(override) != {"dataset", "entry_id", "value", "units", "reason"} or override["dataset"] not in ("core", "bond") or any(not isinstance(override[key], str) or not override[key].strip() for key in ("entry_id", "units", "reason")):
                    raise ValueError("Invalid CAB contribution override")
                if override["entry_id"] not in document[f'{override["dataset"]}_entries']:
                    raise ValueError("Override must identify a selected entry")
                override_ids.append((override["dataset"], override["entry_id"]))
                quantities.append((override["value"], False))
            if len(override_ids) != len(set(override_ids)):
                raise ValueError("Duplicate CAB override")
            reference = document["reference"]
            if not isinstance(reference, dict) or set(reference) != {"projectile", "mass_amu", "energy_keV", "phase", "contribution_units", "normalization"} or type(reference["projectile"]) is not int or not 1 <= reference["projectile"] <= 118 or reference["phase"] not in ("solid", "gas") or any(not isinstance(reference[key], str) or not reference[key].strip() for key in ("contribution_units", "normalization")):
                raise ValueError("Invalid CAB reference conditions")
            quantities.extend(((reference["mass_amu"], True), (reference["energy_keV"], True)))
            calculation = document["calculation"]
            if not isinstance(calculation, dict) or not {"method", "version", "intermediate_totals", "calculated_factor"} <= set(calculation) or set(calculation) - {"method", "version", "intermediate_totals", "calculated_factor", "reference_simulation_id"} or any(not isinstance(calculation[key], str) or not calculation[key].strip() for key in ("method", "version")) or not isinstance(calculation["intermediate_totals"], dict):
                raise ValueError("Invalid CAB calculation provenance")
            if "reference_simulation_id" in calculation:
                _validate_delete_request(DeleteRequest("srim", calculation["reference_simulation_id"]))
            quantities.extend((item, False) for item in calculation["intermediate_totals"].values())
            quantities.append((calculation["calculated_factor"], True))
        for value, positive in quantities:
            if not isinstance(value, str) or re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", value) is None:
                raise ValueError("CAB quantities must be finite decimal strings")
            try:
                number = Decimal(value)
            except InvalidOperation as error:
                raise ValueError("Invalid CAB decimal") from error
            if not number.is_finite() or number < 0 or (positive and number == 0):
                raise ValueError("CAB quantity outside allowed range")
    elif document_type in ("request", "resolved"):
        expected = {"schema_version", "parameters"} if document_type == "request" else {"schema_version", "parameters", "sources", "resources", "cab"}
        if set(document) != expected or not isinstance(document["parameters"], dict):
            raise ValueError(f"Invalid {document_type} fields")
        if document_type == "resolved":
            if not isinstance(document["resources"], list) or not isinstance(document["sources"], list):
                raise ValueError("Resources and sources must be lists")
            filenames: list[str] = []
            for selection in document["resources"]:
                _validate_document(selection, "selection")
                filenames.append(selection["runtime_filename"].casefold())
            if len(filenames) != len(set(filenames)):
                raise ValueError("Duplicate runtime resource filenames")
            source_ids: list[str] = []
            for source in document["sources"]:
                if not isinstance(source, dict) or set(source) != {"id", "name", "version", "sha256", "citation"} or any(not isinstance(source[key], str) or not source[key].strip() for key in ("id", "name")) or any(source[key] is not None and not isinstance(source[key], str) for key in ("version", "citation")) or not isinstance(source["sha256"], str) or re.fullmatch(r"[0-9a-f]{64}", source["sha256"]) is None:
                    raise ValueError("Invalid input source provenance")
                source_ids.append(source["id"])
            if len(source_ids) != len(set(source_ids)):
                raise ValueError("Duplicate input source identifier")
            if document["cab"] is not None:
                _validate_document(document["cab"], "cab")
    elif document_type == "metadata":
        if set(document) != {"schema_version", "id", "engine", "resources", "status", "created_at", "started_at", "finished_at", "continuation", "inputs", "outputs", "failure"}:
            raise ValueError("Invalid metadata fields")
        _validate_delete_request(DeleteRequest("srim", document["id"]))
        _validate_document(document["engine"], "engine")
        if not isinstance(document["resources"], list):
            raise ValueError("Metadata resources must be a list")
        for reference in document["resources"]:
            _validate_document(reference, "reference")
        if document["status"] not in ("created", "running", "succeeded", "failed"):
            raise ValueError("Invalid simulation status")
        for key in ("created_at", "started_at", "finished_at"):
            value = document[key]
            if value is None and key != "created_at":
                continue
            if not isinstance(value, str) or datetime.fromisoformat(value).utcoffset() is None:
                raise ValueError("Metadata times require explicit offsets")
        status = document["status"]
        if (status == "created" and (document["started_at"] is not None or document["finished_at"] is not None)) or (status == "running" and (document["started_at"] is None or document["finished_at"] is not None)) or (status == "succeeded" and (document["started_at"] is None or document["finished_at"] is None)) or (status == "failed" and document["finished_at"] is None):
            raise ValueError("Timestamps inconsistent with status")
        if document["continuation"] is not None:
            _validate_document(document["continuation"], "continuation")
            if document["engine"]["name"] != "trim" or document["continuation"]["parent_id"] == document["id"]:
                raise ValueError("Only TRIM may continue a different simulation")
        for key, prefix in (("inputs", "input/"), ("outputs", "output/")):
            if not isinstance(document[key], list):
                raise ValueError("Manifests must be lists")
            names: list[str] = []
            for item in document[key]:
                _validate_document(item, "manifest")
                if not item["path"].startswith(prefix):
                    raise ValueError("Manifest entry is outside its input/output tree")
                names.append(item["path"].casefold())
            if len(names) != len(set(names)):
                raise ValueError("Duplicate manifest paths")
        if status == "failed":
            _validate_document(document["failure"], "failure")
        elif document["failure"] is not None:
            raise ValueError("Only failed records have failure details")
    elif document_type == "failure":
        if set(document) != {"message", "exit_code"} or not isinstance(document["message"], str) or not document["message"].strip() or (document["exit_code"] is not None and type(document["exit_code"]) is not int):
            raise ValueError("Invalid failure details")
    else:
        raise ValueError(f"Unknown document type: {document_type}")


def _simulation_path(paths: ProjectPaths, simulation_id: str) -> Path:
    """Validate paths, canonical simulation_id, and link-free existing record; return Path or raise ValueError/OSError without writes."""
    _validate_paths(paths)
    _validate_delete_request(DeleteRequest("srim", simulation_id))
    path = paths.simulation_memory / simulation_id
    if path.is_symlink() or path.is_junction():
        raise OSError(errno.ELOOP, "Simulation directory is a link", str(path))
    if not path.is_dir():
        raise FileNotFoundError(errno.ENOENT, "Simulation directory is missing", str(path))
    return path


def _inventory(directory: Path, record: Path) -> list[JSONObject]:
    """Hash directory's regular link-free descendants beneath record; return sorted manifests or raise ValueError/OSError without writes."""
    if not directory.is_relative_to(record):
        raise ValueError("Inventory must remain beneath the record")
    for entry in (*reversed(directory.parents), directory):
        if entry.is_symlink() or entry.is_junction():
            raise OSError(errno.ELOOP, "Inventory path contains a link", str(entry))
    if not directory.is_dir():
        raise FileNotFoundError(errno.ENOENT, "Inventory directory is missing", str(directory))
    result: list[JSONObject] = []
    pending = [directory]
    while pending:
        for entry in pending.pop().iterdir():
            if entry.is_symlink() or entry.is_junction():
                raise OSError(errno.ELOOP, "Inventory contains a link", str(entry))
            if entry.is_dir():
                pending.append(entry)
            else:
                checksum, size = _hash_file(entry)
                result.append({"path": entry.relative_to(record).as_posix(), "size_bytes": size, "sha256": checksum})
    result.sort(key=itemgetter("path"))
    names = [item["path"].casefold() for item in result]
    if len(names) != len(set(names)):
        raise ValueError("Case-insensitive inventory path collision")
    return result


def _read_json(path: Path) -> JSONObject:
    """Read path as finite JSON object; return independent data or raise ValueError/OSError without data writes."""
    return _read_resource_json(path)


def _write_json(path: Path, document: JSONObject) -> None:
    """Synchronize and atomically replace path with document; return None, preserving/reporting temporary files on OSError."""
    _write_resource_json(path, document)


def initialize_simulation(paths: ProjectPaths, created: CreatedDirectory, engine: JSONObject, request: JSONObject, resolved: JSONObject, continuation: JSONObject | None = None) -> JSONObject:
    """Initialize created's empty simulation under paths with engine/request/resolved and optional TRIM continuation.

    Return independent stored metadata. Validate all inputs and shared resources
    before creating the layout under the simulation-parent lock. Raise ValueError
    on schema/identity conflicts or OSError on file/lock failure. Keep partial
    writes with exception notes; metadata is published last. No execution occurs.
    """
    _validate_document(engine, "engine")
    _validate_document(request, "request")
    _validate_document(resolved, "resolved")
    if created.kind != "srim" or created.timestamp is None or created.timestamp.utcoffset() is None or type(created.suffix) is not int or created.suffix < 1:
        raise ValueError("Expected an allocated simulation with aware timestamp")
    instant = created.timestamp
    expected = f"srim_{instant.year:04d}{instant.month:02d}{instant.day:02d}_{instant.hour:02d}{instant.minute:02d}{instant.second:02d}_{created.suffix}"
    if created.path != paths.simulation_memory / expected:
        raise ValueError("Allocated simulation path does not match its timestamp and suffix")
    record = _simulation_path(paths, expected)
    references = [selection["reference"] for selection in resolved["resources"]]
    references.extend(selection["transformation"]["original"] for selection in resolved["resources"] if "transformation" in selection)
    if resolved["cab"] is not None and resolved["cab"]["mode"] == "derived":
        references.extend((resolved["cab"]["core_dataset"], resolved["cab"]["bond_dataset"]))
    metadata: JSONObject = {"schema_version": 1, "id": expected, "engine": engine, "resources": references, "status": "created", "created_at": instant.isoformat(), "started_at": None, "finished_at": None, "continuation": continuation, "inputs": [], "outputs": [], "failure": None}
    _validate_document(metadata, "metadata")
    completed: list[Path] = []
    try:
        with _lock_parent(paths.simulation_memory):
            _simulation_path(paths, expected)
            if any(record.iterdir()):
                raise ValueError("Simulation initialization requires an empty directory")
            for reference in references:
                verify_resource(paths, ResourceReference(**reference))
            for name in ("input", "input/engine", "input/continuation", "output", "output/raw", "output/parsed"):
                target = record / name
                target.mkdir()
                completed.append(target)
            for filename, document in (("input/request.json", request), ("input/resolved.json", resolved)):
                target = record / filename
                _write_json(target, document)
                completed.append(target)
            metadata["inputs"] = _inventory(record / "input", record)
            _write_json(record / "metadata.json", metadata)
            return _read_json(record / "metadata.json")
    except (OSError, ValueError) as error:
        error.add_note("Simulation initialization retained: " + ", ".join(str(path) for path in completed))
        raise


def read_simulation(paths: ProjectPaths, simulation_id: str) -> JSONObject:
    """Read paths' simulation_id without data writes; return validated independent metadata or raise ValueError/OSError, requiring no installed historical resources."""
    record = _simulation_path(paths, simulation_id)
    with _lock_parent(paths.simulation_memory):
        metadata = _read_json(record / "metadata.json")
        _validate_document(metadata, "metadata")
        if metadata["id"] != simulation_id:
            raise ValueError("Metadata identity differs from its directory")
        return metadata


def start_simulation(paths: ProjectPaths, config: Config, simulation_id: str) -> JSONObject:
    """Start paths' created simulation_id using config time; verify resources/inputs, seal manifests, and return updated metadata or raise ValueError/OSError."""
    record = _simulation_path(paths, simulation_id)
    with _lock_parent(paths.simulation_memory):
        metadata = _read_json(record / "metadata.json")
        _validate_document(metadata, "metadata")
        if metadata["id"] != simulation_id or metadata["status"] != "created":
            raise ValueError("Only the matching created simulation can start")
        request = _read_json(record / "input" / "request.json")
        resolved = _read_json(record / "input" / "resolved.json")
        _validate_document(request, "request")
        _validate_document(resolved, "resolved")
        current = _inventory(record / "input", record)
        documents = {item["path"]: item for item in current}
        for original in metadata["inputs"]:
            if original["path"] in ("input/request.json", "input/resolved.json") and documents.get(original["path"]) != original:
                raise ValueError("Initialized input documents were changed")
        if not _inventory(record / "input" / "engine", record):
            raise ValueError("At least one engine-input file is required")
        for reference in metadata["resources"]:
            verify_resource(paths, ResourceReference(**reference))
        continuation = metadata["continuation"]
        if continuation is not None:
            actual = _inventory(record / "input" / "continuation", record)
            if not continuation["files"] or sorted(continuation["files"], key=itemgetter("path")) != actual:
                raise ValueError("Continuation files are missing or differ from their manifest")
        metadata["inputs"] = current
        metadata["status"] = "running"
        metadata["started_at"] = _record_time(config, None).isoformat()
        _validate_document(metadata, "metadata")
        _write_json(record / "metadata.json", metadata)
        return metadata


def finish_simulation(paths: ProjectPaths, config: Config, simulation_id: str, failure: JSONObject | None = None) -> JSONObject:
    """Finish paths' simulation_id using config time, optionally recording failure; return metadata after output inventory or raise ValueError/OSError.

    Success requires running state and unchanged sealed local inputs. Failure
    permits created/running states and preserves evidence even if input integrity
    was lost. Terminal states cannot change. This function does not run engines,
    interpret outputs, or modify shared resources.
    """
    if failure is not None:
        _validate_document(failure, "failure")
    record = _simulation_path(paths, simulation_id)
    with _lock_parent(paths.simulation_memory):
        metadata = _read_json(record / "metadata.json")
        _validate_document(metadata, "metadata")
        if metadata["id"] != simulation_id or metadata["status"] not in ("created", "running") or (failure is None and metadata["status"] != "running"):
            raise ValueError("Invalid completion transition")
        if failure is None and _inventory(record / "input", record) != metadata["inputs"]:
            raise ValueError("Sealed simulation inputs were changed")
        metadata["outputs"] = _inventory(record / "output", record)
        metadata["status"] = "succeeded" if failure is None else "failed"
        metadata["failure"] = failure
        metadata["finished_at"] = _record_time(config, None).isoformat()
        _validate_document(metadata, "metadata")
        _write_json(record / "metadata.json", metadata)
        return _read_json(record / "metadata.json")
