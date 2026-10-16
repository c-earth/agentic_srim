"""Validate explicit scientific JSON inputs and expose typed requests and physical specifications.

Atomic amounts are relative numbers of atoms, normalized to atomic fractions;
masses use u, density g/cm3, and interval energies keV. No material defaults.
"""

from dataclasses import asdict, dataclass
from fractions import Fraction
from agentic_srim.domain.specifications import atomic_number, positive
import re


@dataclass(frozen=True)
class Element:
    """Construct from Z, ASCII name, relative atomic amount, and mass in u.

    Exposes a validated target constituent; amounts are numbers of atoms.
    """

    atomic_number: int
    name: str
    atomic_amount: float
    mass_u: float

    def __post_init__(self) -> None:
        """Validate constituent inputs and normalize number types; return nothing."""
        atomic_number(self.atomic_number)
        if not isinstance(self.name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9 _-]{0,19}", self.name):
            raise ValueError("Element name must be 1..20 safe ASCII characters")
        object.__setattr__(self, "atomic_amount", positive(self.atomic_amount, "atomic_amount"))
        object.__setattr__(self, "mass_u", positive(self.mass_u, "mass_u"))


@dataclass(frozen=True)
class Request:
    """Construct an explicit version-1 ion/material/energy request with sources.

    Exposes validated scientific values and unit selection. Sources must describe
    ion, material, and energy inputs; unverified user input may be labeled as such.
    Phase is SR's condensed-phase solid flag (also liquids) or gas. Correction is
    an explicit positive Bragg multiplier. No Monte Carlo ion count or seed applies.
    """

    schema_version: int
    ion_atomic_number: int
    ion_mass_u: float
    target: tuple[Element, ...]
    density_g_cm3: float
    phase: str
    compound_correction: float
    energy_min_keV: float
    energy_max_keV: float
    stopping_unit: int
    sources: dict[str, str]

    def __post_init__(self) -> None:
        """Validate construction inputs; return nothing and reject unknown modes."""
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("Only schema_version 1 is supported")
        atomic_number(self.ion_atomic_number)
        for key in ("ion_mass_u", "density_g_cm3", "compound_correction", "energy_min_keV", "energy_max_keV"):
            object.__setattr__(self, key, positive(getattr(self, key), key))
        if self.energy_min_keV >= self.energy_max_keV:
            raise ValueError("Energy interval must have min < max; custom lists are unsupported")
        if self.phase not in ("solid", "gas"):
            raise ValueError("phase must be solid or gas")
        if type(self.stopping_unit) is not int or not 1 <= self.stopping_unit <= 8:
            raise ValueError("stopping_unit must be an integer from 1 to 8")
        if not isinstance(self.target, tuple) or not 1 <= len(self.target) <= 100 or not all(isinstance(e, Element) for e in self.target):
            raise ValueError("target must contain 1..100 elements")
        if len({e.atomic_number for e in self.target}) != len(self.target):
            raise ValueError("Duplicate target atomic numbers are unsupported")
        if not isinstance(self.sources, dict) or set(self.sources) != {"ion", "material", "energy"} or not all(isinstance(v, str) and v.strip() for v in self.sources.values()):
            raise ValueError("sources must contain nonempty ion, material, and energy descriptions")

    @classmethod
    def from_dict(cls, data: dict) -> "Request":
        """Parse a JSON object input; return a validated request without defaults."""
        if not isinstance(data, dict):
            raise ValueError("Request must be an object")
        values = dict(data)
        try:
            values["target"] = tuple(Element(**e) for e in values["target"])
            return cls(**values)
        except (KeyError, TypeError) as exc:
            raise ValueError(f"Invalid request fields: {exc}") from exc

    def to_dict(self) -> dict:
        """Accept no inputs; return JSON-compatible scientific inputs with sources."""
        return asdict(self)

    def physical_spec(self) -> dict:
        """Accept no inputs; return a canonical physical specification.

        Names, sources, ordering, and stopping presentation units are excluded.
        Decimal-rational atomic fractions make proportional stoichiometry equal.
        """
        result = self.to_dict()
        for key in ("sources", "stopping_unit"):
            result.pop(key)
        amounts = [Fraction(str(e.atomic_amount)) for e in self.target]
        total = sum(amounts)
        result["target"] = sorted(
            [{"atomic_number": e.atomic_number, "mass_u": e.mass_u,
              "atomic_fraction": str(amount / total)} for e, amount in zip(self.target, amounts)],
            key=lambda e: e["atomic_number"],
        )
        return result


REQUEST_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": list(Request.__dataclass_fields__),
    "properties": {
        "schema_version": {"const": 1, "type": "integer"},
        "ion_atomic_number": {"type": "integer", "minimum": 1, "maximum": 92},
        "ion_mass_u": {"type": "number", "exclusiveMinimum": 0},
        "target": {"type": "array", "minItems": 1, "maxItems": 100, "items": {
            "type": "object", "additionalProperties": False,
            "required": list(Element.__dataclass_fields__), "properties": {
                "atomic_number": {"type": "integer", "minimum": 1, "maximum": 92},
                "name": {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9 _-]{0,19}$"},
                "atomic_amount": {"type": "number", "exclusiveMinimum": 0},
                "mass_u": {"type": "number", "exclusiveMinimum": 0},
            }}},
        "density_g_cm3": {"type": "number", "exclusiveMinimum": 0},
        "phase": {"type": "string", "enum": ["solid", "gas"]},
        "compound_correction": {"type": "number", "exclusiveMinimum": 0},
        "energy_min_keV": {"type": "number", "exclusiveMinimum": 0},
        "energy_max_keV": {"type": "number", "exclusiveMinimum": 0},
        "stopping_unit": {"type": "integer", "minimum": 1, "maximum": 8},
        "sources": {"type": "object", "required": ["ion", "material", "energy"],
                    "additionalProperties": False,
                    "properties": {k: {"type": "string", "minLength": 1} for k in ("ion", "material", "energy")}},
    },
}
