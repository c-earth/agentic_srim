"""Exercise TRIM lifecycle and preservation with native text and mocked processes."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from _fixtures import make_test_project
from test_trim_inputs import TRIM_INSTALLATION_FILES, TRIM_REQUEST, TRIM_SELECTIONS
from test_trim_runtime import CACHE
from src.backend.records import CreateRequest, create_directories
from src.srim.trim_defaults import switch_trim_defaults
from src.srim.trim_execution import run_trim_simulation
from src.srim.trim_resolution import resolve_trim_inputs
from src.srim.trim_runtime import prepare_trim_continuation, prepare_trim_simulation, verify_trim_runtime
from src.srim.trim_storage import verify_preserved_trim_runtime


class TestTRIMExecution(unittest.TestCase):
    """Use owned synthetic allocations and committed native results without GUI launches."""

    def test_success_requires_saved_counts(self: TestTRIMExecution) -> None:
        """Require real saved counts, parsed profiles and preserved settings for success; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            paths, config, worker = make_test_project(Path(directory))
            for name, data in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(data)
            switch_trim_defaults(paths, config)
            request = deepcopy(TRIM_REQUEST)
            request["parameters"].update({"total_ions": 5, "mode": "ion-full", "outputs": {"ranges": True, "backscatter": True, "transmission": True, "sputtering": True, "collisions": "ions-and-recoils", "exyz_increment_eV": "1000"}})
            request["parameters"]["layers"][0]["overrides"]["components"] = [{"component_index": 1, "mass_amu": "28.086", "surface_binding_eV": "4.7"}]
            inputs = resolve_trim_inputs(paths, config, worker, request, TRIM_SELECTIONS)
            created = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            runtime = prepare_trim_simulation(paths, config, created, worker, inputs)
            verify_trim_runtime(paths, created.path.name, runtime)
            shutil.copytree(Path(__file__).parent / "fixtures/trim/2013", runtime, dirs_exist_ok=True)
            process = {"pid": 42, "exit_code": 0, "elapsed_seconds": 0.1, "termination": "exit", "popups": [], "monitor_errors": []}
            with patch("src.srim.trim_execution.verify_trim_runtime", return_value=inputs), patch("src.srim.trim_execution.execute_trim_process", return_value=process):
                result = run_trim_simulation(paths, config, created.path.name, runtime)
            comparison = json.loads((created.path / "output/parsed/saved-values.json").read_bytes())
            self.assertEqual(comparison["differences"], [])
            self.assertEqual(result["status"], "succeeded")
            verify_preserved_trim_runtime(paths, created.path.name, runtime)

    def test_exit_zero_incomplete(self: TestTRIMExecution) -> None:
        """Reject exit zero when no native outputs or checkpoint were produced; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            paths, config, worker = make_test_project(Path(directory))
            for name, data in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(data)
            switch_trim_defaults(paths, config)
            inputs = resolve_trim_inputs(paths, config, worker, TRIM_REQUEST, TRIM_SELECTIONS)
            created = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            runtime = prepare_trim_simulation(paths, config, created, worker, inputs)
            with patch("src.srim.trim_execution.execute_trim_process", return_value={"pid": 42, "exit_code": 0, "elapsed_seconds": 0.1, "termination": "exit", "popups": [], "monitor_errors": []}):
                result = run_trim_simulation(paths, config, created.path.name, runtime)
            self.assertEqual(result["status"], "failed")
            self.assertIn("missing", result["failure"]["message"])
            verify_preserved_trim_runtime(paths, created.path.name, runtime)

    def test_popup_preserves_runtime(self: TestTRIMExecution) -> None:
        """Record popup evidence and terminal failure without any automatic response; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            paths, config, worker = make_test_project(Path(directory))
            for name, data in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(data)
            switch_trim_defaults(paths, config)
            inputs = resolve_trim_inputs(paths, config, worker, TRIM_REQUEST, TRIM_SELECTIONS)
            created = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            runtime = prepare_trim_simulation(paths, config, created, worker, inputs)
            popup = {"title": "Unexpected native dialog", "controls": [{"text": "Native error"}]}
            with patch("src.srim.trim_execution.execute_trim_process", return_value={"pid": 42, "exit_code": 1, "elapsed_seconds": 0.1, "termination": "popup", "popups": [popup], "monitor_errors": []}):
                result = run_trim_simulation(paths, config, created.path.name, runtime)
            report = json.loads((created.path / "output/raw/execution.json").read_bytes())
            self.assertEqual(result["status"], "failed")
            self.assertEqual(report["failure"]["code"], "popup")
            self.assertEqual(report["process"]["popups"], [popup])
            verify_preserved_trim_runtime(paths, created.path.name, runtime)

    def test_interruption_finalizes(self: TestTRIMExecution) -> None:
        """Preserve interrupted runtime and re-raise KeyboardInterrupt; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            paths, config, worker = make_test_project(Path(directory))
            for name, data in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(data)
            switch_trim_defaults(paths, config)
            inputs = resolve_trim_inputs(paths, config, worker, TRIM_REQUEST, TRIM_SELECTIONS)
            created = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            runtime = prepare_trim_simulation(paths, config, created, worker, inputs)
            with patch("src.srim.trim_execution.execute_trim_process", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    run_trim_simulation(paths, config, created.path.name, runtime)
            verify_preserved_trim_runtime(paths, created.path.name, runtime)

    def test_resume_preserves_parent(self: TestTRIMExecution) -> None:
        """Stage complete opaque saved state into a new record with explicit totals; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            paths, config, worker = make_test_project(Path(directory))
            for name, data in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(data)
            switch_trim_defaults(paths, config)
            request = deepcopy(TRIM_REQUEST)
            request["parameters"].update({"total_ions": 5, "mode": "ion-full", "outputs": {"ranges": True, "backscatter": True, "transmission": True, "sputtering": True, "collisions": "ions-and-recoils", "exyz_increment_eV": "1000"}})
            request["parameters"]["layers"][0]["overrides"]["components"] = [{"component_index": 1, "mass_amu": "28.086", "surface_binding_eV": "4.7"}]
            inputs = resolve_trim_inputs(paths, config, worker, request, TRIM_SELECTIONS)
            created = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            runtime = prepare_trim_simulation(paths, config, created, worker, inputs)
            shutil.copytree(Path(__file__).parent / "fixtures/trim/2013", runtime, dirs_exist_ok=True)
            for name in ("E2RECOIL", "IONIZ", "NOVAC", "PHONON", "SPUTTER", "STOPPING", "VACANCY", "XRANGE", "YZRADIAL#", "YZRANGE#", "Ioniz3D", "NoVac3D", "Phonon3D", "Range3D", "Recoil3D", "Vac3D"):
                (runtime / "SRIM Restore" / (name + ".sav")).write_bytes(b"opaque native array fixture")
            with patch("src.srim.trim_execution.verify_trim_runtime", return_value=inputs), patch("src.srim.trim_execution.execute_trim_process", return_value={"pid": 42, "exit_code": 0, "elapsed_seconds": 0.1, "termination": "exit", "popups": [], "monitor_errors": []}):
                self.assertEqual(run_trim_simulation(paths, config, created.path.name, runtime)["status"], "succeeded")
            parent_bytes = (created.path / "output/raw/runtime/SRIM Restore/TDATA.sav").read_bytes()
            child = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            resumed = prepare_trim_continuation(paths, config, child, worker, created.path.name, total_ions=10, autosave_ions=1)
            self.assertEqual((resumed / "TRIMAUTO").read_bytes(), b"2\r\ncheckpoint\\\r\n")
            self.assertEqual((created.path / "output/raw/runtime/SRIM Restore/TDATA.sav").read_bytes(), parent_bytes)
            verify_trim_runtime(paths, child.path.name, resumed)
            self.assertEqual(json.loads((child.path / "input/execution/control.json").read_bytes())["initial_count"], 5)

    def test_substitution_is_failure(self: TestTRIMExecution) -> None:
        """Preserve a native mass substitution and fail without rewriting inputs; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            paths, config, worker = make_test_project(Path(directory))
            for name, data in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(data)
            switch_trim_defaults(paths, config)
            request = deepcopy(TRIM_REQUEST)
            request["parameters"].update({"total_ions": 5, "mode": "ion-full", "outputs": {"ranges": True, "backscatter": True, "transmission": True, "sputtering": True, "collisions": "ions-and-recoils", "exyz_increment_eV": "1000"}})
            request["parameters"]["layers"][0]["overrides"]["components"] = [{"component_index": 1, "mass_amu": "28.086", "surface_binding_eV": "4.7"}]
            inputs = resolve_trim_inputs(paths, config, worker, request, TRIM_SELECTIONS)
            created = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            runtime = prepare_trim_simulation(paths, config, created, worker, inputs)
            shutil.copytree(Path(__file__).parent / "fixtures/trim/2013", runtime, dirs_exist_ok=True)
            saved = runtime / "SRIM Restore/TDATA.sav"
            saved.write_bytes(saved.read_bytes().replace(b"28.0860", b"29.0860"))
            with patch("src.srim.trim_execution.verify_trim_runtime", return_value=inputs), patch("src.srim.trim_execution.execute_trim_process", return_value={"pid": 42, "exit_code": 0, "elapsed_seconds": 0.1, "termination": "exit", "popups": [], "monitor_errors": []}):
                result = run_trim_simulation(paths, config, created.path.name, runtime)
            report = json.loads((created.path / "output/raw/execution.json").read_bytes())
            self.assertEqual(result["status"], "failed")
            self.assertEqual(report["failure"]["code"], "substitution")
            self.assertEqual(inputs.request, request)
            self.assertIn(b"29.0860", (created.path / "output/raw/runtime/SRIM Restore/TDATA.sav").read_bytes())

    def test_launch_failure(self: TestTRIMExecution) -> None:
        """Finalize expected launch failure and preserve the unexecuted runtime; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            paths, config, worker = make_test_project(Path(directory))
            for name, data in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(data)
            switch_trim_defaults(paths, config)
            inputs = resolve_trim_inputs(paths, config, worker, TRIM_REQUEST, TRIM_SELECTIONS)
            created = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            runtime = prepare_trim_simulation(paths, config, created, worker, inputs)
            with patch("src.srim.trim_execution.execute_trim_process", return_value={"pid": None, "exit_code": None, "elapsed_seconds": 0.1, "termination": "launch-error", "popups": [], "monitor_errors": ["Cannot assign owned job"]}):
                result = run_trim_simulation(paths, config, created.path.name, runtime)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(json.loads((created.path / "output/raw/execution.json").read_bytes())["failure"]["code"], "launch")
            verify_preserved_trim_runtime(paths, created.path.name, runtime)

    def test_preservation_failure_retains_worker(self: TestTRIMExecution) -> None:
        """Propagate preservation I/O failure with original runtime intact; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            paths, config, worker = make_test_project(Path(directory))
            for name, data in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(data)
            switch_trim_defaults(paths, config)
            inputs = resolve_trim_inputs(paths, config, worker, TRIM_REQUEST, TRIM_SELECTIONS)
            created = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            runtime = prepare_trim_simulation(paths, config, created, worker, inputs)
            original = (runtime / "TRIM.IN").read_bytes()
            with patch("src.srim.trim_execution.execute_trim_process", return_value={"pid": 42, "exit_code": 0, "elapsed_seconds": 0.1, "termination": "exit", "popups": [], "monitor_errors": []}), patch("src.srim.trim_execution.preserve_trim_runtime", side_effect=PermissionError("copy blocked")):
                with self.assertRaises(PermissionError) as caught:
                    run_trim_simulation(paths, config, created.path.name, runtime)
            self.assertEqual((runtime / "TRIM.IN").read_bytes(), original)
            self.assertTrue(any(str(runtime) in note for note in caught.exception.__notes__))
            self.assertNotEqual(json.loads((created.path / "metadata.json").read_bytes())["status"], "succeeded")

    def test_parse_failure_is_preserved(self: TestTRIMExecution) -> None:
        """Preserve malformed profile bytes and report their parser failure; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            paths, config, worker = make_test_project(Path(directory))
            for name, data in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(data)
            switch_trim_defaults(paths, config)
            inputs = resolve_trim_inputs(paths, config, worker, TRIM_REQUEST, TRIM_SELECTIONS)
            created = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            runtime = prepare_trim_simulation(paths, config, created, worker, inputs)
            with patch("src.srim.trim_execution.verify_trim_runtime", return_value=inputs):
                (runtime / "RANGE.txt").write_bytes(b"broken native profile")
                with patch("src.srim.trim_execution.execute_trim_process", return_value={"pid": 42, "exit_code": 0, "elapsed_seconds": 0.1, "termination": "exit", "popups": [], "monitor_errors": []}):
                    result = run_trim_simulation(paths, config, created.path.name, runtime)
            self.assertEqual(result["status"], "failed")
            self.assertEqual((created.path / "output/raw/runtime/RANGE.txt").read_bytes(), b"broken native profile")
            self.assertTrue(any(i["path"] == "RANGE.txt" and i["code"] == "format" for i in json.loads((created.path / "output/parsed/results.json").read_bytes())["issues"]))
