"""Verify coherent default generations, content reuse and atomic switching failures."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from _fixtures import make_test_installation, make_test_project
from src.backend.resources import ResourceReference, verify_resource
from src.srim.cab.strengths import load_cab
from src.srim.resources.defaults import read_sr_defaults, switch_sr_defaults


class TestSRDefaults(unittest.TestCase):
    """Use inherited unittest construction with automatically removed synthetic installations."""

    def setUp(self: TestSRDefaults) -> None:
        """Create self's project/config/worker and initial generation; return None and register cleanup."""
        self.temporary = TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temporary.cleanup)
        self.paths, self.config, self.worker = make_test_project(Path(self.temporary.name).absolute())
        self.generation = read_sr_defaults(self.paths)

    def test_coordinated_switch(self: TestSRDefaults) -> None:
        """Check self's pinned references survive a changed GUI installation baseline; return None."""
        self.assertEqual(load_cab(self.paths, "core", "default"), {1: "0"})
        (self.config.srim_root / "SR.exe").write_bytes(b"changed GUI executable")
        newer = switch_sr_defaults(self.paths, self.config)
        self.assertNotEqual(newer["baseline_id"], self.generation["baseline_id"])
        self.assertNotEqual(newer["resources"]["core"], self.generation["resources"]["core"])
        self.assertEqual(newer["resources"]["bragg"], self.generation["resources"]["bragg"])
        self.assertTrue(verify_resource(self.paths, ResourceReference(**self.generation["resources"]["core"])).is_file())

    def test_compound_only_switch(self: TestSRDefaults) -> None:
        """Check self's compound-only change preserves numerical versions/baseline; return None."""
        compound = self.config.srim_root / "Data" / "Compound.dat"
        compound.write_bytes(compound.read_bytes().replace(b"*Water", b"*Renamed", 1))
        newer = switch_sr_defaults(self.paths, self.config)
        self.assertEqual(newer["baseline_id"], self.generation["baseline_id"])
        self.assertNotEqual(newer["resources"]["compound"], self.generation["resources"]["compound"])
        for role in set(newer["resources"]) - {"compound"}:
            self.assertEqual(newer["resources"][role], self.generation["resources"][role])

    def test_failed_switch_keeps_generation(self: TestSRDefaults) -> None:
        """Inject self's pointer-publication failure and verify prior generation remains active; return None."""
        pointer = self.paths.resources / "defaults.json"
        before = pointer.read_bytes()
        (self.config.srim_root / "SR.exe").write_bytes(b"changed")
        with patch("src.srim.resources.defaults._write_json", side_effect=PermissionError("pointer blocked")):
            with self.assertRaises(PermissionError) as caught:
                switch_sr_defaults(self.paths, self.config)
        self.assertEqual(pointer.read_bytes(), before)
        self.assertEqual(read_sr_defaults(self.paths), self.generation)
        self.assertTrue(any("staged versions" in note for note in caught.exception.__notes__))

    def test_identical_installation_reuse(self: TestSRDefaults) -> None:
        """Check self's identical inputs reuse versions across different installation paths; return None."""
        pointer = self.paths.resources / "defaults.json"
        before = pointer.read_bytes()
        other = make_test_installation(self.paths.root / "other-installation")
        self.assertEqual(switch_sr_defaults(self.paths, other), self.generation)
        self.assertEqual(pointer.read_bytes(), before)
        for role, reference in self.generation["resources"].items():
            self.assertEqual(len(list((self.paths.resources / reference["category"] / role).glob("v*"))), 1)
