"""Validate and publish individually immutable calculated CAB entries and their evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, DecimalException, localcontext
from fractions import Fraction
import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
from tempfile import mkdtemp

from src.backend.paths import ProjectPaths
from src.backend.records import _lock_parent
from src.backend.resources import ResourceReference, _hash_file, _read_json, _validate_reference, verify_resource
from src.srim.cab_constants import CAB_PRECISION, CAB_ROUNDING, CAB_UNITS, SR_BONDS
from src.srim.metadata import JSONObject


@dataclass(frozen=True)
class CABEntryReference:
    """Identify immutable entry bytes through dataset, dependency_key, entry_id, sha256 and size_bytes.

    Construction stores the concrete ResourceReference and entry identifiers/hash
    without validation or I/O. Storage APIs validate these public attributes.
    """

    dataset: ResourceReference
    dependency_key: str
    entry_id: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class CABEntryResult:
    """Store reference and calculated inputs as the public result of one resolution call.

    reference identifies immutable bytes; calculated reports whether this invocation
    performed calculation. Construction performs no validation or I/O and adds no
    calculation state to persistent entry identity.
    """

    reference: CABEntryReference
    calculated: bool


def _dependency_key(dependency: JSONObject) -> str:
    """Return SHA-256 of dependency's validated canonical recipe bytes without I/O.

    Require exact procedure, file identities and lower-level entry references;
    reject paths, aliases, duplicates and wrong types with ValueError.
    """
    if not isinstance(dependency, dict) or set(dependency) != {"schema_version", "kind", "procedure", "files", "entries"} or type(dependency["schema_version"]) is not int or dependency["schema_version"] != 1 or dependency["kind"] not in ("bragg", "core", "bond"):
        raise ValueError("Invalid CAB dependency recipe")
    procedure = dependency["procedure"]
    if not isinstance(procedure, dict) or set(procedure) != {"method", "version", "precision", "rounding", "units", "reference"} or not isinstance(procedure["method"], str) or not procedure["method"] or type(procedure["version"]) is not int or procedure["version"] < 1 or type(procedure["precision"]) is not int or procedure["precision"] != CAB_PRECISION or procedure["rounding"] != CAB_ROUNDING or procedure["units"] != CAB_UNITS:
        raise ValueError("Invalid CAB procedure")
    if procedure["reference"] != {"projectile": 1, "energy_per_amu_keV": "125", "phase": "gas", "stopping_units": 7, "projectile_mass_field": "isotope_mass_amu", "target_mass_field": "natural_mass_amu"} or type(procedure["reference"].get("projectile")) is not int or type(procedure["reference"].get("stopping_units")) is not int:
        raise ValueError("Invalid fixed CAB reference conditions")
    if not isinstance(dependency["files"], list) or not isinstance(dependency["entries"], list):
        raise ValueError("Dependency files and entries must be lists")
    roles: set[str] = set()
    for item in dependency["files"]:
        if not isinstance(item, dict) or set(item) != {"role", "sha256", "size_bytes"} or not isinstance(item["role"], str) or not item["role"] or item["role"] in roles:
            raise ValueError("Invalid or duplicate dependency file identity")
        roles.add(item["role"])
        _validate_reference(ResourceReference("file", "file", "v1", item["sha256"], item["size_bytes"]))
    identities: set[tuple[str, str, str, str]] = set()
    for item in dependency["entries"]:
        if not isinstance(item, dict) or set(item) != {"dataset", "dependency_key", "entry_id", "sha256", "size_bytes"} or not isinstance(item["dataset"], dict) or set(item["dataset"]) != {"category", "name", "version", "sha256", "size_bytes"}:
            raise ValueError("Invalid dependency entry reference")
        dataset = ResourceReference(**item["dataset"])
        _validate_reference(dataset)
        if dataset.category != "cab" or re.fullmatch(r"v[1-9][0-9]*", dataset.version) is None or not isinstance(item["dependency_key"], str) or re.fullmatch(r"[0-9a-f]{64}", item["dependency_key"]) is None:
            raise ValueError("Invalid dependency dataset or key")
        allowed = {"bragg": (), "bond": ("bragg",), "core": ("bragg", "bond")}[dependency["kind"]]
        if dataset.name not in allowed:
            raise ValueError("Invalid calculated dependency graph")
        pattern = r"z(?:00[1-9]|0[1-8][0-9]|09[0-2])" if dataset.name == "bragg" else r"z[0-9]{3}-z[0-9]{3}-[sdt]"
        if not isinstance(item["entry_id"], str) or re.fullmatch(pattern, item["entry_id"]) is None:
            raise ValueError("Invalid dependency entry ID")
        _validate_reference(ResourceReference("entry", "entry", "v1", item["sha256"], item["size_bytes"]))
        identity = (dataset.name, dataset.version, item["dependency_key"], item["entry_id"])
        if identity in identities:
            raise ValueError("Duplicate dependency entry")
        identities.add(identity)
    return hashlib.sha256((json.dumps(dependency, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")).hexdigest()


def _entry_path(paths: ProjectPaths, dataset: ResourceReference, dependency_key: str, entry_id: str) -> Path:
    """Return dataset's safe entry path in paths after checking dependency_key and entry_id.

    Verify descriptor bytes, kind, fixed recipe and baseline identity. Reject links,
    traversal, aliases and invalid descriptors with ValueError/OSError; write no data.
    """
    _validate_reference(dataset)
    if dataset.category != "cab" or dataset.name not in ("core", "bond", "bragg") or re.fullmatch(r"v[1-9][0-9]*", dataset.version) is None:
        raise ValueError("Calculated entries require a concrete CAB dataset")
    if not isinstance(dependency_key, str) or re.fullmatch(r"[0-9a-f]{64}", dependency_key) is None:
        raise ValueError("Invalid dependency key")
    pattern = r"z(?:00[1-9]|0[1-8][0-9]|09[0-2])" if dataset.name != "bond" else r"z[0-9]{3}-z[0-9]{3}-[sdt]"
    if not isinstance(entry_id, str) or re.fullmatch(pattern, entry_id) is None:
        raise ValueError("Invalid calculated entry ID")
    if dataset.name == "bond":
        native_ids = {f"z{left:03d}-z{right:03d}-" + {"-": "s", "=": "d", "#": "t"}[order] for left, right, order in SR_BONDS}
        if entry_id not in native_ids:
            raise ValueError("Default bond entries require a supported SR bond")
    payload = verify_resource(paths, dataset)
    descriptor = _read_json(payload)
    if set(descriptor) != {"schema_version", "kind", "origin", "baseline_id", "procedure", "units", "base", "dependency"} or type(descriptor["schema_version"]) is not int or descriptor["schema_version"] != 1 or descriptor["kind"] != dataset.name or descriptor["units"] != CAB_UNITS or descriptor["base"] is not None:
        raise ValueError("Invalid calculated descriptor")
    recipe_key = _dependency_key(descriptor["dependency"])
    if descriptor["procedure"] != descriptor["dependency"]["procedure"] or descriptor["dependency"]["kind"] != dataset.name:
        raise ValueError("Descriptor procedure mismatch")
    if dataset.name == "bragg":
        if descriptor["origin"] != "calculated" or descriptor["baseline_id"] is not None or dependency_key != recipe_key:
            raise ValueError("Bragg entry does not match its fixed recipe")
    elif descriptor["origin"] != "installation" or not isinstance(descriptor["baseline_id"], str) or re.fullmatch(r"[0-9a-f]{64}", descriptor["baseline_id"]) is None:
        raise ValueError("Default recovery requires an installation baseline")
    target = payload.parent / "entries" / dependency_key / f"{entry_id}.json"
    for path in (*reversed(target.parents), target):
        if path.is_symlink() or path.is_junction():
            raise OSError(errno.ELOOP, "Calculated entry path contains a link", str(path))
    return target


def _validate_entry(document: JSONObject) -> None:
    """Validate document's exact calculated-entry schema, dependency graph and arithmetic.

    Require successful kind-specific evidence and valid finite decimal values.
    Return None without I/O; raise ValueError for schema, identity or calculation errors.
    """
    try:
        if not isinstance(document, dict) or set(document) != {"schema_version", "kind", "entry_id", "value", "units", "dependency", "simulations", "captures", "calculation"} or type(document["schema_version"]) is not int or document["schema_version"] != 1 or document["kind"] not in ("core", "bond", "bragg") or document["units"] != CAB_UNITS:
            raise ValueError("Invalid calculated entry schema")
        _dependency_key(document["dependency"])
        if document["dependency"]["kind"] != document["kind"]:
            raise ValueError("Entry dependency kind mismatch")
        value = document["value"]
        if not isinstance(value, str) or re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", value) is None or not Decimal(value).is_finite() or Decimal(value) < 0 or (document["kind"] == "bragg" and Decimal(value) == 0):
            raise ValueError("Invalid calculated strength")
        if not isinstance(document["simulations"], list) or not isinstance(document["captures"], list):
            raise ValueError("Entry evidence requires lists")
        calculation = document["calculation"]
        if not isinstance(calculation, dict) or calculation.get("method") != document["dependency"]["procedure"]["method"] or type(calculation.get("version")) is not int or calculation["version"] != document["dependency"]["procedure"]["version"] or type(calculation.get("precision")) is not int or calculation["precision"] != CAB_PRECISION or calculation.get("rounding") != CAB_ROUNDING:
            raise ValueError("Invalid calculation policy")
        common = {"method", "version", "precision", "rounding"}
        kind = document["kind"]
        identifier = document["entry_id"]
        if not isinstance(identifier, str):
            raise ValueError("Entry identity must be a string")
        if kind == "bragg":
            if set(calculation) != common | {"value_field", "row_index", "energy_keV", "projectile_mass_amu", "target_mass_amu", "phase", "density_g_cm3", "stopping_units"} or calculation["value_field"] != "electronic_stopping" or type(calculation["row_index"]) is not int or calculation["row_index"] != 1 or calculation["phase"] != "gas" or type(calculation["stopping_units"]) is not int or calculation["stopping_units"] != 7 or len(document["simulations"]) != 1 or document["captures"]:
                raise ValueError("Invalid Bragg calculation or evidence")
            for field in ("energy_keV", "projectile_mass_amu", "target_mass_amu", "density_g_cm3"):
                if not isinstance(calculation[field], str) or not Decimal(calculation[field]).is_finite() or Decimal(calculation[field]) <= 0:
                    raise ValueError("Invalid Bragg reference quantity")
            if Fraction(calculation["energy_keV"]) != 125 * Fraction(calculation["projectile_mass_amu"]):
                raise ValueError("Bragg energy must be 125 times projectile mass")
        elif kind == "core" and identifier == "z001":
            if set(calculation) != common | {"convention", "value"} or calculation["convention"] != "hydrogen-core-zero" or calculation["value"] != "0" or Decimal(value) != 0 or document["captures"] or document["simulations"] or document["dependency"]["entries"]:
                raise ValueError("Invalid fixed H core convention")
        elif kind == "bond":
            if set(calculation) != common | {"initial_correction", "estimated_count", "captures_by_count", "hydrogen_stopping", "value", "threshold_verified"} or calculation["threshold_verified"] is not True or document["simulations"]:
                raise ValueError("Invalid bond calculation schema")
            initial = calculation["initial_correction"]
            if not isinstance(initial, str) or not Decimal(initial).is_finite() or Decimal(initial) <= 0:
                raise ValueError("Initial bond correction must be positive")
            numerator, denominator = Decimal(initial).as_integer_ratio()
            count = max(1, (denominator + numerator - 1) // numerator)
            if type(calculation["estimated_count"]) is not int or calculation["estimated_count"] != count or not isinstance(calculation["captures_by_count"], list):
                raise ValueError("Invalid estimated bond threshold")
            observed: dict[int, str] = {}
            for capture in calculation["captures_by_count"]:
                if not isinstance(capture, dict) or set(capture) != {"count", "capture_index", "correction"} or type(capture["count"]) is not int or capture["count"] < 1 or capture["count"] in observed or type(capture["capture_index"]) is not int or not 0 <= capture["capture_index"] < len(document["captures"]) or not isinstance(capture["correction"], str) or not Decimal(capture["correction"]).is_finite() or Decimal(capture["correction"]) < 0:
                    raise ValueError("Invalid bond capture index/count/value")
                observed[capture["count"]] = capture["correction"]
            required = {1, count} | ({count - 1} if count > 1 else set())
            if set(observed) != required or observed[1] != initial or Decimal(observed[count]) < 1 or (count > 1 and Decimal(observed[count - 1]) >= 1):
                raise ValueError("Bond threshold verification failed")
            stopping = calculation["hydrogen_stopping"]
            if not isinstance(stopping, str) or not Decimal(stopping).is_finite() or Decimal(stopping) <= 0:
                raise ValueError("Invalid hydrogen stopping")
            with localcontext() as context:
                context.prec, context.rounding = CAB_PRECISION, CAB_ROUNDING
                expected = Decimal(observed[count]) * Decimal(stopping) / count
            if not isinstance(calculation["value"], str) or Decimal(value) != expected or Decimal(calculation["value"]) != expected:
                raise ValueError("Bond arithmetic mismatch")
        else:
            if set(calculation) != common | {"elemental_stopping", "estimates", "mean", "minimum", "maximum", "spread"} or not isinstance(calculation["estimates"], list) or len(calculation["estimates"]) != 18 or len(document["captures"]) != 18 or document["simulations"]:
                raise ValueError("Invalid core calculation schema")
            estimates: list[Decimal] = []
            seen: set[str] = set()
            native_ids = {f"z{left:03d}-z{right:03d}-" + {"-": "s", "=": "d", "#": "t"}[order] for left, right, order in SR_BONDS}
            stopping = calculation["elemental_stopping"]
            if not isinstance(stopping, str) or not Decimal(stopping).is_finite() or Decimal(stopping) <= 0:
                raise ValueError("Invalid elemental stopping")
            with localcontext() as context:
                context.prec, context.rounding = CAB_PRECISION, CAB_ROUNDING
                for estimate in calculation["estimates"]:
                    if not isinstance(estimate, dict) or set(estimate) != {"bond", "correction", "bond_strength", "value", "capture_index"} or estimate["bond"] not in native_ids or estimate["bond"] in seen or type(estimate["capture_index"]) is not int or not 0 <= estimate["capture_index"] < 18:
                        raise ValueError("Invalid core estimate identity")
                    seen.add(estimate["bond"])
                    for field in ("correction", "bond_strength", "value"):
                        if not isinstance(estimate[field], str) or not Decimal(estimate[field]).is_finite() or Decimal(estimate[field]) < 0:
                            raise ValueError("Negative/nonfinite core estimate")
                    expected = Decimal(estimate["correction"]) * Decimal(stopping) - Decimal(estimate["bond_strength"])
                    if Decimal(estimate["value"]) != expected or expected < 0:
                        raise ValueError("Core estimate arithmetic mismatch")
                    estimates.append(expected)
                quantities = {"mean": sum(estimates, Decimal(0)) / 18, "minimum": min(estimates), "maximum": max(estimates), "spread": max(estimates) - min(estimates)}
            if any(not isinstance(calculation[field], str) or Decimal(calculation[field]) != expected for field, expected in quantities.items()) or Decimal(value) != quantities["mean"]:
                raise ValueError("Core aggregate arithmetic mismatch")
        for simulation in document["simulations"]:
            if not isinstance(simulation, dict) or set(simulation) != {"simulation_id", "result", "raw"}:
                raise ValueError("Invalid dependency simulation evidence")
        for capture in document["captures"]:
            if not isinstance(capture, dict) or set(capture) != {"manifest", "correction", "inputs", "stdout", "stderr"} or not isinstance(capture["inputs"], list):
                raise ValueError("Invalid capture evidence")
    except (KeyError, TypeError, DecimalException, OverflowError) as error:
        raise ValueError("Malformed calculated entry values") from error


def _entry_result(document: JSONObject) -> JSONObject:
    """Return document's semantic duplicate-comparison projection without changing it or performing I/O.

    Validate first, then remove evidence paths/timestamps while preserving recipe,
    calculation inputs and numeric results. Canonicalize decimal-equal spellings.
    Raise ValueError for invalid entry data.
    """
    _validate_entry(document)
    result = {key: document[key] for key in ("schema_version", "kind", "entry_id", "value", "units", "dependency", "calculation")}
    encoded = json.dumps(result, sort_keys=True)
    tokens = re.findall(r'"(?:[^"\\]|\\.)*"', encoded)
    for token in set(tokens):
        decoded = json.loads(token)
        if re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", decoded):
            decimal = Decimal(decoded)
            spelling = format(decimal, "f")
            if "." in spelling:
                spelling = spelling.rstrip("0").rstrip(".")
            if decimal == 0:
                spelling = "0"
            encoded = encoded.replace(token, json.dumps(spelling))
    return json.loads(encoded)


def load_cab_entry(paths: ProjectPaths, reference: CABEntryReference) -> JSONObject:
    """Load reference in paths, verifying descriptor, exact entry bytes and all linked evidence.

    Return independent JSON without engine execution or data changes. Missing or
    corrupt published evidence raises ValueError/OSError rather than becoming a miss.
    Dependency kinds form a validated acyclic graph.
    """
    if not isinstance(reference, CABEntryReference):
        raise ValueError("Expected CABEntryReference")
    _validate_reference(ResourceReference("entry", "entry", "v1", reference.sha256, reference.size_bytes))
    path = _entry_path(paths, reference.dataset, reference.dependency_key, reference.entry_id)
    if _hash_file(path) != (reference.sha256, reference.size_bytes):
        raise ValueError("Calculated entry hash/size mismatch")
    document = _read_json(path)
    if _hash_file(path) != (reference.sha256, reference.size_bytes):
        raise ValueError("Calculated entry changed while reading")
    _validate_entry(document)
    if document["entry_id"] != reference.entry_id or document["kind"] != reference.dataset.name or _dependency_key(document["dependency"]) != reference.dependency_key:
        raise ValueError("Calculated entry identity mismatch")
    descriptor = _read_json(verify_resource(paths, reference.dataset))
    fixed = descriptor["dependency"]
    if any(document["dependency"][field] != fixed[field] for field in ("schema_version", "kind", "procedure", "files")):
        raise ValueError("Calculated entry differs from descriptor recipe")
    lower: list[JSONObject] = []
    for entry in document["dependency"]["entries"]:
        lower.append(load_cab_entry(paths, CABEntryReference(ResourceReference(**entry["dataset"]), entry["dependency_key"], entry["entry_id"], entry["sha256"], entry["size_bytes"])))
    files: list[JSONObject] = []
    for simulation in document["simulations"]:
        files.extend((simulation["result"], simulation["raw"]))
    for capture in document["captures"]:
        files.extend(capture[field] for field in ("manifest", "correction", "stdout", "stderr"))
        files.extend(capture["inputs"])
    for evidence in files:
        if not isinstance(evidence, dict) or set(evidence) != {"kind", "id", "path", "sha256", "size_bytes"} or evidence["kind"] not in ("simulation", "worker"):
            raise ValueError("Invalid file evidence")
        pattern = r"srim_[0-9]{8}_[0-9]{6}_[1-9][0-9]*" if evidence["kind"] == "simulation" else r"worker_[1-9][0-9]*"
        if not isinstance(evidence["id"], str) or re.fullmatch(pattern, evidence["id"]) is None or not isinstance(evidence["path"], str):
            raise ValueError("Invalid evidence owner")
        relative = PurePosixPath(evidence["path"])
        if relative.is_absolute() or str(relative) != evidence["path"] or any(part in (".", "..") or ":" in part or "\\" in part for part in relative.parts) or not relative.parts:
            raise ValueError("Unsafe evidence path")
        root = paths.simulation_memory if evidence["kind"] == "simulation" else paths.workers
        _validate_reference(ResourceReference("file", "file", "v1", evidence["sha256"], evidence["size_bytes"]))
        if _hash_file(root / evidence["id"] / relative) != (evidence["sha256"], evidence["size_bytes"]):
            raise ValueError("Calculated evidence hash/size mismatch")
    for simulation in document["simulations"]:
        from src.srim.metadata import read_simulation
        metadata = read_simulation(paths, simulation["simulation_id"])
        if metadata["status"] != "succeeded" or any(simulation[field]["kind"] != "simulation" or simulation[field]["id"] != simulation["simulation_id"] for field in ("result", "raw")):
            raise ValueError("Bragg evidence must belong to a successful simulation")
        result = _read_json(paths.simulation_memory / simulation["simulation_id"] / simulation["result"]["path"])
        rows = result["rows"]
        if len(rows) != 1 or Decimal(rows[0]["electronic_stopping"]) != Decimal(document["value"]):
            raise ValueError("Bragg value does not match parsed evidence")
        from src.srim.outputs import parse_sr_output
        owner = paths.simulation_memory / simulation["simulation_id"]
        effective_path = owner / "input" / ("effective-request.json" if metadata["schema_version"] == 2 else "request.json")
        effective = _read_json(effective_path)
        raw_bytes = (owner / simulation["raw"]["path"]).read_bytes()
        if parse_sr_output(raw_bytes, effective) != result:
            raise ValueError("Bragg parsed evidence does not match preserved raw output")
        quantities = document["calculation"]
        parameters = effective["parameters"]
        if parameters["projectile"] != {"atomic_number": 1, "mass_amu": quantities["projectile_mass_amu"]} or parameters["energy"] != {"mode": "list", "values_keV": [quantities["energy_keV"]]} or parameters["target"]["phase"] != "gas" or parameters["target"]["density_g_cm3"] != quantities["density_g_cm3"] or len(parameters["target"]["elements"]) != 1 or parameters["target"]["elements"][0]["atomic_number"] != int(document["entry_id"][1:]) or parameters["target"]["elements"][0]["mass_amu"] != quantities["target_mass_amu"] or parameters["target"]["elements"][0]["stoichiometry"] != "1" or parameters["stopping_units"] != 7 or parameters["correction"] != {"mode": "none", "applied_factor": "1.0"}:
            raise ValueError("Bragg reference simulation differs from recorded calculation")
        inventory = {item["path"]: item for item in metadata["outputs"]}
        if any(inventory.get(simulation[field]["path"]) != {key: value for key, value in simulation[field].items() if key in ("path", "sha256", "size_bytes")} for field in ("result", "raw")):
            raise ValueError("Bragg evidence differs from sealed simulation outputs")
    for capture in document["captures"]:
        identities = {(item["kind"], item["id"]) for item in [capture[field] for field in ("manifest", "correction", "stdout", "stderr")] + capture["inputs"]}
        if len(identities) != 1:
            raise ValueError("Capture files must belong to one owner")
        owner_kind, owner_id = next(iter(identities))
        owner_root = paths.workers if owner_kind == "worker" else paths.simulation_memory
        if owner_kind == "simulation":
            from src.srim.metadata import read_simulation, _inventory
            owner_metadata = read_simulation(paths, owner_id)
            owner_record = owner_root / owner_id
            if owner_metadata["status"] != "succeeded" or owner_metadata["engine"]["name"] != "sr-capture" or _inventory(owner_record / "input", owner_record) != owner_metadata["inputs"] or _inventory(owner_record / "output", owner_record) != owner_metadata["outputs"]:
                raise ValueError("Capture record is not sealed successful evidence")
        manifest = _read_json(owner_root / capture["manifest"]["id"] / capture["manifest"]["path"])
        if set(manifest) != {"schema_version", "status", "inputs_before", "inputs_after", "correction", "process_id", "started_at", "finished_at", "cleanup", "error"} or manifest["status"] != "succeeded" or manifest["inputs_before"] != manifest["inputs_after"] or manifest["error"] is not None or manifest["cleanup"] is not True:
            raise ValueError("Capture evidence is incomplete or failed")
        if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1 or manifest["inputs_before"] != capture["inputs"] or type(manifest["process_id"]) is not int or manifest["process_id"] < 1:
            raise ValueError("Capture manifest inputs/process identity mismatch")
        from datetime import datetime
        for field in ("started_at", "finished_at"):
            if not isinstance(manifest[field], str) or datetime.fromisoformat(manifest[field]).utcoffset() is None:
                raise ValueError("Capture manifest requires aware timestamps")
        runtime = PurePosixPath(capture["manifest"]["path"]).parent
        if any(not PurePosixPath(item["path"]).is_relative_to(runtime) for item in [capture[field] for field in ("correction", "stdout", "stderr")] + capture["inputs"]):
            raise ValueError("Capture evidence must belong to the same isolated runtime")
        raw = _read_json(owner_root / capture["correction"]["id"] / capture["correction"]["path"])
        if set(raw) != {"compound", "correction", "process_id", "recorded_at"} or raw["compound"] != "Custom" or raw["correction"] != manifest["correction"] or raw["process_id"] != manifest["process_id"]:
            raise ValueError("Capture reading does not match manifest")
        capture_index = document["captures"].index(capture)
        if document["kind"] == "bond":
            observations = [item for item in document["calculation"]["captures_by_count"] if item["capture_index"] == capture_index]
        else:
            observations = [item for item in document["calculation"]["estimates"] if item["capture_index"] == capture_index]
        if len(observations) != 1 or observations[0]["correction"] != raw["correction"]:
            raise ValueError("Calculation correction differs from actual captured text")
        native = {PurePosixPath(item["path"]).relative_to(runtime).as_posix(): item for item in capture["inputs"]}
        expected_names = {"SR.exe", "SR.exe.manifest", "SR.IN", "Data/ATOMDATA", "Data/Compound.dat", "Data/SCOEF03.dat", "Data/SNUC03.dat", "Data/VERSION"}
        if set(native) != expected_names:
            raise ValueError("Capture native inventory is incomplete")
        identities = {item["role"]: item for item in document["dependency"]["files"]}
        for name, role in (("SR.exe", "SR.exe"), ("SR.exe.manifest", "capture-manifest"), ("Data/ATOMDATA", "ATOMDATA"), ("Data/SCOEF03.dat", "SCOEF03.dat"), ("Data/SNUC03.dat", "SNUC03.dat"), ("Data/VERSION", "Data/VERSION")):
            if role not in identities or any(native[name][field] != identities[role][field] for field in ("sha256", "size_bytes")):
                raise ValueError("Capture numerical inputs differ from pinned recovery recipe")
        from src.srim.cab_recovery import _render_mock_compound
        bond_id = document["entry_id"] if document["kind"] == "bond" else observations[0]["bond"]
        left, right, order = bond_id.split("-")
        bond = (int(left[1:]), int(right[1:]), {"s": "-", "d": "=", "t": "#"}[order])
        count = observations[0]["count"] if document["kind"] == "bond" else 1
        expected_mock = _render_mock_compound(1 if document["kind"] == "bond" else int(document["entry_id"][1:]), bond, count)
        if (native["Data/Compound.dat"]["sha256"], native["Data/Compound.dat"]["size_bytes"]) != (hashlib.sha256(expected_mock).hexdigest(), len(expected_mock)):
            raise ValueError("Captured mock input differs from claimed element/bond/count")
    if document["kind"] == "bond" and (len(lower) != 1 or lower[0]["kind"] != "bragg" or lower[0]["entry_id"] != "z001" or Decimal(lower[0]["value"]) != Decimal(document["calculation"]["hydrogen_stopping"])):
        raise ValueError("Bond hydrogen stopping dependency mismatch")
    if document["kind"] == "core" and document["entry_id"] != "z001":
        stopping = [entry for entry in lower if entry["kind"] == "bragg" and entry["entry_id"] == document["entry_id"]]
        bonds = {entry["entry_id"]: entry["value"] for entry in lower if entry["kind"] == "bond"}
        if len(lower) != 19 or len(stopping) != 1 or Decimal(stopping[0]["value"]) != Decimal(document["calculation"]["elemental_stopping"]) or any(estimate["bond"] not in bonds or Decimal(estimate["bond_strength"]) != Decimal(bonds[estimate["bond"]]) for estimate in document["calculation"]["estimates"]):
            raise ValueError("Core lower-level dependencies mismatch")
    return document


def find_cab_entry(paths: ProjectPaths, dataset: ResourceReference, dependency: JSONObject, entry_id: str) -> CABEntryReference | None:
    """Return a verified matching paths/dataset/dependency/entry_id reference or None for absence.

    Verify published bytes and all evidence; corruption raises ValueError/OSError.
    Perform no calculation and do not treat unavailable published evidence as a miss.
    """
    key = _dependency_key(dependency)
    path = _entry_path(paths, dataset, key, entry_id)
    if not os.path.lexists(path):
        return None
    checksum, size = _hash_file(path)
    reference = CABEntryReference(dataset, key, entry_id, checksum, size)
    load_cab_entry(paths, reference)
    return reference


def publish_cab_entry(paths: ProjectPaths, dataset: ResourceReference, entry: JSONObject) -> CABEntryReference:
    """Publish verified successful entry under paths/dataset or reuse its equal immutable result.

    Stage synchronized canonical bytes and take a short version lock only for final
    duplicate comparison/publication. Preserve evidence and staging on failure;
    return a verified reference. Conflicts raise ValueError, filesystem errors
    propagate and existing entry bytes are never changed.
    """
    _validate_entry(entry)
    key = _dependency_key(entry["dependency"])
    target = _entry_path(paths, dataset, key, entry["entry_id"])
    lower: list[JSONObject] = []
    for linked in entry["dependency"]["entries"]:
        lower.append(load_cab_entry(paths, CABEntryReference(ResourceReference(**linked["dataset"]), linked["dependency_key"], linked["entry_id"], linked["sha256"], linked["size_bytes"])))
    files: list[JSONObject] = []
    for simulation in entry["simulations"]:
        files.extend((simulation["result"], simulation["raw"]))
    for capture in entry["captures"]:
        files.extend(capture[field] for field in ("manifest", "correction", "stdout", "stderr"))
        files.extend(capture["inputs"])
    for evidence in files:
        if not isinstance(evidence, dict) or set(evidence) != {"kind", "id", "path", "sha256", "size_bytes"} or evidence["kind"] not in ("simulation", "worker"):
            raise ValueError("Invalid file evidence")
        pattern = r"srim_[0-9]{8}_[0-9]{6}_[1-9][0-9]*" if evidence["kind"] == "simulation" else r"worker_[1-9][0-9]*"
        if not isinstance(evidence["id"], str) or re.fullmatch(pattern, evidence["id"]) is None or not isinstance(evidence["path"], str):
            raise ValueError("Invalid evidence owner")
        relative = PurePosixPath(evidence["path"])
        if relative.is_absolute() or str(relative) != evidence["path"] or any(part in (".", "..") or ":" in part or "\\" in part for part in relative.parts) or not relative.parts:
            raise ValueError("Unsafe evidence path")
        root = paths.simulation_memory if evidence["kind"] == "simulation" else paths.workers
        _validate_reference(ResourceReference("file", "file", "v1", evidence["sha256"], evidence["size_bytes"]))
        if _hash_file(root / evidence["id"] / relative) != (evidence["sha256"], evidence["size_bytes"]):
            raise ValueError("Calculated evidence hash/size mismatch")
    for simulation in entry["simulations"]:
        from src.srim.metadata import read_simulation
        metadata = read_simulation(paths, simulation["simulation_id"])
        if metadata["status"] != "succeeded" or any(simulation[field]["kind"] != "simulation" or simulation[field]["id"] != simulation["simulation_id"] for field in ("result", "raw")):
            raise ValueError("Bragg evidence must belong to a successful simulation")
        result = _read_json(paths.simulation_memory / simulation["simulation_id"] / simulation["result"]["path"])
        rows = result["rows"]
        if len(rows) != 1 or Decimal(rows[0]["electronic_stopping"]) != Decimal(entry["value"]):
            raise ValueError("Bragg value does not match parsed evidence")
        from src.srim.outputs import parse_sr_output
        owner = paths.simulation_memory / simulation["simulation_id"]
        effective_path = owner / "input" / ("effective-request.json" if metadata["schema_version"] == 2 else "request.json")
        effective = _read_json(effective_path)
        raw_bytes = (owner / simulation["raw"]["path"]).read_bytes()
        if parse_sr_output(raw_bytes, effective) != result:
            raise ValueError("Bragg parsed evidence does not match preserved raw output")
        quantities = entry["calculation"]
        parameters = effective["parameters"]
        if parameters["projectile"] != {"atomic_number": 1, "mass_amu": quantities["projectile_mass_amu"]} or parameters["energy"] != {"mode": "list", "values_keV": [quantities["energy_keV"]]} or parameters["target"]["phase"] != "gas" or parameters["target"]["density_g_cm3"] != quantities["density_g_cm3"] or len(parameters["target"]["elements"]) != 1 or parameters["target"]["elements"][0]["atomic_number"] != int(entry["entry_id"][1:]) or parameters["target"]["elements"][0]["mass_amu"] != quantities["target_mass_amu"] or parameters["target"]["elements"][0]["stoichiometry"] != "1" or parameters["stopping_units"] != 7 or parameters["correction"] != {"mode": "none", "applied_factor": "1.0"}:
            raise ValueError("Bragg reference simulation differs from recorded calculation")
        inventory = {item["path"]: item for item in metadata["outputs"]}
        if any(inventory.get(simulation[field]["path"]) != {key: value for key, value in simulation[field].items() if key in ("path", "sha256", "size_bytes")} for field in ("result", "raw")):
            raise ValueError("Bragg evidence differs from sealed simulation outputs")
    for capture in entry["captures"]:
        identities = {(item["kind"], item["id"]) for item in [capture[field] for field in ("manifest", "correction", "stdout", "stderr")] + capture["inputs"]}
        if len(identities) != 1:
            raise ValueError("Capture files must belong to one owner")
        owner_kind, owner_id = next(iter(identities))
        owner_root = paths.workers if owner_kind == "worker" else paths.simulation_memory
        if owner_kind == "simulation":
            from src.srim.metadata import read_simulation, _inventory
            owner_metadata = read_simulation(paths, owner_id)
            owner_record = owner_root / owner_id
            if owner_metadata["status"] != "succeeded" or owner_metadata["engine"]["name"] != "sr-capture" or _inventory(owner_record / "input", owner_record) != owner_metadata["inputs"] or _inventory(owner_record / "output", owner_record) != owner_metadata["outputs"]:
                raise ValueError("Capture record is not sealed successful evidence")
        manifest = _read_json(owner_root / capture["manifest"]["id"] / capture["manifest"]["path"])
        if set(manifest) != {"schema_version", "status", "inputs_before", "inputs_after", "correction", "process_id", "started_at", "finished_at", "cleanup", "error"} or manifest["status"] != "succeeded" or manifest["inputs_before"] != manifest["inputs_after"] or manifest["error"] is not None or manifest["cleanup"] is not True:
            raise ValueError("Capture evidence is incomplete or failed")
        if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1 or manifest["inputs_before"] != capture["inputs"] or type(manifest["process_id"]) is not int or manifest["process_id"] < 1:
            raise ValueError("Capture manifest inputs/process identity mismatch")
        from datetime import datetime
        for field in ("started_at", "finished_at"):
            if not isinstance(manifest[field], str) or datetime.fromisoformat(manifest[field]).utcoffset() is None:
                raise ValueError("Capture manifest requires aware timestamps")
        runtime = PurePosixPath(capture["manifest"]["path"]).parent
        if any(not PurePosixPath(item["path"]).is_relative_to(runtime) for item in [capture[field] for field in ("correction", "stdout", "stderr")] + capture["inputs"]):
            raise ValueError("Capture evidence must belong to the same isolated runtime")
        raw = _read_json(owner_root / capture["correction"]["id"] / capture["correction"]["path"])
        if set(raw) != {"compound", "correction", "process_id", "recorded_at"} or raw["compound"] != "Custom" or raw["correction"] != manifest["correction"] or raw["process_id"] != manifest["process_id"]:
            raise ValueError("Capture reading does not match manifest")
        capture_index = entry["captures"].index(capture)
        if entry["kind"] == "bond":
            observations = [item for item in entry["calculation"]["captures_by_count"] if item["capture_index"] == capture_index]
        else:
            observations = [item for item in entry["calculation"]["estimates"] if item["capture_index"] == capture_index]
        if len(observations) != 1 or observations[0]["correction"] != raw["correction"]:
            raise ValueError("Calculation correction differs from actual captured text")
        native = {PurePosixPath(item["path"]).relative_to(runtime).as_posix(): item for item in capture["inputs"]}
        expected_names = {"SR.exe", "SR.exe.manifest", "SR.IN", "Data/ATOMDATA", "Data/Compound.dat", "Data/SCOEF03.dat", "Data/SNUC03.dat", "Data/VERSION"}
        if set(native) != expected_names:
            raise ValueError("Capture native inventory is incomplete")
        identities = {item["role"]: item for item in entry["dependency"]["files"]}
        for name, role in (("SR.exe", "SR.exe"), ("SR.exe.manifest", "capture-manifest"), ("Data/ATOMDATA", "ATOMDATA"), ("Data/SCOEF03.dat", "SCOEF03.dat"), ("Data/SNUC03.dat", "SNUC03.dat"), ("Data/VERSION", "Data/VERSION")):
            if role not in identities or any(native[name][field] != identities[role][field] for field in ("sha256", "size_bytes")):
                raise ValueError("Capture numerical inputs differ from pinned recovery recipe")
        from src.srim.cab_recovery import _render_mock_compound
        bond_id = entry["entry_id"] if entry["kind"] == "bond" else observations[0]["bond"]
        left, right, order = bond_id.split("-")
        bond = (int(left[1:]), int(right[1:]), {"s": "-", "d": "=", "t": "#"}[order])
        count = observations[0]["count"] if entry["kind"] == "bond" else 1
        expected_mock = _render_mock_compound(1 if entry["kind"] == "bond" else int(entry["entry_id"][1:]), bond, count)
        if (native["Data/Compound.dat"]["sha256"], native["Data/Compound.dat"]["size_bytes"]) != (hashlib.sha256(expected_mock).hexdigest(), len(expected_mock)):
            raise ValueError("Captured mock input differs from claimed element/bond/count")
    if entry["kind"] == "bond" and (len(lower) != 1 or lower[0]["kind"] != "bragg" or lower[0]["entry_id"] != "z001" or Decimal(lower[0]["value"]) != Decimal(entry["calculation"]["hydrogen_stopping"])):
        raise ValueError("Bond hydrogen stopping dependency mismatch")
    if entry["kind"] == "core" and entry["entry_id"] != "z001":
        stopping = [dependency for dependency in lower if dependency["kind"] == "bragg" and dependency["entry_id"] == entry["entry_id"]]
        bonds = {entry["entry_id"]: entry["value"] for entry in lower if entry["kind"] == "bond"}
        if len(lower) != 19 or len(stopping) != 1 or Decimal(stopping[0]["value"]) != Decimal(entry["calculation"]["elemental_stopping"]) or any(estimate["bond"] not in bonds or Decimal(estimate["bond_strength"]) != Decimal(bonds[estimate["bond"]]) for estimate in entry["calculation"]["estimates"]):
            raise ValueError("Core lower-level dependencies mismatch")
    descriptor = _read_json(verify_resource(paths, dataset))
    if any(entry["dependency"][field] != descriptor["dependency"][field] for field in ("schema_version", "kind", "procedure", "files")):
        raise ValueError("Entry differs from immutable descriptor recipe")
    version_directory = verify_resource(paths, dataset).parent
    temporary = Path(mkdtemp(prefix=".entry-", dir=version_directory))
    staged = temporary / target.name
    contents = (json.dumps(entry, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    try:
        with staged.open("xb") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        with _lock_parent(version_directory):
            target = _entry_path(paths, dataset, key, entry["entry_id"])
            if os.path.lexists(target):
                checksum, size = _hash_file(target)
                existing = CABEntryReference(dataset, key, entry["entry_id"], checksum, size)
                loaded = load_cab_entry(paths, existing)
                if _entry_result(loaded) != _entry_result(entry):
                    raise ValueError("Conflicting calculated result; published entry remains unchanged")
                return existing
            target.parent.mkdir(parents=True, exist_ok=True)
            staged.rename(target)
        reference = CABEntryReference(dataset, key, entry["entry_id"], hashlib.sha256(contents).hexdigest(), len(contents))
        load_cab_entry(paths, reference)
        return reference
    except BaseException as error:
        error.add_note(f"Calculated entry retained artifacts: {temporary}; destination: {target}")
        raise
