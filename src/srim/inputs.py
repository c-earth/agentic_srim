"""Validate explicit SRModule requests and serialize deterministic ASCII SR.IN files."""

from decimal import Decimal
import re

from src.srim.metadata import JSONObject, JSONValue, _validate_document


def _positive_decimal(value: JSONValue, field: str) -> Decimal:
    """Return field's positive decimal-string value as Decimal without mutation or I/O.

    value must contain ASCII digits with an optional fractional part. Raise
    ValueError naming field for invalid types, syntax, or a nonpositive value.
    """
    if not isinstance(value, str) or re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value) is None:
        raise ValueError(f"{field} must be a positive decimal string")
    number = Decimal(value)
    if number <= 0:
        raise ValueError(f"{field} must be positive")
    return number


def validate_sr_request(request: JSONObject) -> None:
    """Validate request's exact version-one SRModule schema; return None without effects.

    request supplies projectile, target, correction, stopping_units, and energy
    parameters. Require explicit positive decimal strings, atomic numbers 1--92,
    1--100 distinct number/mass target rows, and either a strict energy range or
    1--1,000 listed energies. Names contain 1--13 printable ASCII characters
    excluding quotes. Correction is none or supplied; no CAB calculation occurs.
    Raise ValueError for envelope, field, type, syntax, or consistency failures.
    """
    _validate_document(request, "request")
    parameters = request["parameters"]
    if set(parameters) != {"projectile", "target", "correction", "stopping_units", "energy"}:
        raise ValueError("Invalid SRModule parameter fields")
    projectile = parameters["projectile"]
    if not isinstance(projectile, dict) or set(projectile) != {"atomic_number", "mass_amu"}:
        raise ValueError("Invalid projectile fields")
    if type(projectile["atomic_number"]) is not int or not 1 <= projectile["atomic_number"] <= 92:
        raise ValueError("Projectile atomic_number must be an integer from 1 through 92")
    _positive_decimal(projectile["mass_amu"], "projectile.mass_amu")
    target = parameters["target"]
    if not isinstance(target, dict) or set(target) != {"phase", "density_g_cm3", "elements"}:
        raise ValueError("Invalid target fields")
    if target["phase"] not in ("solid", "gas"):
        raise ValueError("Target phase must be solid or gas")
    _positive_decimal(target["density_g_cm3"], "target.density_g_cm3")
    elements = target["elements"]
    if not isinstance(elements, list) or not 1 <= len(elements) <= 100:
        raise ValueError("Target must contain 1 through 100 element rows")
    identities: set[tuple[int, Decimal]] = set()
    for index, element in enumerate(elements):
        if not isinstance(element, dict) or set(element) != {"atomic_number", "name", "stoichiometry", "mass_amu"}:
            raise ValueError("Invalid target element fields")
        atomic_number = element["atomic_number"]
        if type(atomic_number) is not int or not 1 <= atomic_number <= 92:
            raise ValueError("Target atomic_number must be an integer from 1 through 92")
        name = element["name"]
        if not isinstance(name, str) or not 1 <= len(name) <= 13 or any(ord(character) < 32 or ord(character) > 126 or character == '"' for character in name):
            raise ValueError("Target names require 1 through 13 printable ASCII characters excluding quotes")
        mass = _positive_decimal(element["mass_amu"], f"target.elements[{index}].mass_amu")
        _positive_decimal(element["stoichiometry"], f"target.elements[{index}].stoichiometry")
        identity = (atomic_number, mass)
        if identity in identities:
            raise ValueError("Duplicate target atomic number and mass")
        identities.add(identity)
    correction = parameters["correction"]
    if not isinstance(correction, dict) or correction.get("mode") not in ("none", "supplied"):
        raise ValueError("Correction must use none or supplied mode")
    _validate_document(correction, "cab")
    _positive_decimal(correction["applied_factor"], "correction.applied_factor")
    if type(parameters["stopping_units"]) is not int or not 1 <= parameters["stopping_units"] <= 8:
        raise ValueError("Stopping units must be an integer from 1 through 8")
    energy = parameters["energy"]
    if not isinstance(energy, dict):
        raise ValueError("Energy must be an object")
    if energy.get("mode") == "range":
        if set(energy) != {"mode", "minimum_keV", "maximum_keV"}:
            raise ValueError("Invalid energy range fields")
        minimum = _positive_decimal(energy["minimum_keV"], "energy.minimum_keV")
        maximum = _positive_decimal(energy["maximum_keV"], "energy.maximum_keV")
        if minimum >= maximum:
            raise ValueError("Energy minimum must be less than maximum")
    elif energy.get("mode") == "list":
        if set(energy) != {"mode", "values_keV"}:
            raise ValueError("Invalid energy list fields")
        values = energy["values_keV"]
        if not isinstance(values, list) or not 1 <= len(values) <= 1000:
            raise ValueError("Energy list must contain 1 through 1,000 values")
        for index, value in enumerate(values):
            _positive_decimal(value, f"energy.values_keV[{index}]")
    else:
        raise ValueError("Energy mode must be range or list")


def render_sr_input(request: JSONObject) -> bytes:
    """Return validated request as ASCII SR.IN bytes without mutation or I/O.

    Preserve numeric strings and target/list ordering, use single-space fields,
    CRLF endings, and fixed output name stopping.txt. List mode emits 0 0,
    individual energies, and a terminating 0. Raise ValueError for invalid request.
    """
    validate_sr_request(request)
    parameters = request["parameters"]
    projectile = parameters["projectile"]
    target = parameters["target"]
    correction = parameters["correction"]
    energy = parameters["energy"]
    lines = [
        "---Stopping/Range Input Data (Number-format: Period = Decimal Point)",
        "---Output File Name",
        '"stopping.txt"',
        "---Ion(Z), Ion Mass(u)",
        f'{projectile["atomic_number"]} {projectile["mass_amu"]}',
        "---Target Data: (Solid=0,Gas=1), Density(g/cm3), Compound Corr.",
        f'{0 if target["phase"] == "solid" else 1} {target["density_g_cm3"]} {correction["applied_factor"]}',
        "---Number of Target Elements",
        str(len(target["elements"])),
        "---Target Elements: (Z), Target name, Stoich, Target Mass(u)",
    ]
    for element in target["elements"]:
        lines.append(f'{element["atomic_number"]} "{element["name"]}" {element["stoichiometry"]} {element["mass_amu"]}')
    lines.extend((
        "---Output Stopping Units (1-8)",
        str(parameters["stopping_units"]),
        "---Ion Energy : E-Min(keV), E-Max(keV)",
    ))
    if energy["mode"] == "range":
        lines.append(f'{energy["minimum_keV"]} {energy["maximum_keV"]}')
    else:
        lines.extend(("0 0", *energy["values_keV"], "0"))
    return ("\r\n".join(lines) + "\r\n").encode("ascii")
