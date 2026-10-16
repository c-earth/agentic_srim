"""Accept explicit JSON TRIM specifications; expose typed validated inputs.

Lengths use nm, energies use keV for the beam and eV for target/particle data.
Composition uses relative atomic amounts. Every scientific field is required.
"""

from dataclasses import asdict, dataclass
from fractions import Fraction
import math
import re

from agentic_srim.domain.specifications import atomic_number, finite, integer, positive


MODES = {1: "ions_quick_damage", 2: "ions_full_cascades", 3: "monolayer_sputtering",
         4: "particle_list_quick_damage", 5: "particle_list_full_cascades",
         6: "recoil_list_full_cascades", 7: "recoil_list_monolayer_cascades",
         8: "recoil_list_quick_damage"}


def label(value: object, name: str, maximum: int) -> str:
    """Return a bounded printable ASCII input label, excluding native delimiters."""
    if not isinstance(value, str) or not 1 <= len(value) <= maximum or not re.fullmatch(r"[A-Za-z0-9 @$_().,+/-]+", value) or not value.strip():
        raise ValueError(f"{name} must contain 1..{maximum} safe ASCII characters")
    return value


def length_nm(value: object, name: str, positive_only: bool = False) -> float:
    """Validate a named nm input; return its float with finite native Angstrom conversion.

    positive_only requires a strictly positive input. Values retain nm units;
    conversion is checked without imposing an independent scientific upper bound.
    """
    result = finite(value, name, positive_only=positive_only)
    if not math.isfinite(result * 10):
        raise ValueError(f"{name} must remain finite when converted from nm to Angstroms")
    return result


@dataclass(frozen=True)
class Element:
    """Construct from element identity, mass in u, and three damage energies in eV.

    Exposes an independently tracked constituent. Duplicate Z entries are allowed
    for layer mixing studies; labels identify the distinct native columns.
    """

    atomic_number: int
    name: str
    mass_u: float
    displacement_eV: float
    lattice_binding_eV: float
    surface_binding_eV: float

    def __post_init__(self) -> None:
        """Validate all element construction inputs; return nothing."""
        atomic_number(self.atomic_number)
        label(self.name, "element name", 8)
        for key in ("mass_u", "displacement_eV"):
            object.__setattr__(self, key, positive(getattr(self, key), key))
        for key in ("lattice_binding_eV", "surface_binding_eV"):
            value = finite(getattr(self, key), key)
            if value < 0:
                raise ValueError(f"{key} cannot be negative")
            object.__setattr__(self, key, value)


@dataclass(frozen=True)
class Layer:
    """Construct layer name, width nm, density g/cm3, composition, phase, correction.

    Exposes a target layer; amounts correspond to the ordered element columns.
    """

    name: str
    width_nm: float
    density_g_cm3: float
    atomic_amounts: tuple[float, ...]
    phase: str
    compound_correction: float

    def __post_init__(self) -> None:
        """Validate layer inputs and atomic amounts; return nothing."""
        label(self.name, "layer name", 32)
        object.__setattr__(self, "width_nm", length_nm(self.width_nm, "width_nm", positive_only=True))
        for key in ("density_g_cm3", "compound_correction"):
            object.__setattr__(self, key, positive(getattr(self, key), key))
        if self.phase not in ("solid", "gas"):
            raise ValueError("phase must be solid or gas")
        if not isinstance(self.atomic_amounts, tuple) or not self.atomic_amounts:
            raise ValueError("atomic_amounts must be a nonempty tuple")
        amounts = tuple(finite(v, "atomic amount") for v in self.atomic_amounts)
        if any(v < 0 for v in amounts) or not any(v > 0 for v in amounts) or not math.isfinite(sum(amounts)):
            raise ValueError("Each layer needs nonnegative atomic amounts with a positive total")
        object.__setattr__(self, "atomic_amounts", amounts)


@dataclass(frozen=True)
class Particle:
    """Construct event label, Z, energy eV, position nm, and three direction cosines.

    Exposes one TRIM.DAT event; normalization is per event, not per external primary.
    """

    event: str
    atomic_number: int
    energy_eV: float
    position_nm: tuple[float, float, float]
    direction: tuple[float, float, float]

    def __post_init__(self) -> None:
        """Validate event construction inputs and direction normalization; return nothing."""
        if not isinstance(self.event, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,5}", self.event):
            raise ValueError("event must contain 1..5 ASCII letters/digits/underscore/hyphen")
        atomic_number(self.atomic_number)
        object.__setattr__(self, "energy_eV", positive(self.energy_eV, "energy_eV"))
        for key in ("position_nm", "direction"):
            value = getattr(self, key)
            if not isinstance(value, tuple) or len(value) != 3:
                raise ValueError(f"{key} must contain three numbers")
            validate = length_nm if key == "position_nm" else finite
            object.__setattr__(self, key, tuple(validate(v, f"{key}[{i}]") for i, v in enumerate(value)))
        if self.position_nm[0] < 0:
            raise ValueError("Particle depth cannot be negative")
        if not math.isclose(sum(v * v for v in self.direction), 1, abs_tol=0.0001):
            raise ValueError("direction must contain normalized direction cosines")


@dataclass(frozen=True)
class OutputOptions:
    """Construct four diskfile switches, collision level, and EXYZ interval in eV.

    Exposes native output selection; collisions 0/1/2 mean off/ion/ion+recoils.
    EXYZ zero disables the file; a positive integer selects its energy interval.
    collision_records selects summary/every for mode 8 with collisions=1; None
    means inapplicable and never supplies a choice for an applicable request.
    """

    ranges: bool
    backscatter: bool
    transmit: bool
    sputter: bool
    collisions: int
    exyz_interval_eV: int
    collision_records: str | None = None

    def __post_init__(self) -> None:
        """Validate output switches and native integer controls; return nothing."""
        if any(type(getattr(self, key)) is not bool for key in ("ranges", "backscatter", "transmit", "sputter")):
            raise ValueError("Diskfile switches must be booleans")
        integer(self.collisions, "collisions", 0, 2)
        integer(self.exyz_interval_eV, "exyz_interval_eV", 0, 2147483647)
        if self.collision_records is not None and self.collision_records not in ("summary", "every"):
            raise ValueError("collision_records must be summary, every, or null when inapplicable")


@dataclass(frozen=True)
class Request:
    """Construct a complete version-1 TRIM transport or external-recoil request.

    Exposes scientific inputs with sources; no implicit materials, seeds, sample
    counts, corrections, or damage energies are selected. Modes 4..8 require an
    explicit event list whose length equals ion_count. Modes 6..8 simulate target
    recoils, not neutron/electron/photon transport or nuclear reactions.
    """

    schema_version: int
    title: str
    ion_atomic_number: int
    ion_mass_u: float
    energy_keV: float
    angle_degrees: float
    ion_count: int
    random_seed: int
    calculation_mode: int
    elements: tuple[Element, ...]
    layers: tuple[Layer, ...]
    depth_min_nm: float
    depth_max_nm: float
    stopping_power_version: int
    outputs: OutputOptions
    particles: tuple[Particle, ...]
    sources: dict[str, str]

    def __post_init__(self) -> None:
        """Validate construction inputs, dimensions, and cross-field constraints; return nothing."""
        integer(self.schema_version, "schema_version", 1, 1)
        label(self.title, "title", 80)
        atomic_number(self.ion_atomic_number)
        for key in ("ion_mass_u", "energy_keV"):
            object.__setattr__(self, key, positive(getattr(self, key), key))
        angle = finite(self.angle_degrees, "angle_degrees")
        if not 0 <= angle <= 89.9:
            raise ValueError("angle_degrees must lie in [0, 89.9]")
        object.__setattr__(self, "angle_degrees", angle)
        integer(self.ion_count, "ion_count", 1, 9999999)
        integer(self.random_seed, "random_seed", 0, 999999999)
        integer(self.calculation_mode, "calculation_mode", 1, 8)
        integer(self.stopping_power_version, "stopping_power_version", 0, 1)
        if not isinstance(self.elements, tuple) or not 1 <= len(self.elements) <= 12 or not all(isinstance(v, Element) for v in self.elements):
            raise ValueError("elements must contain 1..12 typed constituents")
        if len({v.name for v in self.elements}) != len(self.elements):
            raise ValueError("Element labels must be unique, including duplicate Z constituents")
        if not isinstance(self.layers, tuple) or not 1 <= len(self.layers) <= 8 or not all(isinstance(v, Layer) for v in self.layers):
            raise ValueError("layers must contain 1..8 typed layers")
        if any(len(v.atomic_amounts) != len(self.elements) for v in self.layers):
            raise ValueError("Every layer must specify one amount per element")
        if any(not any(layer.atomic_amounts[i] > 0 for layer in self.layers) for i in range(len(self.elements))):
            raise ValueError("Every element must occur in at least one layer")
        for key in ("depth_min_nm", "depth_max_nm"):
            object.__setattr__(self, key, length_nm(getattr(self, key), key))
        total = sum(layer.width_nm for layer in self.layers)
        if not math.isfinite(total) or not ((self.depth_min_nm == self.depth_max_nm == 0) or 0 <= self.depth_min_nm < self.depth_max_nm <= total):
            raise ValueError("Depth window must be 0,0 or an increasing interval inside the target")
        if not isinstance(self.outputs, OutputOptions):
            raise ValueError("outputs must be typed OutputOptions")
        if self.calculation_mode in (1, 4, 8) and self.outputs.collisions == 2:
            raise ValueError("KP modes 1/4/8 cannot write full cascade details; choose collisions 0 or 1")
        if self.calculation_mode == 6 and self.outputs.collisions:
            raise ValueError("Installed TRIM mode 6 rejects the per-cascade COLLISON.txt option; choose collisions 0")
        if self.calculation_mode == 8 and self.outputs.collisions == 1:
            if self.outputs.collision_records is None:
                raise ValueError("Mode 8 with collisions 1 requires collision_records: summary or every")
        elif self.outputs.collision_records is not None:
            raise ValueError("collision_records applies only to mode 8 with collisions 1")
        if not isinstance(self.particles, tuple) or not all(isinstance(v, Particle) for v in self.particles):
            raise ValueError("particles must be a tuple of typed events")
        if self.calculation_mode <= 3 and self.particles:
            raise ValueError("Modes 1..3 do not read TRIM.DAT")
        if self.calculation_mode >= 4:
            if len(self.particles) != self.ion_count or len(self.particles) > 99999:
                raise ValueError("Modes 4..8 require 1..99999 events and ion_count equal to event count")
            for particle in self.particles:
                if particle.energy_eV > self.energy_keV * 1000:
                    raise ValueError("Beam energy must cover the maximum TRIM.DAT event energy")
                if not particle.position_nm[0] < total:
                    raise ValueError("Every event must start inside the target")
                if self.calculation_mode <= 5 and particle.atomic_number != self.ion_atomic_number:
                    raise ValueError("Modes 4/5 events must match the declared ion Z and mass")
                if self.calculation_mode >= 6:
                    bottom = 0.0
                    for layer in self.layers:
                        bottom += layer.width_nm
                        if particle.position_nm[0] < bottom:
                            if not any(e.atomic_number == particle.atomic_number and amount > 0 for e, amount in zip(self.elements, layer.atomic_amounts)):
                                raise ValueError("Recoil species must occur in the event's starting layer")
                            break
        if not isinstance(self.sources, dict) or set(self.sources) != {"ion", "material", "calculation", "particles"} or not all(isinstance(v, str) and v.strip() for v in self.sources.values()):
            raise ValueError("sources requires nonempty ion, material, calculation, particles descriptions")

    @classmethod
    def from_dict(cls, data: dict) -> "Request":
        """Parse an explicit JSON object; return a validated TRIM request without defaults."""
        if not isinstance(data, dict):
            raise ValueError("TRIM request must be an object")
        values = dict(data)
        try:
            for key in ("elements", "layers", "particles"):
                if not isinstance(values[key], (list, tuple)):
                    raise TypeError(f"{key} must be an array")
            values["elements"] = tuple(Element(**v) for v in values["elements"])
            values["layers"] = tuple(Layer(**(v | {"atomic_amounts": tuple(v["atomic_amounts"])})) for v in values["layers"])
            values["particles"] = tuple(Particle(**(v | {"position_nm": tuple(v["position_nm"]), "direction": tuple(v["direction"])})) for v in values["particles"])
            values["outputs"] = OutputOptions(**values["outputs"])
            return cls(**values)
        except (KeyError, TypeError) as exc:
            raise ValueError(f"Invalid TRIM request fields: {exc}") from exc

    def to_dict(self) -> dict:
        """Accept no inputs; return JSON-compatible request data including sources."""
        return asdict(self)

    def physical_spec(self) -> dict:
        """Accept no inputs; return physical inputs excluding presentation/sampling controls.

        Element column order preserves distinct tracked species. Fractions normalize
        proportional compositions. Count/seed and viewing/output options belong in
        the execution hash; the complete event list remains a physical input.
        """
        result = self.to_dict()
        for key in ("title", "sources", "ion_count", "random_seed", "outputs", "depth_min_nm", "depth_max_nm"):
            result.pop(key)
        for element in result["elements"]:
            element.pop("name")
        for layer in result["layers"]:
            layer.pop("name")
            amounts = [Fraction(str(v)) for v in layer.pop("atomic_amounts")]
            layer["atomic_fractions"] = [str(v / sum(amounts)) for v in amounts]
        for particle in result["particles"]:
            particle.pop("event")
        return result


def object_schema(properties: dict) -> dict:
    """Return a closed JSON object schema requiring every input property."""
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


NUMBER = {"type": "number"}
POSITIVE = {"type": "number", "exclusiveMinimum": 0}
VECTOR = {"type": "array", "minItems": 3, "maxItems": 3, "items": NUMBER}
ELEMENT_SCHEMA = object_schema({"atomic_number": {"type": "integer", "minimum": 1, "maximum": 92},
                                "name": {"type": "string", "minLength": 1, "maxLength": 8},
                                "mass_u": POSITIVE, "displacement_eV": POSITIVE,
                                "lattice_binding_eV": {"type": "number", "minimum": 0},
                                "surface_binding_eV": {"type": "number", "minimum": 0}})
LAYER_SCHEMA = object_schema({"name": {"type": "string", "minLength": 1, "maxLength": 32},
                              "width_nm": POSITIVE, "density_g_cm3": POSITIVE,
                              "atomic_amounts": {"type": "array", "minItems": 1, "maxItems": 12, "items": {"type": "number", "minimum": 0}},
                              "phase": {"enum": ["solid", "gas"], "type": "string"}, "compound_correction": POSITIVE})
PARTICLE_SCHEMA = object_schema({"event": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,5}$"},
                                 "atomic_number": {"type": "integer", "minimum": 1, "maximum": 92},
                                 "energy_eV": POSITIVE, "position_nm": VECTOR, "direction": VECTOR})
OUTPUT_SCHEMA = object_schema({**{k: {"type": "boolean"} for k in ("ranges", "backscatter", "transmit", "sputter")},
                               "collisions": {"type": "integer", "minimum": 0, "maximum": 2},
                               "exyz_interval_eV": {"type": "integer", "minimum": 0, "maximum": 2147483647},
                               "collision_records": {"type": ["string", "null"], "enum": ["summary", "every", None],
                                                     "description": "Required for mode 8/collisions=1: summary answers No; every answers Yes. Null/omitted otherwise."}})
OUTPUT_SCHEMA["required"].remove("collision_records")
REQUEST_SCHEMA = object_schema({
    "schema_version": {"type": "integer", "const": 1}, "title": {"type": "string", "minLength": 1, "maxLength": 80},
    "ion_atomic_number": {"type": "integer", "minimum": 1, "maximum": 92}, "ion_mass_u": POSITIVE,
    "energy_keV": POSITIVE, "angle_degrees": {"type": "number", "minimum": 0, "maximum": 89.9},
    "ion_count": {"type": "integer", "minimum": 1, "maximum": 9999999},
    "random_seed": {"type": "integer", "minimum": 0, "maximum": 999999999},
    "calculation_mode": {"type": "integer", "enum": list(MODES)},
    "elements": {"type": "array", "minItems": 1, "maxItems": 12, "items": ELEMENT_SCHEMA},
    "layers": {"type": "array", "minItems": 1, "maxItems": 8, "items": LAYER_SCHEMA},
    "depth_min_nm": {"type": "number", "minimum": 0}, "depth_max_nm": {"type": "number", "minimum": 0},
    "stopping_power_version": {"type": "integer", "enum": [0, 1]}, "outputs": OUTPUT_SCHEMA,
    "particles": {"type": "array", "maxItems": 99999, "items": PARTICLE_SCHEMA},
    "sources": object_schema({k: {"type": "string", "minLength": 1} for k in ("ion", "material", "calculation", "particles")}),
})
REQUEST_SCHEMA["allOf"] = [{
    "if": {"properties": {"calculation_mode": {"const": 8},
                           "outputs": {"properties": {"collisions": {"const": 1}}, "required": ["collisions"]}},
           "required": ["calculation_mode", "outputs"]},
    "then": {"properties": {"outputs": {"required": ["collision_records"],
                                        "properties": {"collision_records": {"type": "string", "enum": ["summary", "every"]}}}}},
    "else": {"properties": {"outputs": {"properties": {"collision_records": {"type": "null"}}}}},
}]
