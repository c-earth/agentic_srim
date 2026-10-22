"""Verify pinned TRIM staging, offline replay and prepared-runtime integrity."""

from __future__ import annotations

from copy import deepcopy
import json
import hashlib
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from _fixtures import make_test_project
from test_trim_inputs import TRIM_INSTALLATION_FILES, TRIM_REQUEST, TRIM_SELECTIONS
from src.backend.records import CreateRequest, create_directories
from src.srim.trim_defaults import switch_trim_defaults
from src.srim.trim_resolution import resolve_trim_inputs
from src.srim.trim_runtime import load_trim_input_bundle, prepare_trim_continuation, prepare_trim_simulation, verify_trim_runtime
from src.srim.trim_execution import run_trim_simulation
from src.srim.trim_storage import write_trim_input_bundle
from src.srim.trim_dialogs import ENGINES


CACHE = Path(".cache/trim-execution/tests").resolve()
CACHE.mkdir(parents=True, exist_ok=True)


class TestTRIMRuntime(unittest.TestCase):
    """Build owned cache fixtures without launching any native process."""

    def test_opt_in_resume_rejected_without_staging(self: TestTRIMRuntime) -> None:
        """Reject automatic continuation of a sealed parent without writes or launches; return None."""
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
            original = {p.relative_to(created.path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in created.path.rglob("*") if p.is_file()}
            child = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            before_runtimes = sorted(p.name for p in (worker.path / "trim").iterdir())
            with patch("src.srim.trim_runtime.mkdtemp") as staging, patch("src.srim.trim_native.subprocess.Popen") as launch:
                with self.assertRaisesRegex(ValueError, "checkpoint .sav integration"):
                    prepare_trim_continuation(paths, config, child, worker, created.path.name, total_ions=10, autosave_ions=1, cascade_output="none")
                staging.assert_not_called()
                launch.assert_not_called()
            self.assertEqual(sorted(p.name for p in (worker.path / "trim").iterdir()), before_runtimes)
            self.assertEqual({p.relative_to(created.path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in created.path.rglob("*") if p.is_file()}, original)
            self.assertFalse((child.path / "input").exists())

    def test_policy_sealed_and_replayed(self: TestTRIMRuntime) -> None:
        """Seal opt-in controls and independently replay pinned policy; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            paths, config, worker = make_test_project(Path(directory))
            for name, data in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(data)
            switch_trim_defaults(paths, config)
            inputs = resolve_trim_inputs(paths, config, worker, TRIM_REQUEST, TRIM_SELECTIONS)
            created = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            with patch.dict(ENGINES, {"2013": hashlib.sha256(TRIM_INSTALLATION_FILES["TRIM.exe"]).hexdigest()}):
                runtime = prepare_trim_simulation(paths, config, created, worker, inputs, cascade_output="none")
                self.assertEqual(verify_trim_runtime(paths, created.path.name, runtime), inputs)
            control = json.loads((created.path / "input/execution/control.json").read_bytes())
            self.assertEqual(control["schema_version"], 2)
            self.assertEqual(control["dialog_policy"]["cascade_output"], "none")
            self.assertEqual(control["dialog_policy"]["schema_version"], 2)
            self.assertNotIn("resume_export_order", control["dialog_policy"])

    def test_policy_tampering(self: TestTRIMRuntime) -> None:
        """Reject policy edits against sealed inventories before launch; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            paths, config, worker = make_test_project(Path(directory))
            for name, data in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(data)
            switch_trim_defaults(paths, config)
            inputs = resolve_trim_inputs(paths, config, worker, TRIM_REQUEST, TRIM_SELECTIONS)
            created = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            with patch.dict(ENGINES, {"2013": hashlib.sha256(TRIM_INSTALLATION_FILES["TRIM.exe"]).hexdigest()}):
                runtime = prepare_trim_simulation(paths, config, created, worker, inputs, cascade_output="none")
                path = created.path / "input/execution/control.json"
                control = json.loads(path.read_bytes())
                control["dialog_policy"]["expected_count"] += 1
                path.write_text(json.dumps(control), encoding="utf-8")
                with self.assertRaises(ValueError):
                    verify_trim_runtime(paths, created.path.name, runtime)

    def test_legacy_control_compatible(self: TestTRIMRuntime) -> None:
        """Keep None's exact version-one control keys and values; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            paths, config, worker = make_test_project(Path(directory))
            for name, data in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(data)
            switch_trim_defaults(paths, config)
            inputs = resolve_trim_inputs(paths, config, worker, TRIM_REQUEST, TRIM_SELECTIONS)
            created = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            runtime = prepare_trim_simulation(paths, config, created, worker, inputs)
            control = json.loads((created.path / "input/execution/control.json").read_bytes())
            self.assertEqual(control, {"schema_version": 1, "engine": "trim", "kind": "fresh", "parent_id": None, "initial_count": 0, "total_ions": 1000, "autosave_ions": 10000, "event_prefix_sha256": None, "edits": []})
            self.assertEqual(verify_trim_runtime(paths, created.path.name, runtime), inputs)

    def test_quote_preflight_before_staging(self: TestTRIMRuntime) -> None:
        """Reject verified failing names before creating runtime contents; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            paths, config, worker = make_test_project(Path(directory))
            for name, data in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(data)
            switch_trim_defaults(paths, config)
            request = deepcopy(TRIM_REQUEST)
            request["parameters"]["name"] = 'quoted "name"'
            inputs = resolve_trim_inputs(paths, config, worker, request, TRIM_SELECTIONS)
            created = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            with patch.dict(ENGINES, {"2013": hashlib.sha256(TRIM_INSTALLATION_FILES["TRIM.exe"]).hexdigest()}):
                with self.assertRaisesRegex(ValueError, "doubled-quote"):
                    prepare_trim_simulation(paths, config, created, worker, inputs)
            self.assertEqual(list((worker.path / "trim").iterdir()), [])

    def test_event_count_preflight(self: TestTRIMRuntime) -> None:
        """Reject insufficient actual events before staging a verified engine; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            paths, config, worker = make_test_project(Path(directory))
            for name, data in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(data)
            switch_trim_defaults(paths, config)
            request = deepcopy(TRIM_REQUEST)
            request["parameters"].update({"mode": "recoil-quick", "total_ions": 2, "events": [{"id": "T0001", "atomic_number": 14, "energy_eV": "1000", "position_angstrom": ["100", "0", "0"], "direction_cosines": ["1", "0", "0"]}]})
            inputs = resolve_trim_inputs(paths, config, worker, request, TRIM_SELECTIONS)
            created = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            with patch.dict(ENGINES, {"2013": hashlib.sha256(TRIM_INSTALLATION_FILES["TRIM.exe"]).hexdigest()}):
                with self.assertRaisesRegex(ValueError, "fewer complete events"):
                    prepare_trim_simulation(paths, config, created, worker, inputs)
            self.assertEqual(list((worker.path / "trim").iterdir()), [])

    def test_fresh_pinned_staging(self: TestTRIMRuntime) -> None:
        """Prepare a pinned bundle and replay after installed executable changes; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            paths, config, worker = make_test_project(Path(directory))
            for name, data in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(data)
            switch_trim_defaults(paths, config)
            inputs = resolve_trim_inputs(paths, config, worker, TRIM_REQUEST, TRIM_SELECTIONS)
            (config.srim_root / "TRIM.exe").write_bytes(b"different live engine")
            created = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            runtime = prepare_trim_simulation(paths, config, created, worker, inputs)
            self.assertEqual((runtime / "TRIM.exe").read_bytes(), TRIM_INSTALLATION_FILES["TRIM.exe"])
            self.assertEqual((runtime / "TRIMAUTO").read_bytes(), b"1\r\n\r\n")
            self.assertEqual(verify_trim_runtime(paths, created.path.name, runtime), inputs)
            metadata = json.loads((created.path / "metadata.json").read_bytes())
            self.assertEqual(metadata["status"], "created")

    def test_offline_bundle_loading(self: TestTRIMRuntime) -> None:
        """Load exported hashes and reject unlisted evidence; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            paths, config, worker = make_test_project(Path(directory))
            for name, data in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(data)
            switch_trim_defaults(paths, config)
            inputs = resolve_trim_inputs(paths, config, worker, TRIM_REQUEST, TRIM_SELECTIONS)
            destination = paths.root / "export"
            write_trim_input_bundle(destination, inputs)
            self.assertEqual(load_trim_input_bundle(destination), inputs)
            (destination / "unlisted.txt").write_bytes(b"unexpected")
            with self.assertRaises(ValueError):
                load_trim_input_bundle(destination)

    def test_collision_and_tampering(self: TestTRIMRuntime) -> None:
        """Reject a modified staged native input before launch or reuse; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            paths, config, worker = make_test_project(Path(directory))
            for name, data in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(data)
            switch_trim_defaults(paths, config)
            inputs = resolve_trim_inputs(paths, config, worker, deepcopy(TRIM_REQUEST), TRIM_SELECTIONS)
            created = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            runtime = prepare_trim_simulation(paths, config, created, worker, inputs)
            (runtime / "TRIM.IN").write_bytes(b"tampered")
            with self.assertRaises(ValueError):
                verify_trim_runtime(paths, created.path.name, runtime)
            with self.assertRaises(ValueError):
                prepare_trim_simulation(paths, config, created, worker, inputs)

    def test_event_prefix_preserved(self: TestTRIMRuntime) -> None:
        """Append coherent structured events and reject any changed earlier event; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            paths, config, worker = make_test_project(Path(directory))
            for name, data in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(data)
            switch_trim_defaults(paths, config)
            event = {"id": "T0001", "atomic_number": 18, "energy_eV": "50000", "position_angstrom": ["0", "0", "0"], "direction_cosines": ["1", "0", "0"]}
            request = deepcopy(TRIM_REQUEST)
            request["parameters"].update({"total_ions": 5, "mode": "specified-ion-quick", "events": [event], "outputs": {"ranges": True, "backscatter": True, "transmission": True, "sputtering": True, "collisions": "ions-and-recoils", "exyz_increment_eV": "1000"}})
            request["parameters"]["layers"][0]["overrides"]["components"] = [{"component_index": 1, "mass_amu": "28.086", "surface_binding_eV": "4.7"}]
            inputs = resolve_trim_inputs(paths, config, worker, request, TRIM_SELECTIONS)
            parent = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            runtime = prepare_trim_simulation(paths, config, parent, worker, inputs)
            shutil.copytree(Path(__file__).parent / "fixtures/trim/2013", runtime, dirs_exist_ok=True)
            for name in ("E2RECOIL", "IONIZ", "NOVAC", "PHONON", "SPUTTER", "STOPPING", "VACANCY", "XRANGE", "YZRADIAL#", "YZRANGE#", "Ioniz3D", "NoVac3D", "Phonon3D", "Range3D", "Recoil3D", "Vac3D"):
                (runtime / "SRIM Restore" / (name + ".sav")).write_bytes(b"opaque array fixture")
            data_path = runtime / "SRIM Restore/TDATA.sav"
            data_path.write_bytes(data_path.read_bytes().replace(b"  2 1 1 1 1 1 0 ", b"  4 1 1 1 1 1 0 "))
            native_path = runtime / "SRIM Restore/TRIMIN.sav"
            native_path.write_bytes(native_path.read_bytes().replace(b"2 12345 0", b"4 12345 0"))
            with patch("src.srim.trim_execution.verify_trim_runtime", return_value=inputs), patch("src.srim.trim_execution.execute_trim_process", return_value={"pid": 42, "exit_code": 1, "elapsed_seconds": 0.1, "termination": "timeout", "popups": [], "monitor_errors": []}):
                self.assertEqual(run_trim_simulation(paths, config, parent.path.name, runtime)["status"], "failed")
            second = deepcopy(event)
            second["id"] = "T0002"
            original = deepcopy(event)
            child = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            resumed = prepare_trim_continuation(paths, config, child, worker, parent.path.name, total_ions=6, autosave_ions=1, events=[event, second])
            self.assertEqual(event, original)
            self.assertTrue((resumed / "TRIM.DAT").read_bytes().endswith(b"T0002 18 50000 0 0 0 1 0 0\r\n\x1a"))
            verify_trim_runtime(paths, child.path.name, resumed)
            invalid = deepcopy(event)
            invalid["energy_eV"] = "40000"
            another = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            with self.assertRaises(ValueError):
                prepare_trim_continuation(paths, config, another, worker, parent.path.name, total_ions=6, autosave_ions=1, events=[invalid, second])

    def test_resume_requires_complete_checkpoint(self: TestTRIMRuntime) -> None:
        """Reject failed parents without a complete native saved set; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            paths, config, worker = make_test_project(Path(directory))
            for name, data in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(data)
            switch_trim_defaults(paths, config)
            inputs = resolve_trim_inputs(paths, config, worker, TRIM_REQUEST, TRIM_SELECTIONS)
            parent = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            runtime = prepare_trim_simulation(paths, config, parent, worker, inputs)
            with patch("src.srim.trim_execution.execute_trim_process", return_value={"pid": 42, "exit_code": 1, "elapsed_seconds": 0.1, "termination": "timeout", "popups": [], "monitor_errors": []}):
                run_trim_simulation(paths, config, parent.path.name, runtime)
            child = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            with self.assertRaisesRegex(ValueError, "incomplete saved-state"):
                prepare_trim_continuation(paths, config, child, worker, parent.path.name, total_ions=1001, autosave_ions=1)
