"""Recognize and normalize published SNUMAT v1 TRIM input and summary evidence."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
import re

from src.backend.resources import _unique_object
from src.knowledge.external_schema import ESTIMATE_UNITS, MANIFEST_FIELDS, PLANNED_UNITS, SUMMARY_UNITS, validate_external_document
from src.knowledge.schema import _bounded_decimal
from src.srim.common.types import JSONObject
from src.srim.trim.outputs import parse_trim_summary
from src.srim.cab.strengths import _SYMBOLS

NUMBER = r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?"
SELECTED_MEMBERS = ("case.json", "TRIM.IN", "TDATA.txt", "validation.json", "run_metadata.json", "RANGE.txt")


class SnumatTRIMAdapter:
    """Provide stateless code-owned snumat-trim-v1 recognition, version 1.

    Construction takes no inputs. Public adapter_id and adapter_version identify
    the reusable scientific schema rather than a registered source instance.
    """

    adapter_id: str = "snumat-trim-v1"
    adapter_version: int = 1

    def probe(self: SnumatTRIMAdapter, profile: JSONObject) -> JSONObject:
        """Return recognition decision/evidence for bounded profile without I/O.

        profile contains sampled bytes as decoded text plus source locators.
        Match requires same-case JSON identity/manifest and supported TDATA
        grammar. Missing scientific samples are insufficient_evidence; malformed
        claimed scientific samples are no_match. No caller state is changed.
        """
        cases, summaries, evidence = {}, {}, []
        malformed = False
        for sample in profile.get("samples", []):
            reference = sample.get("reference", {})
            member = reference.get("member_path") or reference.get("relative_path", "")
            if not isinstance(member, str) or not isinstance(sample.get("content"), str) or sample.get("encoding") != "utf-8":
                continue
            parent, _, name = member.rpartition("/")
            try:
                if name == "case.json":
                    case = json.loads(sample["content"], object_pairs_hook=_unique_object, parse_float=str, parse_int=str)
                    if not isinstance(case, dict) or not isinstance(case.get("manifest"), dict) or not {"case_id", "target_id", "target_formula", "selected_energy_keV", "requested_depth_nm"} <= set(case["manifest"]):
                        malformed = True
                        continue
                    if case.get("case_id") != parent.rsplit("/", 1)[-1] or case["manifest"].get("case_id") != case["case_id"]:
                        malformed = True
                        continue
                    if _bounded_decimal(case["manifest"]["selected_energy_keV"]) <= 0 or _bounded_decimal(case["manifest"]["requested_depth_nm"]) <= 0:
                        malformed = True
                        continue
                    atomic_number = int(case["manifest"]["ion_z"])
                    if not 1 <= atomic_number <= len(_SYMBOLS) or _SYMBOLS[atomic_number - 1] != case["manifest"]["ion_symbol"]:
                        malformed = True
                        continue
                    cases[parent] = case
                elif name == "TDATA.txt":
                    summary = parse_trim_summary(sample["content"].encode("utf-8"))
                    if summary["version"] == "SRIM-2013.00":
                        summaries[parent] = summary
            except (ValueError, TypeError, KeyError):
                malformed = True
        for parent in sorted(cases.keys() & summaries.keys()):
            case, summary = cases[parent], summaries[parent]
            if case["manifest"].get("ion_symbol") == summary["projectile"]["symbol"] and str(case["manifest"].get("ion_z")) == str(summary["projectile"]["atomic_number"]):
                evidence.extend([parent + "/case.json: explicit manifest keys and identity", parent + "/TDATA.txt: SRIM-2013.00 summary grammar"])
        return {"adapter_id": self.adapter_id, "adapter_version": self.adapter_version, "result": "match" if evidence else "no_match" if malformed else "insufficient_evidence", "evidence": evidence[:32]}


def parse_snumat_input(contents: bytes) -> JSONObject:
    """Parse labeled single-layer SRIM-2013.00 TRIM.IN bytes without I/O.

    contents must contain the complete supported mode-1 input. Return input
    fields with original decimal tokens and internal _fields line spans for
    normalization. No values are inferred from filenames. Raise ValueError on
    unsupported labels/mode/version, cardinality, unknown trailing sections or
    invalid numbers. No caller data changes.
    """
    if not isinstance(contents, bytes) or len(contents) > 1048576:
        raise ValueError("TRIM.IN: invalid bytes or size")
    try:
        lines = [(i + 1, line.strip()) for i, line in enumerate(contents.decode("ascii").splitlines()) if line.strip()]
    except UnicodeError as error:
        raise ValueError("TRIM.IN: expected ASCII") from error
    expected = ("==> SRIM-2013.00 This file controls TRIM Calculations.", "Ion: Z1", "Cascades(", "Diskfiles (", "Target material", "PlotType", "Target Elements:")
    cursor, blocks = 0, {}
    for prefix in expected:
        if cursor >= len(lines) or not lines[cursor][1].startswith(prefix):
            raise ValueError("TRIM.IN: missing/ambiguous labeled section " + prefix)
        header = lines[cursor]
        cursor += 1
        if cursor >= len(lines):
            raise ValueError("TRIM.IN: truncated section")
        if prefix.startswith("==>"):
            continue
        if prefix == "Target Elements:":
            blocks[prefix] = (header[0], [])
            break
        blocks[prefix] = lines[cursor]
        cursor += 1
    ion = blocks["Ion: Z1"][1].split()
    cascade = blocks["Cascades("][1].split()
    disks = blocks["Diskfiles ("][1].split()
    target = re.fullmatch(r'"([^"]+)"\s+([0-9]+)\s+([0-9]+)', blocks["Target material"][1])
    if len(ion) != 7 or len(cascade) != 3 or len(disks) != 6 or target is None:
        raise ValueError("TRIM.IN: section cardinality invalid")
    for token in ion + cascade + disks + blocks["PlotType"][1].split():
        _bounded_decimal(token)
    integer_tokens = [ion[0], ion[4], ion[6], *cascade, *disks]
    if any(_bounded_decimal(token) != _bounded_decimal(token).to_integral_value() for token in integer_tokens):
        raise ValueError("TRIM.IN: integer field not integral")
    plot = blocks["PlotType"][1].split()
    if len(plot) != 3 or _bounded_decimal(plot[0]) != _bounded_decimal(plot[0]).to_integral_value() or not 0 <= int(plot[0]) <= 5 or _bounded_decimal(plot[1]) < 0 or _bounded_decimal(plot[2]) < _bounded_decimal(plot[1]) or int(ion[6]) < 0 or int(cascade[2]) not in (0, 1):
        raise ValueError("TRIM.IN: invalid plot/autosave/reminder settings")
    count, layer_count = int(target[2]), int(target[3])
    if layer_count != 1 or not 1 <= count <= 92 or int(cascade[0]) != 1:
        raise ValueError("TRIM.IN: unsupported layers/elements/calculation mode")
    title = target[1]
    projectile = re.fullmatch(r"([A-Z][a-z]?) into (.+)", title)
    if projectile is None:
        raise ValueError("TRIM.IN: target label does not identify projectile explicitly")
    fields = {"/inputs/" + key: [blocks["Ion: Z1"][0]] * 2 for key in ("ion_z", "ion_symbol", "ion_mass_amu", "energy_keV", "incidence_angle_deg", "number_of_ions", "compound_correction")}
    fields["/inputs/random_seed"] = [blocks["Cascades("][0]] * 2
    fields["/inputs/calculation_mode"] = [blocks["Cascades("][0]] * 2
    fields["/inputs/target_label"] = [blocks["Target material"][0]] * 2
    fields["/inputs/ion_symbol"] = [blocks["Target material"][0]] * 2
    raw_quantities = {"ion_mass_amu": (ion[1], "amu"), "energy_keV": (ion[2], "keV"), "incidence_angle_deg": (ion[3], "deg"), "compound_correction": (ion[5], "1")}
    result = {"engine": "trim", "engine_version": "SRIM-2013.00", "ion_symbol": projectile[1], "ion_z": int(ion[0]), "number_of_ions": int(ion[4]), "random_seed": int(cascade[1]), "calculation_mode": int(cascade[0]), "target_label": title}
    for key, (token, unit) in raw_quantities.items():
        if _bounded_decimal(token) < 0 or key in ("ion_mass_amu", "energy_keV", "compound_correction") and _bounded_decimal(token) == 0:
            raise ValueError("TRIM.IN: invalid positive quantity " + key)
        result[key] = {"value": token, "unit": unit, "raw": token, "availability": "available", "locator": None}
    if not 1 <= result["ion_z"] <= len(_SYMBOLS) or _SYMBOLS[result["ion_z"] - 1] != result["ion_symbol"] or result["number_of_ions"] < 1 or result["random_seed"] < 0 or not 0 <= _bounded_decimal(ion[3]) <= 90 or any(int(token) not in (0, 1) for token in disks[:4]) or int(disks[4]) not in (0, 1, 2) or int(disks[5]) not in (0, 1):
        raise ValueError("TRIM.IN: out-of-range identity/settings")
    components = []
    element_start = lines[cursor][0] if cursor < len(lines) else 0
    for ordinal in range(1, count + 1):
        if cursor >= len(lines):
            raise ValueError("TRIM.IN: truncated elements")
        line_no, line = lines[cursor]
        cursor += 1
        match = re.fullmatch(r"Atom\s+([0-9]+)\s*=\s*([A-Z][a-z]?)\s*=\s*([0-9]+)\s+(" + NUMBER + ")", line)
        if match is None or int(match[1]) != ordinal or not 1 <= int(match[3]) <= 92 or _SYMBOLS[int(match[3]) - 1] != match[2] or any(item["atomic_number"] == int(match[3]) for item in components) or _bounded_decimal(match[4]) <= 0:
            raise ValueError("TRIM.IN: invalid/duplicate target element")
        components.append({"symbol": match[2], "atomic_number": int(match[3]), "mass_amu": {"value": match[4], "unit": "amu", "raw": match[4], "availability": "available", "locator": None}})
        fields["/inputs/layers/0/composition/" + str(ordinal - 1) + "/mass_amu"] = [line_no] * 2
    fields["/inputs/layers/0/composition"] = [element_start, lines[cursor - 1][0]]
    if cursor + 2 >= len(lines) or not lines[cursor][1].startswith("Layer ") or not lines[cursor + 1][1].startswith("Numb."):
        raise ValueError("TRIM.IN: missing labeled layer table")
    header = lines[cursor][1]
    if any(symbol not in header for symbol in (item["symbol"] + "(" + str(item["atomic_number"]) + ")" for item in components)):
        raise ValueError("TRIM.IN: layer header does not match elements")
    cursor += 2
    layer_no, layer_text = lines[cursor]
    cursor += 1
    layer = re.fullmatch(r'1\s+"([^"]+)"\s+(.+)', layer_text)
    if layer is None:
        raise ValueError("TRIM.IN: invalid layer data")
    tokens = layer[2].split()
    if len(tokens) != count + 2 or any(_bounded_decimal(token) <= 0 for token in tokens):
        raise ValueError("TRIM.IN: invalid layer thickness/density/stoichiometry")
    for component, token in zip(components, tokens[2:], strict=True):
        component["stoichiometry"] = {"value": token, "unit": "1", "raw": token, "availability": "available", "locator": None}
    result["layers"] = [{"index": 1, "label": layer[1], "thickness_A": {"value": tokens[0], "unit": "A", "raw": tokens[0], "availability": "available", "locator": None}, "density_g_cm3": {"value": tokens[1], "unit": "g/cm3", "raw": tokens[1], "availability": "available", "locator": None}, "composition": components, "phase": 0}]
    fields["/inputs/layers/0/thickness_A"] = [layer_no] * 2
    fields["/inputs/layers/0/density_g_cm3"] = [layer_no] * 2
    for i in range(count):
        fields["/inputs/layers/0/composition/" + str(i) + "/stoichiometry"] = [layer_no] * 2
    sections = (("0  Target layer phases", "phase", "1"), ("Target Compound Corrections (Bragg)", "target_correction", "1"), ("Individual target atom displacement energies (eV)", "displacement_eV", "eV"), ("Individual target atom lattice binding energies (eV)", "lattice_binding_eV", "eV"), ("Individual target atom surface binding energies (eV)", "surface_binding_eV", "eV"), ("Stopping Power Version", "stopping", "1"))
    for prefix, key, unit in sections:
        if cursor + 1 >= len(lines) or not lines[cursor][1].startswith(prefix):
            raise ValueError("TRIM.IN: missing labeled " + key)
        line_no, text = lines[cursor + 1]
        cursor += 2
        values = text.split()
        if len(values) != (count if key.endswith("_eV") else 1) or any(_bounded_decimal(token) < 0 for token in values):
            raise ValueError("TRIM.IN: invalid " + key)
        if key == "phase":
            if values[0] not in ("0", "1"):
                raise ValueError("TRIM.IN: unsupported phase")
            result["layers"][0]["phase"] = int(values[0])
            fields["/inputs/layers/0/phase"] = [line_no] * 2
        elif key == "target_correction":
            if _bounded_decimal(values[0]) != _bounded_decimal(ion[5]):
                raise ValueError("TRIM.IN: conflicting compound corrections")
        elif key == "stopping":
            if values[0] not in ("0", "1"):
                raise ValueError("TRIM.IN: unsupported stopping version")
        else:
            result[key] = [{"value": value, "unit": unit, "raw": value, "availability": "available", "locator": None} for value in values]
            for index in range(count):
                fields["/inputs/" + key + "/" + str(index)] = [line_no] * 2
    if cursor != len(lines):
        raise ValueError("TRIM.IN: unexpected trailing sections")
    result["_fields"] = fields
    return result


def normalize_snumat_case(source_id: str, case_id: str, members: Mapping[str, bytes], locators: Mapping[str, JSONObject]) -> JSONObject:
    """Normalize exact case members and source locators into an ExternalRecord.

    source_id/case_id identify the source instance and literal publisher case.
    members holds selected complete case bytes and optional metadata-join rows;
    locators pins their original archive members. Return decimal-preserving input,
    planned/estimated quantities, six summaries, provenance and diagnostics.
    Perform no I/O. Missing inputs, duplicate JSON keys, schema mismatch or failed
    hash claims raise ValueError. Missing/unparsable summaries remain explicit.
    Literal nan SR estimates retain raw evidence with unavailable numeric values.
    """
    if "case.json" not in members or "TRIM.IN" not in members:
        raise ValueError("missing: case.json/TRIM.IN required")
    documents = {}
    for name in ("case.json", "validation.json", "run_metadata.json", "__manifest__.json", "__mapping__.json"):
        if name in members:
            value = json.loads(members[name].decode("utf-8"), object_pairs_hook=_unique_object, parse_float=str, parse_int=str)
            if not isinstance(value, dict):
                raise ValueError("malformed: expected JSON object " + name)
            documents[name] = value
    case = documents["case.json"]
    manifest, mapping = case.get("manifest"), case.get("case_mapping")
    if not isinstance(manifest, dict) or set(manifest) != set(MANIFEST_FIELDS) or not isinstance(mapping, dict) or case.get("case_id") != case_id or manifest.get("case_id") != case_id or mapping.get("case_id") != case_id:
        raise ValueError("malformed: unsupported manifest or conflicting case identity")
    if manifest.get("target_id") != case_id.split("_")[0] or mapping.get("target_id") != manifest["target_id"]:
        raise ValueError("conflicting: target/case identity")
    issues, checks = [], []
    for name, actual in (("__manifest__.json", manifest), ("__mapping__.json", mapping)):
        if name in documents and documents[name] != actual:
            issues.append({"code": "conflicting", "field": name, "message": "Published metadata join differs from case-local row", "locators": [deepcopy(locators[name])]})
        elif name in documents:
            checks.append(name + " matches case-local row")
    hashes = case.get("provenance", {}).get("sha256", {})
    if not isinstance(hashes, dict):
        raise ValueError("malformed: per-member checksum claims")
    for name, contents in members.items():
        if name.startswith("__"):
            continue
        if name not in locators:
            raise ValueError("missing: evidence locator")
        locator = deepcopy(locators[name])
        validate_external_document(locator, "locator")
        digest = hashlib.sha256(contents).hexdigest()
        if locator["source_id"] != source_id or locator["member_sha256"] != digest or locator["size_bytes"] != len(contents) or locator["member_path"].rsplit("/", 2)[-2] != case_id:
            raise ValueError("integrity: member locator mismatch " + name)
        if name in hashes and hashes[name] != digest:
            raise ValueError("integrity: publisher member hash mismatch " + name)
        checks.append(name + " bytes match selected-member hash")
    input_digest = hashlib.sha256(members["TRIM.IN"]).hexdigest()
    claims = {key: mapping[key] for key in ("sha256_delivered", "sha256_as_run") if key in mapping}
    metadata = documents.get("run_metadata.json", {})
    if "trim_in_sha256" in metadata:
        claims["run_metadata.trim_in_sha256"] = metadata["trim_in_sha256"]
    if any(value != input_digest for value in claims.values()):
        raise ValueError("integrity: delivered/as-run input claims disagree")
    inputs = parse_snumat_input(members["TRIM.IN"])
    input_spans = inputs.pop("_fields")
    input_fields = {}
    pending = [(inputs, "/inputs")]
    while pending:
        value, pointer = pending.pop()
        if isinstance(value, dict) and set(value) == {"value", "unit", "raw", "availability", "locator"}:
            span = input_spans.get(pointer)
            if span is None:
                raise ValueError("missing: input field evidence " + pointer)
            value["locator"] = {**deepcopy(locators["TRIM.IN"]), "json_pointer": None, "line_start": span[0], "line_end": span[1]}
            input_fields[pointer] = deepcopy(value["locator"])
        elif isinstance(value, dict):
            pending.extend((v, pointer + "/" + key) for key, v in value.items())
        elif isinstance(value, list):
            pending.extend((v, pointer + "/" + str(i)) for i, v in enumerate(value))
    for pointer, span in input_spans.items():
        input_fields[pointer] = {**deepcopy(locators["TRIM.IN"]), "json_pointer": None, "line_start": span[0], "line_end": span[1]}
    if inputs["ion_symbol"] != manifest["ion_symbol"] or inputs["ion_z"] != int(manifest["ion_z"]) or manifest["target_formula"] != inputs["layers"][0]["label"]:
        raise ValueError("conflicting: manifest/input identity differs")
    record = {"schema_version": 1, "kind": "external-run", "source_id": source_id, "adapter_id": "snumat-trim-v1", "adapter_version": 1, "case_id": case_id, "target_id": manifest["target_id"], "inputs": inputs, "planned": {}, "estimates": {}, "outputs": {"state": "unavailable", "summary": {}, "unavailable_outputs": []}, "provenance": {"manifest_status": str(manifest["status"]), "mapping_status": str(mapping.get("status", "")), "validation": documents.get("validation.json", {}), "run_metadata": metadata, "case_mapping": mapping, "input_hash_claims": claims, "input_fields": input_fields, "selected_members": {key: deepcopy(value) for key, value in locators.items() if not key.startswith("__")}, "independent_checks": checks, "parser_diagnostics": []}, "issues": issues}
    for key in MANIFEST_FIELDS:
        value = manifest[key]
        if not isinstance(value, str):
            raise ValueError("malformed: manifest quantities/categories must preserve string tokens")
        if key in ESTIMATE_UNITS or key in PLANNED_UNITS:
            unit = ESTIMATE_UNITS.get(key, PLANNED_UNITS.get(key))
            unavailable_estimate = key in ESTIMATE_UNITS and value == "nan"
            if value and not unavailable_estimate:
                _bounded_decimal(value)
            available = bool(value) and not unavailable_estimate
            quantity = {"value": value if available else None, "unit": unit, "raw": value if value else None, "availability": "available" if available else "unavailable", "locator": {**deepcopy(locators["case.json"]), "json_pointer": "/manifest/" + key, "line_start": None, "line_end": None}}
            if unavailable_estimate:
                issues.append({"code": "missing", "field": "estimates." + key, "message": "Publisher SR estimate is nan; numeric value unavailable; original token preserved", "locators": [deepcopy(quantity["locator"])]})
            record["estimates" if key in ESTIMATE_UNITS else "planned"][key] = quantity
        else:
            record["planned"][key] = value
    for key, planned in (("energy_keV", "selected_energy_keV"), ("ion_mass_amu", "ion_mass_u")):
        if _bounded_decimal(inputs[key]["value"]) != _bounded_decimal(manifest[planned]):
            issues.append({"code": "precision_difference", "field": "inputs." + key, "message": "Manifest and printed TRIM.IN tokens differ; preserved separately", "locators": [inputs[key]["locator"], record["planned"][planned]["locator"]]})
    comparisons = (("incidence_angle_deg", inputs["incidence_angle_deg"], "incidence_angle_deg"), ("density_g_cm3", inputs["layers"][0]["density_g_cm3"], "density_g_cm3"), ("target_thickness_angstrom", inputs["layers"][0]["thickness_A"], "target_thickness_angstrom"), ("compound_correction", inputs["compound_correction"], "compound_correction"))
    for field, actual, planned_key in comparisons:
        if _bounded_decimal(actual["value"]) != _bounded_decimal(manifest[planned_key]):
            issues.append({"code": "precision_difference", "field": "inputs." + field, "message": "Planned and printed input values differ; representations remain separate", "locators": [actual["locator"], record["planned"][planned_key]["locator"]]})
    if _bounded_decimal(manifest["planned_number_of_ions"]) != inputs["number_of_ions"]:
        issues.append({"code": "conflicting", "field": "inputs.number_of_ions", "message": "Manifest planned count differs from TRIM.IN requested count", "locators": [input_fields["/inputs/number_of_ions"], record["planned"]["planned_number_of_ions"]["locator"]]})

    parsed = None
    if "TDATA.txt" in members:
        try:
            parsed = parse_trim_summary(members["TDATA.txt"])
            if parsed["version"] != "SRIM-2013.00" or parsed["projectile"]["atomic_number"] != inputs["ion_z"] or parsed["projectile"]["symbol"] != inputs["ion_symbol"]:
                issues.append({"code": "conflicting", "field": "outputs.identity", "message": "TDATA identity differs from input", "locators": [deepcopy(locators["TDATA.txt"])]})
                parsed = None
            elif parsed["counts"]["calculated"] != inputs["number_of_ions"]:
                issues.append({"code": "conflicting", "field": "outputs.calculated_ions", "message": "Calculated ion count differs from planned input count", "locators": [deepcopy(locators["TDATA.txt"])]})
            if parsed is not None and parsed["counts"]["transmitted"] + parsed["counts"]["backscattered"] > parsed["counts"]["calculated"]:
                issues.append({"code": "conflicting", "field": "outputs.counts", "message": "Backscattered plus transmitted counts exceed calculated count", "locators": [deepcopy(locators["TDATA.txt"])]})
            if parsed is not None:
                for output_key, input_key in (("energy_keV", "energy_keV"), ("mass_amu", "ion_mass_amu"), ("angle_degrees", "incidence_angle_deg")):
                    if _bounded_decimal(parsed["projectile"][output_key]) != _bounded_decimal(inputs[input_key]["value"]):
                        issues.append({"code": "precision_difference", "field": "outputs.projectile." + output_key, "message": "TDATA printed projectile value differs from TRIM.IN; source tokens retained", "locators": [deepcopy(locators["TDATA.txt"]), inputs[input_key]["locator"]]})
        except ValueError as error:
            record["provenance"]["parser_diagnostics"].append(str(error))
            issues.append({"code": "malformed", "field": "TDATA.txt", "message": str(error), "locators": [deepcopy(locators["TDATA.txt"])]})
    else:
        issues.append({"code": "missing", "field": "TDATA.txt", "message": "Published summary unavailable", "locators": []})
    summary_keys = {"calculated_ions": ("counts", "calculated", "Total Ions calculated"), "transmitted_ions": ("counts", "transmitted", "Total Transmitted Ions"), "backscattered_ions": ("counts", "backscattered", "Total Backscattered Ions"), "mean_range": ("statistics", "mean_range_angstrom", "Average Range"), "straggling": ("statistics", "straggling_angstrom", "Average Straggling"), "vacancies_per_ion": ("statistics", "vacancies_per_ion", "Average Vacancy/Ion")}
    for key, unit in SUMMARY_UNITS.items():
        group, source_key, label = summary_keys[key]
        value = None if parsed is None else parsed[group].get(source_key)
        value = None if value is None else str(value)
        locator, raw = None, None
        if value is not None:
            matches = [(i + 1, re.search(re.escape(label) + r"\s*=\s*(" + NUMBER + ")", line)) for i, line in enumerate(members["TDATA.txt"].decode("ascii").splitlines()) if re.search(re.escape(label) + r"\s*=", line)]
            if len(matches) != 1 or matches[0][1] is None:
                raise ValueError("malformed: missing unique summary line")
            raw = matches[0][1][1]
            locator = {**deepcopy(locators["TDATA.txt"]), "json_pointer": None, "line_start": matches[0][0], "line_end": matches[0][0]}
        record["outputs"]["summary"][key] = {"value": value, "unit": unit, "raw": raw, "availability": "available" if value is not None else "unavailable", "locator": locator}
    record["outputs"]["state"] = "rejected" if parsed is None and "TDATA.txt" in members else "unavailable" if parsed is None else "available" if all(v["value"] is not None for v in record["outputs"]["summary"].values()) else "partial"
    for name in ("IONIZ.txt", "VACANCY.txt", "PHONON.txt", "E2RECOIL.txt", "LATERAL.txt", "NOVAC.txt"):
        record["outputs"]["unavailable_outputs"].append({"name": name, "state": "not_published", "reason": "Not retained in this published dataset; historical file-presence claims are not current availability"})
    for name in ("RANGE.txt", "RANGE_3D.txt"):
        record["outputs"]["unavailable_outputs"].append({"name": name, "state": "not_indexed", "reason": "Input and summary scope excludes profiles and per-ion records"})
    validation = record["provenance"]["validation"]
    if validation.get("validation_passed") is True and (validation.get("pysrim_run_success") is False or metadata.get("pysrim_run_success") is False):
        issues.append({"code": "publisher_status_conflict", "field": "provenance", "message": "Publisher validation_passed=true coexists with pysrim_run_success=false; native success unresolved", "locators": [deepcopy(locators[name]) for name in ("validation.json", "run_metadata.json") if name in locators]})
    if "RANGE.txt" in members and parsed is not None:
        match = re.search(r"Ion Average Range\s*=\s*(" + NUMBER + r")\s+A", members["RANGE.txt"].decode("ascii"))
        if match is not None and parsed["statistics"]["mean_range_angstrom"] is not None and _bounded_decimal(match[1]) != _bounded_decimal(parsed["statistics"]["mean_range_angstrom"]):
            issues.append({"code": "precision_difference", "field": "outputs.mean_range", "message": "RANGE.txt prints a different precision; TDATA summary is used", "locators": [deepcopy(locators["RANGE.txt"]), record["outputs"]["summary"]["mean_range"]["locator"]]})
    validate_external_document(record, "record")
    return record
