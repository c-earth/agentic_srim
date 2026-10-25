"""Check closed external schemas, exact quantities and source witnesses."""
from __future__ import annotations

from copy import deepcopy
import unittest
from src.knowledge.external_schema import EMPTY_EXTERNAL_QUERY, match_external_record, validate_external_document
from src.knowledge.snumat_adapter import normalize_snumat_case
from test_snumat_adapter import CASE_ID, LOCATORS, MEMBERS, SOURCE_ID



class TestExternalSchema(unittest.TestCase):
    """Exercise external schema boundaries without native execution."""

    def test_closed_envelopes(self: TestExternalSchema) -> None:
        """Reject extra keys, unsupported scientific fields and boolean versions."""
        record = normalize_snumat_case(SOURCE_ID, CASE_ID, MEMBERS, LOCATORS)
        validate_external_document(record, "record")
        query = deepcopy(EMPTY_EXTERNAL_QUERY)
        query["extra"] = True
        with self.assertRaises(ValueError):
            validate_external_document(query, "query")
        wrong_unit = deepcopy(record)
        wrong_unit["inputs"]["energy_keV"]["unit"] = "ions"
        with self.assertRaises(ValueError):
            validate_external_document(wrong_unit, "record")
        record["schema_version"] = True
        with self.assertRaises(ValueError):
            validate_external_document(record, "record")
        query = deepcopy(EMPTY_EXTERNAL_QUERY)
        query["clauses"] = [{"field": "outputs.profiles", "operator": "eq", "value": "1", "unit": None}]
        with self.assertRaises(ValueError):
            validate_external_document(query, "query")


    def test_exact_predicates_and_units(self: TestExternalSchema) -> None:
        """Match exact converted quantities while keeping original witness tokens."""
        record = normalize_snumat_case(SOURCE_ID, CASE_ID, MEMBERS, LOCATORS)
        query = {**deepcopy(EMPTY_EXTERNAL_QUERY), "clauses": [{"field": "outputs.mean_range", "operator": "eq", "value": "5", "unit": "nm"}, {"field": "inputs.energy_keV", "operator": "eq", "value": "100000", "unit": "eV"}]}
        match = match_external_record(record, query)
        assert match["matched"] is True
        assert match["witnesses"][0]["raw"] == "50000.E-03"
        assert match["witnesses"][0]["unit"] == "A"
        query["clauses"][0]["value"] = "5.0000000000000000001"
        assert match_external_record(record, query)["matched"] is False
        for value in ("NaN", "1e1025", "x" * 257):
            query["clauses"][0]["value"] = value
            with self.assertRaises(ValueError):
                validate_external_document(query, "query")
        for bounds in ([None, None], ["2", "1"]):
            query["clauses"] = [{"field": "outputs.mean_range", "operator": "between", "value": bounds, "unit": "A"}]
            with self.assertRaises(ValueError):
                validate_external_document(query, "query")


    def test_unknown_and_layer_witnesses(self: TestExternalSchema) -> None:
        """Unknown never means zero; element/layer predicates retain one-layer witnesses."""
        members = {key: value for key, value in MEMBERS.items() if key != "TDATA.txt"}
        record = normalize_snumat_case(SOURCE_ID, CASE_ID, members, LOCATORS)
        query = {**deepcopy(EMPTY_EXTERNAL_QUERY), "clauses": [{"field": "outputs.transmitted_ions", "operator": "unknown", "value": None, "unit": None}]}
        assert match_external_record(record, query)["matched"] is True
        known = normalize_snumat_case(SOURCE_ID, CASE_ID, MEMBERS, LOCATORS)
        assert match_external_record(known, query)["matched"] is False
        query["clauses"] = [{"field": "inputs.target_elements", "operator": "contains_all", "value": ["Si"], "unit": None}, {"field": "inputs.layer.density_g_cm3", "operator": "eq", "value": "2.3290", "unit": "g/cm3"}]
        matched = match_external_record(known, query)
        assert matched["matched"] is True
        assert all(item["locator"]["member_path"].endswith("TRIM.IN") for item in matched["witnesses"])
