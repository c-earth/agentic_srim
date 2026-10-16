"""Convert validated stopping/range requests to the documented native SR.IN format."""

from agentic_srim.capabilities.stopping_range.inputs import Request


OUTPUT_NAME = "SR_OUTPUT.txt"


def render(request: Request) -> str:
    """Return ASCII SR.IN text for a request, using a fixed safe output basename.

    Input masses use u, energies keV, density g/cm3, and target stoichiometry
    relative atomic amounts. Output preserves requested stopping-unit code.
    """
    lines = [
        "---Stopping/Range Input Data (Number-format: Period = Decimal Point)",
        "---Output File Name", f'"{OUTPUT_NAME}"',
        "---Ion(Z), Ion Mass(u)", f"{request.ion_atomic_number} {request.ion_mass_u:.15g}",
        "---Target Data: (Solid=0,Gas=1), Density(g/cm3), Compound Corr.",
        f"{int(request.phase == 'gas')} {request.density_g_cm3:.15g} {request.compound_correction:.15g}",
        "---Number of Target Elements", str(len(request.target)),
        "---Target Elements: (Z), Target name, Stoich, Target Mass(u)",
    ]
    lines.extend(f'{e.atomic_number} "{e.name}" {e.atomic_amount:.15g} {e.mass_u:.15g}' for e in request.target)
    lines.extend(["---Output Stopping Units (1-8)", str(request.stopping_unit),
                  "---Ion Energy : E-Min(keV), E-Max(keV)",
                  f"{request.energy_min_keV:.15g} {request.energy_max_keV:.15g}"])
    return "\r\n".join(lines) + "\r\n"
