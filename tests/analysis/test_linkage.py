"""Accept unittest discovery; verify Coordinator intent/checkpoint/atomic-link recovery."""

from copy import deepcopy
import uuid

from case.records import CaseError
from case.storage import Store as CaseStore
from analysis_support import AnalysisTest


class LinkageTests(AnalysisTest):
    """Construct independent Case/Analysis stores; verify interrupted-link recovery without reruns."""

    def test_pending_actions_preserve_intent_and_atomic_resolution_replays(self) -> None:
        """Accept no inputs; simulate lost submit/checkpoint/link replies and stale writers; return nothing."""
        method = self.methods()["dataset_summary"]
        self.verify(method)
        source = self.source()
        cases = CaseStore(self.runtime)
        case = cases.perform("create_case", self.audit(title="Review synthetic results", original_request="Software regression only"))["case"]
        submission = self.summary_request(method, source)
        action = {"schema_version": 1, "action_id": uuid.uuid4().hex, "kind": "analysis", "state": "intent", "submission": submission}
        request = self.audit(case_id=case["id"], expected_revision=1, progress={"pending_actions": [action], "next_action": "Submit saved intent"})
        intent = cases.perform("record_case_progress", request)["case"]
        self.assertEqual(cases.perform("record_case_progress", request)["case"], intent)
        first = self.store.perform("submit_analysis", submission)
        self.assertEqual(self.store.perform("submit_analysis", submission)["analysis_id"], first["analysis_id"])
        submitted_action = {**action, "state": "submitted", "analysis_id": first["analysis_id"]}
        checkpoint = cases.perform("record_case_progress", self.audit(case_id=case["id"], expected_revision=2, progress={"pending_actions": [submitted_action]}))["case"]
        result = self.wait(first["analysis_id"])
        reference = {**result["output"], "store_id": "local"}
        attachment = self.audit(case_id=case["id"], expected_revision=3, artifact={"owner": "analysis", "artifact_id": first["analysis_id"],
                                "kind": "numerical_result", "purpose": "Regression review", "provenance": "CHECKED synthetic method output", "reference": reference},
                                resolve_action_id=action["action_id"], progress={"next_action": "Interpret with software limitations",
                                "analyses": [{"text": "Sampled summary", "artifact_refs": [{"owner": "analysis", "artifact_id": first["analysis_id"]}]}]})
        with self.assertRaises(CaseError):
            cases.perform("link_case_artifact", {**attachment, **self.audit(), "expected_revision": 2})
        resolved = cases.perform("link_case_artifact", attachment)["case"]
        self.assertEqual(resolved["pending_actions"][0]["state"], "resolved")
        self.assertEqual(cases.perform("link_case_artifact", attachment)["case"], resolved)
        self.assertEqual(resolved["status"], "active")
        self.assertEqual(cases.perform("get_case", {"case_id": case["id"]})["artifact_checks"][0]["state"], "verified")
        self.assertEqual(len(self.wait(first["analysis_id"])["attempts"]), 1)
        wrong = deepcopy(submitted_action)
        wrong["submission"]["parameters"]["coordinate"] = "changed"
        with self.assertRaises(CaseError):
            cases.perform("record_case_progress", self.audit(case_id=case["id"], expected_revision=4, progress={"pending_actions": [wrong]}))
        second = cases.perform("create_case", self.audit(title="Other consumer", original_request="Reuse software result"))["case"]
        cases.perform("link_case_artifact", self.audit(case_id=second["id"], expected_revision=1, artifact=attachment["artifact"]))
        self.assertEqual(cases.perform("get_case", {"case_id": case["id"], "revision": 1})["case"]["pending_actions"], [])
