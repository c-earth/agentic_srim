"""Verify registered-source hashing, bounded TAR reading and pinned member access."""
from __future__ import annotations

from copy import deepcopy
import csv
import gzip
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tarfile
import time
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from src.backend.paths import get_project_paths
from src.knowledge.database_sources import list_database_sources
from src.knowledge.external_sources import ARCHIVES, DATASET_ROOT, SOURCE_PROGRESS, SOURCE_DEADLINE, fingerprint_external_source, iter_snumat_cases, read_external_evidence, resolve_external_source
from src.knowledge.snumat_adapter import normalize_snumat_case
from test_snumat_adapter import CASE_ID, MANIFEST, MAPPING, MEMBERS

METADATA_MEMBERS = {}
for filename, row in (("manifest.csv", MANIFEST), ("case_mapping.csv", MAPPING)):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(row))
    writer.writeheader()
    writer.writerow(row)
    METADATA_MEMBERS[filename] = stream.getvalue().encode("utf-8")
CHECKSUM_MEMBERS = {**METADATA_MEMBERS, **{"cases/SM-1/" + CASE_ID + "/" + key: value for key, value in MEMBERS.items()}}
METADATA_MEMBERS["SHA256SUMS"] = "".join(hashlib.sha256(value).hexdigest() + "  " + key + "\n" for key, value in sorted(CHECKSUM_MEMBERS.items())).encode("ascii")
SYNTHETIC_ARCHIVES = {}
for ordinal, name in enumerate(ARCHIVES):
    content = METADATA_MEMBERS if ordinal == 0 else {"cases/SM-1/" + CASE_ID + "/" + key: value for key, value in MEMBERS.items()} if ordinal == 1 else {}
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
        for filename, payload in content.items():
            entry = tarfile.TarInfo(DATASET_ROOT + "/" + filename)
            entry.size = len(payload)
            entry.pax_headers = {"atime": "1792289113", "ctime": "1792289103"}
            if filename.endswith("/TRIM.IN"):
                entry.pax_headers.update({"path": entry.name, "size": str(len(payload))})
                entry.name = DATASET_ROOT + "/raw-placeholder-input"
            archive.addfile(entry, io.BytesIO(payload))
    SYNTHETIC_ARCHIVES[name] = stream.getvalue()
ROOT_MEMBERS = {**SYNTHETIC_ARCHIVES, "README.md": b"Synthetic schema fixture; no scientific claims.\n", "SHA256SUMS.txt": "".join(hashlib.sha256(value).hexdigest() + "  " + key + "\n" for key, value in sorted(SYNTHETIC_ARCHIVES.items())).encode("ascii")}


class TestExternalSources(unittest.TestCase):
    """Exercise source boundaries in cache-owned isolated fixtures."""

    def test_source_revocation_and_unsafe_paths(self: TestExternalSources) -> None:
        """Reload registrations, reject revoked IDs and never create missing sources."""
        cache = Path(__file__).resolve().parents[2] / ".cache/external-source-tests"
        cache.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=cache) as temporary:
            root = Path(temporary)
            source = root / "dataset"
            source.mkdir()
            settings = '[srim]\nroot = ' + json.dumps(str(root / "unused")) + '\n[knowledge]\ndatabase_paths = ' + json.dumps([str(source)]) + '\n'
            (root / "settings.toml").write_text(settings, encoding="utf-8")
            paths = get_project_paths(root)
            source_id = list_database_sources(paths)["sources"][0]["source_id"]
            self.assertEqual(resolve_external_source(paths, source_id), source)
            (root / "settings.toml").write_text(settings.replace(json.dumps([str(source)]), "[]"), encoding="utf-8")
            with self.assertRaises(ValueError):
                resolve_external_source(paths, source_id)
            (root / "settings.toml").write_text(settings, encoding="utf-8")
            source.rmdir()
            with self.assertRaises(OSError):
                resolve_external_source(paths, source_id)
            self.assertFalse(source.exists())

    def test_archive_integrity_and_bounds(self: TestExternalSources) -> None:
        """Verify full archives/joins and fail unsafe, stale or over-budget input."""
        cache = Path(__file__).resolve().parents[2] / ".cache/external-source-tests"
        cache.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=cache) as temporary:
            root = Path(temporary)
            source = root / "dataset"
            source.mkdir()
            for name, payload in ROOT_MEMBERS.items():
                (source / name).write_bytes(payload)
            (root / "settings.toml").write_text('[srim]\nroot = ' + json.dumps(str(root / "unused")) + '\n[knowledge]\ndatabase_paths = ' + json.dumps([str(source)]) + '\n', encoding="utf-8")
            paths = get_project_paths(root)
            source_id = list_database_sources(paths)["sources"][0]["source_id"]
            fingerprints = fingerprint_external_source(paths, source_id, cancel=lambda: False)
            self.assertEqual(len(fingerprints["files"]), 13)
            deadline_token = SOURCE_DEADLINE.set(time.monotonic() - 1)
            try:
                with self.assertRaises(ValueError):
                    fingerprint_external_source(paths, source_id, cancel=lambda: False)
            finally:
                SOURCE_DEADLINE.reset(deadline_token)
            self.assertIsNone(SOURCE_DEADLINE.get())
            events = []
            token = SOURCE_PROGRESS.set(events.append)
            try:
                cases = list(iter_snumat_cases(paths, source_id, fingerprints, cancel=lambda: False))
            finally:
                SOURCE_PROGRESS.reset(token)
            self.assertEqual([event["data"]["archive"] for event in events], list(ARCHIVES))
            self.assertTrue(all(event["phase"] == "indexing" and event["data"].get("stage") == "archive-completed" and event["source"] == "adapter" and not event["worker_ids"] and not event["simulation_ids"] for event in events))
            self.assertIsNone(SOURCE_PROGRESS.get())
            self.assertEqual(len(cases), 1)
            self.assertEqual(cases[0]["case_id"], CASE_ID)
            self.assertNotIn("RANGE_3D.txt", cases[0]["members"])
            self.assertEqual(cases[0]["issues"], [])
            self.assertFalse(any((root / "memory/knowledge/external/staging").glob("*/spool")))
            with patch("src.knowledge.external_sources.MAX_EXPANDED", 512), self.assertRaises(ValueError):
                list(iter_snumat_cases(paths, source_id, fingerprints, cancel=lambda: False))
            (source / ARCHIVES[1]).write_bytes(b"changed")
            with self.assertRaises(ValueError):
                fingerprint_external_source(paths, source_id, cancel=lambda: False)
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode="w:gz", format=tarfile.GNU_FORMAT) as archive:
                entry = tarfile.TarInfo(DATASET_ROOT + "/escape")
                entry.type, entry.linkname = tarfile.SYMTYPE, "../outside"
                archive.addfile(entry)
            (source / ARCHIVES[1]).write_bytes(ROOT_MEMBERS[ARCHIVES[1]])
            (source / ARCHIVES[-1]).write_bytes(stream.getvalue())
            (source / "SHA256SUMS.txt").write_text("".join(hashlib.sha256((source / name).read_bytes()).hexdigest() + "  " + name + "\n" for name in sorted(ARCHIVES)), encoding="ascii")
            unsafe = fingerprint_external_source(paths, source_id, cancel=lambda: False)
            with self.assertRaises(ValueError):
                list(iter_snumat_cases(paths, source_id, unsafe, cancel=lambda: False))

            variants = []
            for filename, member_type in ((DATASET_ROOT + "/../escape", tarfile.REGTYPE), (DATASET_ROOT + "/sparse", tarfile.GNUTYPE_SPARSE), (DATASET_ROOT + "/pax", tarfile.XHDTYPE)):
                entry = tarfile.TarInfo(filename)
                entry.type = member_type
                variants.append([entry])
            duplicate = tarfile.TarInfo(DATASET_ROOT + "/duplicate")
            variants.append([duplicate, duplicate])
            upper, lower = tarfile.TarInfo(DATASET_ROOT + "/Case"), tarfile.TarInfo(DATASET_ROOT + "/case")
            variants.append([upper, lower])
            huge = tarfile.TarInfo(DATASET_ROOT + "/cases/SM-1/" + CASE_ID + "/TRIM.IN")
            huge.size = 1048577
            variants.append([huge])
            for variant in variants:
                raw = b"".join(entry.tobuf(format=tarfile.GNU_FORMAT) for entry in variant) + bytes(1024)
                (source / ARCHIVES[-1]).write_bytes(gzip.compress(raw))
                (source / "SHA256SUMS.txt").write_text("".join(hashlib.sha256((source / name).read_bytes()).hexdigest() + "  " + name + "\n" for name in sorted(ARCHIVES)), encoding="ascii")
                pinned = fingerprint_external_source(paths, source_id, cancel=lambda: False)
                with self.assertRaises(ValueError):
                    list(iter_snumat_cases(paths, source_id, pinned, cancel=lambda: False))
                with self.assertRaises(ValueError):
                    list(iter_snumat_cases(paths, source_id, pinned, cancel=lambda: False, inventory_only=True))

            pax_payloads = [b"99 atime=123\n", b"20 atime=1792289113\n20 atime=1792289113\n"]
            for content in (b"path=../escape\n", b"GNU.sparse.map=0,1\n", b"linkpath=outside\n", b"size=536870913\n"):
                length = len(content) + 2
                while length != len(content) + len(str(length)) + 1:
                    length = len(content) + len(str(length)) + 1
                pax_payloads.append(str(length).encode("ascii") + b" " + content)
            pax_raw_variants = []
            following = tarfile.TarInfo(DATASET_ROOT + "/after-pax").tobuf(format=tarfile.GNU_FORMAT)
            for payload in pax_payloads:
                entry = tarfile.TarInfo(DATASET_ROOT + "/PaxHeaders/after-pax")
                entry.type, entry.size = tarfile.XHDTYPE, len(payload)
                pax_raw_variants.append(entry.tobuf(format=tarfile.GNU_FORMAT) + payload + bytes((-len(payload)) % 512) + following + bytes(1024))
            valid_payload = b"20 atime=1792289113\n"
            entry = tarfile.TarInfo(DATASET_ROOT + "/PaxHeaders/after-pax")
            entry.type, entry.size = tarfile.XHDTYPE, len(valid_payload)
            local_header = entry.tobuf(format=tarfile.GNU_FORMAT) + valid_payload + bytes((-len(valid_payload)) % 512)
            pax_raw_variants.extend((local_header + bytes(1024), local_header + local_header + following + bytes(1024)))
            entry.size = 65537
            pax_raw_variants.append(entry.tobuf(format=tarfile.GNU_FORMAT) + bytes(1024))
            for raw in pax_raw_variants:
                (source / ARCHIVES[-1]).write_bytes(gzip.compress(raw))
                (source / "SHA256SUMS.txt").write_text("".join(hashlib.sha256((source / name).read_bytes()).hexdigest() + "  " + name + "\n" for name in sorted(ARCHIVES)), encoding="ascii")
                pinned = fingerprint_external_source(paths, source_id, cancel=lambda: False)
                with self.assertRaises(ValueError):
                    list(iter_snumat_cases(paths, source_id, pinned, cancel=lambda: False))
                with self.assertRaises(ValueError):
                    list(iter_snumat_cases(paths, source_id, pinned, cancel=lambda: False, inventory_only=True))

    def test_member_reader_and_cancellation(self: TestExternalSources) -> None:
        """Read an owned member from its committed snapshot and reject stale bytes."""
        cache = Path(__file__).resolve().parents[2] / ".cache/external-source-tests"
        cache.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=cache) as temporary:
            root = Path(temporary)
            source = root / "dataset"
            source.mkdir()
            for name, payload in ROOT_MEMBERS.items():
                (source / name).write_bytes(payload)
            (root / "settings.toml").write_text('[srim]\nroot = ' + json.dumps(str(root / "unused")) + '\n[knowledge]\ndatabase_paths = ' + json.dumps([str(source)]) + '\n', encoding="utf-8")
            paths = get_project_paths(root)
            source_id = list_database_sources(paths)["sources"][0]["source_id"]
            token = SOURCE_DEADLINE.set(3600)
            try:
                with patch("src.knowledge.external_sources.time.monotonic", side_effect=[0.0] + [700.0] * 2000):
                    extended = fingerprint_external_source(paths, source_id, cancel=lambda: False)
                    self.assertEqual(len(list(iter_snumat_cases(paths, source_id, extended, cancel=lambda: False))), 1)
            finally:
                SOURCE_DEADLINE.reset(token)
            with patch("src.knowledge.external_sources.time.monotonic", side_effect=[0.0] + [700.0] * 2000):
                with self.assertRaises(ValueError):
                    fingerprint_external_source(paths, source_id, cancel=lambda: False)
            token = SOURCE_DEADLINE.set(0)
            try:
                with self.assertRaises(ValueError):
                    fingerprint_external_source(paths, source_id, cancel=lambda: False)
            finally:
                SOURCE_DEADLINE.reset(token)
            with self.assertRaises(InterruptedError):
                fingerprint_external_source(paths, source_id, cancel=lambda: True)
            fingerprints = fingerprint_external_source(paths, source_id, cancel=lambda: False)
            normal_inventory = list(iter_snumat_cases(paths, source_id, fingerprints, cancel=lambda: False))
            with patch("src.knowledge.external_sources.MAX_SPOOL", 0):
                inventory = list(iter_snumat_cases(paths, source_id, fingerprints, cancel=lambda: False, inventory_only=True))
            self.assertEqual([item["case_id"] for item in inventory], [item["case_id"] for item in normal_inventory])
            self.assertEqual(inventory[0]["locators"], normal_inventory[0]["locators"])
            self.assertEqual(inventory[0]["issues"], normal_inventory[0]["issues"])
            self.assertTrue(all(key.startswith("__") for key in inventory[0]["members"]))
            self.assertEqual(inventory[0]["members"]["__manifest__.json"], normal_inventory[0]["members"]["__manifest__.json"])
            self.assertFalse(any((root / "memory/knowledge/external/staging").glob("*/spool")))
            for invalid in (1, None, "true"):
                with self.assertRaises(ValueError):
                    list(iter_snumat_cases(paths, source_id, fingerprints, cancel=lambda: False, inventory_only=invalid))
            with self.assertRaises(InterruptedError):
                list(iter_snumat_cases(paths, source_id, fingerprints, cancel=lambda: True, inventory_only=True))
            token = SOURCE_DEADLINE.set(0)
            try:
                with self.assertRaises(ValueError):
                    list(iter_snumat_cases(paths, source_id, fingerprints, cancel=lambda: False, inventory_only=True))
            finally:
                SOURCE_DEADLINE.reset(token)
            case = normal_inventory[0]
            record = normalize_snumat_case(source_id, CASE_ID, case["members"], case["locators"])
            metadata_locator = deepcopy(case["locators"]["__manifest__.json"])
            record["issues"].append({"code": "conflicting", "field": "manifest.csv", "message": "Synthetic metadata conflict witness", "locators": [metadata_locator]})
            payload = json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
            digest = hashlib.sha256(payload).hexdigest()
            reference = {key: record[key] for key in ("schema_version", "kind", "source_id", "adapter_id", "adapter_version", "case_id")}
            reference["record_sha256"] = digest
            directory = root / "memory/knowledge/external"
            (directory / "objects").mkdir(exist_ok=True)
            (directory / "snapshots").mkdir(exist_ok=True)
            (directory / "objects" / (digest + ".json")).write_bytes(payload)
            snapshot = json.dumps({"source_fingerprints": fingerprints}, sort_keys=True, separators=(",", ":")).encode("utf-8")
            snapshot_digest = hashlib.sha256(snapshot).hexdigest()
            (directory / "snapshots" / (snapshot_digest + ".json")).write_bytes(snapshot)
            with sqlite3.connect(directory / "index.sqlite") as connection:
                connection.execute("CREATE TABLE bindings(source_id TEXT,case_id TEXT,record_sha256 TEXT,snapshot_sha256 TEXT)")
                connection.execute("INSERT INTO bindings VALUES(?,?,?,?)", (source_id, CASE_ID, digest, snapshot_digest))
            connection.close()
            self.assertEqual(read_external_evidence(paths, reference, case["locators"]["TRIM.IN"]), MEMBERS["TRIM.IN"])
            self.assertEqual(read_external_evidence(paths, reference, metadata_locator, max_bytes=32 * 1024 * 1024), METADATA_MEMBERS["manifest.csv"])
            with self.assertRaises(ValueError):
                read_external_evidence(paths, reference, case["locators"]["TRIM.IN"], max_bytes=1)
            bad = deepcopy(case["locators"]["TRIM.IN"])
            bad["member_sha256"] = "f" * 64
            with self.assertRaises(ValueError):
                read_external_evidence(paths, reference, bad)
            (source / ARCHIVES[1]).write_bytes(b"changed")
            with self.assertRaises(ValueError):
                read_external_evidence(paths, reference, case["locators"]["TRIM.IN"])

    def test_grouped_member_verification(self: TestExternalSources) -> None:
        """Hash/scan shared archives once and stream member hashes with strict bounds.

        self owns temporary gzip fixtures. Return None; propagate assertions.
        No settings, production sources or native simulation records are changed.
        """
        import time
        from src.knowledge.external_sources import _read_external_members
        cache = Path(__file__).resolve().parents[2] / ".cache/external-source-tests"
        cache.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=cache) as temporary:
            root = Path(temporary)
            members = {DATASET_ROOT + "/one": b"one payload", DATASET_ROOT + "/two": b"second payload"}
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
                for name, payload in members.items():
                    entry = tarfile.TarInfo(name)
                    entry.size = len(payload)
                    entry.pax_headers = {"mtime": "123.5"}
                    archive.addfile(entry, io.BytesIO(payload))
            compressed = stream.getvalue()
            path = root / ARCHIVES[1]
            path.write_bytes(compressed)
            fingerprints = {"files": [{"path": path.name, "size_bytes": len(compressed), "sha256": hashlib.sha256(compressed).hexdigest()}]}
            locators = [{"relative_path": path.name, "member_path": name, "size_bytes": len(payload), "member_sha256": hashlib.sha256(payload).hexdigest()} for name, payload in members.items()]
            gzip_reader, sha = gzip.GzipFile, hashlib.sha256
            with patch("src.knowledge.external_sources.gzip.GzipFile", wraps=gzip_reader) as scans, patch("src.knowledge.external_sources.hashlib.sha256", wraps=sha) as hashes:
                result = _read_external_members(root, fingerprints, locators + locators, max_bytes=0, collect=False, deadline=time.monotonic() + 10)
            self.assertEqual(len(result), 2)
            self.assertTrue(all(value is None for value in result.values()))
            self.assertEqual(scans.call_count, 1)
            self.assertEqual(hashes.call_count, 3)
            collected = _read_external_members(root, fingerprints, locators, max_bytes=1000, collect=True, deadline=time.monotonic() + 10)
            self.assertEqual({key[1]: value for key, value in collected.items()}, members)
            for updates in ({"member_sha256": "0" * 64}, {"size_bytes": 99}):
                with self.assertRaisesRegex(ValueError, "conflicting"):
                    _read_external_members(root, fingerprints, [locators[0], {**locators[0], **updates}], max_bytes=1000, collect=True, deadline=time.monotonic() + 10)
            with self.assertRaisesRegex(ValueError, "size cap"):
                _read_external_members(root, fingerprints, locators, max_bytes=1, collect=True, deadline=time.monotonic() + 10)
            with self.assertRaisesRegex(ValueError, "deadline"):
                _read_external_members(root, fingerprints, locators, max_bytes=0, collect=False, deadline=0)
            for updates in ({"member_sha256": "0" * 64}, {"size_bytes": 99}, {"member_path": DATASET_ROOT + "/missing"}):
                with self.assertRaises(ValueError):
                    _read_external_members(root, fingerprints, [{**locators[0], **updates}], max_bytes=1000, collect=True, deadline=time.monotonic() + 10)
            metadata = path.stat()
            changed_handle = unittest.mock.Mock(st_dev=metadata.st_dev, st_ino=metadata.st_ino, st_size=0, st_mtime_ns=metadata.st_mtime_ns, st_ctime_ns=metadata.st_ctime_ns)
            with patch("src.knowledge.external_sources.os.fstat", side_effect=[changed_handle]):
                with self.assertRaises((ValueError, OSError)):
                    _read_external_members(root, fingerprints, locators, max_bytes=0, collect=False, deadline=time.monotonic() + 10)
            variants = []
            for filename, kind in ((DATASET_ROOT + "/../escape", tarfile.REGTYPE), (DATASET_ROOT + "/sparse", tarfile.GNUTYPE_SPARSE), (DATASET_ROOT + "/link", tarfile.SYMTYPE), (DATASET_ROOT + "/global", tarfile.XGLTYPE)):
                entry = tarfile.TarInfo(filename)
                entry.type = kind
                variants.append(entry.tobuf(format=tarfile.GNU_FORMAT) + bytes(1024))
            duplicate = tarfile.TarInfo(DATASET_ROOT + "/duplicate").tobuf(format=tarfile.GNU_FORMAT)
            variants.extend((duplicate + duplicate + bytes(1024), duplicate, bytes(1024) + duplicate))
            for raw in variants:
                compressed = gzip.compress(raw)
                path.write_bytes(compressed)
                fingerprint = {"files": [{"path": path.name, "size_bytes": len(compressed), "sha256": hashlib.sha256(compressed).hexdigest()}]}
                with self.assertRaises(ValueError):
                    _read_external_members(root, fingerprint, locators, max_bytes=0, collect=False, deadline=time.monotonic() + 10)