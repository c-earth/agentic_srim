"""Accept unittest construction; expose disposable case stores and request helpers.

Workspaces live beneath runtime/test_workspaces and are removed after each test.
No simulator configuration, installation or execution is required.
"""

from pathlib import Path
import tempfile
import unittest
import uuid

from case.storage import Store


ROOT = Path(__file__).resolve().parents[2]


class CaseTest(unittest.TestCase):
    """Construct unittest cases; provide isolated runtime/store and mutation helpers."""

    def setUp(self) -> None:
        """Accept no inputs; create a disposable runtime and register cleanup; return nothing."""
        parent = ROOT / "runtime" / "test_workspaces"
        parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(temporary.cleanup)
        self.runtime = Path(temporary.name).resolve()
        self.assertTrue(self.runtime.is_relative_to(parent.resolve()))
        self.store = Store(self.runtime)

    def audit(self, **values) -> dict:
        """Accept argument overrides; return fresh actor/reason/idempotency fields."""
        return {"actor": "test coordinator", "reason": "Regression checkpoint",
                "idempotency_key": uuid.uuid4().hex, **values}

    def create(self, **values) -> dict:
        """Accept create-field overrides; create and return a default case snapshot."""
        return self.store.perform("create_case", self.audit(**{
            "title": "Helium depth", "original_request": "Compare archived helium profiles", **values}))["case"]

    def change(self, record: dict, patch: dict, **values) -> dict:
        """Accept snapshot/patch/audit overrides; return an updated case snapshot."""
        return self.store.perform("update_case", self.audit(case_id=record["id"],
            expected_revision=record["revision"], patch=patch, **values))["case"]
