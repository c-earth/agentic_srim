"""Verify capture ownership, bond thresholds, core estimates and resumable default recovery."""

from __future__ import annotations

from copy import deepcopy
import ctypes
from ctypes import wintypes
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, Mock, patch

from _fixtures import fake_bragg_run, make_test_project
from src.backend.resources import _hash_file, _write_json
from src.srim.cab_cache import load_cab_entry
from src.srim.cab_constants import SR_BONDS
from src.srim.cab_recovery import _capture_correction, _estimate_bond_count, _prepare_capture_runtime, _recover_core_value, _render_mock_compound, populate_default_cab, resolve_default_bond, resolve_default_core
from src.srim.defaults import read_sr_defaults


class TestCABRecovery(unittest.TestCase):
    """Use inherited unittest construction and isolated synthetic capture evidence."""

    def setUp(self: TestCABRecovery) -> None:
        """Create self's project and controlled native capture fixtures where needed; return None and register cleanup."""
        self.temporary = TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temporary.cleanup)
        self.paths, self.config, self.worker = make_test_project(Path(self.temporary.name).absolute())
        self.defaults = read_sr_defaults(self.paths)
        self.captures = {}
        if self._testMethodName in ("test_bond_threshold_recovery", "test_zero_initial_capture", "test_core_estimates"):
            bonds = SR_BONDS if self._testMethodName == "test_core_estimates" else SR_BONDS[:1]
            cases = [(1, bond, count) for bond in bonds for count in (1, 3, 2)]
            if self._testMethodName == "test_core_estimates":
                cases.extend((6, bond, 1) for bond in bonds)
            for z, bond, count in cases:
                runtime = _prepare_capture_runtime(self.paths, self.config, self.worker, self.defaults, _render_mock_compound(z, bond, count))
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
        runtime = _prepare_capture_runtime(self.paths, self.config, self.worker, self.defaults, _render_mock_compound(1, SR_BONDS[0], 1))
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
