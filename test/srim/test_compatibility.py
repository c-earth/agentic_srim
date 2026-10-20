"""Verify conservative native compatibility fixes before resource and worker staging."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from _fixtures import make_test_project
from src.backend.resources import ResourceReference, verify_resource
from src.srim.compatibility import normalize_atomdata, normalize_compounds, read_module_version
from src.srim.defaults import switch_sr_defaults, verify_sr_baseline
from src.srim.material import parse_atomdata, parse_compounds


class TestCompatibility(unittest.TestCase):
    """Provide temporary synthetic installations for native defect regression checks."""

    def setUp(self: TestCompatibility) -> None:
        """Create self's project and installation; return None and register cleanup."""
        temporary = TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(temporary.cleanup)
        self.paths, self.config, self.worker = make_test_project(Path(temporary.name).absolute())

    def test_version_selection(self: TestCompatibility) -> None:
        """Check self's preferred/fallback version, invalid preferred and missing paths; return None."""
        preferred = self.config.srim_root / "SR Module/VERSION"
        alternate = preferred.with_name("_VERSION")
        original = preferred.read_bytes()
        alternate.write_bytes(b"alternate\r\n")
        self.assertEqual(read_module_version(self.config.srim_root), (preferred, original))
        preferred.write_bytes(b"\r\n")
        with self.assertRaises(ValueError):
            read_module_version(self.config.srim_root)
        preferred.unlink()
        self.assertEqual(read_module_version(self.config.srim_root), (alternate, b"alternate\r\n"))
        alternate.unlink()
        with self.assertRaises(FileNotFoundError):
            read_module_version(self.config.srim_root)
        with self.assertRaises(ValueError):
            read_module_version(Path("relative"))

    def test_atomdata_conservative(self: TestCompatibility) -> None:
        """Check self's exact Rubidium repair, idempotence and rejection of other defects; return None."""
        original = (self.config.srim_root / "Data/ATOMDATA").read_bytes().replace(b"37 Rb Element37", b"37 Rb Rubidium")
        bad = original.replace(b"37 Rb Rubidium", b"37 Ru Rubidium")
        effective, changes = normalize_atomdata(bad)
        self.assertEqual(effective, original)
        self.assertEqual(changes, [{"operation": "set", "key": [37, "symbol"], "value": "Rb"}])
        self.assertEqual(normalize_atomdata(effective), (effective, []))
        parse_atomdata(effective)
        other = bad.replace(b"Rubidium", b"Other")
        self.assertEqual(normalize_atomdata(other), (other, []))
        with self.assertRaises(ValueError):
            parse_atomdata(other)

    def test_compound_conservative(self: TestCompatibility) -> None:
        """Check self's two independent comma fixes, idempotence and strict rejection; return None."""
        original = (self.config.srim_root / "Data/Compound.dat").read_bytes()
        variants = [original.replace(b'"Water",', b'"Water"'), original.replace(b'8, 1\r\n', b'8, 1,\r\n')]
        variants.append(variants[0].replace(b'8, 1\r\n', b'8, 1,\r\n'))
        for bad in variants:
            with self.assertRaises(ValueError):
                parse_compounds(bad)
            effective, changes = normalize_compounds(bad)
            self.assertEqual(effective, original)
            self.assertTrue(changes)
            self.assertEqual(normalize_compounds(effective), (effective, []))
        for bad in (original.replace(b'8, 1\r\n', b'8, 1,,\r\n'), original.replace(b'1.0, 2,', b'1.0, 3,')):
            with self.assertRaises(ValueError):
                normalize_compounds(bad)

    def test_native_registration(self: TestCompatibility) -> None:
        """Check self's native-style registration, effective baseline, provenance and source preservation; return None."""
        root = self.config.srim_root
        (root / "SR Module/VERSION").rename(root / "SR Module/_VERSION")
        atom = root / "Data/ATOMDATA"
        atom.write_bytes(atom.read_bytes().replace(b"37 Rb Element37", b"37 Ru Rubidium"))
        compound = root / "Data/Compound.dat"
        compound.write_bytes(compound.read_bytes().replace(b"*Water", b"*CompatibilityWater").replace(b'"Water",', b'"Water"').replace(b'8, 1\r\n', b'8, 1,\r\n'))
        before = {p: p.read_bytes() for p in (root / "SR Module/_VERSION", atom, compound)}
        generation = switch_sr_defaults(self.paths, self.config)
        for role, original in (("atomdata", atom), ("compound", compound)):
            payload = verify_resource(self.paths, ResourceReference(**generation["resources"][role]))
            provenance = json.loads((payload.parent / "manifest.json").read_text())["provenance"]
            self.assertEqual(provenance["source"]["origin"], str(original))
            self.assertTrue(provenance["changes"])
            self.assertNotEqual(payload.read_bytes(), before[original])
        baseline = json.loads((self.paths.resources / "baselines" / (generation["baseline_id"] + ".json")).read_text())
        verify_sr_baseline(self.paths, self.config, baseline)
        self.assertEqual(switch_sr_defaults(self.paths, self.config), generation)
        self.assertEqual({p: p.read_bytes() for p in before}, before)
