"""Verify SRModule request schemas and exact range/list input serialization."""

from __future__ import annotations

from copy import deepcopy
import unittest

from src.srim.sr.inputs import render_sr_input, validate_sr_request
from src.srim.common.types import JSONObject


class TestSRInputs(unittest.TestCase):
    """Verify pure SR inputs using unittest's inherited test-method-name constructor.

    setUp supplies the public request dictionary independently for each test.
    Tests validate and serialize that fixture without filesystem operations.
    """

    def setUp(self: TestSRInputs) -> None:
        """Give self an independent explicit water request; return None without I/O."""
        self.request: JSONObject = {
            "schema_version": 1,
            "parameters": {
                "projectile": {"atomic_number": 1, "mass_amu": "1.008"},
                "target": {
                    "phase": "solid",
                    "density_g_cm3": "1.0",
                    "elements": [
                        {"atomic_number": 1, "name": "Hydrogen", "stoichiometry": "2", "mass_amu": "1.008"},
                        {"atomic_number": 8, "name": "Oxygen", "stoichiometry": "1", "mass_amu": "15.999"},
                    ],
                },
                "correction": {"mode": "none", "applied_factor": "1.0"},
                "stopping_units": 5,
                "energy": {"mode": "range", "minimum_keV": "10", "maximum_keV": "10000"},
            },
        }

    def test_range_serialization(self: TestSRInputs) -> None:
        """Check self's range request against complete expected bytes and immutability; return None or assertion failures."""
        original = deepcopy(self.request)
        expected = (
            "---Stopping/Range Input Data (Number-format: Period = Decimal Point)\r\n"
            "---Output File Name\r\n"
            '"stopping.txt"\r\n'
            "---Ion(Z), Ion Mass(u)\r\n"
            "1 1.008\r\n"
            "---Target Data: (Solid=0,Gas=1), Density(g/cm3), Compound Corr.\r\n"
            "0 1.0 1.0\r\n"
            "---Number of Target Elements\r\n"
            "2\r\n"
            "---Target Elements: (Z), Target name, Stoich, Target Mass(u)\r\n"
            '1 "Hydrogen" 2 1.008\r\n'
            '8 "Oxygen" 1 15.999\r\n'
            "---Output Stopping Units (1-8)\r\n"
            "5\r\n"
            "---Ion Energy : E-Min(keV), E-Max(keV)\r\n"
            "10 10000\r\n"
        ).encode("ascii")
        self.assertEqual(render_sr_input(self.request), expected)
        self.assertEqual(self.request, original)
        self.request["parameters"]["target"]["phase"] = "gas"
        self.request["parameters"]["target"]["density_g_cm3"] = "00.00100"
        self.assertIn(b"1 00.00100 1.0\r\n", render_sr_input(self.request))

    def test_list_serialization(self: TestSRInputs) -> None:
        """Check self's list ordering, repetitions, terminator, precision, and count limits; return None or assertion failures."""
        parameters = self.request["parameters"]
        parameters["energy"] = {"mode": "list", "values_keV": ["3.9800", "1.02", "3.9800"]}
        self.assertTrue(render_sr_input(self.request).endswith(b"0 0\r\n3.9800\r\n1.02\r\n3.9800\r\n0\r\n"))
        parameters["energy"]["values_keV"] = ["1"] * 1000
        validate_sr_request(self.request)
        for values in ([], ["1"] * 1001, ["0"], [True], [1.5], ["1e2"]):
            parameters["energy"]["values_keV"] = values
            with self.subTest(values_count=len(values)):
                with self.assertRaises(ValueError):
                    render_sr_input(self.request)

    def test_request_validation(self: TestSRInputs) -> None:
        """Check self's malformed envelopes, fields, quantities, units, and ranges; return None or assertion failures without I/O."""
        for invalid in (None, [], self.request | {"schema_version": True}, self.request | {"extra": 1}, self.request | {"parameters": []}):
            with self.assertRaises(ValueError):
                validate_sr_request(invalid)
        for value in (0, False, 1.0, "0", "-1", "+1", "1e3", "NaN", "Infinity", " 1", "1 ", ".5", "1.", "", "١"):
            request = deepcopy(self.request)
            request["parameters"]["projectile"]["mass_amu"] = value
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_sr_request(request)
        for section in ("projectile", "target", "energy"):
            request = deepcopy(self.request)
            request["parameters"][section]["extra"] = 1
            with self.assertRaises(ValueError):
                validate_sr_request(request)
            request["parameters"][section] = None
            with self.assertRaises(ValueError):
                validate_sr_request(request)
        for value in (0, 93, True, 1.0, "1"):
            request = deepcopy(self.request)
            request["parameters"]["projectile"]["atomic_number"] = value
            with self.assertRaises(ValueError):
                validate_sr_request(request)
        for value in (0, 9, True, "5", 5.0):
            request = deepcopy(self.request)
            request["parameters"]["stopping_units"] = value
            with self.assertRaises(ValueError):
                validate_sr_request(request)
        for energy in (
            {"mode": "range", "minimum_keV": "10", "maximum_keV": "10.00"},
            {"mode": "range", "minimum_keV": "11", "maximum_keV": "10"},
            {"mode": "list", "values_keV": "1"},
            {"mode": "other"},
        ):
            request = deepcopy(self.request)
            request["parameters"]["energy"] = energy
            with self.assertRaises(ValueError):
                validate_sr_request(request)

    def test_target_and_correction_rules(self: TestSRInputs) -> None:
        """Check self's target identities, bounds, names, and explicit corrections; return None or assertion failures without I/O."""
        parameters = self.request["parameters"]
        target = parameters["target"]
        original = deepcopy(target["elements"])
        target["elements"] = [
            {"atomic_number": 1, "name": "H", "stoichiometry": "0.25", "mass_amu": str(index + 1)}
            for index in range(100)
        ]
        validate_sr_request(self.request)
        target["elements"].append({"atomic_number": 92, "name": "U", "stoichiometry": "1", "mass_amu": "238"})
        with self.assertRaises(ValueError):
            validate_sr_request(self.request)
        target["elements"] = []
        with self.assertRaises(ValueError):
            validate_sr_request(self.request)
        target["elements"] = original + [original[0] | {"mass_amu": "01.0080", "name": "H"}]
        with self.assertRaises(ValueError):
            validate_sr_request(self.request)
        target["elements"] = original
        for name in ("", "A" * 14, 'bad"name', "bad\nname", "Hydrogène"):
            target["elements"][0]["name"] = name
            with self.assertRaises(ValueError):
                validate_sr_request(self.request)
        target["elements"][0]["name"] = "A" * 13
        validate_sr_request(self.request)
        for field, value in (("atomic_number", True), ("mass_amu", "0"), ("stoichiometry", "0")):
            request = deepcopy(self.request)
            request["parameters"]["target"]["elements"][0][field] = value
            with self.assertRaises(ValueError):
                validate_sr_request(request)
        for phase in (0, "liquid", None):
            target["phase"] = phase
            with self.assertRaises(ValueError):
                validate_sr_request(self.request)
        target["phase"] = "solid"
        target["density_g_cm3"] = "0"
        with self.assertRaises(ValueError):
            validate_sr_request(self.request)
        target["density_g_cm3"] = "1.0"
        parameters["correction"] = {"mode": "supplied", "applied_factor": "1.0500", "source": "measurement", "reason": "fit"}
        self.assertIn(b"0 1.0 1.0500\r\n", render_sr_input(self.request))
        for correction in (
            {"mode": "none", "applied_factor": "1.00"},
            {"mode": "supplied", "applied_factor": "1e0", "source": "measurement", "reason": "fit"},
            {"mode": "supplied", "applied_factor": "1", "source": "", "reason": "fit"},
            {"mode": "derived"},
        ):
            parameters["correction"] = correction
            with self.assertRaises(ValueError):
                validate_sr_request(self.request)
