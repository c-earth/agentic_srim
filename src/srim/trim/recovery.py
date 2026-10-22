"""Recover explicitly labeled profiles from reviewed frozen TRIM checkpoints."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, DecimalException, localcontext
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
from src.srim.common.types import JSONObject
from src.srim.trim.outputs import parse_trim_checkpoint, saved_trim_mode, saved_trim_mode_name


BASE_FILES = frozenset({"e2recoil.sav", "ioniz.sav", "novac.sav", "phonon.sav", "sputter.sav", "stopping.sav", "tdata.sav", "trimin.sav", "vacancy.sav", "xrange.sav", "yzradial#.sav", "yzrange#.sav"})


EXTRA_FILES = frozenset({"ioniz3d.sav", "novac3d.sav", "phonon3d.sav", "range3d.sav", "recoil3d.sav", "vac3d.sav"})


PROFILE_LAYOUTS = {
    "RANGE.txt": ("xrange.sav", "target_entries", ("ions", "recoils"), "cm^-1"),
    "VACANCY.txt": ("vacancy.sav", "target_entries", ("ion_vacancies", "recoil_vacancies"), "events/angstrom/incident_ion"),
    "IONIZ.txt": ("ioniz.sav", "aggregate", ("ion_ionization", "recoil_ionization"), "eV/angstrom/incident_ion"),
    "PHONON.txt": ("phonon.sav", "aggregate", ("ion_phonons", "recoil_phonons"), "eV/angstrom/incident_ion"),
    "E2RECOIL.txt": ("e2recoil.sav", "target_entries", ("ion_energy_to_recoils", "recoil_energy_to_recoils"), "eV/angstrom/incident_ion"),
    "NOVAC.txt": ("novac.sav", "aggregate", ("replacement_collisions",), "events/angstrom/incident_ion"),
}


LATERAL_COLUMNS = ("projected_range", "projected_straggling", "radial_range", "radial_straggling")


FORMAT = "trim-checkpoint-recovery-v2"


RESULT_KEYS = frozenset({"schema_version", "format", "provenance", "checkpoint", "grid", "summary", "profiles", "warnings"})


def _decode_numeric_variants(contents: bytes, *, source: str, expected_count: int) -> tuple[Decimal, ...]:
    """Return expected_count saved numeric values from contents identified by source.

    Decode reviewed VB Empty/Single/Double/Decimal records without effects.
    Raise ValueError with source and offset for malformed or nonfinite records.
    """
    if not isinstance(contents, bytes) or type(expected_count) is not int or expected_count < 0:
        raise ValueError(f"{source}: invalid numeric buffer/count at byte 0")
    values: list[Decimal] = []
    offset = 0
    start = 0
    try:
        while offset < len(contents):
            start = offset
            tag = struct.unpack_from("<H", contents, offset)[0]
            offset += 2
            if tag == 0:
                value = Decimal(0)
            elif tag in (4, 5):
                value = Decimal.from_float(struct.unpack_from("<f" if tag == 4 else "<d", contents, offset)[0])
                offset += 4 if tag == 4 else 8
            elif tag == 14:
                reserved, scale, sign, high, low = struct.unpack_from("<HBBIQ", contents, offset)
                offset += 16
                if reserved != 14 or scale > 28 or sign not in (0, 128):
                    raise ValueError("invalid Decimal fields")
                magnitude = high * 2**64 + low
                value = Decimal((int(sign == 128), tuple(map(int, str(magnitude))), -scale))
            else:
                raise ValueError(f"unsupported Variant tag {tag}")
            if not value.is_finite():
                raise ValueError("nonfinite value")
            values.append(value)
            if len(values) > expected_count:
                raise ValueError("extra records")
        if len(values) != expected_count:
            start = offset
            raise ValueError(f"expected {expected_count} records, found {len(values)}")
    except (struct.error, ValueError) as error:
        raise ValueError(f"{source}: byte {start}: {error}") from error
    return tuple(values)


def _decode_double_pairs(contents: bytes, *, source: str, expected_rows: int) -> tuple[tuple[Decimal, Decimal], ...]:
    """Return expected_rows binary64 pairs from contents identified by source.

    Perform no effects; reject invalid lengths/counts or nonfinite values with
    ValueError. Decimal values preserve the exact stored binary numbers.
    """
    if not isinstance(contents, bytes) or type(expected_rows) is not int or expected_rows < 0 or len(contents) != expected_rows * 16:
        raise ValueError(f"{source}: invalid double-pair length")
    pairs = tuple((Decimal.from_float(a), Decimal.from_float(b)) for a, b in struct.iter_unpack("<dd", contents))
    if any(not value.is_finite() for pair in pairs for value in pair):
        raise ValueError(f"{source}: nonfinite double pair")
    return pairs


def _recover_layout(checkpoint: JSONObject, files: Mapping[str, bytes]) -> JSONObject:
    """Derive array layout from parsed checkpoint metadata and frozen files.

    checkpoint supplies target entries, layers, semantic mode and saved plot
    tokens. files supplies case-unique checkpoint bytes. Return dynamic grid,
    entry/layer associations, profile descriptors and inferred-rule identities.
    Validate saved precision, shapes and geometry; raise ValueError on ambiguous
    or contradictory data. Use private Decimal precision without effects.
    """
    try:
        mapping = {name.casefold(): data for name, data in files.items()}
        lateral_size = len(mapping["yzrange#.sav"])
        if not lateral_size or lateral_size % 16 or lateral_size != len(mapping["yzradial#.sav"]):
            raise ValueError("Inconsistent lateral/radial array lengths")
        rows = lateral_size // 16
        if not checkpoint["atoms"] or not checkpoint["layers"]:
            raise ValueError("Empty target-entry/layer metadata")
        with localcontext() as context:
            context.prec = 80
            entries: list[JSONObject] = []
            for index, atom in enumerate(checkpoint["atoms"], 1):
                mass = Decimal(atom["mass_amu"])
                if not mass.is_finite() or mass <= 0 or type(atom["atomic_number"]) is not int or atom["atomic_number"] <= 0:
                    raise ValueError("Invalid target-entry identity/mass")
                entries.append({"id": f"target_{index:03}_Z{atom['atomic_number']}", "index": index, "atomic_number": atom["atomic_number"], "mass_amu": atom["mass_amu"]})
            layers: list[JSONObject] = []
            cumulative = Decimal(0)
            uncertainty = Decimal(0)
            previous_bottom = Decimal(0)
            for index, layer in enumerate(checkpoint["layers"], 1):
                thickness = Decimal(layer["thickness_angstrom"])
                bottom = Decimal(layer["bottom_depth_angstrom"])
                density = Decimal(layer["density_g_cm3"])
                stoichiometry = list(map(Decimal, layer["stoichiometry"]))
                if any(not value.is_finite() for value in [thickness, bottom, density, *stoichiometry]) or min(thickness, density) <= 0 or bottom <= previous_bottom or len(stoichiometry) != len(entries) or min(stoichiometry) < 0 or sum(stoichiometry) <= 0 or layer["phase"] not in ("solid", "gas"):
                    raise ValueError(f"Invalid layer geometry/stoichiometry at layer {index}")
                cumulative += thickness
                uncertainty += Decimal(10) ** thickness.as_tuple().exponent / 2
                if abs(bottom - cumulative) > uncertainty + Decimal(10) ** bottom.as_tuple().exponent / 2:
                    raise ValueError(f"Inconsistent cumulative depth at layer {index}")
                previous_bottom = bottom
                layers.append({"index": index, "name": layer["name"], "phase": layer["phase"], "thickness_angstrom": layer["thickness_angstrom"], "bottom_depth_angstrom": layer["bottom_depth_angstrom"], "density_g_cm3": layer["density_g_cm3"], "target_entries": [{"id": entry["id"], "stoichiometry": token} for entry, token in zip(entries, layer["stoichiometry"], strict=True)]})
            start, end, saved_width = map(Decimal, checkpoint["saved_plot_tokens"])
            if any(not value.is_finite() for value in (start, end, saved_width)) or start < 0 or end <= start or saved_width <= 0:
                raise ValueError("Invalid saved plot interval/resolution")
            width = (end - start) / rows
            tolerance = Decimal(10) ** saved_width.as_tuple().exponent / 2 + (Decimal(10) ** start.as_tuple().exponent + Decimal(10) ** end.as_tuple().exponent) / (2 * rows)
            if abs(width - saved_width) > tolerance:
                raise ValueError("Plot resolution disagrees with array length and interval")
            inferred = []
            if start != 0:
                inferred.append("nonzero_plot_start")
            if width != width.to_integral_value():
                inferred.append("fractional_bin_width")
            if rows != 100:
                inferred.append("unobserved_row_count")
            if any(layer["phase"] == "gas" for layer in layers):
                inferred.append("gas_layer")
            grid = {"start": str(start), "end": str(end), "saved_resolution": str(saved_width), "bin_width": str(width), "row_count": rows, "depth": [str(start + (row + 1) * width + Decimal(".01")) for row in range(rows)], "convention": "native_bin_plus_0.01_angstrom", "evidence": "inferred" if inferred else "metadata_driven_observed_layout"}
            descriptors: JSONObject = {}
            quick = checkpoint["semantic_mode"].endswith("quick")
            for name, (saved, storage, roles, units) in PROFILE_LAYOUTS.items():
                target_columns = [entry["id"] + "_" + roles[-1] for entry in entries] if storage == "target_entries" else []
                columns = [roles[0], *target_columns] if storage == "target_entries" and not quick else list(roles)
                descriptors[name] = {"saved_file": saved, "storage_width": len(entries) + 1 if storage == "target_entries" else len(roles), "storage_kind": storage, "aggregation": "summed_target_entries" if storage == "target_entries" and quick else "none", "target_columns": target_columns, "columns": ["depth", *columns], "units": ["angstrom", *[units] * len(columns)]}
            descriptors["LATERAL.txt"] = {"storage_width": 2, "storage_kind": "double_pairs", "aggregation": "none", "target_columns": [], "columns": ["depth", *LATERAL_COLUMNS], "units": ["angstrom"] * 5}
    except (KeyError, TypeError, IndexError, DecimalException, ValueError) as error:
        raise ValueError(f"Invalid checkpoint layout: {error}") from error
    return {"row_count": rows, "target_entries": entries, "layers": layers, "profiles": descriptors, "grid": grid, "evidence": grid["evidence"], "inferred_rules": inferred}


def _recover_metadata(files: Mapping[str, bytes]) -> JSONObject:
    """Validate frozen checkpoint files and return saved identity and summary.

    files maps safe flat names to bytes. Require a complete reviewed version,
    completed positive count, consistent semantic mode and coherent geometry.
    Preserve stored summary tokens, initial seed and generator state separately.
    Raise ValueError on unsupported/inconsistent data; perform no effects.
    """
    if not isinstance(files, Mapping):
        raise ValueError("Checkpoint files must be a mapping")
    names: dict[str, str] = {}
    for name, data in files.items():
        if not isinstance(name, str) or not name or name in (".", "..") or name.endswith((".", " ")) or os.path.isreserved(name) or re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or not isinstance(data, bytes) or name.casefold() in names:
            raise ValueError("Unsafe, duplicate or invalid checkpoint entry")
        names[name.casefold()] = name
    if not BASE_FILES <= set(names):
        raise ValueError("Incomplete checkpoint: missing base saved files")
    checkpoint = parse_trim_checkpoint(files[names["tdata.sav"]], files[names["trimin.sav"]])
    if checkpoint["version"] not in ("SRIM-2008.04", "SRIM-2013.00"):
        raise ValueError("Unsupported checkpoint version")
    if checkpoint["version"] == "SRIM-2013.00" and not EXTRA_FILES <= set(names):
        raise ValueError("Incomplete 2013 checkpoint: missing 3D saved files")
    mode = saved_trim_mode_name(checkpoint["version"], checkpoint["mode"])
    if saved_trim_mode(checkpoint["version"], checkpoint["native_input"]["mode"]) != checkpoint["mode"]:
        raise ValueError("Inconsistent saved/input modes")
    counts = checkpoint["counts"]
    if counts["current"] <= 0 or counts["current"] != counts["total"] or counts["transmitted"] + counts["backscattered"] > counts["current"]:
        raise ValueError("Recovery requires a completed positive checkpoint and valid counts")
    raw = files[names["tdata.sav"]].decode("cp1252")
    lines = raw.splitlines()
    selected: dict[str, list[str]] = {}
    for key, heading, width in (("averages", "ION AVGS:", 3), ("moments", "ION SUMS:", 8), ("lateral", "Ion Y-Z Lateral Data", 4), ("grid", "Plot Display Start", 3)):
        indices = [i + 1 for i, line in enumerate(lines) if line.startswith(heading)]
        if len(indices) != 1 or indices[0] >= len(lines):
            raise ValueError(f"Missing/ambiguous saved {key}")
        tokens = lines[indices[0]].split()
        try:
            if len(tokens) != width or any(not Decimal(token).is_finite() for token in tokens):
                raise ValueError(f"Invalid saved {key}")
        except DecimalException as error:
            raise ValueError(f"Invalid saved {key}") from error
        selected[key] = tokens
        if key == "lateral":
            if indices[0] + 1 >= len(lines):
                raise ValueError("Missing saved lateral averages")
            extra = lines[indices[0] + 1].split()
            try:
                if len(extra) not in (4, 5) or any(not Decimal(token).is_finite() for token in extra):
                    raise ValueError("Invalid saved lateral averages")
            except DecimalException as error:
                raise ValueError("Invalid saved lateral averages") from error
            selected["lateral_averages"] = extra
    checkpoint["semantic_mode"] = mode
    checkpoint["saved_plot_tokens"] = selected["grid"]
    checkpoint["layout"] = _recover_layout(checkpoint, files)
    checkpoint["saved_generator_state"] = {label: value for label, value in zip(checkpoint["flags"]["labels"], checkpoint["flags"]["values"], strict=True) if label.startswith("rng_")}
    return {"checkpoint": checkpoint, "selected": selected, "raw_saved_text": raw, "sources": [{"path": name, "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()} for name, data in sorted(files.items())]}


def recover_trim_checkpoint(files: Mapping[str, bytes]) -> JSONObject:
    """Recover standard profiles from complete frozen files without I/O or mutation.

    Return recovery-v2 JSON with stored summary, cumulative normalized profiles,
    source hashes and explicit precision/scope warnings. Quick NOVAC is absent
    measurement, never invented zeros. Reject invalid arrays or unsupported
    checkpoint/geometry with ValueError. Use a private 80-digit Decimal context.
    """
    metadata = _recover_metadata(files)
    checkpoint = metadata["checkpoint"]
    layout = checkpoint["layout"]
    row_count = layout["row_count"]
    mapping = {name.casefold(): data for name, data in files.items()}
    warnings = [{"code": "native_format_not_reproduced", "message": "Recovered files are not native TRIM exports or native-parser substitutes."}, {"code": "saved_precision_limits", "message": "Stored precision and native arithmetic/formatting differences prevent exact native equivalence."}, {"code": "layout_evidence", "message": "Metadata-driven dimensions and target-entry identities; layout evidence does not certify every composition or parameter combination."}]
    if layout["inferred_rules"]:
        warnings.append({"code": "inferred_geometry", "message": "Structurally consistent but not natively verified: " + ", ".join(layout["inferred_rules"]) + "."})
    profiles: JSONObject = {}
    with localcontext() as context:
        context.prec = 80
        count = Decimal(checkpoint["counts"]["current"])
        depth = layout["grid"]["depth"]
        for filename, (saved, storage, roles, units) in PROFILE_LAYOUTS.items():
            descriptor = layout["profiles"][filename]
            storage_width = descriptor["storage_width"]
            values = _decode_numeric_variants(mapping[saved], source=saved, expected_count=row_count * storage_width)
            available = not (filename == "NOVAC.txt" and checkpoint["semantic_mode"].endswith("quick"))
            factor = Decimal("1e8") / count if filename == "RANGE.txt" else Decimal(1) / count
            rows: list[list[str]] = []
            stored_target_rows: list[list[str]] = []
            for row in range(row_count):
                vector = values[row * storage_width:(row + 1) * storage_width]
                if descriptor["aggregation"] == "summed_target_entries":
                    stored_target_rows.append(list(map(str, vector[1:])))
                    vector = (vector[0], sum(vector[1:], Decimal(0)))
                if available:
                    rows.append([depth[row], *[str(value * factor) for value in vector]])
            profiles[filename] = {"availability": "available" if available else "not_calculated", "reason": None if available else "Kinchin-Pease modes do not calculate replacement collisions.", "columns": descriptor["columns"], "units": descriptor["units"], "rows": rows}
            if descriptor["aggregation"] == "summed_target_entries":
                profiles[filename]["stored_target_components"] = {"entry_ids": [entry["id"] for entry in layout["target_entries"]], "units": "unscaled_saved_accumulator", "rows": stored_target_rows}
        ion_stride = layout["profiles"]["RANGE.txt"]["storage_width"]
        ion_bins = _decode_numeric_variants(mapping["xrange.sav"], source="XRANGE.sav", expected_count=row_count * ion_stride)
        lateral = _decode_double_pairs(mapping["yzrange#.sav"], source="YZRANGE#.sav", expected_rows=row_count)
        radial = _decode_double_pairs(mapping["yzradial#.sav"], source="YZRADIAL#.sav", expected_rows=row_count)
        lateral_rows: list[list[str]] = []
        for row, ((sum_y, sum_y2), (sum_r, sum_r2)) in enumerate(zip(lateral, radial, strict=True)):
            bin_count = ion_bins[ion_stride * row] * Decimal(layout["grid"]["bin_width"])
            n = round(bin_count)
            if n < 0 or abs(bin_count - n) > Decimal(".001"):
                raise ValueError(f"LATERAL.txt: invalid bin count at row {row + 1}")
            if n == 0:
                if any(value != 0 for value in (sum_y, sum_y2, sum_r, sum_r2)):
                    raise ValueError(f"LATERAL.txt: nonzero moments in empty row {row + 1}")
                values = (Decimal(0),) * 4
            else:
                if min(sum_y, sum_y2, sum_r, sum_r2) < 0:
                    raise ValueError(f"LATERAL.txt: negative moments at row {row + 1}")
                mean_r = sum_r / n
                second = sum_r2 / n
                variance = second - mean_r**2
                if variance < 0:
                    if -variance > Decimal("1e-12") * max(Decimal(1), abs(second), mean_r**2):
                        raise ValueError(f"LATERAL.txt: negative variance at row {row + 1}")
                    warnings.append({"code": "radial_variance_roundoff", "message": f"Clamped small negative radial variance at row {row + 1}."})
                    variance = Decimal(0)
                values = (sum_y / n, (sum_y2 / n).sqrt(), mean_r, variance.sqrt())
            lateral_rows.append([depth[row], *map(str, values)])
        profiles["LATERAL.txt"] = {"availability": "available", "reason": None, "columns": ["depth", *LATERAL_COLUMNS], "units": ["angstrom"] * 5, "rows": lateral_rows}
    return {"schema_version": 2, "format": FORMAT, "provenance": {"recovered": True, "method": "metadata_driven_saved_accumulators", "sources": metadata["sources"]}, "checkpoint": checkpoint, "grid": layout["grid"], "summary": {"raw_saved_text": metadata["raw_saved_text"], "stored_averages": metadata["selected"]["averages"], "stored_moments": metadata["selected"]["moments"], "stored_lateral_values": {"sums": metadata["selected"]["lateral"], "averages": metadata["selected"]["lateral_averages"]}, "unavailable": [{"field": "exact_native_only_statistics", "value": None, "reason": "Rounded saved moments cannot establish every original native statistic at its printed precision."}]}, "profiles": profiles, "warnings": warnings}


def _render_recovered_files(result: JSONObject) -> dict[str, bytes]:
    """Render validated result into eight recovered texts and three sidecar files.

    Return UTF-8/LF byte mappings for profiles, saved summary, JSON provenance,
    manifest and README. Validate schema and finite numeric rows; ValueError
    rejects malformed input. Perform no I/O or mutation; native format differs.
    """
    try:
        if set(result) != RESULT_KEYS or type(result["schema_version"]) is not int or result["schema_version"] != 2 or result["format"] != FORMAT or set(result["profiles"]) != set(PROFILE_LAYOUTS) | {"LATERAL.txt"}:
            raise ValueError("Invalid recovery schema")
        canonical = json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + "\n"
        if result["provenance"]["recovered"] is not True or type(result["checkpoint"]["counts"]["current"]) is not int or result["checkpoint"]["counts"]["current"] <= 0:
            raise ValueError("Invalid recovered identity")
        layout = result["checkpoint"]["layout"]
        row_count = layout["row_count"]
        if type(row_count) is not int or row_count <= 0 or result["grid"] != layout["grid"] or result["grid"]["row_count"] != row_count or len(result["grid"]["depth"]) != row_count:
            raise ValueError("Invalid grid/layout shape")
        with localcontext() as context:
            context.prec = 80
            start = Decimal(result["grid"]["start"])
            end = Decimal(result["grid"]["end"])
            width = Decimal(result["grid"]["bin_width"])
            if any(not value.is_finite() for value in (start, end, width)) or start < 0 or end <= start or width != (end - start) / row_count or any(Decimal(token) != start + (row + 1) * width + Decimal(".01") for row, token in enumerate(result["grid"]["depth"])):
                raise ValueError("Invalid grid coordinates")
        for tokens in (result["summary"]["stored_averages"], result["summary"]["stored_moments"], *result["summary"]["stored_lateral_values"].values()):
            if any(not isinstance(token, str) or not Decimal(token).is_finite() for token in tokens):
                raise ValueError("Invalid stored summary numeric tokens")
        output: dict[str, bytes] = {}
        title = f"{result['checkpoint']['version']}; {result['checkpoint']['semantic_mode']}; cumulative completed ions = {result['checkpoint']['counts']['current']}"
        for name, profile in result["profiles"].items():
            descriptor = layout["profiles"][name]
            columns = descriptor["columns"]
            if profile["columns"] != columns or profile["units"] != descriptor["units"]:
                raise ValueError(f"{name}: invalid columns/units")
            if descriptor["aggregation"] == "summed_target_entries":
                components = profile["stored_target_components"]
                if components["entry_ids"] != [entry["id"] for entry in layout["target_entries"]] or components["units"] != "unscaled_saved_accumulator" or len(components["rows"]) != row_count or any(len(row) != len(components["entry_ids"]) or any(not isinstance(token, str) or not Decimal(token).is_finite() for token in row) for row in components["rows"]):
                    raise ValueError("Invalid stored target components")
            body = ["RECOVERED FROM CHECKPOINT; not a native TRIM export.", title, " ".join(f"{column}[{unit}]" for column, unit in zip(columns, profile["units"], strict=True))]
            if profile["availability"] == "not_calculated":
                if name != "NOVAC.txt" or not result["checkpoint"]["semantic_mode"].endswith("quick") or profile["rows"] or not profile["reason"]:
                    raise ValueError("Invalid unavailable profile")
                body.append("NOT CALCULATED: " + profile["reason"])
            elif profile["availability"] == "available" and profile["reason"] is None and len(profile["rows"]) == row_count:
                for index, row in enumerate(profile["rows"]):
                    if len(row) != len(columns) or any(not isinstance(token, str) or not Decimal(token).is_finite() for token in row) or Decimal(row[0]) != Decimal(result["grid"]["depth"][index]):
                        raise ValueError(f"{name}: invalid numeric row")
                    body.append(" ".join(format(Decimal(token), ".15E") for token in row))
            else:
                raise ValueError("Invalid profile availability/shape")
            output[name] = ("\n".join(body) + "\n").encode("utf-8")
        output["TDATA.txt"] = ("RECOVERED FROM CHECKPOINT; saved summary, not native TDATA export.\n" + result["summary"]["raw_saved_text"].replace("\r\n", "\n").replace("\r", "\n").rstrip("\n") + "\n").encode("utf-8")
        output["recovery.json"] = canonical.encode("utf-8")
        output["README.txt"] = ("Checkpoint recovery v2: explicitly recovered files, not native TRIM exports or native-parser substitutes.\nAll profile values are cumulative through the completed count and normalized by that count.\nUnits and column order are written in every profile. Empty quick-mode NOVAC is not calculated, not zero.\nDistinct target-entry indices remain distinct even when atomic numbers match. Quick target components in recovery.json are unscaled saved accumulators, not separate tracked histories.\nTDATA.txt retains the saved summary at stored precision; native-only statistics may be unavailable.\nFifteen fractional scientific-notation digits preserve stored values, not physical accuracy.\nLayout dimensions and grids are metadata-driven. Shape evidence does not certify every mode/geometry combination.\nKnown native printing/arithmetic discrepancies remain; exact native precision/format is not reproduced.\nrecovery.json contains values, layout evidence, warnings and source hashes. manifest.json hashes outputs except itself.\nSources must be frozen; file-change detection cannot guarantee checkpoint atomicity.\n" + "\n".join(f"WARNING {item['code']}: {item['message']}" for item in result["warnings"]) + "\n").encode("utf-8")
        manifest = {"schema_version": 2, "format": FORMAT, "sources": result["provenance"]["sources"], "outputs": [{"path": name, "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()} for name, data in sorted(output.items())], "scope": {"row_count": row_count, "target_entries": len(layout["target_entries"]), "layers": len(layout["layers"]), "grid": result["grid"], "evidence": layout["evidence"], "inferred_rules": layout["inferred_rules"]}, "warnings": result["warnings"]}
        output["manifest.json"] = (json.dumps(manifest, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    except (KeyError, TypeError, IndexError, DecimalException, ValueError) as error:
        raise ValueError(f"Invalid recovery result: {error}") from error
    return output


def export_trim_checkpoint_recovery(source: Path, destination: Path) -> JSONObject:
    """Read frozen source and exclusively export recovery artifacts to destination.

    Both Path arguments must be absolute. Reject source reparse/symlink entries,
    nesting, unsafe files, existing destinations and destinations inside source.
    Validate/render before creating output; recheck source and written hashes.
    Return output inventory/identity/warnings. ValueError rejects changed/invalid
    source, OSError propagates I/O errors. Retain partial destination on failure
    and annotate it; never overwrite, delete or modify source files.
    """
    if not isinstance(source, Path) or not isinstance(destination, Path) or not source.is_absolute() or not destination.is_absolute():
        raise ValueError("Source/destination must be absolute Paths")
    source_stat = source.lstat()
    if not stat.S_ISDIR(source_stat.st_mode) or source.is_symlink() or getattr(source_stat, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError("Source must be a regular frozen directory")
    resolved_source = source.resolve()
    resolved_destination = destination.resolve()
    if resolved_destination == resolved_source or resolved_destination.is_relative_to(resolved_source):
        raise ValueError("Destination must be outside source")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(str(destination))
    files: dict[str, bytes] = {}
    for entry in source.iterdir():
        info = entry.lstat()
        if not stat.S_ISREG(info.st_mode) or entry.is_symlink() or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError(f"Unsafe source entry: {entry.name}")
        files[entry.name] = entry.read_bytes()
    result = recover_trim_checkpoint(files)
    rendered = _render_recovered_files(result)
    try:
        for phase in ("before publication", "after publication"):
            entries = list(source.iterdir())
            if {entry.name for entry in entries} != set(files):
                raise ValueError(f"Source inventory changed {phase}")
            for entry in entries:
                info = entry.lstat()
                if not stat.S_ISREG(info.st_mode) or entry.is_symlink() or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT or hashlib.sha256(entry.read_bytes()).digest() != hashlib.sha256(files[entry.name]).digest():
                    raise ValueError(f"Source changed {phase}: {entry.name}")
            if phase == "before publication":
                destination.mkdir()
                for name, data in rendered.items():
                    with (destination / name).open("xb") as stream:
                        stream.write(data)
                for name, data in rendered.items():
                    if (destination / name).read_bytes() != data:
                        raise OSError(f"Recovery output verification failed: {name}")
    except (OSError, ValueError) as error:
        error.add_note(f"Recovery destination (partial output retained if created): {destination}")
        raise
    return {"destination": str(destination), "files": [{"path": name, "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)} for name, data in sorted(rendered.items())], "version": result["checkpoint"]["version"], "mode": result["checkpoint"]["semantic_mode"], "counts": result["checkpoint"]["counts"], "layout": result["checkpoint"]["layout"], "evidence": result["checkpoint"]["layout"]["evidence"], "warnings": result["warnings"]}
