"""Accept pinned energy/depth tables; return sampled summaries or aligned comparisons.

The module is copied into each immutable built-in bundle. Calculations use only
sampled rows, without interpolation, integration, weighting or unit conversion.
"""

import json
import math
from pathlib import Path


def dataset(inputs: dict, selection: dict, context: dict, coordinate: str, values: list[str]) -> tuple[dict, list[dict]]:
    """Accept input manifests/selection/context/columns; return metadata and increasing finite rows."""
    manifest = inputs[selection["input"]]
    table = next((item for item in manifest.get("datasets", []) if item["name"] == selection["dataset"]), None)
    if table is None:
        raise ValueError("Unknown selected dataset")
    columns = {item["name"]: item["unit"] for item in table["columns"]}
    if not values or len(values) != len(set(values)) or coordinate in values or not {coordinate, *values} <= columns.keys():
        raise ValueError("Explicit distinct coordinate/value columns are required")
    directory = Path(context["input_directories"][selection["input"]])
    with (directory / table["path"]).open(encoding="utf-8") as stream:
        raw_rows = [json.loads(line) for line in stream]
    if any(type(row) is not list or len(row) != len(columns) for row in raw_rows):
        raise ValueError("Malformed dataset row array")
    rows = [dict(zip(columns, row)) for row in raw_rows]
    if not rows or len(rows) != table["row_count"]:
        raise ValueError("Empty dataset or incorrect row count")
    previous = -math.inf
    for row in rows:
        if not isinstance(row, dict) or set(row) != set(columns):
            raise ValueError("Malformed dataset row")
        if any(type(row[name]) not in (int, float) or not math.isfinite(row[name]) for name in (coordinate, *values)):
            raise ValueError("Selected values must be finite numbers")
        if row[coordinate] <= previous:
            raise ValueError("Coordinate must be unique and strictly increasing")
        previous = row[coordinate]
    metadata = {"columns": [{"name": name, "unit": columns[name], "type": "number", "nullable": False} for name in (coordinate, *values)],
                "normalization": table["normalization"], "source": {key: manifest[key] for key in ("owner", "export_id", "analysis_id", "attempt_id", "run_id", "source_ids") if key in manifest},
                "selection": selection, "limitations": manifest.get("limitations", [])}
    metadata["conditions"] = manifest.get("conditions", {})
    metadata["validation"] = manifest.get("validation", {})
    metadata["source_reference"] = context.get("input_references", {}).get(selection["input"])
    return metadata, rows


def save_table(context: dict, name: str, rows: list[dict], columns: list[dict], normalization: dict) -> dict:
    """Accept output context/name/rows/columns/normalization; write JSONL and return descriptor."""
    path = name + ".jsonl"
    with (Path(context["output_directory"]) / path).open("x", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps([row[column["name"]] for column in columns], allow_nan=False, separators=(",", ":")) + "\n")
    return {"name": name, "path": path, "columns": columns, "normalization": normalization, "row_count": len(rows)}


def summary(inputs: dict, parameters: dict, context: dict) -> dict:
    """Accept pinned data/explicit coordinate/value columns/context; return sampled extrema/means."""
    coordinate, values = parameters["coordinate"], parameters["value_columns"]
    metadata, rows = dataset(inputs, parameters["source"], context, coordinate, values)
    statistics = {}
    for name in values:
        minimum = min(rows, key=lambda row: row[name])
        maximum = max(rows, key=lambda row: row[name])
        statistics[name] = {"min": minimum[name], "min_coordinate": minimum[coordinate],
                            "max": maximum[name], "max_coordinate": maximum[coordinate],
                            "row_mean": math.fsum(row[name] / len(rows) for row in rows)}
    sampled = [{name: row[name] for name in (coordinate, *values)} for row in rows]
    table = save_table(context, "samples", sampled, metadata["columns"], metadata["normalization"])
    return {"schema_version": 1, "summary": {"kind": "dataset_summary", "coordinate": coordinate, "value_columns": values,
            "row_count": len(rows), "coordinate_range": [rows[0][coordinate], rows[-1][coordinate]], "statistics": statistics,
            "metadata": metadata}, "tables": [table], "figures": [],
            "limitations": list(dict.fromkeys([*metadata["limitations"], "Unweighted sampled row statistics; no physical integration or scientific acceptance."]))}


def comparison(inputs: dict, parameters: dict, context: dict) -> dict:
    """Accept one reference/ordered candidates/explicit columns/context; return all aligned differences."""
    coordinate, values = parameters["coordinate"], parameters["value_columns"]
    reference_meta, reference = dataset(inputs, parameters["reference"], context, coordinate, values)
    candidates, keys, sources = [], set(), set()
    if not parameters["candidates"] or len(parameters["candidates"]) > context["limits"]["candidates"]:
        raise ValueError("Candidate list is empty or exceeds accepted bound")
    for item in parameters["candidates"]:
        key = item["key"]
        try:
            selection = {name: item[name] for name in ("input", "dataset")}
            metadata, rows = dataset(inputs, selection, context, coordinate, values)
            source_identity = json.dumps([metadata["source"], item["dataset"]], sort_keys=True)
            if key in keys or source_identity in sources:
                raise ValueError("Duplicate candidate key/source")
            if metadata["columns"] != reference_meta["columns"] or metadata["normalization"] != reference_meta["normalization"]:
                raise ValueError("Selected columns/units/normalization are incompatible")
            if len(rows) != len(reference) or any(a[coordinate] != b[coordinate] for a, b in zip(reference, rows)):
                raise ValueError("Coordinate grids do not match exactly")
            keys.add(key)
            sources.add(source_identity)
            candidates.append((key, metadata, rows))
        except (ValueError, KeyError) as exc:
            raise ValueError(f"Candidate {key}: {exc}") from exc
    tables, summaries = [], []
    limitations = list(reference_meta["limitations"])
    for index, (key, metadata, candidate) in enumerate(candidates):
        records = []
        metrics = {}
        for a, b in zip(reference, candidate):
            row = {coordinate: a[coordinate]}
            for name in values:
                delta = b[name] - a[name]
                percentage = 100 * (delta / a[name]) if a[name] != 0 else None
                if not math.isfinite(delta) or (percentage is not None and not math.isfinite(percentage)):
                    raise ValueError(f"Candidate {key}: numerical difference overflow")
                row.update({f"reference:{name}": a[name], f"candidate:{name}": b[name], f"difference:{name}": delta,
                            f"absolute:{name}": abs(delta), f"percentage:{name}": percentage, f"zero_reference:{name}": a[name] == 0})
            records.append(row)
        units = {item["name"]: item["unit"] for item in metadata["columns"]}
        columns = [{"name": coordinate, "unit": units[coordinate], "type": "number", "nullable": False}]
        for name in values:
            deltas = [row[f"difference:{name}"] for row in records]
            maximum = max(abs(value) for value in deltas)
            scale = maximum or 1.0
            rmse = scale * math.sqrt(math.fsum((delta / scale) ** 2 / len(deltas) for delta in deltas))
            metrics[name] = {"max_absolute_difference": maximum, "row_rmse": rmse}
            columns.extend({"name": f"{kind}:{name}", "unit": "%" if kind == "percentage" else "" if kind == "zero_reference" else units[name],
                            "type": "boolean" if kind == "zero_reference" else "number", "nullable": kind == "percentage"}
                           for kind in ("reference", "candidate", "difference", "absolute", "percentage", "zero_reference"))
        tables.append(save_table(context, f"candidate_{index + 1}", records, columns, metadata["normalization"]))
        summaries.append({"key": key, "table": tables[-1]["name"], "metadata": metadata, "metrics": metrics})
        limitations.extend(metadata["limitations"])
    return {"schema_version": 1, "summary": {"kind": "aligned_comparison", "coordinate": coordinate,
            "value_columns": values, "row_count": len(reference), "reference": reference_meta, "candidates": summaries},
            "tables": tables, "figures": [], "limitations": list(dict.fromkeys([*limitations,
            "Exact sampled-grid comparison; row RMSE is unweighted, not a physical integral.",
            "Percentages are undefined whenever the reference equals zero, including zero/zero.", "Software verification does not establish scientific acceptance."]))}
