"""Describe scientific tool schemas, capabilities, qualifications and editable examples."""

from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path

from jsonschema import Draft202012Validator

from src.mcp.common.contracts import COMMON_PARAMETERS, RUN_DIAGNOSTICS_SCHEMA, HASH_SCHEMA, JOB_SCHEMA, LIMIT_SCHEMA, NATIVE_FILE_SCHEMA, NATIVE_SOURCE_SCHEMA, OFFSET_SCHEMA, REFERENCE_SCHEMA, RESPONSE_SCHEMA, UUID_SCHEMA, ServerContext, canonical_bytes
from src.srim.common.types import JSONObject
from src.srim.trim.inputs import MODES, PLOTS


def build_catalog(context: ServerContext) -> JSONObject:
    """Build independently copied scientific schemas, tools, capabilities and examples.

    Context supplies workspace/configured limits. Perform no native execution,
    publication, default import or signature scraping. Invalid static schemas raise.
    """
    text = {"type": "string", "minLength": 1}
    decimal = {"type": "string", "pattern": r"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$"}
    sr_decimal = {"type": "string", "pattern": r"^[0-9]+(?:\.[0-9]+)?$"}
    positive = {"type": "integer", "minimum": 1}
    boolean = {"type": "boolean"}
    null = {"type": "null"}
    worker = {"type": ["string", "null"], "pattern": r"^worker_[1-9][0-9]*$", "default": None}
    timeout = {"type": "number", "exclusiveMinimum": 0, "default": 60.0}
    cascade = {"enum": [None, "none", "statistics", "detailed"], "default": None}
    element = {"type": "object", "additionalProperties": False, "required": ["atomic_number", "stoichiometry"], "properties": {"atomic_number": positive, "stoichiometry": decimal}}
    bonds = {"type": "object", "propertyNames": {"type": "string", "pattern": r"^[A-Z][a-z]?(?:-|=|#)[A-Z][a-z]?$"}, "additionalProperties": decimal}
    material = {"oneOf": [
        {"type": "object", "additionalProperties": False, "required": ["source", "phase", "density_g_cm3", "elements"], "properties": {"source": {"const": "direct"}, "phase": {"enum": ["solid", "gas"]}, "density_g_cm3": decimal, "elements": {"type": "array", "minItems": 1, "items": element}, "bonds": bonds}},
        {"type": "object", "additionalProperties": False, "required": ["source", "phase", "record_index"], "properties": {"source": {"const": "compound"}, "phase": {"enum": ["solid", "gas"]}, "record_index": positive}},
    ]}
    correction = {"oneOf": [
        {"type": "object", "additionalProperties": False, "required": ["mode", "applied_factor"], "properties": {"mode": {"const": "none"}, "applied_factor": {"const": "1.0"}}},
        {"type": "object", "additionalProperties": False, "required": ["mode", "applied_factor", "source", "reason"], "properties": {"mode": {"const": "supplied"}, "applied_factor": decimal, "source": text, "reason": text}},
        {"type": "object", "additionalProperties": False, "required": ["mode"], "properties": {"mode": {"const": "derived"}}},
    ]}
    energy = {"oneOf": [
        {"type": "object", "additionalProperties": False, "required": ["mode", "values_keV"], "properties": {"mode": {"const": "list"}, "values_keV": {"type": "array", "minItems": 1, "maxItems": 1000, "items": sr_decimal}}},
        {"type": "object", "additionalProperties": False, "required": ["mode", "minimum_keV", "maximum_keV"], "properties": {"mode": {"const": "range"}, "minimum_keV": sr_decimal, "maximum_keV": sr_decimal}},
    ]}
    sr_projectile = {"type": "object", "additionalProperties": False, "required": ["atomic_number", "mass_amu"], "properties": {"atomic_number": {"type": "integer", "minimum": 1, "maximum": 92}, "mass_amu": sr_decimal}}
    sr_parameters = {"projectile": sr_projectile, "target": material, "correction": correction, "stopping_units": {"type": "integer", "minimum": 1, "maximum": 8}, "energy": energy}
    sr_request = {"type": "object", "additionalProperties": False, "required": ["schema_version", "parameters"], "properties": {"schema_version": {"const": 2}, "parameters": {"type": "object", "additionalProperties": False, "required": list(sr_parameters), "properties": sr_parameters}}}
    numeric_sr = deepcopy(sr_request)
    numeric_sr["properties"]["schema_version"] = {"enum": [1, 2]}
    numeric_sr["properties"]["parameters"]["properties"] = deepcopy(sr_parameters)
    numeric_sr["properties"]["parameters"]["properties"]["target"] = {"type": "object", "additionalProperties": False, "required": ["phase", "density_g_cm3", "elements"], "properties": {"phase": {"enum": ["solid", "gas"]}, "density_g_cm3": sr_decimal, "elements": {"type": "array", "minItems": 1, "maxItems": 100, "items": {"type": "object", "additionalProperties": False, "required": ["atomic_number", "name", "stoichiometry", "mass_amu"], "properties": {"atomic_number": {"type": "integer", "minimum": 1, "maximum": 92}, "name": {"type": "string", "minLength": 1, "maxLength": 13}, "stoichiometry": sr_decimal, "mass_amu": sr_decimal}}}}}
    numeric_sr["properties"]["parameters"]["properties"]["correction"] = {"oneOf": [correction["oneOf"][0], correction["oneOf"][1], {"type": "object", "additionalProperties": False, "required": ["mode", "applied_factor"], "properties": {"mode": {"const": "derived"}, "applied_factor": sr_decimal}}]}
    event = {"type": "object", "additionalProperties": False, "required": ["id", "atomic_number", "energy_eV", "position_angstrom", "direction_cosines"], "properties": {"id": text, "atomic_number": positive, "energy_eV": decimal, "position_angstrom": {"type": "array", "minItems": 3, "maxItems": 3, "items": decimal}, "direction_cosines": {"type": "array", "minItems": 3, "maxItems": 3, "items": decimal}}}
    component_properties = {"component_index": positive, "mass_amu": decimal, "displacement_eV": decimal, "lattice_binding_eV": decimal, "surface_binding_eV": decimal}
    layer = {"type": "object", "additionalProperties": False, "required": ["name", "thickness_angstrom", "material", "correction", "overrides"], "properties": {"name": text, "thickness_angstrom": decimal, "material": material, "correction": correction, "overrides": {"type": "object", "additionalProperties": False, "required": ["density_g_cm3", "components"], "properties": {"density_g_cm3": {"oneOf": [decimal, null]}, "components": {"type": "array", "items": {"type": "object", "additionalProperties": False, "minProperties": 2, "required": ["component_index"], "properties": component_properties}}}}}}
    outputs = {"type": "object", "additionalProperties": False, "required": ["ranges", "backscatter", "transmission", "sputtering", "collisions", "exyz_increment_eV"], "properties": {"ranges": boolean, "backscatter": boolean, "transmission": boolean, "sputtering": boolean, "collisions": {"enum": ["none", "ions", "ions-and-recoils"]}, "exyz_increment_eV": decimal}}
    plot = {"type": "object", "additionalProperties": False, "required": ["type", "minimum_depth_angstrom", "maximum_depth_angstrom"], "properties": {"type": {"enum": list(PLOTS)}, "minimum_depth_angstrom": decimal, "maximum_depth_angstrom": decimal}}
    trim_projectile = {"type": "object", "additionalProperties": False, "required": ["atomic_number", "mass_amu", "energy_keV", "angle_degrees"], "properties": {"atomic_number": positive, "mass_amu": {"oneOf": [decimal, null]}, "energy_keV": decimal, "angle_degrees": decimal}}
    trim_parameters = {"name": text, "mode": {"enum": list(MODES["2013"])}, "projectile": trim_projectile, "total_ions": positive, "random_seed": {"type": "integer"}, "autosave_ions": {"type": "integer"}, "outputs": outputs, "plot": plot, "layers": {"type": "array", "minItems": 1, "items": layer}, "events": {"oneOf": [null, {"type": "array", "minItems": 1, "items": event}]}}
    trim_request = {"type": "object", "additionalProperties": False, "required": ["schema_version", "engine", "parameters"], "properties": {"schema_version": {"const": 1}, "engine": {"const": "trim"}, "parameters": {"type": "object", "additionalProperties": False, "required": list(trim_parameters), "properties": trim_parameters}}}
    numeric_trim = deepcopy(trim_request)
    tp = numeric_trim["properties"]["parameters"]
    tp["properties"]["engine_family"] = {"enum": ["2008", "2013"]}
    tp["properties"]["native_fields"] = {"type": "object", "additionalProperties": False, "required": ["legacy_bragg", "reminders", "stopping_version"], "properties": {"legacy_bragg": decimal, "reminders": {"type": "integer"}, "stopping_version": {"type": "integer"}}}
    tp["properties"]["atoms"] = {"type": "array", "minItems": 1, "items": {"type": "object", "additionalProperties": False, "required": ["atomic_number", "mass_amu", "displacement_eV", "lattice_binding_eV", "surface_binding_eV"], "properties": {"atomic_number": positive, "mass_amu": decimal, "displacement_eV": decimal, "lattice_binding_eV": decimal, "surface_binding_eV": decimal}}}
    tp["properties"]["layers"] = {"type": "array", "minItems": 1, "items": {"type": "object", "additionalProperties": False, "required": ["name", "thickness_angstrom", "density_g_cm3", "phase", "correction_factor", "stoichiometry"], "properties": {"name": text, "thickness_angstrom": decimal, "density_g_cm3": decimal, "phase": {"enum": ["solid", "gas"]}, "correction_factor": decimal, "stoichiometry": {"type": "array", "minItems": 1, "items": decimal}}}}
    tp["required"] += ["engine_family", "native_fields", "atoms"]
    compound_element = {"oneOf": [element, {"type": "object", "additionalProperties": False, "required": ["atomic_number", "mass_proportion"], "properties": {"atomic_number": positive, "mass_proportion": decimal}}]}
    compound_record = {"type": "object", "additionalProperties": False, "required": ["name", "composition_basis", "density_g_cm3", "elements", "bonds"], "properties": {"name": text, "composition_basis": {"enum": ["atomic", "mass"]}, "density_g_cm3": decimal, "elements": {"type": "array", "items": compound_element}, "bonds": bonds}}
    compound_data = {"type": "object", "additionalProperties": False, "required": ["schema_version", "records"], "properties": {"schema_version": {"const": 1}, "records": {"type": "array", "items": compound_record}}}
    strength = {"type": "object", "additionalProperties": False, "required": ["schema_version", "kind", "units", "entries"], "properties": {"schema_version": {"const": 2}, "kind": {"enum": ["core", "bond"]}, "units": {"const": "eV/(1E15 atoms/cm2)"}, "entries": {"type": "array", "items": {"oneOf": [{"type": "object", "additionalProperties": False, "required": ["element", "value"], "properties": {"element": {"oneOf": [positive, text]}, "value": decimal}}, {"type": "object", "additionalProperties": False, "required": ["elements", "bond", "value"], "properties": {"elements": {"type": "array", "minItems": 2, "maxItems": 2, "items": {"oneOf": [positive, text]}}, "bond": {"enum": ["-", "=", "#"]}, "value": decimal}}]}}}}
    damage = {"type": "object", "additionalProperties": False, "required": ["schema_version", "units", "rows"], "properties": {"schema_version": {"const": 1}, "units": {"const": "eV"}, "rows": {"type": "array", "minItems": 92, "maxItems": 92, "items": {"type": "object", "additionalProperties": False, "required": ["atomic_number", "displacement_eV", "lattice_binding_eV"], "properties": {"atomic_number": positive, "displacement_eV": decimal, "lattice_binding_eV": decimal, "surface_binding_eV": decimal}}}}}
    edit_key = {"oneOf": [text, {"type": "integer"}, {"type": "array", "minItems": 1, "items": {"oneOf": [text, {"type": "integer"}]}}]}
    atom_edit = {"type": "object", "additionalProperties": False, "minProperties": 1, "properties": {key: positive if key == "isotope_mass_number" else text for key in ("name", "isotope_mass_number", "isotope_mass_amu", "natural_mass_amu", "solid_density_g_cm3", "solid_atomic_density_cm3", "fermi_velocity", "sublimation_energy_eV", "gas_density_g_cm3", "gas_atomic_density_cm3")}}
    edit_value = {"oneOf": [text, {"type": "integer"}, compound_record, atom_edit, {"type": "array", "items": decimal}]}
    override = {"oneOf": [{"type": "object", "additionalProperties": False, "required": ["operation", "key", "value"], "properties": {"operation": {"const": "set"}, "key": edit_key, "value": edit_value}}, {"type": "object", "additionalProperties": False, "required": ["operation", "key"], "properties": {"operation": {"const": "remove"}, "key": edit_key}}, {"type": "object", "additionalProperties": False, "required": ["operation", "value"], "properties": {"operation": {"const": "append"}, "value": compound_record}}]}
    selection = {"oneOf": []}
    source = {"oneOf": [{"type": "object", "additionalProperties": False, "required": ["kind", "path"], "properties": {"kind": {"const": "file"}, "path": text}}, {"type": "object", "additionalProperties": False, "required": ["kind", "data"], "properties": {"kind": {"const": "inline"}, "data": {"oneOf": [text, compound_data, strength, damage]}}}]}
    for property_name, property_schema in (("version", {"type": "string", "pattern": r"^(default|v[1-9][0-9]*)$"}), ("source", source)):
        selection["oneOf"].append({"type": "object", "additionalProperties": False, "required": [property_name, "overrides", "create_version"], "properties": {property_name: property_schema, "overrides": {"type": "array", "items": override}, "create_version": boolean}})
    sr_selections = {"type": "object", "additionalProperties": False, "required": ["atomdata", "compound", "electronic", "nuclear", "core", "bond"], "properties": {role: {"oneOf": [selection, null]} if role == "compound" else selection for role in ("atomdata", "compound", "electronic", "nuclear", "core", "bond")}}
    trim_selections = deepcopy(sr_selections)
    trim_selections["required"].append("damage")
    trim_selections["properties"]["damage"] = selection
    resource = {"type": "object", "additionalProperties": False, "required": ["category", "name", "version", "sha256", "size_bytes"], "properties": {"category": text, "name": text, "version": text, "sha256": HASH_SCHEMA, "size_bytes": {"type": "integer", "minimum": 0}}}
    prepared = {"type": "object", "additionalProperties": False, "required": ["engine", "simulation_id", "worker_id", "runtime_manifest_sha256"], "properties": {"engine": {"enum": ["sr", "trim"]}, "simulation_id": {"type": "string", "pattern": r"^srim_[0-9]{8}_[0-9]{6}_[1-9][0-9]*$"}, "worker_id": {"type": "string", "pattern": r"^worker_[1-9][0-9]*$"}, "runtime_manifest_sha256": HASH_SCHEMA}}
    bundle = {"type": "object", "additionalProperties": False, "required": ["id", "manifest_sha256"], "properties": {"id": {"type": "string", "pattern": r"^object_[0-9a-f-]{36}$"}, "manifest_sha256": HASH_SCHEMA}}
    directory = {"oneOf": [{"type": "object", "additionalProperties": False, "required": ["kind", "path"], "properties": {"kind": {"const": "local-directory"}, "path": text}}, {"type": "object", "additionalProperties": False, "required": ["kind", "reference"], "properties": {"kind": {"const": "artifact-directory"}, "reference": REFERENCE_SCHEMA}}]}
    quantity = {"oneOf": [null, text, {"type": "integer"}, {"type": "array", "items": {"oneOf": [null, text, {"type": "integer"}]}}]}
    clause = {"type": "object", "additionalProperties": False, "required": ["field", "operator", "value", "unit", "layer"], "properties": {"field": text, "operator": {"enum": ["eq", "in", "between", "unknown", "contains_all", "contains_any"]}, "value": {"oneOf": [quantity, {"type": "object", "additionalProperties": decimal}, {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["atomic_number", "mass_amu", "atomic_proportion"], "properties": {"atomic_number": positive, "mass_amu": {"oneOf": [decimal, null]}, "atomic_proportion": decimal}}}]}, "unit": {"type": ["string", "null"]}, "layer": {"oneOf": [null, positive, {"enum": ["any", "all"]}]}}}
    filters = {"type": "object", "additionalProperties": False, "required": ["schema_version", "clauses"], "properties": {"schema_version": {"const": 1}, "clauses": {"type": "array", "items": clause}}}
    compound_filters = {"type": "object", "additionalProperties": False, "properties": {"name": {"type": ["string", "null"]}, "name_mode": {"enum": ["exact", "contains"]}, "composition": {"oneOf": [null, {"type": "array", "items": {"oneOf": [positive, text]}}, {"type": "object", "additionalProperties": decimal}]}, "composition_mode": {"enum": ["contains", "elements", "ratio"]}}}
    variant_source = {"oneOf": [{"type": "object", "additionalProperties": False, "required": ["kind", "encoding", "content"], "properties": {"kind": {"const": "inline"}, "encoding": {"enum": ["utf8", "base64"]}, "content": text}}, {"type": "object", "additionalProperties": False, "required": ["kind", "path"], "properties": {"kind": {"const": "local-file"}, "path": text}}, {"type": "object", "additionalProperties": False, "required": ["kind", "value"], "properties": {"kind": {"const": "json"}, "value": {"oneOf": [compound_data, strength, damage]}}}]}
    replacements = {"type": "array", "minItems": 1, "items": {"type": "object", "additionalProperties": False, "required": ["key", "row"], "properties": {"key": {"oneOf": [positive, {"type": "array", "minItems": 2, "maxItems": 2, "items": positive}]}, "row": text}}}
    variant = {"oneOf": []}
    for mode in ("source", "base_version"):
        props = {"kind": {"enum": ["atomdata", "compound", "core", "bond", "electronic", "nuclear", "damage"]}, mode: variant_source if mode == "source" else text, "overrides": {"type": "array", "items": override, "default": []}, "replacements": replacements}
        variant["oneOf"].append({"type": "object", "additionalProperties": False, "required": ["kind", mode], "properties": props})
    params = deepcopy(COMMON_PARAMETERS)
    params.update({
        "environment_get": {}, "defaults_get": {"kind": {"enum": ["sr", "trim"]}}, "defaults_switch": {"kind": {"enum": ["sr", "trim"]}, "damage_version": {"type": "string", "default": "builtin"}},
        "resources_list": {"category": {"type": ["string", "null"], "default": None}, "name": {"type": ["string", "null"], "default": None}, "offset": OFFSET_SCHEMA, "limit": LIMIT_SCHEMA},
        "resource_read": {"reference": resource, "section": {"enum": ["manifest", "payload", "entries"], "default": "manifest"}, "offset": OFFSET_SCHEMA, "limit": LIMIT_SCHEMA},
        "compounds_search": {"compound_selection": selection, "atomdata_selection": selection, "filters": compound_filters, "offset": OFFSET_SCHEMA, "limit": LIMIT_SCHEMA},
        "variant_create": {"spec": variant}, "cab_populate": {"kind": {"enum": ["core", "bond"]}, "entries": {"type": "array", "minItems": 1, "items": {"oneOf": [positive, text, {"type": "array", "minItems": 3, "maxItems": 3, "items": {"oneOf": [positive, text]}}]}}, "worker_id": worker, "capture_timeout_seconds": {**timeout, "default": 30.0}, "dependency_timeout_seconds": timeout},
        "request_validate": {"engine": {"enum": ["sr", "trim"]}, "request": {"anyOf": [sr_request, numeric_sr, trim_request, numeric_trim]}, "explicit": {"type": "boolean", "default": False}},
        "input_render": {"engine": {"enum": ["sr", "trim"]}, "request": {"anyOf": [numeric_sr, numeric_trim]}},
        "sr_resolve": {"request": sr_request, "selections": sr_selections, "worker_id": worker, "capture_timeout_seconds": {**timeout, "default": 30.0}, "dependency_timeout_seconds": timeout},
        "sr_prepare": {"request": sr_request, "selections": sr_selections, "worker_id": worker, "capture_timeout_seconds": {**timeout, "default": 30.0}, "dependency_timeout_seconds": timeout},
        "trim_resolve": {"request": trim_request, "selections": trim_selections, "worker_id": worker, "capture_timeout_seconds": {**timeout, "default": 30.0}, "dependency_timeout_seconds": timeout},
        "trim_bundle_load": {"source": directory}, "trim_prepare": {"bundle": bundle, "worker_id": worker, "cascade_output": cascade},
        "trim_continue_prepare": {"parent_id": text, "total_ions": positive, "autosave_ions": {"type": "integer"}, "worker_id": worker, "events": {"oneOf": [null, {"type": "array", "items": event}], "default": None}, "cascade_output": cascade},
        "run_submit": {"prepared": prepared, "timeout_seconds": {"oneOf": [timeout, null], "default": 60.0}},
        "batch_submit": {"engine": {"enum": ["sr", "trim"]}, "tasks": {"type": "array", "minItems": 1, "items": prepared}, "max_workers": positive, "timeout_seconds": {"oneOf": [timeout, null], "default": 60.0}, "worker_timeouts": {"oneOf": [null, {"type": "object", "propertyNames": {"pattern": r"^worker_[1-9][0-9]*$"}, "additionalProperties": {"oneOf": [timeout, null]}}], "default": None}, "cleanup": {"type": ["boolean", "null"], "default": None}},
        "simulations_list": {"engine": {"enum": [None, "sr", "trim", "sr-capture"], "default": None}, "status": {"enum": [None, "created", "running", "succeeded", "failed"], "default": None}, "offset": OFFSET_SCHEMA, "limit": LIMIT_SCHEMA},
        "simulation_get": {"simulation_id": text}, "source_get": {"simulation_id": text}, "source_read": {"reference": NATIVE_SOURCE_SCHEMA, "file": NATIVE_FILE_SCHEMA, "offset": OFFSET_SCHEMA, "length": {"type": "integer", "minimum": 1, "default": 262144}},
        "result_read": {"simulation_id": text, "section": {"enum": ["summary", "checkpoint", "consistency", "issues", "saved-values", "config-integrity", "availability", "manifest", "diagnostics"], "default": "summary"}},
        "table_read": {"simulation_id": text, "table": text, "offset": OFFSET_SCHEMA, "limit": LIMIT_SCHEMA}, "trim_availability_get": {"simulation_id": text},
        "trim_parse": {"source": directory, "request": {"oneOf": [trim_request, numeric_trim, null], "default": None}}, "trim_recover": {"source": directory},
        "inputs_search": {"filters": {"oneOf": [filters, null], "default": None}, "offset": OFFSET_SCHEMA, "limit": LIMIT_SCHEMA, "expected_generation": {"type": ["string", "null"], "default": None}},
        "index_update": {"operation": {"enum": ["initialize", "update", "rebuild"]}, "simulation_ids": {"type": "array", "items": text}},
        "workers_cleanup": {"engine": {"enum": ["sr", "trim"]}, "worker_ids": {"type": "array", "items": {"type": "string", "pattern": r"^worker_[1-9][0-9]*$"}}},
    })
    mutations = {"defaults_switch", "variant_create", "cab_populate", "sr_resolve", "sr_prepare", "trim_resolve", "trim_bundle_load", "trim_prepare", "trim_continue_prepare", "run_submit", "batch_submit", "trim_parse", "trim_recover", "index_update", "workers_cleanup"}
    families = {"discovery": set(COMMON_PARAMETERS) | {"environment_get"}, "resources": {"defaults_get", "defaults_switch", "resources_list", "resource_read", "compounds_search", "variant_create", "cab_populate"}, "sr": {"sr_resolve", "sr_prepare"}, "trim": {"trim_resolve", "trim_bundle_load", "trim_prepare", "trim_continue_prepare"}, "execution": {"run_submit", "batch_submit"}, "results": {"simulations_list", "simulation_get", "source_get", "source_read", "result_read", "table_read", "trim_availability_get", "trim_parse", "trim_recover"}, "search": {"inputs_search", "index_update"}, "retention": {"workers_cleanup"}}
    descriptions = {"simulation_get": "Read run metadata and verified structured diagnostics; distinguish execution, parsing, monitoring and input findings, retaining unresolved causes.", "result_read": "Read saved result sections; diagnostics assembles sealed failure evidence without requiring parsed results or successful input replay. Review before excluding failed runs.", "request_validate": "Validate explicit or unresolved SR/TRIM syntax without resolving resources or running native dependencies.", "input_render": "Render complete numeric SR.IN or TRIM.IN/TRIM.DAT bytes without resource resolution or native execution.", "sr_resolve": "Resolve pinned SR inspection evidence; requested variants and missing CAB dependencies can execute/publish. Later SR preparation resolves again.", "sr_prepare": "Allocate and prepare SR; CAB dependencies may run. Main SR requires run_submit.", "trim_resolve": "Resolve layered/event TRIM and export a pinned bundle. Missing derived CAB dependencies may execute.", "trim_prepare": "Prepare a fresh TRIM record from a verified pinned bundle; no main execution.", "trim_continue_prepare": "Prepare a new child from a complete parent checkpoint; total_ions is absolute, physics inherited, autosave explicit.", "run_submit": "Submit an existing prepared calculation. Individual TRIM is cooperatively cancellable; SR has finite timeout-only cancellation.", "batch_submit": "Run explicit prepared tasks in input result order. Active batch cancellation unsupported; unlimited TRIM batch may delay graceful shutdown indefinitely.", "trim_recover": "Recover labeled cumulative profiles from complete frozen checkpoints; retain provenance/precision/inferred-layout warnings, never certify physics.", "compounds_search": "Search selected effective compounds without publication or CAB recovery; record indices belong to that database.", "defaults_switch": "Explicitly import/switch one default pointer; never automatic during server startup.", "table_read": "Read paged saved numerical rows with original strings/units, provenance and availability; no analysis or zero substitution."}
    tools = {}
    for name, properties in params.items():
        properties = deepcopy(properties)
        if name in mutations:
            properties["request_id"] = UUID_SCHEMA
        family = next((family for family, names in families.items() if name in names), "discovery")
        tools[name] = {"name": name, "family": family, "mutating": name in mutations, "description": descriptions.get(name, name.replace("_", " ").capitalize() + "; use capability/schema details for exact evidence and side-effect rules."), "inputSchema": {"type": "object", "additionalProperties": False, "required": [key for key, schema in properties.items() if "default" not in schema], "properties": properties}, "outputSchema": RESPONSE_SCHEMA}
    schemas = {"sr.request.v2": sr_request, "sr.engine-request.v2": numeric_sr, "sr.selections.v1": sr_selections, "trim.request.v1": trim_request, "trim.engine-request.v1": numeric_trim, "trim.selections.v1": trim_selections, "knowledge.filter.v1": filters, "mcp.run-diagnostics.v1": RUN_DIAGNOSTICS_SCHEMA, "mcp.response.v1": RESPONSE_SCHEMA, "mcp.reference.v1": REFERENCE_SCHEMA, "mcp.job.v1": JOB_SCHEMA, "native.source.v1": NATIVE_SOURCE_SCHEMA, "native.file.v1": NATIVE_FILE_SCHEMA, "trim.prepared.v1": prepared, "trim.bundle-reference.v1": bundle, "resource.reference.v1": resource}
    output_quantity = {"type": "object", "additionalProperties": False, "required": ["value", "unit"], "properties": {"value": text, "unit": text}}
    list_row = {"type": "object", "additionalProperties": False, "required": ["energy", "electronic_stopping", "nuclear_stopping", "requested_energy_keV"], "properties": {"energy": output_quantity, "electronic_stopping": text, "nuclear_stopping": text, "requested_energy_keV": sr_decimal}}
    range_row = {"type": "object", "additionalProperties": False, "required": ["energy", "electronic_stopping", "nuclear_stopping", "projected_range", "longitudinal_straggling", "lateral_straggling"], "properties": {"energy": output_quantity, "electronic_stopping": text, "nuclear_stopping": text, "projected_range": output_quantity, "longitudinal_straggling": output_quantity, "lateral_straggling": output_quantity}}
    schemas["sr.result.v1"] = {"type": "object", "additionalProperties": False, "required": ["schema_version", "energy_mode", "source", "stopping_units", "rows"], "properties": {"schema_version": {"const": 1}, "energy_mode": {"enum": ["list", "range"]}, "source": {"type": "object", "additionalProperties": False, "required": ["filename", "sha256", "size_bytes"], "properties": {"filename": {"const": "stopping.txt"}, "sha256": HASH_SCHEMA, "size_bytes": {"type": "integer", "minimum": 0}}}, "stopping_units": {"type": "object", "additionalProperties": False, "required": ["code", "label"], "properties": {"code": {"type": "integer", "minimum": 1, "maximum": 8}, "label": text}}, "rows": {"type": "array", "items": {"oneOf": [list_row, range_row]}}}}
    capabilities = {}
    for name, tool in tools.items():
        identity = "capability_" + name.replace("_", "-")
        capabilities[identity] = {"id": identity, "title": name.replace("_", " ").capitalize(), "family": tool["family"], "summary": tool["description"], "tool_names": [name], "implementation": "implemented", "exposure": "mcp", "input_schema_ids": ["tool." + name], "output_schema_ids": ["mcp.response.v1"], "parameters": [{"pointer": "/" + key, "description": key.replace("_", " "), "value_type": schema.get("type", "union"), "unit": "seconds" if "seconds" in key else None, "required": key in tool["inputSchema"]["required"], "default": schema.get("default"), "enum": schema.get("enum"), "minimum": schema.get("minimum", schema.get("exclusiveMinimum")), "maximum": schema.get("maximum"), "bounds_kind": "schema" if "minimum" in schema or "exclusiveMinimum" in schema or "maximum" in schema else "unknown", "reason": "Native cross-field validation remains authoritative; no speculative physics caps", "source": "doc/reference/mcp-format.md"} for key, schema in tool["inputSchema"]["properties"].items()], "units": {"energy": "keV in main requests; eV in event/damage fields", "thickness": "angstrom", "density": "g/cm3", "mass": "amu", "angle": "degrees", "stopping_codes": {str(index): label for index, label in enumerate(("eV/Angstrom", "keV/micron", "MeV/mm", "keV/(ug/cm2)", "MeV/(mg/cm2)", "keV/(mg/cm2)", "eV/(1E15 atoms/cm2)", "L.S.S. reduced units"), 1)}}, "defaults": {key: schema["default"] for key, schema in tool["inputSchema"]["properties"].items() if "default" in schema}, "effects": ["Durable asynchronous job; inspect native status independently"] if tool["mutating"] else ["Read/validate only; verification can create existing coordination locks"], "compatibility": {"families": ["2008", "2013 Standard", "2013 Pro"], "mode_mapping": deepcopy(MODES), "installed": "Inspect environment_get; no executable assumed from a version label"}, "automation": "reviewed-policy for explicitly selected TRIM response policy; otherwise report-only", "availability": "Saved result/profile availability is conditional and never inferred as zero", "evidence": [{"claim": "Core/recovery fixtures cover 23 documented version/mode combinations", "kind": "native-fixture", "reference": "doc/reference/validation.md#trim-native-checks", "scope": "Measured workloads only; not independent scientific validation or universal unattended mode coverage"}], "limitations": ["No automatic retries or permanent deletion", "SR range grid has no user-specified increment", "Exact 90-degree TRIM incidence rejected", "Unknown native bounds remain unknown", "Checkpoint recovery is labeled and does not establish atomic saving or native text precision"], "example_ids": [], "python_apis": ["doc/reference/api.md", "doc/reference/trim-execution-format.md", "doc/reference/input-search-format.md"]}
        schemas["tool." + name] = tool["inputSchema"]
    selections_example = {role: {"version": "default", "overrides": [], "create_version": False} for role in ("atomdata", "electronic", "nuclear", "core", "bond")}
    selections_example["compound"] = None
    sr_example = {"schema_version": 2, "parameters": {"projectile": {"atomic_number": 1, "mass_amu": "1.008"}, "target": {"source": "direct", "phase": "solid", "density_g_cm3": "2.267", "elements": [{"atomic_number": 6, "stoichiometry": "1"}]}, "correction": {"mode": "none", "applied_factor": "1.0"}, "stopping_units": 7, "energy": {"mode": "list", "values_keV": ["126", "1000", "126"]}}}
    trim_example = {"schema_version": 1, "engine": "trim", "parameters": {"name": "Ar into silicon", "mode": "ion-full", "projectile": {"atomic_number": 18, "mass_amu": "39.962", "energy_keV": "50", "angle_degrees": "0"}, "total_ions": 100, "random_seed": 12345, "autosave_ions": 100, "outputs": {"ranges": False, "backscatter": False, "transmission": False, "sputtering": False, "collisions": "none", "exyz_increment_eV": "0"}, "plot": {"type": "none", "minimum_depth_angstrom": "0", "maximum_depth_angstrom": "1000"}, "layers": [{"name": "Silicon", "thickness_angstrom": "1000", "material": {"source": "direct", "phase": "solid", "density_g_cm3": "2.3212", "elements": [{"atomic_number": 14, "stoichiometry": "1"}]}, "correction": {"mode": "none", "applied_factor": "1.0"}, "overrides": {"density_g_cm3": None, "components": []}}], "events": None}}
    examples = {"sr-list": {"tool": "request_validate", "arguments": {"engine": "sr", "request": sr_example, "explicit": False}}, "sr-range": {"tool": "request_validate", "arguments": {"engine": "sr", "request": deepcopy(sr_example), "explicit": False}}, "trim-layered": {"tool": "request_validate", "arguments": {"engine": "trim", "request": trim_example, "explicit": False}}}
    examples["sr-range"]["arguments"]["request"]["parameters"]["energy"] = {"mode": "range", "minimum_keV": "1", "maximum_keV": "1000"}
    for name, tool in tools.items():
        if name in mutations:
            continue
        if name not in {"request_validate", "input_render"}:
            continue
        capabilities["capability_" + name.replace("_", "-")]["example_ids"] = list(examples)
    examples["sr-prepare"] = {"tool": "sr_prepare", "arguments": {"request_id": "00000000-0000-0000-0000-000000000001", "request": sr_example, "selections": selections_example}}
    examples["trim-resolve"] = {"tool": "trim_resolve", "arguments": {"request_id": "00000000-0000-0000-0000-000000000002", "request": trim_example, "selections": {**selections_example, "damage": {"version": "default", "overrides": [], "create_version": False}}}}
    for identity, correction_value in (("sr-supplied-correction", {"mode": "supplied", "applied_factor": "1.05", "source": "user calibration", "reason": "Explicit caller-selected factor"}), ("sr-derived-correction", {"mode": "derived"})):
        request = deepcopy(sr_example)
        request["parameters"]["correction"] = correction_value
        if correction_value["mode"] == "derived":
            request["parameters"]["target"]["bonds"] = {"C-C": "1"}
        examples[identity] = {"tool": "request_validate", "arguments": {"engine": "sr", "request": request, "explicit": False}}
    request = deepcopy(sr_example)
    request["parameters"]["target"] = {"source": "compound", "record_index": 1, "phase": "solid"}
    examples["sr-compound"] = {"tool": "request_validate", "arguments": {"engine": "sr", "request": request, "explicit": False}}
    request = deepcopy(trim_example)
    request["parameters"]["mode"] = "specified-ion-full"
    request["parameters"]["total_ions"] = 1
    request["parameters"]["autosave_ions"] = 1
    request["parameters"]["events"] = [{"id": "T0001", "atomic_number": 18, "energy_eV": "50000", "position_angstrom": ["0", "0", "0"], "direction_cosines": ["1", "0", "0"]}]
    examples["trim-specified-events"] = {"tool": "request_validate", "arguments": {"engine": "trim", "request": request, "explicit": False}}
    request = deepcopy(trim_example)
    second_layer = deepcopy(request["parameters"]["layers"][0])
    second_layer.update(name="Carbon", thickness_angstrom="500")
    second_layer["material"] = deepcopy(sr_example["parameters"]["target"])
    request["parameters"]["layers"].append(second_layer)
    examples["trim-multilayer"] = {"tool": "request_validate", "arguments": {"engine": "trim", "request": request, "explicit": False}}
    request = deepcopy(trim_example)
    request["parameters"]["layers"][0]["overrides"]["components"] = [{"component_index": 1, "mass_amu": "29", "displacement_eV": "20", "lattice_binding_eV": "0", "surface_binding_eV": "6"}]
    examples["trim-component-overrides"] = {"tool": "request_validate", "arguments": {"engine": "trim", "request": request, "explicit": False}}
    prepared_example = {"engine": "trim", "simulation_id": "srim_20000101_000000_1", "worker_id": "worker_1", "runtime_manifest_sha256": "0" * 64}
    examples.update({
        "damage-variant": {"tool": "variant_create", "arguments": {"request_id": "00000000-0000-0000-0000-000000000004", "spec": {"kind": "damage", "base_version": "default", "overrides": [{"operation": "set", "key": [14, "surface_binding_eV"], "value": "6"}]}}},
        "trim-prepare": {"tool": "trim_prepare", "arguments": {"request_id": "00000000-0000-0000-0000-000000000005", "bundle": {"id": "object_00000000-0000-0000-0000-000000000001", "manifest_sha256": "0" * 64}, "cascade_output": "none"}},
        "trim-continuation": {"tool": "trim_continue_prepare", "arguments": {"request_id": "00000000-0000-0000-0000-000000000006", "parent_id": "srim_20000101_000000_1", "total_ions": 200, "autosave_ions": 200, "cascade_output": "none"}},
        "trim-run-unlimited": {"tool": "run_submit", "arguments": {"request_id": "00000000-0000-0000-0000-000000000007", "prepared": prepared_example, "timeout_seconds": None}},
        "prepared-batch": {"tool": "batch_submit", "arguments": {"request_id": "00000000-0000-0000-0000-000000000008", "engine": "trim", "tasks": [prepared_example], "max_workers": 1, "timeout_seconds": 60.0, "cleanup": False}},
        "checkpoint-recovery": {"tool": "trim_recover", "arguments": {"request_id": "00000000-0000-0000-0000-000000000009", "source": {"kind": "local-directory", "path": str(context.paths.root / ".cache" / "frozen-checkpoint")}}},
        "saved-profile-page": {"tool": "table_read", "arguments": {"simulation_id": "srim_20000101_000000_1", "table": "RANGE.txt", "offset": 0, "limit": 20}},
        "exact-energy-search": {"tool": "inputs_search", "arguments": {"filters": {"schema_version": 1, "clauses": [{"field": "energy", "operator": "between", "value": ["1", "1000"], "unit": "keV", "layer": None}]}, "offset": 0, "limit": 100}},
        "cancel-job": {"tool": "job_cancel", "arguments": {"job_id": "job_00000000-0000-0000-0000-000000000001"}},
    })
    for identity, example in examples.items():
        example.update(id=identity, purpose="Editable explicit request template; replace request UUID before a new mutation", prerequisites=["Inspect environment/default readiness", "Choose scientific parameters explicitly"], expected="Validated response or durable job reference", limitations=["Example values are serialization templates, not scientific recommendations"])
        Draft202012Validator(tools[example["tool"]]["inputSchema"]).validate(example["arguments"])
        card = capabilities["capability_" + example["tool"].replace("_", "-")]
        if identity not in card["example_ids"]:
            card["example_ids"].append(identity)
    conceptual = [
        ("sr-stopping-list", "SR electronic and nuclear stopping at listed energies", "sr", ["sr_prepare", "run_submit", "table_read"], ["sr.request.v2"], ["sr-list", "sr-prepare"], ["1–1,000 positive keV strings; requested ordering and duplicates preserved", "Eight explicit stopping-unit codes; no hidden unit conversion"]),
        ("sr-range", "SR engine-grid projected range and straggling", "sr", ["sr_prepare", "run_submit", "table_read"], ["sr.request.v2"], ["sr-range"], ["Minimum strictly below maximum", "Engine chooses grid; no step parameter, interpolation or exact-endpoint guarantee"]),
        ("materials-and-cab", "Direct/compound materials and explicit CAB correction", "resources", ["compounds_search", "variant_create", "cab_populate", "sr_prepare", "trim_resolve"], ["sr.selections.v1", "trim.selections.v1"], ["sr-compound", "sr-supplied-correction", "sr-derived-correction"], ["No chemistry/bond inference", "Derived CAB uses pinned hydrogen stopping/strength evidence and Decimal precision 28", "Missing default strengths may trigger serialized visible GUI capture", "18 native bond identities; H-core zero convention; user missing-entry fallback differs from default recovery"]),
        ("trim-layered-target", "TRIM multilayer, isotope and damage-energy controls", "trim", ["trim_resolve", "trim_prepare"], ["trim.request.v1"], ["trim-multilayer", "trim-component-overrides", "damage-variant"], ["Thickness angstrom; density g/cm3; mass amu; damage energies eV", "Keep repeated native target entries distinct across layers", "Density/mass overrides do not silently recalculate other physical properties"]),
        ("trim-profile-families", "TRIM depth/lateral profiles and conditional histories", "results", ["result_read", "table_read", "trim_availability_get", "trim_recover"], [], ["saved-profile-page", "checkpoint-recovery"], ["RANGE, VACANCY, IONIZ, PHONON, E2RECOIL, NOVAC, LATERAL recovered families", "Optional ranges/backscatter/transmission/sputtering/collisions/EXYZ requested separately", "Quick-mode replacement collisions can be not_calculated, never zero-filled", "2008 full-recoil detailed-history evidence differs from 2013; universal optional-history coverage is unestablished"]),
    ]
    for slug, title, family, tool_names, schema_ids, example_ids, limitations in conceptual:
        detail = deepcopy(capabilities["capability_" + tool_names[0].replace("_", "-")])
        detail.update(id="capability_" + slug, title=title, summary=title, family=family, tool_names=tool_names, input_schema_ids=schema_ids, example_ids=example_ids, limitations=limitations)
        capabilities[detail["id"]] = detail
    for mode in MODES["2013"]:
        detail = deepcopy(capabilities["capability_trim-layered-target"])
        mapping = {family: values.get(mode) for family, values in MODES.items()}
        example_id = "trim-mode-" + mode
        request = deepcopy(trim_example if mode.startswith("ion-") else examples["trim-specified-events"]["arguments"]["request"])
        request["parameters"]["mode"] = mode
        examples[example_id] = {"id": example_id, "tool": "request_validate", "arguments": {"engine": "trim", "request": request, "explicit": False}, "purpose": "Explicit " + mode + " request template", "prerequisites": ["Check installed engine/mode and automation policy compatibility"], "expected": "Schema validation, not proof of native execution or physics", "limitations": ["Replace event geometry/energies and physical parameters explicitly"]}
        Draft202012Validator(tools["request_validate"]["inputSchema"]).validate(examples[example_id]["arguments"])
        detail.update(id="capability_trim-mode-" + mode, title="TRIM " + mode, summary="Select " + mode + " using version-specific native codes and explicit inputs", compatibility={"mode_codes": mapping, "installed": "Inspect environment_get", "unavailable_reason": "2008 has no recoil-monolayer mode" if mapping["2008"] is None else None}, example_ids=[example_id], automation="Syntax/mode mapping is implemented; reviewed executable/dialog policy and actual output requirements remain independent", limitations=["A mode's accepted syntax does not establish universal unattended execution or physical validation", "Specified/recoil modes require explicit structured events; vectors and positions are not silently normalized or relocated"])
        capabilities[detail["id"]] = detail
    advanced = {
        "pure-data-and-normalization": ["parse_atomdata", "parse_compounds", "apply_material_overrides", "construct_sr_target", "normalize_atomdata", "normalize_compounds", "parse_trim_damage", "apply_trim_damage_overrides", "flatten_trim_target"],
        "pinned-resolution-verification": ["verify_sr_resolution", "verify_trim_resolution", "verify_sr_baseline", "read_module_version"],
        "bragg-dependencies": ["SRDependencyInputs", "resolve_bragg", "populate_bragg", "resolve_material_correction"],
        "calculated-entry-store": ["CABEntryReference", "CABEntryResult", "find_cab_entry", "load_cab_entry", "publish_cab_entry", "resolve_default_core", "resolve_default_bond"],
        "generic-resources": ["register_resource", "resolve_resource", "verify_resource"],
        "low-level-lifecycle": ["initialize_simulation", "start_simulation", "finish_simulation", "initialize_capture_simulation", "finish_capture_simulation"],
        "low-level-native-monitoring": ["execute_trim_process", "build_trim_dialog_policy", "classify_trim_dialog", "validate_trim_native_request"],
        "pure-parsers-and-recovery": ["parse_sr_output", "parse_trim_summary", "parse_trim_profile", "parse_trim_checkpoint", "compare_trim_saved_values", "parse_trim_outputs", "recover_trim_checkpoint", "parse_trim_checkpoint_results"],
        "preservation-and-directory-allocation": ["create_directories", "delete_directories", "preserve_sr_runtime", "verify_preserved_runtime", "preserve_trim_runtime", "verify_preserved_trim_runtime"],
        "search-helpers": ["convert_quantity", "matches_filters", "validate_search_document", "read_source_file"],
    }
    for slug, apis in advanced.items():
        detail = deepcopy(capabilities["capability_request-validate"])
        detail.update(id="capability_python-" + slug, title="Python APIs: " + slug.replace("-", " "), summary="Advanced Python interfaces; use orchestrated MCP tools for normal agent tasks", tool_names=[], exposure="python-only", input_schema_ids=[], output_schema_ids=[], parameters=[], defaults={}, example_ids=[], python_apis=apis, effects=["Effects depend on named API; see exact Python API reference"], limitations=["Direct low-level publication/lifecycle/process APIs are not exposed as bypasses around verified orchestration", "Generic deletion is not reference-aware and is not an MCP tool", "Typed signatures and full effects are documented in doc/reference/api.md and TRIM references"])
        capabilities[detail["id"]] = detail
    for schema in schemas.values():
        Draft202012Validator.check_schema(schema)
    result = {"server": context.name, "tools": tools, "schemas": schemas, "capabilities": capabilities, "examples": examples, "limits": deepcopy(context.limits), "instructions": "Discover schemas before selecting scientific inputs; preparation can run CAB dependencies. Inspect native status, provenance and availability. Review simulation_get diagnostics before excluding failed runs; distinguish recorded findings from unresolved causes. Continuation uses verified checkpoint recovery. Organize studies through project_mcp."}
    result["generation"] = hashlib.sha256(canonical_bytes(result)).hexdigest()
    return deepcopy(result)
