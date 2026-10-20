"""Verify electronic default imports, variant allocation, and exact row replacement."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from src.backend.config import Config
from src.backend.paths import create_base_directories, get_project_paths
from src.backend.records import _lock_parent
from src.backend.resources import ResourceReference, verify_resource
from src.srim.defaults import read_sr_defaults, switch_sr_defaults
from _fixtures import make_test_project
from src.srim.electronic import create_electronic_variant


class TestElectronic(unittest.TestCase):
    """Use unittest construction and temporary fixtures to exercise electronic resources."""

    def test_default_registration(self: TestElectronic) -> None:
        """Verify self's coordinated concrete default, immutable source and idempotent switching; return None."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths, config, worker = make_test_project(Path(directory).absolute())
            first = read_sr_defaults(paths)
            reference = ResourceReference(**first["resources"]["electronic"])
            self.assertEqual((reference.category, reference.name, reference.version), ("engine", "electronic", "v1"))
            payload = verify_resource(paths, reference)
            source = config.srim_root / "Data" / "SCOEF03.dat"
            self.assertEqual(payload.read_bytes(), source.read_bytes())
            manifest = (payload.parent / "manifest.json").read_bytes()
            self.assertEqual(switch_sr_defaults(paths, config), first)
            self.assertEqual((payload.parent / "manifest.json").read_bytes(), manifest)
            source.write_bytes(b"invalid changed source")
            with self.assertRaises(ValueError):
                switch_sr_defaults(paths, config)
            self.assertEqual(read_sr_defaults(paths), first)

    def test_file_and_byte_variants(self: TestElectronic) -> None:
        """Verify self's file/byte/text inputs allocate versions without default; return None and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths = get_project_paths(Path(directory).absolute())
            create_base_directories(paths)
            config = Config("UTC", paths.root)
            source = paths.root / "custom"
            source.write_bytes(b"\x00\xff\r\n")
            first = create_electronic_variant(paths, config, source=source)
            second = create_electronic_variant(paths, config, source=source.read_bytes())
            self.assertEqual((first.version, second.version), ("v1", "v2"))
            self.assertEqual(verify_resource(paths, first).read_bytes(), source.read_bytes())
            self.assertEqual(verify_resource(paths, second).read_bytes(), source.read_bytes())
            text = "line one\r\nline two\n"
            third = create_electronic_variant(paths, config, source=text)
            self.assertEqual(third.version, "v3")
            self.assertEqual(verify_resource(paths, third).read_bytes(), text.encode("ascii"))
            fourth = create_electronic_variant(paths, config, source=str(source))
            self.assertEqual(verify_resource(paths, fourth).read_bytes(), str(source).encode("ascii"))
            name_directory = paths.resources / "engine" / "electronic"
            before = set(name_directory.iterdir())
            with self.assertRaises(ValueError):
                create_electronic_variant(paths, config, source="non-ASCII \u03bb")
            self.assertEqual(set(name_directory.iterdir()), before)
            self.assertEqual(create_electronic_variant(paths, config, source="").version, "v5")

    def test_row_replacements(self: TestElectronic) -> None:
        """Verify self's multiple string/byte edits, base variants, and line preservation; return None and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths = get_project_paths(Path(directory).absolute())
            create_base_directories(paths)
            config = Config("UTC", paths.root)
            (paths.root / "Data").mkdir()
            lines = [(f'" X" "Element {number}" {number} ' + " ".join(["0"] * 92)).encode("ascii") + (b"\r\n" if number % 2 else b"\n") for number in range(1, 93)]
            lines[-1] = lines[-1].removesuffix(b"\n")
            original = b"".join(lines)
            (paths.root / "Data" / "SCOEF03.dat").write_bytes(original)
            default = create_electronic_variant(paths, config, source=original)
            row1 = lines[0].removesuffix(b"\r\n").replace(b'" X"', b'" H"')
            row8 = lines[7].removesuffix(b"\n").replace(b'" X"', b'" O"')
            first = create_electronic_variant(paths, config, base_version="v1", replacements={1: row1, 8: row8.decode("ascii")})
            expected = list(lines)
            expected[0] = row1 + b"\r\n"
            expected[7] = row8 + b"\n"
            self.assertEqual(verify_resource(paths, first).read_bytes(), b"".join(expected))
            last = lines[-1].replace(b'" X"', b'" U"')
            second = create_electronic_variant(paths, config, base_version="v2", replacements={92: last})
            expected[-1] = last
            self.assertEqual(verify_resource(paths, second).read_bytes(), b"".join(expected))
            third = create_electronic_variant(paths, config, base_version="v3", replacements={92: last})
            self.assertEqual(third.version, "v4")
            self.assertEqual(third.sha256, second.sha256)
            self.assertEqual(verify_resource(paths, default).read_bytes(), original)

    def test_invalid_inputs(self: TestElectronic) -> None:
        """Verify self's invalid modes, keys, encodings, and row structures fail; return None and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths = get_project_paths(Path(directory).absolute())
            create_base_directories(paths)
            config = Config("UTC", paths.root)
            rows = [(f'" X" "Element {number}" {number} ' + " ".join(["0"] * 92)).encode("ascii") for number in range(1, 93)]
            create_electronic_variant(paths, config, source=b"\n".join(rows))
            for options in ({}, {"source": b"x", "base_version": "v1"}, {"source": b"x", "replacements": {1: rows[0]}}, {"base_version": "v1"}, {"replacements": {1: rows[0]}}, {"base_version": "v1", "replacements": {}}, {"source": 123}):
                with self.assertRaises(ValueError):
                    create_electronic_variant(paths, config, **options)
            for key, value in ((True, rows[0]), (0, rows[0]), (93, rows[0]), ("1", rows[0]), (1, rows[1]), (1, rows[0] + b"\n"), (1, rows[0] + b"\r"), (1, rows[0] + b" extra"), (1, 'non-ASCII \u03bb'), (1, b"\xff"), (1, 5), (1, b'"unclosed')):
                with self.assertRaises(ValueError):
                    create_electronic_variant(paths, config, base_version="v1", replacements={key: value})
            for malformed in (b"\n".join(rows[:-1]), b"\n".join(reversed(rows)), b"\n".join([b"bad", *rows[1:]])):
                reference = create_electronic_variant(paths, config, source=malformed)
                with self.assertRaises(ValueError):
                    create_electronic_variant(paths, config, base_version=reference.version, replacements={1: rows[0]})

    def test_missing_corrupt_and_linked_sources(self: TestElectronic) -> None:
        """Verify self's missing setup/files/bases, corruption, and links fail; return None and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths = get_project_paths(Path(directory).absolute())
            config = Config("UTC", paths.root)
            with self.assertRaises(FileNotFoundError):
                create_electronic_variant(paths, config, source=b"x")
            create_base_directories(paths)
            with self.assertRaises(FileNotFoundError):
                switch_sr_defaults(paths, config)
            with self.assertRaises(ValueError):
                switch_sr_defaults(paths, Config("UTC", Path("relative")))
            with self.assertRaises(ValueError):
                create_electronic_variant(paths, config, base_version="missing", replacements={1: b"x"})
            reference = create_electronic_variant(paths, config, source=b"original")
            payload = verify_resource(paths, reference)
            payload.write_bytes(b"corrupt")
            with self.assertRaises(ValueError):
                create_electronic_variant(paths, config, base_version="v1", replacements={1: b"x"})
            payload.write_bytes(b"original")
            with patch("src.srim.electronic.verify_resource", return_value=payload), patch.object(Path, "read_bytes", return_value=b"changed after verification"):
                with self.assertRaises(ValueError):
                    create_electronic_variant(paths, config, base_version="v1", replacements={1: b"x"})
            for method in ("is_symlink", "is_junction"):
                with patch.object(Path, method, return_value=True):
                    with self.assertRaises(OSError):
                        create_electronic_variant(paths, config, source=payload)

    def test_contention_and_failed_publication(self: TestElectronic) -> None:
        """Verify self's contention, retained staging, and successful retry; return None and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths = get_project_paths(Path(directory).absolute())
            create_base_directories(paths)
            config = Config("UTC", paths.root)
            with _lock_parent(paths.resources):
                with self.assertRaises(OSError):
                    create_electronic_variant(paths, config, source=b"data")
            with patch.object(Path, "rename", side_effect=PermissionError("blocked")):
                with self.assertRaises(PermissionError) as caught:
                    create_electronic_variant(paths, config, source=b"data")
            self.assertTrue(any("retained artifacts" in note for note in caught.exception.__notes__))
            reference = create_electronic_variant(paths, config, source=b"data")
            self.assertEqual(reference.version, "v1")
            self.assertEqual(verify_resource(paths, reference).read_bytes(), b"data")
