"""Verify selection snapshots, sparse fallbacks, pinned defaults and effective-input reconstruction."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
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

    def setUp(self: TestSRResolution) -> None:
        """Create self's temporary project/worker/request/selections; return None and register cleanup."""
        self.temporary = TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temporary.cleanup)
        self.paths, self.config, self.worker = make_test_project(Path(self.temporary.name).absolute())
        self.request, self.selections = make_test_request(), make_test_selections()

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
