"""Accept bundled unittest discovery; verify built-in numerical/rendering contracts on synthetic data."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


class MethodTests(unittest.TestCase):
    """Construct unittest cases; verify the bundled callable with independent sampled fixtures."""

    def test_sampled_results(self) -> None:
        """Accept no inputs; check independent summary/comparison answers or renderer import; return nothing."""
        import method
        if method.run.__module__ == "rendering":
            import matplotlib
            self.assertEqual(matplotlib.__version__, "3.10.8")
            return
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "output").mkdir()
            context = {"input_directories": {}, "output_directory": str(root / "output"), "limits": {"candidates": 32}}
            inputs = {}
            for key, values in (("a", [0, 2, 2]), ("b", [1, 4, 1])):
                directory = root / key
                directory.mkdir()
                rows = [{"x": x, "y": y} for x, y in zip([1, 3, 9], values)]
                (directory / "data.jsonl").write_text("".join(json.dumps([row["x"], row["y"]]) + "\n" for row in rows), encoding="utf-8")
                inputs[key] = {"owner": "srim", "export_id": key, "datasets": [{"name": "data", "path": "data.jsonl", "row_count": 3,
                               "columns": [{"name": "x", "unit": "nm"}, {"name": "y", "unit": "eV"}], "normalization": {}}]}
                context["input_directories"][key] = str(directory)
            params = {"coordinate": "x", "value_columns": ["y"]}
            if method.run.__name__ == "summary":
                params["source"] = {"input": "a", "dataset": "data"}
                result = method.run(inputs, params, context)
                stats = result["summary"]["statistics"]["y"]
                self.assertEqual(stats["max_coordinate"], 3)
                self.assertAlmostEqual(stats["row_mean"], 4 / 3)
            else:
                params.update(reference={"input": "a", "dataset": "data"}, candidates=[{"key": "b", "input": "b", "dataset": "data"}])
                result = method.run(inputs, params, context)
                metrics = result["summary"]["candidates"][0]["metrics"]["y"]
                self.assertEqual(metrics["max_absolute_difference"], 2)
                self.assertAlmostEqual(metrics["row_rmse"], 2 ** 0.5)
                lines = (root / "output" / result["tables"][0]["path"]).read_text().splitlines()
                first = dict(zip((column["name"] for column in result["tables"][0]["columns"]), json.loads(lines[0])))
                self.assertIsNone(first["percentage:y"])
