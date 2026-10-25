"""Verify bounded registration, packaging inspection and pinned samples using synthetic data."""

from __future__ import annotations

from copy import deepcopy
import gzip
import io
import json
import os
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zipfile

from src.backend.paths import get_project_paths
from src.knowledge.database_sources import InspectionBudget, BudgetedReader, inspect_database_source, list_database_sources, read_database_sample, validate_database_document


class TestDatabaseSources(unittest.TestCase):
    """Provide cache-owned neutral sources without native execution or source extraction."""

    def setUp(self: TestDatabaseSources) -> None:
        """Create self's isolated cache project and source directory; return None."""
        cache = Path(__file__).resolve().parents[2] / ".cache/database-source-tests"
        cache.mkdir(parents=True, exist_ok=True)
        self.temporary = TemporaryDirectory(dir=cache)
        self.root = Path(self.temporary.name)
        self.paths = get_project_paths(self.root)
        self.source = self.root / "collection"
        self.source.mkdir()
        self.register(self.source)

    def tearDown(self: TestDatabaseSources) -> None:
        """Remove self's isolated test workspace; return None."""
        self.temporary.cleanup()

    def register(self: TestDatabaseSources, path: Path | None) -> str:
        """Write self's synthetic settings for path and return source ID or empty string."""
        self.root.joinpath("settings.toml").write_text('[srim]\nroot = ' + json.dumps(str(self.root / "unused")) + '\n[knowledge]\ndatabase_paths = ' + json.dumps([] if path is None else [str(path)]) + '\n', encoding="utf-8")
        sources = list_database_sources(self.paths)["sources"]
        return sources[0]["source_id"] if sources else ""

    def inspect(self: TestDatabaseSources) -> dict:
        """Inspect self's currently registered single source and return its report."""
        return inspect_database_source(self.paths, list_database_sources(self.paths)["sources"][0]["source_id"])

    def test_text_json_binary_sqlite_and_no_mutation(self: TestDatabaseSources) -> None:
        """Report syntax only, pin prefixes and leave source bytes and pool absent."""
        contents = {"a.csv": b"a,b\n1,2\n", "b.json": b'{"value":"1.20"}', "c.bin": b"\xff\x00", "d.sqlite": b"SQLite format 3\x00" + bytes(100)}
        for name, data in contents.items():
            (self.source / name).write_bytes(data)
        report = self.inspect()
        self.assertEqual(report["inspection_state"], "inspected", report["issues"])
        self.assertEqual(report["format_detection"]["state"], "insufficient_evidence")
        self.assertEqual(report["format_detection"]["candidates"][0]["adapter_id"], "snumat-trim-v1")
        self.assertEqual(report["recommended_skill"], "external-database-onboarding")
        self.assertEqual(report["samples"][0]["structure"], {"header_candidates": ["a", "b"]})
        self.assertEqual(report["samples"][1]["structure"], {"json_keys": ["value"]})
        self.assertEqual(report["samples"][2]["encoding"], "base64")
        self.assertEqual(report["inventory"][-1]["container"], "sqlite")
        for sample in report["samples"]:
            self.assertEqual(read_database_sample(self.paths, sample["reference"])["content"], sample["content"])
        self.assertEqual({path.name: path.read_bytes() for path in self.source.iterdir()}, contents)
        self.assertFalse(self.root.joinpath("memory").exists())

    def test_revocation_missing_and_change(self: TestDatabaseSources) -> None:
        """Reload registration and reject revoked, changed and missing source evidence."""
        path = self.source / "table.txt"
        path.write_bytes(b"original")
        identity = self.register(path)
        reference = self.inspect()["samples"][0]["reference"]
        self.register(None)
        with self.assertRaisesRegex(ValueError, "unconfigured"):
            read_database_sample(self.paths, reference)
        self.assertEqual(identity, self.register(path))
        path.write_bytes(b"modified")
        os.utime(path, ns=(reference["file_mtime_ns"], reference["file_mtime_ns"]))
        with self.assertRaisesRegex(ValueError, "integrity"):
            read_database_sample(self.paths, reference)
        path.unlink()
        self.assertEqual(self.inspect()["inspection_state"], "unavailable")
        self.assertFalse(path.exists())

    def test_archive_packaging_and_sample_rereads(self: TestDatabaseSources) -> None:
        """Inspect synthetic ZIP, TAR, compressed TAR and gzip without extracting members."""
        for container in ("zip", "tar", "tar_gzip", "gzip"):
            with self.subTest(container=container):
                path = self.source / container
                payload = b"x,y\n3,4\n"
                if container == "zip":
                    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                        archive.writestr("neutral/table.csv", payload)
                elif container == "gzip":
                    path.write_bytes(gzip.compress(payload))
                else:
                    with tarfile.open(path, "w:gz" if container == "tar_gzip" else "w", format=tarfile.USTAR_FORMAT) as archive:
                        info = tarfile.TarInfo("neutral/table.csv")
                        info.size = len(payload)
                        archive.addfile(info, io.BytesIO(payload))
                self.register(path)
                original = path.read_bytes()
                report = self.inspect()
                self.assertEqual(report["inspection_state"], "inspected", report["issues"])
                sample = report["samples"][0]
                self.assertEqual(sample["reference"]["container"], container)
                self.assertEqual(read_database_sample(self.paths, sample["reference"])["content"], payload.decode())
                self.assertEqual(path.read_bytes(), original)
                self.assertFalse((self.source / "neutral").exists())

    def test_unsafe_and_duplicate_archive_members(self: TestDatabaseSources) -> None:
        """Reject traversal, Windows devices, case collisions and TAR links without samples."""
        path = self.source / "bundle"
        for names in (("../escape",), ("/absolute",), ("CON",), ("a:stream",), ("same", "SAME")):
            with self.subTest(names=names):
                with zipfile.ZipFile(path, "w") as archive:
                    for name in names:
                        archive.writestr(name, b"text")
                self.register(path)
                report = self.inspect()
                self.assertEqual(report["inspection_state"], "unsafe", report["issues"])
                self.assertEqual(report["samples"], [])
        with tarfile.open(path, "w", format=tarfile.USTAR_FORMAT) as archive:
            info = tarfile.TarInfo("link")
            info.type, info.linkname = tarfile.SYMTYPE, "outside"
            archive.addfile(info)
        self.assertEqual(self.inspect()["inspection_state"], "unsafe")

    def test_limits_depth_samples_and_large_tar_skip(self: TestDatabaseSources) -> None:
        """Report limited subsets and stop before decompressing a large TAR payload."""
        deep = self.source / "one/two/three"
        deep.mkdir(parents=True)
        (deep / "hidden.txt").write_bytes(b"not sampled")
        for index in range(12):
            (self.source / f"file{index:02d}").write_bytes(b"text")
        report = self.inspect()
        self.assertEqual(report["inspection_state"], "limited")
        self.assertLessEqual(len(report["samples"]), 8)
        self.assertFalse(any("three" in entry["relative_path"] for entry in report["inventory"]))
        path = self.source / "large.tar.gz"
        info = tarfile.TarInfo("huge")
        info.size = 20 * 1024 * 1024
        path.write_bytes(gzip.compress(info.tobuf(format=tarfile.USTAR_FORMAT) + b"\0" * 1024))
        self.register(path)
        report = self.inspect()
        self.assertEqual(report["inspection_state"], "limited")
        self.assertTrue(any("payload skip" in issue for issue in report["issues"]))
        self.assertLess(report["usage"]["expanded_bytes"], 4096)

    def test_directory_and_stream_budgets(self: TestDatabaseSources) -> None:
        """Cap inventory, compressed expansion, raw reads and expired deadlines."""
        for index in range(270):
            (self.source / str(index)).touch()
        report = self.inspect()
        self.assertEqual(report["inspection_state"], "limited")
        self.assertLessEqual(len(report["inventory"]), 256)
        budget = InspectionBudget(raw_bytes=16777215)
        reader = BudgetedReader(io.BytesIO(b"abcd"), budget, "raw")
        with self.assertRaisesRegex(ValueError, "budget-exhausted"):
            reader.read(2)
        self.assertEqual(reader.read(1), b"a")
        with self.assertRaisesRegex(ValueError, "budget-exhausted"):
            BudgetedReader(io.BytesIO(b"x"), InspectionBudget(deadline=0), "raw").read(1)
        with self.assertRaises(io.UnsupportedOperation):
            BudgetedReader(io.BytesIO(b"x"), InspectionBudget(), "expanded").seek(0)
        compressed = self.source / "expansion.gz"
        compressed.write_bytes(gzip.compress(b"x" * 1000000))
        self.register(compressed)
        self.assertEqual(self.inspect()["usage"]["sample_bytes"], 65536)

    def test_bad_archives_closed_envelopes_and_reparse(self: TestDatabaseSources) -> None:
        """Reject malformed packaging, forged locators and reparse source metadata."""
        path = self.source / "bad.zip"
        path.write_bytes(b"PK\x03\x04broken")
        identity = self.register(path)
        self.assertTrue(self.inspect()["issues"])
        path.write_bytes(b"text")
        reference = self.inspect()["samples"][0]["reference"]
        for change in ({"extra": 1}, {"relative_path": "../escape"}, {"sample_offset": 1}, {"member_path": "missing"}, {"sample_size_bytes": True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                read_database_sample(self.paths, {**reference, **change})
        with self.assertRaises(ValueError):
            validate_database_document({"schema_version": 1, "sources": [], "issues": [], "extra": None}, "source-list")
        metadata = path.lstat()
        fake = type("Metadata", (), {"st_mode": metadata.st_mode, "st_file_attributes": 1024})()
        original = Path.lstat
        with patch.object(Path, "lstat", autospec=True, side_effect=lambda current: fake if current == path else original(current)):
            self.assertEqual(list_database_sources(self.paths)["sources"][0]["state"], "unsafe")
            self.assertEqual(inspect_database_source(self.paths, identity)["inspection_state"], "unsafe")

    def test_syntax_empty_and_truncated_compression(self: TestDatabaseSources) -> None:
        """Recognize JSON-lines/TSV candidates and safely report short or corrupt containers."""
        for payload, structure in ((b'{"field":1}\n{"field":2}\n', {"json_keys": ["field"]}), (b"a\tb\n1\t2\n", {"header_candidates": ["a", "b"]}), (b"", None)):
            path = self.source / "neutral"
            path.write_bytes(payload)
            self.register(path)
            report = self.inspect()
            self.assertEqual(report["samples"][0]["structure"], structure)
        for payload in (b"\x1f\x8b", b"\x1f\x8b\x08\x00" + bytes(6) + b"invalid compressed body"):
            path.write_bytes(payload)
            report = self.inspect()
            self.assertTrue(report["issues"])
            self.assertEqual(report["samples"], [])

    def test_zip_preflight_and_metadata_integrity(self: TestDatabaseSources) -> None:
        """Bound central metadata and reject changed archive bytes before pinned reread."""
        import struct
        path = self.source / "neutral.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("field.txt", b"evidence")
        original = path.read_bytes()
        self.register(path)
        reference = self.inspect()["samples"][0]["reference"]
        path.write_bytes(original + b"changed")
        with self.assertRaisesRegex(ValueError, "integrity"):
            read_database_sample(self.paths, reference)
        for field, value in ((10, 65535), (12, 8388609), (16, 0xffffffff)):
            changed = bytearray(original)
            offset = changed.rfind(b"PK\x05\x06")
            struct.pack_into("<H" if field == 10 else "<L", changed, offset + field, value)
            path.write_bytes(changed)
            self.assertEqual(self.inspect()["inspection_state"], "unsupported_container")
