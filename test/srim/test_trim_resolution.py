"""Verify pinned TRIM layer construction, resource precedence and offline CAB replay."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from _fixtures import fake_bragg_run, make_test_project
from test_trim_inputs import TRIM_INSTALLATION_FILES, TRIM_REQUEST, TRIM_SELECTIONS
from test_trim_defaults import CACHE
from src.srim.cab import _encode_payload
from src.srim.defaults import switch_sr_defaults
from src.srim.trim_defaults import switch_trim_defaults
from src.srim.trim_resolution import resolve_trim_inputs, verify_trim_resolution


class TestTRIMResolution(unittest.TestCase):
    """Use inherited unittest construction and owned cache projects for layered contracts."""

    def test_complete_layer_progress(self: TestTRIMResolution) -> None:
        """Check self's two layers retain tags, complete counters and replay for skipped corrections; return None."""
        with TemporaryDirectory(dir=CACHE) as td:
            paths, config, worker = make_test_project(Path(td))
            for name, payload in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(payload)
            switch_sr_defaults(paths, config)
            switch_trim_defaults(paths, config)
            request, selections = deepcopy(TRIM_REQUEST), deepcopy(TRIM_SELECTIONS)
            layer = request["parameters"]["layers"][0]
            layer["correction"] = {"mode": "derived"}
            layer["material"]["bonds"] = {}
            request["parameters"]["layers"].append(deepcopy(layer))
            events: list[dict] = []
            bundle = resolve_trim_inputs(paths, config, worker, request, selections, progress=events.append)
            self.assertEqual(verify_trim_resolution(bundle), bundle.effective_request)
            self.assertEqual(set(event["layer_index"] for event in events), {1, 2})
            for index in (1, 2):
                selected = [event for event in events if event["layer_index"] == index]
                self.assertEqual([event["completed"] for event in selected], list(range(1, selected[-1]["total"] + 1)))
                self.assertTrue(all(event["state"] == "skipped" for event in selected))

    def test_multilayer_direct_and_compound(self: TestTRIMResolution) -> None:
        """Resolve mixed direct/atomic/mass compound layers with stable columns; return None."""
        with TemporaryDirectory(dir=CACHE) as td:
            paths, config, worker = make_test_project(Path(td))
            for name, payload in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(payload)
            switch_trim_defaults(paths, config)
            request, selections = deepcopy(TRIM_REQUEST), deepcopy(TRIM_SELECTIONS)
            for index in (1, 2):
                layer = deepcopy(request["parameters"]["layers"][0])
                layer["material"] = {"source": "compound", "record_index": index, "phase": "gas" if index == 2 else "solid"}
                request["parameters"]["layers"].append(layer)
            selections["compound"] = {"version": "default", "overrides": [], "create_version": False}
            bundle = resolve_trim_inputs(paths, config, worker, request, selections)
            native = bundle.effective_request["parameters"]
            self.assertEqual(len(native["atoms"]), 5)
            self.assertEqual([layer["density_g_cm3"] for layer in native["layers"]], ["2.3212", "1.0", "1.1"])
            self.assertEqual(native["layers"][2]["phase"], "gas")
            self.assertEqual([Decimal(value) for value in native["layers"][2]["stoichiometry"]], [Decimal(0), Decimal(0), Decimal(0), Decimal(2), Decimal(1)])
            self.assertEqual(verify_trim_resolution(bundle), bundle.effective_request)

    def test_surface_precedence(self: TestTRIMResolution) -> None:
        """Check zero fallback, ATOMDATA edits, damage edits and component Surf priority; return None."""
        with TemporaryDirectory(dir=CACHE) as td:
            paths, config, worker = make_test_project(Path(td))
            for name, payload in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(payload)
            switch_trim_defaults(paths, config)
            request, selections = deepcopy(TRIM_REQUEST), deepcopy(TRIM_SELECTIONS)
            fallback = resolve_trim_inputs(paths, config, worker, request, selections)
            self.assertEqual(fallback.effective_request["parameters"]["atoms"][0]["surface_binding_eV"], "2")
            selections["atomdata"]["overrides"] = [{"operation": "set", "key": 14, "value": {"sublimation_energy_eV": "8.25"}}]
            algorithmic = resolve_trim_inputs(paths, config, worker, request, selections)
            self.assertEqual(algorithmic.effective_request["parameters"]["atoms"][0]["surface_binding_eV"], "8.25")
            selections["damage"]["overrides"] = [{"operation": "set", "key": [14, "surface_binding_eV"], "value": "6"}]
            resource = resolve_trim_inputs(paths, config, worker, request, selections)
            self.assertEqual(resource.effective_request["parameters"]["atoms"][0]["surface_binding_eV"], "6")
            request["parameters"]["layers"][0]["overrides"]["components"] = [{"component_index": 1, "surface_binding_eV": "0"}]
            component = resolve_trim_inputs(paths, config, worker, request, selections)
            self.assertEqual(component.effective_request["parameters"]["atoms"][0]["surface_binding_eV"], "0")
            self.assertEqual(component.resolved["layers"][0]["provenance"]["components"][0]["damage"]["surface_binding_eV"]["kind"], "component-override")
            self.assertEqual(verify_trim_resolution(component), component.effective_request)

    def test_density_and_mass_overrides(self: TestTRIMResolution) -> None:
        """Resolve explicit density/isotope/damage values without merging equal atoms; return None."""
        with TemporaryDirectory(dir=CACHE) as td:
            paths, config, worker = make_test_project(Path(td))
            for name, payload in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(payload)
            switch_trim_defaults(paths, config)
            request = deepcopy(TRIM_REQUEST)
            request["parameters"]["projectile"]["mass_amu"] = None
            layer = request["parameters"]["layers"][0]
            layer["material"]["elements"].append({"atomic_number": 14, "stoichiometry": "2"})
            layer["overrides"] = {"density_g_cm3": "2.5", "components": [{"component_index": 2, "mass_amu": "29", "lattice_binding_eV": "0"}]}
            bundle = resolve_trim_inputs(paths, config, worker, request, TRIM_SELECTIONS)
            self.assertEqual(bundle.effective_request["parameters"]["projectile"]["mass_amu"], "36")
            self.assertEqual([atom["mass_amu"] for atom in bundle.effective_request["parameters"]["atoms"]], ["28", "29"])
            self.assertEqual(bundle.effective_request["parameters"]["layers"][0]["density_g_cm3"], "2.5")
            self.assertEqual(bundle.effective_request["parameters"]["atoms"][1]["lattice_binding_eV"], "0")

    def test_shared_selections_published_once(self: TestTRIMResolution) -> None:
        """Publish one shared damage variant across ten layers and preserve caller intent; return None."""
        with TemporaryDirectory(dir=CACHE) as td:
            paths, config, worker = make_test_project(Path(td))
            for name, payload in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(payload)
            switch_trim_defaults(paths, config)
            request, selections = deepcopy(TRIM_REQUEST), deepcopy(TRIM_SELECTIONS)
            request["parameters"]["layers"] = [deepcopy(request["parameters"]["layers"][0]) for _ in range(10)]
            selections["damage"].update({"create_version": True, "overrides": [{"operation": "set", "key": [14, "surface_binding_eV"], "value": "9"}]})
            before = deepcopy((request, selections))
            bundle = resolve_trim_inputs(paths, config, worker, request, selections)
            self.assertEqual(len(list((paths.resources / "material/trim-damage").glob("v*"))), 2)
            self.assertEqual(len(bundle.effective_request["parameters"]["atoms"]), 10)
            self.assertEqual(before, (request, selections))
            self.assertEqual(verify_trim_resolution(bundle), bundle.effective_request)

    def test_cab_layer_evidence(self: TestTRIMResolution) -> None:
        """Resolve cached native-dependency CAB contributions per layer and replay offline; return None."""
        with TemporaryDirectory(dir=CACHE) as td:
            paths, config, worker = make_test_project(Path(td))
            for name, payload in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(payload)
            switch_sr_defaults(paths, config)
            switch_trim_defaults(paths, config)
            request, selections = deepcopy(TRIM_REQUEST), deepcopy(TRIM_SELECTIONS)
            layer = request["parameters"]["layers"][0]
            layer["material"]["elements"] = [{"atomic_number": 1, "stoichiometry": "1"}]
            layer["material"]["bonds"] = {"H-H": "1"}
            layer["correction"] = {"mode": "derived"}
            layer["overrides"]["components"] = [{"component_index": 1, "mass_amu": "2.014"}]
            request["parameters"]["layers"].append(deepcopy(layer))
            selections["bond"] = {"source": {"kind": "inline", "data": json.loads(_encode_payload("bond", {"H-H": "10"}))}, "overrides": [], "create_version": False}
            events: list[dict] = []
            with patch("src.srim.execution.subprocess.run", side_effect=fake_bragg_run):
                bundle = resolve_trim_inputs(paths, config, worker, request, selections, progress=events.append)
            self.assertEqual([Decimal(row["correction_factor"]) for row in bundle.effective_request["parameters"]["layers"]], [Decimal("0.4"), Decimal("0.4")])
            self.assertTrue(all("layer_index" in event for event in events))
            self.assertEqual([event["state"] for event in events], ["resolved", "calculated", "cached", "resolved", "cached", "cached"])
            self.assertEqual([event["completed"] for event in events], [1, 2, 3, 1, 2, 3])
            self.assertEqual(verify_trim_resolution(bundle), bundle.effective_request)
            bad = deepcopy(bundle.resolved)
            bad["layers"][0]["correction"]["bond_entries"][0]["value"] = "20"
            with self.assertRaises(ValueError):
                verify_trim_resolution(replace(bundle, resolved=bad))

    def test_replay_without_installation(self: TestTRIMResolution) -> None:
        """Replay event/exotic explicit overrides after sources and aliases change; return None."""
        with TemporaryDirectory(dir=CACHE) as td:
            paths, config, worker = make_test_project(Path(td))
            for name, payload in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(payload)
            switch_trim_defaults(paths, config)
            request = deepcopy(TRIM_REQUEST)
            request["parameters"].update({"mode": "recoil-quick", "total_ions": 10000000, "random_seed": -1, "autosave_ions": 99999, "events": [{"id": "LONG-ID", "atomic_number": 119, "energy_eV": "1e6", "position_angstrom": ["-1", "0", "0"], "direction_cosines": ["2", "0", "0"]}]})
            layer = request["parameters"]["layers"][0]
            layer["material"]["elements"][0]["atomic_number"] = 119
            layer["overrides"]["components"] = [{"component_index": 1, "mass_amu": "300", "displacement_eV": "1", "lattice_binding_eV": "0", "surface_binding_eV": "2"}]
            bundle = resolve_trim_inputs(paths, config, worker, request, TRIM_SELECTIONS)
            (config.srim_root / "TRIM.IN").write_bytes(b"changed")
            with patch("src.srim.trim_resolution.read_sr_defaults", side_effect=AssertionError("no aliases")), patch("src.srim.trim_resolution._read_regular_bytes", side_effect=AssertionError("no filesystem")), patch("src.srim.trim_resolution.read_trim_installation", side_effect=AssertionError("no installation")):
                self.assertEqual(verify_trim_resolution(bundle), bundle.effective_request)
            self.assertEqual(bundle.effective_request["parameters"]["atoms"][0]["atomic_number"], 119)
            self.assertTrue({"event-identifier", "event-energy-coverage", "event-consumption", "autosave-saved-value"} <= {item["code"] for item in bundle.resolved["native_checks"]})

    def test_tampered_bundle_rejected(self: TestTRIMResolution) -> None:
        """Reject tampered sources, physical parameters, provenance and native bytes; return None."""
        with TemporaryDirectory(dir=CACHE) as td:
            paths, config, worker = make_test_project(Path(td))
            for name, payload in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(payload)
            switch_trim_defaults(paths, config)
            bundle = resolve_trim_inputs(paths, config, worker, TRIM_REQUEST, TRIM_SELECTIONS)
            for field in ("native_checks", "atom_index_map", "installation", "layers"):
                bad = deepcopy(bundle.resolved)
                if field == "installation":
                    bad[field]["native_fields"]["stopping_version"] = 1
                else:
                    bad[field] = []
                with self.assertRaises(ValueError):
                    verify_trim_resolution(replace(bundle, resolved=bad))
            for name in ("resources/damage/effective", "engine/TRIM.IN", "installation/TRIM.exe"):
                snapshots = dict(bundle.snapshots)
                snapshots[name] += b"tampered"
                with self.assertRaises(ValueError):
                    verify_trim_resolution(replace(bundle, snapshots=tuple(sorted(snapshots.items()))))
            with self.assertRaises(ValueError):
                verify_trim_resolution(replace(bundle, snapshots=bundle.snapshots + (("../escape", b"x"),)))

    def test_dependency_failure_retains_evidence(self: TestTRIMResolution) -> None:
        """Retain explicitly published versions and identify dependency failure evidence; return None."""
        with TemporaryDirectory(dir=CACHE) as td:
            paths, config, worker = make_test_project(Path(td))
            for name, payload in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(payload)
            switch_trim_defaults(paths, config)
            selections = deepcopy(TRIM_SELECTIONS)
            selections["damage"]["create_version"] = True
            with patch("src.srim.trim_resolution.resolve_material_correction", side_effect=RuntimeError("dependency failed")):
                with self.assertRaisesRegex(RuntimeError, "dependency failed") as caught:
                    resolve_trim_inputs(paths, config, worker, TRIM_REQUEST, selections)
            self.assertEqual(len(list((paths.resources / "material/trim-damage").glob("v*"))), 2)
            self.assertTrue(any("retained explicit versions" in note for note in caught.exception.__notes__))
