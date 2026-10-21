"""Verify native-only input search, pagination, generation pins and stale evidence."""

from __future__ import annotations

import test_source as fixtures
from src.knowledge.index import update_index
from src.knowledge.search import search_inputs


class TestSearch(fixtures.NativeFixture):
    """Query independent native input expectations in a temporary cache project."""

    def test_inputs_status_and_paging(self: TestSearch) -> None:
        """Include failed runs by default and use exact explicit input filters; return None."""
        update_index(self.paths, [self.created.path.name])
        filters = {"schema_version": 1, "clauses": [{"field": "engine", "operator": "eq", "value": "sr", "unit": None, "layer": None}, {"field": "energy", "operator": "between", "value": ["100000", "100000"], "unit": "eV", "layer": None}]}
        result = search_inputs(self.paths, filters, limit=1)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["matches"][0]["execution_status"], "failed")
        self.assertEqual(search_inputs(self.paths, filters, offset=1, expected_generation=result["generation"])["matches"], [])
        filters["clauses"].append({"field": "execution_status", "operator": "eq", "value": "succeeded", "unit": None, "layer": None})
        self.assertEqual(search_inputs(self.paths, filters)["total"], 0)

    def test_stale_source_and_generation_pin(self: TestSearch) -> None:
        """Exclude tampered evidence and reject mixed pagination generations; return None."""
        update_index(self.paths, [self.created.path.name])
        result = search_inputs(self.paths)
        (self.created.path / "input/engine/SR.IN").write_bytes(b"changed")
        stale = search_inputs(self.paths)
        self.assertEqual(stale["total"], 0)
        self.assertEqual(len(stale["excluded"]), 1)
        update_index(self.paths, [self.created.path.name])
        with self.assertRaisesRegex(ValueError, "generation"):
            search_inputs(self.paths, expected_generation=result["generation"])

    def test_invalid_args_before_catalog_access(self: TestSearch) -> None:
        """Reject invalid page/filter requests even with no catalog; return None."""
        for kwargs in ({"offset": True}, {"limit": 1001}, {"expected_generation": "bad"}, {"filters": {"schema_version": 1, "clauses": [{"field": "output.range", "operator": "eq", "value": "1", "unit": "nm", "layer": None}]}}):
            with self.assertRaises(ValueError):
                search_inputs(self.paths, **kwargs)
        self.assertFalse((self.paths.root / "memory/knowledge").exists())
