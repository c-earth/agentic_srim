"""Validate layered TRIM intent and render deterministic native input/event bytes."""

from collections.abc import Sequence
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import re

from src.srim.metadata import JSONObject, JSONValue


MODES = {
    "2008": {"ion-quick": 1, "ion-full": 2, "ion-monolayer": 3, "specified-ion-quick": 4, "specified-ion-full": 5, "recoil-quick": 6, "recoil-full": 7},
    "2013": {"ion-quick": 1, "ion-full": 2, "ion-monolayer": 3, "specified-ion-quick": 4, "specified-ion-full": 5, "recoil-quick": 8, "recoil-full": 6, "recoil-monolayer": 7},
}
PLOTS = ("xy-ions-recoils", "xz-ions-recoils", "xy-ions", "yz-ions-recoils", "all", "none")
ENERGIES = ("displacement_eV", "lattice_binding_eV", "surface_binding_eV")


def _decimal(value: JSONValue, field: str, *, minimum: Decimal | None = None, strict: bool = False) -> Decimal:
    """Return exact decimal-string value for field subject to minimum/strict bounds.

    Reject malformed/nonfinite strings or an inclusive/strict minimum violation
    with ValueError identifying field. No mutation, rounding, maximum or I/O.
    """
    if not isinstance(value, str) or re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", value) is None:
        raise ValueError(f"{field}: expected a finite decimal string")
    try:
        number = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"{field}: invalid decimal") from error
    if not number.is_finite() or (minimum is not None and (number < minimum or (strict and number == minimum))):
        raise ValueError(f"{field}: outside the required semantic range")
    return number


def _text(value: JSONValue, field: str, *, ascii_only: bool = False) -> str:
    """Return unchanged single-record field text in ASCII or CP1252.

    value must be a nonempty string without control characters. Raise ValueError
    on representation/record failures; impose no length cap and perform no I/O.
    """
    if not isinstance(value, str) or not value or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value):
        raise ValueError(f"{field}: expected nonempty single-record text")
    try:
        value.encode("ascii" if ascii_only else "cp1252")
    except UnicodeError as error:
        raise ValueError(f"{field}: text cannot be represented in the native encoding") from error
    return value


def validate_trim_request(request: JSONObject) -> None:
    """Validate high-level request's exact TRIM intent without mutation or I/O.

    Check required fields, scalar representations, layer materials, correction
    forms, sparse component overrides and event relationships. Return None;
    raise ValueError with a field path on malformed or known-unsafe inputs.
    Unknown native count/size/energy limits are deliberately not imposed.
    """
    if not isinstance(request, dict) or set(request) != {"schema_version", "engine", "parameters"} or type(request["schema_version"]) is not int or request["schema_version"] != 1 or request["engine"] != "trim":
        raise ValueError("request: expected version-one TRIM intent")
    p = request["parameters"]
    if not isinstance(p, dict) or set(p) != {"name", "mode", "projectile", "total_ions", "random_seed", "autosave_ions", "outputs", "plot", "layers", "events"}:
        raise ValueError("parameters: invalid fields")
    _text(p["name"], "name", ascii_only=p["events"] is not None)
    if not isinstance(p["mode"], str) or p["mode"] not in MODES["2013"]:
        raise ValueError("mode: unsupported descriptive mode")
    projectile = p["projectile"]
    if not isinstance(projectile, dict) or set(projectile) != {"atomic_number", "mass_amu", "energy_keV", "angle_degrees"}:
        raise ValueError("projectile: invalid fields")
    if type(projectile["atomic_number"]) is not int or projectile["atomic_number"] < 1:
        raise ValueError("projectile.atomic_number: expected positive integer")
    if projectile["mass_amu"] is not None:
        _decimal(projectile["mass_amu"], "projectile.mass_amu", minimum=Decimal(0), strict=True)
    _decimal(projectile["energy_keV"], "projectile.energy_keV", minimum=Decimal(0), strict=True)
    if _decimal(projectile["angle_degrees"], "projectile.angle_degrees") == 90:
        raise ValueError("projectile.angle_degrees: native TRIM silently substitutes 0 for 90 degrees")
    for field in ("total_ions", "random_seed", "autosave_ions"):
        if type(p[field]) is not int or (field == "total_ions" and p[field] < 1):
            raise ValueError(f"{field}: invalid integer")
    outputs = p["outputs"]
    if not isinstance(outputs, dict) or set(outputs) != {"ranges", "backscatter", "transmission", "sputtering", "collisions", "exyz_increment_eV"}:
        raise ValueError("outputs: invalid fields")
    for field in ("ranges", "backscatter", "transmission", "sputtering"):
        if type(outputs[field]) is not bool:
            raise ValueError(f"outputs.{field}: expected boolean")
    if outputs["collisions"] not in ("none", "ions", "ions-and-recoils"):
        raise ValueError("outputs.collisions: invalid choice")
    _decimal(outputs["exyz_increment_eV"], "outputs.exyz_increment_eV", minimum=Decimal(0))
    plot = p["plot"]
    if not isinstance(plot, dict) or set(plot) != {"type", "minimum_depth_angstrom", "maximum_depth_angstrom"} or plot["type"] not in PLOTS:
        raise ValueError("plot: invalid fields/type")
    lower = _decimal(plot["minimum_depth_angstrom"], "plot.minimum_depth_angstrom", minimum=Decimal(0))
    upper = _decimal(plot["maximum_depth_angstrom"], "plot.maximum_depth_angstrom", minimum=Decimal(0))
    if (lower != 0 or upper != 0) and upper <= lower:
        raise ValueError("plot: maximum must exceed minimum, or both must be zero")
    if not isinstance(p["layers"], list) or not p["layers"]:
        raise ValueError("layers: expected nonempty list")
    for index, layer in enumerate(p["layers"], 1):
        path = f"layers[{index}]"
        if not isinstance(layer, dict) or set(layer) != {"name", "thickness_angstrom", "material", "correction", "overrides"}:
            raise ValueError(f"{path}: invalid fields")
        _text(layer["name"], path + ".name")
        _decimal(layer["thickness_angstrom"], path + ".thickness_angstrom", minimum=Decimal(0), strict=True)
        material = layer["material"]
        if not isinstance(material, dict) or material.get("phase") not in ("solid", "gas"):
            raise ValueError(f"{path}.material: explicit solid/gas phase required")
        if material.get("source") == "compound":
            if set(material) != {"source", "phase", "record_index"} or type(material["record_index"]) is not int or material["record_index"] < 1:
                raise ValueError(f"{path}.material: invalid compound selector")
        elif material.get("source") == "direct":
            if not {"source", "phase", "density_g_cm3", "elements"} <= set(material) or set(material) - {"source", "phase", "density_g_cm3", "elements", "bonds"}:
                raise ValueError(f"{path}.material: invalid direct fields")
            _decimal(material["density_g_cm3"], path + ".density_g_cm3", minimum=Decimal(0), strict=True)
            if not isinstance(material["elements"], list) or not material["elements"]:
                raise ValueError(f"{path}.material.elements: expected nonempty list")
            for component, atom in enumerate(material["elements"], 1):
                if not isinstance(atom, dict) or set(atom) != {"atomic_number", "stoichiometry"} or type(atom["atomic_number"]) is not int or atom["atomic_number"] < 1:
                    raise ValueError(f"{path}.components[{component}]: invalid identity")
                _decimal(atom["stoichiometry"], f"{path}.components[{component}].stoichiometry", minimum=Decimal(0), strict=True)
            if "bonds" in material:
                if not isinstance(material["bonds"], dict):
                    raise ValueError(f"{path}.material.bonds: expected mapping")
                from src.srim.cab import _normalize_entries
                _normalize_entries("bond", material["bonds"])
                for key, value in material["bonds"].items():
                    _decimal(value, f"{path}.material.bonds[{key}]", minimum=Decimal(0))
        else:
            raise ValueError(f"{path}.material: source must be direct or compound")
        correction = layer["correction"]
        if not isinstance(correction, dict):
            raise ValueError(f"{path}.correction: expected object")
        if correction.get("mode") == "derived":
            if set(correction) != {"mode"} or (material["source"] == "direct" and "bonds" not in material):
                raise ValueError(f"{path}.correction: derived requires explicit bonds and no supplied factor")
        elif correction.get("mode") == "none":
            if correction != {"mode": "none", "applied_factor": "1.0"}:
                raise ValueError(f"{path}.correction: none requires exact factor 1.0")
        elif correction.get("mode") == "supplied":
            if set(correction) != {"mode", "applied_factor", "source", "reason"}:
                raise ValueError(f"{path}.correction: invalid supplied fields")
            _decimal(correction["applied_factor"], path + ".correction.applied_factor", minimum=Decimal(0), strict=True)
            for field in ("source", "reason"):
                if not isinstance(correction[field], str) or not correction[field].strip():
                    raise ValueError(f"{path}.correction.{field}: expected nonblank string")
        else:
            raise ValueError(f"{path}.correction: invalid mode")
        overrides = layer["overrides"]
        if not isinstance(overrides, dict) or set(overrides) != {"density_g_cm3", "components"} or not isinstance(overrides["components"], list):
            raise ValueError(f"{path}.overrides: invalid fields")
        if overrides["density_g_cm3"] is not None:
            _decimal(overrides["density_g_cm3"], path + ".overrides.density_g_cm3", minimum=Decimal(0), strict=True)
        seen: set[int] = set()
        for override in overrides["components"]:
            if not isinstance(override, dict) or "component_index" not in override or not set(override) - {"component_index"} or set(override) - {"component_index", "mass_amu", *ENERGIES}:
                raise ValueError(f"{path}.overrides.components: invalid fields")
            number = override["component_index"]
            if type(number) is not int or number < 1 or number in seen:
                raise ValueError(f"{path}.overrides.components: invalid/duplicate index")
            if material["source"] == "direct" and number > len(material["elements"]):
                raise ValueError(f"{path}.overrides.components: missing component {number}")
            seen.add(number)
            for field in set(override) - {"component_index"}:
                _decimal(override[field], f"{path}.overrides[{number}].{field}", minimum=Decimal(0), strict=field == "mass_amu")
    events = p["events"]
    if p["mode"].startswith("ion-"):
        if events is not None:
            raise ValueError("events: ordinary ion modes require null")
    else:
        if not isinstance(events, list) or not events:
            raise ValueError("events: specified-ion/recoil modes require nonempty list")
        for index, event in enumerate(events, 1):
            path = f"events[{index}]"
            if not isinstance(event, dict) or set(event) != {"id", "atomic_number", "energy_eV", "position_angstrom", "direction_cosines"}:
                raise ValueError(f"{path}: invalid fields")
            identifier = _text(event["id"], path + ".id", ascii_only=True)
            if any(char.isspace() or char in ',\"\'' for char in identifier):
                raise ValueError(f"{path}.id: expected a native token")
            if type(event["atomic_number"]) is not int or event["atomic_number"] < 1:
                raise ValueError(f"{path}.atomic_number: expected positive integer")
            _decimal(event["energy_eV"], path + ".energy_eV", minimum=Decimal(0), strict=True)
            for field in ("position_angstrom", "direction_cosines"):
                if not isinstance(event[field], list) or len(event[field]) != 3:
                    raise ValueError(f"{path}.{field}: expected three coordinates")
                for axis, value in zip("XYZ", event[field], strict=True):
                    _decimal(value, f"{path}.{field}.{axis}")


def validate_trim_engine_request(request: JSONObject) -> None:
    """Validate complete explicit request and native matrix without resources or I/O.

    Require supplied masses/energies/profile, a supported family/mode map and a
    consistent nonempty layer/atom matrix. Return None or raise ValueError with
    context. Reuse high-level scalar/event checks without inherited SR caps.
    """
    if not isinstance(request, dict) or set(request) != {"schema_version", "engine", "parameters"} or not isinstance(request["parameters"], dict):
        raise ValueError("request: invalid explicit TRIM envelope")
    p = request["parameters"]
    fields = {"name", "mode", "projectile", "total_ions", "random_seed", "autosave_ions", "outputs", "plot", "layers", "events", "engine_family", "native_fields", "atoms"}
    if set(p) != fields or p["engine_family"] not in ("2008", "2013") or not isinstance(p["mode"], str) or p["mode"] not in MODES[p["engine_family"]]:
        raise ValueError("parameters: invalid explicit fields or family/mode mapping")
    native = p["native_fields"]
    if not isinstance(native, dict) or set(native) != {"legacy_bragg", "reminders", "stopping_version"} or any(type(native[key]) is not int for key in ("reminders", "stopping_version")):
        raise ValueError("native_fields: invalid fixed fields")
    _decimal(native["legacy_bragg"], "native_fields.legacy_bragg")
    if not isinstance(p["atoms"], list) or not p["atoms"] or not isinstance(p["layers"], list) or not p["layers"]:
        raise ValueError("atoms/layers: expected nonempty lists")
    for index, atom in enumerate(p["atoms"], 1):
        if not isinstance(atom, dict) or set(atom) != {"atomic_number", "mass_amu", *ENERGIES} or type(atom["atomic_number"]) is not int or atom["atomic_number"] < 1:
            raise ValueError(f"atoms[{index}]: invalid fields/identity")
        for field in ("mass_amu", *ENERGIES):
            _decimal(atom[field], f"atoms[{index}].{field}", minimum=Decimal(0), strict=field == "mass_amu")
    used: set[int] = set()
    projected = deepcopy(request)
    projected["parameters"] = {key: deepcopy(value) for key, value in p.items() if key not in ("engine_family", "native_fields", "atoms", "layers")}
    projected["parameters"]["layers"] = []
    for index, layer in enumerate(p["layers"], 1):
        if not isinstance(layer, dict) or set(layer) != {"name", "thickness_angstrom", "density_g_cm3", "phase", "correction_factor", "stoichiometry"} or not isinstance(layer["stoichiometry"], list) or len(layer["stoichiometry"]) != len(p["atoms"]):
            raise ValueError(f"layers[{index}]: invalid native matrix")
        elements: list[JSONObject] = []
        for number, value in enumerate(layer["stoichiometry"]):
            if _decimal(value, f"layers[{index}].stoichiometry[{number + 1}]", minimum=Decimal(0)) > 0:
                used.add(number)
                elements.append({"atomic_number": p["atoms"][number]["atomic_number"], "stoichiometry": value})
        _decimal(layer["correction_factor"], f"layers[{index}].correction_factor", minimum=Decimal(0), strict=True)
        projected["parameters"]["layers"].append({"name": layer["name"], "thickness_angstrom": layer["thickness_angstrom"], "material": {"source": "direct", "phase": layer["phase"], "density_g_cm3": layer["density_g_cm3"], "elements": elements}, "correction": {"mode": "supplied", "applied_factor": layer["correction_factor"], "source": "resolved", "reason": "explicit native input"}, "overrides": {"density_g_cm3": None, "components": []}})
    if len(used) != len(p["atoms"]):
        raise ValueError("atoms: every entry must occur in at least one layer")
    validate_trim_request(projected)
    _decimal(p["projectile"]["mass_amu"], "projectile.mass_amu", minimum=Decimal(0), strict=True)


def flatten_trim_target(layers: Sequence[JSONObject]) -> tuple[list[JSONObject], list[JSONObject], list[JSONObject]]:
    """Return ordered atoms/native layers/index map for resolved layers without I/O.

    Each layer supplies numeric components and correction_factor. Preserve one
    native entry per component, including equal elements across layers. Raise
    ValueError for missing/incomplete fields; never mutate caller data.
    """
    if not isinstance(layers, (list, tuple)) or not layers:
        raise ValueError("layers: expected resolved nonempty sequence")
    atoms: list[JSONObject] = []
    native_layers: list[JSONObject] = []
    mapping: list[JSONObject] = []
    total = 0
    for layer in layers:
        if not isinstance(layer, dict) or set(layer) != {"name", "thickness_angstrom", "density_g_cm3", "phase", "correction_factor", "components"} or not isinstance(layer["components"], list) or not layer["components"]:
            raise ValueError("layers: incomplete resolved layer")
        total += len(layer["components"])
    for layer_index, layer in enumerate(layers, 1):
        stoichiometry = ["0"] * total
        for component_index, component in enumerate(layer["components"], 1):
            if not isinstance(component, dict) or set(component) != {"atomic_number", "mass_amu", "stoichiometry", *ENERGIES}:
                raise ValueError("layers: incomplete resolved component")
            _decimal(component["stoichiometry"], "component.stoichiometry", minimum=Decimal(0), strict=True)
            stoichiometry[len(atoms)] = component["stoichiometry"]
            atoms.append({key: component[key] for key in ("atomic_number", "mass_amu", *ENERGIES)})
            mapping.append({"layer_index": layer_index, "component_index": component_index, "atom_index": len(atoms)})
        native_layers.append({**{key: deepcopy(layer[key]) for key in ("name", "thickness_angstrom", "density_g_cm3", "phase", "correction_factor")}, "stoichiometry": stoichiometry})
    return atoms, native_layers, mapping


def render_trim_input(request: JSONObject) -> bytes:
    """Return validated explicit request as CP1252 CRLF TRIM.IN without I/O.

    Preserve values exactly while expanding decimal exponents without rounding.
    Use fixed headings, 16-character ignored atom prefixes and doubled embedded
    name quotes. Raise ValueError for invalid/unsupported representation.
    """
    validate_trim_engine_request(request)
    p = request["parameters"]
    ion = p["projectile"]
    native = p["native_fields"]
    outputs = p["outputs"]
    plot = p["plot"]
    lines = [
        "==> TRIM input generated from pinned explicit parameters.",
        "Ion: Z1, M1, Energy (keV), Angle, Number, Bragg Corr, AutoSave Number.",
        " ".join((str(ion["atomic_number"]), format(Decimal(ion["mass_amu"]), "f"), format(Decimal(ion["energy_keV"]), "f"), format(Decimal(ion["angle_degrees"]), "f"), str(p["total_ions"]), native["legacy_bragg"], str(p["autosave_ions"]))),
        "Cascades, Random Number Seed, Reminders",
        f'{MODES[p["engine_family"]][p["mode"]]} {p["random_seed"]} {native["reminders"]}',
        "Diskfiles: Ranges, Backscatter, Transmit, Sputtered, Collisions, EXYZ increment (eV)",
        " ".join([str(int(outputs[key])) for key in ("ranges", "backscatter", "transmission", "sputtering")] + [str(("none", "ions", "ions-and-recoils").index(outputs["collisions"])), format(Decimal(outputs["exyz_increment_eV"]), "f")]),
        "Target material: Number of Elements and Layers",
        '"' + p["name"].replace('"', '""') + f'" {len(p["atoms"])} {len(p["layers"])}',
        "PlotType (0-5), Plot Depths: minimum/maximum (Angstrom)",
        f'{PLOTS.index(plot["type"])} {format(Decimal(plot["minimum_depth_angstrom"]), "f")} {format(Decimal(plot["maximum_depth_angstrom"]), "f")}',
        "Target Elements: Z Mass(amu)",
    ]
    for index, atom in enumerate(p["atoms"], 1):
        lines.append(f"Atom {index}"[:16].ljust(16) + f'{atom["atomic_number"]} {format(Decimal(atom["mass_amu"]), "f")}')
    lines.extend(("Layer Layer Name Width Density Stoichiometry", "Number Description (Angstrom) (g/cm3) Atom entries"))
    for index, layer in enumerate(p["layers"], 1):
        lines.append(str(index) + ' "' + layer["name"].replace('"', '""') + '" ' + " ".join(format(Decimal(value), "f") for value in (layer["thickness_angstrom"], layer["density_g_cm3"], *layer["stoichiometry"])))
    lines.extend(("0 Target layer phases (0=Solid, 1=Gas)", " ".join(str(int(layer["phase"] == "gas")) for layer in p["layers"]), "Target Compound Corrections (Bragg)", " ".join(format(Decimal(layer["correction_factor"]), "f") for layer in p["layers"])))
    for field, heading in zip(ENERGIES, ("Individual target atom displacement energies (eV)", "Individual target atom lattice binding energies (eV)", "Individual target atom surface binding energies (eV)"), strict=True):
        lines.extend((heading, " ".join(format(Decimal(atom[field]), "f") for atom in p["atoms"])))
    lines.extend(("Stopping Power Version (copied from installed input)", str(native["stopping_version"])))
    return ("\r\n".join(lines) + "\r\n").encode("cp1252")


def render_trim_events(request: JSONObject) -> bytes | None:
    """Return validated explicit request's ASCII CRLF event file or None without I/O.

    Preserve event order/IDs and vectors; place description at header line eight,
    use eight numeric fields and terminal 0x1A. Raise ValueError for invalid input.
    """
    validate_trim_engine_request(request)
    p = request["parameters"]
    if p["events"] is None:
        return None
    lines = ["TRIM.DAT event input", "Units: eV and Angstrom", "Coordinates: X Y Z", "Directions: CosX CosY CosZ", "ID Z Energy X Y Z CosX CosY CosZ", "Event order preserved", "User calculation description follows", p["name"], "Eight numeric fields per event", "Event data follows"]
    for event in p["events"]:
        lines.append(event["id"] + " " + str(event["atomic_number"]) + " " + " ".join(format(Decimal(value), "f") for value in (event["energy_eV"], *event["position_angstrom"], *event["direction_cosines"])))
    return ("\r\n".join(lines) + "\r\n").encode("ascii") + b"\x1a"
