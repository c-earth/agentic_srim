"""Accept a store and audit identity; explicitly install immutable tested built-in bundles."""

from pathlib import Path
import sys

from analysis.artifacts import REFERENCE
from analysis.records import JSON_OBJECT, NAME, SHORT, obj


SOURCE = obj({"input": NAME, "dataset": SHORT})
VALUES = {"type": "array", "items": SHORT, "minItems": 1, "maxItems": 63}
RESULT = obj({"schema_version": {"type": "integer", "enum": [1]}, "summary": JSON_OBJECT,
              "tables": {"type": "array", "items": JSON_OBJECT, "maxItems": 32},
              "figures": {"type": "array", "items": JSON_OBJECT, "maxItems": 32},
              "limitations": {"type": "array", "items": SHORT, "maxItems": 100}})
PARAMETERS = {
    "dataset_summary": obj({"source": SOURCE, "coordinate": SHORT, "value_columns": VALUES}),
    "aligned_comparison": obj({"reference": SOURCE, "candidates": {"type": "array", "items": obj({"key": NAME, **SOURCE["properties"]}), "minItems": 1, "maxItems": 32},
                                "coordinate": SHORT, "value_columns": VALUES}),
    "render_figures": obj({"input": NAME, "formats": {"type": "array", "items": {"type": "string", "enum": ["png", "svg"]}, "minItems": 1, "maxItems": 2},
                           "layout": {"type": "string", "enum": ["separate", "panels", "overlay"]},
                           "share_x": {"type": "boolean"}, "share_y": {"type": "boolean"},
                           "curves": {"type": "array", "items": {"type": "string", "enum": ["original", "difference", "absolute", "percentage"]}, "minItems": 1, "maxItems": 4},
                           "x_scale": {"type": "string", "enum": ["linear", "log"]}, "y_scale": {"type": "string", "enum": ["linear", "log"]},
                           "x_limits": {"type": "array", "items": {"type": "number"}, "maxItems": 2},
                           "y_limits": {"type": "array", "items": {"type": "number"}, "maxItems": 2},
                           "columns": {"type": "array", "items": SHORT, "maxItems": 63},
                           "candidates": {"type": "array", "items": NAME, "maxItems": 32},
                           "labels": {"type": "object", "additionalProperties": SHORT, "maxProperties": 100},
                           "legend": {"type": "boolean"}, "width": {"type": "integer", "minimum": 64, "maximum": 4096},
                           "height": {"type": "integer", "minimum": 64, "maximum": 4096}}),
}


def bundle(name: str) -> tuple[dict, dict]:
    """Accept a built-in method name; return complete specification and immutable source/tests."""
    package = Path(__file__).parent
    renderer = name == "render_figures"
    module, function = ("rendering", "run") if renderer else ("numerics", "summary" if name == "dataset_summary" else "comparison")
    method = f'"""Accept pinned inputs/parameters/context; return a version-1 result manifest."""\nfrom {module} import {function} as run\n'
    files = {"method.py": method, f"{module}.py": (package / f"{module}.py").read_text(encoding="utf-8"),
             "tests/test_method.py": (package / "method_tests.py").read_text(encoding="utf-8")}
    spec = {"description": name.replace("_", " "), "applicability": ["Sampled energy/depth data; software verification only"],
            "input_schema": {"type": "object", "additionalProperties": REFERENCE, "minProperties": 1, "maxProperties": 33},
            "parameter_schema": PARAMETERS[name], "output_schema": RESULT,
            "requirements": {"matplotlib": "3.10.8"} if renderer else {},
            "python": f"{sys.version_info.major}.{sys.version_info.minor}", "reproducible": True,
            "external_state": {"randomness": "none"}, "components": []}
    return spec, files


def install(store, actor: str, reason: str) -> list[dict]:
    """Accept store/actor/reason; register missing built-in version 1 bundles; return references."""
    references = []
    existing = store.perform("list_methods", {"limit": 100})["methods"]
    for name in PARAMETERS:
        match = next((item for item in existing if item["method_id"] == name and item["version"] == 1), None)
        if match:
            references.append({key: match[key] for key in ("method_id", "version", "bundle_sha256")})
            continue
        spec, files = bundle(name)
        result = store.perform("register_method", {"schema_version": 1, "method_id": name, "version": 1,
                               "specification": spec, "files": files, "actor": actor, "reason": reason,
                               "idempotency_key": f"builtin:{name}:1"})
        references.append(result["method"])
    return references
