"""Verify native input projection, sealed references and lazy evidence access."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import shutil
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from src.backend.config import Config
from src.backend.paths import create_base_directories, get_project_paths
from src.backend.records import CreateRequest, create_directories
from src.knowledge.source import project_simulation_inputs, read_source_file, read_source_record
from src.srim.inputs import render_sr_input
from src.srim.metadata import finish_simulation, initialize_simulation
from src.srim.trim_inputs import render_trim_events, render_trim_input


CACHE = Path(".cache/input-search/tests").resolve()
CACHE.mkdir(parents=True, exist_ok=True)
SR_REQUEST = {"schema_version": 1, "parameters": {"projectile": {"atomic_number": 2, "mass_amu": "4.0026"}, "target": {"phase": "solid", "density_g_cm3": "2.2", "elements": [{"atomic_number": 14, "name": "Si", "mass_amu": "28.086", "stoichiometry": "1"}, {"atomic_number": 8, "name": "O", "mass_amu": "15.999", "stoichiometry": "2"}]}, "correction": {"mode": "none", "applied_factor": "1.0"}, "stopping_units": 7, "energy": {"mode": "list", "values_keV": ["100", "500", "100"]}}}
TRIM_REQUEST = {"schema_version": 1, "engine": "trim", "parameters": {"name": "SiO2", "mode": "ion-quick", "projectile": {"atomic_number": 2, "mass_amu": "4.0026", "energy_keV": "100", "angle_degrees": "0"}, "total_ions": 2000, "random_seed": 0, "autosave_ions": 100, "outputs": {"ranges": True, "backscatter": False, "transmission": False, "sputtering": False, "collisions": "none", "exyz_increment_eV": "0"}, "plot": {"type": "none", "minimum_depth_angstrom": "0", "maximum_depth_angstrom": "1000"}, "events": None, "engine_family": "2013", "native_fields": {"legacy_bragg": "0", "reminders": 0, "stopping_version": 0}, "atoms": [{"atomic_number": 14, "mass_amu": "28.086", "displacement_eV": "15", "lattice_binding_eV": "2", "surface_binding_eV": "4.7"}, {"atomic_number": 8, "mass_amu": "15.999", "displacement_eV": "15", "lattice_binding_eV": "2", "surface_binding_eV": "0"}], "layers": [{"name": "silica", "phase": "solid", "thickness_angstrom": "1000", "density_g_cm3": "2.2", "stoichiometry": ["1", "2"], "correction_factor": "1"}]}}


class NativeFixture(unittest.TestCase):
    """Provide a cache-owned terminal SR record for subclasses using unittest inputs."""

    def setUp(self: NativeFixture) -> None:
        """Create self's synthetic native evidence and paths; return None, no engines."""
        self.temporary = TemporaryDirectory(dir=CACHE)
        self.addCleanup(self.temporary.cleanup)
        self.paths = get_project_paths(Path(self.temporary.name))
        create_base_directories(self.paths)
        self.config = Config("UTC", self.paths.root)
        self.created = create_directories(self.paths, self.config, [CreateRequest("srim")]).completed[0]
        self.request = deepcopy(SR_REQUEST)
        resolved = {"schema_version": 1, "parameters": self.request["parameters"], "sources": [], "resources": [], "cab": None}
        engine = {"name": "sr", "version": "2013", "executable": {"name": "SRModule.exe", "sha256": "a" * 64}}
        initialize_simulation(self.paths, self.created, engine, self.request, resolved, engine_inputs={"SR.IN": render_sr_input(self.request)})
        (self.created.path / "output/raw/available.txt").write_bytes(b"preserved output")
        with patch("src.knowledge.index.notify_simulation_finished"):
            finish_simulation(self.paths, self.config, self.created.path.name, {"message": "controlled failure", "exit_code": 1})


class TestSource(NativeFixture):
    """Verify projection from independent native input expectations, without live installs."""

    def test_continuation_count_and_appended_events(self: TestSource) -> None:
        """Use sealed continuation controls instead of parent count/events; return None."""
        sys.path.insert(0, str(Path(__file__).parents[1] / "srim"))
        self.addCleanup(sys.path.pop, 0)
        from _fixtures import make_test_project
        from test_trim_inputs import TRIM_INSTALLATION_FILES, TRIM_REQUEST as intent, TRIM_SELECTIONS
        from src.srim.trim_defaults import switch_trim_defaults
        from src.srim.trim_execution import run_trim_simulation
        from src.srim.trim_resolution import resolve_trim_inputs
        from src.srim.trim_runtime import prepare_trim_continuation, prepare_trim_simulation
        for event_mode in (False, True):
            paths, config, worker = make_test_project(self.paths.root / ("event" if event_mode else "ordinary"))
            for name, data in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(data)
            switch_trim_defaults(paths, config)
            request = deepcopy(intent)
            request["parameters"].update(total_ions=5, mode="specified-ion-quick" if event_mode else "ion-full", outputs={"ranges": True, "backscatter": True, "transmission": True, "sputtering": True, "collisions": "ions-and-recoils", "exyz_increment_eV": "1000"})
            request["parameters"]["layers"][0]["overrides"]["components"] = [{"component_index": 1, "mass_amu": "28.086", "surface_binding_eV": "4.7"}]
            event = {"id": "T0001", "atomic_number": 18, "energy_eV": "50000", "position_angstrom": ["0", "0", "0"], "direction_cosines": ["1", "0", "0"]}
            if event_mode:
                request["parameters"]["events"] = [event]
            inputs = resolve_trim_inputs(paths, config, worker, request, TRIM_SELECTIONS)
            parent = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            runtime = prepare_trim_simulation(paths, config, parent, worker, inputs)
            shutil.copytree(Path(__file__).parents[1] / "srim/fixtures/trim/2013", runtime, dirs_exist_ok=True)
            for name in ("E2RECOIL", "IONIZ", "NOVAC", "PHONON", "SPUTTER", "STOPPING", "VACANCY", "XRANGE", "YZRADIAL#", "YZRANGE#", "Ioniz3D", "NoVac3D", "Phonon3D", "Range3D", "Recoil3D", "Vac3D"):
                (runtime / "SRIM Restore" / (name + ".sav")).write_bytes(b"opaque fixture")
            if event_mode:
                path = runtime / "SRIM Restore/TDATA.sav"
                path.write_bytes(path.read_bytes().replace(b"  2 1 1 1 1 1 0 ", b"  4 1 1 1 1 1 0 "))
                path = runtime / "SRIM Restore/TRIMIN.sav"
                path.write_bytes(path.read_bytes().replace(b"2 12345 0", b"4 12345 0"))
            process = {"pid": 42, "exit_code": 1, "elapsed_seconds": 0.1, "termination": "timeout", "popups": [], "monitor_errors": []}
            with patch("src.srim.trim_execution.verify_trim_runtime", return_value=inputs), patch("src.srim.trim_execution.execute_trim_process", return_value=process):
                run_trim_simulation(paths, config, parent.path.name, runtime)
            child = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            appended = [{**event, "id": "T0002", "energy_eV": "60000"}]
            prepare_trim_continuation(paths, config, child, worker, parent.path.name, total_ions=6, autosave_ions=1, events=[event, *appended] if event_mode else None)
            finish_simulation(paths, config, child.path.name, {"message": "not launched", "exit_code": None})
            result = project_simulation_inputs(paths, child.path.name)
            self.assertEqual(result["inputs"]["total_ions"], 6)
            if event_mode:
                self.assertEqual([item["id"] for item in result["inputs"]["events"]], ["T0001", "T0002"])
                self.assertEqual(result["inputs"]["energy"]["values_keV"], ["50.000", "60.000"])

    def test_saved_inputs_and_file_access(self: TestSource) -> None:
        """Keep repeated energies, source status and bounded output bytes; return None."""
        record = project_simulation_inputs(self.paths, self.created.path.name)
        self.assertEqual(record["inputs"]["energy"]["values_keV"], ["100", "500", "100"])
        self.assertEqual(record["inputs"]["layers"][0]["components"][1]["atomic_proportion"], "2")
        self.assertEqual(record["execution_status"], "failed")
        output = next(file for file in record["files"] if file["role"] == "output")
        self.assertEqual(read_source_file(self.paths, record["reference"], output), b"preserved output")
        with self.assertRaises(ValueError):
            read_source_file(self.paths, record["reference"], output, max_bytes=1)
        (self.created.path / output["path"]).write_bytes(b"modified")
        self.assertEqual(read_source_record(self.paths, record["reference"]), record)
        with self.assertRaises(ValueError):
            read_source_file(self.paths, record["reference"], output)

    def test_input_tamper_and_pinned_metadata(self: TestSource) -> None:
        """Reject changed metadata identity and changed source bytes; return None."""
        record = project_simulation_inputs(self.paths, self.created.path.name)
        path = self.created.path / "metadata.json"
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaises(ValueError):
            read_source_record(self.paths, record["reference"])
        (self.created.path / "input/engine/SR.IN").write_bytes(b"tampered")
        with self.assertRaises(ValueError):
            project_simulation_inputs(self.paths, self.created.path.name)

    def test_render_mismatch_despite_updated_inventory(self: TestSource) -> None:
        """Detect altered native settings even when inventory hashes are replaced; return None."""
        path = self.created.path / "input/engine/SR.IN"
        data = path.read_bytes().replace(b"100\r\n", b"101\r\n")
        path.write_bytes(data)
        metadata_path = self.created.path / "metadata.json"
        metadata = json.loads(metadata_path.read_bytes())
        for item in metadata["inputs"]:
            if item["path"] == "input/engine/SR.IN":
                item.update(sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data))
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "reproduce"):
            project_simulation_inputs(self.paths, self.created.path.name)

    def test_trim_ordinary_and_event_projection(self: TestSource) -> None:
        """Verify sealed TRIM layers, requested counts and actual event energies; return None."""
        for event_mode in (False, True):
            created = create_directories(self.paths, self.config, [CreateRequest("srim")]).completed[0]
            request = deepcopy(TRIM_REQUEST)
            if event_mode:
                request["parameters"].update(mode="specified-ion-quick", events=[{"id": "A", "atomic_number": 2, "energy_eV": "50000", "position_angstrom": ["0", "0", "0"], "direction_cosines": ["1", "0", "0"]}, {"id": "B", "atomic_number": 18, "energy_eV": "100000", "position_angstrom": ["0", "0", "0"], "direction_cosines": ["1", "0", "0"]}])
            for directory in ("input/engine", "output/raw", "output/parsed"):
                (created.path / directory).mkdir(parents=True, exist_ok=True)
            (created.path / "input/effective-request.json").write_text(json.dumps(request), encoding="utf-8")
            (created.path / "input/engine/TRIM.IN").write_bytes(render_trim_input(request))
            if event_mode:
                (created.path / "input/engine/TRIM.DAT").write_bytes(render_trim_events(request))
            from src.srim.metadata import _inventory
            metadata = json.loads((self.created.path / "metadata.json").read_bytes())
            metadata.update(id=created.path.name, engine={"name": "trim", "version": "2013", "executable": {"name": "TRIM.exe", "sha256": "b" * 64}}, inputs=_inventory(created.path / "input", created.path), outputs=[])
            (created.path / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
            result = project_simulation_inputs(self.paths, created.path.name)
            self.assertEqual(result["inputs"]["total_ions"], 2000)
            self.assertEqual(result["inputs"]["random_seed"], 0)
            self.assertEqual(result["inputs"]["layers"][0]["thickness_angstrom"], "1000")
            self.assertEqual(result["inputs"]["energy"]["values_keV"], ["50.000", "100.000"] if event_mode else ["100"])
