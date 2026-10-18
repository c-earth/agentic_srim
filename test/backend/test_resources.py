"""Verify shared resource identity, immutable readable versions, publication, and corruption detection."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from src.backend.config import Config
from src.backend.paths import create_base_directories, get_project_paths
from src.backend.records import _lock_parent
from src.backend.resources import ResourceReference, _validate_identifier, register_resource, resolve_resource, verify_resource


class TestResources(unittest.TestCase):
    """Use unittest construction to test shared resources in automatically removed local fixtures."""

    def test_registration_versions_and_categories(self: TestResources) -> None:
        """Check self's readable versions, deduplication, extensible hierarchy, and snapshots; return None and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths = get_project_paths(Path(directory).absolute())
            create_base_directories(paths)
            config = Config("UTC", paths.root)
            source = paths.root / "source.dat"
            source.write_bytes(b"original coefficients\r\n")
            first = register_resource(paths, config, "engine", "electronic-stopping", "srim-coefficients", "standard", source)
            self.assertEqual(first.sha256, hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual(resolve_resource(paths, first.category, first.subcategory, first.name, first.version), first)
            self.assertEqual(register_resource(paths, config, first.category, first.subcategory, first.name, first.version, source), first)
            alias_version = register_resource(paths, config, first.category, first.subcategory, first.name, "same-content", source)
            self.assertEqual(verify_resource(paths, first), verify_resource(paths, alias_version))
            for category, subcategory in (("cab", "core"), ("cab", "bond"), ("future-family", "future-subtype")):
                reference = register_resource(paths, config, category, subcategory, "dataset", "v1", source)
                self.assertEqual(verify_resource(paths, reference).read_bytes(), source.read_bytes())
            source.write_bytes(b"new coefficients")
            second = register_resource(paths, config, first.category, first.subcategory, first.name, "custom", source)
            self.assertNotEqual(first.sha256, second.sha256)
            self.assertEqual(verify_resource(paths, first).read_bytes(), b"original coefficients\r\n")
            with self.assertRaises(ValueError):
                register_resource(paths, config, first.category, first.subcategory, first.name, first.version, source)
            self.assertEqual(resolve_resource(paths, first.category, first.subcategory, first.name, first.version), first)
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
            reference = register_resource(paths, config, "cab", "core", "parameters", "v1", source)
            self.assertEqual(reference.size_bytes, 0)
            for invalid in (replace(reference, sha256="bad"), replace(reference, size_bytes=True), replace(reference, category="../escape"), replace(reference, size_bytes=-1)):
                with self.assertRaises(ValueError):
                    verify_resource(paths, invalid)
            with self.assertRaises(ValueError):
                resolve_resource(paths, "cab", "core", "parameters", "missing")

    def test_corruption_and_reference_mismatch(self: TestResources) -> None:
        """Check self's same-size corruption, manifest/index tampering, and forged references are rejected; return None and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths = get_project_paths(Path(directory).absolute())
            create_base_directories(paths)
            source = paths.root / "source"
            source.write_bytes(b"abc")
            config = Config("UTC", paths.root)
            reference = register_resource(paths, config, "cab", "bond", "parameters", "v1", source)
            payload = verify_resource(paths, reference)
            payload.write_bytes(b"xyz")
            with self.assertRaises(ValueError):
                verify_resource(paths, reference)
            with self.assertRaises(ValueError):
                register_resource(paths, config, "cab", "bond", "parameters", "v2", source)
            payload.write_bytes(b"abc")
            with self.assertRaises(ValueError):
                verify_resource(paths, replace(reference, size_bytes=4))
            manifest = payload.parent / "manifest.json"
            original = manifest.read_text()
            document = json.loads(original)
            document["schema_version"] = True
            manifest.write_text(json.dumps(document))
            with self.assertRaises(ValueError):
                resolve_resource(paths, "cab", "bond", "parameters", "v1")
            manifest.write_text(original)
            index = payload.parents[2] / "versions.json"
            index.write_text('{"schema_version": 1, "versions": {"v1": "../bad"}}')
            with self.assertRaises(ValueError):
                resolve_resource(paths, "cab", "bond", "parameters", "v1")

    def test_publication_failure_and_retry(self: TestResources) -> None:
        """Check self's failed index publication retains a complete object for retry and reports artifacts; return None and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths = get_project_paths(Path(directory).absolute())
            create_base_directories(paths)
            source = paths.root / "source"
            source.write_bytes(b"resource")
            config = Config("UTC", paths.root)
            import os
            original = os.replace

            def replace_file(source_path: Path, destination: Path) -> None:
                """Replace source_path at destination unless it is the version index, which raises PermissionError; return None otherwise."""
                if destination.name == "versions.json":
                    raise PermissionError("index blocked")
                original(source_path, destination)

            with patch("src.backend.resources.os.replace", side_effect=replace_file):
                with self.assertRaises(PermissionError) as caught:
                    register_resource(paths, config, "engine", "electronic-stopping", "coefficients", "v1", source)
            self.assertTrue(any("retained artifacts" in note for note in caught.exception.__notes__))
            reference = register_resource(paths, config, "engine", "electronic-stopping", "coefficients", "v1", source)
            self.assertEqual(verify_resource(paths, reference).read_bytes(), b"resource")

    def test_missing_setup_links_and_contention(self: TestResources) -> None:
        """Check self's missing setup, linked ancestors/payloads, and contention reject operations; return None and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths = get_project_paths(Path(directory).absolute())
            config = Config("UTC", paths.root)
            source = paths.root / "source"
            source.write_bytes(b"resource")
            with self.assertRaises(FileNotFoundError):
                register_resource(paths, config, "engine", "electronic-stopping", "coefficients", "v1", source)
            create_base_directories(paths)
            reference = register_resource(paths, config, "engine", "electronic-stopping", "coefficients", "v1", source)
            payload = verify_resource(paths, reference)
            for method in ("is_symlink", "is_junction"):
                for linked in (source, paths.resources / "engine", payload):

                    def is_link(path: Path) -> bool:
                        """Return whether path matches the simulated linked location; no side effects."""
                        return path == linked

                    with patch.object(Path, method, autospec=True, side_effect=is_link):
                        with self.assertRaises(OSError):
                            if linked == source:
                                register_resource(paths, config, "engine", "electronic-stopping", "coefficients", "v2", source)
                            else:
                                verify_resource(paths, reference)
            with _lock_parent(paths.resources):
                with self.assertRaises(OSError):
                    verify_resource(paths, reference)
