"""Check both tool inventories and scientific request schemas against native validators."""

from __future__ import annotations

from copy import deepcopy

from jsonschema import Draft202012Validator

from src.mcp.project_mcp.catalog import build_catalog as project_catalog
from src.mcp.srim_mcp.catalog import build_catalog as scientific_catalog
from src.srim.inputs import validate_sr_request
from src.srim.trim_inputs import MODES, validate_trim_request
from test_contracts import Fixture


class TestCatalog(Fixture):
    """Validate inventory separation, schemas, editable examples and version maps."""

    def test_inventory_and_examples(self: TestCatalog) -> None:
        """Assert stable separated tool counts and all template schema/native checks."""
        science, project = scientific_catalog(self.science), project_catalog(self.project)
        self.assertEqual(len(science["tools"]), 38)
        self.assertEqual(len(project["tools"]), 32)
        self.assertNotIn("run_submit", project["tools"])
        self.assertNotIn("case_create", science["tools"])
        for catalog in (science, project):
            for example in catalog["examples"].values():
                Draft202012Validator(catalog["tools"][example["tool"]]["inputSchema"]).validate(example["arguments"])
                if example["tool"] == "request_validate":
                    (validate_sr_request if example["arguments"]["engine"] == "sr" else validate_trim_request)(example["arguments"]["request"])
        self.assertEqual(science["capabilities"]["capability_trim-prepare"]["compatibility"]["mode_mapping"], MODES)
        self.assertEqual(sum(len(mapping) for mapping in (MODES["2008"], MODES["2013"], MODES["2013"])), 23)

    def test_closed_schemas_and_integer_types(self: TestCatalog) -> None:
        """Reject unknown request fields, bool-as-count and changed tool envelopes."""
        catalog = scientific_catalog(self.science)
        request = deepcopy(catalog["examples"]["trim-layered"]["arguments"]["request"])
        request["parameters"]["total_ions"] = True
        self.assertFalse(Draft202012Validator(catalog["schemas"]["trim.request.v1"]).is_valid(request))
        request = deepcopy(catalog["examples"]["sr-list"]["arguments"]["request"])
        request["parameters"]["energy"]["step_keV"] = "1"
        self.assertFalse(Draft202012Validator(catalog["schemas"]["sr.request.v2"]).is_valid(request))
