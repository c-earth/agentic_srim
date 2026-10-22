"""Verify selection snapshots, sparse fallbacks, pinned defaults and effective-input reconstruction."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from functools import partial
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from _fixtures import fake_bragg_run, make_test_project, make_test_request, make_test_selections
from src.backend.records import CreateRequest, create_directories
from src.backend.resources import ResourceReference, verify_resource
from src.srim.cab import _encode_payload
from src.srim.defaults import read_sr_defaults, switch_sr_defaults
from src.srim.execution import run_sr_simulation, verify_sr_runtime
from src.srim.metadata import read_simulation
from src.srim.resolution import resolve_sr_inputs, verify_sr_resolution
from src.srim.runtime import prepare_sr_simulation


class TestSRResolution(unittest.TestCase):
    """Use inherited unittest construction with isolated synthetic project and intent."""

    def test_complete_material_progress(self: TestSRResolution) -> None:
        """Check self's dataset, override, fallback and cached contributions reach their total; return None."""
        request = deepcopy(self.request)
        request["parameters"]["target"]["elements"] = [{"atomic_number": 1, "stoichiometry": "1"}]
        request["parameters"]["target"]["bonds"] = {"H-H": "1"}
        selections = deepcopy(self.selections)
        selections["bond"] = {"source": {"kind": "inline", "data": json.loads(_encode_payload("bond", {"H-H": "10"}))}, "overrides": [], "create_version": False}
        for core_mode in ("default", "sparse", "override", "removed"):
            with self.subTest(core=core_mode):
                selections["core"] = deepcopy(self.selections["core"])
                if core_mode == "sparse":
                    selections["core"] = {"source": {"kind": "inline", "data": json.loads(_encode_payload("core", {}))}, "overrides": [], "create_version": False}
                elif core_mode == "override":
                    selections["core"]["overrides"] = [{"operation": "set", "key": "H", "value": "2"}]
                elif core_mode == "removed":
                    selections["core"]["overrides"] = [{"operation": "remove", "key": "H"}]
                events: list[dict] = []
                with patch("src.srim.execution.subprocess.run", side_effect=fake_bragg_run):
                    bundle = resolve_sr_inputs(self.paths, self.config, self.worker, request, selections, progress=events.append)
                self.assertEqual([event["kind"] for event in events], ["bond", "bragg", "core"])
                self.assertEqual([event["completed"] for event in events], [1, 2, 3])
                self.assertEqual([event["total"] for event in events], [3, 3, 3])
                self.assertEqual(events[0]["state"], "resolved")
                self.assertEqual(events[1]["state"], "calculated" if core_mode == "default" else "cached")
                self.assertEqual(events[2]["state"], {"default": "cached", "sparse": "fallback", "override": "resolved", "removed": "fallback"}[core_mode])
                self.assertEqual(verify_sr_resolution(self.paths, request, selections, bundle.resolved, dict(bundle.snapshots)), bundle.effective_request)
        from test_cab_recovery import TestCABRecovery
        for expected in ("calculated", "cached"):
            events = []
            with patch("src.srim.execution.subprocess.run", side_effect=fake_bragg_run), patch("src.srim.cab_recovery._capture_correction", side_effect=partial(TestCABRecovery._fake_capture, self)):
                bundle = resolve_sr_inputs(self.paths, self.config, self.worker, request, self.selections, progress=events.append)
            self.assertEqual([event["state"] for event in events], [expected, "cached", "cached"])
            self.assertEqual(events[-1]["completed"], 3)
            self.assertEqual(verify_sr_resolution(self.paths, request, self.selections, bundle.resolved, dict(bundle.snapshots)), bundle.effective_request)

    def test_skipped_material_progress(self: TestSRResolution) -> None:
        """Check self's unused contributions complete without dependencies and explicit modes stay silent; return None."""
        for bonds in ({}, {"H-O": "0"}, {"H-O": "2"}):
            with self.subTest(bonds=bonds):
                request, selections = deepcopy(self.request), deepcopy(self.selections)
                request["parameters"]["target"]["bonds"] = bonds
                selections["bond"] = {"source": {"kind": "inline", "data": json.loads(_encode_payload("bond", {}))}, "overrides": [], "create_version": False}
                events: list[dict] = []
                with patch("src.srim.bragg.resolve_bragg", side_effect=AssertionError("unneeded dependency")):
                    bundle = resolve_sr_inputs(self.paths, self.config, self.worker, request, selections, progress=events.append)
                total = len(bonds) + 4
                self.assertEqual([event["completed"] for event in events], list(range(1, total + 1)))
                self.assertTrue(all(event["state"] == "skipped" for event in events[len(bonds):]))
                self.assertEqual(bundle.resolved["cab"]["applied_factor"], "1.0")
                if bonds:
                    self.assertEqual(events[0]["state"], "fallback")
        for mode in ("none", "supplied"):
            request = deepcopy(self.request)
            request["parameters"]["correction"] = {"mode": mode, "applied_factor": "1.0"}
            if mode == "supplied":
                request["parameters"]["correction"].update(source="test", reason="explicit factor")
            events = []
            resolve_sr_inputs(self.paths, self.config, self.worker, request, self.selections, progress=events.append)
            self.assertEqual(events, [])

    def test_material_progress_callback_failure(self: TestSRResolution) -> None:
        """Propagate self's callback failure after calculated Bragg evidence remains preserved; return None."""
        request, selections = deepcopy(self.request), deepcopy(self.selections)
        request["parameters"]["target"]["elements"] = [{"atomic_number": 1, "stoichiometry": "1"}]
        request["parameters"]["target"]["bonds"] = {"H-H": "1"}
        selections["bond"] = {"source": {"kind": "inline", "data": json.loads(_encode_payload("bond", {"H-H": "10"}))}, "overrides": [], "create_version": False}
        callback = unittest.mock.Mock(side_effect=[None, RuntimeError("callback failure")])
        with patch("src.srim.execution.subprocess.run", side_effect=fake_bragg_run):
            with self.assertRaisesRegex(RuntimeError, "callback failure"):
                resolve_sr_inputs(self.paths, self.config, self.worker, request, selections, progress=callback)
        self.assertTrue(list(self.paths.simulation_memory.glob("*/output/parsed/result.json")))

    def test_preparation_progress_forwarding(self: TestSRResolution) -> None:
        """Verify self's preparation forwards the complete correction progress without running native code; return None."""
        request = deepcopy(self.request)
        request["parameters"]["target"]["bonds"] = {}
        created = create_directories(self.paths, self.config, [CreateRequest("srim")]).completed[0]
        events: list[dict] = []
        prepare_sr_simulation(self.paths, self.config, created, self.worker, request, self.selections, progress=events.append)
        self.assertEqual(events[-1]["completed"], events[-1]["total"])
        self.assertTrue(all(event["state"] == "skipped" for event in events))

    def setUp(self: TestSRResolution) -> None:
        """Create self's temporary project/worker/request/selections; return None and register cleanup."""
        temporary_root = Path(__file__).resolve().parents[2] / ".cache/srim-trim-closeout/tests"
        temporary_root.mkdir(parents=True, exist_ok=True)
        self.temporary = TemporaryDirectory(dir=temporary_root)
        self.addCleanup(self.temporary.cleanup)
        self.paths, self.config, self.worker = make_test_project(Path(self.temporary.name).absolute())
        self.request, self.selections = make_test_request(), make_test_selections()

    def test_shared_correction_preserves_sr_result(self: TestSRResolution) -> None:
        """Verify self's existing SR path calls shared correction without changing replay; return None."""
        from src.srim.resolution import resolve_material_correction
        request = deepcopy(self.request)
        request["parameters"]["target"]["bonds"] = {}
        with patch("src.srim.resolution.resolve_material_correction", wraps=resolve_material_correction) as shared:
            bundle = resolve_sr_inputs(self.paths, self.config, self.worker, request, self.selections)
        self.assertEqual(shared.call_count, 1)
        self.assertEqual(bundle.resolved["cab"]["applied_factor"], "1.0")
        self.assertEqual(verify_sr_resolution(self.paths, request, self.selections, bundle.resolved, dict(bundle.snapshots)), bundle.effective_request)

    def test_sources_and_version_creation(self: TestSRResolution) -> None:
        """Check self's filesystem/inline/registered sources and explicit immutable publications; return None."""
        request = deepcopy(self.request)
        request["parameters"]["target"]["bonds"] = {}
        source = self.paths.root / "strength.json"
        source.write_bytes(_encode_payload("bond", {}))
        selections = deepcopy(self.selections)
        selections["bond"] = {"source": {"kind": "file", "path": str(source)}, "overrides": [], "create_version": True}
        bundle = resolve_sr_inputs(self.paths, self.config, self.worker, request, selections)
        role = bundle.resolved["resources"]["bond"]
        self.assertIsNotNone(role["published"])
        self.assertEqual(verify_resource(self.paths, ResourceReference(**role["published"])).read_bytes(), _encode_payload("bond", {}))
        source.write_bytes(b"changed mutable source")
        self.assertEqual(verify_sr_resolution(self.paths, request, selections, bundle.resolved, dict(bundle.snapshots)), bundle.effective_request)
        selections["bond"] = {"version": role["published"]["version"], "overrides": [], "create_version": False}
        again = resolve_sr_inputs(self.paths, self.config, self.worker, request, selections)
        self.assertEqual(again.effective_request, bundle.effective_request)
        created = create_directories(self.paths, self.config, [CreateRequest("srim")]).completed[0]
        runtime = prepare_sr_simulation(self.paths, self.config, created, self.worker, request, selections)
        self.assertEqual(verify_sr_runtime(self.paths, created.path.name, runtime), again.effective_request)
        with patch("src.srim.execution.subprocess.run", side_effect=fake_bragg_run):
            metadata = run_sr_simulation(self.paths, self.config, created.path.name, runtime)
        self.assertEqual(metadata["status"], "succeeded")
        self.assertEqual(metadata["schema_version"], 2)
        for invalid in ("v01", "standard", "https://example.com"):
            invalid_selections = deepcopy(selections)
            invalid_selections["bond"] = {"version": invalid, "overrides": [], "create_version": False}
            with self.assertRaises(ValueError):
                resolve_sr_inputs(self.paths, self.config, self.worker, request, invalid_selections)

    def test_override_fallbacks(self: TestSRResolution) -> None:
        """Check self's sparse user and explicitly removed default core entries use Bragg fallback; return None."""
        request = deepcopy(self.request)
        request["parameters"]["target"]["elements"] = [{"atomic_number": 1, "stoichiometry": "1"}]
        request["parameters"]["target"]["bonds"] = {"H-H": "1"}
        selections = deepcopy(self.selections)
        selections["bond"] = {"source": {"kind": "inline", "data": json.loads(_encode_payload("bond", {"H-H": "10"}))}, "overrides": [], "create_version": False}
        selections["core"]["overrides"] = [{"operation": "remove", "key": "H"}]
        with patch("src.srim.execution.subprocess.run", side_effect=fake_bragg_run):
            removed = resolve_sr_inputs(self.paths, self.config, self.worker, request, selections)
        self.assertEqual(Decimal(removed.resolved["cab"]["applied_factor"]), Decimal("1.4"))
        self.assertEqual(removed.resolved["cab"]["core_entries"][0]["origin"], "bragg-fallback")
        self.assertEqual(removed.resolved["cab"]["omissions"][0]["reason"], "removed-entry")
        selections["core"] = {"source": {"kind": "inline", "data": json.loads(_encode_payload("core", {}))}, "overrides": [], "create_version": False}
        sparse = resolve_sr_inputs(self.paths, self.config, self.worker, request, selections)
        self.assertEqual(sparse.resolved["cab"]["omissions"][0]["reason"], "missing-user-entry")
        self.assertEqual(Decimal(sparse.resolved["cab"]["applied_factor"]), Decimal("1.4"))
        selections["core"] = deepcopy(self.selections["core"])
        convention = resolve_sr_inputs(self.paths, self.config, self.worker, request, selections)
        self.assertEqual(convention.resolved["cab"]["core_entries"][0]["origin"], "convention")
        self.assertEqual(Decimal(convention.resolved["cab"]["applied_factor"]), Decimal("0.4"))

    def test_no_effective_bonds(self: TestSRResolution) -> None:
        """Check self's zero selected strengths and zero counts short-circuit without calculations; return None."""
        selections = deepcopy(self.selections)
        selections["bond"] = {"source": {"kind": "inline", "data": json.loads(_encode_payload("bond", {}))}, "overrides": [], "create_version": False}
        with patch("src.srim.bragg.resolve_bragg", side_effect=AssertionError("unnecessary Bragg")), patch("src.srim.cab_recovery.resolve_default_core", side_effect=AssertionError("unnecessary core")), patch("src.srim.cab_recovery.resolve_default_bond", side_effect=AssertionError("unnecessary bond")):
            bundle = resolve_sr_inputs(self.paths, self.config, self.worker, self.request, selections)
            request = deepcopy(self.request)
            request["parameters"]["target"]["bonds"] = {"H-O": "0"}
            zero = resolve_sr_inputs(self.paths, self.config, self.worker, request, self.selections)
        self.assertEqual(bundle.resolved["cab"]["applied_factor"], "1.0")
        self.assertEqual(bundle.resolved["cab"]["core_entries"], [])
        self.assertEqual(bundle.resolved["cab"]["bragg_entries"], [])
        self.assertEqual(zero.resolved["cab"]["omissions"][0]["reason"], "zero-count")

    def test_old_default_missing_entry(self: TestSRResolution) -> None:
        """Check self's old default missing core errors without recovery or mutation; return None."""
        old = read_sr_defaults(self.paths)["resources"]["core"]
        (self.config.srim_root / "SR.exe").write_bytes(b"changed GUI installation")
        switch_sr_defaults(self.paths, self.config)
        selections = deepcopy(self.selections)
        selections["core"]["version"] = old["version"]
        selections["bond"] = {"source": {"kind": "inline", "data": json.loads(_encode_payload("bond", {"H-C": "10"}))}, "overrides": [], "create_version": False}
        request = deepcopy(self.request)
        request["parameters"]["target"]["elements"] = [{"atomic_number": 6, "stoichiometry": "1"}]
        request["parameters"]["target"]["bonds"] = {"H-C": "1"}
        with patch("src.srim.execution.subprocess.run", side_effect=fake_bragg_run), patch("src.srim.cab_recovery.resolve_default_core", side_effect=AssertionError("must not extend inactive defaults")):
            with self.assertRaisesRegex(ValueError, "inactive default core"):
                resolve_sr_inputs(self.paths, self.config, self.worker, request, selections)
        self.assertEqual(len(list(verify_resource(self.paths, ResourceReference(**old)).parent.glob("entries/*/*.json"))), 1)

    def test_pinned_defaults(self: TestSRResolution) -> None:
        """Check self's saved generation reconstructs after alias switching without any alias lookup; return None."""
        request = deepcopy(self.request)
        request["parameters"]["target"]["bonds"] = {}
        bundle = resolve_sr_inputs(self.paths, self.config, self.worker, request, self.selections)
        (self.config.srim_root / "SR.exe").write_bytes(b"new baseline")
        newer = switch_sr_defaults(self.paths, self.config)
        self.assertNotEqual(bundle.resolved["defaults"]["generation"]["baseline_id"], newer["baseline_id"])
        with patch("src.srim.resolution.read_sr_defaults", side_effect=AssertionError("must not resolve aliases again")):
            self.assertEqual(verify_sr_resolution(self.paths, request, self.selections, bundle.resolved, dict(bundle.snapshots)), bundle.effective_request)

    def test_resolution_reconstruction(self: TestSRResolution) -> None:
        """Check self's original/effective split and reject tampered arithmetic, snapshots and provenance; return None."""
        request = deepcopy(self.request)
        request["parameters"]["target"]["bonds"] = {}
        bundle = resolve_sr_inputs(self.paths, self.config, self.worker, request, self.selections)
        self.assertNotEqual(bundle.request["parameters"]["target"], bundle.effective_request["parameters"]["target"])
        for field in ("parameters", "cab", "target_provenance"):
            changed = deepcopy(bundle.resolved)
            if field == "parameters":
                changed[field]["projectile"]["mass_amu"] = "2"
            elif field == "cab":
                changed[field]["applied_factor"] = "2"
            else:
                changed[field]["source"] = "compound"
            with self.assertRaises(ValueError):
                verify_sr_resolution(self.paths, request, self.selections, changed, dict(bundle.snapshots))
        snapshots = dict(bundle.snapshots)
        snapshots["resources/atomdata/effective"] += b"tampered"
        with self.assertRaises(ValueError):
            verify_sr_resolution(self.paths, request, self.selections, bundle.resolved, snapshots)
        request["parameters"]["correction"] = {"mode": "none", "applied_factor": "1.0"}
        del request["parameters"]["target"]["bonds"]
        explicit = resolve_sr_inputs(self.paths, self.config, self.worker, request, self.selections)
        self.assertEqual(explicit.resolved["cab"], request["parameters"]["correction"])
