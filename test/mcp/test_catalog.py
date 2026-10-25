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
        self.assertEqual(len(project["tools"]), 47)
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

    def test_database_discovery_schemas(self: TestCatalog) -> None:
        """Expose three read-only database tools and four closed database envelope schemas."""
        catalog = project_catalog(self.project)
        for name in ("database_sources_list", "database_inspect", "database_sample_read"):
            tool = catalog["tools"][name]
            self.assertEqual(tool["family"], "databases")
            self.assertFalse(tool["mutating"])
        for identity in ("database.source-list.v1", "database.inspection.v1", "database.sample-reference.v1", "database.sample-read.v1"):
            self.assertFalse(catalog["schemas"][identity]["additionalProperties"])

    def test_external_database_tool_contracts(self: TestCatalog) -> None:
        """Validate new job/read contracts, closed schemas and native-reference isolation."""
        from src.knowledge.external_schema import EMPTY_EXTERNAL_QUERY, EXTERNAL_QUERY_SCHEMA
        catalog = project_catalog(self.project)
        source_id = "db_" + "a" * 64
        for name in ("database_preview", "database_import", "database_reindex", "database_search", "database_get", "database_status"):
            tool = catalog["tools"][name]
            self.assertEqual(tool["family"], "databases")
            self.assertEqual(tool["mutating"], name in ("database_preview", "database_import", "database_reindex"))
            self.assertFalse(tool["inputSchema"]["additionalProperties"])
        self.assertEqual(catalog["tools"]["database_search"]["inputSchema"]["properties"]["limit"]["default"], 20)
        for name, default in (("database_search", 60), ("database_get", 240)):
            timeout = catalog["tools"][name]["inputSchema"]["properties"]["timeout_seconds"]
            self.assertEqual(timeout, {"type": "integer", "minimum": 1, "maximum": 240, "default": default})
        query_schema = catalog["tools"]["database_search"]["inputSchema"]
        validator = Draft202012Validator(query_schema)
        validator.validate({"source_id": source_id, "query": deepcopy(EMPTY_EXTERNAL_QUERY), "offset": 0, "limit": 100})
        self.assertFalse(validator.is_valid({"source_id": source_id, "query": deepcopy(EMPTY_EXTERNAL_QUERY), "limit": 101}))
        self.assertFalse(validator.is_valid({"source_id": source_id, "query": {**EMPTY_EXTERNAL_QUERY, "unexpected": True}}))
        self.assertEqual(catalog["schemas"]["database.external-query.v1"], EXTERNAL_QUERY_SCHEMA)
        for name in ("database_preview", "database_import", "database_reindex"):
            tool = catalog["tools"][name]
            self.assertIn("request_id", tool["inputSchema"]["required"])
            self.assertEqual(tool["inputSchema"]["properties"]["timeout_seconds"], {"type": "integer", "minimum": 1, "maximum": 3600, "default": 600})
            args = {"source_id": source_id, "request_id": "d7f9d14e-1ddd-40cd-bfe6-c22678591294"}
            if name == "database_import":
                args["expected_plan_sha256"] = "b" * 64
            if name == "database_reindex":
                args["expected_generation"] = "00000000-0000-0000-0000-000000000001"
            validator = Draft202012Validator(tool["inputSchema"])
            self.assertTrue(validator.is_valid({**args, "timeout_seconds": 3600}))
            for invalid in (True, 0, 3601):
                self.assertFalse(validator.is_valid({**args, "timeout_seconds": invalid}))
        preview_schema = catalog["tools"]["database_preview"]["inputSchema"]
        self.assertIsNone(preview_schema["properties"]["resume_operation_id"]["default"])
        self.assertNotIn("resume_operation_id", preview_schema["required"])
        validator = Draft202012Validator(preview_schema)
        args = {"source_id": source_id, "request_id": "d7f9d14e-1ddd-40cd-bfe6-c22678591294"}
        for valid in (None, "ad9476d0-a457-4f43-8ba4-0be100027beb"):
            self.assertTrue(validator.is_valid({**args, "resume_operation_id": valid}))
        for invalid in ("../outside", "not-a-uuid", True, 12):
            self.assertFalse(validator.is_valid({**args, "resume_operation_id": invalid}))
        self.assertIn("expected_plan_sha256", catalog["tools"]["database_import"]["inputSchema"]["required"])
        reference = {"schema_version": 1, "kind": "external-run", "source_id": source_id, "adapter_id": "snumat-trim-v1", "adapter_version": 1, "case_id": "SM-1005_Rb_p_d0700nm", "record_sha256": "b" * 64}
        Draft202012Validator(catalog["tools"]["database_get"]["inputSchema"]).validate({"reference": reference})
        native_get = catalog["tools"]["knowledge_get"]["inputSchema"]
        self.assertFalse(Draft202012Validator(native_get).is_valid({"kind": "run", "reference": reference}))
        self.assertEqual(catalog["tools"]["database_search"]["inputSchema"]["properties"]["offset"], catalog["tools"]["knowledge_search"]["inputSchema"]["properties"]["offset"])
