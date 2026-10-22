"""Parse native TRIM summaries, depth profiles and textual checkpoint evidence."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from decimal import Decimal, DecimalException, localcontext
import hashlib
import os
from operator import itemgetter
from pathlib import PurePosixPath
import re
import shlex

from src.srim.metadata import JSONObject
from src.srim.trim_inputs import ENERGIES, MODES, PLOTS, validate_trim_engine_request
from src.srim.trim_resolution import ResolvedTRIMInputs, verify_trim_resolution


PROFILES = ("RANGE.txt", "VACANCY.txt", "IONIZ.txt", "PHONON.txt", "E2RECOIL.txt", "NOVAC.txt", "LATERAL.txt")
NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?"
AGGREGATE_NOTE = "Native Kinchin-Pease tables contain aggregate recoil quantities, not per-atom columns."
SIGNED_ENERGY_NOTE = "Native SRIM-2008.04/SRIM-2013.00 energy transfer contains signed values; printed tokens are preserved without physical correction."


def saved_trim_mode(version: str, input_mode: int) -> int:
    """Return the saved cascade number for version and input_mode without effects.

    Raise ValueError for unsupported versions or input mode numbers.
    """
    family = "2008" if "2008" in version else "2013" if "2013" in version else None
    if family is None or type(input_mode) is not int or input_mode not in MODES[family].values():
        raise ValueError("Unsupported TRIM version/input mode")
    return {8: 6, 6: 7, 7: 8}.get(input_mode, input_mode) if family == "2013" else input_mode


def saved_trim_mode_name(version: str, saved_mode: int) -> str:
    """Return descriptive saved_mode semantics for version, without effects.

    Raise ValueError for unsupported versions or saved mode numbers.
    """
    family = "2008" if "2008" in version else "2013" if "2013" in version else None
    if family is not None:
        for name, number in MODES[family].items():
            if saved_trim_mode(version, number) == saved_mode:
                return name
    raise ValueError("Unsupported TRIM version/saved mode")


def _trim_text(contents: bytes, source: str) -> list[str]:
    """Decode contents from source as native CP1252 lines without effects.

    Return lines preserving text; allow one terminal EOF marker and CR/LF/tab.
    Raise ValueError for nonbytes, embedded EOF or other record controls.
    """
    if not isinstance(contents, bytes):
        raise ValueError(f"{source}: expected bytes")
    text = contents.removesuffix(b"\x1a").decode("cp1252")
    if any(ord(c) < 32 and c not in "\r\n\t" for c in text):
        raise ValueError(f"{source}: illegal native text control")
    return text.splitlines()


def _trim_number(token: str, field: str, *, signed: bool = False) -> str:
    """Return unchanged finite token for field, allowing negatives when signed.

    Perform no I/O or mutation; raise ValueError for invalid syntax or bounds.
    """
    if not isinstance(token, str) or re.fullmatch(NUMBER, token) is None:
        raise ValueError(f"{field}: invalid numeric token {token!r}")
    try:
        value = Decimal(token)
        if not value.is_finite() or (not signed and value < 0):
            raise ValueError(f"{field}: invalid numeric value")
    except DecimalException as error:
        raise ValueError(f"{field}: invalid decimal") from error
    return token


def _trim_source(filename: str, contents: bytes) -> JSONObject:
    """Return exact hash/size provenance of filename and contents without effects.

    Raise ValueError for unsafe relative names or nonbyte payloads.
    """
    if not isinstance(filename, str) or not filename or PurePosixPath(filename).is_absolute() or any(p in ("", ".", "..") or p.endswith((".", " ")) or os.path.isreserved(p) or any(ord(c) < 32 or c in '<>:"\\|?*' for c in p) for p in filename.split("/")) or not isinstance(contents, bytes):
        raise ValueError("Invalid TRIM source")
    return {"path": filename, "sha256": hashlib.sha256(contents).hexdigest(), "size_bytes": len(contents)}


def parse_trim_summary(contents: bytes) -> JSONObject:
    """Parse exact native TDATA.txt bytes into ordered summary fields.

    Return printed quantities, source identity and unavailable optional fields.
    Require unique version/projectile/count/target sections; raise ValueError
    for malformed or truncated supported 2008/2013 text. Perform no effects.
    """
    lines = _trim_text(contents, "TDATA.txt")
    text = "\n".join(lines)
    versions = re.findall(r"^\s*(SRIM-(?:2008|2013)\.[\w.]+)\s*$", text, re.M)
    ion = re.findall(rf"Ion\s*=\s*(\S+)\s*\((\d+)\)\s+Ion Mass=\s*({NUMBER})", text)
    energy = re.findall(rf"^Energy\s*=\s*({NUMBER})\s+keV\s*$", text, re.M)
    angle = re.findall(rf"Ion Angle to Surface\s*=\s*({NUMBER})\s+degrees", text)
    counts: JSONObject = {}
    for key, pattern in (("calculated", r"Total Ions calculated"), ("transmitted", r"Total Transmitted Ions"), ("backscattered", r"Total Backscattered Ions")):
        found = re.findall(pattern + rf"\s*=\s*({NUMBER})", text)
        if len(found) != 1 or Decimal(_trim_number(found[0], key)) != Decimal(found[0]).to_integral_value():
            raise ValueError(f"TDATA.txt: missing or invalid {key} count")
        counts[key] = int(Decimal(found[0]))
    if len(versions) != 1 or len(ion) != 1 or len(energy) != 1 or len(angle) != 1:
        raise ValueError("TDATA.txt: missing/ambiguous native identity")
    layers: list[JSONObject] = []
    for m in re.finditer(r"^\s*Layer #\s*(\d+)\s*-\s*([^\n=]+)$", text, re.M):
        index, name = int(m[1]), m[2].strip()
        if index != len(layers) + 1:
            raise ValueError("TDATA.txt: invalid layer order")
        depth = re.findall(rf"Layer #\s*{index}\s*-\s*Bottom Depth=\s*({NUMBER})\s+A", text)
        density = re.findall(rf"Layer #\s*{index}\s*-\s*Density\s*=\s*({NUMBER})\s+atoms/cm3\s*=\s*({NUMBER})\s+g/cm3", text)
        components = re.findall(rf"Layer #\s*{index}\s*-\s*(\S+)\s*=\s*({NUMBER})\s+Atomic Percent\s*=\s*({NUMBER})\s+Mass Percent", text)
        if len(depth) != 1 or len(density) != 1 or not components:
            raise ValueError(f"TDATA.txt: incomplete layer {index}")
        layers.append({"index": index, "name": name, "bottom_depth_angstrom": _trim_number(depth[0], "depth"), "density_g_cm3": _trim_number(density[0][1], "density"), "density_atoms_cm3": _trim_number(density[0][0], "atom density"), "composition": [{"index": n, "symbol": c[0], "atomic_percent": _trim_number(c[1], "atomic percent"), "mass_percent": _trim_number(c[2], "mass percent")} for n, c in enumerate(components, 1)]})
    energies = re.findall(rf"Target energies for target atom\s*=\s*(\S+)\s*\n\s*Displacement\s*=\s*({NUMBER})\s+eV,\s*Binding\s*=\s*({NUMBER})\s+eV,\s*Surface\s*=\s*({NUMBER})\s+eV", text)
    if not layers or not energies:
        raise ValueError("TDATA.txt: missing target material/energy records")
    statistics: JSONObject = {}
    unavailable: list[str] = []
    for key, label in (("mean_range_angstrom", "Average Range"), ("straggling_angstrom", "Average Straggling"), ("vacancies_per_ion", "Average Vacancy/Ion")):
        found = re.findall(re.escape(label) + rf"\s*=\s*({NUMBER})", text)
        if len(found) > 1:
            raise ValueError(f"TDATA.txt: duplicate {label}")
        statistics[key] = _trim_number(found[0], label) if found else None
        if not found:
            unavailable.append("statistics." + key)
    return {"source": _trim_source("TDATA.txt", contents), "version": versions[0], "name": lines[0].strip().strip("= "), "projectile": {"atomic_number": int(ion[0][1]), "symbol": ion[0][0], "mass_amu": _trim_number(ion[0][2], "mass"), "energy_keV": _trim_number(energy[0], "energy"), "angle_degrees": _trim_number(angle[0], "angle", signed=True)}, "layers": layers, "target_energies": [{"index": n, "symbol": e[0], **{key: _trim_number(value, key) for key, value in zip(ENERGIES, e[1:], strict=True)}} for n, e in enumerate(energies, 1)], "counts": counts, "statistics": statistics, "unavailable": unavailable}


def parse_trim_profile(filename: str, contents: bytes) -> JSONObject:
    """Parse one supported filename's native depth table from contents.

    Return exactly printed columns, ordered 100 rows, statistics and notes.
    Verified quick-mode NOVAC not-calculated text returns empty columns/rows,
    unavailable calculated_ions and the native explanation instead of zero data.
    Kinchin-Pease aggregate recoil columns retain no per-atom identity.
    Validate native identity, headings, units, widths, finite nonnegative data
    and increasing depth. Raise ValueError for unsupported/truncated formats.
    No input mutation, resource access or I/O occurs.
    """
    if not isinstance(filename, str) or filename.casefold() not in {p.casefold() for p in PROFILES}:
        raise ValueError("Unsupported TRIM profile filename")
    filename = next(p for p in PROFILES if p.casefold() == filename.casefold())
    lines = _trim_text(contents, filename)
    versions = [line.strip() for line in lines if re.fullmatch(r"\s*SRIM-(?:2008|2013)\.[\w.]+\s*", line)]
    count = re.findall(rf"Total Ions calculated\s*=\s*({NUMBER})", "\n".join(lines))
    unavailable_notes = ["Recoil/Damage Calculations made with Kinchin-Pease Estimates", "There are NO Replacement Collisions calculated.", "You must execute a full TRIM cascade calculation for these."]
    if filename == "NOVAC.txt" and len(versions) == 1 and not count and all(note in [line.strip() for line in lines] for note in unavailable_notes) and not any(re.match(r"\s*" + NUMBER + r"\s", line) for line in lines):
        return {"source": _trim_source(filename, contents), "version": versions[0], "calculated_ions": None, "title": "Replacement", "columns": [], "rows": [], "statistics": [], "notes": unavailable_notes}
    markers = [i for i, line in enumerate(lines) if re.fullmatch(r"\s*-{3,}(?:\s+-{3,})+\s*", line)]
    markers = [i for i in markers if i + 1 < len(lines) and re.match(r"\s*" + NUMBER + r"\s", lines[i + 1])]
    if len(versions) != 1 or len(count) != 1 or len(markers) != 1:
        raise ValueError(f"{filename}: missing/unsupported table header")
    start = markers[0]
    header = lines[max(0, start - 3):start]
    if not any("DEPTH" in line.upper() for line in header):
        raise ValueError(f"{filename}: missing depth heading")
    units = re.findall(r">{3,}\s*(.*?)\s*<{3,}", "\n".join(lines[:start]))
    unit = units[-1] if units else "Angstrom"
    required = {"RANGE.txt": "Distributions", "VACANCY.txt": "VACANCY", "IONIZ.txt": "IONIZATION", "PHONON.txt": "PHONONS", "E2RECOIL.txt": "RECOILS", "NOVAC.txt": "Replacement", "LATERAL.txt": "LATERAL"}[filename]
    if required.casefold() not in "\n".join(lines[:start]).casefold() or (filename != "LATERAL.txt" and not units):
        raise ValueError(f"{filename}: invalid title/units")
    rows: list[list[str]] = []
    width = len(lines[start].split())
    heading_width = width
    if filename == "E2RECOIL.txt":
        width = len(lines[start + 1].split())
    if width < 2 or (filename in ("IONIZ.txt", "PHONON.txt") and width != 3) or (filename == "NOVAC.txt" and width != 2) or (filename == "LATERAL.txt" and width != 5):
        raise ValueError(f"{filename}: unexpected column layout")
    previous: Decimal | None = None
    for line_number, line in enumerate(lines[start + 1:], start + 2):
        if not line.strip():
            continue
        if filename == "VACANCY.txt" and len(rows) == 100 and re.fullmatch(rf"\s*To convert to Energy (?:Lost -|Lo-) multiply by Average Binding Energy\s*=\s*{NUMBER}\s+eV/Vacancy\s*", line):
            continue
        tokens = line.split()
        if len(tokens) != width:
            raise ValueError(f"{filename}: malformed row at line {line_number}")
        signed_energy = filename == "E2RECOIL.txt" and versions[0] in ("SRIM-2008.04", "SRIM-2013.00") and width == 3 and "from" in " ".join(header) and "IONS" in " ".join(header) and "Absorbed" in " ".join(header)
        row = [_trim_number(t, f"{filename}:{line_number}", signed=signed_energy and i == 1) for i, t in enumerate(tokens)]
        depth = Decimal(row[0])
        if previous is not None and depth <= previous:
            raise ValueError(f"{filename}: nonincreasing depth")
        previous = depth
        rows.append(row)
    if len(rows) != 100:
        raise ValueError(f"{filename}: truncated/unsupported depth table ({len(rows)} rows)")
    columns: list[JSONObject] = []
    symbols: list[str] = []
    for line in header:
        candidates = [t for t in line.split() if re.fullmatch(r"[A-Z][a-z]?", t) and t not in ("A",)]
        if candidates:
            symbols = candidates
    labels = {"IONIZ.txt": ["depth", "ion ionization", "recoil ionization"], "PHONON.txt": ["depth", "ion phonons", "recoil phonons"], "NOVAC.txt": ["depth", "replacement collisions"], "LATERAL.txt": ["depth", "projected lateral range", "projected straggling", "radial range", "radial straggling"]}.get(filename)
    aggregate = filename in ("RANGE.txt", "VACANCY.txt", "E2RECOIL.txt") and width == 3 and any("Kinchin-Pease Estimates" in line for line in lines[:start])
    if aggregate:
        labels = {"RANGE.txt": ["depth", "incident ion distribution", "aggregate recoil distribution"], "VACANCY.txt": ["depth", "vacancies by ions", "aggregate vacancies by recoils"], "E2RECOIL.txt": ["depth", "energy transferred from ions", "aggregate energy absorbed by recoils"]}[filename]
    for i in range(width):
        atom = i - 1 if filename in ("RANGE.txt", "VACANCY.txt", "E2RECOIL.txt") and i >= 2 and not aggregate else None
        symbol = symbols[i - 1] if i > 0 and len(symbols) == width - 1 else None
        if aggregate and i == 2:
            symbol = None
        columns.append({"index": i, "label": labels[i] if labels else ("depth" if i == 0 else (" | ".join(h.strip() for h in header) + f" [column {i}]")), "unit": "Angstrom" if i == 0 or filename == "LATERAL.txt" else unit, "atom_entry_index": atom, "symbol": symbol})
    statistics = [{"name": m[1].strip(), "value": _trim_number(m[2], "statistic", signed=True), "unit": m[3].strip()} for line in lines[:start] if (m := re.fullmatch(rf"\s*([^=]+)=\s*({NUMBER})\s+([^=]+)", line)) and ("/Ion" in m[3] or m[3].strip() == "A")]
    notes = [line.strip() for line in lines[:start] if "NOTE" in line or "not accurate" in line or "less than" in line]
    if heading_width != width:
        notes.append("Native E2RECOIL heading omits printed data columns; their labels remain unavailable.")
    if aggregate:
        notes.append(AGGREGATE_NOTE)
    if filename == "E2RECOIL.txt" and any(Decimal(row[1]) < 0 for row in rows):
        notes.append(SIGNED_ENERGY_NOTE)
    return {"source": _trim_source(filename, contents), "version": versions[0], "calculated_ions": _trim_number(count[0], "ions"), "title": required, "columns": columns, "rows": rows, "statistics": statistics, "notes": notes}


def parse_trim_checkpoint(contents: bytes, trim_input: bytes) -> JSONObject:
    """Parse textual TDATA.sav contents and TRIMIN.sav trim_input without effects.

    Return saved counts, target records and initial native controls, retaining
    lexical count-line identity for explicit continuation edits. Reject missing,
    ambiguous, truncated or inconsistent supported records with ValueError.
    """
    lines = _trim_text(contents, "TDATA.sav")
    native = _trim_text(trim_input, "TRIMIN.sav")
    positions: dict[str, int] = {}
    headings = {"identity": "TRIM  Version", "projectile": "ION DATA:", "counts": "ION NUMB:", "mode": "Cascade Type,", "target": "TGT DATA: -------", "atoms": "TGT DATA: Atomic", "layers": "TGT Layers:"}
    for key, label in headings.items():
        found = [i + 1 for i, line in enumerate(lines) if line.startswith(label) or (key == "mode" and line.startswith("Cascades ("))]
        if len(found) != 1 or found[0] >= len(lines):
            raise ValueError(f"TDATA.sav: missing/ambiguous {key}")
        positions[key] = found[0]
    identity = shlex.split(lines[positions["identity"]])
    if len(identity) != 2 or not re.fullmatch(r"SRIM-(?:2008|2013)\.[\w.]+", identity[0]):
        raise ValueError("TDATA.sav: unsupported identity")
    projectile = lines[positions["projectile"]].split()
    counts = lines[positions["counts"]].split()
    target = lines[positions["target"]].split()
    flags = lines[positions["mode"]].split()
    if len(projectile) != 4 or len(counts) != 5 or len(target) != 2 or len(flags) not in (7, 9):
        raise ValueError("TDATA.sav: invalid scalar records")
    try:
        parsed_counts = {key: int(value) for key, value in zip(("current", "total", "autosave", "transmitted", "backscattered"), counts, strict=True)}
        atom_count, layer_count = map(int, target)
        flag_values = [int(v) for v in flags]
        if atom_count < 1 or layer_count < 1 or min(parsed_counts["current"], parsed_counts["total"], parsed_counts["transmitted"], parsed_counts["backscattered"]) < 0 or parsed_counts["current"] > parsed_counts["total"]:
            raise ValueError("invalid counts")
        atoms: list[JSONObject] = []
        for line in lines[positions["atoms"]:positions["atoms"] + atom_count]:
            tokens = line.split()
            if len(tokens) != 5:
                raise ValueError("invalid atom record")
            atoms.append({"atomic_number": int(tokens[0]), **{key: _trim_number(value, key) for key, value in zip(("mass_amu", *ENERGIES), tokens[1:], strict=True)}})
        layers: list[JSONObject] = []
        for line in lines[positions["layers"]:positions["layers"] + layer_count]:
            tokens = shlex.split(line)
            if len(tokens) != 4 + atom_count:
                raise ValueError("invalid layer record")
            layers.append({"name": tokens[0], "thickness_angstrom": _trim_number(tokens[1], "width"), "bottom_depth_angstrom": _trim_number(tokens[2], "depth"), "density_g_cm3": _trim_number(tokens[3], "density"), "stoichiometry": [_trim_number(t, "stoichiometry") for t in tokens[4:]]})
        if len(atoms) != atom_count or len(layers) != layer_count:
            raise ValueError("truncated target")
        ion_values = native[2].split()
        cascade_values = native[4].split()
        output_values = native[6].split()
        target_values = shlex.split(native[8])
        if len(ion_values) != 7 or len(cascade_values) != 3 or len(output_values) != 6 or list(map(int, target_values[-2:])) != [atom_count, layer_count]:
            raise ValueError("invalid saved native input")
        phase_heading = next(i for i, line in enumerate(native) if "Target layer phases" in line)
        phases = native[phase_heading + 1].split()
        corrections = native[phase_heading + 3].split()
        if len(phases) != layer_count or len(corrections) != layer_count:
            raise ValueError("invalid phases/corrections")
        for layer, phase, correction in zip(layers, phases, corrections, strict=True):
            if phase not in ("0", "1"):
                raise ValueError("invalid phase")
            layer.update({"phase": "solid" if phase == "0" else "gas", "correction_factor": _trim_number(correction, "correction")})
        native_input = {"total_ions": int(ion_values[4]), "autosave_ions": int(ion_values[6]), "random_seed": int(cascade_values[1]), "mode": int(cascade_values[0]), "outputs": output_values, "plot": native[10].split(), "native_fields": {"legacy_bragg": ion_values[5], "reminders": int(cascade_values[2]), "stopping_version": int(native[-1])}}
        labels = ["cascade_type", "backscatter", "transmit", "sputter", "rng_1", "rng_2", "reserved"] if len(flag_values) == 7 else ["cascade_type", "sputter", "ranges", "backscatter", "transmit", "collisions", "reserved", "rng_1", "rng_2"]
        return {"sources": [_trim_source("SRIM Restore/TDATA.sav", contents), _trim_source("SRIM Restore/TRIMIN.sav", trim_input)], "version": identity[0], "name": identity[1], "projectile": {"atomic_number": int(projectile[0]), **{key: _trim_number(value, key, signed=key == "angle_degrees") for key, value in zip(("mass_amu", "energy_keV", "angle_degrees"), projectile[1:], strict=True)}}, "counts": parsed_counts, "mode": flag_values[0], "flags": {"labels": labels, "values": flag_values}, "atoms": atoms, "layers": layers, "native_input": native_input, "count_line": {"line": positions["counts"] + 1, "before": lines[positions["counts"]]}}
    except (ValueError, IndexError, StopIteration) as error:
        raise ValueError(f"Invalid TRIM checkpoint: {error}") from error


def compare_trim_saved_values(checkpoint: JSONObject, inputs: ResolvedTRIMInputs, *, resumed: bool = False) -> JSONObject:
    """Compare checkpoint with independently verified inputs, returning field evidence.

    resumed exempts inherited count and plot changes only. Preserve every
    requested/observed value, using half printed quantum for numeric precision.
    Compare requested layer proportions as 28-digit decimal fractions against
    the saved fractions; original requests/native input bytes remain unchanged.
    Return checks/differences/unverified; malformed inputs raise ValueError.
    No mutation, file access or native operation occurs.
    """
    p = verify_trim_resolution(inputs)["parameters"]
    observed: list[tuple[str, object, object, bool]] = [("name", p["name"], checkpoint["name"], False), ("version", inputs.resolved["installation"]["version_text"].strip(), checkpoint["version"], False), ("mode", saved_trim_mode(inputs.resolved["installation"]["version_text"], MODES[p["engine_family"]][p["mode"]]), checkpoint["mode"], False)]
    for key, value in p["projectile"].items():
        observed.append(("projectile." + key, value, checkpoint["projectile"].get(key), isinstance(value, str)))
    for key in ("atoms", "layers"):
        observed.append((key + ".length", len(p[key]), len(checkpoint[key]), False))
        for i, (requested, saved) in enumerate(zip(p[key], checkpoint[key])):
            for field, value in requested.items():
                if isinstance(value, list):
                    observed.append((f"{key}[{i}].{field}.length", len(value), len(saved.get(field, [])), False))
                    total = sum((Decimal(v) for v in value), Decimal(0)) if field == "stoichiometry" else None
                    for j, (a, b) in enumerate(zip(value, saved.get(field, []))):
                        if total is not None:
                            with localcontext() as context:
                                context.prec = 28
                                a = str(Decimal(a) / total)
                        observed.append((f"{key}[{i}].{field}{'_fraction' if total is not None else ''}[{j}]", a, b, True))
                else:
                    observed.append((f"{key}[{i}].{field}", value, saved.get(field), isinstance(value, str) and field not in ("name", "phase")))
    for key in ("random_seed", "native_fields"):
        observed.append((key, p[key], checkpoint["native_input"].get(key), False))
    if not resumed:
        expected_plot = [str(PLOTS.index(p["plot"]["type"])), p["plot"]["minimum_depth_angstrom"], p["plot"]["maximum_depth_angstrom"]]
        if len(checkpoint["native_input"]["plot"]) != 3:
            raise ValueError("Invalid saved plot input")
        for i, (a, b) in enumerate(zip(expected_plot, checkpoint["native_input"]["plot"], strict=True)):
            observed.append((f"plot_input[{i}]", a, b, True))
    depth = Decimal(0)
    for i, layer in enumerate(p["layers"]):
        depth += Decimal(layer["thickness_angstrom"])
        if i < len(checkpoint["layers"]):
            observed.append((f"layers[{i}].bottom_depth_angstrom", str(depth), checkpoint["layers"][i]["bottom_depth_angstrom"], True))
    observed.append(("native_input.mode", MODES[p["engine_family"]][p["mode"]], checkpoint["native_input"]["mode"], False))
    flag_positions = (("backscatter", 1), ("transmission", 2), ("sputtering", 3)) if p["engine_family"] == "2008" else (("ranges", 2), ("backscatter", 3), ("transmission", 4), ("sputtering", 1))
    for key, index in flag_positions:
        observed.append(("outputs." + key, int(p["outputs"][key]), checkpoint["flags"]["values"][index], False))
    expected_outputs = [str(int(p["outputs"][key])) for key in ("ranges", "backscatter", "transmission", "sputtering")]
    expected_outputs += [str(("none", "ions", "ions-and-recoils").index(p["outputs"]["collisions"])), p["outputs"]["exyz_increment_eV"]]
    for i, (a, b) in enumerate(zip(expected_outputs, checkpoint["native_input"]["outputs"], strict=True)):
        observed.append((f"native_outputs[{i}]", a, b, True))
    if not resumed:
        for key, saved in (("total_ions", "total"), ("autosave_ions", "autosave")):
            observed.append((key, p[key], checkpoint["counts"][saved], False))
    checks: list[JSONObject] = []
    for field, requested, saved, numeric in observed:
        precision = "exact"
        equal = requested == saved
        if numeric and saved is not None:
            try:
                a, b = Decimal(str(requested)), Decimal(_trim_number(str(saved), field, signed=True))
                with localcontext() as context:
                    context.prec = max(28, len(a.as_tuple().digits) + len(b.as_tuple().digits) + 8)
                    quantum = Decimal(1).scaleb(b.as_tuple().exponent)
                    equal = abs(a - b) <= abs(quantum) / 2
                    precision = str(quantum)
            except DecimalException as error:
                raise ValueError("Saved comparison exceeds decimal precision") from error
        checks.append({"field": field, "requested": requested, "observed": saved, "precision": precision, "status": "equal" if equal else "different"})
    return {"schema_version": 1, "engine": "trim", "checks": checks, "differences": [c for c in checks if c["status"] == "different"], "unverified": [{"field": "binary_saved_arrays", "requested": None, "observed": None, "precision": "exact", "status": "unverified"}]}


def parse_trim_outputs(files: Mapping[str, bytes], request: JSONObject | None = None) -> JSONObject:
    """Parse available safe files and optional explicit request into a partial report.

    Return successes and per-file missing/format/ambiguity/consistency issues.
    Require byte-identical duplicates across native locations. Without request,
    report observed consistency only, never successful execution. Invalid mapping
    or request raises ValueError; native parse errors are retained in issues.
    No writes, filesystem reads or mutation occur.
    """
    if not isinstance(files, Mapping):
        raise ValueError("Expected native file mapping")
    if request is not None:
        validate_trim_engine_request(request)
    copied = dict(files)
    seen: set[str] = set()
    for name, payload in copied.items():
        _trim_source(name, payload)
        if name.casefold() in seen:
            raise ValueError("Case-colliding native files")
        seen.add(name.casefold())
    result: JSONObject = {"schema_version": 1, "engine": "trim", "summary": None, "profiles": {}, "checkpoint": None, "sources": [], "issues": [], "consistency": {"request_checked": request is not None, "checks": []}, "complete": False}
    selected: dict[str, bytes] = {}
    for filename in ("TDATA.txt", *PROFILES, "TDATA.sav", "TRIMIN.sav"):
        locations = {filename.casefold(), ("SRIM Outputs/" + filename).casefold()} if filename.endswith(".txt") else {("SRIM Restore/" + filename).casefold()}
        candidates = [(name, payload) for name, payload in copied.items() if name.casefold() in locations]
        result["sources"].extend(_trim_source(name, payload) for name, payload in candidates)
        if not candidates:
            if filename.endswith(".txt"):
                result["issues"].append({"path": filename, "code": "missing", "line": None, "message": "Native output is absent"})
            continue
        if len({payload for _, payload in candidates}) != 1:
            result["issues"].append({"path": filename, "code": "ambiguous", "line": None, "message": "Native output locations differ"})
            continue
        payload = candidates[0][1]
        selected[filename] = payload
        if filename.endswith(".sav"):
            continue
        try:
            parsed = parse_trim_summary(payload) if filename == "TDATA.txt" else parse_trim_profile(filename, payload)
            parsed["source"] = _trim_source(candidates[0][0], payload)
            if filename == "TDATA.txt":
                result["summary"] = parsed
            else:
                result["profiles"][filename] = parsed
        except ValueError as error:
            result["issues"].append({"path": filename, "code": "truncated" if "truncat" in str(error) else "format", "line": None, "message": str(error)})
    if "TDATA.sav" in selected and "TRIMIN.sav" in selected:
        try:
            result["checkpoint"] = parse_trim_checkpoint(selected["TDATA.sav"], selected["TRIMIN.sav"])
        except ValueError as error:
            result["issues"].append({"path": "SRIM Restore", "code": "format", "line": None, "message": str(error)})
    summary = result["summary"]
    if summary is not None:
        expected_count = summary["counts"]["calculated"]
        reference_depths: list[Decimal] | None = None
        for filename, profile in result["profiles"].items():
            unavailable = filename == "NOVAC.txt" and profile["calculated_ions"] is None and not profile["rows"]
            equal = (unavailable or Decimal(profile["calculated_ions"]) == expected_count) and profile["version"] == summary["version"]
            result["consistency"]["checks"].append({"field": filename, "status": "equal" if equal else "different", "detail": "Native explicitly states replacement collisions were not calculated; no count/data inferred" if unavailable else "Native version and calculated ion count"})
            if not equal:
                result["issues"].append({"path": filename, "code": "inconsistent", "line": None, "message": "Native count/version differs from summary"})
            if unavailable:
                quick_mode: bool | None = request["parameters"]["mode"].endswith("-quick") if request is not None else None
                if result["checkpoint"] is not None:
                    family = "2008" if result["checkpoint"]["version"].startswith("SRIM-2008.") else "2013"
                    saved_quick = saved_trim_mode_name(result["checkpoint"]["version"], result["checkpoint"]["mode"]).endswith("-quick")
                    quick_mode = saved_quick if quick_mode is None else quick_mode and saved_quick
                if quick_mode is False:
                    result["issues"].append({"path": filename, "code": "inconsistent", "line": None, "message": "Full-cascade mode requires its replacement-collision table"})
                continue
            depths = [Decimal(row[0]) for row in profile["rows"]]
            if reference_depths is None:
                reference_depths = depths
            elif depths != reference_depths:
                result["issues"].append({"path": filename, "code": "inconsistent", "line": None, "message": "Depth grid differs from other native profiles"})
            header = "\n".join(_trim_text(selected[filename], filename))
            projectile = re.findall(rf"Ion\s*=\s*(\S+)\s+Energy\s*=\s*({NUMBER})\s*keV", header)
            if len(projectile) != 1 or projectile[0][0] != summary["projectile"]["symbol"] or abs(Decimal(projectile[0][1]) - Decimal(summary["projectile"]["energy_keV"])) > Decimal("0.5").scaleb(Decimal(projectile[0][1]).as_tuple().exponent):
                result["issues"].append({"path": filename, "code": "inconsistent", "line": None, "message": "Projectile header differs from summary"})
        checkpoint = result["checkpoint"]
        if checkpoint is not None:
            native_equal = checkpoint["version"] == summary["version"] and checkpoint["name"] == summary["name"] and checkpoint["counts"]["current"] == expected_count and checkpoint["projectile"]["atomic_number"] == summary["projectile"]["atomic_number"] and len(checkpoint["layers"]) == len(summary["layers"]) and len(checkpoint["atoms"]) == len(summary["target_energies"])
            if not native_equal:
                result["issues"].append({"path": "SRIM Restore", "code": "inconsistent", "line": None, "message": "Checkpoint identity/count/target differs from summary"})
        if request is not None:
            p = request["parameters"]
            for field, actual, expected in (("projectile.atomic_number", summary["projectile"]["atomic_number"], p["projectile"]["atomic_number"]), ("layers.length", len(summary["layers"]), len(p["layers"])), ("counts.calculated", expected_count, p["total_ions"])):
                equal = actual == expected
                result["consistency"]["checks"].append({"field": field, "status": "equal" if equal else "different", "detail": f"Observed {actual}; requested {expected}"})
                if not equal:
                    result["issues"].append({"path": "TDATA.txt", "code": "inconsistent", "line": None, "message": field + " differs from request"})
            comparisons: list[tuple[str, object, object, bool]] = [("name", p["name"], summary["name"], False)]
            comparisons.extend(("projectile." + key, value, summary["projectile"].get(key), isinstance(value, str)) for key, value in p["projectile"].items())
            depth = Decimal(0)
            for i, (layer, saved) in enumerate(zip(p["layers"], summary["layers"])):
                depth += Decimal(layer["thickness_angstrom"])
                comparisons.extend(((f"layers[{i}].name", layer["name"], saved["name"], False), (f"layers[{i}].bottom_depth_angstrom", str(depth), saved["bottom_depth_angstrom"], True), (f"layers[{i}].density_g_cm3", layer["density_g_cm3"], saved["density_g_cm3"], True)))
            comparisons.append(("target_energies.length", len(p["atoms"]), len(summary["target_energies"]), False))
            for i, (atom, saved) in enumerate(zip(p["atoms"], summary["target_energies"])):
                comparisons.extend((f"atoms[{i}].{key}", atom[key], saved[key], True) for key in ENERGIES)
            for field, expected, actual, numeric in comparisons:
                equal = expected == actual
                if numeric and actual is not None:
                    printed = Decimal(str(actual))
                    equal = abs(Decimal(str(expected)) - printed) <= Decimal("0.5").scaleb(printed.as_tuple().exponent)
                result["consistency"]["checks"].append({"field": field, "status": "equal" if equal else "different", "detail": f"Observed {actual}; requested {expected}"})
                if not equal:
                    result["issues"].append({"path": "TDATA.txt", "code": "inconsistent", "line": None, "message": field + " differs from request"})
            for filename in ("RANGE.txt", "VACANCY.txt", "E2RECOIL.txt"):
                profile = result["profiles"].get(filename)
                expected_width = 3 if profile is not None and AGGREGATE_NOTE in profile["notes"] else len(p["atoms"]) + 2
                if profile is not None and len(profile["columns"]) != expected_width:
                    result["issues"].append({"path": filename, "code": "inconsistent", "line": None, "message": "Profile target-entry columns differ from explicit request"})
    result["sources"].sort(key=itemgetter("path"))
    result["complete"] = summary is not None and len(result["profiles"]) == len(PROFILES) and not result["issues"]
    return deepcopy(result)
