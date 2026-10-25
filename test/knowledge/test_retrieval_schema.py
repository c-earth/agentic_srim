"""Verify closed unified queries, exact output values and shared-row witnesses."""

from __future__ import annotations

from copy import deepcopy
import unittest

from src.knowledge.retrieval_schema import EMPTY_QUERY, match_output_filter, validate_retrieval_document


class TestRetrievalSchema(unittest.TestCase):
    """Construct instances by unittest test name and test pure retrieval contracts."""

    def test_shared_output_row_and_exact_units(self: TestRetrievalSchema) -> None:
        """Require coordinate and value on one row, preserving exact converted values."""
        outputs = {"state": "available", "result_source": "native-text", "complete": True, "summary": [], "tables": [{"name": "sr.rows", "availability": "available", "columns": [{"name": "energy", "unit": "keV"}, {"name": "projected_range", "unit": "A"}], "rows": [["100", "10"], ["200", "20"]], "locator": {"json_pointer": "/rows"}}]}
        filters = {"result_sources": None, "complete": None, "summary": [], "table_groups": [{"table": "sr.rows", "clauses": [{"column": "energy", "operator": "eq", "value": "100000", "unit": "eV"}, {"column": "projected_range", "operator": "eq", "value": "2", "unit": "nm"}]}], "availability": []}
        self.assertFalse(match_output_filter(outputs, filters)["matched"])
        filters["table_groups"][0]["clauses"][1]["value"] = "1"
        result = match_output_filter(outputs, filters)
        self.assertTrue(result["matched"])
        self.assertEqual(result["row_locators"][0]["row"], 0)
        filters["table_groups"][0]["clauses"][0]["value"] = "1e1025"
        with self.assertRaises(ValueError):
            match_output_filter(outputs, filters)

    def test_unavailable_is_not_zero_and_signed_values(self: TestRetrievalSchema) -> None:
        """Keep unavailable tables empty and match signed saved energy cells exactly."""
        outputs = {"state": "partial", "result_source": "checkpoint-recovery", "complete": False, "summary": [], "tables": [{"name": "NOVAC.txt", "availability": "not_calculated", "columns": [], "rows": [], "locator": {}}, {"name": "E2RECOIL.txt", "availability": "available", "columns": [{"unit": "A"}, {"unit": "eV/angstrom/incident_ion"}], "rows": [["10", "-0.125"]], "locator": {}}]}
        filters = {"result_sources": ["checkpoint-recovery"], "complete": None, "summary": [], "table_groups": [], "availability": [{"table": "NOVAC.txt", "state": "not_calculated"}]}
        self.assertTrue(match_output_filter(outputs, filters)["matched"])
        filters["table_groups"] = [{"table": "NOVAC.txt", "clauses": [{"column": 0, "operator": "eq", "value": "0", "unit": "A"}]}]
        self.assertFalse(match_output_filter(outputs, filters)["matched"])
        filters["table_groups"] = [{"table": "E2RECOIL.txt", "clauses": [{"column": 1, "operator": "eq", "value": "-.125", "unit": "eV/angstrom/incident_ion"}]}]
        self.assertTrue(match_output_filter(outputs, filters)["matched"])

    def test_query_scope_and_closed_shapes(self: TestRetrievalSchema) -> None:
        """Reject wrong kinds, unknown keys, boolean bounds and implicit units."""
        validate_retrieval_document(deepcopy(EMPTY_QUERY), "query")
        for invalid in ({**EMPTY_QUERY, "extra": True}, {**EMPTY_QUERY, "kinds": []}, {**EMPTY_QUERY, "input_filters": {"schema_version": 1, "clauses": []}}, {**EMPTY_QUERY, "revision_scope": "latest"}, {**EMPTY_QUERY, "text": " "}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_retrieval_document(invalid, "query")
        filters = {"result_sources": None, "complete": None, "summary": [{"name": "calculated_ions", "operator": "eq", "value": True, "unit": "ions"}], "table_groups": [], "availability": []}
        with self.assertRaises(ValueError):
            match_output_filter({"state": "missing"}, filters)
