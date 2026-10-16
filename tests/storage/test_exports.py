"""Accept unittest discovery; verify producer exports against captured SR output without native execution."""

from copy import deepcopy
import json
import shutil
from unittest.mock import patch

from agentic_srim.capabilities.stopping_range.workflow import execute, submit
from agentic_srim.interfaces.mcp import call_tool
from agentic_srim.storage.exports import export_run_data
from analysis.artifacts import resolve
from analysis.records import AnalysisError, checksum
from sr_module.support import IsolatedTest
from trim.support import IsolatedTest as TrimIsolatedTest, FIXTURES as TRIM_FIXTURES


class ExportTests(IsolatedTest):
    """Construct fixture-backed SR runs; verify exact immutable exports, selections and source damage."""

    def test_export_selection_replay_conflict_and_manifest_integrity(self) -> None:
        """Accept no inputs; export a bounded fixture subset and verify index/bytes/identity; return nothing."""
        run_id = submit(self.request, self.settings)
        with patch("agentic_srim.capabilities.stopping_range.workflow.run", side_effect=self.simulate):
            run = execute(self.runtime, run_id)
        attempt_id = run["result"]["provenance"]["attempt_id"]
        archive = self.runtime / run["attempts"][-1]["artifact_path"]
        original = checksum(archive / "manifest.json")
        request = {"schema_version": 1, "run_id": run_id, "attempt_id": attempt_id, "store_id": "fixture",
                   "datasets": [{"name": "stopping_range", "columns": ["energy_keV", "electronic_stopping"], "offset": 1, "count": 2}],
                   "actor": "test", "reason": "Fixture export", "idempotency_key": "subset"}
        result = export_run_data(self.runtime, request)
        self.assertEqual(result, export_run_data(self.runtime, request))
        self.assertEqual(result["datasets"][0]["row_count"], 2)
        directory, manifest = resolve(result["reference"], {"fixture": self.runtime}, 67108864)
        first_row = json.loads((directory / result["datasets"][0]["path"]).read_text().splitlines()[0])
        self.assertEqual(first_row, [run["result"]["rows"][1][column] for column in ("energy_keV", "electronic_stopping")])
        self.assertEqual(manifest["datasets"][0]["columns"][0]["type"], "number")
        self.assertEqual(manifest["source_manifest_sha256"], original)
        self.assertEqual(checksum(archive / "manifest.json"), original)
        with self.assertRaises(AnalysisError):
            export_run_data(self.runtime, {**request, "reason": "changed"})
        self.assertTrue(call_tool("export_run_data", {**request, "unknown": 1}, self.settings)["isError"])
        source = directory / result["datasets"][0]["path"]
        source.write_text("damaged", encoding="utf-8")
        with self.assertRaises(AnalysisError):
            export_run_data(self.runtime, request)

    def test_missing_source_and_wrong_attempt_are_rejected_without_simulation(self) -> None:
        """Accept no inputs; reject absent producer/unfinished run/exact-attempt mismatch; return nothing."""
        request = {"schema_version": 1, "run_id": "a" * 32, "attempt_id": "b" * 32, "store_id": "local",
                   "datasets": [{"name": "stopping_range"}], "actor": "test", "reason": "Missing data", "idempotency_key": "missing"}
        with self.assertRaises(AnalysisError):
            export_run_data(self.runtime, request)
        self.assertFalse((self.runtime / "metadata.sqlite").exists())

    def test_published_export_recovers_same_identity_after_interrupted_metadata(self) -> None:
        """Accept no inputs; interrupt after rename, replay the key and reconcile one immutable export; return nothing."""
        with patch("agentic_srim.capabilities.stopping_range.workflow.run", side_effect=self.simulate):
            run = execute(self.runtime, submit(self.request, self.settings))
        request = {"schema_version": 1, "run_id": run["id"], "attempt_id": run["result"]["provenance"]["attempt_id"], "store_id": "fixture",
                   "datasets": [{"name": "stopping_range"}], "actor": "test", "reason": "Publication recovery", "idempotency_key": "recovery"}
        from analysis.artifacts import publish
        def interrupted(temporary, destination) -> None:
            """Accept publication paths; rename then simulate interruption; return no successful result."""
            publish(temporary, destination)
            raise OSError("Interrupted after publication")
        with patch("agentic_srim.storage.exports.artifacts.publish", side_effect=interrupted), self.assertRaises(OSError):
            export_run_data(self.runtime, request)
        original = next(path for path in (self.runtime / "exports").iterdir() if path.is_dir())
        response = export_run_data(self.runtime, request)
        self.assertEqual(response["reference"]["export_id"], original.name)
        self.assertEqual(len(list((self.runtime / "exports").iterdir())), 1)


class TrimExportTests(TrimIsolatedTest):
    """Construct captured TRIM profile/detail exports; verify serialization without native execution."""

    def test_captured_profiles_and_details_preserve_units_normalization_and_rows(self) -> None:
        """Accept no inputs; export native-captured parsed profiles/details with exact column order; return nothing."""
        from agentic_srim.capabilities.trim.workflow import execute, submit
        def simulate(workspace, archive, timeout, cancellation, count, collisions, mode, collision_records) -> int:
            """Accept backend runner fields; copy captured mode-2 bytes, launch nothing and return zero."""
            shutil.copytree(TRIM_FIXTURES / "mode_2" / "native", workspace, dirs_exist_ok=True)
            return 0
        with patch("agentic_srim.capabilities.trim.workflow.run", side_effect=simulate):
            run = execute(self.runtime, submit(self.request, self.settings))
        self.assertEqual(run["state"], "CHECKED", run["error"])
        profile_name = next(iter(run["result"]["profiles"]))
        detail_name = next(name for name, detail in run["result"]["details"].items() if detail["records"] >= 2)
        request = {"schema_version": 1, "run_id": run["id"], "attempt_id": run["result"]["provenance"]["attempt_id"], "store_id": "fixture",
                   "datasets": [{"name": profile_name, "count": 2}, {"name": detail_name, "count": 2}],
                   "actor": "test", "reason": "Captured TRIM export", "idempotency_key": "trim"}
        result = export_run_data(self.runtime, request)
        directory, manifest = resolve(result["reference"], {"fixture": self.runtime}, 67108864)
        self.assertEqual(manifest["capability"], "trim")
        profile = run["result"]["profiles"][profile_name]
        self.assertEqual(manifest["datasets"][0]["normalization"]["native_ion_normalization"], profile["native_ion_normalization"])
        self.assertEqual([item["unit"] for item in manifest["datasets"][0]["columns"]], list(profile["units"]))
        rows = [json.loads(line) for line in (directory / manifest["datasets"][0]["path"]).read_text().splitlines()]
        self.assertEqual(rows, [list(row) for row in profile["rows"][:2]])
        self.assertEqual(manifest["datasets"][1]["row_count"], 2)
