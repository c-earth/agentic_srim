"""Register installation baselines and atomically switch one coherent set of default resources."""

from collections.abc import Mapping
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
from src.backend.config import Config
from src.backend.paths import ProjectPaths
from src.backend.records import _lock_parent
from src.backend.resources import ResourceReference, _hash_file, _read_json, _validate_reference, _write_json, register_resource, verify_resource
from src.srim.cab.cache import _dependency_key, find_cab_entry, publish_cab_entry
from src.srim.cab.constants import BRAGG_PROCEDURE_VERSION, CAB_PRECISION, CAB_ROUNDING, CAB_UNITS, CAPTURE_MANIFEST, CAPTURE_PROCEDURE_VERSION, MOCK_HEADER, RECOVERY_PROCEDURE_VERSION
from src.srim.resources.compatibility import normalize_atomdata, normalize_compounds, read_module_version
from src.srim.resources.electronic import _row_atomic_number
from src.srim.resources.materials import parse_atomdata, parse_compounds
from src.srim.common.types import JSONObject
from src.srim.resources.nuclear import _row_pair


def _read_installation(config: Config) -> tuple[JSONObject, dict[str, bytes], JSONObject]:
    """Read config's link-free required installation files and return effective identities/bytes and original source records.

    Validate native materials/coefficient tables and ASCII versions, checking every
    file before and after reading. Include installed compound only for target defaults.
    Perform no writes; raise ValueError for malformed/changing data and propagate I/O errors.
    """
    if not isinstance(config.srim_root, Path) or not config.srim_root.is_absolute():
        raise ValueError("SRIM installation must be an absolute Path")
    locations = {"SRModule.exe": "SR Module/SRModule.exe", "VERSION": "SR Module/VERSION", "SR.exe": "SR.exe", "Data/VERSION": "Data/VERSION", "ATOMDATA": "Data/ATOMDATA", "SCOEF03.dat": "Data/SCOEF03.dat", "SNUC03.dat": "Data/SNUC03.dat", "Compound.dat": "Data/Compound.dat"}
    version_path, version_bytes = read_module_version(config.srim_root)
    locations["VERSION"] = str(version_path.relative_to(config.srim_root))
    identities: JSONObject = {}
    sources: JSONObject = {}
    files: dict[str, bytes] = {}
    for role, location in locations.items():
        path = config.srim_root / location
        expected = _hash_file(path)
        contents = path.read_bytes()
        if (hashlib.sha256(contents).hexdigest(), len(contents)) != expected or _hash_file(path) != expected:
            raise ValueError(f"Installation source changed while reading: {path}")
        changes: list[dict[str, object]] = []
        if role == "VERSION" and contents != version_bytes:
            raise ValueError("Module version changed while collecting inputs")
        if role == "ATOMDATA":
            contents, changes = normalize_atomdata(contents)
        elif role == "Compound.dat":
            contents, changes = normalize_compounds(contents)
        files[role] = contents
        sources[role] = {"path": str(path), "sha256": expected[0], "size_bytes": expected[1], "changes": changes}
        identities[role] = {"role": role, "sha256": hashlib.sha256(contents).hexdigest(), "size_bytes": len(contents)}
    for role in ("VERSION", "Data/VERSION"):
        try:
            lines = files[role].decode("ascii").splitlines()
        except UnicodeError as error:
            raise ValueError(f"Installation {role} must be ASCII") from error
        if not lines or not lines[0].strip():
            raise ValueError(f"Installation {role} requires a nonblank first line")
    if not files["SR.exe"] or not files["SRModule.exe"]:
        raise ValueError("Installation executables cannot be empty")
    parse_atomdata(files["ATOMDATA"])
    parse_compounds(files["Compound.dat"])
    electronic = files["SCOEF03.dat"].splitlines()
    if len(electronic) != 92 or any(_row_atomic_number(row) != z for z, row in enumerate(electronic, 1)):
        raise ValueError("Installation requires 92 ordered electronic coefficient rows")
    pairs = [(left, right) for left in range(1, 93) for right in range(left, 93)]
    nuclear = files["SNUC03.dat"].splitlines()
    if len(nuclear) != len(pairs) or any(_row_pair(row) != pair for pair, row in zip(pairs, nuclear)):
        raise ValueError("Installation requires 4,278 ordered nuclear coefficient rows")
    for role, location in locations.items():
        if _hash_file(config.srim_root / location) != (sources[role]["sha256"], sources[role]["size_bytes"]):
            raise ValueError("Installation changed while collecting default inputs")
    if read_module_version(config.srim_root) != (version_path, version_bytes):
        raise ValueError("Module version selection changed while collecting inputs")
    return identities, files, sources


def _make_baseline(files: Mapping[str, bytes]) -> JSONObject:
    """Return a fresh canonical numerical baseline recipe from required role bytes in files.

    Exclude installed compound/path, include generated assets and fixed procedures.
    Perform no I/O and raise ValueError for absent/wrongly typed numerical inputs.
    The caller hashes canonical returned bytes; the recipe does not hash itself.
    """
    roles = ("SRModule.exe", "VERSION", "SR.exe", "Data/VERSION", "ATOMDATA", "SCOEF03.dat", "SNUC03.dat")
    if any(role not in files or not isinstance(files[role], bytes) for role in roles):
        raise ValueError("Missing baseline file bytes")
    procedures: JSONObject = {}
    for kind, method, version in (("bragg", "bragg", BRAGG_PROCEDURE_VERSION), ("recovery", "default-recovery", RECOVERY_PROCEDURE_VERSION), ("capture", "compound-capture", CAPTURE_PROCEDURE_VERSION)):
        procedures[kind] = {"method": method, "version": version, "precision": CAB_PRECISION, "rounding": CAB_ROUNDING, "units": CAB_UNITS, "reference": {"projectile": 1, "energy_per_amu_keV": "125", "phase": "gas", "stopping_units": 7, "projectile_mass_field": "isotope_mass_amu", "target_mass_field": "natural_mass_amu"}}
    return {"schema_version": 1, "files": [{"role": role, "sha256": hashlib.sha256(files[role]).hexdigest(), "size_bytes": len(files[role])} for role in sorted(roles)], "procedures": procedures, "generated": {"manifest": {"role": "capture-manifest", "sha256": hashlib.sha256(CAPTURE_MANIFEST).hexdigest(), "size_bytes": len(CAPTURE_MANIFEST)}, "mock_header": {"role": "mock-header", "sha256": hashlib.sha256(MOCK_HEADER).hexdigest(), "size_bytes": len(MOCK_HEADER)}}}


def _validate_default_generation(document: JSONObject) -> None:
    """Validate document's exact coordinated pointer and concrete role identities without I/O.

    Return None; raise ValueError for wrong schema, baseline hash, role or version.
    Physical resource and recipe checks are performed by the coordinator readers.
    """
    roles = {"atomdata", "compound", "electronic", "nuclear", "core", "bond", "bragg"}
    if not isinstance(document, dict) or set(document) != {"schema_version", "baseline_id", "resources"} or type(document["schema_version"]) is not int or document["schema_version"] != 1 or not isinstance(document["baseline_id"], str) or re.fullmatch(r"[0-9a-f]{64}", document["baseline_id"]) is None or not isinstance(document["resources"], dict) or set(document["resources"]) != roles:
        raise ValueError("Invalid coordinated defaults schema")
    for role, reference in document["resources"].items():
        if not isinstance(reference, dict) or set(reference) != {"category", "name", "version", "sha256", "size_bytes"}:
            raise ValueError("Invalid default resource reference")
        _validate_reference(ResourceReference(**reference))
        category = "material" if role in ("atomdata", "compound") else "engine" if role in ("electronic", "nuclear") else "cab"
        if reference["category"] != category or reference["name"] != role or re.fullmatch(r"v[1-9][0-9]*", reference["version"]) is None:
            raise ValueError("Default role requires its concrete numbered resource")


def read_sr_defaults(paths: ProjectPaths) -> JSONObject:
    """Return paths' verified coordinated default generation without registration or alias writes.

    Require canonical pointer bytes, immutable baseline and coherent descriptor
    dependencies. Return fresh JSON; missing initialization/corruption raises
    ValueError, filesystem failures propagate. No live installation is required.
    """
    pointer = paths.resources / "defaults.json"
    if not os.path.lexists(pointer):
        raise ValueError("Defaults are uninitialized; call switch_sr_defaults")
    expected = _hash_file(pointer)
    document = _read_json(pointer)
    _validate_default_generation(document)
    encoded = (json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    if (hashlib.sha256(encoded).hexdigest(), len(encoded)) != expected:
        raise ValueError("Default pointer is noncanonical or changed while reading")
    baseline_path = paths.resources / "baselines" / f'{document["baseline_id"]}.json'
    baseline = _read_json(baseline_path)
    canonical = (json.dumps(baseline, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    if _hash_file(baseline_path) != (document["baseline_id"], len(canonical)) or hashlib.sha256(canonical).hexdigest() != document["baseline_id"] or set(baseline) != {"schema_version", "files", "procedures", "generated"}:
        raise ValueError("Default baseline identity mismatch")
    identities = {item["role"]: item for item in baseline["files"]}
    for role, raw in document["resources"].items():
        reference = ResourceReference(**raw)
        payload = verify_resource(paths, reference)
        manifest = _read_json(payload.parent / "manifest.json")
        provenance = manifest.get("provenance")
        if not isinstance(provenance, dict) or provenance["origin"] not in ("installation", "calculated"):
            raise ValueError("Default references require generated installation provenance")
        if role in ("atomdata", "electronic", "nuclear"):
            file_role = {"atomdata": "ATOMDATA", "electronic": "SCOEF03.dat", "nuclear": "SNUC03.dat"}[role]
            if reference.sha256 != identities[file_role]["sha256"] or reference.size_bytes != identities[file_role]["size_bytes"]:
                raise ValueError("Default native resource differs from its baseline")
        if role in ("core", "bond", "bragg"):
            descriptor = _read_json(payload)
            if set(descriptor) != {"schema_version", "kind", "origin", "baseline_id", "procedure", "units", "base", "dependency"} or descriptor["kind"] != role or descriptor["units"] != CAB_UNITS or descriptor["base"] is not None:
                raise ValueError("Invalid default calculated descriptor")
            _dependency_key(descriptor["dependency"])
            if descriptor["procedure"] != descriptor["dependency"]["procedure"]:
                raise ValueError("Default descriptor procedure mismatch")
            if role == "bragg":
                expected_files = [identities[key] for key in sorted(("SRModule.exe", "VERSION", "ATOMDATA", "SCOEF03.dat", "SNUC03.dat"))]
                if descriptor["origin"] != "calculated" or descriptor["baseline_id"] is not None or descriptor["dependency"]["files"] != expected_files:
                    raise ValueError("Default Bragg recipe differs from baseline")
            elif descriptor["origin"] != "installation" or descriptor["baseline_id"] != document["baseline_id"] or descriptor["dependency"]["files"] != baseline["files"] + [baseline["generated"][key] for key in sorted(baseline["generated"])]:
                raise ValueError("Default recovery recipe differs from baseline")
    return document


def verify_sr_baseline(paths: ProjectPaths, config: Config, baseline: JSONObject) -> None:
    """Verify pinned baseline against config's required live numerical files and paths' immutable recipe.

    baseline is the numerical recipe, not a mutable alias. Return None without
    data writes. Raise ValueError for identity/procedure mismatch, propagate I/O
    errors and never replace the caller's pinned baseline with newer defaults.
    """
    if not isinstance(baseline, dict) or set(baseline) != {"schema_version", "files", "procedures", "generated"}:
        raise ValueError("Invalid pinned baseline")
    _, files, _ = _read_installation(config)
    if _make_baseline(files) != baseline:
        raise ValueError("Pinned recovery baseline no longer matches the installation")
    encoded = (json.dumps(baseline, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    checksum = hashlib.sha256(encoded).hexdigest()
    if _hash_file(paths.resources / "baselines" / f"{checksum}.json") != (checksum, len(encoded)):
        raise ValueError("Pinned immutable baseline is unavailable or corrupt")


def switch_sr_defaults(paths: ProjectPaths, config: Config) -> JSONObject:
    """Stage config's validated installation resources and atomically switch paths' defaults.

    Return a coherent concrete generation, reusing identical bytes/recipes and the
    Bragg version on compound/GUI-only changes. Initialize fixed H zero without
    engine work. Failures retain prior pointer and staged versions with notes;
    never change installed files or recover numerical values.
    """
    pointer = paths.resources / "defaults.json"
    prior = _hash_file(pointer) if os.path.lexists(pointer) else None
    if prior is not None:
        read_sr_defaults(paths)
    identities, files, sources = _read_installation(config)
    baseline = _make_baseline(files)
    encoded = (json.dumps(baseline, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    baseline_id = hashlib.sha256(encoded).hexdigest()
    resources: JSONObject = {}
    artifacts: list[str] = []
    try:
        for role, category, file_role in (("atomdata", "material", "ATOMDATA"), ("compound", "material", "Compound.dat"), ("electronic", "engine", "SCOEF03.dat"), ("nuclear", "engine", "SNUC03.dat")):
            location = "Data/" + file_role
            provenance = {"origin": "installation", "source": {"kind": "file", "origin": sources[file_role]["path"], "sha256": sources[file_role]["sha256"], "size_bytes": sources[file_role]["size_bytes"]}, "base": None, "changes": sources[file_role]["changes"], "copied_entries": [], "baseline_id": None, "procedure": None}
            reference = register_resource(paths, config, category, role, None, files[file_role], provenance=provenance, reuse_key=identities[file_role]["sha256"])
            resources[role] = asdict(reference)
            artifacts.append(str(paths.resources / category / role / reference.version))
        descriptors: dict[str, JSONObject] = {}
        for kind in ("bragg", "bond", "core"):
            procedure = dict(baseline["procedures"]["bragg" if kind == "bragg" else "recovery"])
            procedure["method"] = "bragg" if kind == "bragg" else f"default-{kind}"
            recipe = {"schema_version": 1, "kind": kind, "procedure": procedure, "files": [identities[key] for key in sorted(("SRModule.exe", "VERSION", "ATOMDATA", "SCOEF03.dat", "SNUC03.dat"))] if kind == "bragg" else baseline["files"] + [baseline["generated"][key] for key in sorted(baseline["generated"])], "entries": []}
            descriptor = {"schema_version": 1, "kind": kind, "origin": "calculated" if kind == "bragg" else "installation", "baseline_id": None if kind == "bragg" else baseline_id, "procedure": procedure, "units": CAB_UNITS, "base": None, "dependency": recipe}
            payload = (json.dumps(descriptor, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
            provenance = {"origin": descriptor["origin"], "source": {"kind": "recipe", "origin": "generated", "sha256": hashlib.sha256(payload).hexdigest(), "size_bytes": len(payload)}, "base": None, "changes": [], "copied_entries": [], "baseline_id": descriptor["baseline_id"], "procedure": {"method": procedure["method"], "version": procedure["version"]}}
            reference = register_resource(paths, config, "cab", kind, None, payload, provenance=provenance, reuse_key=hashlib.sha256(payload).hexdigest())
            resources[kind] = asdict(reference)
            descriptors[kind] = descriptor
            artifacts.append(str(paths.resources / "cab" / kind / reference.version))
        core = ResourceReference(**resources["core"])
        if find_cab_entry(paths, core, descriptors["core"]["dependency"], "z001") is None:
            calculation = {key: descriptors["core"]["procedure"][key] for key in ("method", "version", "precision", "rounding")}
            calculation.update({"convention": "hydrogen-core-zero", "value": "0"})
            publish_cab_entry(paths, core, {"schema_version": 1, "kind": "core", "entry_id": "z001", "value": "0", "units": CAB_UNITS, "dependency": descriptors["core"]["dependency"], "simulations": [], "captures": [], "calculation": calculation})
        document = {"schema_version": 1, "baseline_id": baseline_id, "resources": resources}
        _validate_default_generation(document)
        for source in sources.values():
            if _hash_file(Path(source["path"])) != (source["sha256"], source["size_bytes"]):
                raise ValueError("Installation changed before default publication")
        version_path, version_bytes = read_module_version(config.srim_root)
        if str(version_path) != sources["VERSION"]["path"] or version_bytes != files["VERSION"]:
            raise ValueError("Module version selection changed before default publication")
        with _lock_parent(paths.resources):
            observed = _hash_file(pointer) if os.path.lexists(pointer) else None
            if observed != prior:
                raise ValueError("Default generation changed concurrently; staged versions retained")
            baseline_path = paths.resources / "baselines" / f"{baseline_id}.json"
            for ancestor in (baseline_path.parent, baseline_path):
                if ancestor.is_symlink() or ancestor.is_junction():
                    raise OSError("Baseline path contains a link or junction")
            baseline_path.parent.mkdir(exist_ok=True)
            if os.path.lexists(baseline_path):
                if _hash_file(baseline_path) != (baseline_id, len(encoded)):
                    raise ValueError("Immutable baseline is corrupt")
            else:
                with baseline_path.open("xb") as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
            canonical = (json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
            if observed != (hashlib.sha256(canonical).hexdigest(), len(canonical)):
                _write_json(pointer, document)
        return read_sr_defaults(paths)
    except BaseException as error:
        error.add_note("Default switch retained staged versions: " + ", ".join(artifacts))
        raise
