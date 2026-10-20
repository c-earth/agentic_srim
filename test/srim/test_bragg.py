"""Verify Bragg reference inputs, automatic versions, reuse, bulk progress and failures."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from _fixtures import fake_bragg_run, make_test_project, make_test_request, make_test_selections
from src.srim.bragg import _make_bragg_inputs, populate_bragg, resolve_bragg
from src.srim.cab_cache import load_cab_entry
from src.srim.defaults import read_sr_defaults
from src.srim.material import parse_atomdata
from src.srim.resolution import _make_dependency_inputs, resolve_sr_inputs


class TestBragg(unittest.TestCase):
    """Use inherited unittest construction with pinned synthetic dependency bundles."""

    def setUp(self: TestBragg) -> None:
        """Create self's temporary project/worker and no-bond dependency bundle; return None and register cleanup."""
        self.temporary = TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temporary.cleanup)
        self.paths, self.config, self.worker = make_test_project(Path(self.temporary.name).absolute())
        self.request, self.selections = make_test_request(), make_test_selections()
        self.request["parameters"]["target"]["bonds"] = {}
        self.bundle = resolve_sr_inputs(self.paths, self.config, self.worker, self.request, self.selections)
        self.inputs = _make_dependency_inputs(self.bundle.resolved["defaults"], self.bundle.resolved["resources"], parse_atomdata(dict(self.bundle.snapshots)["resources/atomdata/effective"]), dict(self.bundle.engine_files))

    def test_reference_simulation(self: TestBragg) -> None:
        """Verify self's gas/H125M/natural-mass/units7 reference and successful evidence; return None."""
        bundle = _make_bragg_inputs(self.inputs, 6)
        parameters = bundle.effective_request["parameters"]
        self.assertEqual(parameters["projectile"], {"atomic_number": 1, "mass_amu": "1.008"})
        self.assertEqual(parameters["energy"]["values_keV"], ["126.000"])
        self.assertEqual(parameters["target"]["elements"][0]["mass_amu"], "12.011")
        self.assertEqual(parameters["target"]["phase"], "gas")
        self.assertEqual(parameters["stopping_units"], 7)
        with patch("src.srim.execution.subprocess.run", side_effect=fake_bragg_run) as run:
            result = resolve_bragg(self.paths, self.config, self.worker, self.inputs, 6)
        self.assertEqual(run.call_count, 1)
        entry = load_cab_entry(self.paths, result.reference)
        self.assertEqual(entry["value"], "2.500E+01")
        self.assertTrue(result.calculated)
        self.assertEqual(len(entry["simulations"]), 1)

    def test_automatic_versions(self: TestBragg) -> None:
        """Check self's same/changed effective dependencies, local edits and unchanged aliases; return None."""
        before = read_sr_defaults(self.paths)
        with patch("src.srim.execution.subprocess.run", side_effect=fake_bragg_run) as run:
            first = resolve_bragg(self.paths, self.config, self.worker, self.inputs, 6)
            again = resolve_bragg(self.paths, self.config, self.worker, self.inputs, 6)
            second_element = resolve_bragg(self.paths, self.config, self.worker, self.inputs, 8)
            self.assertEqual(run.call_count, 2)
            self.assertEqual(first.reference, again.reference)
            self.assertFalse(again.calculated)
            self.assertEqual(first.reference.dataset, second_element.reference.dataset)
            selections = deepcopy(self.selections)
            selections["atomdata"]["overrides"] = [{"operation": "set", "key": 6, "value": {"natural_mass_amu": "13.003"}}]
            bundle = resolve_sr_inputs(self.paths, self.config, self.worker, self.request, selections)
            inputs = _make_dependency_inputs(bundle.resolved["defaults"], bundle.resolved["resources"], parse_atomdata(dict(bundle.snapshots)["resources/atomdata/effective"]), dict(bundle.engine_files))
            changed = resolve_bragg(self.paths, self.config, self.worker, inputs, 6)
        self.assertNotEqual(first.reference.dataset, changed.reference.dataset)
        self.assertEqual(read_sr_defaults(self.paths), before)
        with self.assertRaises(ValueError):
            _make_bragg_inputs(replace(self.inputs, engine_files=self.inputs.engine_files + (self.inputs.engine_files[0],)), 6)

    def test_bulk_reuse_progress(self: TestBragg) -> None:
        """Check self's all-92 dispatch, ordered progress and retained callback completion; return None."""
        with patch("src.srim.execution.subprocess.run", side_effect=fake_bragg_run):
            first = resolve_bragg(self.paths, self.config, self.worker, self.inputs, 1)
        results = [replace(first, reference=replace(first.reference, entry_id=f"z{z:03d}"), calculated=z != 1) for z in range(1, 93)]
        events = []
        with patch("src.srim.bragg.resolve_bragg", side_effect=results) as resolve:
            references = populate_bragg(self.paths, self.config, self.worker, self.inputs, progress=events.append)
        self.assertEqual(resolve.call_count, 92)
        self.assertEqual([call.args[4] for call in resolve.call_args_list], list(range(1, 93)))
        self.assertEqual(events[0]["state"], "cached")
        self.assertEqual(events[-1]["completed"], 92)
        self.assertEqual(len(references), 92)
        with patch("src.srim.bragg.resolve_bragg", return_value=first), patch("builtins.print", side_effect=RuntimeError("callback failed")):
            with self.assertRaises(RuntimeError) as caught:
                populate_bragg(self.paths, self.config, self.worker, self.inputs, atomic_numbers=[1], progress=print)
        self.assertTrue(any("completed entries retained" in note for note in caught.exception.__notes__))

    def test_dependency_failure(self: TestBragg) -> None:
        """Check self's timeout/interruption evidence, no publication and invalid preflight; return None."""
        with patch("src.srim.execution.subprocess.run", side_effect=subprocess.TimeoutExpired("SRModule", 0.01)):
            with self.assertRaises(RuntimeError):
                resolve_bragg(self.paths, self.config, self.worker, self.inputs, 6, timeout_seconds=0.01)
        directory = self.paths.resources / "cab" / "bragg" / "v1"
        self.assertEqual(list(directory.glob("entries/*/*.json")), [])
        with patch("src.srim.execution.subprocess.run", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                resolve_bragg(self.paths, self.config, self.worker, self.inputs, 6)
        with self.assertRaises(ValueError):
            populate_bragg(self.paths, self.config, self.worker, self.inputs, atomic_numbers=[1, 1])
