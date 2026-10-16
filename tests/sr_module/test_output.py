"""Accept repository fixtures and unittest discovery; expose sr_module/test_output.py checks without native execution unless opted in."""

import json
from agentic_srim.backends.sr_module.output import parse
from agentic_srim.capabilities.stopping_range.inputs import Request
from agentic_srim.storage.artifacts import checksum
from sr_module.support import IsolatedTest, FIXTURES, NATIVE


class OutputTests(IsolatedTest):
    """Construct unittest cases; verify sr_module contracts and report failures."""

    def test_fixture_integrity_and_unit_conversion(self) -> None:
        """Accept no inputs; verify captured hashes and mixed native unit conversion."""
        provenance = json.loads((FIXTURES / "provenance.json").read_text())
        for name, expected in provenance["files_sha256"].items():
            self.assertEqual(checksum(FIXTURES / name), expected)
        table = parse(NATIVE.read_text(encoding="cp1252"), self.request)
        self.assertEqual(table.stopping_unit, "MeV/(mg/cm2)")
        self.assertEqual(table.rows[0].energy_keV, 10)
        self.assertAlmostEqual(table.rows[0].projected_range_nm, 224.7)
        self.assertAlmostEqual(table.rows[0].electronic_stopping, 0.4945)
        self.assertAlmostEqual(table.rows[-1].projected_range_nm, 1120000)
        self.assertEqual(table.rows[-1].energy_keV, 10000)
        self.assertEqual(table.to_dict()["validation"]["scientific_accuracy"], "not_validated")

    def test_parser_rejects_corruption_and_wrong_requests(self) -> None:
        """Accept no inputs; verify incomplete, invalid, and mismatched outputs fail."""
        text = NATIVE.read_text(encoding="cp1252")
        cases = [text[:len(text) // 2], text.replace("2247 A", "2247 xx"),
                 text.replace("4.945E-01", "NaN"), text.replace("4.945E-01", "-4.945E-01"),
                 text.replace("11.00 keV", "10.00 keV"), text.replace("MeV/(mg/cm2)", "bogus"),
                 text.replace("061.54", "051.54")]
        for value in cases:
            with self.subTest(value=value[:30]), self.assertRaises(ValueError):
                parse(value, self.request)
        for change in ({"ion_atomic_number": 2}, {"density_g_cm3": 2}, {"stopping_unit": 1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                parse(text, Request.from_dict(self.data | change))

    def test_target_isotope_mismatch(self) -> None:
        """Accept no inputs; reject changed target masses with unchanged atomic ratios."""
        text = NATIVE.read_text(encoding="cp1252")
        for index, mass in ((0, 2.014), (1, 13.003), (2, 17.999)):
            target = [dict(element) for element in self.data["target"]]
            target[index]["mass_u"] = mass
            with self.subTest(index=index), self.assertRaisesRegex(ValueError, "mass composition"):
                parse(text, Request.from_dict(self.data | {"target": target}))

    def test_bragg_correction_matching(self) -> None:
        """Accept no inputs; verify negative, zero and positive correction matching."""
        text = NATIVE.read_text(encoding="cp1252")
        for multiplier in (1.0, 1.0543):
            with self.subTest(multiplier=multiplier), self.assertRaisesRegex(ValueError, "Bragg correction"):
                parse(text, Request.from_dict(self.data | {"compound_correction": multiplier}))
        for multiplier, printed in ((0.9457121, "-5.43"), (1.0, "0.00"), (1.0543, "+5.43")):
            with self.subTest(multiplier=multiplier):
                table = parse(text.replace("-5.43%", printed + "%"), Request.from_dict(self.data | {"compound_correction": multiplier}))
                self.assertEqual(len(table.rows), 79)

    def test_material_percentage_rounding(self) -> None:
        """Accept no inputs; check half-unit rounding boundaries in percentage points.

        Synthetic equal atomic amounts and masses summing to 100 make the expected
        mass percentages explicit, independently of the production normalization.
        """
        text = NATIVE.read_text(encoding="cp1252")
        start = text.index("      H      1")
        end = text.index("\n ====================================", start)
        binary = text[:start] + "      H      1    050.00    025.00\n      C      6    050.00    075.00" + text[end:]
        for mass, accepted in ((25.0, True), (25.004999, True), (25.005, True), (24.995, True), (25.005001, False), (24.994999, False)):
            target = [self.data["target"][0] | {"atomic_amount": 1, "mass_u": mass},
                      self.data["target"][1] | {"atomic_amount": 1, "mass_u": 100 - mass}]
            request = Request.from_dict(self.data | {"target": target})
            with self.subTest(mass=mass):
                if accepted:
                    self.assertEqual(len(parse(binary, request).rows), 79)
                else:
                    with self.assertRaisesRegex(ValueError, "mass composition"):
                        parse(binary, request)
        for multiplier, accepted in ((0.94574999, True), (0.94575, True), (0.94565, True), (0.94575001, False), (0.94564999, False)):
            request = Request.from_dict(self.data | {"compound_correction": multiplier})
            with self.subTest(multiplier=multiplier):
                if accepted:
                    self.assertEqual(len(parse(text, request).rows), 79)
                else:
                    with self.assertRaisesRegex(ValueError, "Bragg correction"):
                        parse(text, request)

    def test_missing_or_damaged_material_fields(self) -> None:
        """Accept no inputs; reject damaged, absent or ambiguous material evidence."""
        text = NATIVE.read_text(encoding="cp1252")
        for replacement in ("010.62", "", "NaN", "Infinity", "-10.60", "10..60"):
            with self.subTest(mass=replacement), self.assertRaises(ValueError):
                parse(text.replace("010.60", replacement), self.request)
        for replacement in ("", " Bragg Correction = NaN%", " Bragg Correction = Infinity%",
                            " Bragg Correction = -5..43%", " Bragg Correction = -5.43",
                            " Bragg Correction = -5.43% trailing", " Bragg Correction = -5.43%\n Bragg Correction = -5.43%"):
            with self.subTest(correction=replacement), self.assertRaises(ValueError):
                parse(text.replace(" Bragg Correction = -5.43%", replacement), self.request)
        with self.assertRaises(ValueError):
            parse(text.replace(" Bragg Correction", " H 1 061.54 010.60\n Bragg Correction"), self.request)
        with self.assertRaisesRegex(ValueError, "Bragg correction"):
            parse(text.replace("-5.43%", "9" * 400 + "%"), Request.from_dict(self.data | {"compound_correction": 1e308}))

    def test_equivalent_material_representations(self) -> None:
        """Accept no inputs; accept reordering and proportional atomic amounts."""
        text = NATIVE.read_text(encoding="cp1252")
        expected = parse(text, self.request)
        for target in (list(reversed(self.data["target"])),
                       [element | {"atomic_amount": element["atomic_amount"] * 2} for element in self.data["target"]]):
            with self.subTest(target=target):
                self.assertEqual(parse(text, Request.from_dict(self.data | {"target": target})), expected)
