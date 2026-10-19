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
from src.srim.cab import _decode_payload, _normalize_entries, create_cab_variant, load_cab, register_cab_default


class TestCAB(unittest.TestCase):
    """Use unittest construction and temporary fixtures for independent CAB datasets."""

    def test_default_registration(self: TestCAB) -> None:
        """Verify self's normalized baselines and immutable conflicts; return None and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths = get_project_paths(Path(directory).absolute())
            create_base_directories(paths)
            config = Config("UTC", paths.root)
            for kind, source, equivalent, changed in (("core", {"C": 12.5, "H": 0}, {1: 0.0, 6: 12.5}, {6: 13}), ("bond", {"C-H": 10, "C=C": 20}, {(6, 6, "="): 20.0, (1, 6, "-"): 10.0}, {"H-C": 11})):
                reference = register_cab_default(paths, config, kind, source)
                payload = verify_resource(paths, reference)
                manifest = (payload.parent / "manifest.json").read_bytes()
                self.assertEqual(register_cab_default(paths, config, kind, equivalent), reference)
                self.assertEqual(load_cab(paths, kind, "default"), equivalent)
                self.assertEqual((payload.parent / "manifest.json").read_bytes(), manifest)
                with self.assertRaises(ValueError):
                    register_cab_default(paths, config, kind, changed)
                self.assertTrue(payload.read_bytes().endswith(b"\n"))

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
                self.assertEqual(json.loads(verify_resource(paths, reference).read_bytes()), {"schema_version": 1, "entries": []})

    def test_normalization_and_duplicates(self: TestCAB) -> None:
        """Verify self's canonical keys, deterministic ordering, and duplicate rejection; return None without persistent writes."""
        self.assertEqual(_normalize_entries("core", {"H": 0, "C": 12, "Og": 3}), {1: 0.0, 6: 12.0, 118: 3.0})
        self.assertEqual(_normalize_entries("bond", {"Cl#C": 4, ("O", 1, "="): 5}), {(6, 17, "#"): 4.0, (1, 8, "="): 5.0})
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
            for kind, baseline, updates, expected in (("core", {1: 0, 6: 1}, {"C": 2, "O": 3}, {1: 0.0, 6: 2.0, 8: 3.0}), ("bond", {"H-C": 1, "C=C": 2}, {(6, 1, "-"): 3, "Cl#C": 4}, {(1, 6, "-"): 3.0, (6, 6, "="): 2.0, (6, 17, "#"): 4.0})):
                register_cab_default(paths, config, kind, baseline)
                first = create_cab_variant(paths, config, kind, base_version="default", updates=updates)
                second = create_cab_variant(paths, config, kind, base_version="v1", updates=updates)
                self.assertEqual((first.version, second.version), ("v1", "v2"))
                self.assertEqual(first.sha256, second.sha256)
                self.assertEqual(load_cab(paths, kind, "v2"), expected)
                self.assertEqual(load_cab(paths, kind, "default"), _normalize_entries(kind, baseline))
                duplicates = {"C": 0, 6: 1} if kind == "core" else {"H-C": 0, "C-H": 1}
                with self.assertRaises(ValueError):
                    create_cab_variant(paths, config, kind, base_version="v2", updates=duplicates)

    def test_invalid_inputs(self: TestCAB) -> None:
        """Verify self's invalid schemas, keys, strengths, and input modes; return None and clean fixtures."""
        for kind, mapping in (("other", {}), ("core", {True: 0}), ("core", {0: 0}), ("core", {119: 0}), ("core", {"c": 0}), ("core", {"6": 0}), ("bond", {"H~C": 0}), ("bond", {("H", "C", "x"): 0}), ("bond", {("H",): 0}), ("bond", {("Xx", "C", "-"): 0})):
            with self.assertRaises(ValueError):
                _normalize_entries(kind, mapping)
        for value in (-1, float("nan"), float("inf"), True, "1", None, 10 ** 1000):
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
            for options in ({}, {"source": {}, "base_version": "default"}, {"source": {}, "updates": {1: 0}}, {"base_version": "default", "updates": {}}, {"updates": {1: 0}}, {"source": 123}, {"source": "\ud800"}):
                with self.assertRaises(ValueError):
                    create_cab_variant(paths, config, "core", **options)

    def test_load_integrity_and_independence(self: TestCAB) -> None:
        """Verify self's fresh dictionaries, unchecked endpoints, and integrity errors; return None and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths = get_project_paths(Path(directory).absolute())
            create_base_directories(paths)
            config = Config("UTC", paths.root)
            register_cab_default(paths, config, "core", {})
            reference = create_cab_variant(paths, config, "bond", source={"Cl#C": 1})
            loaded = load_cab(paths, "bond", "v1")
            loaded.clear()
            self.assertEqual(load_cab(paths, "bond", "v1"), {(6, 17, "#"): 1.0})
            payload = verify_resource(paths, reference)
            original = payload.read_bytes()
            payload.write_bytes(b"corrupt")
            with self.assertRaises(ValueError):
                load_cab(paths, "bond", "v1")
            payload.write_bytes(original)
            with patch("src.srim.cab.verify_resource", return_value=payload), patch.object(Path, "read_bytes", return_value=b"changed"):
                with self.assertRaises(ValueError):
                    load_cab(paths, "bond", "v1")
            register_resource(paths, config, "cab", "core", "invalid", b"invalid JSON")
            with self.assertRaises(ValueError):
                load_cab(paths, "core", "invalid")

    def test_files_contention_and_publication(self: TestCAB) -> None:
        """Verify self's missing sources/setup, links, contention, and retry; return None and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths = get_project_paths(Path(directory).absolute())
            config = Config("UTC", paths.root)
            with self.assertRaises(FileNotFoundError):
                register_cab_default(paths, config, "core", {})
            create_base_directories(paths)
            with self.assertRaises(FileNotFoundError):
                register_cab_default(paths, config, "core", paths.root / "missing")
            source = paths.root / "source.json"
            source.write_bytes(b'{"schema_version":1,"entries":[]}')
            for method in ("is_symlink", "is_junction"):
                with patch.object(Path, method, return_value=True):
                    with self.assertRaises(OSError):
                        register_cab_default(paths, config, "core", source)
            with patch.object(Path, "read_bytes", return_value=b"changed"):
                with self.assertRaises(ValueError):
                    register_cab_default(paths, config, "core", source)
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
