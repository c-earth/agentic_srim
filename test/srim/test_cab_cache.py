"""Verify calculated-entry immutability, provenance integrity and publication conflicts."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from _fixtures import fake_bragg_run, make_test_project, make_test_request, make_test_selections
from src.backend.records import CreateRequest, _lock_parent, create_directories
from src.backend.resources import _hash_file, verify_resource
from src.srim.bragg import resolve_bragg
from src.srim.cab_cache import _dependency_key, find_cab_entry, load_cab_entry, publish_cab_entry
from src.srim.material import parse_atomdata
from src.srim.metadata import _inventory, _read_json, _write_json
from src.srim.outputs import parse_sr_output
from src.srim.resolution import _make_dependency_inputs, resolve_sr_inputs


class TestCABEntries(unittest.TestCase):
    """Use inherited unittest construction and real synthetic dependency records."""

    def setUp(self: TestCABEntries) -> None:
        """Create self's temporary project, pinned inputs and one successful calculated entry; return None and register cleanup."""
        self.temporary = TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temporary.cleanup)
        self.paths, self.config, self.worker = make_test_project(Path(self.temporary.name).absolute())
        request = make_test_request()
        request["parameters"]["target"]["bonds"] = {}
        bundle = resolve_sr_inputs(self.paths, self.config, self.worker, request, make_test_selections())
        self.inputs = _make_dependency_inputs(bundle.resolved["defaults"], bundle.resolved["resources"], parse_atomdata(dict(bundle.snapshots)["resources/atomdata/effective"]), dict(bundle.engine_files))
        with patch("src.srim.execution.subprocess.run", side_effect=fake_bragg_run):
            self.reference = resolve_bragg(self.paths, self.config, self.worker, self.inputs, 6).reference
        self.entry = load_cab_entry(self.paths, self.reference)

    def test_immutable_entry_publication(self: TestCABEntries) -> None:
        """Check self's later elemental additions preserve dataset and earlier exact entry bytes; return None."""
        payload = verify_resource(self.paths, self.reference.dataset)
        before = {"payload": payload.read_bytes(), "manifest": (payload.parent / "manifest.json").read_bytes()}
        path = payload.parent / "entries" / self.reference.dependency_key / "z006.json"
        entry_bytes = path.read_bytes()
        with patch("src.srim.execution.subprocess.run", side_effect=fake_bragg_run):
            other = resolve_bragg(self.paths, self.config, self.worker, self.inputs, 8)
        self.assertEqual(other.reference.dataset, self.reference.dataset)
        self.assertEqual(path.read_bytes(), entry_bytes)
        self.assertEqual(payload.read_bytes(), before["payload"])
        self.assertEqual((payload.parent / "manifest.json").read_bytes(), before["manifest"])

    def test_duplicate_and_conflict(self: TestCABEntries) -> None:
        """Check self's equal publication reuses identity and valid different result raises without overwrite; return None."""
        self.assertEqual(publish_cab_entry(self.paths, self.reference.dataset, deepcopy(self.entry)), self.reference)
        original_id = self.entry["simulations"][0]["simulation_id"]
        original = self.paths.simulation_memory / original_id
        created = create_directories(self.paths, self.config, [CreateRequest("srim")]).completed[0]
        shutil.copytree(original, created.path, dirs_exist_ok=True)
        raw_path = created.path / "output" / "raw" / "stopping.txt"
        raw_path.write_bytes(raw_path.read_bytes().replace(b"2.500E+01", b"2.600E+01"))
        effective = _read_json(created.path / "input" / "effective-request.json")
        result_path = created.path / "output" / "parsed" / "result.json"
        _write_json(result_path, parse_sr_output(raw_path.read_bytes(), effective))
        metadata = _read_json(created.path / "metadata.json")
        metadata["id"] = created.path.name
        metadata["outputs"] = _inventory(created.path / "output", created.path)
        _write_json(created.path / "metadata.json", metadata)
        changed = deepcopy(self.entry)
        changed["value"] = "2.600E+01"
        changed["simulations"][0]["simulation_id"] = created.path.name
        for role, path in (("raw", raw_path), ("result", result_path)):
            checksum, size = _hash_file(path)
            changed["simulations"][0][role].update({"id": created.path.name, "sha256": checksum, "size_bytes": size})
        with self.assertRaisesRegex(ValueError, "Conflicting calculated result"):
            publish_cab_entry(self.paths, self.reference.dataset, changed)
        self.assertEqual(load_cab_entry(self.paths, self.reference), self.entry)

    def test_tamper_and_missing_evidence(self: TestCABEntries) -> None:
        """Check self's entry/evidence tampering raises rather than becoming a cache miss; return None."""
        raw = self.entry["simulations"][0]["raw"]
        path = self.paths.simulation_memory / raw["id"] / raw["path"]
        original = path.read_bytes()
        path.write_bytes(original + b"tampered")
        with self.assertRaises(ValueError):
            find_cab_entry(self.paths, self.reference.dataset, self.entry["dependency"], "z006")
        path.write_bytes(original)
        path.unlink()
        with self.assertRaises(FileNotFoundError):
            load_cab_entry(self.paths, self.reference)

    def test_publication_lock(self: TestCABEntries) -> None:
        """Check self's held entry lock rejects publication and retains staged bytes; return None."""
        directory = verify_resource(self.paths, self.reference.dataset).parent
        with _lock_parent(directory):
            with self.assertRaises(OSError) as caught:
                publish_cab_entry(self.paths, self.reference.dataset, self.entry)
        self.assertTrue(any("retained artifacts" in note for note in caught.exception.__notes__))
        self.assertTrue(list(directory.glob(".entry-*")))

    def test_dependency_recipe_identity(self: TestCABEntries) -> None:
        """Check self's effective-byte/procedure identity rejects paths, unknown fields and changed recipes; return None."""
        recipe = deepcopy(self.entry["dependency"])
        self.assertEqual(_dependency_key(recipe), self.reference.dependency_key)
        recipe["files"][0]["sha256"] = "f" * 64
        self.assertNotEqual(_dependency_key(recipe), self.reference.dependency_key)
        with self.assertRaises(ValueError):
            find_cab_entry(self.paths, self.reference.dataset, recipe, "z006")
        recipe["path"] = "unapproved"
        with self.assertRaises(ValueError):
            _dependency_key(recipe)
