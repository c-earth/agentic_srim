"""Exercise scientific argument forwarding and failure boundaries using controlled APIs."""

from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
from dataclasses import replace
from pathlib import Path
import sys
from unittest.mock import patch
from uuid import uuid4

from src.mcp.srim_mcp.catalog import build_catalog
from src.mcp.srim_mcp.operations import read_operation, write_operation
from src.mcp.common.contracts import canonical_bytes
from src.srim.sr.inputs import render_sr_input
from src.srim.common.types import JSONObject
from test_contracts import Fixture


class TestScientificAdapter(Fixture):
    """Use native-free request/render and mocked lifecycle checks to verify adapters."""

    def cancel(self: TestScientificAdapter) -> bool:
        """Return False for self's noncancelled synthetic operation without effects."""
        return False

    def progress(self: TestScientificAdapter, event: JSONObject) -> None:
        """Append event to self's test evidence without process or file effects."""
        self.events.append(event)

    def test_validation_render_and_offline_environment(self: TestScientificAdapter) -> None:
        """Validate string-preserving SR data and offline readiness without a native engine."""
        catalog = build_catalog(self.science)
        request = deepcopy(catalog["examples"]["sr-list"]["arguments"]["request"])
        self.assertTrue(read_operation("request_validate", {"engine": "sr", "request": request, "explicit": False}, self.science)["valid"])
        request["parameters"]["target"] = {"phase": "solid", "density_g_cm3": "2.267", "elements": [{"atomic_number": 6, "name": "C", "mass_amu": "12.011", "stoichiometry": "1"}]}
        data = read_operation("input_render", {"engine": "sr", "request": request}, self.science)
        self.assertEqual(base64.b64decode(data["files"][0]["content"]), render_sr_input(request))
        environment = read_operation("environment_get", {}, self.science)
        self.assertFalse(environment["installation"]["sr"]["available"])
        self.assertFalse(environment["installation"]["trim"]["available"])
        self.assertEqual(environment["cancellation"]["sr"], "timeout-only")

    def test_prepared_run_forwarding_and_failed_status(self: TestScientificAdapter) -> None:
        """Forward an explicit prepared SR timeout and retain native failed metadata."""
        self.events = []
        identity = "srim_20000101_000000_1"
        manifest = self.paths.simulation_memory / identity / "input" / "engine" / "runtime.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_bytes(b'{"schema_version":1,"files":[]}')
        prepared = {"engine": "sr", "simulation_id": identity, "worker_id": "worker_1", "runtime_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest()}
        metadata = {"engine": {"name": "sr"}, "status": "failed", "failure": {"message": "timeout", "exit_code": None}}
        with patch("src.mcp.srim_mcp.operations.verify_sr_runtime", return_value={}) as verify, patch("src.mcp.srim_mcp.operations.run_sr_simulation", return_value=metadata) as run:
            result = write_operation("run_submit", {"prepared": prepared, "timeout_seconds": 12.5}, self.science, self.cancel, self.progress)
            self.assertEqual(result["outcome"], "failed")
            self.assertEqual(result["data"], metadata)
            self.assertEqual(run.call_args.kwargs["timeout_seconds"], 12.5)
            verify.assert_called_once()
        with self.assertRaises(ValueError):
            write_operation("run_submit", {"prepared": prepared, "timeout_seconds": None}, self.science, self.cancel, self.progress)

    def test_result_paging_and_uncalculated_profile(self: TestScientificAdapter) -> None:
        """Page pinned recovered profiles while preserving uncalculated data and warnings."""
        identity = "srim_20000101_000000_1"
        payload = canonical_bytes({"result_source": "checkpoint-recovery", "complete": True, "recovery": {"warnings": ["Stored checkpoint precision"], "profiles": {"RANGE.txt": {"columns": ["depth"], "rows": [["1"], ["2"], ["3"]]}, "NOVAC.txt": {"columns": [], "rows": [], "calculated_ions": None, "notes": ["Not calculated in quick mode"]}}}})
        target = self.paths.simulation_memory / identity / "output" / "parsed" / "results.json"
        target.parent.mkdir(parents=True)
        target.write_bytes(payload)
        source = {"reference": {"id": identity}, "files": [{"path": "output/parsed/results.json", "sha256": hashlib.sha256(payload).hexdigest(), "size_bytes": len(payload)}]}
        with patch("src.mcp.srim_mcp.operations.project_simulation_inputs", return_value=source), patch("src.mcp.srim_mcp.operations.read_simulation", return_value={"engine": {"name": "trim"}, "status": "failed"}), patch("src.mcp.srim_mcp.operations.read_source_record", return_value=source):
            page = read_operation("table_read", {"simulation_id": identity, "table": "RANGE.txt", "offset": 1, "limit": 1}, self.science)
            self.assertEqual(page["table"]["rows"], [["2"]])
            self.assertEqual(page["table"]["total"], 3)
            self.assertEqual(page["native_status"], "failed")
            self.assertEqual(page["warnings"], ["Stored checkpoint precision"])
            empty = read_operation("table_read", {"simulation_id": identity, "table": "NOVAC.txt", "offset": 0, "limit": 10}, self.science)
            self.assertEqual(empty["table"]["availability"], "not_calculated")
            self.assertEqual(empty["table"]["rows"], [])
            target.write_bytes(payload + b" ")
            with self.assertRaises(ValueError):
                read_operation("table_read", {"simulation_id": identity, "table": "RANGE.txt", "offset": 0, "limit": 10}, self.science)

    def test_real_preparation_with_synthetic_installation(self: TestScientificAdapter) -> None:
        """Resolve, pin, reload and prepare both engines with no native process launch."""
        sys.path.insert(0, str(self.repository / "test" / "srim"))
        from _fixtures import make_test_project
        from test_trim_inputs import EXPLICIT_REQUEST, TRIM_INSTALLATION_FILES, TRIM_REQUEST, TRIM_SELECTIONS
        from jsonschema import Draft202012Validator
        from src.srim.resources.defaults import switch_sr_defaults
        from src.srim.trim.defaults import switch_trim_defaults

        self.events = []
        with patch("subprocess.Popen", side_effect=AssertionError("No native launch during synthetic preparation")):
            paths, config, worker = make_test_project(self.root)
            sr_environment = read_operation("environment_get", {}, replace(self.science, config=config))
            self.assertTrue(sr_environment["installation"]["sr"]["available"])
            self.assertFalse(sr_environment["installation"]["trim"]["available"])
            for name, payload in TRIM_INSTALLATION_FILES.items():
                target = config.srim_root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
            context = replace(self.science, config=config)
            switch_sr_defaults(paths, config)
            switch_trim_defaults(paths, config)
            catalog = build_catalog(context)
            Draft202012Validator(catalog["schemas"]["trim.engine-request.v1"]).validate(EXPLICIT_REQUEST)
            resolved = write_operation("trim_resolve", {"request": TRIM_REQUEST, "selections": TRIM_SELECTIONS, "worker_id": worker.path.name, "capture_timeout_seconds": 30.0, "dependency_timeout_seconds": 60.0}, context, self.cancel, self.progress)
            prepared = write_operation("trim_prepare", {"bundle": resolved["data"], "worker_id": worker.path.name, "cascade_output": None}, context, self.cancel, self.progress)
            self.assertEqual(prepared["data"]["engine"], "trim")
            request = deepcopy(catalog["examples"]["sr-list"]["arguments"]["request"])
            selections = catalog["examples"]["sr-prepare"]["arguments"]["selections"]
            sr_arguments = {"request": request, "selections": selections, "worker_id": worker.path.name, "capture_timeout_seconds": 30.0, "dependency_timeout_seconds": 60.0}
            inspected = write_operation("sr_resolve", sr_arguments, context, self.cancel, self.progress)
            self.assertEqual(inspected["data"]["engine"], "sr")
            staged = write_operation("sr_prepare", sr_arguments, context, self.cancel, self.progress)
            self.assertEqual(staged["data"]["engine"], "sr")
            matches = read_operation("resources_list", {"category": "material", "name": "atomdata", "offset": 0, "limit": 100}, context)
            self.assertTrue(matches["matches"])
            atomic = read_operation("resource_read", {"reference": matches["matches"][0], "section": "payload", "offset": 0, "limit": 5}, context)
            self.assertEqual(len(atomic["rows"]), 5)
            compounds = read_operation("compounds_search", {"compound_selection": {"version": "default", "overrides": [], "create_version": False}, "atomdata_selection": {"version": "default", "overrides": [], "create_version": False}, "filters": {}, "offset": 0, "limit": 10}, context)
            self.assertGreater(compounds["total"], 0)
