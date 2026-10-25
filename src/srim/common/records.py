"""Persist strict simulation provenance, saved-input schemas and execution lifecycle."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation
import errno
import hashlib
import json
import os
from operator import itemgetter
from pathlib import Path, PurePosixPath
import re
from uuid import UUID, uuid4
from src.backend.config import Config
from src.backend.paths import ProjectPaths
from src.backend.records import CreatedDirectory, DeleteRequest, _lock_parent, _record_time, _validate_delete_request, _validate_paths
from src.backend.resources import ResourceReference, _validate_reference, verify_resource
from src.srim.common.types import JSONObject, JSONValue
from src.srim.common.files import _inventory, _read_json, _write_json, _write_input_file, _read_regular_bytes


def _validate_document(document: JSONObject, document_type: str) -> None:
    """Validate document against document_type's legacy, version-two or TRIM schema; return None or raise ValueError without filesystem effects.

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
        if type(document.get("schema_version")) is not int or document["schema_version"] not in (1, 2):
            raise ValueError("Unsupported simulation schema")
    if document_type in ("request", "resolved") and document.get("engine") == "trim":
        from src.srim.trim.inputs import validate_trim_request
        if document_type == "request":
            validate_trim_request(document)
        else:
            expected = {"schema_version", "engine", "installation", "defaults", "resources", "layers", "atom_index_map", "dependencies", "native_checks", "files"}
            if set(document) != expected or document["schema_version"] != 1 or not isinstance(document["resources"], dict) or not isinstance(document["files"], list):
                raise ValueError("Invalid resolved TRIM schema")
            profile = document["installation"]
            if not isinstance(profile, dict) or set(profile) != {"schema_version", "engine_family", "version_text", "native_fields", "files"} or type(profile["schema_version"]) is not int or profile["schema_version"] != 1 or profile["engine_family"] not in ("2008", "2013") or not isinstance(profile["version_text"], str) or not isinstance(profile["native_fields"], dict) or not isinstance(profile["files"], list):
                raise ValueError("Invalid TRIM installation profile")
            if not {"atomdata", "electronic", "nuclear", "core", "bond", "damage"} <= set(document["resources"]) or set(document["resources"]) - {"atomdata", "electronic", "nuclear", "core", "bond", "damage", "compound"}:
                raise ValueError("Invalid TRIM resource roles")
            for role in document["resources"].values():
                _validate_document(role, "resolved-role")
            if not isinstance(document["defaults"], dict) or set(document["defaults"]) != {"sr", "trim", "dependency_engine"}:
                raise ValueError("Invalid TRIM default evidence")
            for key in ("layers", "atom_index_map", "dependencies", "native_checks"):
                if not isinstance(document[key], list) or any(not isinstance(item, dict) for item in document[key]):
                    raise ValueError(f"Invalid TRIM {key} evidence")
            if not document["layers"] or not document["atom_index_map"]:
                raise ValueError("TRIM requires layer/component provenance")
            for item in profile["files"]:
                _validate_document(item, "manifest")
            for item in document["files"]:
                _validate_document(item, "manifest")
        return
    if document_type == "entry-reference":
        if set(document) != {"dataset", "dependency_key", "entry_id", "sha256", "size_bytes"}:
            raise ValueError("Invalid calculated entry reference fields")
        _validate_document(document["dataset"], "reference")
        dataset = document["dataset"]
        if dataset["category"] != "cab" or dataset["name"] not in ("core", "bond", "bragg") or re.fullmatch(r"v[1-9][0-9]*", dataset["version"]) is None:
            raise ValueError("Calculated entries require concrete CAB datasets")
        for field in ("dependency_key", "sha256"):
            if not isinstance(document[field], str) or re.fullmatch(r"[0-9a-f]{64}", document[field]) is None:
                raise ValueError("Invalid entry hash")
        if not isinstance(document["entry_id"], str) or re.fullmatch(r"z[0-9]{3}(?:-z[0-9]{3}-[sdt])?", document["entry_id"]) is None or type(document["size_bytes"]) is not int or document["size_bytes"] < 0:
            raise ValueError("Invalid entry identity/size")
        return
    if document_type == "recorded-generation":
        from src.srim.resources.defaults import _validate_default_generation
        if set(document) != {"generation", "sha256", "size_bytes"}:
            raise ValueError("Invalid recorded generation")
        _validate_default_generation(document["generation"])
        encoded = (json.dumps(document["generation"], indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        import hashlib
        if document["sha256"] != hashlib.sha256(encoded).hexdigest() or type(document["size_bytes"]) is not int or document["size_bytes"] != len(encoded):
            raise ValueError("Recorded generation hash/size mismatch")
        return
    if document_type == "resolved-role":
        if set(document) != {"reference", "base", "source", "overrides", "effective", "published"} or not isinstance(document["overrides"], list):
            raise ValueError("Invalid resolved resource role")
        for field in ("reference", "base", "published"):
            if document[field] is not None:
                _validate_document(document[field], "reference")
        source = document["source"]
        if not isinstance(source, dict) or set(source) != {"kind", "origin", "snapshot"} or source["kind"] not in ("version", "file", "inline"):
            raise ValueError("Invalid resolved source")
        if source["kind"] == "version":
            _validate_document(source["origin"], "reference")
        elif not isinstance(source["origin"], str) or (source["kind"] == "file" and not Path(source["origin"]).is_absolute()) or (source["kind"] == "inline" and source["origin"] != "inline"):
            raise ValueError("Invalid resolved source origin")
        _validate_document(source["snapshot"], "manifest")
        _validate_document(document["effective"], "manifest")
        if any(not item["path"].startswith("input/resources/") for item in (source["snapshot"], document["effective"])):
            raise ValueError("Resource snapshots must be under input/resources")
        return
    if document_type == "cab-v2":
        if document.get("mode") != "derived":
            _validate_document(document, "cab")
            return
        if set(document) != {"mode", "applied_factor", "core_entries", "bond_entries", "bragg_entries", "composition", "bonds", "omissions", "reference", "calculation"}:
            raise ValueError("Invalid derived CAB fields")
        quantities: list[tuple[object, bool]] = [(document["applied_factor"], True)]
        for kind in ("core", "bond"):
            entries = document[f"{kind}_entries"]
            if not isinstance(entries, list):
                raise ValueError("CAB contributions require lists")
            seen: set[str] = set()
            for item in entries:
                if not isinstance(item, dict) or set(item) != {"entry_id", "value", "origin", "entry_reference", "snapshot", "override_index"} or item["origin"] not in ("dataset", "override", "recovered-default", "bragg-fallback", "zero-fallback", "convention") or not isinstance(item["entry_id"], str) or item["entry_id"] in seen:
                    raise ValueError("Invalid/duplicate CAB contribution")
                seen.add(item["entry_id"])
                quantities.append((item["value"], False))
                if item["entry_reference"] is not None:
                    _validate_document(item["entry_reference"], "entry-reference")
                if item["snapshot"] is not None:
                    _validate_document(item["snapshot"], "manifest")
                if item["override_index"] is not None and (type(item["override_index"]) is not int or item["override_index"] < 0):
                    raise ValueError("Invalid contribution override index")
        if not isinstance(document["bragg_entries"], list) or not isinstance(document["composition"], list) or not document["composition"] or not isinstance(document["bonds"], list) or not isinstance(document["omissions"], list):
            raise ValueError("Invalid CAB arrays")
        for item in document["bragg_entries"]:
            _validate_document(item, "entry-reference")
        for item in document["composition"]:
            if not isinstance(item, dict) or set(item) != {"atomic_number", "atomic_mass", "atom_count"} or type(item["atomic_number"]) is not int or not 1 <= item["atomic_number"] <= 92:
                raise ValueError("Invalid CAB composition")
            quantities.extend(((item["atomic_mass"], True), (item["atom_count"], True)))
        for item in document["bonds"]:
            if not isinstance(item, dict) or set(item) != {"identifier", "count"} or not isinstance(item["identifier"], str):
                raise ValueError("Invalid CAB bond")
            quantities.append((item["count"], False))
        for item in document["omissions"]:
            if not isinstance(item, dict) or set(item) != {"dataset", "entry_id", "reason"} or item["dataset"] not in ("core", "bond") or not isinstance(item["entry_id"], str) or item["reason"] not in ("missing-user-entry", "removed-entry", "unsupported-default-bond", "zero-count"):
                raise ValueError("Invalid CAB omission")
        reference = document["reference"]
        if not isinstance(reference, dict) or set(reference) != {"projectile", "mass_amu", "energy_keV", "phase", "contribution_units"} or type(reference["projectile"]) is not int or reference["projectile"] != 1 or reference["phase"] != "gas" or reference["contribution_units"] != "eV/(1E15 atoms/cm2)":
            raise ValueError("Invalid CAB reference")
        quantities.extend(((reference["mass_amu"], True), (reference["energy_keV"], True)))
        calculation = document["calculation"]
        if not isinstance(calculation, dict) or set(calculation) != {"method", "version", "precision", "rounding", "path", "intermediate_totals", "calculated_factor"} or not isinstance(calculation["method"], str) or not calculation["method"] or type(calculation["version"]) is not int or calculation["version"] < 1 or type(calculation["precision"]) is not int or calculation["precision"] != 28 or calculation["rounding"] != "ROUND_HALF_EVEN" or calculation["path"] not in ("weighted-sum", "no-effective-bonds") or not isinstance(calculation["intermediate_totals"], dict) or set(calculation["intermediate_totals"]) != {"core", "bond", "bragg"}:
            raise ValueError("Invalid derived CAB calculation")
        quantities.extend((value, False) for value in calculation["intermediate_totals"].values())
        quantities.append((calculation["calculated_factor"], True))
        for value, positive in quantities:
            if not isinstance(value, str) or re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", value) is None or not Decimal(value).is_finite() or Decimal(value) < 0 or (positive and Decimal(value) == 0):
                raise ValueError("Invalid CAB decimal quantity")
        if Decimal(calculation["calculated_factor"]) != Decimal(document["applied_factor"]):
            raise ValueError("CAB factor mismatch")
        return
    if document_type == "resolved" and document["schema_version"] == 2:
        if set(document) != {"schema_version", "parameters", "sources", "resources", "cab", "defaults", "target_provenance", "dependencies"} or not isinstance(document["parameters"], dict) or not isinstance(document["sources"], list) or not isinstance(document["resources"], dict) or not {"atomdata", "electronic", "nuclear"} <= set(document["resources"]) or set(document["resources"]) - {"atomdata", "electronic", "nuclear", "core", "bond", "compound"}:
            raise ValueError("Invalid version-two resolved schema")
        for role, selection in document["resources"].items():
            _validate_document(selection, "resolved-role")
            category = "material" if role in ("atomdata", "compound") else "engine" if role in ("electronic", "nuclear") else "cab"
            for field in ("reference", "base", "published"):
                value = selection[field]
                if value is not None and (value["category"] != category or value["name"] != role):
                    raise ValueError("Resolved resource role mismatch")
        legacy = {"schema_version": 1, "parameters": {}, "sources": document["sources"], "resources": [], "cab": None}
        _validate_document(legacy, "resolved")
        _validate_document(document["defaults"], "recorded-generation")
        _validate_document(document["cab"], "cab-v2")
        target = document["target_provenance"]
        if not isinstance(target, dict) or set(target) != {"source", "record_index", "original_record_index", "record_name", "composition_basis", "original_elements", "atomic_composition", "masses", "record_sha256", "mapping"} or target["source"] not in ("direct", "compound") or target["composition_basis"] not in ("atomic", "mass") or not isinstance(document["dependencies"], list):
            raise ValueError("Invalid target/dependency provenance")
        for dependency in document["dependencies"]:
            if not isinstance(dependency, dict) or set(dependency) not in ({"simulation_id", "result", "raw"}, {"manifest", "correction", "inputs", "stdout", "stderr"}):
                raise ValueError("Invalid dependency evidence shape")
        return
    if document_type == "reference":
        if set(document) != {"category", "name", "version", "sha256", "size_bytes"}:
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
        if set(document) != {"name", "version", "executable"} or document["name"] not in ("sr", "trim", "sr-capture") or not isinstance(document["version"], str) or not document["version"].strip():
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
                if document[f"{part}_dataset"]["category"] != "cab" or document[f"{part}_dataset"]["name"] != part:
                    raise ValueError("CAB dataset category/name mismatch")
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
        expected = {"schema_version", "id", "engine", "resources", "status", "created_at", "started_at", "finished_at", "continuation", "inputs", "outputs", "failure"}
        if document["schema_version"] == 2:
            expected |= {"entry_references", "defaults_generation"}
            if not isinstance(document.get("entry_references"), list):
                raise ValueError("Metadata entry references must be a list")
            for entry_reference in document["entry_references"]:
                _validate_document(entry_reference, "entry-reference")
            if document.get("defaults_generation") is not None:
                _validate_document(document["defaults_generation"], "recorded-generation")
        if set(document) != expected:
            raise ValueError("Invalid metadata fields")
        _validate_delete_request(DeleteRequest("srim", document["id"]))
        _validate_document(document["engine"], "engine")
        if not isinstance(document["resources"], list):
            raise ValueError("Metadata resources must be a list")
        for reference in document["resources"]:
            _validate_document(reference, "reference")
        if document["schema_version"] == 2:
            for key in ("resources", "entry_references"):
                identities = [json.dumps(item, sort_keys=True) for item in document[key]]
                if identities != sorted(set(identities)):
                    raise ValueError("Version-two references must be sorted and unique")
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
    elif document_type in ("execution-launch", "execution-stopped"):
        launch = document_type == "execution-launch"
        fields = {"schema_version", "simulation_id", "execution_id", "recorded_at"} | ({"engine", "worker_id", "runtime_path", "input_inventory_sha256"} if launch else {"launch_sha256", "reason", "exit_code"})
        if set(document) != fields or type(document["schema_version"]) is not int or document["schema_version"] != 1:
            raise ValueError("Invalid execution evidence schema")
        _validate_delete_request(DeleteRequest("srim", document["simulation_id"]))
        if not isinstance(document["execution_id"], str) or str(UUID(document["execution_id"])) != document["execution_id"]:
            raise ValueError("Invalid execution evidence UUID")
        if not isinstance(document["recorded_at"], str) or datetime.fromisoformat(document["recorded_at"]).utcoffset() is None:
            raise ValueError("Execution evidence requires an aware timestamp")
        digest = document["input_inventory_sha256" if launch else "launch_sha256"]
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError("Invalid execution evidence hash")
        if launch:
            if document["engine"] not in ("sr", "trim") or not isinstance(document["worker_id"], str) or re.fullmatch(r"worker_[1-9][0-9]*", document["worker_id"]) is None:
                raise ValueError("Invalid execution worker or engine")
            if document["runtime_path"] != f'worker/{document["worker_id"]}/{document["engine"]}/{document["simulation_id"]}':
                raise ValueError("Invalid execution runtime ownership")
        elif document["reason"] not in ("exit", "timeout", "controller-stopped") or document["exit_code"] is not None and type(document["exit_code"]) is not int:
            raise ValueError("Invalid stopped execution details")
    elif document_type == "run-closeout":
        if set(document) != {"schema_version", "complete", "rows", "counts", "issues"} or type(document["schema_version"]) is not int or document["schema_version"] != 1 or type(document["complete"]) is not bool or not isinstance(document["rows"], list) or not isinstance(document["issues"], list):
            raise ValueError("Invalid closeout report")
        categories = ("terminal", "unstarted", "stale-stopped", "stale-unresolved", "running-unresolved", "invalid")
        if not isinstance(document["counts"], dict) or set(document["counts"]) != {value.replace("-", "_") for value in categories}:
            raise ValueError("Invalid closeout counts")
        counts = {key: 0 for key in document["counts"]}
        ids = []
        for row in document["rows"]:
            if not isinstance(row, dict) or set(row) != {"simulation_id", "metadata_status", "classification", "worker_id", "runtime_path", "job_ids", "job_states", "stop_evidence", "recovery_action", "issues"} or row["classification"] not in categories or row["metadata_status"] not in (None, "created", "running", "succeeded", "failed") or type(row["stop_evidence"]) is not bool:
                raise ValueError("Invalid closeout row")
            _validate_delete_request(DeleteRequest("srim", row["simulation_id"]))
            if row["worker_id"] is not None and (not isinstance(row["worker_id"], str) or re.fullmatch(r"worker_[1-9][0-9]*", row["worker_id"]) is None):
                raise ValueError("Invalid closeout worker")
            if row["runtime_path"] is not None and (row["worker_id"] is None or row["runtime_path"] not in (f'worker/{row["worker_id"]}/sr/{row["simulation_id"]}', f'worker/{row["worker_id"]}/trim/{row["simulation_id"]}')):
                raise ValueError("Invalid closeout runtime")
            if row["recovery_action"] not in ("none", "reconcile", "investigate-native-ownership", "repair-record") or not isinstance(row["issues"], list):
                raise ValueError("Invalid closeout action")
            for key in ("job_ids", "job_states"):
                if not isinstance(row[key], list) or any(not isinstance(value, str) for value in row[key]) or row[key] != sorted(set(row[key])):
                    raise ValueError("Invalid closeout job references")
            for issue in row["issues"]:
                if not isinstance(issue, dict) or set(issue) != {"code", "message"} or any(not isinstance(value, str) or not value for value in issue.values()):
                    raise ValueError("Invalid closeout row issue")
            counts[row["classification"].replace("-", "_")] += 1
            ids.append(row["simulation_id"])
        if ids != sorted(set(ids)) or any(type(value) is not int or value < 0 for value in document["counts"].values()) or counts != document["counts"] or document["complete"] != (counts["terminal"] + counts["unstarted"] == len(ids)):
            raise ValueError("Inconsistent closeout completion or counts")
        for issue in document["issues"]:
            if not isinstance(issue, dict) or set(issue) != {"simulation_id", "code", "message"} or any(not isinstance(value, str) or not value for value in issue.values()) or issue["simulation_id"] not in ids:
                raise ValueError("Invalid closeout issue")
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


def _validate_engine_inputs(engine_inputs: Mapping[str, bytes] | None) -> dict[str, bytes]:
    """Return an independent engine_inputs dictionary, or empty data for None.

    Require byte values and safe Windows leaf filenames without case-insensitive
    collisions. Raise ValueError on invalid mappings, names, or values. Perform
    no filesystem operations or changes to the supplied mapping.
    """
    if engine_inputs is None:
        return {}
    if not isinstance(engine_inputs, Mapping):
        raise ValueError("Engine inputs must be a mapping or None")
    result = dict(engine_inputs)
    names: set[str] = set()
    for name, contents in result.items():
        if not isinstance(name, str) or not name or name in (".", "..") or any(ord(character) < 32 or character in '<>:"/\\|?*' for character in name) or name.endswith((".", " ")) or os.path.isreserved(name):
            raise ValueError("Engine input names must be safe Windows leaf filenames")
        if name.casefold() in names:
            raise ValueError("Case-insensitive engine input filename collision")
        if not isinstance(contents, bytes):
            raise ValueError("Engine input contents must be bytes")
        names.add(name.casefold())
    return result


def _validate_input_files(input_files: Mapping[str, bytes] | None) -> dict[str, bytes]:
    """Return copied safe nested input_files bytes, or an empty map for None, without I/O.

    Accept resource/CAB/installation/dependency/continuation/execution descendants
    and effective-request.json/selections.json.
    Reject reserved, unsafe, duplicate, overlapping or nonbyte entries with ValueError.
    """
    if input_files is None:
        return {}
    if not isinstance(input_files, Mapping):
        raise ValueError("Input files must be a mapping")
    result = dict(input_files)
    seen: set[str] = set()
    for name, contents in result.items():
        if not isinstance(name, str) or not name or not isinstance(contents, bytes):
            raise ValueError("Input evidence requires string names and byte contents")
        parts = name.split("/")
        if name not in ("effective-request.json", "selections.json") and (len(parts) < 2 or parts[0] not in ("resources", "cab", "installation", "dependencies", "continuation", "execution")):
            raise ValueError("Unknown input evidence root")
        for part in parts:
            if not part or part in (".", "..") or part.endswith((".", " ")) or os.path.isreserved(part) or any(ord(char) < 32 or char in '<>:"\\|?*' for char in part):
                raise ValueError("Unsafe input evidence path")
        folded = name.casefold()
        if folded in seen or any(folded.startswith(existing + "/") or existing.startswith(folded + "/") for existing in seen):
            raise ValueError("Input evidence paths collide")
        seen.add(folded)
    return result


def _collect_resource_references(resolved: JSONObject) -> list[JSONObject]:
    """Return validated resolved ordinary references deterministically without I/O or mutation.

    Preserve legacy ordering; collect version-two roles, bases, outputs, pinned
    defaults and calculated datasets with deterministic deduplication. Raise
    ValueError for invalid reference/document structure.
    """
    _validate_document(resolved, "resolved")
    if resolved["schema_version"] == 1 and resolved.get("engine") != "trim":
        references = [selection["reference"] for selection in resolved["resources"]]
        references.extend(selection["transformation"]["original"] for selection in resolved["resources"] if "transformation" in selection)
        if resolved["cab"] is not None and resolved["cab"]["mode"] == "derived":
            references.extend((resolved["cab"]["core_dataset"], resolved["cab"]["bond_dataset"]))
        return json.loads(json.dumps(references))
    pending: list[JSONValue] = [resolved]
    references_by_identity: dict[str, JSONObject] = {}
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            if set(item) == {"category", "name", "version", "sha256", "size_bytes"}:
                _validate_document(item, "reference")
                identity = json.dumps(item, sort_keys=True)
                references_by_identity[identity] = item
            else:
                pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return json.loads(json.dumps([references_by_identity[key] for key in sorted(references_by_identity)]))


def initialize_simulation(paths: ProjectPaths, created: CreatedDirectory, engine: JSONObject, request: JSONObject, resolved: JSONObject, continuation: JSONObject | None = None, *, engine_inputs: Mapping[str, bytes] | None = None, input_files: Mapping[str, bytes] | None = None) -> JSONObject:
    """Initialize created's empty simulation under paths with engine/request/resolved and optional TRIM continuation.

    engine_inputs optionally maps safe flat filenames to exact bytes, defaulting
    to no prepared files. Write these under input/engine before inventorying inputs.
    input_files supplies validated nested resource/CAB and effective intent evidence.
    Return independent stored metadata. Validate all inputs and shared resources
    before creating the layout under the simulation-parent lock. Raise ValueError
    on schema/identity conflicts or OSError on file/lock failure. Keep partial
    writes with exception notes; metadata is published last. No execution occurs.
    """
    _validate_document(engine, "engine")
    _validate_document(request, "request")
    _validate_document(resolved, "resolved")
    prepared_inputs = _validate_engine_inputs(engine_inputs)
    evidence_inputs = _validate_input_files(input_files)
    if created.kind != "srim" or created.timestamp is None or created.timestamp.utcoffset() is None or type(created.suffix) is not int or created.suffix < 1:
        raise ValueError("Expected an allocated simulation with aware timestamp")
    instant = created.timestamp
    expected = f"srim_{instant.year:04d}{instant.month:02d}{instant.day:02d}_{instant.hour:02d}{instant.minute:02d}{instant.second:02d}_{created.suffix}"
    if created.path != paths.simulation_memory / expected:
        raise ValueError("Allocated simulation path does not match its timestamp and suffix")
    record = _simulation_path(paths, expected)
    references = _collect_resource_references(resolved)
    metadata: JSONObject = {"schema_version": resolved["schema_version"], "id": expected, "engine": engine, "resources": references, "status": "created", "created_at": instant.isoformat(), "started_at": None, "finished_at": None, "continuation": continuation, "inputs": [], "outputs": [], "failure": None}
    if resolved["schema_version"] == 2:
        from src.srim.cab.cache import CABEntryReference, load_cab_entry
        found: dict[str, JSONObject] = {}
        pending: list[JSONValue] = [resolved["cab"]]
        while pending:
            item = pending.pop()
            if isinstance(item, dict):
                if set(item) == {"dataset", "dependency_key", "entry_id", "sha256", "size_bytes"}:
                    _validate_document(item, "entry-reference")
                    load_cab_entry(paths, CABEntryReference(ResourceReference(**item["dataset"]), item["dependency_key"], item["entry_id"], item["sha256"], item["size_bytes"]))
                    found[json.dumps(item, sort_keys=True)] = item
                else:
                    pending.extend(item.values())
            elif isinstance(item, list):
                pending.extend(item)
        metadata["entry_references"] = [found[key] for key in sorted(found)]
        metadata["defaults_generation"] = resolved["defaults"]
    _validate_document(metadata, "metadata")
    completed: list[Path] = []
    try:
        with _lock_parent(paths.simulation_memory, timeout_seconds=30.0):
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
            for filename in sorted(prepared_inputs):
                target = record / "input" / "engine" / filename
                _write_input_file(target, prepared_inputs[filename])
                completed.append(target)
            for filename in sorted(evidence_inputs):
                target = record / "input" / filename
                for parent in reversed(target.parent.relative_to(record / "input").parents):
                    if str(parent) != ".":
                        (record / "input" / parent).mkdir(exist_ok=True)
                target.parent.mkdir(exist_ok=True)
                _write_input_file(target, evidence_inputs[filename])
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
    with _lock_parent(paths.simulation_memory, timeout_seconds=30.0):
        metadata = _read_json(record / "metadata.json")
        _validate_document(metadata, "metadata")
        if metadata["id"] != simulation_id:
            raise ValueError("Metadata identity differs from its directory")
        return metadata


def start_simulation(paths: ProjectPaths, config: Config, simulation_id: str) -> JSONObject:
    """Start paths' created simulation_id using config time; verify resources/inputs, seal manifests, and return updated metadata or raise ValueError/OSError."""
    record = _simulation_path(paths, simulation_id)
    with _lock_parent(paths.simulation_memory, timeout_seconds=30.0):
        metadata = _read_json(record / "metadata.json")
        _validate_document(metadata, "metadata")
        if metadata["id"] != simulation_id or metadata["status"] != "created":
            raise ValueError("Only the matching created simulation can start")
        request = _read_json(record / "input" / "request.json")
        resolved = _read_json(record / "input" / "resolved.json")
        _validate_document(request, "request")
        _validate_document(resolved, "resolved")
        current = _inventory(record / "input", record)
        if (metadata["schema_version"] == 2 or request.get("engine") == "trim") and current != metadata["inputs"]:
            raise ValueError("Initialized version-two inputs were changed")
        documents = {item["path"]: item for item in current}
        for original in metadata["inputs"]:
            if (metadata["schema_version"] == 2 or original["path"] in ("input/request.json", "input/resolved.json")) and documents.get(original["path"]) != original:
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
    interpret outputs, or modify shared resources. After releasing the record
    lock, notify best-effort input indexing without changing returned metadata
    or allowing ordinary indexing failures to change the simulation outcome.
    """
    if failure is not None:
        _validate_document(failure, "failure")
    record = _simulation_path(paths, simulation_id)
    with _lock_parent(paths.simulation_memory, timeout_seconds=30.0):
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
        completed = _read_json(record / "metadata.json")
    try:
        from src.knowledge.index import notify_simulation_finished
        notify_simulation_finished(paths, completed)
    except Exception:
        try:
            import logging
            logging.getLogger(__name__).exception("Native simulation finalized; input indexing notification failed")
        except Exception:
            pass
    return completed



def begin_execution_evidence(paths: ProjectPaths, config: Config, simulation_id: str, worker_id: str) -> JSONObject:
    """Publish paths' created simulation launch intent for worker_id using config time.

    The caller holds the worker lease. Validate identity, runtime and current
    inputs; return a fresh execution UUID and exclusively synchronized evidence.
    Do not launch or start anything. Invalid state raises ValueError; unsafe,
    conflicting or failed filesystem operations raise OSError without overwrite.
    """
    record = _simulation_path(paths, simulation_id)
    metadata = read_simulation(paths, simulation_id)
    if metadata["status"] != "created" or metadata["engine"]["name"] not in ("sr", "trim"):
        raise ValueError("Execution evidence requires a created SR/TRIM simulation")
    if not isinstance(worker_id, str) or re.fullmatch(r"worker_[1-9][0-9]*", worker_id) is None:
        raise ValueError("Invalid execution worker")
    runtime = paths.workers / worker_id / metadata["engine"]["name"] / simulation_id
    _inventory(runtime, runtime)
    if _inventory(record / "input", record) != metadata["inputs"]:
        raise ValueError("Execution inputs differ from their initialized inventory")
    digest = hashlib.sha256(json.dumps(metadata["inputs"], sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
    document = {"schema_version": 1, "simulation_id": simulation_id, "engine": metadata["engine"]["name"], "execution_id": str(uuid4()), "worker_id": worker_id, "runtime_path": runtime.relative_to(paths.root).as_posix(), "input_inventory_sha256": digest, "recorded_at": _record_time(config, None).isoformat()}
    _validate_document(document, "execution-launch")
    directory = record / "lifecycle"
    if directory.is_symlink() or directory.is_junction():
        raise OSError(errno.ELOOP, "Lifecycle path contains a link", str(directory))
    directory.mkdir(exist_ok=True)
    _write_input_file(directory / "launch.json", (json.dumps(document, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8"))
    return document


def record_execution_stopped(paths: ProjectPaths, config: Config, simulation_id: str, execution_id: str, *, reason: str, exit_code: int | None) -> JSONObject:
    """Publish proven native shutdown for paths' running execution_id/simulation_id.

    The caller holds its worker lease and proves stopping before calling. Use
    config time, reason and exit_code; return durable proof or identical existing
    semantic evidence. Reject conflicting state, identity or contents with
    ValueError; unsafe or failed I/O raises OSError. Never infer native shutdown.
    """
    record = _simulation_path(paths, simulation_id)
    metadata = read_simulation(paths, simulation_id)
    launch_bytes = _read_regular_bytes(record / "lifecycle/launch.json")
    launch = _read_json(record / "lifecycle/launch.json")
    _validate_document(launch, "execution-launch")
    if metadata["status"] != "running" or launch["simulation_id"] != simulation_id or launch["execution_id"] != execution_id or launch["engine"] != metadata["engine"]["name"]:
        raise ValueError("Stopped evidence does not match the running execution")
    if _read_regular_bytes(record / "lifecycle/launch.json") != launch_bytes:
        raise ValueError("Execution launch changed during stop publication")
    document = {"schema_version": 1, "simulation_id": simulation_id, "execution_id": execution_id, "launch_sha256": hashlib.sha256(launch_bytes).hexdigest(), "recorded_at": _record_time(config, None).isoformat(), "reason": reason, "exit_code": exit_code}
    _validate_document(document, "execution-stopped")
    target = record / "lifecycle/stopped.json"
    if os.path.lexists(target):
        existing = _read_json(target)
        _validate_document(existing, "execution-stopped")
        if {key: value for key, value in existing.items() if key != "recorded_at"} != {key: value for key, value in document.items() if key != "recorded_at"}:
            raise ValueError("Conflicting stopped execution evidence")
        return existing
    _write_input_file(target, (json.dumps(document, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8"))
    return document


def read_execution_evidence(paths: ProjectPaths, simulation_id: str) -> JSONObject:
    """Read stable lifecycle evidence for paths/simulation_id without mutation.

    Return launch/stopped documents and explicit missing, malformed or integrity
    issues. Unsafe base paths/IDs raise ValueError/OSError; individual evidence
    errors become issues and cannot authorize recovery. Rehash current inputs
    and recheck metadata and evidence bytes to reject inconsistent snapshots.
    """
    record = _simulation_path(paths, simulation_id)
    result: JSONObject = {"schema_version": 1, "simulation_id": simulation_id, "launch": None, "stopped": None, "issues": []}
    snapshots: dict[Path, bytes] = {}
    try:
        snapshots[record / "metadata.json"] = _read_regular_bytes(record / "metadata.json")
        metadata = read_simulation(paths, simulation_id)
        for name, kind in (("launch", "execution-launch"), ("stopped", "execution-stopped")):
            target = record / "lifecycle" / (name + ".json")
            snapshots[target] = _read_regular_bytes(target)
            document = _read_json(target)
            _validate_document(document, kind)
            if document["simulation_id"] != simulation_id:
                raise ValueError("Execution evidence names another simulation")
            result[name] = document
        launch, stopped = result["launch"], result["stopped"]
        if launch["engine"] != metadata["engine"]["name"] or stopped["execution_id"] != launch["execution_id"] or stopped["launch_sha256"] != hashlib.sha256(snapshots[record / "lifecycle/launch.json"]).hexdigest():
            raise ValueError("Execution stop proof does not match launch ownership")
        digest = hashlib.sha256(json.dumps(metadata["inputs"], sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
        if launch["input_inventory_sha256"] != digest or _inventory(record / "input", record) != metadata["inputs"]:
            raise ValueError("Execution stop proof has changed inputs")
    except (ValueError, OSError, TypeError, KeyError) as error:
        result["issues"].append({"code": "missing" if isinstance(error, FileNotFoundError) else "integrity", "message": str(error) or type(error).__name__})
    for target, original in snapshots.items():
        try:
            if _read_regular_bytes(target) != original:
                raise ValueError("Execution evidence changed during read")
        except (ValueError, OSError) as error:
            result["issues"].append({"code": "stale", "message": str(error) or type(error).__name__})
    return result


def check_run_closeout(paths: ProjectPaths, simulation_ids: Sequence[str], *, job_states: Sequence[JSONObject] = ()) -> JSONObject:
    """Check every explicit simulation_id under paths against copied job_states.

    Return sorted schema-one rows/counts and complete only when all started runs
    are terminal and no record is invalid. Created preparations remain separate.
    Validate 1..1000 distinct IDs and job snapshots; invalid arguments/base paths
    raise ValueError/OSError. Per-record failures become visible issue rows.
    Never infer death from age, execute engines or modify simulation records.
    """
    _validate_paths(paths)
    if isinstance(simulation_ids, (str, bytes)) or not isinstance(simulation_ids, Sequence) or not 1 <= len(simulation_ids) <= 1000:
        raise ValueError("Closeout requires 1..1000 distinct simulation IDs")
    ids = list(simulation_ids)
    for identity in ids:
        _validate_delete_request(DeleteRequest("srim", identity))
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate closeout simulation ID")
    if isinstance(job_states, (str, bytes)) or not isinstance(job_states, Sequence):
        raise ValueError("Invalid closeout job snapshots")
    jobs = []
    for job in job_states:
        if not isinstance(job, dict) or not isinstance(job.get("id"), str) or re.fullmatch(r"job_[0-9a-f-]{36}", job["id"]) is None or str(UUID(job["id"][4:])) != job["id"][4:] or job.get("state") not in ("queued", "running", "cancelling", "succeeded", "failed", "cancelled", "interrupted") or not isinstance(job.get("simulation_ids"), list):
            raise ValueError("Invalid closeout job snapshot")
        for identity in job["simulation_ids"]:
            _validate_delete_request(DeleteRequest("srim", identity))
        jobs.append({"id": job["id"], "state": job["state"], "simulation_ids": list(job["simulation_ids"])})
    report: JSONObject = {"schema_version": 1, "complete": True, "rows": [], "counts": {key: 0 for key in ("terminal", "unstarted", "stale_stopped", "stale_unresolved", "running_unresolved", "invalid")}, "issues": []}
    for identity in sorted(ids):
        related = [job for job in jobs if identity in job["simulation_ids"]]
        row: JSONObject = {"simulation_id": identity, "metadata_status": None, "classification": "invalid", "worker_id": None, "runtime_path": None, "job_ids": sorted({job["id"] for job in related}), "job_states": sorted({job["state"] for job in related}), "stop_evidence": False, "recovery_action": "repair-record", "issues": []}
        try:
            record = _simulation_path(paths, identity)
            before = _read_regular_bytes(record / "metadata.json")
            metadata = read_simulation(paths, identity)
            row["metadata_status"] = metadata["status"]
            if metadata["status"] in ("succeeded", "failed"):
                row.update(classification="terminal", recovery_action="none")
            elif metadata["status"] == "created":
                row.update(classification="unstarted", recovery_action="none")
            else:
                evidence = read_execution_evidence(paths, identity)
                row["issues"].extend(evidence["issues"])
                if evidence["launch"] is not None:
                    row["worker_id"] = evidence["launch"]["worker_id"]
                    row["runtime_path"] = evidence["launch"]["runtime_path"]
                row["stop_evidence"] = evidence["stopped"] is not None and not evidence["issues"]
                row["classification"] = "stale-stopped" if row["stop_evidence"] else "stale-unresolved" if any(job["state"] in ("succeeded", "failed", "cancelled", "interrupted") for job in related) else "running-unresolved"
                row["recovery_action"] = "reconcile" if row["stop_evidence"] else "investigate-native-ownership"
            if _read_regular_bytes(record / "metadata.json") != before:
                raise ValueError("Simulation metadata changed during closeout")
        except (ValueError, OSError, TypeError, KeyError) as error:
            row.update(classification="invalid", stop_evidence=False, recovery_action="repair-record")
            row["issues"].append({"code": "record", "message": str(error) or type(error).__name__})
        report["rows"].append(row)
        report["counts"][row["classification"].replace("-", "_")] += 1
        report["issues"].extend({"simulation_id": identity, **issue} for issue in row["issues"])
    report["complete"] = report["counts"]["terminal"] + report["counts"]["unstarted"] == len(ids)
    _validate_document(report, "run-closeout")
    return report
