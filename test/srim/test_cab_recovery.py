"""Verify capture ownership, bond thresholds, core estimates and resumable default recovery."""

from __future__ import annotations

from copy import deepcopy
import ctypes
from ctypes import wintypes
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, Mock, patch

from _fixtures import fake_bragg_run, make_test_project
from src.backend.records import CreateRequest, create_directories
from src.backend.resources import _hash_file, _write_json
from src.srim.cab_cache import load_cab_entry, publish_cab_entry
from src.srim.cab_constants import SR_BONDS
from src.srim.cab_recovery import _capture_correction, _estimate_bond_count, _prepare_capture_runtime, _recover_core_value, _render_mock_compound, populate_default_cab, resolve_default_bond, resolve_default_core
from src.srim.defaults import read_sr_defaults, switch_sr_defaults
from src.srim.storage import cleanup_sr_batch
from src.srim.metadata import read_simulation


class TestCABRecovery(unittest.TestCase):
    """Use inherited unittest construction and isolated synthetic capture evidence."""

    def test_core_publication_rejects_wrong_element(self: TestCABRecovery) -> None:
        """Reject self's equal-value oxygen dependency before publishing a carbon entry; return None."""
        sequence = [self.captures[(1, bond, count)] for bond in SR_BONDS for count in (1, 3, 2)]
        sequence.extend(self.captures[(z, bond, 1)] for z in (6, 8) for bond in SR_BONDS)
        with patch("src.srim.execution.subprocess.run", side_effect=fake_bragg_run), patch("src.srim.cab_recovery._capture_mock", side_effect=sequence):
            carbon = resolve_default_core(self.paths, self.config, self.worker, self.defaults, 6)
            oxygen = resolve_default_core(self.paths, self.config, self.worker, self.defaults, 8)
        original = load_cab_entry(self.paths, carbon.reference)
        invalid = deepcopy(original)
        invalid["dependency"]["entries"][0] = load_cab_entry(self.paths, oxygen.reference)["dependency"]["entries"][0]
        directory = self.paths.resources / "cab" / "core"
        before = sorted(str(path) for path in directory.rglob("*"))
        with self.assertRaisesRegex(ValueError, "Core lower-level dependencies mismatch"):
            publish_cab_entry(self.paths, carbon.reference.dataset, invalid)
        self.assertEqual(before, sorted(str(path) for path in directory.rglob("*")))
        self.assertEqual(load_cab_entry(self.paths, carbon.reference), original)

    def test_inactive_missing_recovery_rejected(self: TestCABRecovery) -> None:
        """Reject self's inactive missing bond/core despite restored matching installation bytes; return None."""
        executable = self.config.srim_root / "SR.exe"
        original = executable.read_bytes()
        executable.write_bytes(b"different generation")
        switch_sr_defaults(self.paths, self.config)
        executable.write_bytes(original)
        with patch("src.srim.cab_recovery.resolve_bragg") as bragg, patch("src.srim.cab_recovery._capture_mock") as capture:
            with self.assertRaisesRegex(ValueError, "inactive default generation"):
                resolve_default_bond(self.paths, self.config, self.worker, self.defaults, SR_BONDS[0])
            with self.assertRaisesRegex(ValueError, "inactive default generation"):
                resolve_default_core(self.paths, self.config, self.worker, self.defaults, 6)
        bragg.assert_not_called()
        capture.assert_not_called()

    def test_inactive_cached_recovery_allowed(self: TestCABRecovery) -> None:
        """Reuse self's inactive verified core/bond/H cache without recovery; return None."""
        sequence = [self.captures[(1, bond, count)] for bond in SR_BONDS for count in (1, 3, 2)]
        sequence.extend(self.captures[(6, bond, 1)] for bond in SR_BONDS)
        with patch("src.srim.execution.subprocess.run", side_effect=fake_bragg_run), patch("src.srim.cab_recovery._capture_mock", side_effect=sequence):
            core = resolve_default_core(self.paths, self.config, self.worker, self.defaults, 6)
            bond = resolve_default_bond(self.paths, self.config, self.worker, self.defaults, SR_BONDS[0])
        (self.config.srim_root / "SR.exe").write_bytes(b"different generation")
        switch_sr_defaults(self.paths, self.config)
        with patch("src.srim.cab_recovery.resolve_bragg") as bragg, patch("src.srim.cab_recovery._capture_mock") as capture:
            self.assertEqual(resolve_default_core(self.paths, self.config, self.worker, self.defaults, 6).reference, core.reference)
            self.assertEqual(resolve_default_bond(self.paths, self.config, self.worker, self.defaults, SR_BONDS[0]).reference, bond.reference)
            self.assertFalse(resolve_default_core(self.paths, self.config, self.worker, self.defaults, 1).calculated)
        bragg.assert_not_called()
        capture.assert_not_called()

    def test_default_switch_stops_next_capture(self: TestCABRecovery) -> None:
        """Stop self's second capture after a simulated generation switch, retaining the first; return None."""
        newer = deepcopy(self.defaults)
        newer["baseline_id"] = "0" * 64
        with patch("src.srim.execution.subprocess.run", side_effect=fake_bragg_run), patch("src.srim.cab_recovery._capture_correction", side_effect=self._fake_capture) as capture, patch("src.srim.cab_recovery.read_sr_defaults", side_effect=[self.defaults, self.defaults, self.defaults, newer]):
            with self.assertRaisesRegex(ValueError, "inactive default generation") as caught:
                resolve_default_bond(self.paths, self.config, self.worker, self.defaults, SR_BONDS[0])
        self.assertEqual(capture.call_count, 1)
        self.assertTrue(any("retained captures" in note and "correction.json" in note for note in caught.exception.__notes__))
        self.assertTrue(list(self.paths.simulation_memory.glob("*/output/raw/runtime/correction.json")))

    def test_default_switch_stops_missing_nested_bond(self: TestCABRecovery) -> None:
        """Stop self's missing nested bond after core dependency admission when defaults switch; return None."""
        newer = deepcopy(self.defaults)
        newer["baseline_id"] = "0" * 64
        with patch("src.srim.execution.subprocess.run", side_effect=fake_bragg_run), patch("src.srim.cab_recovery.read_sr_defaults", side_effect=[self.defaults, self.defaults, newer]), patch("src.srim.cab_recovery._capture_mock") as capture:
            with self.assertRaisesRegex(ValueError, "inactive default generation"):
                resolve_default_core(self.paths, self.config, self.worker, self.defaults, 6)
        capture.assert_not_called()
        self.assertTrue(list(self.paths.simulation_memory.glob("*/output/parsed/result.json")))

    def _fake_capture(self: TestCABRecovery, runtime: Path, compound_name: str, output_path: Path, *, timeout_seconds: float) -> str:
        """Write runtime's controlled correction at output_path for self's storage test.

        Validate compound_name/timeout_seconds and derive the reading from the
        diagnostic bond count. Return its decimal string, preserving native
        inputs and using a deterministic owned process identity.
        """
        self.assertEqual(compound_name, "Custom")
        self.assertGreater(timeout_seconds, 0)
        lines = (runtime / "Data/Compound.dat").read_text(encoding="cp1252").splitlines()
        position = lines.index("*Custom")
        count = sum(int(value) for value in lines[position + 2].split())
        z = int(lines[position + 1].split(",")[3].strip())
        correction = str(Decimal("0.4") * count) if z == 1 else "0.8"
        _write_json(output_path, {"compound": compound_name, "correction": correction, "process_id": 123, "recorded_at": datetime.now(timezone.utc).isoformat()})
        return correction

    def test_permanent_capture_evidence(self: TestCABRecovery) -> None:
        """Reload self's new bond entry after clearing its worker runtimes, then reject corrupted permanent evidence; return None."""
        with patch("src.srim.execution.subprocess.run", side_effect=fake_bragg_run), patch("src.srim.cab_recovery._capture_correction", side_effect=self._fake_capture):
            result = resolve_default_bond(self.paths, self.config, self.worker, self.defaults, SR_BONDS[0])
        entry = load_cab_entry(self.paths, result.reference)
        self.assertTrue(entry["captures"])
        for capture in entry["captures"]:
            self.assertEqual(capture["manifest"]["kind"], "simulation")
            self.assertEqual(read_simulation(self.paths, capture["manifest"]["id"])["engine"]["name"], "sr-capture")
        report = cleanup_sr_batch(self.paths, [self.worker.path])
        self.assertEqual(report.cleaned_workers, (self.worker.path,))
        self.assertFalse(report.retained_workers)
        self.assertEqual(load_cab_entry(self.paths, result.reference), entry)
        evidence = entry["captures"][0]["correction"]
        (self.paths.simulation_memory / evidence["id"] / evidence["path"]).write_bytes(b"changed")
        with self.assertRaises(ValueError):
            load_cab_entry(self.paths, result.reference)

    def test_failed_capture_record(self: TestCABRecovery) -> None:
        """Preserve self's failed GUI capture as a terminal record eligible for verified cleanup; return None."""
        with patch("src.srim.execution.subprocess.run", side_effect=fake_bragg_run), patch("src.srim.cab_recovery._capture_correction", side_effect=RuntimeError("controlled GUI failure")):
            with self.assertRaises(RuntimeError):
                resolve_default_bond(self.paths, self.config, self.worker, self.defaults, SR_BONDS[0])
        captures = [read_simulation(self.paths, runtime.name) for runtime in (self.worker.path / "sr").iterdir() if read_simulation(self.paths, runtime.name)["engine"]["name"] == "sr-capture"]
        self.assertEqual(len(captures), 1)
        self.assertEqual(captures[0]["status"], "failed")
        self.assertFalse(cleanup_sr_batch(self.paths, [self.worker.path]).retained_workers)

    def test_permanent_core_evidence(self: TestCABRecovery) -> None:
        """Reload self's carbon core and its 18 bond dependencies after worker cleanup; return None."""
        with patch("src.srim.execution.subprocess.run", side_effect=fake_bragg_run), patch("src.srim.cab_recovery._capture_correction", side_effect=self._fake_capture):
            result = resolve_default_core(self.paths, self.config, self.worker, self.defaults, 6)
        entry = load_cab_entry(self.paths, result.reference)
        self.assertEqual(len(entry["captures"]), 18)
        self.assertEqual(len(entry["dependency"]["entries"]), 19)
        self.assertEqual(cleanup_sr_batch(self.paths, [self.worker.path]).cleaned_workers, (self.worker.path,))
        self.assertEqual(load_cab_entry(self.paths, result.reference), entry)

    def setUp(self: TestCABRecovery) -> None:
        """Create self's project and controlled native capture fixtures where needed; return None and register cleanup."""
        temporary_root = Path(__file__).resolve().parents[2] / ".cache/srim-trim-closeout/tests"
        temporary_root.mkdir(parents=True, exist_ok=True)
        self.temporary = TemporaryDirectory(dir=temporary_root)
        self.addCleanup(self.temporary.cleanup)
        self.paths, self.config, self.worker = make_test_project(Path(self.temporary.name).absolute())
        self.defaults = read_sr_defaults(self.paths)
        self.captures = {}
        core_cases = ("test_core_estimates", "test_core_publication_rejects_wrong_element", "test_inactive_cached_recovery_allowed")
        if self._testMethodName in ("test_bond_threshold_recovery", "test_zero_initial_capture", *core_cases):
            bonds = SR_BONDS if self._testMethodName in core_cases else SR_BONDS[:1]
            cases = [(1, bond, count) for bond in bonds for count in (1, 3, 2)]
            if self._testMethodName in core_cases:
                cases.extend((6, bond, 1) for bond in bonds)
            if self._testMethodName == "test_core_publication_rejects_wrong_element":
                cases.extend((8, bond, 1) for bond in bonds)
            for z, bond, count in cases:
                runtime = _prepare_capture_runtime(self.paths, self.config, create_directories(self.paths, self.config, [CreateRequest("srim")]).completed[0], self.worker, self.defaults, _render_mock_compound(z, bond, count))
                correction = str(Decimal("0.4") * count) if z == 1 else "0.8"
                if self._testMethodName == "test_zero_initial_capture":
                    correction = "0"
                inputs = []
                for name in sorted(("SR.exe", "SR.exe.manifest", "SR.IN", "Data/ATOMDATA", "Data/Compound.dat", "Data/SCOEF03.dat", "Data/SNUC03.dat", "Data/VERSION")):
                    checksum, size = _hash_file(runtime / name)
                    inputs.append({"kind": "worker", "id": self.worker.path.name, "path": (runtime / name).relative_to(self.worker.path).as_posix(), "sha256": checksum, "size_bytes": size})
                timestamp = "2026-10-20T12:00:00+00:00"
                _write_json(runtime / "correction.json", {"compound": "Custom", "correction": correction, "process_id": 123, "recorded_at": timestamp})
                (runtime / "stdout.bin").write_bytes(b"controlled capture stdout")
                (runtime / "stderr.bin").write_bytes(b"")
                _write_json(runtime / "manifest.json", {"schema_version": 1, "status": "succeeded", "inputs_before": inputs, "inputs_after": inputs, "correction": correction, "process_id": 123, "started_at": timestamp, "finished_at": timestamp, "cleanup": True, "error": None})
                evidence = {"inputs": inputs}
                for role, name in (("manifest", "manifest.json"), ("correction", "correction.json"), ("stdout", "stdout.bin"), ("stderr", "stderr.bin")):
                    checksum, size = _hash_file(runtime / name)
                    evidence[role] = {"kind": "worker", "id": self.worker.path.name, "path": (runtime / name).relative_to(self.worker.path).as_posix(), "sha256": checksum, "size_bytes": size}
                self.captures[(z, bond, count)] = {"correction": correction, "evidence": evidence}

    def test_bond_threshold_recovery(self: TestCABRecovery) -> None:
        """Check self's count-one reuse, exact threshold/formula and failure without retry; return None."""
        bond = SR_BONDS[0]
        with patch("src.srim.execution.subprocess.run", side_effect=fake_bragg_run), patch("src.srim.cab_recovery._capture_mock", side_effect=[self.captures[(1, bond, count)] for count in (1, 3, 2)]) as capture:
            result = resolve_default_bond(self.paths, self.config, self.worker, self.defaults, bond)
        self.assertEqual([call.args[6] for call in capture.call_args_list], [1, 3, 2])
        self.assertEqual(Decimal(load_cab_entry(self.paths, result.reference)["value"]), Decimal("10"))
        with patch("src.srim.cab_recovery._capture_mock", side_effect=AssertionError("must reuse")):
            again = resolve_default_bond(self.paths, self.config, self.worker, self.defaults, bond)
        self.assertEqual(result.reference, again.reference)
        self.assertFalse(again.calculated)
        self.assertEqual(_estimate_bond_count("1.5"), 1)
        self.assertEqual(_estimate_bond_count("0.333333333333333333333333333333333333"), 4)
        failed = deepcopy(self.captures[(1, bond, 3)])
        failed["correction"] = "0.9"
        with patch("src.srim.cab_recovery._capture_mock", side_effect=[self.captures[(1, bond, 1)], failed, self.captures[(1, bond, 2)]]) as capture:
            with self.assertRaisesRegex(ValueError, "threshold failed"):
                resolve_default_bond(self.paths, self.config, self.worker, self.defaults, SR_BONDS[1])
        self.assertEqual(capture.call_count, 3)

    def test_zero_initial_capture(self: TestCABRecovery) -> None:
        """Check self's zero reading is retained and raises before further captures; return None."""
        bond = SR_BONDS[0]
        with patch("src.srim.execution.subprocess.run", side_effect=fake_bragg_run), patch("src.srim.cab_recovery._capture_mock", return_value=self.captures[(1, bond, 1)]) as capture:
            with self.assertRaises(ValueError) as caught:
                resolve_default_bond(self.paths, self.config, self.worker, self.defaults, bond)
        self.assertEqual(capture.call_count, 1)
        self.assertTrue(any("retained captures" in note for note in caught.exception.__notes__))
        self.assertFalse(list((self.paths.resources / "cab" / "bond" / "v1").glob("entries/*/*.json")))

    def test_core_estimates(self: TestCABRecovery) -> None:
        """Check self's 18 distinct captured estimates, mean/spread, H convention and negative rejection; return None."""
        sequence = [self.captures[(1, bond, count)] for bond in SR_BONDS for count in (1, 3, 2)]
        sequence.extend(self.captures[(6, bond, 1)] for bond in SR_BONDS)
        with patch("src.srim.execution.subprocess.run", side_effect=fake_bragg_run), patch("src.srim.cab_recovery._capture_mock", side_effect=sequence) as capture:
            result = resolve_default_core(self.paths, self.config, self.worker, self.defaults, 6)
        self.assertEqual(capture.call_count, 72)
        entry = load_cab_entry(self.paths, result.reference)
        self.assertEqual(len(entry["calculation"]["estimates"]), 18)
        self.assertEqual(Decimal(entry["value"]), Decimal("10"))
        self.assertEqual(Decimal(entry["calculation"]["spread"]), 0)
        hydrogen = resolve_default_core(self.paths, self.config, self.worker, self.defaults, 1)
        self.assertFalse(hydrogen.calculated)
        self.assertEqual(load_cab_entry(self.paths, hydrogen.reference)["value"], "0")
        observations = [{key: estimate[key] for key in ("bond", "correction", "capture_index")} for estimate in entry["calculation"]["estimates"]]
        observations[0]["correction"] = "0"
        strengths = {estimate["bond"]: estimate["bond_strength"] for estimate in entry["calculation"]["estimates"]}
        with self.assertRaisesRegex(ValueError, "Negative/nonfinite core estimate"):
            _recover_core_value(observations, "25", strengths)

    def test_capture_ownership_cleanup(self: TestCABRecovery) -> None:
        """Mock self's owned Windows processes to verify zero capture, cleanup and interruption; return None."""
        runtime = _prepare_capture_runtime(self.paths, self.config, create_directories(self.paths, self.config, [CreateRequest("srim")]).completed[0], self.worker, self.defaults, _render_mock_compound(1, SR_BONDS[0], 1))
        process = Mock()
        process.pid = 123
        process.poll.side_effect = [None, 0]
        prepare = subprocess.CompletedProcess([], 0, json.dumps({"tree_handle": 1, "item_bounds": [1, 1, 2, 2], "tree_bounds": [0, 0, 100, 100]}), "")
        reading = subprocess.CompletedProcess([], 0, '{"compound":"Custom","correction":"0"}', "")
        closed = subprocess.CompletedProcess([], 0, '{"closed":true}', "")
        owner = wintypes.DWORD(123)
        bounds = wintypes.RECT(0, 0, 100, 100)
        class_name = MagicMock()
        class_name.value = "TreeViewWndClass"
        class_name.__len__.return_value = 256
        user32 = MagicMock()
        with patch("src.srim.cab_recovery.subprocess.Popen", return_value=process) as launch, patch("src.srim.cab_recovery.subprocess.run", side_effect=[prepare, reading, closed]), patch("src.srim.cab_recovery.ctypes.WinDLL", return_value=user32), patch("src.srim.cab_recovery.wintypes.DWORD", return_value=owner), patch("src.srim.cab_recovery.wintypes.RECT", return_value=bounds), patch("src.srim.cab_recovery.ctypes.POINTER", return_value=ctypes.c_void_p), patch("src.srim.cab_recovery.ctypes.create_unicode_buffer", return_value=class_name):
            correction = _capture_correction(runtime, "Custom", runtime / "owned.json")
        self.assertEqual(correction, "0")
        self.assertEqual(launch.call_args.kwargs["cwd"], runtime)
        process.wait.assert_called_once_with(timeout=5.0)
        process.terminate.assert_not_called()
        process.poll.side_effect = [None, None]
        process.reset_mock()
        with patch("src.srim.cab_recovery.subprocess.Popen", return_value=process), patch("src.srim.cab_recovery.subprocess.run", side_effect=[KeyboardInterrupt, closed]):
            with self.assertRaises(KeyboardInterrupt):
                _capture_correction(runtime, "Custom", runtime / "interrupted.json")
        process.terminate.assert_called_once()

    def test_bulk_resume_and_callback(self: TestCABRecovery) -> None:
        """Check self's all-92/all-18 dispatch and callback failure preserves completed entries; return None."""
        hydrogen = resolve_default_core(self.paths, self.config, self.worker, self.defaults, 1)
        results = [replace(hydrogen, reference=replace(hydrogen.reference, entry_id=f"z{z:03d}"), calculated=z != 1) for z in range(1, 93)]
        events = []
        with patch("src.srim.cab_recovery.resolve_default_core", side_effect=results) as resolve:
            references = populate_default_cab(self.paths, self.config, self.worker, "core", progress=events.append)
        self.assertEqual(resolve.call_count, 92)
        self.assertEqual(len(references), 92)
        self.assertEqual(events[0]["state"], "cached")
        self.assertEqual(events[-1]["completed"], 92)
        with patch("src.srim.cab_recovery.resolve_default_bond", return_value=hydrogen) as resolve:
            self.assertEqual(len(populate_default_cab(self.paths, self.config, self.worker, "bond")), 18)
        self.assertEqual([call.args[4] for call in resolve.call_args_list], list(SR_BONDS))
        with patch("builtins.print", side_effect=RuntimeError("callback failure")):
            with self.assertRaises(RuntimeError) as caught:
                populate_default_cab(self.paths, self.config, self.worker, "core", entries=[1], progress=print)
        self.assertTrue(any("completed entries retained" in note for note in caught.exception.__notes__))
