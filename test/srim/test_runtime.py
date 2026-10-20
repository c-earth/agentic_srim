"""Verify SRModule preparation, parsing, execution, result preservation, and lifecycle evidence."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, replace
from functools import partial
import hashlib
import json
from operator import eq
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from typing import BinaryIO
import unittest
from unittest.mock import patch

from src.backend.config import Config
from src.backend.paths import create_base_directories, get_project_paths
from src.backend.records import CreateRequest, _lock_parent, _lock_worker, create_directories
from src.backend.resources import register_resource, verify_resource
from src.srim.inputs import render_sr_input
from src.srim.execution import run_sr_simulation, verify_sr_runtime
from src.srim.metadata import JSONObject, ResolvedSRInputs, _inventory, _write_json, read_simulation, start_simulation
from src.srim.outputs import STOPPING_LABELS, _matches_printed, _parse_number, parse_sr_output
from src.srim.runtime import _prepare_sr_runtime, _read_selected_resource


class TestSRRuntime(unittest.TestCase):
    """Test SR workflows using unittest's inherited test-method-name constructor.

    setUp supplies temporary, paths, config, module, created, worker, request,
    electronic/nuclear selection dictionaries, and their resource references.
    engine_output, process_returncode, process_error, produce_output,
    mutate_runtime, and mutate_input configure the subprocess mock. These public
    fixtures isolate writes and are removed by registered cleanup.
    """

    def setUp(self: TestSRRuntime) -> None:
        """Create self's temporary project, installation, selections, allocations, and mock process configuration; return None, register cleanup, and propagate setup errors."""
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
        self.electronic_reference = register_resource(self.paths, self.config, "engine", "electronic", "v1", b"electronic coefficients\x00\xff\r\n")
        self.nuclear_reference = register_resource(self.paths, self.config, "engine", "nuclear", "v1", b"nuclear coefficients\r\n")
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
        self.pinned_sources = [
            {"id": "sr-module-executable", "name": "SRModule.exe", "version": "SRIM-2012.01", "sha256": hashlib.sha256((self.module / "SRModule.exe").read_bytes()).hexdigest(), "citation": None},
            {"id": "sr-module-version", "name": "VERSION", "version": "SRIM-2012.01", "sha256": hashlib.sha256((self.module / "VERSION").read_bytes()).hexdigest(), "citation": None},
        ]
        self.pinned_engine = {"SRModule.exe": (self.module / "SRModule.exe").read_bytes(), "VERSION": (self.module / "VERSION").read_bytes()}
        self.engine_output = (Path(__file__).parent / "fixtures" / "sr-range.txt").read_bytes()
        self.process_returncode = 0
        self.process_error: BaseException | None = None
        self.produce_output = True
        self.mutate_runtime = False
        self.mutate_input = False

    def _fake_sr_run(
        self: TestSRRuntime,
        args: list[str],
        *,
        cwd: Path,
        stdin: int,
        stdout: BinaryIO,
        stderr: BinaryIO,
        timeout: float,
        check: bool,
        creationflags: int,
        startupinfo: subprocess.STARTUPINFO | None,
    ) -> subprocess.CompletedProcess[bytes]:
        """Simulate args at cwd for self's process/output fixture configuration.

        Assert closed stdin, writable binary stdout/stderr, positive timeout,
        disabled check, console creationflags and config-matched startupinfo,
        running metadata, and worker
        locking. Write exact diagnostics and optional output or configured input
        mutations. Return configured exit status or raise configured process_error.
        Effects remain in self's temporary project; assertion failures propagate.
        """
        self.assertEqual(args, [str(cwd / "SRModule.exe")])
        self.assertEqual(stdin, subprocess.DEVNULL)
        self.assertGreater(timeout, 0)
        self.assertFalse(check)
        self.assertEqual(creationflags, subprocess.CREATE_NO_WINDOW)
        if self.config.hide_sr_window:
            self.assertIsInstance(startupinfo, subprocess.STARTUPINFO)
            self.assertTrue(startupinfo.dwFlags & subprocess.STARTF_USESHOWWINDOW)
            self.assertEqual(startupinfo.wShowWindow, subprocess.SW_HIDE)
        else:
            self.assertIsNone(startupinfo)
        self.assertEqual(read_simulation(self.paths, cwd.name)["status"], "running")
        with self.assertRaises(OSError):
            with _lock_worker(self.paths, cwd.parent.parent):
                pass
        stdout.write(b"engine stdout\x00\xff\r\n")
        stderr.write(b"engine stderr\r\n")
        if self.produce_output:
            (cwd / "stopping.txt").write_bytes(self.engine_output)
        if self.mutate_runtime:
            (cwd / "SCOEF03.dat").write_bytes(b"changed during execution")
        if self.mutate_input:
            (self.paths.simulation_memory / cwd.name / "input" / "engine" / "SR.IN").write_bytes(b"changed sealed input")
        if self.process_error is not None:
            raise self.process_error
        return subprocess.CompletedProcess(args, self.process_returncode)

    def test_prepared_layout_and_provenance(self: TestSRRuntime) -> None:
        """Check self's complete bytes, manifests, module version, immutable sources, and lifecycle input sealing; return None or assertion failures."""
        original_request = deepcopy(self.request)
        original_electronic = deepcopy(self.electronic)
        source_paths = (self.module / "SRModule.exe", self.module / "VERSION", verify_resource(self.paths, self.electronic_reference), verify_resource(self.paths, self.nuclear_reference))
        original_bytes = {path: path.read_bytes() for path in source_paths}
        runtime = _prepare_sr_runtime(self.paths, self.config, self.created, self.worker, ResolvedSRInputs((self.request), {}, (self.request), {"schema_version": 1, "parameters": (self.request).get("parameters", {}), "sources": self.pinned_sources, "resources": [self.electronic, self.nuclear], "cab": (self.request).get("parameters", {}).get("correction")}, (), tuple({**self.pinned_engine, "SCOEF03.dat": _read_selected_resource(self.paths, self.electronic, "electronic", "SCOEF03.dat"), "SNUC03.dat": _read_selected_resource(self.paths, self.nuclear, "nuclear", "SNUC03.dat")}.items())))
        self.assertEqual(runtime, self.worker.path / "sr" / self.created.path.name)
        self.assertEqual({path.name for path in runtime.iterdir()}, {"SRModule.exe", "SR.IN", "VERSION", "SCOEF03.dat", "SNUC03.dat"})
        self.assertEqual((runtime / "SR.IN").read_bytes(), render_sr_input(original_request))
        self.assertEqual((runtime / "SRModule.exe").read_bytes(), original_bytes[source_paths[0]])
        self.assertEqual((runtime / "VERSION").read_bytes(), original_bytes[source_paths[1]])
        self.assertEqual((runtime / "SCOEF03.dat").read_bytes(), original_bytes[source_paths[2]])
        self.assertEqual((runtime / "SNUC03.dat").read_bytes(), original_bytes[source_paths[3]])
        record = self.created.path
        engine_input = record / "input" / "engine"
        self.assertEqual({path.name for path in engine_input.iterdir()}, {"SR.IN", "VERSION", "runtime.json", "SRModule.exe", "SCOEF03.dat", "SNUC03.dat"})
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
        self.assertEqual([entry["path"] for entry in metadata["inputs"]], ["input/engine/SCOEF03.dat", "input/engine/SNUC03.dat", "input/engine/SR.IN", "input/engine/SRModule.exe", "input/engine/VERSION", "input/engine/runtime.json", "input/request.json", "input/resolved.json"])
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
            _prepare_sr_runtime(self.paths, self.config, self.created, self.worker, ResolvedSRInputs((self.request), {}, (self.request), {"schema_version": 1, "parameters": (self.request).get("parameters", {}), "sources": self.pinned_sources, "resources": [self.electronic, self.nuclear], "cab": (self.request).get("parameters", {}).get("correction")}, (), tuple({**self.pinned_engine, "SCOEF03.dat": _read_selected_resource(self.paths, self.electronic, "electronic", "SCOEF03.dat"), "SNUC03.dat": _read_selected_resource(self.paths, self.nuclear, "nuclear", "SNUC03.dat")}.items())))
        self.assertEqual(list((self.worker.path / "sr").iterdir()), [])
        payload.write_bytes(original)
        with patch("src.srim.runtime._read_regular_bytes", return_value=b"changed after verification"):
            with self.assertRaisesRegex(ValueError, "changed while reading"):
                _read_selected_resource(self.paths, self.electronic, "electronic", "SCOEF03.dat")
        for selection in (self.electronic | {"runtime_filename": "scoef03.dat"}, self.nuclear):
            with self.assertRaises(ValueError):
                _prepare_sr_runtime(self.paths, self.config, self.created, self.worker, ResolvedSRInputs((self.request), {}, (self.request), {"schema_version": 1, "parameters": (self.request).get("parameters", {}), "sources": self.pinned_sources, "resources": [selection, self.nuclear], "cab": (self.request).get("parameters", {}).get("correction")}, (), tuple({**self.pinned_engine, "SCOEF03.dat": _read_selected_resource(self.paths, selection, "electronic", "SCOEF03.dat"), "SNUC03.dat": _read_selected_resource(self.paths, self.nuclear, "nuclear", "SNUC03.dat")}.items())))
        transformed = register_resource(self.paths, self.config, "engine", "electronic", "v2", b"transformed coefficients")
        selection = deepcopy(self.electronic)
        selection["reference"] = asdict(transformed)
        selection["transformation"] = {"original": asdict(self.electronic_reference), "method": "explicit-test-transform", "version": "v1", "parameters": {"fields": 95}}
        payload.write_bytes(b"corrupt original")
        with self.assertRaises(ValueError):
            _prepare_sr_runtime(self.paths, self.config, self.created, self.worker, ResolvedSRInputs((self.request), {}, (self.request), {"schema_version": 1, "parameters": (self.request).get("parameters", {}), "sources": self.pinned_sources, "resources": [selection, self.nuclear], "cab": (self.request).get("parameters", {}).get("correction")}, (), tuple({**self.pinned_engine, "SCOEF03.dat": _read_selected_resource(self.paths, selection, "electronic", "SCOEF03.dat"), "SNUC03.dat": _read_selected_resource(self.paths, self.nuclear, "nuclear", "SNUC03.dat")}.items())))
        payload.write_bytes(original)
        runtime = _prepare_sr_runtime(self.paths, self.config, self.created, self.worker, ResolvedSRInputs((self.request), {}, (self.request), {"schema_version": 1, "parameters": (self.request).get("parameters", {}), "sources": self.pinned_sources, "resources": [selection, self.nuclear], "cab": (self.request).get("parameters", {}).get("correction")}, (), tuple({**self.pinned_engine, "SCOEF03.dat": _read_selected_resource(self.paths, selection, "electronic", "SCOEF03.dat"), "SNUC03.dat": _read_selected_resource(self.paths, self.nuclear, "nuclear", "SNUC03.dat")}.items())))
        self.assertEqual((runtime / "SCOEF03.dat").read_bytes(), b"transformed coefficients")
        metadata = read_simulation(self.paths, self.created.path.name)
        self.assertIn(asdict(self.electronic_reference), metadata["resources"])
        resolved = json.loads((self.created.path / "input" / "resolved.json").read_bytes())
        self.assertEqual(resolved["resources"][0], selection)

    def test_invalid_inputs_and_collisions(self: TestSRRuntime) -> None:
        """Check self's invalid requests, allocations, versions, missing assets, and existing destinations preserve data; return None or assertion failures."""
        for created in (replace(self.created, suffix=True), replace(self.created, timestamp=None), replace(self.created, path=self.created.path.parent / "wrong")):
            with self.assertRaises(ValueError):
                _prepare_sr_runtime(self.paths, self.config, created, self.worker, ResolvedSRInputs((self.request), {}, (self.request), {"schema_version": 1, "parameters": (self.request).get("parameters", {}), "sources": self.pinned_sources, "resources": [self.electronic, self.nuclear], "cab": (self.request).get("parameters", {}).get("correction")}, (), tuple({**self.pinned_engine, "SCOEF03.dat": _read_selected_resource(self.paths, self.electronic, "electronic", "SCOEF03.dat"), "SNUC03.dat": _read_selected_resource(self.paths, self.nuclear, "nuclear", "SNUC03.dat")}.items())))
        for worker in (replace(self.worker, suffix=True), replace(self.worker, kind="srim"), replace(self.worker, path=self.worker.path.parent / "wrong")):
            with self.assertRaises(ValueError):
                _prepare_sr_runtime(self.paths, self.config, self.created, worker, ResolvedSRInputs((self.request), {}, (self.request), {"schema_version": 1, "parameters": (self.request).get("parameters", {}), "sources": self.pinned_sources, "resources": [self.electronic, self.nuclear], "cab": (self.request).get("parameters", {}).get("correction")}, (), tuple({**self.pinned_engine, "SCOEF03.dat": _read_selected_resource(self.paths, self.electronic, "electronic", "SCOEF03.dat"), "SNUC03.dat": _read_selected_resource(self.paths, self.nuclear, "nuclear", "SNUC03.dat")}.items())))
        with self.assertRaises(ValueError):
            _prepare_sr_runtime(self.paths, Config("UTC", Path("relative")), self.created, self.worker, ResolvedSRInputs((self.request), {}, (self.request), {"schema_version": 1, "parameters": (self.request).get("parameters", {}), "sources": self.pinned_sources, "resources": [self.electronic, self.nuclear], "cab": (self.request).get("parameters", {}).get("correction")}, (), tuple({**self.pinned_engine, "SCOEF03.dat": _read_selected_resource(self.paths, self.electronic, "electronic", "SCOEF03.dat"), "SNUC03.dat": _read_selected_resource(self.paths, self.nuclear, "nuclear", "SNUC03.dat")}.items())))
        with self.assertRaises(ValueError):
            _prepare_sr_runtime(self.paths, self.config, self.created, self.worker, ResolvedSRInputs((self.request | {"parameters": {}}), {}, (self.request | {"parameters": {}}), {"schema_version": 1, "parameters": (self.request | {"parameters": {}}).get("parameters", {}), "sources": self.pinned_sources, "resources": [self.electronic, self.nuclear], "cab": (self.request | {"parameters": {}}).get("parameters", {}).get("correction")}, (), tuple({**self.pinned_engine, "SCOEF03.dat": _read_selected_resource(self.paths, self.electronic, "electronic", "SCOEF03.dat"), "SNUC03.dat": _read_selected_resource(self.paths, self.nuclear, "nuclear", "SNUC03.dat")}.items())))
        version_path = self.module / "VERSION"
        original_version = version_path.read_bytes()
        for version in (b"", b"\r\nSRIM-2012.01", b" \r\n", b"\xff"):
            version_path.write_bytes(version)
            with self.assertRaises(ValueError):
                _prepare_sr_runtime(self.paths, self.config, self.created, self.worker, ResolvedSRInputs((self.request), {}, (self.request), {"schema_version": 1, "parameters": (self.request).get("parameters", {}), "sources": self.pinned_sources, "resources": [self.electronic, self.nuclear], "cab": (self.request).get("parameters", {}).get("correction")}, (), tuple({**self.pinned_engine, "SCOEF03.dat": _read_selected_resource(self.paths, self.electronic, "electronic", "SCOEF03.dat"), "SNUC03.dat": _read_selected_resource(self.paths, self.nuclear, "nuclear", "SNUC03.dat")}.items())))
        version_path.unlink()
        with self.assertRaises(FileNotFoundError):
            _prepare_sr_runtime(self.paths, self.config, self.created, self.worker, ResolvedSRInputs((self.request), {}, (self.request), {"schema_version": 1, "parameters": (self.request).get("parameters", {}), "sources": self.pinned_sources, "resources": [self.electronic, self.nuclear], "cab": (self.request).get("parameters", {}).get("correction")}, (), tuple({**self.pinned_engine, "SCOEF03.dat": _read_selected_resource(self.paths, self.electronic, "electronic", "SCOEF03.dat"), "SNUC03.dat": _read_selected_resource(self.paths, self.nuclear, "nuclear", "SNUC03.dat")}.items())))
        version_path.write_bytes(original_version)
        runtime = self.worker.path / "sr" / self.created.path.name
        runtime.mkdir()
        (runtime / "keep").write_bytes(b"evidence")
        with self.assertRaises(FileExistsError):
            _prepare_sr_runtime(self.paths, self.config, self.created, self.worker, ResolvedSRInputs((self.request), {}, (self.request), {"schema_version": 1, "parameters": (self.request).get("parameters", {}), "sources": self.pinned_sources, "resources": [self.electronic, self.nuclear], "cab": (self.request).get("parameters", {}).get("correction")}, (), tuple({**self.pinned_engine, "SCOEF03.dat": _read_selected_resource(self.paths, self.electronic, "electronic", "SCOEF03.dat"), "SNUC03.dat": _read_selected_resource(self.paths, self.nuclear, "nuclear", "SNUC03.dat")}.items())))
        self.assertEqual((runtime / "keep").read_bytes(), b"evidence")
        (self.created.path / "keep").write_bytes(b"record evidence")
        with self.assertRaises(ValueError):
            _prepare_sr_runtime(self.paths, self.config, self.created, self.worker, ResolvedSRInputs((self.request), {}, (self.request), {"schema_version": 1, "parameters": (self.request).get("parameters", {}), "sources": self.pinned_sources, "resources": [self.electronic, self.nuclear], "cab": (self.request).get("parameters", {}).get("correction")}, (), tuple({**self.pinned_engine, "SCOEF03.dat": _read_selected_resource(self.paths, self.electronic, "electronic", "SCOEF03.dat"), "SNUC03.dat": _read_selected_resource(self.paths, self.nuclear, "nuclear", "SNUC03.dat")}.items())))
        self.assertEqual((self.created.path / "keep").read_bytes(), b"record evidence")

    def test_links_and_contention(self: TestSRRuntime) -> None:
        """Check self's worker/source links and worker/simulation/resource lock contention; return None or assertion failures, preserving reported partial artifacts."""
        for method in ("is_symlink", "is_junction"):
            for target in (self.worker.path, self.worker.path / "sr", self.module / "SRModule.exe", self.module / "VERSION"):
                with patch.object(Path, method, autospec=True, side_effect=partial(eq, target)):
                    with self.assertRaises(OSError):
                        _prepare_sr_runtime(self.paths, self.config, self.created, self.worker, ResolvedSRInputs((self.request), {}, (self.request), {"schema_version": 1, "parameters": (self.request).get("parameters", {}), "sources": self.pinned_sources, "resources": [self.electronic, self.nuclear], "cab": (self.request).get("parameters", {}).get("correction")}, (), tuple({**self.pinned_engine, "SCOEF03.dat": _read_selected_resource(self.paths, self.electronic, "electronic", "SCOEF03.dat"), "SNUC03.dat": _read_selected_resource(self.paths, self.nuclear, "nuclear", "SNUC03.dat")}.items())))
        for parent in (self.paths.workers, self.paths.resources):
            with _lock_parent(parent):
                with self.assertRaises(OSError):
                    _prepare_sr_runtime(self.paths, self.config, self.created, self.worker, ResolvedSRInputs((self.request), {}, (self.request), {"schema_version": 1, "parameters": (self.request).get("parameters", {}), "sources": self.pinned_sources, "resources": [self.electronic, self.nuclear], "cab": (self.request).get("parameters", {}).get("correction")}, (), tuple({**self.pinned_engine, "SCOEF03.dat": _read_selected_resource(self.paths, self.electronic, "electronic", "SCOEF03.dat"), "SNUC03.dat": _read_selected_resource(self.paths, self.nuclear, "nuclear", "SNUC03.dat")}.items())))
            self.assertEqual(list((self.worker.path / "sr").iterdir()), [])
        with _lock_parent(self.paths.simulation_memory):
            with self.assertRaises(OSError) as caught:
                _prepare_sr_runtime(self.paths, self.config, self.created, self.worker, ResolvedSRInputs((self.request), {}, (self.request), {"schema_version": 1, "parameters": (self.request).get("parameters", {}), "sources": self.pinned_sources, "resources": [self.electronic, self.nuclear], "cab": (self.request).get("parameters", {}).get("correction")}, (), tuple({**self.pinned_engine, "SCOEF03.dat": _read_selected_resource(self.paths, self.electronic, "electronic", "SCOEF03.dat"), "SNUC03.dat": _read_selected_resource(self.paths, self.nuclear, "nuclear", "SNUC03.dat")}.items())))
        runtime = self.worker.path / "sr" / self.created.path.name
        self.assertTrue(runtime.is_dir())
        self.assertFalse((self.created.path / "metadata.json").exists())
        self.assertTrue(any(str(runtime) in note for note in caught.exception.__notes__))

    def test_publication_failures(self: TestSRRuntime) -> None:
        """Check self's staged write/rename and record-initialization failures retain evidence without premature metadata; return None or assertion failures."""
        with patch("src.srim.metadata.os.fsync", side_effect=PermissionError("sync blocked")):
            with self.assertRaises(PermissionError) as caught:
                _prepare_sr_runtime(self.paths, self.config, self.created, self.worker, ResolvedSRInputs((self.request), {}, (self.request), {"schema_version": 1, "parameters": (self.request).get("parameters", {}), "sources": self.pinned_sources, "resources": [self.electronic, self.nuclear], "cab": (self.request).get("parameters", {}).get("correction")}, (), tuple({**self.pinned_engine, "SCOEF03.dat": _read_selected_resource(self.paths, self.electronic, "electronic", "SCOEF03.dat"), "SNUC03.dat": _read_selected_resource(self.paths, self.nuclear, "nuclear", "SNUC03.dat")}.items())))
        staging = list((self.worker.path / "sr").glob(".staging-*"))
        self.assertEqual(len(staging), 1)
        self.assertEqual((staging[0] / "SCOEF03.dat").read_bytes(), verify_resource(self.paths, self.electronic_reference).read_bytes())
        self.assertTrue(any(str(staging[0]) in note for note in caught.exception.__notes__))
        self.assertFalse((self.created.path / "metadata.json").exists())
        second = create_directories(self.paths, self.config, [CreateRequest("srim")]).completed[0]
        with patch.object(Path, "rename", side_effect=PermissionError("rename blocked")):
            with self.assertRaises(PermissionError):
                _prepare_sr_runtime(self.paths, self.config, second, self.worker, ResolvedSRInputs((self.request), {}, (self.request), {"schema_version": 1, "parameters": (self.request).get("parameters", {}), "sources": self.pinned_sources, "resources": [self.electronic, self.nuclear], "cab": (self.request).get("parameters", {}).get("correction")}, (), tuple({**self.pinned_engine, "SCOEF03.dat": _read_selected_resource(self.paths, self.electronic, "electronic", "SCOEF03.dat"), "SNUC03.dat": _read_selected_resource(self.paths, self.nuclear, "nuclear", "SNUC03.dat")}.items())))
        complete_staging = [path for path in (self.worker.path / "sr").glob(".staging-*") if len(list(path.iterdir())) == 5]
        self.assertEqual(len(complete_staging), 1)
        self.assertFalse((second.path / "metadata.json").exists())
        third = create_directories(self.paths, self.config, [CreateRequest("srim")]).completed[0]
        with patch("src.srim.metadata._write_json", side_effect=PermissionError("record write blocked")):
            with self.assertRaises(PermissionError) as caught:
                _prepare_sr_runtime(self.paths, self.config, third, self.worker, ResolvedSRInputs((self.request), {}, (self.request), {"schema_version": 1, "parameters": (self.request).get("parameters", {}), "sources": self.pinned_sources, "resources": [self.electronic, self.nuclear], "cab": (self.request).get("parameters", {}).get("correction")}, (), tuple({**self.pinned_engine, "SCOEF03.dat": _read_selected_resource(self.paths, self.electronic, "electronic", "SCOEF03.dat"), "SNUC03.dat": _read_selected_resource(self.paths, self.nuclear, "nuclear", "SNUC03.dat")}.items())))
        runtime = self.worker.path / "sr" / third.path.name
        self.assertEqual(len(list(runtime.iterdir())), 5)
        self.assertFalse((third.path / "metadata.json").exists())
        self.assertTrue(any(str(runtime) in note for note in caught.exception.__notes__))

    def test_independent_runtime_directories(self: TestSRRuntime) -> None:
        """Check self's two requests and workers preserve independent inputs, resources, trim data, and prior runtimes; return None or assertion failures."""
        (self.worker.path / "trim" / "keep").write_bytes(b"trim data")
        first = _prepare_sr_runtime(self.paths, self.config, self.created, self.worker, ResolvedSRInputs((self.request), {}, (self.request), {"schema_version": 1, "parameters": (self.request).get("parameters", {}), "sources": self.pinned_sources, "resources": [self.electronic, self.nuclear], "cab": (self.request).get("parameters", {}).get("correction")}, (), tuple({**self.pinned_engine, "SCOEF03.dat": _read_selected_resource(self.paths, self.electronic, "electronic", "SCOEF03.dat"), "SNUC03.dat": _read_selected_resource(self.paths, self.nuclear, "nuclear", "SNUC03.dat")}.items())))
        first_bytes = {path.name: path.read_bytes() for path in first.iterdir()}
        second_created = create_directories(self.paths, self.config, [CreateRequest("srim")]).completed[0]
        self.request["parameters"]["energy"] = {"mode": "list", "values_keV": ["3.98", "1.02", "3.98"]}
        self.request["parameters"]["correction"] = {"mode": "supplied", "applied_factor": "1.05", "source": "measurement", "reason": "fit"}
        second = _prepare_sr_runtime(self.paths, self.config, second_created, self.worker, ResolvedSRInputs((self.request), {}, (self.request), {"schema_version": 1, "parameters": (self.request).get("parameters", {}), "sources": self.pinned_sources, "resources": [self.electronic, self.nuclear], "cab": (self.request).get("parameters", {}).get("correction")}, (), tuple({**self.pinned_engine, "SCOEF03.dat": _read_selected_resource(self.paths, self.electronic, "electronic", "SCOEF03.dat"), "SNUC03.dat": _read_selected_resource(self.paths, self.nuclear, "nuclear", "SNUC03.dat")}.items())))
        self.assertNotEqual(first, second)
        self.assertTrue((second / "SR.IN").read_bytes().endswith(b"0 0\r\n3.98\r\n1.02\r\n3.98\r\n0\r\n"))
        self.assertEqual({path.name: path.read_bytes() for path in first.iterdir()}, first_bytes)
        self.assertEqual((self.worker.path / "trim" / "keep").read_bytes(), b"trim data")
        resolved = json.loads((second_created.path / "input" / "resolved.json").read_bytes())
        self.assertEqual(resolved["cab"], self.request["parameters"]["correction"])
        third_created, other_worker = create_directories(self.paths, self.config, [CreateRequest("srim"), CreateRequest("worker")]).completed
        third = _prepare_sr_runtime(self.paths, self.config, third_created, other_worker, ResolvedSRInputs((self.request), {}, (self.request), {"schema_version": 1, "parameters": (self.request).get("parameters", {}), "sources": self.pinned_sources, "resources": [self.electronic, self.nuclear], "cab": (self.request).get("parameters", {}).get("correction")}, (), tuple({**self.pinned_engine, "SCOEF03.dat": _read_selected_resource(self.paths, self.electronic, "electronic", "SCOEF03.dat"), "SNUC03.dat": _read_selected_resource(self.paths, self.nuclear, "nuclear", "SNUC03.dat")}.items())))
        self.assertEqual(third.parent, other_worker.path / "sr")
        self.assertEqual((third / "SCOEF03.dat").read_bytes(), (first / "SCOEF03.dat").read_bytes())

    def test_parse_sr_range(self: TestSRRuntime) -> None:
        """Check self's captured range fixture, mixed units, precision, and source hash; return None without writes or raise assertions."""
        original = deepcopy(self.request)
        result = parse_sr_output(self.engine_output, self.request)
        self.assertEqual(set(result), {"schema_version", "energy_mode", "source", "stopping_units", "rows"})
        self.assertEqual(result["energy_mode"], "range")
        self.assertEqual(result["source"], {"filename": "stopping.txt", "sha256": hashlib.sha256(self.engine_output).hexdigest(), "size_bytes": len(self.engine_output)})
        self.assertEqual(result["stopping_units"], {"code": 5, "label": "MeV/(mg/cm2)"})
        self.assertEqual(len(result["rows"]), 79)
        self.assertEqual(result["rows"][0], {
            "energy": {"value": "10.00", "unit": "keV"},
            "electronic_stopping": "4.556E-01", "nuclear_stopping": "8.659E-03",
            "projected_range": {"value": "2507", "unit": "A"},
            "longitudinal_straggling": {"value": "651", "unit": "A"},
            "lateral_straggling": {"value": "678", "unit": "A"},
        })
        self.assertEqual(result["rows"][-1]["energy"], {"value": "10.00", "unit": "MeV"})
        self.assertEqual(result["rows"][-1]["projected_range"], {"value": "1.20", "unit": "mm"})
        self.assertEqual(result["rows"][-1]["longitudinal_straggling"], {"value": "53.86", "unit": "um"})
        self.assertEqual(self.request, original)

    def test_parse_sr_list(self: TestSRRuntime) -> None:
        """Check self's ordered duplicate and rounded list energies, numeric precision, and 1000 rows; return None or assertions without writes."""
        contents = (Path(__file__).parent / "fixtures" / "sr-list.txt").read_bytes()
        values = ["3.98", "1.02", "3.98", "10000"]
        self.request["parameters"]["energy"] = {"mode": "list", "values_keV": values}
        result = parse_sr_output(contents, self.request)
        self.assertEqual(result["energy_mode"], "list")
        self.assertEqual([row["requested_energy_keV"] for row in result["rows"]], values)
        self.assertEqual(result["rows"][0]["electronic_stopping"], "2.957E-01")
        self.assertEqual(result["rows"][0]["nuclear_stopping"], "1.681E-02")
        self.assertNotIn("projected_range", result["rows"][0])
        self.request["parameters"]["energy"]["values_keV"] = ["3.98001", "1.0200001", "3.97999", "10000.01"]
        self.assertEqual(parse_sr_output(contents, self.request)["rows"][0]["requested_energy_keV"], "3.98001")
        self.assertTrue(_matches_printed("1.235E+00", "1.23456789"))
        self.assertFalse(_matches_printed("1.235E+00", "1.24"))
        self.assertFalse(_matches_printed("0", "1"))
        self.request["parameters"]["energy"]["values_keV"] = ["3.98"] * 1000
        lines = contents.splitlines()
        large = b"\r\n".join(lines[:4] + [lines[4]] * 1000) + b"\r\n"
        self.assertEqual(len(parse_sr_output(large, self.request)["rows"]), 1000)

    def test_parse_sr_units(self: TestSRRuntime) -> None:
        """Check self's range/list unit labels for all eight selected codes; return None or assertions without filesystem writes."""
        list_contents = (Path(__file__).parent / "fixtures" / "sr-list.txt").read_bytes()
        for mode, contents in (("range", self.engine_output), ("list", list_contents)):
            self.request["parameters"]["energy"] = {"mode": "range", "minimum_keV": "10", "maximum_keV": "10000"} if mode == "range" else {"mode": "list", "values_keV": ["3.98", "1.02", "3.98", "10000"]}
            for code, label in enumerate(STOPPING_LABELS, 1):
                with self.subTest(mode=mode, code=code):
                    self.request["parameters"]["stopping_units"] = code
                    lines = contents.decode("ascii").splitlines()
                    units_index = next(index for index, line in enumerate(lines) if line.strip().startswith("Stopping Units ="))
                    lines[units_index] = "Stopping Units = " + label
                    modified = ("\r\n".join(lines) + "\r\n").encode("ascii")
                    self.assertEqual(parse_sr_output(modified, self.request)["stopping_units"], {"code": code, "label": label})

    def test_parse_sr_invalid_output(self: TestSRRuntime) -> None:
        """Check self's malformed, truncated, negative, unordered, nonfinite, and mismatched outputs; return None or assertions without writes."""
        contents = self.engine_output
        invalid = [
            b"", b"\xff", contents.decode("ascii"), contents[:100],
            contents.replace(b"MeV/(mg/cm2)", b"wrong units", 1),
            contents.replace(b"10.00 keV", b"0.00 keV", 1),
            contents.replace(b"11.00 keV", b"9.00 keV", 1),
            contents.replace(b"4.556E-01", b"-4.556E-01", 1),
            contents.replace(b"4.556E-01", b"NaN", 1),
            contents.replace(b"2507 A", b"2507 furlong", 1),
            contents.replace(b"10.00 keV", b"10.00 joule", 1),
            contents.replace(b"2507 A", b"A", 1),
            contents[:contents.index(b" Multiply Stopping")],
            contents.replace(b"9.9997E+00", b"-9.9997E+00", 1),
            contents.replace(b"(C) 1984", b"copyright 1984", 1),
        ]
        for index, payload in enumerate(invalid):
            with self.subTest(range_case=index), self.assertRaises(ValueError):
                parse_sr_output(payload, self.request)
        for token in ("-1", "NaN", "Infinity", "1,2", "1_000", "", "1e"):
            with self.subTest(token=token), self.assertRaises(ValueError):
                _parse_number(token, "test")
        self.request["parameters"]["energy"] = {"mode": "list", "values_keV": ["3.98", "1.02", "3.98", "10000"]}
        contents = (Path(__file__).parent / "fixtures" / "sr-list.txt").read_bytes()
        for payload in (contents.rsplit(b"\r\n", 2)[0], contents + b"1 2 3\r\n", contents.replace(b"3.980E+00", b"3.990E+00", 1), contents.replace(b"2.957E-01", b"-1", 1), contents.replace(b"Energy(keV)", b"Energy(MeV)", 1), self.engine_output):
            with self.subTest(list_payload=payload[:40]), self.assertRaises(ValueError):
                parse_sr_output(payload, self.request)

    def test_verify_sr_runtime(self: TestSRRuntime) -> None:
        """Check self's verified request, all runtime files, record inputs, resource provenance, and links; return None or assertions, restoring temporary edits."""
        runtime = _prepare_sr_runtime(self.paths, self.config, self.created, self.worker, ResolvedSRInputs((self.request), {}, (self.request), {"schema_version": 1, "parameters": (self.request).get("parameters", {}), "sources": self.pinned_sources, "resources": [self.electronic, self.nuclear], "cab": (self.request).get("parameters", {}).get("correction")}, (), tuple({**self.pinned_engine, "SCOEF03.dat": _read_selected_resource(self.paths, self.electronic, "electronic", "SCOEF03.dat"), "SNUC03.dat": _read_selected_resource(self.paths, self.nuclear, "nuclear", "SNUC03.dat")}.items())))
        self.assertEqual(verify_sr_runtime(self.paths, self.created.path.name, runtime), self.request)
        for name in ("SRModule.exe", "VERSION", "SCOEF03.dat", "SNUC03.dat", "SR.IN"):
            path = runtime / name
            original = path.read_bytes()
            path.write_bytes(original + b"changed")
            with self.subTest(filename=name), self.assertRaises(ValueError):
                verify_sr_runtime(self.paths, self.created.path.name, runtime)
            path.write_bytes(original)
        extra = runtime / "stopping.txt"
        extra.write_bytes(b"old output")
        with self.assertRaises(ValueError):
            verify_sr_runtime(self.paths, self.created.path.name, runtime)
        extra.unlink()
        manifest = self.created.path / "input" / "engine" / "runtime.json"
        original = manifest.read_bytes()
        manifest.write_bytes(b"{}")
        with self.assertRaises(ValueError):
            verify_sr_runtime(self.paths, self.created.path.name, runtime)
        manifest.write_bytes(original)
        for method in ("is_symlink", "is_junction"):
            with patch.object(Path, method, autospec=True, side_effect=partial(eq, runtime / "SRModule.exe")), self.assertRaises(OSError):
                verify_sr_runtime(self.paths, self.created.path.name, runtime)
        payload = verify_resource(self.paths, self.electronic_reference)
        original = payload.read_bytes()
        payload.write_bytes(b"corrupted resource")
        with self.assertRaises(ValueError):
            verify_sr_runtime(self.paths, self.created.path.name, runtime)
        payload.write_bytes(original)
        transformed_reference = register_resource(self.paths, self.config, "engine", "electronic", "v2", b"transformed coefficients")
        transformed = deepcopy(self.electronic)
        transformed["reference"] = asdict(transformed_reference)
        transformed["transformation"] = {"original": asdict(self.electronic_reference), "method": "test", "version": "v1", "parameters": {}}
        for selection in (transformed, self.electronic | {"transformation": {"original": asdict(self.electronic_reference), "method": "identity", "version": "v1", "parameters": {}}}):
            created = create_directories(self.paths, self.config, [CreateRequest("srim")]).completed[0]
            other_runtime = _prepare_sr_runtime(self.paths, self.config, created, self.worker, ResolvedSRInputs((self.request), {}, (self.request), {"schema_version": 1, "parameters": (self.request).get("parameters", {}), "sources": self.pinned_sources, "resources": [selection, self.nuclear], "cab": (self.request).get("parameters", {}).get("correction")}, (), tuple({**self.pinned_engine, "SCOEF03.dat": _read_selected_resource(self.paths, selection, "electronic", "SCOEF03.dat"), "SNUC03.dat": _read_selected_resource(self.paths, self.nuclear, "nuclear", "SNUC03.dat")}.items())))
            self.assertEqual(verify_sr_runtime(self.paths, created.path.name, other_runtime), self.request)
        metadata = read_simulation(self.paths, self.created.path.name)
        valid_manifest = json.loads(manifest.read_bytes())
        malformed = [valid_manifest | {"schema_version": True}, valid_manifest | {"files": {}}, valid_manifest | {"extra": 1}]
        for field, value in (("size_bytes", True), ("filename", "../outside"), ("sha256", "INVALID")):
            invalid_manifest = deepcopy(valid_manifest)
            invalid_manifest["files"][0][field] = value
            malformed.append(invalid_manifest)
        for invalid_manifest in malformed:
            _write_json(manifest, invalid_manifest)
            with patch("src.srim.execution._inventory", return_value=metadata["inputs"]), self.assertRaises(ValueError):
                verify_sr_runtime(self.paths, self.created.path.name, runtime)
        _write_json(manifest, valid_manifest)
        metadata = read_simulation(self.paths, self.created.path.name)
        metadata["engine"]["executable"]["sha256"] = "0" * 64
        _write_json(self.created.path / "metadata.json", metadata)
        with self.assertRaises(ValueError):
            verify_sr_runtime(self.paths, self.created.path.name, runtime)

    def test_run_sr_success(self: TestSRRuntime) -> None:
        """Check self's range/list execution, exact evidence, inventories, timestamps, immutable sources, and terminal guard; return None or assertions using mocks."""
        for mode in ("range", "list"):
            created = create_directories(self.paths, self.config, [CreateRequest("srim")]).completed[0]
            if mode == "list":
                self.request["parameters"]["energy"] = {"mode": "list", "values_keV": ["3.98", "1.02", "3.98", "10000"]}
                self.engine_output = (Path(__file__).parent / "fixtures" / "sr-list.txt").read_bytes()
            runtime = _prepare_sr_runtime(self.paths, self.config, created, self.worker, ResolvedSRInputs((self.request), {}, (self.request), {"schema_version": 1, "parameters": (self.request).get("parameters", {}), "sources": self.pinned_sources, "resources": [self.electronic, self.nuclear], "cab": (self.request).get("parameters", {}).get("correction")}, (), tuple({**self.pinned_engine, "SCOEF03.dat": _read_selected_resource(self.paths, self.electronic, "electronic", "SCOEF03.dat"), "SNUC03.dat": _read_selected_resource(self.paths, self.nuclear, "nuclear", "SNUC03.dat")}.items())))
            before = {entry.name: entry.read_bytes() for entry in runtime.iterdir()}
            with patch("src.srim.execution.subprocess.run", side_effect=self._fake_sr_run) as launched:
                result = run_sr_simulation(self.paths, self.config, created.path.name, runtime)
                launched.assert_called_once()
            self.assertEqual(result["status"], "succeeded")
            self.assertIsNotNone(result["started_at"])
            self.assertIsNotNone(result["finished_at"])
            self.assertEqual(result["outputs"], _inventory(created.path / "output", created.path))
            raw = created.path / "output" / "raw"
            self.assertEqual((raw / "stopping.txt").read_bytes(), self.engine_output)
            self.assertEqual((raw / "stdout.bin").read_bytes(), b"engine stdout\x00\xff\r\n")
            self.assertEqual((raw / "stderr.bin").read_bytes(), b"engine stderr\r\n")
            self.assertEqual({name: (runtime / name).read_bytes() for name in before}, before)
            diagnostic = json.loads((raw / "execution.json").read_bytes())
            self.assertEqual(diagnostic, {"schema_version": 1, "simulation_id": created.path.name, "runtime_path": runtime.relative_to(self.paths.root).as_posix(), "timeout_seconds": 60.0, "stage": "completed", "exit_code": 0, "timed_out": False, "message": None})
            parsed = json.loads((created.path / "output" / "parsed" / "result.json").read_bytes())
            self.assertEqual(parsed, parse_sr_output(self.engine_output, self.request))
            self.assertEqual((raw / "execution.json").read_bytes(), (json.dumps(diagnostic, indent=2) + "\n").encode("utf-8"))
            with patch("src.srim.execution.subprocess.run") as launched, self.assertRaises(ValueError):
                run_sr_simulation(self.paths, self.config, created.path.name, runtime)
            launched.assert_not_called()

    def test_run_sr_window_visibility(self: TestSRRuntime) -> None:
        """Check self's hidden/default and visible launches and invalid direct settings.

        Prepare isolated records and simulate engine output through the owned
        subprocess boundary. Return None or raise assertion failures. Invalid
        nonboolean settings must raise ValueError before launch or record writes.
        """
        for hidden in (True, False):
            with self.subTest(hidden=hidden):
                self.config = replace(self.config, hide_sr_window=hidden, hide_trim_window=not hidden)
                created = create_directories(self.paths, self.config, [CreateRequest("srim")]).completed[0]
                inputs = ResolvedSRInputs(self.request, {}, self.request, {"schema_version": 1, "parameters": self.request["parameters"], "sources": self.pinned_sources, "resources": [self.electronic, self.nuclear], "cab": self.request["parameters"]["correction"]}, (), tuple({**self.pinned_engine, "SCOEF03.dat": _read_selected_resource(self.paths, self.electronic, "electronic", "SCOEF03.dat"), "SNUC03.dat": _read_selected_resource(self.paths, self.nuclear, "nuclear", "SNUC03.dat")}.items()))
                runtime = _prepare_sr_runtime(self.paths, self.config, created, self.worker, inputs)
                before = {path.relative_to(created.path): path.read_bytes() for path in created.path.rglob("*") if path.is_file()}
                for invalid in (0, 1, "true", None):
                    with patch("src.srim.execution.subprocess.run") as launched, self.assertRaisesRegex(ValueError, "hide_sr_window"):
                        run_sr_simulation(self.paths, replace(self.config, hide_sr_window=invalid), created.path.name, runtime)
                    launched.assert_not_called()
                    self.assertEqual({path.relative_to(created.path): path.read_bytes() for path in created.path.rglob("*") if path.is_file()}, before)
                with patch("src.srim.execution.subprocess.run", side_effect=self._fake_sr_run) as launched:
                    result = run_sr_simulation(self.paths, self.config, created.path.name, runtime)
                self.assertEqual(result["status"], "succeeded")
                self.assertIs(launched.call_args.kwargs["startupinfo"] is None, not hidden)
                self.assertEqual((created.path / "output" / "raw" / "stopping.txt").read_bytes(), self.engine_output)

    def test_run_sr_preflight_failure(self: TestSRRuntime) -> None:
        """Check self's preflight corruption and stale runtime output fail before launch with created-state timestamps; return None or assertions using mocks."""
        for name in ("SRModule.exe", "SR.IN", "VERSION", "SCOEF03.dat", "SNUC03.dat", "stopping.txt"):
            created = create_directories(self.paths, self.config, [CreateRequest("srim")]).completed[0]
            runtime = _prepare_sr_runtime(self.paths, self.config, created, self.worker, ResolvedSRInputs((self.request), {}, (self.request), {"schema_version": 1, "parameters": (self.request).get("parameters", {}), "sources": self.pinned_sources, "resources": [self.electronic, self.nuclear], "cab": (self.request).get("parameters", {}).get("correction")}, (), tuple({**self.pinned_engine, "SCOEF03.dat": _read_selected_resource(self.paths, self.electronic, "electronic", "SCOEF03.dat"), "SNUC03.dat": _read_selected_resource(self.paths, self.nuclear, "nuclear", "SNUC03.dat")}.items())))
            (runtime / name).write_bytes(b"changed or stale")
            with patch("src.srim.execution.subprocess.run") as launched:
                result = run_sr_simulation(self.paths, self.config, created.path.name, runtime)
                launched.assert_not_called()
            self.assertEqual(result["status"], "failed")
            self.assertIsNone(result["started_at"])
            self.assertIsNotNone(result["finished_at"])
            self.assertTrue(result["failure"]["message"].startswith("preflight:"))

    def test_run_sr_process_failures(self: TestSRRuntime) -> None:
        """Check self's launch, timeout, and nonzero exit cases preserve diagnostics/output and failed metadata; return None or assertions using configured mocks."""
        for stage in ("launch", "timeout", "exit"):
            created = create_directories(self.paths, self.config, [CreateRequest("srim")]).completed[0]
            runtime = _prepare_sr_runtime(self.paths, self.config, created, self.worker, ResolvedSRInputs((self.request), {}, (self.request), {"schema_version": 1, "parameters": (self.request).get("parameters", {}), "sources": self.pinned_sources, "resources": [self.electronic, self.nuclear], "cab": (self.request).get("parameters", {}).get("correction")}, (), tuple({**self.pinned_engine, "SCOEF03.dat": _read_selected_resource(self.paths, self.electronic, "electronic", "SCOEF03.dat"), "SNUC03.dat": _read_selected_resource(self.paths, self.nuclear, "nuclear", "SNUC03.dat")}.items())))
            self.process_error = subprocess.TimeoutExpired("SRModule.exe", 0.25) if stage == "timeout" else None
            self.process_returncode = 7 if stage == "exit" else 0
            self.mutate_runtime = stage == "exit"
            effect = OSError("cannot launch") if stage == "launch" else self._fake_sr_run
            with patch("src.srim.execution.subprocess.run", side_effect=effect):
                result = run_sr_simulation(self.paths, self.config, created.path.name, runtime, timeout_seconds=0.25)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["failure"]["exit_code"], 7 if stage == "exit" else None)
            diagnostic = json.loads((created.path / "output" / "raw" / "execution.json").read_bytes())
            self.assertEqual(diagnostic["stage"], stage)
            self.assertEqual(diagnostic["timed_out"], stage == "timeout")
            if stage == "exit":
                self.assertIn("Runtime files changed", diagnostic["message"])
            if stage != "launch":
                self.assertEqual((created.path / "output" / "raw" / "stopping.txt").read_bytes(), self.engine_output)
            self.assertFalse((created.path / "output" / "parsed" / "result.json").exists())

    def test_run_sr_output_failures(self: TestSRRuntime) -> None:
        """Check self's missing, malformed, changed-runtime and changed-sealed-input outcomes retain evidence and fail; return None or assertions using mocks."""
        for stage in ("missing", "parse", "integrity", "sealed"):
            created = create_directories(self.paths, self.config, [CreateRequest("srim")]).completed[0]
            runtime = _prepare_sr_runtime(self.paths, self.config, created, self.worker, ResolvedSRInputs((self.request), {}, (self.request), {"schema_version": 1, "parameters": (self.request).get("parameters", {}), "sources": self.pinned_sources, "resources": [self.electronic, self.nuclear], "cab": (self.request).get("parameters", {}).get("correction")}, (), tuple({**self.pinned_engine, "SCOEF03.dat": _read_selected_resource(self.paths, self.electronic, "electronic", "SCOEF03.dat"), "SNUC03.dat": _read_selected_resource(self.paths, self.nuclear, "nuclear", "SNUC03.dat")}.items())))
            self.produce_output = stage != "missing"
            self.engine_output = b"partial malformed output" if stage == "parse" else (Path(__file__).parent / "fixtures" / "sr-range.txt").read_bytes()
            self.mutate_runtime = stage == "integrity"
            self.mutate_input = stage == "sealed"
            with patch("src.srim.execution.subprocess.run", side_effect=self._fake_sr_run):
                result = run_sr_simulation(self.paths, self.config, created.path.name, runtime)
            self.assertEqual(result["status"], "failed")
            self.assertEqual((runtime / "stopping.txt").exists(), stage != "missing")
            self.assertEqual((created.path / "output" / "raw" / "stopping.txt").exists(), stage != "missing")
            diagnostic = json.loads((created.path / "output" / "raw" / "execution.json").read_bytes())
            self.assertEqual(diagnostic["stage"], {"missing": "output", "sealed": "integrity"}.get(stage, stage))

    def test_run_sr_preservation_failures(self: TestSRRuntime) -> None:
        """Check self's copy/JSON/finalization failures preserve evidence, report secondary errors, and avoid false completion; return None or assertions using mocks."""
        for operation in ("copy", "json", "finish"):
            created = create_directories(self.paths, self.config, [CreateRequest("srim")]).completed[0]
            runtime = _prepare_sr_runtime(self.paths, self.config, created, self.worker, ResolvedSRInputs((self.request), {}, (self.request), {"schema_version": 1, "parameters": (self.request).get("parameters", {}), "sources": self.pinned_sources, "resources": [self.electronic, self.nuclear], "cab": (self.request).get("parameters", {}).get("correction")}, (), tuple({**self.pinned_engine, "SCOEF03.dat": _read_selected_resource(self.paths, self.electronic, "electronic", "SCOEF03.dat"), "SNUC03.dat": _read_selected_resource(self.paths, self.nuclear, "nuclear", "SNUC03.dat")}.items())))
            target = {"copy": "src.srim.execution._write_input_file", "json": "src.srim.execution._write_json", "finish": "src.srim.execution.finish_simulation"}[operation]
            with patch("src.srim.execution.subprocess.run", side_effect=self._fake_sr_run), patch(target, side_effect=PermissionError("publication blocked")), self.assertRaises(PermissionError) as caught:
                run_sr_simulation(self.paths, self.config, created.path.name, runtime)
            self.assertEqual((runtime / "stopping.txt").read_bytes(), self.engine_output)
            self.assertTrue(any(str(runtime) in note for note in caught.exception.__notes__))
            result = read_simulation(self.paths, created.path.name)
            self.assertEqual(result["status"], "running" if operation == "finish" else "failed")
            if operation == "finish":
                self.assertTrue(any("finalization" in note for note in caught.exception.__notes__))
            self.assertTrue((created.path / "output" / "raw" / "stdout.bin").exists())

    def test_run_sr_state_and_contention(self: TestSRRuntime) -> None:
        """Check self's invalid arguments/configuration, collisions, links, and locks leave created state without launching; return None or assertions using mocks."""
        runtime = _prepare_sr_runtime(self.paths, self.config, self.created, self.worker, ResolvedSRInputs((self.request), {}, (self.request), {"schema_version": 1, "parameters": (self.request).get("parameters", {}), "sources": self.pinned_sources, "resources": [self.electronic, self.nuclear], "cab": (self.request).get("parameters", {}).get("correction")}, (), tuple({**self.pinned_engine, "SCOEF03.dat": _read_selected_resource(self.paths, self.electronic, "electronic", "SCOEF03.dat"), "SNUC03.dat": _read_selected_resource(self.paths, self.nuclear, "nuclear", "SNUC03.dat")}.items())))
        for timeout in (0, -1, True, "60", None, float("nan"), float("inf"), 10 ** 1000):
            with self.subTest(timeout=repr(timeout)), self.assertRaises(ValueError):
                run_sr_simulation(self.paths, self.config, self.created.path.name, runtime, timeout_seconds=timeout)
        with self.assertRaises(ValueError):
            run_sr_simulation(self.paths, Config("invalid", self.config.srim_root), self.created.path.name, runtime)
        for wrong in (Path("relative"), runtime.parent / "wrong", self.paths.root / "other" / "sr" / self.created.path.name):
            with self.assertRaises(ValueError):
                run_sr_simulation(self.paths, self.config, self.created.path.name, wrong)
        for parent in (self.paths.workers, self.paths.simulation_memory, self.paths.resources):
            with _lock_parent(parent), self.assertRaises(OSError):
                run_sr_simulation(self.paths, self.config, self.created.path.name, runtime)
        blocker = self.created.path / "output" / "raw" / "stdout.bin"
        blocker.write_bytes(b"keep")
        with patch("src.srim.execution.subprocess.run") as launched, self.assertRaises(FileExistsError):
            run_sr_simulation(self.paths, self.config, self.created.path.name, runtime)
        launched.assert_not_called()
        self.assertEqual(blocker.read_bytes(), b"keep")
        self.assertEqual(read_simulation(self.paths, self.created.path.name)["status"], "created")

    def test_run_sr_interruption(self: TestSRRuntime) -> None:
        """Check self's interrupted child preserves output, records failed status, and reraises interruption; return None or assertions using mocks."""
        runtime = _prepare_sr_runtime(self.paths, self.config, self.created, self.worker, ResolvedSRInputs((self.request), {}, (self.request), {"schema_version": 1, "parameters": (self.request).get("parameters", {}), "sources": self.pinned_sources, "resources": [self.electronic, self.nuclear], "cab": (self.request).get("parameters", {}).get("correction")}, (), tuple({**self.pinned_engine, "SCOEF03.dat": _read_selected_resource(self.paths, self.electronic, "electronic", "SCOEF03.dat"), "SNUC03.dat": _read_selected_resource(self.paths, self.nuclear, "nuclear", "SNUC03.dat")}.items())))
        self.process_error = KeyboardInterrupt()
        with patch("src.srim.execution.subprocess.run", side_effect=self._fake_sr_run), self.assertRaises(KeyboardInterrupt):
            run_sr_simulation(self.paths, self.config, self.created.path.name, runtime)
        result = read_simulation(self.paths, self.created.path.name)
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["failure"]["message"].startswith("interrupted:"))
        self.assertEqual((self.created.path / "output" / "raw" / "stopping.txt").read_bytes(), self.engine_output)
