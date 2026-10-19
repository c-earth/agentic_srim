"""Verify isolated SRModule preparation, provenance, integrity, locks, and failure evidence."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, replace
from functools import partial
import hashlib
import json
from operator import eq
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from src.backend.config import Config
from src.backend.paths import create_base_directories, get_project_paths
from src.backend.records import CreateRequest, _lock_parent, create_directories
from src.backend.resources import register_resource, verify_resource
from src.srim.inputs import render_sr_input
from src.srim.metadata import JSONObject, read_simulation, start_simulation
from src.srim.runtime import _read_selected_resource, prepare_sr_simulation


class TestSRRuntime(unittest.TestCase):
    """Test preparation using unittest's inherited test-method-name constructor.

    setUp supplies temporary, paths, config, module, created, worker, request,
    electronic/nuclear selection dictionaries, and their resource references.
    These public fixtures isolate writes and are removed by registered cleanup.
    """

    def setUp(self: TestSRRuntime) -> None:
        """Create self's temporary project, installation, selections, and allocations; return None, register cleanup, and propagate setup errors."""
        self.temporary = TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temporary.cleanup)
        self.paths = get_project_paths(Path(self.temporary.name).absolute())
        create_base_directories(self.paths)
        self.config = Config("UTC", self.paths.root / "installation")
        self.module = self.config.srim_root / "SR Module"
        self.module.mkdir(parents=True)
        (self.module / "SRModule.exe").write_bytes(b"synthetic executable\x00\xff")
        (self.module / "VERSION").write_bytes(b" SRIM-2012.01 \r\nModule version information.\r\n")
        data = self.config.srim_root / "Data"
        data.mkdir()
        (data / "VERSION").write_bytes(b"SRIM-2013.00\r\n")
        self.electronic_reference = register_resource(self.paths, self.config, "engine", "electronic", "default", b"electronic coefficients\x00\xff\r\n")
        self.nuclear_reference = register_resource(self.paths, self.config, "engine", "nuclear", "default", b"nuclear coefficients\r\n")
        self.electronic: JSONObject = {
            "reference": asdict(self.electronic_reference),
            "source": {"description": "Explicit electronic source", "version": "user-e1", "citation": "electronic citation"},
            "runtime_filename": "SCOEF03.dat",
        }
        self.nuclear: JSONObject = {
            "reference": asdict(self.nuclear_reference),
            "source": {"description": "Explicit nuclear source", "version": None, "citation": None},
            "runtime_filename": "SNUC03.dat",
        }
        self.created, self.worker = create_directories(self.paths, self.config, [CreateRequest("srim"), CreateRequest("worker")]).completed
        self.request: JSONObject = {
            "schema_version": 1,
            "parameters": {
                "projectile": {"atomic_number": 1, "mass_amu": "1.008"},
                "target": {
                    "phase": "solid", "density_g_cm3": "1.0",
                    "elements": [
                        {"atomic_number": 1, "name": "Hydrogen", "stoichiometry": "2", "mass_amu": "1.008"},
                        {"atomic_number": 8, "name": "Oxygen", "stoichiometry": "1", "mass_amu": "15.999"},
                    ],
                },
                "correction": {"mode": "none", "applied_factor": "1.0"},
                "stopping_units": 5,
                "energy": {"mode": "range", "minimum_keV": "10", "maximum_keV": "10000"},
            },
        }

    def test_prepared_layout_and_provenance(self: TestSRRuntime) -> None:
        """Check self's complete bytes, manifests, module version, immutable sources, and lifecycle input sealing; return None or assertion failures."""
        original_request = deepcopy(self.request)
        original_electronic = deepcopy(self.electronic)
        source_paths = (self.module / "SRModule.exe", self.module / "VERSION", verify_resource(self.paths, self.electronic_reference), verify_resource(self.paths, self.nuclear_reference))
        original_bytes = {path: path.read_bytes() for path in source_paths}
        runtime = prepare_sr_simulation(self.paths, self.config, self.created, self.worker, self.request, self.electronic, self.nuclear)
        self.assertEqual(runtime, self.worker.path / "sr" / self.created.path.name)
        self.assertEqual({path.name for path in runtime.iterdir()}, {"SRModule.exe", "SR.IN", "VERSION", "SCOEF03.dat", "SNUC03.dat"})
        self.assertEqual((runtime / "SR.IN").read_bytes(), render_sr_input(original_request))
        self.assertEqual((runtime / "SRModule.exe").read_bytes(), original_bytes[source_paths[0]])
        self.assertEqual((runtime / "VERSION").read_bytes(), original_bytes[source_paths[1]])
        self.assertEqual((runtime / "SCOEF03.dat").read_bytes(), original_bytes[source_paths[2]])
        self.assertEqual((runtime / "SNUC03.dat").read_bytes(), original_bytes[source_paths[3]])
        record = self.created.path
        engine_input = record / "input" / "engine"
        self.assertEqual({path.name for path in engine_input.iterdir()}, {"SR.IN", "VERSION", "runtime.json"})
        manifest_bytes = (engine_input / "runtime.json").read_bytes()
        manifest = json.loads(manifest_bytes)
        expected_manifest = {
            "schema_version": 1,
            "files": [
                {"filename": path.name, "size_bytes": len(path.read_bytes()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                for path in sorted(runtime.iterdir())
            ],
        }
        self.assertEqual(manifest, expected_manifest)
        self.assertEqual(manifest_bytes, (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
        self.assertEqual((engine_input / "SR.IN").read_bytes(), (runtime / "SR.IN").read_bytes())
        self.assertEqual((engine_input / "VERSION").read_bytes(), (runtime / "VERSION").read_bytes())
        metadata = read_simulation(self.paths, record.name)
        self.assertEqual(metadata["status"], "created")
        self.assertEqual(metadata["engine"], {"name": "sr", "version": "SRIM-2012.01", "executable": {"name": "SRModule.exe", "sha256": hashlib.sha256(original_bytes[source_paths[0]]).hexdigest()}})
        self.assertNotIn("worker", metadata)
        self.assertEqual(metadata["resources"], [asdict(self.electronic_reference), asdict(self.nuclear_reference)])
        self.assertEqual([entry["path"] for entry in metadata["inputs"]], ["input/engine/SR.IN", "input/engine/VERSION", "input/engine/runtime.json", "input/request.json", "input/resolved.json"])
        resolved = json.loads((record / "input" / "resolved.json").read_bytes())
        self.assertEqual(resolved["parameters"], original_request["parameters"])
        self.assertEqual(resolved["resources"], [self.electronic, self.nuclear])
        self.assertEqual(resolved["cab"], original_request["parameters"]["correction"])
        self.assertEqual(resolved["sources"], [
            {"id": "sr-module-executable", "name": "SRModule.exe", "version": "SRIM-2012.01", "sha256": hashlib.sha256(original_bytes[source_paths[0]]).hexdigest(), "citation": None},
            {"id": "sr-module-version", "name": "VERSION", "version": "SRIM-2012.01", "sha256": hashlib.sha256(original_bytes[source_paths[1]]).hexdigest(), "citation": None},
        ])
        self.assertEqual(self.request, original_request)
        self.assertEqual(self.electronic, original_electronic)
        self.request["parameters"]["stopping_units"] = 1
        self.electronic["source"]["description"] = "changed by caller"
        self.assertEqual(json.loads((record / "input" / "request.json").read_bytes()), original_request)
        self.assertEqual(json.loads((record / "input" / "resolved.json").read_bytes())["resources"][0], original_electronic)
        self.assertEqual({path: path.read_bytes() for path in source_paths}, original_bytes)
        self.assertEqual(list((self.worker.path / "trim").iterdir()), [])
        self.assertEqual(start_simulation(self.paths, self.config, record.name)["inputs"], metadata["inputs"])

    def test_resource_integrity(self: TestSRRuntime) -> None:
        """Check self's corrupt payloads, changed read bytes, selection identity, and transformed-source provenance; return None or assertion failures."""
        payload = verify_resource(self.paths, self.electronic_reference)
        original = payload.read_bytes()
        payload.write_bytes(b"corrupt")
        with self.assertRaises(ValueError):
            prepare_sr_simulation(self.paths, self.config, self.created, self.worker, self.request, self.electronic, self.nuclear)
        self.assertEqual(list((self.worker.path / "sr").iterdir()), [])
        payload.write_bytes(original)
        with patch("src.srim.runtime._read_regular_bytes", return_value=b"changed after verification"):
            with self.assertRaisesRegex(ValueError, "changed while reading"):
                _read_selected_resource(self.paths, self.electronic, "electronic", "SCOEF03.dat")
        for selection in (self.electronic | {"runtime_filename": "scoef03.dat"}, self.nuclear):
            with self.assertRaises(ValueError):
                prepare_sr_simulation(self.paths, self.config, self.created, self.worker, self.request, selection, self.nuclear)
        transformed = register_resource(self.paths, self.config, "engine", "electronic", "v1", b"transformed coefficients")
        selection = deepcopy(self.electronic)
        selection["reference"] = asdict(transformed)
        selection["transformation"] = {"original": asdict(self.electronic_reference), "method": "explicit-test-transform", "version": "v1", "parameters": {"fields": 95}}
        payload.write_bytes(b"corrupt original")
        with self.assertRaises(ValueError):
            prepare_sr_simulation(self.paths, self.config, self.created, self.worker, self.request, selection, self.nuclear)
        payload.write_bytes(original)
        runtime = prepare_sr_simulation(self.paths, self.config, self.created, self.worker, self.request, selection, self.nuclear)
        self.assertEqual((runtime / "SCOEF03.dat").read_bytes(), b"transformed coefficients")
        metadata = read_simulation(self.paths, self.created.path.name)
        self.assertIn(asdict(self.electronic_reference), metadata["resources"])
        resolved = json.loads((self.created.path / "input" / "resolved.json").read_bytes())
        self.assertEqual(resolved["resources"][0], selection)

    def test_invalid_inputs_and_collisions(self: TestSRRuntime) -> None:
        """Check self's invalid requests, allocations, versions, missing assets, and existing destinations preserve data; return None or assertion failures."""
        for created in (replace(self.created, suffix=True), replace(self.created, timestamp=None), replace(self.created, path=self.created.path.parent / "wrong")):
            with self.assertRaises(ValueError):
                prepare_sr_simulation(self.paths, self.config, created, self.worker, self.request, self.electronic, self.nuclear)
        for worker in (replace(self.worker, suffix=True), replace(self.worker, kind="srim"), replace(self.worker, path=self.worker.path.parent / "wrong")):
            with self.assertRaises(ValueError):
                prepare_sr_simulation(self.paths, self.config, self.created, worker, self.request, self.electronic, self.nuclear)
        with self.assertRaises(ValueError):
            prepare_sr_simulation(self.paths, Config("UTC", Path("relative")), self.created, self.worker, self.request, self.electronic, self.nuclear)
        with self.assertRaises(ValueError):
            prepare_sr_simulation(self.paths, self.config, self.created, self.worker, self.request | {"parameters": {}}, self.electronic, self.nuclear)
        version_path = self.module / "VERSION"
        original_version = version_path.read_bytes()
        for version in (b"", b"\r\nSRIM-2012.01", b" \r\n", b"\xff"):
            version_path.write_bytes(version)
            with self.assertRaises(ValueError):
                prepare_sr_simulation(self.paths, self.config, self.created, self.worker, self.request, self.electronic, self.nuclear)
        version_path.unlink()
        with self.assertRaises(FileNotFoundError):
            prepare_sr_simulation(self.paths, self.config, self.created, self.worker, self.request, self.electronic, self.nuclear)
        version_path.write_bytes(original_version)
        runtime = self.worker.path / "sr" / self.created.path.name
        runtime.mkdir()
        (runtime / "keep").write_bytes(b"evidence")
        with self.assertRaises(FileExistsError):
            prepare_sr_simulation(self.paths, self.config, self.created, self.worker, self.request, self.electronic, self.nuclear)
        self.assertEqual((runtime / "keep").read_bytes(), b"evidence")
        (self.created.path / "keep").write_bytes(b"record evidence")
        with self.assertRaises(ValueError):
            prepare_sr_simulation(self.paths, self.config, self.created, self.worker, self.request, self.electronic, self.nuclear)
        self.assertEqual((self.created.path / "keep").read_bytes(), b"record evidence")

    def test_links_and_contention(self: TestSRRuntime) -> None:
        """Check self's worker/source links and worker/simulation/resource lock contention; return None or assertion failures, preserving reported partial artifacts."""
        for method in ("is_symlink", "is_junction"):
            for target in (self.worker.path, self.worker.path / "sr", self.module / "SRModule.exe", self.module / "VERSION"):
                with patch.object(Path, method, autospec=True, side_effect=partial(eq, target)):
                    with self.assertRaises(OSError):
                        prepare_sr_simulation(self.paths, self.config, self.created, self.worker, self.request, self.electronic, self.nuclear)
        for parent in (self.paths.workers, self.paths.resources):
            with _lock_parent(parent):
                with self.assertRaises(OSError):
                    prepare_sr_simulation(self.paths, self.config, self.created, self.worker, self.request, self.electronic, self.nuclear)
            self.assertEqual(list((self.worker.path / "sr").iterdir()), [])
        with _lock_parent(self.paths.simulation_memory):
            with self.assertRaises(OSError) as caught:
                prepare_sr_simulation(self.paths, self.config, self.created, self.worker, self.request, self.electronic, self.nuclear)
        runtime = self.worker.path / "sr" / self.created.path.name
        self.assertTrue(runtime.is_dir())
        self.assertFalse((self.created.path / "metadata.json").exists())
        self.assertTrue(any(str(runtime) in note for note in caught.exception.__notes__))

    def test_publication_failures(self: TestSRRuntime) -> None:
        """Check self's staged write/rename and record-initialization failures retain evidence without premature metadata; return None or assertion failures."""
        with patch("src.srim.metadata.os.fsync", side_effect=PermissionError("sync blocked")):
            with self.assertRaises(PermissionError) as caught:
                prepare_sr_simulation(self.paths, self.config, self.created, self.worker, self.request, self.electronic, self.nuclear)
        staging = list((self.worker.path / "sr").glob(".staging-*"))
        self.assertEqual(len(staging), 1)
        self.assertEqual((staging[0] / "SCOEF03.dat").read_bytes(), verify_resource(self.paths, self.electronic_reference).read_bytes())
        self.assertTrue(any(str(staging[0]) in note for note in caught.exception.__notes__))
        self.assertFalse((self.created.path / "metadata.json").exists())
        second = create_directories(self.paths, self.config, [CreateRequest("srim")]).completed[0]
        with patch.object(Path, "rename", side_effect=PermissionError("rename blocked")):
            with self.assertRaises(PermissionError):
                prepare_sr_simulation(self.paths, self.config, second, self.worker, self.request, self.electronic, self.nuclear)
        complete_staging = [path for path in (self.worker.path / "sr").glob(".staging-*") if len(list(path.iterdir())) == 5]
        self.assertEqual(len(complete_staging), 1)
        self.assertFalse((second.path / "metadata.json").exists())
        third = create_directories(self.paths, self.config, [CreateRequest("srim")]).completed[0]
        with patch("src.srim.metadata._write_json", side_effect=PermissionError("record write blocked")):
            with self.assertRaises(PermissionError) as caught:
                prepare_sr_simulation(self.paths, self.config, third, self.worker, self.request, self.electronic, self.nuclear)
        runtime = self.worker.path / "sr" / third.path.name
        self.assertEqual(len(list(runtime.iterdir())), 5)
        self.assertFalse((third.path / "metadata.json").exists())
        self.assertTrue(any(str(runtime) in note for note in caught.exception.__notes__))

    def test_independent_runtime_directories(self: TestSRRuntime) -> None:
        """Check self's two requests and workers preserve independent inputs, resources, trim data, and prior runtimes; return None or assertion failures."""
        (self.worker.path / "trim" / "keep").write_bytes(b"trim data")
        first = prepare_sr_simulation(self.paths, self.config, self.created, self.worker, self.request, self.electronic, self.nuclear)
        first_bytes = {path.name: path.read_bytes() for path in first.iterdir()}
        second_created = create_directories(self.paths, self.config, [CreateRequest("srim")]).completed[0]
        self.request["parameters"]["energy"] = {"mode": "list", "values_keV": ["3.98", "1.02", "3.98"]}
        self.request["parameters"]["correction"] = {"mode": "supplied", "applied_factor": "1.05", "source": "measurement", "reason": "fit"}
        second = prepare_sr_simulation(self.paths, self.config, second_created, self.worker, self.request, self.electronic, self.nuclear)
        self.assertNotEqual(first, second)
        self.assertTrue((second / "SR.IN").read_bytes().endswith(b"0 0\r\n3.98\r\n1.02\r\n3.98\r\n0\r\n"))
        self.assertEqual({path.name: path.read_bytes() for path in first.iterdir()}, first_bytes)
        self.assertEqual((self.worker.path / "trim" / "keep").read_bytes(), b"trim data")
        resolved = json.loads((second_created.path / "input" / "resolved.json").read_bytes())
        self.assertEqual(resolved["cab"], self.request["parameters"]["correction"])
        third_created, other_worker = create_directories(self.paths, self.config, [CreateRequest("srim"), CreateRequest("worker")]).completed
        third = prepare_sr_simulation(self.paths, self.config, third_created, other_worker, self.request, self.electronic, self.nuclear)
        self.assertEqual(third.parent, other_worker.path / "sr")
        self.assertEqual((third / "SCOEF03.dat").read_bytes(), (first / "SCOEF03.dat").read_bytes())
