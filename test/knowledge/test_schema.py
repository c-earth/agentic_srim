"""Verify exact numeric, composition, layer and event input filter semantics."""

from __future__ import annotations

from copy import deepcopy
import unittest
from unittest.mock import patch

import test_source as fixtures
from src.knowledge.schema import _bounded_decimal, convert_quantity, matches_filters, validate_search_document
from src.knowledge.source import project_simulation_inputs


class TestSchema(fixtures.NativeFixture):
    """Check exact filters and bounded decimals using temporary native fixture projections."""

    def test_numeric_representation_limits(self: TestSchema) -> None:
        """Check self's length/exponent/unit boundaries without unbounded expected outputs."""
        for value, source, target, expected in (
            ("1" * 256, "keV", "keV", "1" * 256),
            ("1e1024", "keV", "keV", "1" + "0" * 1024),
            ("1e-1024", "keV", "keV", "0." + "0" * 1023 + "1"),
            ("0.1e-1023", "keV", "keV", "0." + "0" * 1023 + "1"),
            ("1e1021", "MeV", "keV", "1" + "0" * 1024),
            ("1e-1021", "eV", "keV", "0." + "0" * 1023 + "1"),
            ("-123456789012345678901234567890.12300", "MeV", "eV", "-123456789012345678901234567890123000"),
            ("-0.00", "keV", "keV", "-0.00"),
            ("+1.2300", "keV", "keV", "1.2300"),
        ):
            with self.subTest(value=value, source=source, target=target):
                self.assertEqual(convert_quantity(value, source, target), expected)
        for value, source, target in (
            ("1" * 257, "keV", "keV"), ("0" * 257, "keV", "keV"),
            ("1e1025", "keV", "keV"), ("1e-1025", "keV", "keV"),
            ("0.1e-1024", "keV", "keV"),
            ("1e1022", "MeV", "keV"), ("1e-1022", "eV", "keV"),
            ("1e1025", "eV", "keV"), ("1e-1025", "MeV", "keV"),
            ("1e1000000", "keV", "keV"), ("1e-1000000", "keV", "keV"),
            ("0e1000000", "keV", "keV"), ("-0e-1000000", "keV", "keV"),
        ):
            with self.subTest(value=value, source=source, target=target), self.assertRaisesRegex(ValueError, "exceeds 256 characters|exponent outside -1024..1024"):
                convert_quantity(value, source, target)
        for value in ("", " 1", "1 ", "NaN", "Infinity", "1e", "1/2", "1e" + "9" * 250):
            with self.subTest(malformed=value), self.assertRaises(ValueError):
                convert_quantity(value, "keV", "keV")
        with self.assertRaises(ValueError):
            _bounded_decimal("1", exponent_shift=True)

    def test_filter_numeric_limits(self: TestSchema) -> None:
        """Reject invalid query numbers before Fraction processing and retain exact controls."""
        record = project_simulation_inputs(self.paths, self.created.path.name)
        for token in ("1e1000000", "1e-1000000", "1" * 257):
            clauses = [
                {"field": "energy", "operator": "eq", "value": token, "unit": "keV", "layer": None},
                {"field": "energy", "operator": "between", "value": [token, "1"], "unit": "keV", "layer": None},
                {"field": "energy", "operator": "between", "value": ["1", token], "unit": "keV", "layer": None},
                {"field": "target.composition", "operator": "eq", "value": [{"atomic_number": 8, "mass_amu": token, "atomic_proportion": "1"}], "unit": None, "layer": "any"},
                {"field": "target.composition", "operator": "eq", "value": [{"atomic_number": 8, "mass_amu": None, "atomic_proportion": token}], "unit": None, "layer": "any"},
            ]
            for clause in clauses:
                with self.subTest(token=token, clause=clause), patch("src.knowledge.schema.Fraction") as fraction:
                    with self.assertRaisesRegex(ValueError, "exceeds 256 characters|exponent outside -1024..1024"):
                        matches_filters(record, {"schema_version": 1, "clauses": [clause]})
                    fraction.assert_not_called()
        for bounds in ([None, "100"], ["500", None]):
            filters = {"schema_version": 1, "clauses": [{"field": "energy", "operator": "between", "value": bounds, "unit": "keV", "layer": None}]}
            self.assertTrue(matches_filters(record, filters))
        self.assertTrue(matches_filters(record, {"schema_version": 1, "clauses": [{"field": "energy", "operator": "eq", "value": "1e2", "unit": "keV", "layer": None}]}))
        for token in ("1e1024", "1e-1024", "1" * 256):
            validate_search_document({"schema_version": 1, "clauses": [{"field": "energy", "operator": "eq", "value": token, "unit": "keV", "layer": None}]}, "filters")
        for token in ("0", "-1", "-0"):
            with self.assertRaisesRegex(ValueError, "invalid positive decimal"):
                validate_search_document({"schema_version": 1, "clauses": [{"field": "target.composition", "operator": "eq", "value": [{"atomic_number": 8, "mass_amu": None, "atomic_proportion": token}], "unit": None, "layer": "any"}]}, "filters")

    def test_search_record_numeric_limits(self: TestSchema) -> None:
        """Validate every decimal group using self's in-memory copies, retaining sealed bytes."""
        original = project_simulation_inputs(self.paths, self.created.path.name)
        fields = (("incidence_angle_degrees",), ("projectile", "mass_amu"), ("energy", "values_keV", 0), ("layers", 0, "thickness_angstrom"), ("layers", 0, "density_g_cm3"), ("layers", 0, "components", 0, "mass_amu"), ("layers", 0, "components", 0, "atomic_proportion"), ("energy", "minimum_keV"), ("energy", "maximum_keV"), ("events", 0, "energy_keV"))
        before = {path: path.read_bytes() for path in self.created.path.rglob("*") if path.is_file()}
        for field in fields:
            for token in ("1e1000000", "1e-1000000", "1" * 257):
                with self.subTest(field=field, token=token):
                    record = deepcopy(original)
                    if field[-1] in ("minimum_keV", "maximum_keV"):
                        record["inputs"]["energy"] = {"kind": "range", "values_keV": [], "minimum_keV": "1", "maximum_keV": "1000"}
                    if field[0] == "events":
                        record["inputs"].update(engine="trim", mode="specified-ion-quick", mode_code=4, projectile=None, events=[{"id": "A", "atomic_number": 2, "energy_keV": "100"}])
                    destination = record["inputs"]
                    for key in field[:-1]:
                        destination = destination[key]
                    destination[field[-1]] = token
                    with self.assertRaisesRegex(ValueError, "exceeds 256 characters|exponent outside -1024..1024"):
                        validate_search_document(record, "record")
        record = deepcopy(original)
        record["inputs"]["incidence_angle_degrees"] = "0"
        validate_search_document(record, "record")
        record["inputs"]["incidence_angle_degrees"] = "-1"
        with self.assertRaisesRegex(ValueError, "invalid decimal quantity"):
            validate_search_document(record, "record")
        record = deepcopy(original)
        record["inputs"]["projectile"]["mass_amu"] = "0"
        with self.assertRaisesRegex(ValueError, "invalid decimal quantity"):
            validate_search_document(record, "record")
        self.assertEqual(before, {path: path.read_bytes() for path in self.created.path.rglob("*") if path.is_file()})

    def test_units_and_exact_energy(self: TestSchema) -> None:
        """Convert long decimals exactly and distinguish ranges from lists; return None."""
        self.assertEqual(convert_quantity("123456789012345678901234567890.123", "MeV", "eV"), "123456789012345678901234567890123000")
        record = project_simulation_inputs(self.paths, self.created.path.name)
        for expected, match in (("100000", True), ("100001", False)):
            filters = {"schema_version": 1, "clauses": [{"field": "energy", "operator": "eq", "value": expected, "unit": "eV", "layer": None}]}
            self.assertEqual(matches_filters(record, filters), match)
        record["inputs"]["energy"] = {"kind": "range", "values_keV": [], "minimum_keV": "1", "maximum_keV": "1000"}
        self.assertTrue(matches_filters(record, {"schema_version": 1, "clauses": [{"field": "energy", "operator": "eq", "value": "100001", "unit": "eV", "layer": None}]}))

    def test_atomic_composition_and_layer_selectors(self: TestSchema) -> None:
        """Match scaled atomic ratios, explicit layers and unknown SR thickness; return None."""
        record = project_simulation_inputs(self.paths, self.created.path.name)
        composition = [{"atomic_number": 8, "mass_amu": None, "atomic_proportion": "4"}, {"atomic_number": 14, "mass_amu": None, "atomic_proportion": "2"}]
        filters = {"schema_version": 1, "clauses": [{"field": "target.composition", "operator": "eq", "value": composition, "unit": None, "layer": "all"}, {"field": "layer.thickness", "operator": "unknown", "value": None, "unit": None, "layer": 1}]}
        self.assertTrue(matches_filters(record, filters))
        composition[0]["atomic_proportion"] = "3"
        self.assertFalse(matches_filters(record, filters))
        filters["clauses"][0]["layer"] = None
        with self.assertRaises(ValueError):
            matches_filters(record, filters)

    def test_event_witness_must_be_shared(self: TestSchema) -> None:
        """Prevent helium and argon energies being combined into one witness; return None."""
        record = project_simulation_inputs(self.paths, self.created.path.name)
        record["inputs"].update(engine="trim", mode="specified-ion-quick", mode_code=4, projectile=None, events=[{"id": "A", "atomic_number": 2, "energy_keV": "50"}, {"id": "B", "atomic_number": 18, "energy_keV": "100"}], energy={"kind": "list", "values_keV": ["50", "100"], "minimum_keV": None, "maximum_keV": None})
        filters = {"schema_version": 1, "clauses": [{"field": "projectile.atomic_number", "operator": "eq", "value": 2, "unit": None, "layer": None}, {"field": "energy", "operator": "eq", "value": "100", "unit": "keV", "layer": None}]}
        self.assertFalse(matches_filters(record, filters))
        filters["clauses"][1]["value"] = "50"
        self.assertTrue(matches_filters(record, filters))

    def test_invalid_filters_are_rejected(self: TestSchema) -> None:
        """Reject bool counts, unit mismatches and nonfinite numeric queries; return None."""
        for clause in ({"field": "total_ions", "operator": "eq", "value": True, "unit": None, "layer": None}, {"field": "energy", "operator": "eq", "value": "1", "unit": "nm", "layer": None}, {"field": "energy", "operator": "eq", "value": "NaN", "unit": "keV", "layer": None}, {"field": "energy", "operator": "between", "value": ["2", "1"], "unit": "keV", "layer": None}):
            with self.assertRaises(ValueError):
                validate_search_document({"schema_version": 1, "clauses": [clause]}, "filters")

    def test_layers_any_all_and_explicit_colocation(self: TestSchema) -> None:
        """Distinguish any-layer matches from one explicitly shared layer; return None."""
        record = project_simulation_inputs(self.paths, self.created.path.name)
        record["inputs"].update(engine="trim", mode="ion-quick", mode_code=1)
        second = deepcopy(record["inputs"]["layers"][0])
        second.update(index=2, density_g_cm3="9", thickness_angstrom="100")
        record["inputs"]["layers"].append(second)
        record["inputs"]["layers"][0]["thickness_angstrom"] = "200"
        filters = {"schema_version": 1, "clauses": [{"field": "layer.density", "operator": "eq", "value": "9", "unit": "g/cm3", "layer": "any"}, {"field": "layer.thickness", "operator": "eq", "value": "20", "unit": "nm", "layer": "any"}]}
        self.assertTrue(matches_filters(record, filters))
        for clause in filters["clauses"]:
            clause["layer"] = 2
        self.assertFalse(matches_filters(record, filters))
        filters["clauses"] = [filters["clauses"][0]]
        filters["clauses"][0]["layer"] = "all"
        self.assertFalse(matches_filters(record, filters))
