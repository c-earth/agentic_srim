"""Verify canonical sparse CAB storage, normalization, updates, and failure behavior."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from src.backend.config import Config
from src.backend.paths import create_base_directories, get_project_paths
from src.backend.records import _lock_parent
from src.backend.resources import register_resource, resolve_resource, verify_resource
from src.srim.cab.strengths import _decode_payload, _normalize_entries, create_cab_variant, load_cab


class TestCAB(unittest.TestCase):
    """Use unittest construction and temporary fixtures for independent CAB datasets."""

    def test_default_registration(self: TestCAB) -> None:
        """Verify self's coordinated default references and convention without supplied-default APIs; return None."""
        from _fixtures import make_test_project
        from src.srim.resources.defaults import read_sr_defaults
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths, config, worker = make_test_project(Path(directory).absolute())
            defaults = read_sr_defaults(paths)
            self.assertEqual(load_cab(paths, "core", "default"), {1: "0"})
            self.assertEqual(load_cab(paths, "bond", "default"), {})
            self.assertEqual(defaults["resources"]["core"]["version"], "v1")

    def test_source_forms(self: TestCAB) -> None:
        """Verify self's JSON paths/strings/bytes and mappings, including empty data; return None and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths = get_project_paths(Path(directory).absolute())
            create_base_directories(paths)
            config = Config("UTC", paths.root)
            for kind, entry, mapping in (("core", {"element": 118, "value": 0}, {"Og": 0}), ("bond", {"elements": [17, 6], "bond": "#", "value": 2}, {"Cl#C": 2})):
                text = json.dumps({"schema_version": 1, "entries": [entry]})
                file = paths.root / f"{kind}.json"
                file.write_text(text, encoding="utf-8")
                for index, source in enumerate((file, text, text.encode("utf-8"), mapping), 1):
                    reference = create_cab_variant(paths, config, kind, source=source)
                    self.assertEqual(reference.version, f"v{index}")
                    self.assertEqual(load_cab(paths, kind, reference.version), _normalize_entries(kind, mapping))
                reference = create_cab_variant(paths, config, kind, source={})
                self.assertEqual(load_cab(paths, kind, reference.version), {})
                self.assertEqual(json.loads(verify_resource(paths, reference).read_bytes()), {"schema_version": 2, "kind": kind, "units": "eV/(1E15 atoms/cm2)", "entries": []})

    def test_normalization_and_duplicates(self: TestCAB) -> None:
        """Verify self's canonical keys, deterministic ordering, and duplicate rejection; return None without persistent writes."""
        self.assertEqual(_normalize_entries("core", {"H": 0, "C": 12, "Og": 3}), {1: "0", 6: "12", 118: "3"})
        self.assertEqual(_normalize_entries("bond", {"Cl#C": 4, ("O", 1, "="): 5}), {(6, 17, "#"): "4", (1, 8, "="): "5"})
        for kind, mapping in (("core", {"C": 1, 6: 1}), ("bond", {"C-H": 1, "H-C": 1}), ("bond", {(1, 6, "-"): 1, (6, 1, "-"): 1})):
            with self.assertRaises(ValueError):
                _normalize_entries(kind, mapping)
        for kind, raw in (("core", b'{"schema_version":1,"schema_version":1,"entries":[]}'), ("core", b'{"schema_version":1,"entries":[{"element":6,"element":6,"value":0}]}'), ("core", b'{"schema_version":1,"entries":[{"element":6,"value":0},{"element":6,"value":0}]}'), ("bond", b'{"schema_version":1,"entries":[{"elements":[1,6],"bond":"-","value":0},{"elements":[6,1],"bond":"-","value":0}]}')):
            with self.assertRaises(ValueError):
                _decode_payload(kind, raw)
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths = get_project_paths(Path(directory).absolute())
            create_base_directories(paths)
            config = Config("UTC", paths.root)
            reference = create_cab_variant(paths, config, "bond", source={"C=C": 3, "C-H": 2, "H=C": 1, "H#C": 0})
            entries = json.loads(verify_resource(paths, reference).read_bytes())["entries"]
            self.assertEqual([(entry["elements"], entry["bond"]) for entry in entries], [([1, 6], "#"), ([1, 6], "-"), ([1, 6], "="), ([6, 6], "=")])

    def test_variant_updates(self: TestCAB) -> None:
        """Verify self's additions/replacements, no-op updates, and independent sequences; return None and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths = get_project_paths(Path(directory).absolute())
            create_base_directories(paths)
            config = Config("UTC", paths.root)
            for kind, baseline, updates, expected in (("core", {1: 0, 6: 1}, {"C": 2, "O": 3}, {1: "0", 6: "2", 8: "3"}), ("bond", {"H-C": 1, "C=C": 2}, {(6, 1, "-"): 3, "Cl#C": 4}, {(1, 6, "-"): "3", (6, 6, "="): "2", (6, 17, "#"): "4"})):
                create_cab_variant(paths, config, kind, source=baseline)
                first = create_cab_variant(paths, config, kind, base_version="v1", overrides=[{"operation": "set", "key": key, "value": str(value)} for key, value in updates.items()])
                second = create_cab_variant(paths, config, kind, base_version="v2", overrides=[{"operation": "set", "key": key, "value": str(value)} for key, value in updates.items()])
                self.assertEqual((first.version, second.version), ("v2", "v3"))
                self.assertEqual(first.sha256, second.sha256)
                self.assertEqual(load_cab(paths, kind, "v3"), _normalize_entries(kind, expected))
                self.assertEqual(load_cab(paths, kind, "v1"), _normalize_entries(kind, baseline))
                duplicates = {"C": 0, 6: 1} if kind == "core" else {"H-C": 0, "C-H": 1}
                with self.assertRaises(ValueError):
                    create_cab_variant(paths, config, kind, base_version="v2", overrides=[{"operation": "set", "key": key, "value": str(value)} for key, value in duplicates.items()])

            removed = create_cab_variant(paths, config, "core", base_version="v1", overrides=[{"operation": "remove", "key": "C"}])
            self.assertEqual(load_cab(paths, "core", removed.version), {1: "0"})
            self.assertEqual(_decode_payload("core", b'{"schema_version":1,"entries":[{"element":6,"value":0.12345678901234567890123456789}]}'), {6: "0.12345678901234567890123456789"})

    def test_invalid_inputs(self: TestCAB) -> None:
        """Verify self's invalid schemas, keys, strengths, and input modes; return None and clean fixtures."""
        for kind, mapping in (("other", {}), ("core", {True: 0}), ("core", {0: 0}), ("core", {119: 0}), ("core", {"c": 0}), ("core", {"6": 0}), ("bond", {"H~C": 0}), ("bond", {("H", "C", "x"): 0}), ("bond", {("H",): 0}), ("bond", {("Xx", "C", "-"): 0})):
            with self.assertRaises(ValueError):
                _normalize_entries(kind, mapping)
        for value in (-1, float("nan"), float("inf"), True, " 1", "NaN", "Infinity", None):
            with self.assertRaises(ValueError):
                _normalize_entries("core", {1: value})
        for raw in (b"not json", b"\xff", b"[]", b"{}", b'{"schema_version":true,"entries":[]}', b'{"schema_version":2,"entries":[]}', b'{"schema_version":1,"entries":{}}', b'{"schema_version":1,"entries":[[]]}', b'{"schema_version":1,"entries":[{"element":"C","value":0}]}', b'{"schema_version":1,"entries":[{"element":1,"value":NaN}]}', b'{"schema_version":1,"entries":[],"extra":0}'):
            with self.assertRaises(ValueError):
                _decode_payload("core", raw)
        for entry in ({"elements": [1], "bond": "-", "value": 0}, {"elements": [True, 6], "bond": "-", "value": 0}, {"elements": [1, 6], "bond": [], "value": 0}, {"elements": [1, 6], "bond": "-", "value": -1}):
            with self.assertRaises(ValueError):
                _decode_payload("bond", json.dumps({"schema_version": 1, "entries": [entry]}).encode())
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths = get_project_paths(Path(directory).absolute())
            create_base_directories(paths)
            config = Config("UTC", paths.root)
            for options in ({}, {"source": {}, "base_version": "default"}, {"source": {}, "overrides": [{"operation": "remove", "key": 1}]}, {"base_version": "missing"}, {"overrides": []}, {"source": 123}, {"source": "\ud800"}):
                with self.assertRaises(ValueError):
                    create_cab_variant(paths, config, "core", **options)

    def test_load_integrity_and_independence(self: TestCAB) -> None:
        """Verify self's fresh dictionaries, unchecked endpoints, and integrity errors; return None and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths = get_project_paths(Path(directory).absolute())
            create_base_directories(paths)
            config = Config("UTC", paths.root)
            create_cab_variant(paths, config, "core", source={})
            reference = create_cab_variant(paths, config, "bond", source={"Cl#C": 1})
            loaded = load_cab(paths, "bond", "v1")
            loaded.clear()
            self.assertEqual(load_cab(paths, "bond", "v1"), {(6, 17, "#"): "1"})
            payload = verify_resource(paths, reference)
            original = payload.read_bytes()
            payload.write_bytes(b"corrupt")
            with self.assertRaises(ValueError):
                load_cab(paths, "bond", "v1")
            payload.write_bytes(original)
            with patch("src.srim.cab.strengths.verify_resource", return_value=payload), patch.object(Path, "read_bytes", return_value=b"changed"):
                with self.assertRaises(ValueError):
                    load_cab(paths, "bond", "v1")
            register_resource(paths, config, "cab", "core", "v2", b"invalid JSON")
            with self.assertRaises(ValueError):
                load_cab(paths, "core", "v2")

    def test_files_contention_and_publication(self: TestCAB) -> None:
        """Verify self's missing sources/setup, links, contention, and retry; return None and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths = get_project_paths(Path(directory).absolute())
            config = Config("UTC", paths.root)
            with self.assertRaises(FileNotFoundError):
                create_cab_variant(paths, config, "core", source={})
            create_base_directories(paths)
            with self.assertRaises(FileNotFoundError):
                create_cab_variant(paths, config, "core", source=paths.root / "missing")
            source = paths.root / "source.json"
            source.write_bytes(b'{"schema_version":1,"entries":[]}')
            for method in ("is_symlink", "is_junction"):
                with patch.object(Path, method, return_value=True):
                    with self.assertRaises(OSError):
                        create_cab_variant(paths, config, "core", source=source)
            with patch.object(Path, "read_bytes", return_value=b"changed"):
                with self.assertRaises(ValueError):
                    create_cab_variant(paths, config, "core", source=source)
            with _lock_parent(paths.resources):
                with self.assertRaises(OSError):
                    create_cab_variant(paths, config, "core", source={1: 0})
            with patch.object(Path, "rename", side_effect=PermissionError("blocked")):
                with self.assertRaises(PermissionError) as caught:
                    create_cab_variant(paths, config, "core", source={1: 0})
            self.assertTrue(any("retained artifacts" in note for note in caught.exception.__notes__))
            reference = create_cab_variant(paths, config, "core", source={1: 0})
            self.assertEqual(reference.version, "v1")
            self.assertEqual(resolve_resource(paths, "cab", "core", "v1"), reference)
