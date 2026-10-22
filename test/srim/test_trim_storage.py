"""Verify exclusive TRIM bundle exports, complete evidence and retained failures."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from _fixtures import make_test_project
from test_trim_defaults import CACHE
from test_trim_inputs import TRIM_INSTALLATION_FILES, TRIM_REQUEST, TRIM_SELECTIONS
from src.srim.trim.defaults import switch_trim_defaults
from src.srim.trim.resolution import resolve_trim_inputs
from src.srim.trim.storage import cleanup_trim_batch, parse_trim_directory, write_trim_input_bundle


class TestTRIMStorage(unittest.TestCase):
    """Use inherited unittest construction with owned cache directories for export behavior."""

    def test_complete_bundle_export(self: TestTRIMStorage) -> None:
        """Export a fixture and verify every manifest hash and unchanged request; return None."""
        with TemporaryDirectory(dir=CACHE) as td:
            paths, config, worker = make_test_project(Path(td))
            for name, payload in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(payload)
            switch_trim_defaults(paths, config)
            bundle = resolve_trim_inputs(paths, config, worker, TRIM_REQUEST, TRIM_SELECTIONS)
            destination = Path(td) / "exported"
            self.assertEqual(write_trim_input_bundle(destination, bundle), destination)
            manifest = json.loads((destination / "manifest.json").read_bytes())
            self.assertEqual(manifest["engine"], "trim")
            self.assertEqual({item["path"] for item in manifest["files"]}, {p.relative_to(destination).as_posix() for p in destination.rglob("*") if p.is_file() and p.name != "manifest.json"})
            for item in manifest["files"]:
                payload = (destination / item["path"]).read_bytes()
                self.assertEqual((hashlib.sha256(payload).hexdigest(), len(payload)), (item["sha256"], item["size_bytes"]))
            self.assertEqual(json.loads((destination / "input/request.json").read_bytes()), TRIM_REQUEST)
            self.assertFalse((destination / "TRIM.exe").exists())
            self.assertFalse((destination / "TRIMAUTO").exists())

    def test_event_file_presence(self: TestTRIMStorage) -> None:
        """Export event-mode data with terminal EOF and ordinary inputs without it; return None."""
        with TemporaryDirectory(dir=CACHE) as td:
            paths, config, worker = make_test_project(Path(td))
            for name, payload in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(payload)
            switch_trim_defaults(paths, config)
            ordinary = resolve_trim_inputs(paths, config, worker, TRIM_REQUEST, TRIM_SELECTIONS)
            write_trim_input_bundle(Path(td) / "ordinary", ordinary)
            self.assertFalse((Path(td) / "ordinary/input/engine/TRIM.DAT").exists())
            request = deepcopy(TRIM_REQUEST)
            request["parameters"].update({"mode": "recoil-quick", "total_ions": 1, "events": [{"id": "T0001", "atomic_number": 14, "energy_eV": "1000", "position_angstrom": ["100", "0", "0"], "direction_cosines": ["1", "0", "0"]}]})
            event = resolve_trim_inputs(paths, config, worker, request, TRIM_SELECTIONS)
            write_trim_input_bundle(Path(td) / "event", event)
            self.assertTrue((Path(td) / "event/input/engine/TRIM.DAT").read_bytes().endswith(b"\x1a"))

    def test_destination_collision(self: TestTRIMStorage) -> None:
        """Race independent exports to one path and require one success without merging; return None."""
        with TemporaryDirectory(dir=CACHE) as td:
            paths, config, worker = make_test_project(Path(td))
            for name, payload in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(payload)
            switch_trim_defaults(paths, config)
            bundle = resolve_trim_inputs(paths, config, worker, TRIM_REQUEST, TRIM_SELECTIONS)
            destination = Path(td) / "same"
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(write_trim_input_bundle, destination, bundle) for _ in range(2)]
                successes, collisions = 0, 0
                for future in futures:
                    try:
                        self.assertEqual(future.result(), destination)
                        successes += 1
                    except FileExistsError:
                        collisions += 1
            self.assertEqual((successes, collisions), (1, 1))
            with self.assertRaises(FileExistsError):
                write_trim_input_bundle(destination, bundle)

    def test_unsafe_path_rejected(self: TestTRIMStorage) -> None:
        """Reject relative/traversal/linked destinations before writing anything; return None."""
        with TemporaryDirectory(dir=CACHE) as td:
            paths, config, worker = make_test_project(Path(td))
            for name, payload in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(payload)
            switch_trim_defaults(paths, config)
            bundle = resolve_trim_inputs(paths, config, worker, TRIM_REQUEST, TRIM_SELECTIONS)
            for destination in (Path("relative"), Path(td) / ".." / "escape"):
                with self.assertRaises(ValueError):
                    write_trim_input_bundle(destination, bundle)
            with patch("pathlib.Path.is_symlink", return_value=True):
                with self.assertRaises(OSError):
                    write_trim_input_bundle(Path(td) / "linked", bundle)

    def test_partial_write_retains_artifacts(self: TestTRIMStorage) -> None:
        """Inject a publication I/O failure and verify staging is identified/retained; return None."""
        with TemporaryDirectory(dir=CACHE) as td:
            paths, config, worker = make_test_project(Path(td))
            for name, payload in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(payload)
            switch_trim_defaults(paths, config)
            bundle = resolve_trim_inputs(paths, config, worker, TRIM_REQUEST, TRIM_SELECTIONS)
            with patch("src.srim.trim.storage._read_regular_bytes", side_effect=PermissionError("verification blocked")):
                with self.assertRaises(PermissionError) as caught:
                    write_trim_input_bundle(Path(td) / "failed", bundle)
            self.assertFalse((Path(td) / "failed").exists())
            staging = list(Path(td).glob(".trim-input-*"))
            self.assertEqual(len(staging), 1)
            self.assertTrue((staging[0] / "input/request.json").is_file())
            self.assertTrue(any(str(staging[0]) in note for note in caught.exception.__notes__))

    def test_standalone_export(self: TestTRIMStorage) -> None:
        """Import native fixtures without inventing request or execution provenance; return None."""
        source = (Path(__file__).parent / "fixtures/trim/2013").resolve()
        with TemporaryDirectory(dir=CACHE) as td:
            destination = Path(td) / "parsed"
            self.assertEqual(parse_trim_directory(source, destination), destination)
            report = json.loads((destination / "parsed/results.json").read_bytes())
            self.assertTrue(report["complete"])
            self.assertFalse(report["consistency"]["request_checked"])
            self.assertFalse((destination / "request.json").exists())
            for path in source.rglob("*"):
                if path.is_file():
                    self.assertEqual(path.read_bytes(), (destination / "raw/runtime" / path.relative_to(source)).read_bytes())
            with self.assertRaises(FileExistsError):
                parse_trim_directory(source, destination)

    def test_unsafe_import_paths(self: TestTRIMStorage) -> None:
        """Reject imports overlapping their source or using relative paths; return None."""
        source = (Path(__file__).parent / "fixtures/trim/2013").resolve()
        with self.assertRaises(ValueError):
            parse_trim_directory(source, source / "nested")
        with self.assertRaises(ValueError):
            parse_trim_directory(Path("relative"), source)

    def test_cleanup_preservation_guard(self: TestTRIMStorage) -> None:
        """Retain uninitialized TRIM runtime data during cleanup; return None."""
        with TemporaryDirectory(dir=CACHE) as td:
            paths, config, worker = make_test_project(Path(td))
            unknown = worker.path / "trim/unknown"
            unknown.mkdir()
            (unknown / "result.txt").write_bytes(b"must retain")
            report = cleanup_trim_batch(paths, [worker.path])
            self.assertEqual(len(report.retained_workers), 1)
            self.assertTrue((unknown / "result.txt").exists())

    def test_live_source_change_rejected(self: TestTRIMStorage) -> None:
        """Reject an import whose source inventory changes during copying; return None."""
        from src.srim.common.files import _inventory
        source = (Path(__file__).parent / "fixtures/trim/2013").resolve()
        original = _inventory(source, source)
        with TemporaryDirectory(dir=CACHE) as td:
            destination = Path(td) / "changed"
            with patch("src.srim.trim.storage._inventory", side_effect=[original, original, []]):
                with self.assertRaisesRegex(ValueError, "source changed"):
                    parse_trim_directory(source, destination)
            self.assertFalse(destination.exists())
            self.assertEqual(len(list(Path(td).glob(".trim-import-*"))), 1)
