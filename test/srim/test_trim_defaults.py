"""Verify measured TRIM seed data, immutable variants, pointers and installed profiles."""

from __future__ import annotations

from copy import deepcopy
import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from _fixtures import make_test_project
from test_trim_inputs import TRIM_INSTALLATION_FILES
from src.backend.resources import verify_resource
from src.srim.trim_defaults import apply_trim_damage_overrides, create_trim_damage_variant, parse_trim_damage, read_trim_defaults, read_trim_installation, switch_trim_defaults


CACHE = Path(".cache/trim-tests").resolve()
CACHE.mkdir(parents=True, exist_ok=True)
SEED = Path("src/srim/data/trim-damage-defaults.json").read_bytes()


class TestTRIMDefaults(unittest.TestCase):
    """Use inherited unittest construction with owned cache fixtures for resource contracts."""

    def test_seed_matches_measured_table(self: TestTRIMDefaults) -> None:
        """Compare all packaged Disp/Latt values to recorded measurements; return None."""
        data = parse_trim_damage(SEED)
        with Path("doc/reference/trim-elemental-defaults.csv").open(encoding="utf-8", newline="") as stream:
            measured = list(csv.DictReader(stream))
        self.assertEqual(len(data["rows"]), 92)
        for row, source in zip(data["rows"], measured, strict=True):
            self.assertEqual(row, {"atomic_number": int(source["atomic_number"]), "displacement_eV": source["displacement_eV"], "lattice_binding_eV": source["lattice_binding_eV"]})

    def test_versioned_variants_and_pointer(self: TestTRIMDefaults) -> None:
        """Publish/select an edited vN while retaining prior bytes/defaults; return None."""
        with TemporaryDirectory(dir=CACHE) as td:
            paths, config, worker = make_test_project(Path(td))
            first = switch_trim_defaults(paths, config)
            self.assertEqual(first, switch_trim_defaults(paths, config))
            sr_pointer = (paths.resources / "defaults.json").read_bytes()
            variant = create_trim_damage_variant(paths, config, base_version="default", overrides=[{"operation": "set", "key": [14, "lattice_binding_eV"], "value": "0"}])
            self.assertNotEqual(variant.version, first["damage"]["version"])
            self.assertEqual(read_trim_defaults(paths), first)
            self.assertEqual(parse_trim_damage(verify_resource(paths, variant).read_bytes())["rows"][13]["lattice_binding_eV"], "0")
            self.assertEqual(switch_trim_defaults(paths, config, damage_version=variant.version)["damage"]["version"], variant.version)
            self.assertEqual((paths.resources / "defaults.json").read_bytes(), sr_pointer)

    def test_surface_override_remove(self: TestTRIMDefaults) -> None:
        """Set direct zero Surf and remove it to restore algorithmic inheritance; return None."""
        data = parse_trim_damage(SEED)
        edited = apply_trim_damage_overrides(data, [{"operation": "set", "key": [14, "surface_binding_eV"], "value": "0"}])
        self.assertEqual(edited["rows"][13]["surface_binding_eV"], "0")
        restored = apply_trim_damage_overrides(edited, [{"operation": "remove", "key": [14, "surface_binding_eV"]}])
        self.assertEqual(restored, data)
        with self.assertRaises(ValueError):
            apply_trim_damage_overrides(data, [{"operation": "remove", "key": [14, "lattice_binding_eV"]}])

    def test_duplicate_and_missing_rows(self: TestTRIMDefaults) -> None:
        """Reject duplicate JSON keys, row identities and duplicate edits; return None."""
        data = parse_trim_damage(SEED)
        for rows in (data["rows"][:-1], [data["rows"][0]] * 92):
            bad = deepcopy(data)
            bad["rows"] = rows
            with self.assertRaises(ValueError):
                parse_trim_damage(json.dumps(bad).encode())
        with self.assertRaises(ValueError):
            parse_trim_damage(b'{"schema_version":1,"schema_version":1}')
        edit = {"operation": "set", "key": [14, "surface_binding_eV"], "value": "2"}
        with self.assertRaises(ValueError):
            apply_trim_damage_overrides(data, [edit, edit])

    def test_installed_native_fields(self: TestTRIMDefaults) -> None:
        """Copy noneditable settings/profile from a safe synthetic installation; return None."""
        with TemporaryDirectory(dir=CACHE) as td:
            paths, config, worker = make_test_project(Path(td))
            for name, payload in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(payload)
            profile, files = read_trim_installation(config)
            self.assertEqual(profile["native_fields"], {"legacy_bragg": "0", "reminders": 0, "stopping_version": 0})
            self.assertEqual(profile["engine_family"], "2013")
            self.assertEqual(files["TRIM.IN"], TRIM_INSTALLATION_FILES["TRIM.IN"])
            (config.srim_root / "TRIM.IN").write_bytes(files["TRIM.IN"].replace(b"1 12345 0", b"1 12345 7").replace(b"Version\r\n0", b"Version\r\n1"))
            self.assertEqual(read_trim_installation(config)[0]["native_fields"]["reminders"], 7)
            self.assertEqual(read_trim_installation(config)[0]["native_fields"]["stopping_version"], 1)
            (config.srim_root / "Data/VERSION").write_bytes(b"Unknown\r\n")
            with self.assertRaisesRegex(ValueError, "mode map"):
                read_trim_installation(config)

    def test_integrity_and_concurrent_switch(self: TestTRIMDefaults) -> None:
        """Retain prior pointer on publication failure/concurrent change and reject corruption; return None."""
        with TemporaryDirectory(dir=CACHE) as td:
            paths, config, worker = make_test_project(Path(td))
            original = switch_trim_defaults(paths, config)
            pointer = paths.resources / "trim-damage-defaults.json"
            before = pointer.read_bytes()
            variant = create_trim_damage_variant(paths, config, source=SEED)
            with patch("src.srim.trim_defaults._write_json", side_effect=PermissionError("blocked")):
                with self.assertRaises(PermissionError):
                    switch_trim_defaults(paths, config, damage_version=variant.version)
            self.assertEqual(pointer.read_bytes(), before)
            with patch("src.srim.trim_defaults._hash_file", side_effect=[("a" * 64, 1), ("b" * 64, 1)]):
                with self.assertRaisesRegex(ValueError, "concurrently"):
                    switch_trim_defaults(paths, config, damage_version=variant.version)
            self.assertEqual(read_trim_defaults(paths), original)
            verify_resource(paths, variant).write_bytes(b"corrupt")
            with self.assertRaises(ValueError):
                switch_trim_defaults(paths, config, damage_version=variant.version)
