"""Accept unittest discovery; verify immutable registration, identity, assessment and reuse."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from analysis.builtins import bundle
from analysis.config import Settings
from analysis.config import MAX_LIMITS
from analysis.records import AnalysisError, loads
from analysis.storage import Store
from analysis_support import AnalysisTest


class StorageTests(AnalysisTest):
    """Construct isolated tests; verify transactional registry/reuse and strict validation."""

    def test_absent_reads_never_create_store_and_invalid_requests_are_strict(self) -> None:
        """Accept no inputs; reject extra fields/types/depth/nonfinite data without writes; return nothing."""
        self.assertEqual(self.store.perform("list_methods", {}), {"schema_version": 1, "methods": [], "next_cursor": None})
        self.assertEqual(self.store.perform("list_analyses", {})["analyses"], [])
        for request in ({"limit": True}, {"limit": 101}, {"unknown": 1}, {"limit": float("nan")}):
            with self.assertRaises(AnalysisError):
                self.store.perform("list_methods", request)
        self.assertFalse(self.store.path.exists())
        for text in ('{"x":1,"x":2}', '{"x":NaN}', '[' * 34 + '0' + ']' * 34):
            with self.assertRaises(AnalysisError):
                loads(text)

    def test_immutable_versions_replay_and_no_registration_execution(self) -> None:
        """Accept no inputs; register inert malicious text, enforce versions/keys/checksums; return nothing."""
        spec, files = bundle("dataset_summary")
        files["method.py"] = 'raise RuntimeError("Must not execute during registration")\n'
        request = self.audit(schema_version=1, method_id="sample", version=1, specification=spec, files=files)
        first = self.store.perform("register_method", request)
        self.assertEqual(self.store.perform("register_method", request), first)
        with self.assertRaisesRegex(AnalysisError, "Key"):
            self.store.perform("register_method", {**request, "reason": "changed"})
        for version in (1, 3):
            with self.assertRaises(AnalysisError):
                self.store.perform("register_method", {**request, "version": version, "idempotency_key": str(version)})
        second = self.store.perform("register_method", {**request, "version": 2, "idempotency_key": "second"})
        self.assertEqual(second["method"]["version"], 2)
        path = self.store.method_path(first["method"]) / "method.py"
        path.write_text("changed", encoding="utf-8")
        with self.assertRaises(AnalysisError):
            self.store.perform("get_method", first["method"])

    def test_bundle_traversal_reserved_names_schema_and_limits_fail(self) -> None:
        """Accept no inputs; reject unsafe/colliding/oversized bundles and unsupported schemas; return nothing."""
        spec, files = bundle("dataset_summary")
        for name in ("../outside.py", "C:/outside.py", "dir/../file", "CON", "manifest.json", "a\\b", "a./b"):
            with self.subTest(name=name), self.assertRaises(AnalysisError):
                self.store.perform("register_method", self.audit(schema_version=1, method_id="bad", version=1, specification=spec, files={**files, name: "x"}))
        bad = deepcopy(spec)
        bad["parameter_schema"]["anyOf"] = []
        with self.assertRaises(AnalysisError):
            self.store.perform("register_method", self.audit(schema_version=1, method_id="bad", version=1, specification=bad, files=files))
        with self.assertRaises(AnalysisError):
            Settings(self.runtime, limits={"wall_seconds": 0})

    def test_verified_summary_deduplicates_replays_aliases_and_explicit_generations(self) -> None:
        """Accept no inputs; verify exact-environment reuse/current generation and integrity failure; return nothing."""
        method = self.methods()["dataset_summary"]
        source = self.source()
        request = self.summary_request(method, source)
        with self.assertRaisesRegex(AnalysisError, "verification"):
            self.store.perform("submit_analysis", request)
        self.verify(method)
        first = self.store.perform("submit_analysis", request)
        result = self.wait(first["analysis_id"])
        self.assertEqual(result["state"], "CHECKED", result["error"])
        self.assertEqual(self.store.perform("submit_analysis", request)["analysis_id"], first["analysis_id"])
        fresh = {**request, "inputs": {"data": {**source, "store_id": "alias"}}, **self.audit()}
        second = self.store.perform("submit_analysis", fresh)
        self.assertTrue(second["reused"])
        self.assertEqual(first["analysis_id"], second["analysis_id"])
        self.assertEqual(self.output(result)["summary"]["statistics"]["electronic_stopping"]["max_coordinate"], 3)
        new_request = {**request, **self.audit(), "recompute_of": first["analysis_id"]}
        new = self.store.perform("submit_analysis", new_request)
        self.assertNotEqual(first["analysis_id"], new["analysis_id"])
        self.assertEqual(self.store.perform("submit_analysis", {**request, **self.audit()})["analysis_id"], new["analysis_id"])
        self.assertEqual(self.wait(new["analysis_id"])["state"], "CHECKED")
        with self.assertRaises(AnalysisError):
            self.store.perform("submit_analysis", {**new_request, **self.audit()})
        artifact = self.runtime / "analyses" / new["analysis_id"] / "attempts" / self.wait(new["analysis_id"])["current_attempt"] / "output" / "samples.jsonl"
        artifact.write_text("damaged", encoding="utf-8")
        with self.assertRaises(AnalysisError):
            self.store.perform("submit_analysis", {**request, **self.audit()})

    def test_concurrent_submissions_share_one_original_budget(self) -> None:
        """Accept no inputs; deduplicate simultaneous writers and prevent consumer budget resets; return nothing."""
        method = self.methods()["dataset_summary"]
        self.verify(method)
        source = self.source()
        requests = [self.summary_request(method, source) for _ in range(6)]
        requests[0]["limits"] = {"max_attempts": 1}
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda request: self.store.perform("submit_analysis", request), requests))
        self.assertEqual(len({record["analysis_id"] for record in results}), 1)
        self.assertEqual(sum(not record["shared"] for record in results), 1)
        status = self.wait(results[0]["analysis_id"])
        self.assertEqual(len(status["attempts"]), 1)
        self.assertEqual(status["limits"]["max_attempts"], 1)

    def test_scoped_assessments_require_human_evidence_and_preserve_original_verification(self) -> None:
        """Accept no inputs; reject false promotion/conflicts; block invalidated execution/reuse; return nothing."""
        method = self.methods()["dataset_summary"]
        self.verify(method)
        source = self.source()
        request = self.summary_request(method, source)
        submitted = self.store.perform("submit_analysis", request)
        result = self.wait(submitted["analysis_id"])
        assessment = self.audit(method=method, assessment="approved", scope={"source_ids": []}, evidence=["a" * 32], expected_revision=0)
        with self.assertRaises(AnalysisError):
            self.store.perform("record_method_assessment", assessment)
        approval = {"decision": "approved", "method": method, "scope": {"source_ids": []}, "evidence_id": "a" * 32,
                    "source": "User approval reference", "statement": "Approve exact method version for stated scope"}
        approved = self.store.perform("record_method_assessment", {**assessment, "human_approval": approval})
        self.assertEqual(approved["revision"], 1)
        with self.assertRaises(AnalysisError):
            self.store.perform("record_method_assessment", {**assessment, **self.audit(), "human_approval": approval})
        self.store.perform("record_method_assessment", self.audit(method=method, assessment="invalidated", scope={"source_ids": []}, evidence=["Defect fixture"], expected_revision=1))
        with self.assertRaises(AnalysisError):
            self.store.perform("submit_analysis", {**request, **self.audit()})
        status = self.store.perform("get_analysis", {"analysis_id": result["analysis_id"]})
        self.assertEqual(status["current_validity"]["status"], "invalidated")
        self.assertEqual(status["verification_id"], result["verification_id"])
        self.assertEqual(self.output(result)["current_validity"]["status"], "invalidated")

    def test_publication_reconciliation_version_races_and_filtered_cursors(self) -> None:
        """Accept no inputs; recover published bundles after rollback, serialize versions and pin list filters; return nothing."""
        spec, files = bundle("dataset_summary")
        request = self.audit(schema_version=1, method_id="recoverable", version=1, specification=spec, files=files)
        with patch.object(self.store, "event", side_effect=RuntimeError("Interrupted after publication")), self.assertRaises(RuntimeError):
            self.store.perform("register_method", request)
        self.assertTrue((self.runtime / "methods" / "recoverable" / "1" / "manifest.json").is_file())
        recovered = self.store.perform("register_method", request)
        self.assertEqual(recovered["method"]["version"], 1)
        racing = [{**request, **self.audit(), "method_id": "racing"} for _ in range(2)]
        def register(arguments: dict) -> str:
            """Accept registration fields; return success/conflict code for concurrent allocation."""
            try:
                self.store.perform("register_method", arguments)
                return "success"
            except AnalysisError as exc:
                return exc.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(register, racing))
        self.assertEqual(sorted(outcomes), ["success", "version_conflict"])
        page = self.store.perform("list_methods", {"limit": 1})
        self.assertIsNotNone(page["next_cursor"])
        self.assertEqual(len(self.store.perform("list_methods", {"limit": 1, "cursor": page["next_cursor"]})["methods"]), 1)
        with self.assertRaises(AnalysisError):
            self.store.perform("list_methods", {"limit": 1, "cursor": page["next_cursor"], "method_id": "racing"})

    def test_configured_limits_are_strict_file_relative_and_snapshot_on_submission(self) -> None:
        """Accept no inputs; enforce request ceilings/default retries and preserve original settings on restart; return nothing."""
        config = self.runtime / "analysis.toml"
        config.write_text("runtime_directory='data'\n[stores]\nsource='../producer'\n[limits]\nwall_seconds=2\ntotal_seconds=6\n", encoding="utf-8")
        settings = Settings.load(config)
        self.assertEqual(settings.runtime_directory, self.runtime / "data")
        self.assertEqual(settings.stores["source"], self.runtime.parent / "producer")
        for limits in ({"wall_seconds": 3}, {"max_attempts": True}, {"unknown": 1}, {"log_bytes": 95}):
            with self.assertRaises(AnalysisError):
                settings.effective(limits)
        self.assertEqual(settings.effective({})["max_attempts"], 1)
        method = self.methods()["dataset_summary"]
        job = self.store.perform("verify_method", self.audit(schema_version=1, method=method, limits={"wall_seconds": 2, "total_seconds": 6, "max_attempts": 2}))
        changed = Store(Settings(self.runtime, limits={**MAX_LIMITS, "wall_seconds": 1, "total_seconds": 1, "max_attempts": 1}))
        status = changed.perform("get_analysis", {"analysis_id": job["analysis_id"]})
        self.assertEqual(status["limits"]["wall_seconds"], 2)
        self.assertEqual(status["remaining_attempts"], 2)
