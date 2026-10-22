"""Verify TRIM request contracts, exact native bytes and permissive unknown bounds."""

from __future__ import annotations

from copy import deepcopy
import unittest

from src.srim.trim.inputs import ENERGIES, MODES, flatten_trim_target, render_trim_events, render_trim_input, validate_trim_engine_request, validate_trim_request


TRIM_REQUEST = {
    "schema_version": 1, "engine": "trim", "parameters": {
        "name": "Si 100 nm", "mode": "ion-quick",
        "projectile": {"atomic_number": 18, "mass_amu": "39.962", "energy_keV": "50", "angle_degrees": "0"},
        "total_ions": 1000, "random_seed": 12345, "autosave_ions": 10000,
        "outputs": {"ranges": False, "backscatter": False, "transmission": False, "sputtering": False, "collisions": "none", "exyz_increment_eV": "0"},
        "plot": {"type": "none", "minimum_depth_angstrom": "0", "maximum_depth_angstrom": "1000"},
        "layers": [{"name": "Silicon", "thickness_angstrom": "1000", "material": {"source": "direct", "phase": "solid", "density_g_cm3": "2.3212", "elements": [{"atomic_number": 14, "stoichiometry": "1"}]}, "correction": {"mode": "none", "applied_factor": "1.0"}, "overrides": {"density_g_cm3": None, "components": []}}], "events": None,
    },
}
EXPLICIT_REQUEST = {"schema_version": 1, "engine": "trim", "parameters": {
    **{key: deepcopy(value) for key, value in TRIM_REQUEST["parameters"].items() if key != "layers"},
    "engine_family": "2013", "native_fields": {"legacy_bragg": "0", "reminders": 0, "stopping_version": 0},
    "atoms": [{"atomic_number": 14, "mass_amu": "28.086", "displacement_eV": "15", "lattice_binding_eV": "2", "surface_binding_eV": "4.7"}],
    "layers": [{"name": "Silicon", "thickness_angstrom": "1000", "density_g_cm3": "2.3212", "phase": "solid", "correction_factor": "1.0", "stoichiometry": ["1"]}],
}}
TRIM_SELECTIONS = {**{role: {"version": "default", "overrides": [], "create_version": False} for role in ("atomdata", "electronic", "nuclear", "core", "bond", "damage")}, "compound": None}
TRIM_INSTALLATION_FILES = {"TRIM.exe": b"synthetic TRIM executable", "TRIM.IN": b"Title\r\nIon\r\n18 39.962 50 0 1000 0 10000\r\nCascades\r\n1 12345 0\r\nStopping Power Version\r\n0\r\n", "Data/TRIM.cfg": b"synthetic TRIM configuration", "Data/VERSION": b"SRIM-2013.00\r\n"}


class TestTRIMInputs(unittest.TestCase):
    """Use inherited unittest construction to test independent in-memory TRIM fixtures."""

    def test_exact_input_bytes(self: TestTRIMInputs) -> None:
        """Compare a complete explicit Si fixture to fixed native bytes; return None."""
        expected = (
            '==> TRIM input generated from pinned explicit parameters.\r\n'
            'Ion: Z1, M1, Energy (keV), Angle, Number, Bragg Corr, AutoSave Number.\r\n'
            '18 39.962 50 0 1000 0 10000\r\n'
            'Cascades, Random Number Seed, Reminders\r\n1 12345 0\r\n'
            'Diskfiles: Ranges, Backscatter, Transmit, Sputtered, Collisions, EXYZ increment (eV)\r\n'
            '0 0 0 0 0 0\r\nTarget material: Number of Elements and Layers\r\n'
            '"Si 100 nm" 1 1\r\nPlotType (0-5), Plot Depths: minimum/maximum (Angstrom)\r\n'
            '5 0 1000\r\nTarget Elements: Z Mass(amu)\r\nAtom 1          14 28.086\r\n'
            'Layer Layer Name Width Density Stoichiometry\r\n'
            'Number Description (Angstrom) (g/cm3) Atom entries\r\n'
            '1 "Silicon" 1000 2.3212 1\r\n0 Target layer phases (0=Solid, 1=Gas)\r\n0\r\n'
            'Target Compound Corrections (Bragg)\r\n1.0\r\n'
            'Individual target atom displacement energies (eV)\r\n15\r\n'
            'Individual target atom lattice binding energies (eV)\r\n2\r\n'
            'Individual target atom surface binding energies (eV)\r\n4.7\r\n'
            'Stopping Power Version (copied from installed input)\r\n0\r\n'
        ).encode("ascii")
        self.assertEqual(render_trim_input(EXPLICIT_REQUEST), expected)
        self.assertIsNone(render_trim_events(EXPLICIT_REQUEST))

    def test_event_bytes_and_eof(self: TestTRIMInputs) -> None:
        """Check event ordering, line-eight description, units and terminal EOF; return None."""
        request = deepcopy(EXPLICIT_REQUEST)
        request["parameters"].update({"mode": "specified-ion-quick", "events": [{"id": "T0001", "atomic_number": 18, "energy_eV": "5e4", "position_angstrom": ["0", "-1", "2"], "direction_cosines": ["1", "0", "0"]}]})
        payload = render_trim_events(request)
        self.assertEqual(payload.split(b"\r\n")[7], b"Si 100 nm")
        self.assertTrue(payload.endswith(b"T0001 18 50000 0 -1 2 1 0 0\r\n\x1a"))
        self.assertEqual(payload.count(b"\r\n"), 11)

    def test_version_mode_mapping(self: TestTRIMInputs) -> None:
        """Check every supported family/mode's native code and unavailable 2008 mode; return None."""
        expected = {"recoil-quick": (6, 8), "recoil-full": (7, 6), "recoil-monolayer": (None, 7)}
        for family, modes in MODES.items():
            for mode, code in modes.items():
                request = deepcopy(EXPLICIT_REQUEST)
                request["parameters"].update({"engine_family": family, "mode": mode})
                if not mode.startswith("ion-"):
                    request["parameters"]["events"] = [{"id": "T0001", "atomic_number": 14, "energy_eV": "1000", "position_angstrom": ["100", "0", "0"], "direction_cosines": ["1", "0", "0"]}]
                self.assertEqual(int(render_trim_input(request).splitlines()[4].split()[0]), code)
                if mode in expected:
                    self.assertEqual(code, expected[mode][int(family == "2013")])
        request["parameters"]["engine_family"] = "2008"
        request["parameters"]["mode"] = "recoil-monolayer"
        with self.assertRaises(ValueError):
            render_trim_input(request)

    def test_layer_component_identity(self: TestTRIMInputs) -> None:
        """Ensure equal Si components in separate layers retain separate damage columns; return None."""
        atom = {**EXPLICIT_REQUEST["parameters"]["atoms"][0], "stoichiometry": "1"}
        layer = {key: value for key, value in EXPLICIT_REQUEST["parameters"]["layers"][0].items() if key != "stoichiometry"}
        layer["components"] = [atom]
        second = deepcopy(layer)
        second["components"][0]["surface_binding_eV"] = "7"
        atoms, layers, mapping = flatten_trim_target([layer, second])
        self.assertEqual([a["surface_binding_eV"] for a in atoms], ["4.7", "7"])
        self.assertEqual([l["stoichiometry"] for l in layers], [["1", "0"], ["0", "1"]])
        self.assertEqual(mapping[-1], {"layer_index": 2, "component_index": 1, "atom_index": 2})

    def test_zero_binding_override(self: TestTRIMInputs) -> None:
        """Verify explicit zero energies are accepted and serialized, without fallback; return None."""
        request = deepcopy(EXPLICIT_REQUEST)
        for field in ENERGIES:
            request["parameters"]["atoms"][0][field] = "0"
        self.assertIn(b"lattice binding energies (eV)\r\n0\r\n", render_trim_input(request))

    def test_confirmed_angle_substitution_rejected(self: TestTRIMInputs) -> None:
        """Reject exact 90 degrees but retain untested neighboring finite angles; return None."""
        request = deepcopy(TRIM_REQUEST)
        request["parameters"]["projectile"]["angle_degrees"] = "9e1"
        with self.assertRaisesRegex(ValueError, "silently substitutes"):
            validate_trim_request(request)
        request["parameters"]["projectile"]["angle_degrees"] = "89.95"
        validate_trim_request(request)

    def test_unknown_limits_preserved(self: TestTRIMInputs) -> None:
        """Preserve unverified count/size/seed/energy bounds and explicit exotic species; return None."""
        request = deepcopy(EXPLICIT_REQUEST)
        p = request["parameters"]
        p.update({"total_ions": 10000000, "random_seed": -1, "autosave_ions": -1, "name": "A" * 512})
        p["projectile"]["energy_keV"] = "1e20"
        p["atoms"] = [deepcopy(p["atoms"][0]) for _ in range(55)]
        p["atoms"][-1]["atomic_number"] = 119
        p["layers"] = [deepcopy(p["layers"][0]) for _ in range(101)]
        for layer in p["layers"]:
            layer["stoichiometry"] = ["1"] * 55
        payload = render_trim_input(request)
        self.assertIn(b"55 101\r\n", payload)
        self.assertIn(b"10000000 0 -1\r\n", payload)
        self.assertIn(b"1 -1 0\r\n", payload)

    def test_record_and_matrix_validation(self: TestTRIMInputs) -> None:
        """Reject malformed records, booleans, nonfinite values and invalid matrices; return None."""
        for field, value in (("total_ions", True), ("total_ions", 0), ("random_seed", "1"), ("name", "bad\nrecord")):
            request = deepcopy(EXPLICIT_REQUEST)
            request["parameters"][field] = value
            with self.assertRaises(ValueError):
                validate_trim_engine_request(request)
        for value in ("NaN", "Infinity", " 1", "0", "-1"):
            request = deepcopy(EXPLICIT_REQUEST)
            request["parameters"]["projectile"]["energy_keV"] = value
            with self.assertRaises(ValueError):
                render_trim_input(request)
        for values in ([], ["0"], ["1", "0"], ["-1"]):
            request = deepcopy(EXPLICIT_REQUEST)
            request["parameters"]["layers"][0]["stoichiometry"] = values
            with self.assertRaises(ValueError):
                render_trim_input(request)

    def test_no_mutation(self: TestTRIMInputs) -> None:
        """Validate/render reusable fixtures and check deep intent remains unchanged; return None."""
        original = deepcopy(EXPLICIT_REQUEST)
        validate_trim_request(TRIM_REQUEST)
        render_trim_input(EXPLICIT_REQUEST)
        render_trim_events(EXPLICIT_REQUEST)
        self.assertEqual(original, EXPLICIT_REQUEST)
