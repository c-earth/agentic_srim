"""Accept a saved numerical result and explicit plot options; emit PNG/SVG figures.

The renderer is copied into immutable method bundles. It preserves sampled
values; percentage gaps remain gaps. Plot changes never mutate numerical jobs.
"""

import json
import math
from pathlib import Path


def run(inputs: dict, parameters: dict, context: dict) -> dict:
    """Accept numerical manifests/options/context; return a complete figure-set manifest or fail."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        from matplotlib import pyplot as plt
    except ImportError as exc:
        raise ValueError("Rendering requires the optional pinned plotting dependency") from exc
    source = parameters["input"]
    directory = Path(context["input_directories"][source])
    result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
    summary = result["summary"]
    if summary.get("kind") not in ("dataset_summary", "aligned_comparison"):
        raise ValueError("Rendering requires a saved supported numerical result")
    coordinate = summary["coordinate"]
    values = parameters["columns"] or summary["value_columns"]
    if not set(values) <= set(summary["value_columns"]) or len(values) != len(set(values)):
        raise ValueError("Unknown/duplicate observable selection")
    width, height = parameters["width"], parameters["height"]
    if max(width, height) > context["limits"]["figure_pixels"]:
        raise ValueError("Requested figure dimensions exceed accepted bound")
    formats, curves = parameters["formats"], parameters["curves"]
    matplotlib.rcParams["svg.hashsalt"] = json.dumps([inputs[source], parameters], sort_keys=True)
    if len(set(formats)) != len(formats) or len(set(curves)) != len(curves):
        raise ValueError("Duplicate plot format/curve")
    candidates = summary.get("candidates", [])
    selected = parameters["candidates"] or [item["key"] for item in candidates]
    if len(set(selected)) != len(selected) or not set(selected) <= {item["key"] for item in candidates}:
        raise ValueError("Unknown/duplicate candidate selection")
    if not candidates and (selected or curves != ["original"]):
        raise ValueError("Dataset summaries support original curves only")
    tables = {item["name"]: item for item in result["tables"]}
    loaded = {}
    for name, table in tables.items():
        with (directory / table["path"]).open(encoding="utf-8") as stream:
            loaded[name] = [dict(zip((column["name"] for column in table["columns"]), json.loads(line))) for line in stream]
    panels = []
    metadata = summary.get("metadata", summary.get("reference"))
    units = {item["name"]: item["unit"] for item in metadata["columns"]}
    for column in values:
        for curve in curves:
            series = []
            if not candidates:
                series.append(("source " + json.dumps(metadata["source"], sort_keys=True), loaded["samples"], column))
            else:
                current = [item for item in candidates if item["key"] in selected]
                if curve == "original":
                    series.append(("reference", loaded[current[0]["table"]], f"reference:{column}"))
                for candidate in current:
                    series.append((candidate["key"], loaded[candidate["table"]], f"candidate:{column}" if curve == "original" else f"{curve}:{column}"))
            if parameters["layout"] == "separate":
                for item in series:
                    panels.append((column, curve, [item]))
            else:
                panels.append((column, curve, series))
    groups = [panels] if parameters["layout"] == "panels" else [[panel] for panel in panels]
    if len(groups) * len(formats) > context["limits"]["figure_files"]:
        raise ValueError("Requested figure count exceeds accepted bound")
    if parameters["share_y"] and len({("%" if curve == "percentage" else units[column]) for column, curve, _ in panels}) > 1:
        raise ValueError("Shared y axes require matching plotted units")
    figures = []
    resolved_axes = []
    output = Path(context["output_directory"])
    for index, group in enumerate(groups):
        fig, axes = plt.subplots(len(group), 1, figsize=(width / 100, height / 100), dpi=100,
                                  squeeze=False, sharex=parameters["share_x"], sharey=parameters["share_y"])
        try:
            for axis, (column, curve, series) in zip(axes[:, 0], group):
                for label, rows, value in series:
                    x = [row[coordinate] for row in rows]
                    y = [float("nan") if row[value] is None else row[value] for row in rows]
                    if parameters["x_scale"] == "log" and any(item <= 0 for item in x):
                        raise ValueError("Log coordinate scale requires positive sampled coordinates")
                    if parameters["y_scale"] == "log" and any(item <= 0 for item in y if math.isfinite(item)):
                        raise ValueError("Log value scale requires positive selected values")
                    axis.plot(x, y, marker=".", label=parameters["labels"].get(label, label))
                axis.set_xscale(parameters["x_scale"])
                axis.set_yscale(parameters["y_scale"])
                for key, setter, scale in (("x_limits", axis.set_xlim, parameters["x_scale"]), ("y_limits", axis.set_ylim, parameters["y_scale"])):
                    limits = parameters[key]
                    if limits:
                        if len(limits) != 2 or limits[0] >= limits[1] or (scale == "log" and limits[0] <= 0):
                            raise ValueError("Invalid explicit axis limits")
                        setter(*limits)
                value_unit = "%" if curve == "percentage" else units[column]
                axis.set_xlabel(parameters["labels"].get("x", f"{coordinate} [{units[coordinate]}]"))
                axis.set_ylabel(parameters["labels"].get("y", f"{column} [{value_unit}]"))
                axis.set_title(parameters["labels"].get("title", f"{curve}: {column}"))
                axis.grid(True, alpha=0.25)
                if parameters["legend"]:
                    axis.legend(fontsize="small")
                resolved_axes.append({"column": column, "curve": curve, "x_limits": list(axis.get_xlim()),
                                      "y_limits": list(axis.get_ylim()), "series": [item[0] for item in series]})
            fig.suptitle("Sampled data · normalization: " + json.dumps(metadata["normalization"], sort_keys=True), fontsize=8)
            fig.tight_layout(rect=(0, 0.04, 1, 0.96))
            source_label = ", ".join(inputs[source].get("source_ids", []))
            fig.text(0.01, 0.005, "Sources: " + source_label, fontsize=6, wrap=True)
            for format in formats:
                name = f"figure_{index + 1}.{format}"
                if format == "svg":
                    fig.set_size_inches(width / 72, height / 72)
                else:
                    fig.set_size_inches(width / 100, height / 100)
                fig.savefig(output / name, format=format, dpi=100, metadata={"Description": "Sampled numerical source: " + source_label, "Date": None} if format == "svg" else {"Source": source_label})
                if format == "svg":
                    import xml.etree.ElementTree as ET
                    svg = ET.parse(output / name)
                    svg.getroot().set("width", f"{width}px")
                    svg.getroot().set("height", f"{height}px")
                    svg.write(output / name, encoding="utf-8", xml_declaration=True)
                figures.append({"path": name, "format": format, "width": width, "height": height})
        finally:
            plt.close(fig)
    source_hash = next(item["sha256"] for item in inputs[source]["files"] if item["path"] == "result.json")
    return {"schema_version": 1, "summary": {"kind": "rendering", "source_method": inputs[source]["method"],
            "source_result_sha256": source_hash, "renderer_version": matplotlib.__version__,
            "requested_options": parameters, "options": {**parameters, "columns": values, "candidates": selected},
            "axes": resolved_axes}, "tables": [], "figures": figures,
            "limitations": result["limitations"]}
