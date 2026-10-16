"""Accept unittest discovery; verify sampled numerics, batch failures and independent rendering."""

from copy import deepcopy
import json
import math
from pathlib import Path

from analysis.numerics import comparison, summary
from analysis.records import AnalysisError
from analysis_support import AnalysisTest


class NumericalTests(AnalysisTest):
    """Construct independent synthetic fixtures; verify numerical and rendering contracts."""

    def fixture(self, key: str, x: list, y: list, unit: str = "eV") -> tuple[dict, dict]:
        """Accept source key/grid/values/unit; write local method fixture; return manifest/context."""
        root = self.runtime / key
        root.mkdir(exist_ok=True)
        (root / "data.jsonl").write_text("".join(json.dumps([a, b]) + "\n" for a, b in zip(x, y)), encoding="utf-8")
        output = self.runtime / "output"
        output.mkdir(exist_ok=True)
        manifest = {"owner": "srim", "export_id": key, "datasets": [{"name": "data", "path": "data.jsonl", "columns": [{"name": "x", "unit": "nm"}, {"name": "y", "unit": unit}],
                     "normalization": {}, "row_count": len(x)}], "limitations": ["Independent fixture"]}
        context = {"input_directories": {key: str(root)}, "output_directory": str(output), "limits": {"candidates": 32}}
        return manifest, context

    def test_summary_ties_single_rows_irregular_grids_and_units(self) -> None:
        """Accept no inputs; compare sampled extrema/first-tie/mean to hand-computed answers; return nothing."""
        manifest, context = self.fixture("a", [1, 3, 11], [4, 4, -2])
        result = summary({"a": manifest}, {"source": {"input": "a", "dataset": "data"}, "coordinate": "x", "value_columns": ["y"]}, context)
        self.assertEqual(result["summary"]["statistics"]["y"], {"min": -2, "min_coordinate": 11, "max": 4, "max_coordinate": 1, "row_mean": 2})
        self.assertEqual(result["tables"][0]["columns"][1]["unit"], "eV")
        one, single = self.fixture("single", [3], [9])
        single["output_directory"] = str(self.runtime / "single")
        result = summary({"single": one}, {"source": {"input": "single", "dataset": "data"}, "coordinate": "x", "value_columns": ["y"]}, single)
        self.assertEqual(result["summary"]["coordinate_range"], [3, 3])
        self.assertEqual(result["summary"]["statistics"]["y"]["row_mean"], 9)

    def test_summary_rejects_empty_duplicate_unordered_nonfinite_and_malformed_data(self) -> None:
        """Accept no inputs; reject invalid data without pretending to validate science; return nothing."""
        fixtures = [([], []), ([1, 1], [2, 3]), ([2, 1], [3, 2]), ([1], [float("nan")]), ([float("inf")], [1]), ([1], ["bad"])]
        for index, (x, y) in enumerate(fixtures):
            manifest, context = self.fixture(f"bad{index}", x, y)
            with self.subTest(index=index), self.assertRaises(ValueError):
                summary({"a": manifest}, {"source": {"input": "a", "dataset": "data"}, "coordinate": "x", "value_columns": ["y"]}, {**context, "input_directories": {"a": next(iter(context["input_directories"].values()))}})

    def test_batch_comparison_order_zeros_irregular_grid_and_all_or_nothing(self) -> None:
        """Accept no inputs; check ordered multi-candidate metrics/gaps and reject a mismatching member; return nothing."""
        inputs, context = {}, {}
        for key, values in (("a", [0, 2, -2]), ("b", [1, 4, -1]), ("c", [0, 1, -4])):
            manifest, child_context = self.fixture(key, [1, 3, 11], values)
            inputs[key] = manifest
            if not context:
                context = child_context
            else:
                context["input_directories"].update(child_context["input_directories"])
        params = {"reference": {"input": "a", "dataset": "data"}, "candidates": [{"key": key, "input": key, "dataset": "data"} for key in ("c", "b")], "coordinate": "x", "value_columns": ["y"]}
        result = comparison(inputs, params, context)
        self.assertEqual([item["key"] for item in result["summary"]["candidates"]], ["c", "b"])
        metric = result["summary"]["candidates"][1]["metrics"]["y"]
        self.assertAlmostEqual(metric["row_rmse"], math.sqrt(2))
        self.assertEqual(metric["max_absolute_difference"], 2)
        names = [column["name"] for column in result["tables"][0]["columns"]]
        rows = [dict(zip(names, json.loads(line))) for line in (self.runtime / "output" / "candidate_1.jsonl").read_text().splitlines()]
        self.assertIsNone(rows[0]["percentage:y"])
        self.assertTrue(rows[0]["zero_reference:y"])
        self.assertEqual(rows[2]["percentage:y"], 100)
        for mismatch in ("unit", "grid", "normalization", "duplicate"):
            bad_inputs = deepcopy(inputs)
            bad_params = deepcopy(params)
            if mismatch == "unit":
                bad_inputs["b"]["datasets"][0]["columns"][1]["unit"] = "keV"
            elif mismatch == "grid":
                (self.runtime / "b" / "data.jsonl").write_text('[1,1]\n[4,4]\n[11,-1]\n')
            elif mismatch == "normalization":
                bad_inputs["b"]["datasets"][0]["normalization"] = {"ions": 2}
            else:
                bad_params["candidates"][1] = bad_params["candidates"][0]
            with self.subTest(mismatch=mismatch), self.assertRaisesRegex(ValueError, "Candidate"):
                comparison(bad_inputs, bad_params, context)

    def test_comparison_jobs_render_separately_and_reuse_numeric_data(self) -> None:
        """Accept no inputs; run supervised batch/PNG/SVG jobs, deduplicate and isolate renderer failures; return nothing."""
        methods = self.methods()
        self.verify(methods["aligned_comparison"])
        self.verify(methods["render_figures"])
        sources = {"a": self.source(), "b": self.source([1, 4, 1]), "c": self.source([0, 1, 4])}
        request = self.audit(schema_version=1, method=methods["aligned_comparison"], inputs=sources,
                             parameters={"reference": {"input": "a", "dataset": "stopping_range"},
                                         "candidates": [{"key": key, "input": key, "dataset": "stopping_range"} for key in ("c", "b")],
                                         "coordinate": "energy_keV", "value_columns": ["electronic_stopping"]}, limits={})
        numeric = self.wait(self.store.perform("submit_analysis", request)["analysis_id"])
        self.assertEqual(numeric["state"], "CHECKED", numeric["error"])
        summary = self.output(numeric)["summary"]
        self.assertEqual([item["key"] for item in summary["candidates"]], ["c", "b"])
        page = self.output(numeric, "candidate_1", limit=1)
        self.assertEqual(page["next_offset"], 1)
        reference = {**numeric["output"], "store_id": "local"}
        options = {"input": "numeric", "formats": ["png", "svg"], "layout": "overlay", "share_x": False, "share_y": False,
                   "curves": ["original", "percentage"], "x_scale": "linear", "y_scale": "linear", "x_limits": [], "y_limits": [],
                   "columns": [], "candidates": [], "labels": {}, "legend": True, "width": 640, "height": 480}
        rendering = self.audit(schema_version=1, method=methods["render_figures"], inputs={"numeric": reference}, parameters=options, limits={})
        rendered = self.wait(self.store.perform("submit_analysis", rendering)["analysis_id"])
        self.assertEqual(rendered["state"], "CHECKED", rendered["error"])
        figures = self.output(rendered, "figures")["figures"]
        self.assertEqual(len(figures), 4)
        self.assertEqual({figure["format"] for figure in figures}, {"png", "svg"})
        self.assertEqual(self.store.perform("submit_analysis", {**rendering, **self.audit()})["analysis_id"], rendered["analysis_id"])
        for layout, expected_count in (("panels", 1), ("separate", 3)):
            variant = {**rendering, **self.audit(), "parameters": {**options, "layout": layout, "formats": ["png"], "curves": ["original"],
                       "share_x": layout == "panels", "share_y": layout == "panels"}}
            variant_result = self.wait(self.store.perform("submit_analysis", variant)["analysis_id"])
            self.assertEqual(variant_result["state"], "CHECKED", variant_result["error"])
            self.assertEqual(len(self.output(variant_result, "figures")["figures"]), expected_count)
        recomputed = self.wait(self.store.perform("submit_analysis", {**rendering, **self.audit(), "recompute_of": rendered["analysis_id"]})["analysis_id"])
        self.assertEqual(recomputed["state"], "CHECKED", recomputed["error"])
        self.assertEqual([item["sha256"] for item in self.output(recomputed, "figures")["figures"]], [item["sha256"] for item in figures])
        failed_request = {**rendering, **self.audit(), "parameters": {**options, "y_scale": "log", "curves": ["difference"]}}
        failed = self.wait(self.store.perform("submit_analysis", failed_request)["analysis_id"])
        self.assertEqual(failed["state"], "FAILED")
        self.assertIsNone(failed["output"])
        self.assertEqual(self.store.perform("get_analysis", {"analysis_id": numeric["analysis_id"]})["state"], "CHECKED")
        diagnostics = self.store.perform("get_analysis_output", {"analysis_id": failed["analysis_id"], "attempt_id": failed["current_attempt"],
                                          "output_sha256": failed["attempts"][-1]["archive_sha256"], "name": "diagnostics", "limit": 20})
        self.assertTrue(diagnostics["diagnostic_only"])
