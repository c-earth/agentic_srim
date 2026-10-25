"""Check both tool inventories and scientific request schemas against native validators."""

from __future__ import annotations

from copy import deepcopy

from jsonschema import Draft202012Validator

from src.mcp.project_mcp.catalog import build_catalog as project_catalog
from src.mcp.srim_mcp.catalog import build_catalog as scientific_catalog
from src.srim.sr.inputs import validate_sr_request
from src.srim.trim.inputs import MODES, validate_trim_request
from test_contracts import Fixture


class TestCatalog(Fixture):
    """Validate inventory separation, schemas, editable examples and version maps."""

    def test_inventory_and_examples(self: TestCatalog) -> None:
        """Assert stable separated tool counts and all template schema/native checks."""
        science, project = scientific_catalog(self.science), project_catalog(self.project)
        self.assertEqual(len(science["tools"]), 40)
        self.assertFalse(science["tools"]["run_closeout"]["mutating"])
        self.assertTrue(science["tools"]["run_reconcile"]["mutating"])
        self.assertIn("srim.run-closeout.v1", science["schemas"])
        self.assertEqual(len(project["tools"]), 33)
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

    def test_improvement_assessment_contract(self: TestCatalog) -> None:
        """Validate assessment discovery and reject unknown fields/invalid decisions."""
        catalog = project_catalog(self.project)
        tool = catalog["tools"]["improvement_assess"]
        self.assertTrue(tool["mutating"])
        self.assertEqual(tool["family"], "improvement")
        details = {"observation_ids": ["00000000-0000-0000-0000-000000000001"], "disposition": "proposal_needed", "rationale": "Supported evidence", "uncertainty": "", "follow_up": "Prepare proposal", "related_proposal_ids": [], "predecessor_assessment_id": None}
        validator = Draft202012Validator(tool["inputSchema"])
        arguments = {"request_id": "00000000-0000-0000-0000-000000000002", "details": details}
        validator.validate(arguments)
        for invalid in ({**arguments, "extra": True}, {**arguments, "details": {**details, "extra": True}}, {**arguments, "details": {**details, "disposition": "adopt"}}, {**arguments, "details": {**details, "observation_ids": []}}, {**arguments, "details": {**details, "rationale": " "}}, {**arguments, "details": {**details, "follow_up": ""}}, {**arguments, "details": {**details, "disposition": "already_addressed"}}):
            self.assertFalse(validator.is_valid(invalid))
        self.assertIn("project.assessment.v2", catalog["schemas"])
