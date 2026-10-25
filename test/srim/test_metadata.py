"""Verify simulation provenance, CAB overrides, shared references, input integrity, and lifecycle persistence."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4
from unittest.mock import patch

from src.backend.config import Config
from src.backend.paths import create_base_directories, get_project_paths
from src.backend.records import CreateRequest, DeleteRequest, _lock_parent, _lock_worker, create_directories, delete_directories
from src.backend.resources import register_resource, verify_resource
from src.srim.common.records import _validate_document, begin_execution_evidence, check_run_closeout, read_execution_evidence, record_execution_stopped, finish_simulation, initialize_simulation, read_simulation, start_simulation


class TestMetadata(unittest.TestCase):
    """Use unittest lifecycle to isolate simulation fixtures beneath this module and remove them after each test."""

    def test_index_notification_after_unlock(self: TestMetadata) -> None:
        """Observe durable terminal state outside the parent lease; return None."""
        initialize_simulation(self.paths, self.created, self.engine, self.request, self.resolved)

        def notified(paths: object, metadata: dict[str, object]) -> None:
            """Check paths and metadata after finalization; return None, no mutations."""
            with _lock_parent(self.paths.simulation_memory):
                self.assertEqual(metadata["status"], "failed")
            self.assertEqual(metadata, read_simulation(self.paths, self.created.path.name))

        with patch("src.knowledge.index.notify_simulation_finished", side_effect=notified) as notify:
            result = finish_simulation(self.paths, self.config, self.created.path.name, {"message": "controlled", "exit_code": 1})
        notify.assert_called_once_with(self.paths, result)

    def test_index_notification_failure_is_independent(self: TestMetadata) -> None:
        """Do not reclassify published terminal state on notification errors; return None."""
        initialize_simulation(self.paths, self.created, self.engine, self.request, self.resolved)
        with patch("src.knowledge.index.notify_simulation_finished", side_effect=OSError("index disk")), self.assertLogs("src.srim.common.records", level="ERROR"):
            result = finish_simulation(self.paths, self.config, self.created.path.name, {"message": "native failure", "exit_code": 7})
        self.assertEqual(result["failure"], {"message": "native failure", "exit_code": 7})
        self.assertEqual(read_simulation(self.paths, self.created.path.name), result)

    def test_trim_dispatch_and_sealing(self: TestMetadata) -> None:
        """Seal all new TRIM evidence before start and reject later mutations; return None."""
        from _fixtures import make_test_project
        from test_trim_inputs import TRIM_INSTALLATION_FILES, TRIM_REQUEST, TRIM_SELECTIONS
        from test_trim_runtime import CACHE
        from src.srim.trim.defaults import switch_trim_defaults
        from src.srim.trim.resolution import resolve_trim_inputs
        from src.srim.trim.runtime import prepare_trim_simulation
        with TemporaryDirectory(dir=CACHE) as td:
            paths, config, worker = make_test_project(Path(td))
            for name, payload in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(payload)
            switch_trim_defaults(paths, config)
            inputs = resolve_trim_inputs(paths, config, worker, TRIM_REQUEST, TRIM_SELECTIONS)
            _validate_document(inputs.request, "request")
            _validate_document(inputs.resolved, "resolved")
            created = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            prepare_trim_simulation(paths, config, created, worker, inputs)
            control = created.path / "input/execution/control.json"
            control.write_bytes(control.read_bytes() + b" ")
            with self.assertRaises(ValueError):
                start_simulation(paths, config, created.path.name)
            self.assertEqual(read_simulation(paths, created.path.name)["status"], "created")

    def setUp(self: TestMetadata) -> None:
        """Create self's temporary project, source resource, engine identity, and request documents; return None or propagate setup errors."""
        self.temporary = TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temporary.cleanup)
        self.paths = get_project_paths(Path(self.temporary.name).absolute())
        create_base_directories(self.paths)
        self.config = Config("UTC", self.paths.root)
        self.source = self.paths.root / "custom-coefficients.dat"
        self.source.write_bytes(b"coefficients")
        self.reference = register_resource(self.paths, self.config, "engine", "srim-coefficients", "v1", self.source)
        self.engine = {"name": "sr", "version": "2013", "executable": {"name": "SRModule.exe", "sha256": "a" * 64}}
        self.request = {"schema_version": 1, "parameters": {"energy_keV": "125"}}
        self.resolved = {"schema_version": 1, "parameters": {"energy_keV": "125"}, "sources": [], "resources": [{"reference": asdict(self.reference), "source": {"description": "Custom coefficients", "version": "2013", "citation": None}, "runtime_filename": "SCOEF03.dat"}], "cab": None}
        self.created = create_directories(self.paths, self.config, [CreateRequest("srim", datetime(2026, 10, 18, microsecond=123456, tzinfo=timezone.utc))]).completed[0]

    def test_initialize_read_and_independent_snapshots(self: TestMetadata) -> None:
        """Check self's initialized layout, provenance, independent dictionaries, and unchanged resource snapshots; return None and use fixture cleanup."""
        metadata = initialize_simulation(self.paths, self.created, self.engine, self.request, self.resolved)
        self.assertEqual(metadata["status"], "created")
        self.assertEqual(metadata["created_at"], self.created.timestamp.isoformat())
        self.assertEqual(metadata["resources"], [asdict(self.reference)])
        self.assertNotIn("subcategory", metadata["resources"][0])
        self.assertNotIn("worker", metadata)
        self.assertFalse((self.created.path / "input" / "resources").exists())
        self.assertEqual([item["path"] for item in metadata["inputs"]], ["input/request.json", "input/resolved.json"])
        metadata["engine"]["version"] = "mutated"
        self.engine["version"] = "also-mutated"
        self.assertEqual(read_simulation(self.paths, self.created.path.name)["engine"]["version"], "2013")
        self.source.write_bytes(b"database updated")
        self.assertEqual(verify_resource(self.paths, self.reference).read_bytes(), b"coefficients")
        payload = verify_resource(self.paths, self.reference)
        payload.unlink()
        self.assertEqual(read_simulation(self.paths, self.created.path.name)["status"], "created")
        self.assertTrue((self.created.path / "metadata.json").read_bytes().endswith(b"\n"))

    def test_success_and_terminal_immutability(self: TestMetadata) -> None:
        """Check self's start/seal/output manifests and terminal transition rejection; return None and use fixture cleanup."""
        initialize_simulation(self.paths, self.created, self.engine, self.request, self.resolved)
        engine_input = self.created.path / "input" / "engine" / "SR.IN"
        engine_input.write_bytes(b"input")
        started = start_simulation(self.paths, Config("UTC+02:30", self.paths.root), self.created.path.name)
        self.assertEqual(started["status"], "running")
        self.assertTrue(started["started_at"].endswith("+02:30"))
        output = self.created.path / "output" / "raw" / "result.txt"
        output.write_bytes(b"result")
        finished = finish_simulation(self.paths, self.config, self.created.path.name)
        self.assertEqual(finished["status"], "succeeded")
        self.assertEqual(finished["outputs"], [{"path": "output/raw/result.txt", "size_bytes": 6, "sha256": hashlib.sha256(b"result").hexdigest()}])
        for operation in (start_simulation, finish_simulation):
            with self.assertRaises(ValueError):
                operation(self.paths, self.config, self.created.path.name)
        with self.assertRaises(ValueError):
            initialize_simulation(self.paths, self.created, self.engine, self.request, self.resolved)

    def test_failure_before_and_after_start(self: TestMetadata) -> None:
        """Check self's failures preserve preparation/execution evidence and optional exit codes; return None and use fixture cleanup."""
        initialize_simulation(self.paths, self.created, self.engine, self.request, self.resolved)
        with self.assertRaises(ValueError):
            finish_simulation(self.paths, self.config, self.created.path.name)
        failure = {"message": "input preparation failed", "exit_code": None}
        failed = finish_simulation(self.paths, self.config, self.created.path.name, failure)
        self.assertEqual(failed["status"], "failed")
        self.assertIsNone(failed["started_at"])
        self.assertEqual(failed["failure"], failure)
        second = create_directories(self.paths, self.config, [CreateRequest("srim")]).completed[0]
        initialize_simulation(self.paths, second, self.engine, self.request, self.resolved)
        (second.path / "input" / "engine" / "SR.IN").write_bytes(b"input")
        start_simulation(self.paths, self.config, second.path.name)
        (second.path / "input" / "engine" / "SR.IN").write_bytes(b"damaged")
        failed = finish_simulation(self.paths, self.config, second.path.name, {"message": "engine failed", "exit_code": 3})
        self.assertEqual(failed["failure"]["exit_code"], 3)

    def test_input_integrity_and_resource_verification(self: TestMetadata) -> None:
        """Check self's missing engine input, modified snapshots, corrupt resources, and sealed-input changes block success; return None and use fixture cleanup."""
        initialize_simulation(self.paths, self.created, self.engine, self.request, self.resolved)
        with self.assertRaises(ValueError):
            start_simulation(self.paths, self.config, self.created.path.name)
        input_file = self.created.path / "input" / "engine" / "SR.IN"
        input_file.write_bytes(b"input")
        request_path = self.created.path / "input" / "request.json"
        original = request_path.read_bytes()
        request_path.write_text(json.dumps(self.request | {"parameters": {"changed": True}}))
        with self.assertRaises(ValueError):
            start_simulation(self.paths, self.config, self.created.path.name)
        request_path.write_bytes(original)
        payload = verify_resource(self.paths, self.reference)
        payload.write_bytes(b"corrupt")
        with self.assertRaises(ValueError):
            start_simulation(self.paths, self.config, self.created.path.name)
        payload.write_bytes(b"coefficients")
        start_simulation(self.paths, self.config, self.created.path.name)
        input_file.write_bytes(b"changed")
        with self.assertRaises(ValueError):
            finish_simulation(self.paths, self.config, self.created.path.name)
        self.assertEqual(read_simulation(self.paths, self.created.path.name)["status"], "running")

    def test_cab_shared_datasets_and_overrides(self: TestMetadata) -> None:
        """Check self's independent CAB references, precise overrides, no contribution copies, and decimal/schema rejection; return None and use fixture cleanup."""
        core = register_resource(self.paths, self.config, "cab", "core", "v1", self.source)
        bond = register_resource(self.paths, self.config, "cab", "bond", "v2", self.source)
        cab = {"mode": "derived", "core_dataset": asdict(core), "bond_dataset": asdict(bond), "core_entries": ["oxygen"], "bond_entries": ["hydrogen-oxygen"], "composition": [{"atomic_number": 1, "atomic_mass": "1.008", "atom_count": "2"}, {"atomic_number": 8, "atomic_mass": "15.999", "atom_count": "1"}], "bonds": [{"identifier": "hydrogen-oxygen", "count": "2"}], "overrides": [{"dataset": "core", "entry_id": "oxygen", "value": "5.360000", "units": "reference-stopping-units", "reason": "user parameter set"}], "reference": {"projectile": 1, "mass_amu": "1.008", "energy_keV": "125", "phase": "solid", "contribution_units": "reference-stopping-units", "normalization": "per formula unit"}, "calculation": {"method": "cab-ratio", "version": "v1", "intermediate_totals": {"cab": "25.53", "bragg": "24.94"}, "calculated_factor": "1.023656776"}, "applied_factor": "1.02"}
        resolved = self.resolved | {"cab": cab}
        metadata = initialize_simulation(self.paths, self.created, self.engine, self.request, resolved)
        self.assertEqual(metadata["resources"], [asdict(self.reference), asdict(core), asdict(bond)])
        saved = json.loads((self.created.path / "input" / "resolved.json").read_text())
        self.assertEqual(saved["cab"]["overrides"][0]["value"], "5.360000")
        self.assertNotIn("core_contributions", saved["cab"])
        self.assertNotIn("bond_contributions", saved["cab"])
        for invalid in ("NaN", "Infinity", "-1", 1.02, "0"):
            with self.assertRaises(ValueError):
                _validate_document(cab | {"applied_factor": invalid}, "cab")
        invalid = deepcopy(cab)
        invalid["overrides"][0]["entry_id"] = "unselected"
        with self.assertRaises(ValueError):
            _validate_document(invalid, "cab")
        with self.assertRaises(ValueError):
            _validate_document(cab | {"core_dataset": asdict(bond)}, "cab")
        with self.assertRaises(ValueError):
            _validate_document(cab | {"bond_dataset": asdict(core)}, "cab")
        with self.assertRaises(ValueError):
            _validate_document(cab | {"core_dataset": asdict(replace(core, category="engine"))}, "cab")
        _validate_document({"mode": "none", "applied_factor": "1.0"}, "cab")
        _validate_document({"mode": "supplied", "applied_factor": "1.05", "source": "measurement", "reason": "fit"}, "cab")

    def test_transformed_resource_provenance(self: TestMetadata) -> None:
        """Check self's custom resource references retain source transformation data without payload duplication; return None and use fixture cleanup."""
        self.source.write_bytes(b"truncated coefficients")
        transformed = register_resource(self.paths, self.config, "engine", "srim-coefficients", "v2", self.source)
        resolved = deepcopy(self.resolved)
        selection = resolved["resources"][0]
        selection["reference"] = asdict(transformed)
        selection["transformation"] = {"original": asdict(self.reference), "method": "truncate-columns", "version": "v1", "parameters": {"fields": 95}}
        metadata = initialize_simulation(self.paths, self.created, self.engine, self.request, resolved)
        self.assertEqual(metadata["resources"], [asdict(transformed), asdict(self.reference)])
        self.assertEqual(list((self.created.path / "input" / "engine").iterdir()), [])

    def test_continuation_new_record_and_deleted_parent(self: TestMetadata) -> None:
        """Check self's TRIM continuation uses a new record, verifies files, and survives parent deletion; return None and use fixture cleanup."""
        engine = self.engine | {"name": "trim"}
        child = create_directories(self.paths, self.config, [CreateRequest("srim", self.created.timestamp)]).completed[0]
        continuation = {"parent_id": self.created.path.name, "files": [{"path": "input/continuation/restore.dat", "size_bytes": 7, "sha256": hashlib.sha256(b"restore").hexdigest()}]}
        with self.assertRaises(ValueError):
            initialize_simulation(self.paths, child, self.engine, self.request, self.resolved, continuation)
        with self.assertRaises(ValueError):
            initialize_simulation(self.paths, child, engine, self.request, self.resolved, continuation | {"parent_id": child.path.name})
        initialize_simulation(self.paths, child, engine, self.request, self.resolved, continuation)
        (child.path / "input" / "engine" / "TRIM.IN").write_bytes(b"input")
        with self.assertRaises(ValueError):
            start_simulation(self.paths, self.config, child.path.name)
        (child.path / "input" / "continuation" / "restore.dat").write_bytes(b"restore")
        delete_directories(self.paths, self.config, [DeleteRequest("srim", self.created.path.name)])
        started = start_simulation(self.paths, self.config, child.path.name)
        self.assertEqual(started["continuation"], continuation)
        self.assertNotEqual(child.path, self.created.path)

    def test_schema_identity_and_path_validation(self: TestMetadata) -> None:
        """Check self's malformed JSON, traversal, nonfinite data, duplicate filenames, and invalid identities reject mutation; return None and use fixture cleanup."""
        for invalid in (self.request | {"parameters": {"nan": float("nan")}}, self.request | {"schema_version": True}, self.request | {"unknown": 1}):
            with self.assertRaises(ValueError):
                initialize_simulation(self.paths, self.created, self.engine, invalid, self.resolved)
        self.assertEqual(list(self.created.path.iterdir()), [])
        invalid = deepcopy(self.resolved)
        invalid["resources"].append(deepcopy(invalid["resources"][0]))
        with self.assertRaises(ValueError):
            initialize_simulation(self.paths, self.created, self.engine, self.request, invalid)
        for name in ("../outside.dat", "CON", "bad:name", "trailing."):
            invalid = deepcopy(self.resolved)
            invalid["resources"][0]["runtime_filename"] = name
            with self.assertRaises(ValueError):
                initialize_simulation(self.paths, self.created, self.engine, self.request, invalid)
        with self.assertRaises(ValueError):
            initialize_simulation(self.paths, replace(self.created, suffix=2), self.engine, self.request, self.resolved)
        initialize_simulation(self.paths, self.created, self.engine, self.request, self.resolved)
        for name in ("../outside", "srim_20260230_000000_1"):
            with self.assertRaises(ValueError):
                read_simulation(self.paths, name)
        metadata_path = self.created.path / "metadata.json"
        metadata = json.loads(metadata_path.read_text())
        metadata["id"] = "srim_20261018_000000_99"
        metadata_path.write_text(json.dumps(metadata))
        with self.assertRaises(ValueError):
            read_simulation(self.paths, self.created.path.name)
        metadata_path.write_text("{")
        with self.assertRaises(ValueError):
            read_simulation(self.paths, self.created.path.name)

    def test_atomic_failure_and_partial_initialization(self: TestMetadata) -> None:
        """Check self's failed initialization reports retained files and failed start preserves created metadata; return None and use fixture cleanup."""
        from src.srim.common.files import _write_json

        def write_document(path: Path, document: dict[str, object]) -> None:
            """Write document at path except resolved.json, which raises PermissionError; return None otherwise."""
            if path.name == "resolved.json":
                raise PermissionError("blocked")
            _write_json(path, document)

        with patch("src.srim.common.records._write_json", side_effect=write_document):
            with self.assertRaises(PermissionError) as caught:
                initialize_simulation(self.paths, self.created, self.engine, self.request, self.resolved)
        self.assertTrue((self.created.path / "input" / "request.json").exists())
        self.assertFalse((self.created.path / "metadata.json").exists())
        self.assertTrue(any("retained" in note for note in caught.exception.__notes__))
        fresh = create_directories(self.paths, self.config, [CreateRequest("srim")]).completed[0]
        initialize_simulation(self.paths, fresh, self.engine, self.request, self.resolved)
        (fresh.path / "input" / "engine" / "SR.IN").write_bytes(b"input")
        with patch("src.backend.resources.os.replace", side_effect=PermissionError("replace blocked")):
            with self.assertRaises(PermissionError) as caught:
                start_simulation(self.paths, self.config, fresh.path.name)
        self.assertTrue(any("Surviving temporary file" in note for note in caught.exception.__notes__))
        self.assertEqual(read_simulation(self.paths, fresh.path.name)["status"], "created")

    def test_prepared_engine_inputs(self: TestMetadata) -> None:
        """Check self's prepared bytes, safe names, inventories, and unpublished metadata on write failure; return None or assertions and use fixture cleanup."""
        for engine_inputs in (
            {"../outside": b"bad"}, {"CON": b"bad"}, {"bad:name": b"bad"},
            {"trailing.": b"bad"}, {"trailing ": b"bad"}, {"": b"bad"},
            {"bad\nname": b"bad"}, {"dir\\file": b"bad"},
            {"SR.IN": b"one", "sr.in": b"two"}, {"SR.IN": "not bytes"},
            {1: b"bad"}, [],
        ):
            with self.assertRaises(ValueError):
                initialize_simulation(self.paths, self.created, self.engine, self.request, self.resolved, engine_inputs=engine_inputs)
            self.assertEqual(list(self.created.path.iterdir()), [])
        engine_inputs = {"SR.IN": b"prepared\r\n", "VERSION": b"module version\x00\xff"}
        metadata = initialize_simulation(self.paths, self.created, self.engine, self.request, self.resolved, engine_inputs=engine_inputs)
        for name, contents in engine_inputs.items():
            self.assertEqual((self.created.path / "input" / "engine" / name).read_bytes(), contents)
            self.assertIn({"path": f"input/engine/{name}", "size_bytes": len(contents), "sha256": hashlib.sha256(contents).hexdigest()}, metadata["inputs"])
        engine_inputs["SR.IN"] = b"caller mutation"
        self.assertEqual((self.created.path / "input" / "engine" / "SR.IN").read_bytes(), b"prepared\r\n")
        fresh = create_directories(self.paths, self.config, [CreateRequest("srim")]).completed[0]
        with patch("src.srim.common.records._write_input_file", side_effect=PermissionError("engine write blocked")):
            with self.assertRaises(PermissionError) as caught:
                initialize_simulation(self.paths, fresh, self.engine, self.request, self.resolved, engine_inputs={"SR.IN": b"input"})
        self.assertTrue((fresh.path / "input" / "request.json").exists())
        self.assertFalse((fresh.path / "metadata.json").exists())
        self.assertTrue(any("retained" in note for note in caught.exception.__notes__))

    def test_links_and_lock_contention(self: TestMetadata) -> None:
        """Check self's simulated input links and record/resource lock contention preserve metadata; return None and use fixture cleanup."""
        initialize_simulation(self.paths, self.created, self.engine, self.request, self.resolved)
        engine_input = self.created.path / "input" / "engine" / "SR.IN"
        engine_input.write_bytes(b"input")
        for method in ("is_symlink", "is_junction"):

            def is_link(path: Path) -> bool:
                """Return whether path is the simulated input link; no side effects."""
                return path == engine_input

            with patch.object(Path, method, autospec=True, side_effect=is_link):
                with self.assertRaises(OSError):
                    start_simulation(self.paths, self.config, self.created.path.name)
        for parent in (self.paths.simulation_memory, self.paths.resources):
            with _lock_parent(parent):
                with self.assertRaises(OSError):
                    start_simulation(self.paths, self.config, self.created.path.name)
        self.assertEqual(read_simulation(self.paths, self.created.path.name)["status"], "created")

    def test_execution_launch_validation(self: TestMetadata) -> None:
        """Reject self's invalid launch identities/state/input drift without launching; return None."""
        initialize_simulation(self.paths, self.created, self.engine, self.request, self.resolved, engine_inputs={"SR.IN": b"input"})
        worker = create_directories(self.paths, self.config, [CreateRequest("worker")]).completed[0]
        runtime = worker.path / "sr" / self.created.path.name
        runtime.mkdir()
        (runtime / "SR.IN").write_bytes(b"input")
        for invalid in ("../worker_1", "worker_0", True):
            with self.subTest(worker=invalid), self.assertRaises(ValueError):
                begin_execution_evidence(self.paths, self.config, self.created.path.name, invalid)
        with _lock_worker(self.paths, worker.path):
            launch = begin_execution_evidence(self.paths, self.config, self.created.path.name, worker.path.name)
            self.assertEqual(read_simulation(self.paths, self.created.path.name)["status"], "created")
            self.assertEqual(launch["runtime_path"], runtime.relative_to(self.paths.root).as_posix())
            with self.assertRaises(FileExistsError):
                begin_execution_evidence(self.paths, self.config, self.created.path.name, worker.path.name)
            start_simulation(self.paths, self.config, self.created.path.name)
            with self.assertRaises(ValueError):
                begin_execution_evidence(self.paths, self.config, self.created.path.name, worker.path.name)
        self.assertFalse((self.created.path / "lifecycle/stopped.json").exists())

    def test_execution_stopped_idempotency(self: TestMetadata) -> None:
        """Retain self's exact stopped bytes when repeated semantic evidence matches; return None."""
        initialize_simulation(self.paths, self.created, self.engine, self.request, self.resolved, engine_inputs={"SR.IN": b"input"})
        worker = create_directories(self.paths, self.config, [CreateRequest("worker")]).completed[0]
        runtime = worker.path / "sr" / self.created.path.name
        runtime.mkdir()
        (runtime / "SR.IN").write_bytes(b"input")
        with _lock_worker(self.paths, worker.path):
            launch = begin_execution_evidence(self.paths, self.config, self.created.path.name, worker.path.name)
            start_simulation(self.paths, self.config, self.created.path.name)
            stopped = record_execution_stopped(self.paths, self.config, self.created.path.name, launch["execution_id"], reason="exit", exit_code=0)
            original = (self.created.path / "lifecycle/stopped.json").read_bytes()
            again = record_execution_stopped(self.paths, replace(self.config, timezone="UTC+02:30"), self.created.path.name, launch["execution_id"], reason="exit", exit_code=0)
        self.assertEqual(stopped, again)
        self.assertEqual((self.created.path / "lifecycle/stopped.json").read_bytes(), original)
        self.assertFalse(read_execution_evidence(self.paths, self.created.path.name)["issues"])

    def test_execution_stopped_conflict(self: TestMetadata) -> None:
        """Reject self's foreign UUID, invalid stop reason and conflicting stop result; return None."""
        initialize_simulation(self.paths, self.created, self.engine, self.request, self.resolved, engine_inputs={"SR.IN": b"input"})
        worker = create_directories(self.paths, self.config, [CreateRequest("worker")]).completed[0]
        runtime = worker.path / "sr" / self.created.path.name
        runtime.mkdir()
        (runtime / "SR.IN").write_bytes(b"input")
        with _lock_worker(self.paths, worker.path):
            launch = begin_execution_evidence(self.paths, self.config, self.created.path.name, worker.path.name)
            start_simulation(self.paths, self.config, self.created.path.name)
            for execution_id, reason, code in ((str(uuid4()), "exit", 0), (launch["execution_id"], "guess", 0), (launch["execution_id"], "exit", True)):
                with self.subTest(reason=reason, code=code), self.assertRaises(ValueError):
                    record_execution_stopped(self.paths, self.config, self.created.path.name, execution_id, reason=reason, exit_code=code)
            record_execution_stopped(self.paths, self.config, self.created.path.name, launch["execution_id"], reason="exit", exit_code=0)
            with self.assertRaisesRegex(ValueError, "Conflicting"):
                record_execution_stopped(self.paths, self.config, self.created.path.name, launch["execution_id"], reason="exit", exit_code=7)

    def test_execution_evidence_missing_and_changed(self: TestMetadata) -> None:
        """Expose self's legacy missing proof, mid-read change and input damage as issues; return None."""
        initialize_simulation(self.paths, self.created, self.engine, self.request, self.resolved, engine_inputs={"SR.IN": b"input"})
        worker = create_directories(self.paths, self.config, [CreateRequest("worker")]).completed[0]
        runtime = worker.path / "sr" / self.created.path.name
        runtime.mkdir()
        (runtime / "SR.IN").write_bytes(b"input")
        self.assertEqual(read_execution_evidence(self.paths, self.created.path.name)["issues"][0]["code"], "missing")
        with _lock_worker(self.paths, worker.path):
            launch = begin_execution_evidence(self.paths, self.config, self.created.path.name, worker.path.name)
            start_simulation(self.paths, self.config, self.created.path.name)
            record_execution_stopped(self.paths, self.config, self.created.path.name, launch["execution_id"], reason="timeout", exit_code=None)
        before = [(self.created.path / name).read_bytes() for name in ("metadata.json", "lifecycle/launch.json", "lifecycle/stopped.json")]
        with patch("src.srim.common.records._read_regular_bytes", side_effect=[*before, b"changed", *before[1:]]):
            changed = read_execution_evidence(self.paths, self.created.path.name)
        self.assertTrue(any(issue["code"] == "stale" for issue in changed["issues"]))
        (self.created.path / "input/engine/SR.IN").write_bytes(b"changed")
        self.assertTrue(read_execution_evidence(self.paths, self.created.path.name)["issues"])

    def test_closeout_classifications(self: TestMetadata) -> None:
        """Distinguish all of self's created, terminal, uncertain and proven-stopped runs; return None."""
        initialize_simulation(self.paths, self.created, self.engine, self.request, self.resolved, engine_inputs={"SR.IN": b"input"})
        worker = create_directories(self.paths, self.config, [CreateRequest("worker")]).completed[0]
        runtime = worker.path / "sr" / self.created.path.name
        runtime.mkdir()
        (runtime / "SR.IN").write_bytes(b"input")
        terminal, unstarted = create_directories(self.paths, self.config, [CreateRequest("srim"), CreateRequest("srim")]).completed
        for created in (terminal, unstarted):
            initialize_simulation(self.paths, created, self.engine, self.request, self.resolved)
        finish_simulation(self.paths, self.config, terminal.path.name, {"message": "preflight", "exit_code": None})
        identities = [self.created.path.name, terminal.path.name, unstarted.path.name]
        job = {"id": "job_" + str(uuid4()), "state": "interrupted", "simulation_ids": [self.created.path.name]}
        with _lock_worker(self.paths, worker.path):
            launch = begin_execution_evidence(self.paths, self.config, self.created.path.name, worker.path.name)
            start_simulation(self.paths, self.config, self.created.path.name)
            unknown = check_run_closeout(self.paths, identities)
            self.assertEqual(unknown["counts"]["running_unresolved"], 1)
            interrupted = check_run_closeout(self.paths, identities, job_states=[job])
            self.assertEqual(interrupted["counts"]["stale_unresolved"], 1)
            self.assertFalse(interrupted["complete"])
            record_execution_stopped(self.paths, self.config, self.created.path.name, launch["execution_id"], reason="exit", exit_code=0)
        proven = check_run_closeout(self.paths, identities, job_states=[job])
        self.assertEqual(proven["counts"]["stale_stopped"], 1)
        self.assertEqual(proven["counts"]["terminal"], 1)
        self.assertEqual(proven["counts"]["unstarted"], 1)
        self.assertEqual(len(proven["rows"]), 3)
        self.assertEqual([row["simulation_id"] for row in proven["rows"]], sorted(identities))

    def test_closeout_invalid_records(self: TestMetadata) -> None:
        """Include self's malformed/missing records and reject invalid closeout scope; return None."""
        initialize_simulation(self.paths, self.created, self.engine, self.request, self.resolved, engine_inputs={"SR.IN": b"input"})
        worker = create_directories(self.paths, self.config, [CreateRequest("worker")]).completed[0]
        runtime = worker.path / "sr" / self.created.path.name
        runtime.mkdir()
        (runtime / "SR.IN").write_bytes(b"input")
        for identities in ([], [self.created.path.name] * 2, "not-an-array", ["../escape"], [self.created.path.name] * 1001):
            with self.subTest(scope=type(identities).__name__), self.assertRaises(ValueError):
                check_run_closeout(self.paths, identities)
        (self.created.path / "metadata.json").write_bytes(b"broken")
        report = check_run_closeout(self.paths, [self.created.path.name, "srim_20261025_000000_999"])
        self.assertEqual(report["counts"]["invalid"], 2)
        self.assertFalse(report["complete"])
        self.assertEqual(len(report["issues"]), 2)
