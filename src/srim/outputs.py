"""Parse SRModule range and explicit-energy output without losing printed precision."""

from decimal import Decimal, DecimalException
import hashlib
import re

from src.srim.inputs import validate_sr_engine_request
from src.srim.metadata import JSONObject


STOPPING_LABELS = (
    "eV/Angstrom", "keV/micron", "MeV/mm", "keV/(ug/cm2)",
    "MeV/(mg/cm2)", "keV/(mg/cm2)", "eV/(1E15 atoms/cm2)",
    "L.S.S. reduced units",
)
ENERGY_FACTORS = {"eV": Decimal("0.001"), "keV": Decimal("1"), "MeV": Decimal("1000"), "GeV": Decimal("1000000")}
LENGTH_UNITS = {"A", "um", "mm", "cm", "m"}


def _parse_number(token: str, field: str) -> Decimal:
    """Return finite nonnegative token as Decimal for field, without effects.

    Accept ASCII decimal or scientific notation. Raise ValueError identifying
    field when token has invalid syntax, a negative value, or a nonfinite value.
    """
    if not isinstance(token, str) or re.fullmatch(r"[+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[Ee][+-]?[0-9]+)?", token) is None:
        raise ValueError(f"Invalid nonnegative number for {field}: {token!r}")
    try:
        return Decimal(token)
    except DecimalException as error:
        raise ValueError(f"Unrepresentable number for {field}: {token!r}") from error


def _matches_printed(actual: str, requested: str) -> bool:
    """Compare actual and requested positive decimal strings at printed precision.

    Return whether their difference fits half actual's last printed place plus
    abs(requested) times 1e-6 for engine rounding. Raise ValueError for malformed
    numbers; nonpositive numbers return False. Perform no mutations or I/O.
    """
    printed = _parse_number(actual, "printed energy")
    original = _parse_number(requested, "requested energy")
    if printed <= 0 or original <= 0:
        return False
    try:
        tolerance = Decimal("0.5").scaleb(printed.as_tuple().exponent) + original * Decimal("0.000001")
        return abs(printed - original) <= tolerance
    except DecimalException as error:
        raise ValueError("Energy precision exceeds supported decimal arithmetic") from error


def _parse_list(lines: list[str], request: JSONObject) -> list[JSONObject]:
    """Return ordered stopping rows from lines for validated list request.

    Require the four-line list header, three numeric columns, requested row
    count, and matching positive printed energies. Preserve duplicates, numeric
    strings, and original requested energies. Raise ValueError on malformed or
    inconsistent output. Do not mutate inputs or access the filesystem.
    """
    if len(lines) < 5 or re.fullmatch(r"Ion\s*=\s*.+,\s*Mass\s*=\s*\S+", lines[0]) is None or re.fullmatch(r"Tgt\s*=\s*.+\((?:Solid|Gas)\),\s*Density\s*=\s*\S+\s+g/cm3", lines[1]) is None or lines[3].split() != ["Energy(keV)", "S(Elec)", "S(Nuc)"]:
        raise ValueError("Invalid explicit-energy output header")
    values = request["parameters"]["energy"]["values_keV"]
    data = lines[4:]
    if len(data) != len(values):
        raise ValueError("Explicit-energy output row count differs from request")
    result: list[JSONObject] = []
    for index, (line, requested) in enumerate(zip(data, values, strict=True)):
        tokens = line.split()
        if len(tokens) != 3:
            raise ValueError(f"Invalid explicit-energy row {index + 1}")
        for field, token in zip(("energy", "electronic stopping", "nuclear stopping"), tokens, strict=True):
            _parse_number(token, field)
        if not _matches_printed(tokens[0], requested):
            raise ValueError(f"Explicit-energy row {index + 1} differs from request")
        result.append({"energy": {"value": tokens[0], "unit": "keV"}, "electronic_stopping": tokens[1], "nuclear_stopping": tokens[2], "requested_energy_keV": requested})
    return result


def _parse_range(lines: list[str], request: JSONObject) -> list[JSONObject]:
    """Return range/stopping rows from lines for validated range request.

    Require range headers, data separators, increasing positive energies, known
    energy/length units, nonnegative values, and all eight conversion-footer
    entries with closing separator and copyright. Preserve printed strings and
    mixed units. request supplies the expected range mode. Raise ValueError on
    malformed or truncated output; perform no mutation or filesystem operations.
    """
    if request["parameters"]["energy"]["mode"] != "range":
        raise ValueError("Expected a range request")
    header = "Ion dE/dx dE/dx Projected Longitudinal Lateral"
    normalized = [" ".join(line.split()) for line in lines]
    if normalized.count(header) != 1 or not any(line.startswith("SRIM version --->") for line in lines) or not any(line.startswith("Ion =") for line in lines) or not any(line.startswith("Density =") for line in lines) or "======= Target Composition ========" not in normalized or not any(line.startswith("Bragg Correction =") for line in lines):
        raise ValueError("Invalid range output header")
    index = normalized.index(header)
    if normalized[index + 1:index + 2] != ["Energy Elec. Nuclear Range Straggling Straggling"] or index + 2 >= len(lines) or re.fullmatch(r"[- ]+", lines[index + 2]) is None:
        raise ValueError("Invalid range table headings or separator")
    result: list[JSONObject] = []
    previous = Decimal("0")
    index += 3
    while index < len(lines) and re.fullmatch(r"-+", lines[index]) is None:
        tokens = lines[index].split()
        if len(tokens) != 10 or tokens[1] not in ENERGY_FACTORS or any(tokens[position] not in LENGTH_UNITS for position in (5, 7, 9)):
            raise ValueError(f"Invalid range output row {len(result) + 1}")
        energy = _parse_number(tokens[0], "energy") * ENERGY_FACTORS[tokens[1]]
        if energy <= previous:
            raise ValueError("Range energies must be positive and strictly increasing")
        previous = energy
        for position, field in ((2, "electronic stopping"), (3, "nuclear stopping"), (4, "projected range"), (6, "longitudinal straggling"), (8, "lateral straggling")):
            _parse_number(tokens[position], field)
        result.append({
            "energy": {"value": tokens[0], "unit": tokens[1]},
            "electronic_stopping": tokens[2], "nuclear_stopping": tokens[3],
            "projected_range": {"value": tokens[4], "unit": tokens[5]},
            "longitudinal_straggling": {"value": tokens[6], "unit": tokens[7]},
            "lateral_straggling": {"value": tokens[8], "unit": tokens[9]},
        })
        index += 1
    footer = lines[index:]
    if not result or len(footer) != 13 or normalized[index + 1:index + 2] != ["Multiply Stopping by for Stopping Units"] or re.fullmatch(r"[- ]+", footer[2]) is None or re.fullmatch(r"=+", footer[11]) is None or not footer[12].startswith("(C)"):
        raise ValueError("Missing or incomplete range conversion footer")
    for line, label in zip(footer[3:11], STOPPING_LABELS, strict=True):
        parts = line.split(maxsplit=1)
        if len(parts) != 2 or parts[1] != label:
            raise ValueError("Invalid range conversion units")
        _parse_number(parts[0], "stopping conversion factor")
    return result


def parse_sr_output(contents: bytes, request: JSONObject) -> JSONObject:
    """Return version-one parsed results for exact contents bytes and SR request.

    Validate request, decode strict ASCII, check the selected stopping-unit label,
    and parse its requested format. Preserve printed precision and raw SHA-256/
    byte count. Raise ValueError for invalid types, encoding, format, units, or
    values. Perform no filesystem operations and mutate neither input.
    """
    validate_sr_engine_request(request)
    if not isinstance(contents, bytes):
        raise ValueError("SR output must be bytes")
    try:
        lines = contents.decode("ascii").splitlines()
    except UnicodeError as error:
        raise ValueError("SR output must be ASCII") from error
    while lines and not lines[-1].strip():
        lines.pop()
    lines = [line.strip() for line in lines]
    code = request["parameters"]["stopping_units"]
    unit_lines = [line for line in lines if line.startswith("Stopping Units =")]
    if len(unit_lines) != 1 or unit_lines[0].split("=", 1)[1].strip() != STOPPING_LABELS[code - 1]:
        raise ValueError("SR output stopping units differ from request")
    mode = request["parameters"]["energy"]["mode"]
    try:
        rows = _parse_list(lines, request) if mode == "list" else _parse_range(lines, request)
    except DecimalException as error:
        raise ValueError("SR output exceeds supported decimal arithmetic") from error
    return {
        "schema_version": 1, "energy_mode": mode,
        "source": {"filename": "stopping.txt", "sha256": hashlib.sha256(contents).hexdigest(), "size_bytes": len(contents)},
        "stopping_units": {"code": code, "label": STOPPING_LABELS[code - 1]},
        "rows": rows,
    }
