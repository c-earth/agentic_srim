"""Project pinned native and revisioned project evidence into unified retrieval documents."""

from __future__ import annotations

from copy import deepcopy
import json

from src.backend.paths import ProjectPaths
from src.backend.resources import _unique_object
from src.backend.project_records import read_record
from src.knowledge.retrieval_schema import PROJECTOR_VERSION, SECTIONS, WEIGHTS, canonical_document_key, validate_retrieval_document
from src.knowledge.schema import convert_quantity
from src.knowledge.source import project_simulation_inputs, read_source_file, read_source_record
from src.srim.common.files import _read_json
from src.srim.common.types import JSONObject

MAX_RESULT_BYTES = 67108864
MAX_EVIDENCE_BYTES = 268435456


def project_output_values(paths: ProjectPaths, source: JSONObject) -> JSONObject:
    """Read supported saved parsed outputs and construct exact searchable values.

    Args:
        paths: Validated project layout.
        source: Existing verified native SearchRecord with pinned file references.
    Returns:
        OutputProjection with explicit availability and output-only rejection issues.
    Raises:
        ValueError: Invalid supplied primary source reference.
    """
    read_source_record(paths, source["reference"])
    result = {"state": "missing", "result_source": None, "complete": None, "result_file": None, "source_files": [], "summary": [], "tables": [], "warnings": [], "issues": [], "projection_version": PROJECTOR_VERSION}
    engine = source["inputs"]["engine"]
    parsed_path = "output/parsed/result.json" if engine == "sr" else "output/parsed/results.json"
    file = next((file for file in source["files"] if file["path"] == parsed_path), None)
    if file is None:
        return result
    result["result_file"] = file
    try:
        if file["size_bytes"] > MAX_RESULT_BYTES:
            raise ValueError("too_large: parsed output exceeds 64 MiB")
        payload = read_source_file(paths, source["reference"], file, max_bytes=MAX_RESULT_BYTES)
        document = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object)
        json.dumps(document, allow_nan=False)
        if not isinstance(document, dict):
            raise ValueError("Parsed output must be an object")
        result["result_source"] = document.get("result_source", "native-text")
        recovered = result["result_source"] == "checkpoint-recovery"
        scientific = document.get("recovery") if recovered else document
        if not isinstance(scientific, dict):
            raise ValueError("Checkpoint recovery result is unavailable")
        result["complete"] = document.get("complete", True if engine == "sr" else None)
        if result["complete"] is not None and type(result["complete"]) is not bool:
            raise ValueError("Invalid saved completeness")
        dependencies = []
        if engine == "sr":
            if set(document) != {"schema_version", "energy_mode", "source", "stopping_units", "rows"} or type(document["schema_version"]) is not int or document["schema_version"] != 1 or document["energy_mode"] not in ("list", "range") or document["energy_mode"] != source["inputs"]["energy"]["kind"]:
                raise ValueError("Unsupported SR result schema")
            dependencies = [document["source"]]
        elif recovered:
            if set(document) != {"schema_version", "engine", "result_source", "native", "recovery", "issues", "complete"} or type(document["schema_version"]) is not int or document["schema_version"] != 2 or document["engine"] != "trim" or scientific.get("schema_version") != 2 or scientific.get("provenance", {}).get("recovered") is not True:
                raise ValueError("Unsupported recovered TRIM schema")
            dependencies = scientific["provenance"]["sources"]
            result["warnings"] = deepcopy(scientific["warnings"])
            result["issues"].extend(deepcopy(document["issues"]))
        else:
            if set(document) != {"schema_version", "engine", "summary", "profiles", "checkpoint", "sources", "issues", "consistency", "complete"} or type(document["schema_version"]) is not int or document["schema_version"] != 1 or document["engine"] != "trim":
                raise ValueError("Unsupported native TRIM result schema")
            dependencies = ([scientific["summary"]["source"]] if scientific["summary"] is not None else []) + [profile["source"] for profile in scientific["profiles"].values()]
            result["issues"].extend(deepcopy(scientific["issues"]))
        size = file["size_bytes"]
        for dependency in dependencies:
            name = dependency.get("filename", dependency.get("path"))
            if not isinstance(name, str):
                raise ValueError("Invalid output dependency name")
            candidates = [item for item in source["files"] if item["role"] == "output" and (item["path"] == "output/raw/" + name or recovered and item["path"] == "output/raw/SRIM Restore/" + name)]
            if not candidates:
                candidates = [item for item in source["files"] if item["role"] == "output" and item["path"].endswith("/" + name)]
            candidates = [item for item in candidates if item["sha256"] == dependency["sha256"] and item["size_bytes"] == dependency["size_bytes"]]
            if len(candidates) != 1:
                raise ValueError("Missing or ambiguous inventoried output dependency: " + name)
            selected = candidates[0]
            if selected not in result["source_files"]:
                size += selected["size_bytes"]
                if size > MAX_EVIDENCE_BYTES:
                    raise ValueError("too_large: output evidence exceeds 256 MiB")
                read_source_file(paths, source["reference"], selected, max_bytes=MAX_EVIDENCE_BYTES)
                result["source_files"].append(selected)
        locator = {"reference": source["reference"], "file": file, "json_pointer": ""}
        prefix = "/recovery" if recovered else ""
        if engine == "sr":
            from src.srim.sr.outputs import STOPPING_LABELS
            units = document["stopping_units"]
            if set(units) != {"code", "label"} or type(units["code"]) is not int or not 1 <= units["code"] <= len(STOPPING_LABELS) or units["label"] != STOPPING_LABELS[units["code"] - 1] or not isinstance(document["rows"], list) or not document["rows"]:
                raise ValueError("Invalid SR stopping units or rows")
            columns = [{"name": "energy", "unit": "keV"}, {"name": "requested_energy", "unit": "keV"}, {"name": "electronic_stopping", "unit": units["label"]}, {"name": "nuclear_stopping", "unit": units["label"]}, *[{"name": name, "unit": "A"} for name in ("projected_range", "longitudinal_straggling", "lateral_straggling")]]
            rows = []
            original = []
            for row in document["rows"]:
                expected = {"energy", "electronic_stopping", "nuclear_stopping", "requested_energy_keV"} if document["energy_mode"] == "list" else {"energy", "electronic_stopping", "nuclear_stopping", "projected_range", "longitudinal_straggling", "lateral_straggling"}
                if set(row) != expected:
                    raise ValueError("Invalid SR row fields")
                values = [convert_quantity(row["energy"]["value"], row["energy"]["unit"], "keV"), row.get("requested_energy_keV"), row["electronic_stopping"], row["nuclear_stopping"]]
                for key in ("projected_range", "longitudinal_straggling", "lateral_straggling"):
                    values.append(None if key not in row else convert_quantity(row[key]["value"], row[key]["unit"], "A"))
                rows.append(values)
                original.append(deepcopy(row))
            result["tables"].append({"name": "sr.rows", "columns": columns, "rows": rows, "availability": "available", "calculated_ions": None, "precision": {"kind": "printed", "original_rows": original}, "notes": [], "locator": {**locator, "json_pointer": "/rows"}})
        else:
            if recovered:
                counts = scientific["checkpoint"]["counts"]
                summaries = [("calculated_ions", str(counts["current"]), "ions", "/checkpoint/counts/current"), ("transmitted_ions", str(counts["transmitted"]), "ions", "/checkpoint/counts/transmitted"), ("backscattered_ions", str(counts["backscattered"]), "ions", "/checkpoint/counts/backscattered")]
            else:
                summary = scientific["summary"]
                summaries = []
                if summary is not None:
                    for name, key in (("calculated_ions", "calculated"), ("transmitted_ions", "transmitted"), ("backscattered_ions", "backscattered")):
                        summaries.append((name, str(summary["counts"][key]), "ions", "/summary/counts/" + key))
                    for name, key, unit in (("mean_range", "mean_range_angstrom", "A"), ("straggling", "straggling_angstrom", "A"), ("vacancies_per_ion", "vacancies_per_ion", "vacancies/ion")):
                        summaries.append((name, summary["statistics"].get(key), unit, "/summary/statistics/" + key))
            for name, value, unit, pointer in summaries:
                result["summary"].append({"name": name, "value": value, "unit": unit, "availability": "available" if value is not None else "unavailable", "locator": {**locator, "json_pointer": prefix + pointer}})
            for name, profile in scientific["profiles"].items():
                if name not in ("RANGE.txt", "VACANCY.txt", "IONIZ.txt", "PHONON.txt", "E2RECOIL.txt", "NOVAC.txt", "LATERAL.txt"):
                    raise ValueError("Unsupported profile family")
                if recovered:
                    if len(profile["columns"]) != len(profile["units"]):
                        raise ValueError("Recovered column/unit mismatch")
                    columns = [{"index": i, "label": deepcopy(label), "unit": "A" if unit.lower() == "angstrom" else unit} for i, (label, unit) in enumerate(zip(profile["columns"], profile["units"], strict=True))]
                    available = profile["availability"]
                    notes = [profile["reason"]] if profile["reason"] else []
                    precision = {"kind": "checkpoint-recovery", "provenance": scientific["provenance"], "grid": scientific["grid"], "descriptor": scientific["checkpoint"]["layout"]["profiles"].get(name), "stored_target_components": profile.get("stored_target_components"), "normalization": "cumulative through checkpoint current count"}
                    calculated = str(scientific["checkpoint"]["counts"]["current"])
                else:
                    columns = deepcopy(profile["columns"])
                    for column in columns:
                        if column["unit"] in ("Angstrom", "angstrom"):
                            column["unit"] = "A"
                    available = "not_calculated" if name == "NOVAC.txt" and not profile["rows"] and not columns and profile["calculated_ions"] is None else "available"
                    notes, precision, calculated = deepcopy(profile["notes"]), {"kind": "printed", "statistics": deepcopy(profile["statistics"])}, profile["calculated_ions"]
                    if available == "available" and len(profile["rows"]) != 100:
                        raise ValueError("Invalid native profile row count")
                if available == "not_calculated" and profile["rows"]:
                    raise ValueError("Not-calculated table carries rows")
                result["tables"].append({"name": name, "columns": columns, "rows": deepcopy(profile["rows"]), "availability": available, "calculated_ions": calculated, "precision": precision, "notes": notes, "locator": {**locator, "json_pointer": prefix + "/profiles/" + name.replace("~", "~0").replace("/", "~1") + "/rows"}})
        result["state"] = "available" if result["complete"] is True and not result["issues"] else "partial"
        validate_retrieval_document(result, "projection")
        for selected in [file, *result["source_files"]]:
            read_source_file(paths, source["reference"], selected, max_bytes=MAX_EVIDENCE_BYTES)
        read_source_record(paths, source["reference"])
    except (ValueError, OSError, KeyError, TypeError, IndexError, UnicodeError) as error:
        result.update(state="rejected", summary=[], tables=[])
        if result["result_source"] not in ("native-text", "checkpoint-recovery"):
            result["result_source"] = None
        if type(result["complete"]) is not bool:
            result["complete"] = None
        if not isinstance(result["warnings"], list):
            result["warnings"] = []
        result["issues"].append({"code": "output_rejected", "message": str(error), "file": file})
    return result


def project_retrieval_document(paths: ProjectPaths, kind: str, reference: JSONObject) -> JSONObject:
    """Project one exact source without modifying source or dependent records.

    Args:
        paths: Absolute project layout.
        kind: run, case or analysis.
        reference: Pinned native source or project revision reference.
    Returns:
        Independent RetrievalDocument with locators and explicit dependency links.
    Raises:
        ValueError: Primary source identity/schema/integrity is invalid.
        OSError: Primary evidence is unsafe, absent or unreadable.
    """
    key = canonical_document_key(kind, reference)
    sections, links = [], []
    outputs = None
    if kind == "run":
        source = read_source_record(paths, reference)
        metadata = _read_json(paths.simulation_memory / reference["id"] / "metadata.json")
        title = source["inputs"]["engine"].upper() + " " + reference["id"]
        status, created, updated = source["execution_status"], metadata.get("created_at"), metadata.get("finished_at")
        attributes = {"inputs": deepcopy(source["inputs"])}
        outputs = project_output_values(paths, source)
        notes = []
        for table in outputs["tables"]:
            notes.extend(str(column.get("label", column.get("name", ""))) for column in table["columns"])
            notes.extend(str(note) for note in table["notes"])
        notes.extend(json.dumps(note, ensure_ascii=False) for note in outputs["warnings"])
        if notes and outputs["result_file"] is not None:
            sections.append({"name": "output_notes", "text": "\n".join(notes), "locator": {"reference": reference, "file": outputs["result_file"], "json_pointer": ""}, "weight": 1})
    else:
        stored = read_record(paths, kind, reference["id"], revision=reference["revision"])
        if stored["reference"] != reference:
            raise ValueError("Project reference hash differs from saved revision")
        record = stored["record"]
        title, status, created, updated = record["title"], record["status"], record["created_at"], record["updated_at"]
        attributes = {"tags": deepcopy(record["tags"])} if kind == "case" else {"eligibility": deepcopy(record["eligibility"]), "method_reference": deepcopy(record["method"]["reference"])}
        fields = [("title", record["title"], "/title"), ("question", record["question"], "/question")]
        if kind == "case":
            fields += [("tags", "\n".join(record["tags"]), "/tags"), ("notes", record["notes"], "/notes")]
            for index, member in enumerate(record["members"]):
                loc = {"reference": reference, "file": None, "json_pointer": "/members/" + str(index)}
                links.append({"relation": "case_member", "reference": deepcopy(member["reference"]), "locator": loc, "role": member["role"], "note": member["note"]})
                fields.append(("member_notes", member["note"], "/members/" + str(index) + "/note"))
        else:
            fields += [("method", record["method"]["description"], "/method/description"), ("findings", record["findings"], "/findings"), ("limitations", record["limitations"], "/limitations")]
            for index, selected in enumerate(record["inputs"]):
                links.append({"relation": "analysis_input", "reference": deepcopy(selected["source"]), "locator": {"reference": reference, "file": None, "json_pointer": "/inputs/" + str(index)}, "role": None, "note": selected["purpose"]})
            for index, artifact in enumerate(record["artifacts"]):
                links.append({"relation": "analysis_artifact", "reference": deepcopy(artifact), "locator": {"reference": reference, "file": None, "json_pointer": "/artifacts/" + str(index)}, "role": artifact["role"], "note": None})
        for name, text, pointer in fields:
            if text:
                sections.append({"name": name, "text": text, "locator": {"reference": reference, "file": None, "json_pointer": pointer}, "weight": WEIGHTS[SECTIONS.index(name)]})
    result = {"schema_version": 1, "document_key": key, "kind": kind, "id": reference["id"], "reference": deepcopy(reference), "status": status, "title": title, "created_at": created, "updated_at": updated, "sections": sections, "attributes": attributes, "links": links, "outputs": outputs, "issues": [], "projector_version": PROJECTOR_VERSION}
    validate_retrieval_document(result, "document")
    return result


def read_retrieval_document(paths: ProjectPaths, kind: str, reference: JSONObject, *, dependencies: bool = False) -> JSONObject:
    """Read a pinned document and optionally check all declared source/artifact dependencies.

    Args:
        paths: Project layout.
        kind: Unified record kind.
        reference: Exact immutable evidence reference.
        dependencies: Whether all declared dependencies need verification.
    Returns:
        document and separate record/dependency verification.
    Raises:
        ValueError: Invalid arguments or primary source integrity.
        OSError: Unavailable/unsafe primary source.
    """
    if type(dependencies) is not bool:
        raise ValueError("dependencies must be bool")
    document = project_retrieval_document(paths, kind, reference)
    verification = {"record": "verified", "dependencies": "not_checked", "issues": []}
    if dependencies:
        if kind == "run":
            source = read_source_record(paths, reference)
            for file in source["files"]:
                try:
                    read_source_file(paths, reference, file, max_bytes=MAX_EVIDENCE_BYTES)
                except (ValueError, OSError) as error:
                    verification["issues"].append({"field": file["path"], "message": str(error)})
            verification["issues"].extend(document["outputs"]["issues"])
        else:
            if kind == "case":
                from src.case.records import read_case
                checked = read_case(paths, reference["id"], revision=reference["revision"])
            else:
                from src.analysis.records import read_analysis
                checked = read_analysis(paths, reference["id"], revision=reference["revision"])
            verification["issues"].extend(checked.get("issues", []))
            for check in checked.get("checks", []):
                if check.get("state", check.get("status")) not in (None, "verified", "valid", "available"):
                    verification["issues"].append(check)
        verification["dependencies"] = "issues" if verification["issues"] else "verified"
    return {"document": document, "verification": verification}
