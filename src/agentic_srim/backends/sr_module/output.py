"""Parse complete native interval tables; reject malformed or incompatible output."""

import math
import re
from fractions import Fraction

from agentic_srim.capabilities.stopping_range.inputs import Request
from agentic_srim.capabilities.stopping_range.outputs import Row, STOPPING_UNITS, Table


ENERGY = {"eV": 0.001, "keV": 1, "MeV": 1000, "GeV": 1e6}
LENGTH = {"A": 0.1, "nm": 1, "um": 1000, "mm": 1e6, "cm": 1e7, "m": 1e9}


def parse(text: str, request: Request | None = None) -> Table:
    """Parse native text and optional expected request; return a normalized table.

    Rejects missing footer/rows, nonfinite/negative values, nonincreasing energies,
    and unexpected units or ion/density/material headers. Mass percentages and
    Bragg correction allow 0.005 percentage points for two-decimal rounding plus
    1e-9 for floating-point arithmetic. Mass fractions cannot identify all isotope
    changes, including any mass change in a single-element target. These checks
    establish header consistency, not scientific accuracy.
    The native grid need not include the exact requested interval endpoints.
    """
    version = re.search(r"SRIM version\s*--->\s*([^\r\n]+)", text)
    unit = re.search(r"Stopping Units\s*=\s*([^\r\n]+)", text)
    if not version or not unit or "Multiply Stopping by" not in text or not re.search(r"\(C\).*Biersack.*Ziegler", text):
        raise ValueError("Incomplete SR Module interval table")
    unit_label = unit.group(1).strip()
    if unit_label not in STOPPING_UNITS.values():
        raise ValueError(f"Unsupported stopping unit: {unit_label}")
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if "Energy" in line and "Elec." in line and "Nuclear" in line), None)
    if start is None or start + 2 >= len(lines):
        raise ValueError("Missing interval table header")
    rows = []
    ended = False
    for line in lines[start + 2:]:
        if line.strip().startswith("---"):
            ended = True
            break
        parts = line.split()
        if len(parts) != 10:
            raise ValueError(f"Malformed table row: {line!r}")
        try:
            row = Row(float(parts[0]) * ENERGY[parts[1]], float(parts[2]), float(parts[3]),
                      float(parts[4]) * LENGTH[parts[5]], float(parts[6]) * LENGTH[parts[7]],
                      float(parts[8]) * LENGTH[parts[9]])
        except (ValueError, KeyError) as exc:
            raise ValueError(f"Invalid row values or units: {line!r}") from exc
        values = tuple(row.__dict__.values())
        if not all(math.isfinite(v) and v >= 0 for v in values) or row.energy_keV <= 0 or row.projected_range_nm <= 0:
            raise ValueError("Nonphysical or nonfinite table row")
        if rows and row.energy_keV <= rows[-1].energy_keV:
            raise ValueError("Energies are not strictly increasing")
        rows.append(row)
    if not ended or not rows:
        raise ValueError("Empty or truncated interval table")
    if request:
        ion = re.search(r"Ion\s*=.*?\[(\d+)\]\s*,\s*Mass\s*=\s*([\d.E+-]+)", text)
        density = re.search(r"Density\s*=\s*([\d.E+-]+)\s*g/cm3", text)
        if not ion or int(ion.group(1)) != request.ion_atomic_number or not math.isclose(float(ion.group(2)), request.ion_mass_u, rel_tol=0.001, abs_tol=0.0006):
            raise ValueError("Output ion does not match request")
        if not density or not math.isclose(float(density.group(1)), request.density_g_cm3, rel_tol=0.0001):
            raise ValueError("Output density does not match request")
        if unit_label != STOPPING_UNITS[request.stopping_unit]:
            raise ValueError("Output stopping units do not match request")
        composition_text = text.split("Target  Composition", 1)[-1].split("Bragg Correction", 1)[0]
        composition = re.findall(r"^\s*[A-Za-z]+\s+(\d+)\s+([\d.]+)\s+([\d.]+)\s*$", composition_text, re.MULTILINE)
        total = sum(e.atomic_amount for e in request.target)
        expected = {e.atomic_number: e.atomic_amount / total * 100 for e in request.target}
        observed = {int(z): float(percent) for z, percent, _ in composition}
        if len(composition) != len(expected) or set(observed) != set(expected) or any(not math.isclose(observed[z], percent, abs_tol=0.011) for z, percent in expected.items()):
            raise ValueError("Output target atomic composition does not match request")
        masses = {e.atomic_number: Fraction(str(e.atomic_amount)) * Fraction(str(e.mass_u)) for e in request.target}
        total_mass = sum(masses.values())
        observed_mass = {int(z): float(percent) for z, _, percent in composition}
        if any(not math.isclose(observed_mass[z], float(mass / total_mass * 100), rel_tol=0, abs_tol=0.005000001) for z, mass in masses.items()):
            raise ValueError("Output target mass composition does not match request")
        correction_lines = [line for line in lines if "Bragg Correction" in line]
        correction = re.fullmatch(r"\s*Bragg Correction\s*=\s*([+-]?\d+(?:\.\d+)?)%\s*", correction_lines[0]) if len(correction_lines) == 1 else None
        if not correction or not math.isfinite(float(correction.group(1))) or not math.isclose(float(correction.group(1)), (request.compound_correction - 1) * 100, rel_tol=0, abs_tol=0.005000001):
            raise ValueError("Output Bragg correction does not match request")
        if rows[0].energy_keV < request.energy_min_keV * 0.999 or rows[-1].energy_keV > request.energy_max_keV * 1.001:
            raise ValueError("Output energy interval exceeds request")
    return Table(tuple(rows), unit_label, version.group(1).strip())
