"""Verify exact numeric, composition, layer and event input filter semantics."""

from __future__ import annotations

from copy import deepcopy
import unittest

import test_source as fixtures
from src.knowledge.schema import convert_quantity, matches_filters, validate_search_document
from src.knowledge.source import project_simulation_inputs


class TestSchema(fixtures.NativeFixture):
    """Use native fixture projections and independent expected filter outcomes."""

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
