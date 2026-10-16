"""Parse archived native TRIM tables and details into versioned scientific data.

Inputs are a native artifact directory and validated request. Outputs are finite
normalized profiles, summary metadata, and streamed JSONL detail files. Parsers
reject missing, malformed, truncated, or mismatched results; accuracy is unvalidated.
"""

import math
from pathlib import Path
import re

from agentic_srim.capabilities.trim.inputs import MODES, Request
from agentic_srim.capabilities.trim.outputs import Detail, Profile
from agentic_srim.backends.trim.input import render
from agentic_srim.domain.specifications import canonical
from agentic_srim.storage.artifacts import checksum


NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][+-]?\d+)?"
PROFILES = ("RANGE.txt", "IONIZ.txt", "PHONON.txt", "VACANCY.txt", "E2RECOIL.txt", "NOVAC.txt", "LATERAL.txt")
DETAILS = ("RANGE_3D.txt", "BACKSCAT.txt", "TRANSMIT.txt", "SPUTTER.txt", "TRIMOUT.txt", "EXYZ.txt", "COLLISON.txt")


def numeric(value: str) -> float:
    """Return a finite float from one complete native numeric token; reject corruption."""
    if not re.fullmatch(NUMBER, value.strip()):
        raise ValueError(f"Malformed TRIM numeric token: {value!r}")
    result = float(value.replace("D", "E").replace("d", "e"))
    if not math.isfinite(result):
        raise ValueError("Nonfinite TRIM value")
    return result


def numeric_tokens(text: str) -> list[str]:
    """Return complete numeric fields from native fixed-width text, including spaced signs.

    Native backscatter/EXYZ fields sometimes join a minus sign to the previous
    column or separate it from its magnitude. Reject all nonnumeric residue.
    """
    text = re.sub(r"([+-])\s+(?=[\d.])", r"\1", text.strip())
    matches = list(re.finditer(NUMBER, text))
    position = 0
    for match in matches:
        if text[position:match.start()].strip() or (position and position == match.start() and match[0][0] not in "+-"):
            raise ValueError("Malformed native numeric fields")
        position = match.end()
    if text[position:].strip():
        raise ValueError("Nonnumeric residue in native numeric row")
    return [m[0] for m in matches]


def matched(text: str, pattern: str) -> tuple[str, ...]:
    """Return required regex captures from native text; reject absent/ambiguous fields."""
    matches = list(re.finditer(pattern, text, re.MULTILINE))
    if len(matches) != 1:
        raise ValueError(f"Missing or ambiguous TRIM field: {pattern}")
    return matches[0].groups()


def summary(text: str, request: Request) -> dict:
    """Return TDATA summary after checking request identity, layer geometry, and completion.

    Native printed precision is allowed for identity fields. Integer actual counts
    come from TDATA, never from fractional profile normalization headers.
    """
    version = matched(text, r"^\s*(SRIM-[\d.]+)\s*$")[0]
    z, mass = matched(text, rf"Ion\s*=.*?\(\s*(\d+)\)\s+Ion Mass=\s*({NUMBER})")
    energy = numeric(matched(text, rf"^Energy\s*=\s*({NUMBER})\s+keV")[0])
    angle = numeric(matched(text, rf"Ion Angle to Surface\s*=\s*({NUMBER})\s+degrees")[0])
    for actual, expected in ((int(z), request.ion_atomic_number), (numeric(mass), request.ion_mass_u),
                             (energy, request.energy_keV), (angle, request.angle_degrees)):
        if not math.isclose(actual, expected, rel_tol=0.0006, abs_tol=0.00005):
            raise ValueError("TRIM beam identity does not match request")
    count = int(matched(text, r"^Total Ions calculated\s*=\s*(\d+)\s*$")[0])
    if count != request.ion_count:
        raise ValueError("TRIM did not complete the requested ion/event count")
    depths = re.findall(rf"Layer #\s*(\d+)\s*- Bottom Depth=\s*({NUMBER})\s+A", text)
    densities = re.findall(rf"Layer #\s*(\d+)\s*- Density\s*=.*?=\s*({NUMBER})\s+g/cm3", text)
    if len(depths) != len(request.layers) or len(densities) != len(request.layers):
        raise ValueError("TRIM target layer count mismatch")
    bottom = 0.0
    for i, (layer, depth, density) in enumerate(zip(request.layers, depths, densities), 1):
        bottom += layer.width_nm
        if int(depth[0]) != i or int(density[0]) != i or not math.isclose(numeric(depth[1]) / 10, bottom, rel_tol=0.0006) or not math.isclose(numeric(density[1]), layer.density_g_cm3, rel_tol=0.0006):
            raise ValueError("TRIM target geometry/density mismatch")
    energies = re.findall(rf"Displacement\s*=\s*({NUMBER}) eV, Binding\s*=\s*({NUMBER}) eV, Surface\s*=\s*({NUMBER}) eV", text)
    if len(energies) != len(request.elements):
        raise ValueError("TRIM target element count mismatch")
    for values, element in zip(energies, request.elements):
        if any(not math.isclose(numeric(v), expected, rel_tol=0.0006, abs_tol=0.00005) for v, expected in zip(values, (element.displacement_eV, element.lattice_binding_eV, element.surface_binding_eV))):
            raise ValueError("TRIM target damage-energy mismatch")
    back = int(matched(text, r"^Total Backscattered Ions\s*=\s*(\d+)\s*$")[0])
    transmit = int(matched(text, r"^Total Transmitted Ions\s*=\s*(\d+)\s*$")[0])
    if back + transmit > count:
        raise ValueError("TRIM escaping ion counts exceed actual events")
    average = numeric(matched(text, rf"^Average Range\s*=\s*({NUMBER}) Angstroms")[0]) / 10
    straggling = numeric(matched(text, rf"^Average Straggling\s*=\s*({NUMBER}) Angstroms")[0]) / 10
    vacancy = numeric(matched(text, rf"^Average Vacancy/Ion\s*=\s*({NUMBER})\s*$")[0])
    if min(average, straggling, vacancy) < 0:
        raise ValueError("Negative TRIM summary observable")
    return {"simulator_version": version, "actual_ion_count": count, "backscattered_ions": back,
            "transmitted_ions": transmit, "stopped_ions": count - back - transmit,
            "projected_range_nm": average, "straggling_nm": straggling, "vacancies_per_event": vacancy}


def profile(text: str, name: str, request: Request) -> Profile:
    """Return a normalized 100-bin profile from native text/name and request.

    Quick-damage NOVAC explicitly has no replacement data. Native densities are
    already normalized: no second division by the requested or actual count occurs.
    """
    quick = request.calculation_mode in (1, 4, 8)
    individual = request.calculation_mode in (2, 5, 6, 7)
    if name == "NOVAC.txt" and quick:
        if "There are NO Replacement Collisions calculated." not in text:
            raise ValueError("Missing native quick-damage replacement limitation")
        return Profile(name, (), (), (), "not calculated", None, "not_applicable")
    count = numeric(matched(text, rf"Total Ions calculated\s*=\s*({NUMBER})")[0])
    if count <= 0 or count > request.ion_count + 0.01:
        raise ValueError("Invalid native profile normalization")
    species = tuple(f"element_{i + 1}_{e.name}" for i, e in enumerate(request.elements))
    if name == "LATERAL.txt":
        columns = ("depth", "projected_lateral_range", "projected_lateral_straggling", "radial_range", "radial_straggling")
        units, factors, native = ("nm",) * 5, (0.1,) * 5, "Angstrom"
        if "(Angstroms)" not in text:
            raise ValueError("Unrecognized lateral units")
    else:
        if name == "RANGE.txt":
            columns = ("depth", "ions", *species) if individual else ("depth", "ions", "recoils")
            unit, factor, native = "1/nm", 1e-7, "(Atoms/cm3)/(Atoms/cm2)"
            if not re.search(r"\(Atoms/cm3\)\s*/\s*\(Atoms/cm2\)", text):
                raise ValueError("Unrecognized range units")
        elif name == "NOVAC.txt":
            columns = ("depth", "replacement_collisions")
            unit, factor, native = "1/(nm event)", 10.0, "Number/(Angstrom-Ion)"
        elif name == "VACANCY.txt":
            columns = ("depth", "primary_knock_ons", *species) if individual else ("depth", "ions", "recoils")
            unit, factor, native = "1/(nm event)", 10.0, "Vacancies/(Angstrom-Ion)"
        elif name == "PHONON.txt":
            columns = ("depth", "ions", "recoils")
            unit, factor, native = "phonons/(nm event)", 10.0, "Phonons/(Angstrom-Ion)"
        elif name in ("IONIZ.txt", "E2RECOIL.txt"):
            columns = ("depth", "from_ions", *species) if name == "E2RECOIL.txt" and individual else ("depth", "ions", "recoils")
            unit, factor, native = "eV/(nm event)", 10.0, "eV/(Angstrom-Ion)"
        else:
            raise ValueError("Unknown native profile")
        if name != "RANGE.txt" and native.replace(" ", "") not in text.replace(" ", ""):
            raise ValueError(f"Unrecognized native units in {name}")
        units = ("nm", *(unit for _ in columns[1:]))
        factors = (0.1, *(factor for _ in columns[1:]))
    rows = []
    active = False
    footer = False
    for line in text.splitlines():
        if re.fullmatch(r"\s*-{5,}(?:\s+-{5,})+\s*", line):
            if active:
                raise ValueError("Duplicate native profile table")
            active = True
            continue
        if not active or not line.strip():
            continue
        if line.lstrip().startswith("To convert to Energy Lost"):
            footer = True
            continue
        if footer:
            raise ValueError("Unexpected native profile footer content")
        tokens = line.split()
        if len(tokens) != len(columns):
            raise ValueError(f"Wrong column count in {name}")
        row = tuple(numeric(v) * factor for v, factor in zip(tokens, factors))
        if not all(math.isfinite(v) for v in row) or (rows and row[0] <= rows[-1][0]):
            raise ValueError("Invalid or unordered profile row")
        rows.append(row)
    if len(rows) != 100:
        raise ValueError(f"Expected 100 complete TRIM bins in {name}; got {len(rows)}")
    lower = request.depth_min_nm
    upper = request.depth_max_nm or sum(v.width_nm for v in request.layers)
    spacing = (upper - lower) / 100
    if any(not math.isclose(row[0], lower + (i + 1) * spacing + 0.001, rel_tol=0.0001, abs_tol=0.003) for i, row in enumerate(rows)):
        raise ValueError("Native profile depth window does not match request")
    return Profile(name, columns, units, tuple(rows), native, count)


def ion_id(value: str, maximum: int) -> int:
    """Return an integer native event identifier inside the completed event count."""
    if not value.isdigit() or not 1 <= int(value) <= maximum:
        raise ValueError("Invalid native ion/event number")
    return int(value)


def detail_rows(path: Path, request: Request):
    """Yield normalized detail dictionaries for an input native file/request.

    Streams potentially large files. Collision types include ion collisions,
    recoil records, cascade summaries, and per-ion damage summaries; raw lines
    remain in preserved native artifacts. Unknown numeric rows are rejected.
    """
    name = path.name
    header_seen = False
    current_ion = None
    first_position_row = True
    with path.open(encoding="cp1252") as source:
        for line_number, line in enumerate(source, 1):
            stripped = line.strip()
            if name in ("BACKSCAT.txt", "TRANSMIT.txt", "SPUTTER.txt", "TRIMOUT.txt"):
                if "Cos(X)" in line and "Cos(Y)" in line:
                    header_seen = True
                    continue
                if not header_seen or not stripped:
                    continue
                tokens = [stripped[0], *numeric_tokens(stripped[1:])]
                if len(tokens) != 10 or tokens[0] not in ("S", "B", "T"):
                    raise ValueError(f"Malformed escape record in {name}:{line_number}")
                values = [numeric(v) for v in tokens[3:]]
                z = numeric(tokens[2])
                if z != int(z) or not 1 <= z <= 92 or values[0] < 0 or any(abs(v) > 1.00001 for v in values[4:]):
                    raise ValueError("Invalid native escape kinetics")
                yield {"kind": tokens[0], "ion_number": ion_id(tokens[1], request.ion_count),
                       "atomic_number": int(z), "energy_keV": values[0] / 1000,
                       "position_nm": [v / 10 for v in values[1:4]], "direction": values[4:], "source_line": line_number}
            elif name in ("RANGE_3D.txt", "EXYZ.txt"):
                if re.fullmatch(r"\s*-{5,}(?:\s+-{5,})+\s*", line):
                    header_seen = True
                    continue
                if not header_seen or not stripped:
                    continue
                tokens = numeric_tokens(stripped)
                expected = 4 if name == "RANGE_3D.txt" else 7
                missing_initial_recoil = name == "EXYZ.txt" and request.calculation_mode >= 4 and first_position_row and len(tokens) == 6
                if len(tokens) != expected and not missing_initial_recoil:
                    raise ValueError(f"Malformed position record in {name}:{line_number}")
                values = [numeric(v) for v in tokens[1:]]
                row = {"ion_number": ion_id(tokens[0], request.ion_count), "source_line": line_number}
                if name == "RANGE_3D.txt":
                    row["position_nm"] = [v / 10 for v in values]
                else:
                    row.update({"energy_keV": values[0], "position_nm": [v / 10 for v in values[1:4]],
                                "electronic_stopping_eV_nm": values[4] * 10,
                                "last_recoil_energy_eV": None if missing_initial_recoil else values[5]})
                first_position_row = False
                yield row
            elif name == "COLLISON.txt":
                if "COLLISION HISTORY" in line:
                    header_seen = True
                if not header_seen:
                    continue
                if re.match(r"^\xb3\d{5}\xb3", line):
                    fields = line.strip().strip("\xb3").split("\xb3")
                    if not 9 <= len(fields) <= 12:
                        raise ValueError("Malformed native collision row")
                    current_ion = ion_id(fields[0].strip(), request.ion_count)
                    energy, depth, y, z, stopping = [numeric(v) for v in fields[1:6]]
                    if not re.fullmatch(r"\s*[A-Za-z]{1,2}\s*", fields[6]):
                        raise ValueError("Invalid collision atom symbol")
                    row = {"kind": "collision", "ion_number": current_ion, "energy_keV": energy,
                           "position_nm": [v / 10 for v in (depth, y, z)], "electronic_stopping_eV_nm": stopping * 10,
                           "target_symbol": fields[6].strip(), "recoil_energy_eV": numeric(fields[7]), "source_line": line_number}
                    tail = fields[8:]
                    if "Start of New Cascade" in tail[0]:
                        row["starts_cascade"] = True
                    else:
                        for key, value in zip(("displacements", "vacancies", "replacements", "interstitials"), tail):
                            row[key] = numeric(value) if value.strip() else None
                    yield row
                elif re.match(r"^\xb3\s+\d{5}\s+", line):
                    fields = line.split("\xb3")[1].split()
                    if len(fields) != 8 or current_ion is None:
                        raise ValueError("Malformed native recoil row")
                    values = [numeric(v) for v in fields]
                    yield {"kind": "recoil", "ion_number": current_ion, "recoil_number": int(values[0]),
                           "atomic_number": int(values[1]), "energy_eV": values[2], "position_nm": [v / 10 for v in values[3:6]],
                           "vacancies": values[6], "replacements": values[7], "source_line": line_number}
                elif "Summary of Above Cascade ==>" in line:
                    tail = line.split("Summary of Above Cascade ==>", 1)[1]
                    values = [numeric(v) for v in tail.strip().strip("\xb3 ").split("\xb3")]
                    if len(values) != 5 or current_ion is None:
                        raise ValueError("Malformed native cascade summary")
                    yield dict(zip(("energy_eV", "displacements", "vacancies", "replacements", "interstitials"), values),
                               kind="cascade_summary", ion_number=current_ion, source_line=line_number)
                elif "- For Ion " in line:
                    current_ion = ion_id(matched(line, r"- For Ion\s+(\d+):")[0], request.ion_count)
                elif re.match(r"^- (Displacements|Replacements|Vacancies|Interstitials|Sputtered Atoms|Transmitted Atoms)\s*=", line):
                    match = re.match(rf"^- (.*?)\s*=\s*({NUMBER})\s+Average .*?=\s*({NUMBER})", line)
                    if match is None or current_ion is None:
                        raise ValueError("Malformed per-ion damage summary")
                    yield {"kind": "ion_summary", "ion_number": current_ion, "observable": match[1].strip(),
                           "count": numeric(match[2]), "average_per_event": numeric(match[3]), "source_line": line_number}
                elif stripped.startswith("\xb3") and re.search(r"\d", stripped):
                    raise ValueError(f"Unrecognized collision data at line {line_number}")
            else:
                raise ValueError("Unsupported TRIM detail filename")
    if not header_seen:
        raise ValueError(f"Missing detail table header in {name}")


def native_input_data(payload: bytes, request: Request) -> tuple[str, ...]:
    """Return all native scientific/control data lines for request's declared dimensions.

    Excludes explanatory comment lines so historical captures remain comparable
    when comments are corrected. Includes beam, mode, seed, output flags, target
    identity/composition, phases, binding energies, window, and version choice.
    """
    lines = payload.decode("ascii").splitlines()
    elements, layers = len(request.elements), len(request.layers)
    phase = 14 + elements + layers
    if len(lines) != phase + 12:
        raise ValueError("Native saved input record count mismatch")
    indices = [2, 4, 6, 8, 10, *range(12, 12 + elements),
               *range(14 + elements, phase), *range(phase + 1, phase + 12, 2)]
    return tuple(lines[i] for i in indices) + (lines[phase].split()[0],)


def parse(archive: Path, request: Request) -> dict:
    """Parse collected attempt/request; write new JSONL detail artifacts and return result.

    All raw text tables and native restore snapshots remain archived. Optional
    files are required when requested; zero escaping/stopped events may yield
    header-only detail files. Results never assert scientific acceptance.
    """
    native = archive / "native"
    saved_input = native / "SRIM Restore" / "TRIMIN.sav"
    if native_input_data(saved_input.read_bytes(), request) != native_input_data(render(request).encode("ascii"), request):
        raise ValueError("Native saved input does not match the complete request")
    tdata = native / "TDATA.txt"
    result = {"schema_version": 1, "analysis_version": "trim-v1", "capability": "trim",
              "calculation_mode": request.calculation_mode, "mode_name": MODES[request.calculation_mode],
              "summary": summary(tdata.read_text(encoding="cp1252"), request), "profiles": {}, "details": {}}
    for name in PROFILES:
        result["profiles"][name] = profile((native / name).read_text(encoding="cp1252"), name, request).to_dict()
    options = request.outputs
    required = {"RANGE_3D.txt": options.ranges, "BACKSCAT.txt": options.backscatter,
                "TRANSMIT.txt": options.transmit, "SPUTTER.txt": options.sputter,
                "EXYZ.txt": bool(options.exyz_interval_eV), "COLLISON.txt": bool(options.collisions),
                "TRIMOUT.txt": options.backscatter or options.transmit or options.sputter}
    derived = archive / "processed"
    derived.mkdir(exist_ok=False)
    for name in DETAILS:
        path = native / "SRIM Outputs" / name
        if not path.exists():
            if required[name]:
                raise ValueError(f"Missing requested native file {name}")
            continue
        target = derived / f"{name}.jsonl"
        count = 0
        ion_summaries = {}
        with target.open("x", encoding="utf-8", newline="\n") as output:
            for row in detail_rows(path, request):
                output.write(canonical(row) + "\n")
                count += 1
                if row.get("kind") == "ion_summary":
                    ion_summaries.setdefault(row["ion_number"], set()).add(row["observable"])
        expected_records = {"RANGE_3D.txt": result["summary"]["stopped_ions"],
                            "BACKSCAT.txt": result["summary"]["backscattered_ions"],
                            "TRANSMIT.txt": result["summary"]["transmitted_ions"]}
        if name in expected_records and count != expected_records[name]:
            raise ValueError(f"Incomplete native detail count in {name}")
        if name == "COLLISON.txt" and (set(ion_summaries) != set(range(1, request.ion_count + 1)) or any(len(v) != 6 for v in ion_summaries.values())):
            raise ValueError("Incomplete per-ion collision summaries")
        result["details"][name] = Detail(name, count, target.relative_to(archive).as_posix(), checksum(target),
                                         path.relative_to(archive).as_posix(), checksum(path),
                                         {"position": "nm", "ion_energy": "keV", "recoil_energy": "eV", "electronic_stopping": "eV/nm", "direction": "dimensionless"}).to_dict()
    result["validation"] = {"format": "checked", "scientific_accuracy": "not_validated"}
    result["sampling"] = {"requested_ion_count": request.ion_count, "actual_ion_count": result["summary"]["actual_ion_count"],
                          "requested_random_seed": request.random_seed, "seed_controllable": True,
                          "seed_zero_semantics": "native default", "normalization": "native per-event; fractional profile normalization retained"}
    result["limitations"] = ["Scientific reference/accuracy validation deferred by user.",
                             "TRIM profiles are native per-event averages; profile normalization may be fractional and is retained without renormalization.",
                             "TRIM assumes independent impacts in an amorphous target; accumulated damage and thermal evolution are not simulated.",
                             "Straggling is a distribution width, not a numerical uncertainty estimate.",
                             "Modes 6/7/8 consume externally supplied recoils; no neutron/electron/photon transport is performed.",
                             "External primary-particle normalization must be supplied separately; every TRIM.DAT row is an event.",
                             "Native PHONON tables report phonon counts per length/event, retained as printed rather than relabeled as energy."]
    return result
