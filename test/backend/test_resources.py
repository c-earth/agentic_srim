"""Verify shared resource identity, immutable readable versions, publication, and corruption detection."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from src.backend.config import Config
from src.backend.paths import create_base_directories, get_project_paths
from src.backend.records import _lock_parent
from src.backend.resources import ResourceReference, _validate_identifier, register_resource, resolve_resource, verify_resource


class TestResources(unittest.TestCase):
    """Use unittest construction to test shared resources in automatically removed local fixtures."""

    def test_registration_versions_and_categories(self: TestResources) -> None:
        """Check self's readable versions, independent payloads, extensible hierarchy, and snapshots; return None and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths = get_project_paths(Path(directory).absolute())
            create_base_directories(paths)
            config = Config("UTC", paths.root)
            source = paths.root / "source.dat"
            source.write_bytes(b"original coefficients\r\n")
            first = register_resource(paths, config, "engine", "srim-coefficients", "v1", source)
            self.assertEqual(first.sha256, hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual(resolve_resource(paths, first.category, first.name, first.version), first)
            payload = verify_resource(paths, first)
            self.assertEqual(payload, paths.resources / "engine" / "srim-coefficients" / "v1" / "payload")
            manifest = payload.parent / "manifest.json"
            original_manifest = manifest.read_bytes()
            self.assertEqual(set(json.loads(original_manifest)), {"schema_version", "sha256", "size_bytes", "registered_at", "provenance", "reuse_key"})
            self.assertEqual(register_resource(paths, config, first.category, first.name, first.version, source), first)
            self.assertEqual(manifest.read_bytes(), original_manifest)
            alias_version = register_resource(paths, config, first.category, first.name, "v2", source)
            alias_payload = verify_resource(paths, alias_version)
            self.assertNotEqual(payload, alias_payload)
            self.assertEqual(payload.read_bytes(), alias_payload.read_bytes())
            self.assertEqual(first.sha256, alias_version.sha256)
            self.assertEqual(set(item.name for item in payload.parent.iterdir()), {"manifest.json", "payload"})
            self.assertEqual(set(item.name for item in payload.parent.parent.iterdir()), {"v1", "v2"})
            for category, name in (("cab", "core"), ("cab", "bond"), ("future-family", "dataset")):
                reference = register_resource(paths, config, category, name, "v1", source)
                self.assertEqual(verify_resource(paths, reference).read_bytes(), source.read_bytes())
            source.write_bytes(b"new coefficients")
            second = register_resource(paths, config, first.category, first.name, "v3", source)
            self.assertNotEqual(first.sha256, second.sha256)
            self.assertEqual(verify_resource(paths, first).read_bytes(), b"original coefficients\r\n")
            with self.assertRaises(ValueError):
                register_resource(paths, config, first.category, first.name, first.version, source)
            self.assertEqual(resolve_resource(paths, first.category, first.name, first.version), first)
            byte_reference = register_resource(paths, config, first.category, first.name, "v4", source.read_bytes())
            self.assertEqual(verify_resource(paths, byte_reference).read_bytes(), source.read_bytes())
            text_reference = register_resource(paths, config, "engine", "text-input", "v1", "raw\r\ntext\n")
            self.assertEqual(verify_resource(paths, text_reference).read_bytes(), b"raw\r\ntext\n")
            text_directory = paths.resources / "engine" / "text-input"
            before = set(text_directory.iterdir())
            with self.assertRaises(ValueError):
                register_resource(paths, config, "engine", "text-input", None, "non-ASCII \u03bb")
            self.assertEqual(set(text_directory.iterdir()), before)
            with self.assertRaises(ValueError):
                register_resource(paths, config, "engine", "text-input", None, 123)
            self.assertEqual(register_resource(paths, config, "engine", "text-input", None, "").version, "v2")
            for label in ("default", "dafault", "v01", "variant"):
                with self.assertRaises(ValueError):
                    register_resource(paths, config, "engine", "allocation", label, b"opaque")
            self.assertEqual(register_resource(paths, config, "engine", "allocation", None, b"opaque").version, "v1")
            recipe = hashlib.sha256(b"recipe").hexdigest()
            reusable = register_resource(paths, config, "cab", "bragg", None, b"descriptor", reuse_key=recipe)
            self.assertEqual(register_resource(paths, config, "cab", "bragg", None, b"descriptor", reuse_key=recipe), reusable)
            self.assertEqual(len(list((paths.resources / "cab" / "bragg").glob("v*"))), 1)
            with self.assertRaises(ValueError):
                register_resource(paths, config, "cab", "bragg", None, b"changed", reuse_key=recipe)
            with self.assertRaises(ValueError):
                register_resource(paths, config, "cab", "bragg", "v5", b"descriptor", reuse_key=recipe)
            legacy = json.loads(original_manifest)
            legacy["schema_version"] = 1
            del legacy["provenance"], legacy["reuse_key"]
            manifest.write_text(json.dumps(legacy), encoding="utf-8")
            self.assertEqual(resolve_resource(paths, first.category, first.name, first.version), first)
            manifest.write_bytes(original_manifest)
            for boundary in (9, 99, 100):
                register_resource(paths, config, "engine", "allocation", f"v{boundary}", b"opaque")
                self.assertEqual(register_resource(paths, config, "engine", "allocation", None, b"opaque").version, f"v{boundary + 1}")
            numeric_file = paths.resources / "engine" / "allocation" / "v200"
            numeric_file.write_bytes(b"blocked")
            with self.assertRaises(OSError):
                register_resource(paths, config, "engine", "allocation", None, b"opaque")
            for method in ("is_symlink", "is_junction"):
                entry = Mock(spec=Path)
                entry.name = "v1"
                entry.is_symlink.return_value = method == "is_symlink"
                entry.is_junction.return_value = method == "is_junction"
                entry.is_dir.return_value = True
                with patch.object(Path, "iterdir", return_value=iter([entry])):
                    with self.assertRaises(OSError):
                        register_resource(paths, config, "engine", "allocation", None, b"opaque")
            with self.assertRaises(FrozenInstanceError):
                first.version = "changed"

    def test_identifier_and_reference_validation(self: TestResources) -> None:
        """Check self's invalid labels/references fail safely; return None and clean fixtures."""
        for value in ("", "Uppercase", "../outside", "double--hyphen", "nul", "com1", "a" * 65, "-start", "end-", "a_b"):
            with self.assertRaises(ValueError):
                _validate_identifier(value)
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths = get_project_paths(Path(directory).absolute())
            create_base_directories(paths)
            source = paths.root / "source"
            source.write_bytes(b"")
            config = Config("UTC", paths.root)
            reference = register_resource(paths, config, "cab", "core", "v1", source)
            self.assertEqual(reference.size_bytes, 0)
            for invalid in (replace(reference, sha256="bad"), replace(reference, size_bytes=True), replace(reference, category="../escape"), replace(reference, size_bytes=-1)):
                with self.assertRaises(ValueError):
                    verify_resource(paths, invalid)
            with self.assertRaises(ValueError):
                resolve_resource(paths, "cab", "core", "missing")

    def test_corruption_and_reference_mismatch(self: TestResources) -> None:
        """Check self's same-size corruption, manifest tampering, and forged references are rejected; return None and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths = get_project_paths(Path(directory).absolute())
            create_base_directories(paths)
            source = paths.root / "source"
            source.write_bytes(b"abc")
            config = Config("UTC", paths.root)
            reference = register_resource(paths, config, "cab", "bond", "v1", source)
            payload = verify_resource(paths, reference)
            payload.write_bytes(b"xyz")
            with self.assertRaises(ValueError):
                verify_resource(paths, reference)
            with self.assertRaises(ValueError):
                register_resource(paths, config, "cab", "bond", "v1", source)
            second = register_resource(paths, config, "cab", "bond", "v2", source)
            self.assertEqual(verify_resource(paths, second).read_bytes(), b"abc")
            payload.write_bytes(b"abc")
            with self.assertRaises(ValueError):
                verify_resource(paths, replace(reference, size_bytes=4))
            manifest = payload.parent / "manifest.json"
            original = manifest.read_text()
            document = json.loads(original)
            document["schema_version"] = True
            manifest.write_text(json.dumps(document))
            with self.assertRaises(ValueError):
                resolve_resource(paths, "cab", "bond", "v1")
            manifest.write_text(original)
            for field, invalid in (("sha256", "../bad"), ("size_bytes", True), ("registered_at", "2026-10-19T00:00:00")):
                document = json.loads(original)
                document[field] = invalid
                manifest.write_text(json.dumps(document))
                with self.assertRaises(ValueError):
                    resolve_resource(paths, "cab", "bond", "v1")
            manifest.write_text(original)

    def test_publication_failure_and_retry(self: TestResources) -> None:
        """Check self's failed version publication retains complete staging for retry and reports artifacts; return None and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths = get_project_paths(Path(directory).absolute())
            create_base_directories(paths)
            source = paths.root / "source"
            source.write_bytes(b"resource")
            config = Config("UTC", paths.root)
            with patch.object(Path, "rename", side_effect=PermissionError("publication blocked")):
                with self.assertRaises(PermissionError) as caught:
                    register_resource(paths, config, "engine", "coefficients", "v1", source)
            name_directory = paths.resources / "engine" / "coefficients"
            staging = list(name_directory.glob(".staging-*"))
            self.assertEqual(len(staging), 1)
            self.assertEqual(set(item.name for item in staging[0].iterdir()), {"manifest.json", "payload"})
            self.assertFalse((name_directory / "v1").exists())
            self.assertTrue(any("retained artifacts" in note for note in caught.exception.__notes__))
            reference = register_resource(paths, config, "engine", "coefficients", "v1", source)
            self.assertEqual(verify_resource(paths, reference).read_bytes(), b"resource")

    def test_missing_setup_links_and_contention(self: TestResources) -> None:
        """Check self's missing setup, linked ancestors/payloads, and contention reject operations; return None and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths = get_project_paths(Path(directory).absolute())
            config = Config("UTC", paths.root)
            source = paths.root / "source"
            source.write_bytes(b"resource")
            with self.assertRaises(FileNotFoundError):
                register_resource(paths, config, "engine", "coefficients", "v1", source)
            create_base_directories(paths)
            reference = register_resource(paths, config, "engine", "coefficients", "v1", source)
            payload = verify_resource(paths, reference)
            for method in ("is_symlink", "is_junction"):
                for linked in (source, paths.resources / "engine", payload):

                    def is_link(path: Path) -> bool:
                        """Return whether path matches the simulated linked location; no side effects."""
                        return path == linked

                    with patch.object(Path, method, autospec=True, side_effect=is_link):
                        with self.assertRaises(OSError):
                            if linked == source:
                                register_resource(paths, config, "engine", "coefficients", "v2", source)
                            else:
                                verify_resource(paths, reference)
            with _lock_parent(paths.resources):
                with self.assertRaises(OSError):
                    verify_resource(paths, reference)
