"""Calculate elemental electronic stopping through ordinary isolated dependency simulations."""

from collections.abc import Callable, Sequence
from copy import deepcopy
from dataclasses import asdict
from decimal import Decimal, localcontext
import hashlib
import json
import math

from src.backend.config import Config
from src.backend.paths import ProjectPaths
from src.backend.records import CreateRequest, CreatedDirectory, _validate_paths, create_directories
from src.backend.resources import ResourceReference, _hash_file, register_resource
from src.srim.cab import _encode_payload
from src.srim.cab_cache import CABEntryReference, CABEntryResult, _dependency_key, find_cab_entry, publish_cab_entry
from src.srim.cab_constants import BRAGG_PROCEDURE_VERSION, CAB_PRECISION, CAB_ROUNDING, CAB_UNITS
from src.srim.execution import run_sr_simulation
from src.srim.material import _encode_atomdata, construct_sr_target
from src.srim.metadata import JSONObject, ResolvedSRInputs, SRDependencyInputs, _read_json
from src.srim.runtime import _prepare_sr_runtime


def _make_bragg_inputs(inputs: SRDependencyInputs, atomic_number: int) -> ResolvedSRInputs:
    """Return pure pinned Bragg intent/effective evidence from inputs for atomic_number.

    Use H at 125 times its isotope mass, one gas element at its natural mass,
    correction one and units seven. Preserve effective native byte snapshots as
    explicit inline dependency sources, avoiding mutable source-path rereads.
    Raise ValueError for invalid bundle/element; perform no I/O or registration.
    """
    if not isinstance(inputs, SRDependencyInputs) or type(atomic_number) is not int or not 1 <= atomic_number <= 92:
        raise ValueError("Bragg requires pinned inputs and an element 1 through 92")
    from src.srim.resolution import _make_dependency_inputs
    _make_dependency_inputs(inputs.defaults, inputs.resources, inputs.atomdata, dict(inputs.engine_files))
    engine_files = dict(inputs.engine_files)
    if len(engine_files) != len(inputs.engine_files) or set(engine_files) != {"SRModule.exe", "VERSION", "SCOEF03.dat", "SNUC03.dat"} or any(not isinstance(value, bytes) for value in engine_files.values()):
        raise ValueError("Invalid pinned Bragg engine inventory")
    atoms = inputs.atomdata
    if len(atoms["rows"]) != 92:
        raise ValueError("Bragg requires complete effective ATOMDATA")
    hydrogen = atoms["rows"][0]["isotope_mass_amu"]
    selected = atoms["rows"][atomic_number - 1]
    with localcontext() as context:
        context.prec, context.rounding = CAB_PRECISION, CAB_ROUNDING
        energy = format(125 * Decimal(hydrogen), "f")
    request = {"schema_version": 2, "parameters": {"projectile": {"atomic_number": 1, "mass_amu": format(Decimal(hydrogen), "f")}, "target": {"source": "direct", "phase": "gas", "density_g_cm3": format(Decimal(selected["gas_density_g_cm3"]), "f"), "elements": [{"atomic_number": atomic_number, "stoichiometry": "1"}], "bonds": {}}, "correction": {"mode": "none", "applied_factor": "1.0"}, "stopping_units": 7, "energy": {"mode": "list", "values_keV": [energy]}}}
    target = construct_sr_target(request["parameters"]["target"], atoms, None)
    effective_request = {"schema_version": 2, "parameters": {**deepcopy(request["parameters"]), "target": target["target"]}}
    files = {"atomdata": _encode_atomdata(atoms), "electronic": engine_files["SCOEF03.dat"], "nuclear": engine_files["SNUC03.dat"], "core": _encode_payload("core", {}), "bond": _encode_payload("bond", {})}
    selections: JSONObject = {"compound": None}
    resources: JSONObject = {}
    snapshots: dict[str, bytes] = {}
    for role, contents in sorted(files.items()):
        selections[role] = {"source": {"kind": "inline", "data": contents.decode("ascii" if role in ("atomdata", "electronic", "nuclear") else "utf-8")}, "overrides": [], "create_version": False}
        manifests = {}
        for part in ("source", "effective"):
            name = f"resources/{role}/{part}"
            snapshots[name] = contents
            manifests[part] = {"path": "input/" + name, "sha256": hashlib.sha256(contents).hexdigest(), "size_bytes": len(contents)}
        resources[role] = {"reference": None, "base": None, "source": {"kind": "inline", "origin": "inline", "snapshot": manifests["source"]}, "overrides": [], "effective": manifests["effective"], "published": None}
    version_lines = engine_files["VERSION"].decode("ascii").splitlines()
    if not version_lines or not version_lines[0].strip():
        raise ValueError("Pinned Bragg VERSION must have a nonblank first line")
    version = version_lines[0].strip()
    sources = [{"id": "sr-module-executable", "name": "SRModule.exe", "version": version, "sha256": hashlib.sha256(engine_files["SRModule.exe"]).hexdigest(), "citation": None}, {"id": "sr-module-version", "name": "VERSION", "version": version, "sha256": hashlib.sha256(engine_files["VERSION"]).hexdigest(), "citation": None}]
    resolved = {"schema_version": 2, "parameters": effective_request["parameters"], "sources": sources, "resources": resources, "cab": request["parameters"]["correction"], "defaults": deepcopy(inputs.defaults), "target_provenance": target["provenance"], "dependencies": []}
    return ResolvedSRInputs(request, selections, effective_request, resolved, tuple(sorted(snapshots.items())), tuple(sorted(engine_files.items())))


def resolve_bragg(paths: ProjectPaths, config: Config, worker: CreatedDirectory, inputs: SRDependencyInputs, atomic_number: int, *, timeout_seconds: float = 60.0) -> CABEntryResult:
    """Return reused/calculated Bragg entry for paths/config/worker and pinned inputs/atomic_number.

    timeout_seconds is a finite positive owned execution limit. Atomically reuse
    the matching concrete recipe version; only missing elements allocate ordinary
    dependency simulations. Publish successful positive electronic stopping with
    exact result/raw evidence. Preserve failed records, propagate errors/interruption
    and never run the caller's main simulation or reassign default aliases.
    """
    if type(timeout_seconds) not in (int, float) or timeout_seconds <= 0 or not math.isfinite(timeout_seconds):
        raise ValueError("Bragg timeout must be positive and finite")
    _validate_paths(paths)
    if not isinstance(worker, CreatedDirectory) or worker.kind != "worker" or worker.timestamp is not None or type(worker.suffix) is not int or worker.suffix < 1 or worker.path != paths.workers / f"worker_{worker.suffix}":
        raise ValueError("Bragg requires an allocated worker")
    for directory in (worker.path, worker.path / "sr", worker.path / "trim"):
        if directory.is_symlink() or directory.is_junction() or not directory.is_dir():
            raise OSError("Bragg worker is missing or linked")
    bundle = _make_bragg_inputs(inputs, atomic_number)
    engine = dict(inputs.engine_files)
    native = {"SRModule.exe": engine["SRModule.exe"], "VERSION": engine["VERSION"], "ATOMDATA": _encode_atomdata(inputs.atomdata), "SCOEF03.dat": engine["SCOEF03.dat"], "SNUC03.dat": engine["SNUC03.dat"]}
    procedure = {"method": "bragg", "version": BRAGG_PROCEDURE_VERSION, "precision": CAB_PRECISION, "rounding": CAB_ROUNDING, "units": CAB_UNITS, "reference": {"projectile": 1, "energy_per_amu_keV": "125", "phase": "gas", "stopping_units": 7, "projectile_mass_field": "isotope_mass_amu", "target_mass_field": "natural_mass_amu"}}
    recipe = {"schema_version": 1, "kind": "bragg", "procedure": procedure, "files": [{"role": role, "sha256": hashlib.sha256(contents).hexdigest(), "size_bytes": len(contents)} for role, contents in sorted(native.items())], "entries": []}
    descriptor = {"schema_version": 1, "kind": "bragg", "origin": "calculated", "baseline_id": None, "procedure": procedure, "units": CAB_UNITS, "base": None, "dependency": recipe}
    payload = (json.dumps(descriptor, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    provenance = {"origin": "calculated", "source": {"kind": "recipe", "origin": "generated", "sha256": hashlib.sha256(payload).hexdigest(), "size_bytes": len(payload)}, "base": None, "changes": [], "copied_entries": [], "baseline_id": None, "procedure": {"method": "bragg", "version": BRAGG_PROCEDURE_VERSION}}
    dataset = register_resource(paths, config, "cab", "bragg", None, payload, provenance=provenance, reuse_key=hashlib.sha256(payload).hexdigest())
    identifier = f"z{atomic_number:03d}"
    cached = find_cab_entry(paths, dataset, recipe, identifier)
    if cached is not None:
        return CABEntryResult(cached, False)
    created = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
    try:
        runtime = _prepare_sr_runtime(paths, config, created, worker, bundle)
        metadata = run_sr_simulation(paths, config, created.path.name, runtime, timeout_seconds=timeout_seconds)
        if metadata["status"] != "succeeded":
            raise RuntimeError(f"Bragg dependency failed for {identifier}: {metadata['failure']}")
        parsed_path = created.path / "output" / "parsed" / "result.json"
        raw_path = created.path / "output" / "raw" / "stopping.txt"
        parsed = _read_json(parsed_path)
        if len(parsed["rows"]) != 1:
            raise ValueError("Bragg reference requires exactly one parsed row")
        value = parsed["rows"][0]["electronic_stopping"]
        if not Decimal(value).is_finite() or Decimal(value) <= 0:
            raise ValueError("Bragg electronic stopping must be positive")
        evidence = {}
        for name, path in (("result", parsed_path), ("raw", raw_path)):
            checksum, size = _hash_file(path)
            evidence[name] = {"kind": "simulation", "id": created.path.name, "path": path.relative_to(created.path).as_posix(), "sha256": checksum, "size_bytes": size}
        parameters = bundle.effective_request["parameters"]
        calculation = {key: procedure[key] for key in ("method", "version", "precision", "rounding")}
        calculation.update({"value_field": "electronic_stopping", "row_index": 1, "energy_keV": parameters["energy"]["values_keV"][0], "projectile_mass_amu": parameters["projectile"]["mass_amu"], "target_mass_amu": parameters["target"]["elements"][0]["mass_amu"], "phase": "gas", "density_g_cm3": parameters["target"]["density_g_cm3"], "stopping_units": 7})
        entry = {"schema_version": 1, "kind": "bragg", "entry_id": identifier, "value": value, "units": CAB_UNITS, "dependency": recipe, "simulations": [{"simulation_id": created.path.name, **evidence}], "captures": [], "calculation": calculation}
        return CABEntryResult(publish_cab_entry(paths, dataset, entry), True)
    except BaseException as error:
        error.add_note(f"Bragg {identifier} retained dependency record: {created.path}")
        raise


def populate_bragg(paths: ProjectPaths, config: Config, worker: CreatedDirectory, inputs: SRDependencyInputs, *, atomic_numbers: Sequence[int] | None = None, timeout_seconds: float = 60.0, progress: Callable[[JSONObject], None] | None = None) -> list[CABEntryReference]:
    """Resolve ordered atomic_numbers (None means 1..92) with paths/config/worker/inputs.

    timeout_seconds bounds each missing simulation; progress reports cached or
    calculated completion outside locks. Return references in selected order.
    Validate the whole requested set first, stop on failure/callback/interruption,
    retain completed entries and raise with completed references in exception notes.
    """
    if atomic_numbers is not None and not isinstance(atomic_numbers, (list, tuple)):
        raise ValueError("Bragg elements must be a sequence or None")
    chosen = list(range(1, 93)) if atomic_numbers is None else list(atomic_numbers)
    if not chosen or any(type(z) is not int or not 1 <= z <= 92 for z in chosen) or len(set(chosen)) != len(chosen):
        raise ValueError("Bragg elements must be unique integers 1 through 92")
    if progress is not None and not callable(progress):
        raise ValueError("progress must be callable or None")
    completed: list[CABEntryReference] = []
    try:
        for z in chosen:
            result = resolve_bragg(paths, config, worker, inputs, z, timeout_seconds=timeout_seconds)
            completed.append(result.reference)
            if progress is not None:
                progress({"kind": "bragg", "entry_id": f"z{z:03d}", "state": "calculated" if result.calculated else "cached", "completed": len(completed), "total": len(chosen)})
        return completed
    except BaseException as error:
        error.add_note("Bragg completed entries retained: " + json.dumps([asdict(reference) for reference in completed], sort_keys=True))
        raise
