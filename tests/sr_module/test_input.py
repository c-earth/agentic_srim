"""Accept repository fixtures and unittest discovery; expose sr_module/test_input.py checks without native execution unless opted in."""

import copy
from agentic_srim.backends.sr_module.input import OUTPUT_NAME, render
from agentic_srim.capabilities.stopping_range.inputs import Request
from agentic_srim.domain.specifications import digest
from sr_module.support import IsolatedTest, FIXTURES


class InputTests(IsolatedTest):
    """Construct unittest cases; verify sr_module contracts and report failures."""

    def test_input_rejects_invalid_scientific_values(self) -> None:
        """Accept no inputs; verify invalid requests fail before execution."""
        changes = [
            {"schema_version": True}, {"ion_atomic_number": 93}, {"ion_mass_u": float("nan")},
            {"ion_mass_u": 10 ** 400}, {"ion_mass_u": -(10 ** 400)},
            {"density_g_cm3": 0}, {"compound_correction": -1}, {"energy_min_keV": 10000},
            {"energy_min_keV": 0}, {"phase": "liquid"}, {"stopping_unit": True},
            {"stopping_unit": 9}, {"sources": {}}, {"target": []}, {"unexpected": 1},
        ]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                Request.from_dict(self.data | change)
        bad = copy.deepcopy(self.data)
        bad["target"][0]["name"] = '../escape"\n'
        with self.assertRaises(ValueError):
            Request.from_dict(bad)
        bad = copy.deepcopy(self.data)
        bad["target"].append(bad["target"][0])
        with self.assertRaises(ValueError):
            Request.from_dict(bad)

    def test_physical_hash_normalizes_atomic_amounts(self) -> None:
        """Accept no inputs; verify proportional/reordered targets hash equally."""
        changed = copy.deepcopy(self.data)
        for element in changed["target"]:
            element["atomic_amount"] *= 2
            element["name"] = "DisplayName"
        changed["target"].reverse()
        changed["sources"]["ion"] = "Another source"
        changed["stopping_unit"] = 1
        self.assertEqual(digest(self.request.physical_spec()), digest(Request.from_dict(changed).physical_spec()))
        changed["density_g_cm3"] *= 2
        self.assertNotEqual(digest(self.request.physical_spec()), digest(Request.from_dict(changed).physical_spec()))

    def test_native_input_matches_installed_example_values(self) -> None:
        """Accept no inputs; compare native input fields with the captured example."""
        output = render(self.request)
        lines = [line.strip() for line in output.splitlines() if not line.startswith("---")]
        expected = [line.strip() for line in (FIXTURES / "installed_example.in").read_text().splitlines() if not line.startswith("---")]
        self.assertEqual(lines[0], f'"{OUTPUT_NAME}"')
        actual_tokens = [token for line in lines[1:] for token in line.split()]
        expected_tokens = [token for line in expected[1:] for token in line.split()]
        self.assertEqual(len(actual_tokens), len(expected_tokens))
        for actual, reference in zip(actual_tokens, expected_tokens):
            if actual.startswith('"'):
                self.assertEqual(actual, reference)
            else:
                self.assertEqual(float(actual), float(reference))
        self.assertIn("\r\n", output)
