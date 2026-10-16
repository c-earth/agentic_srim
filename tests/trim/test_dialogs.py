"""Accept no external inputs; verify native dialog policies and request isolation."""

import unittest

from agentic_srim.backends.trim.runner import (
    CASCADE_TITLE, EVERY_ACTIVE, EVERY_QUESTION, FILE_ACTIVE, dialog_response,
)


class DialogTests(unittest.TestCase):
    """Construct unittest cases without parameters; expose exact-response checks."""

    def test_mode_eight_requires_request_and_completed_activation_sequence(self) -> None:
        """Accept no inputs; reject extra prompts without matching mode, owner, choice, or state."""
        base = {"title": CASCADE_TITLE, "message": EVERY_QUESTION, "owner_pid": 11,
                "native_pid": 11, "count": 3, "collisions": 1, "cascade_selected": True,
                "file_active": True, "calculation_mode": 8, "collision_records": "summary",
                "every_selected": False}
        self.assertEqual(dialog_response(**base), (7, "No"))
        self.assertEqual(dialog_response(**(base | {"collision_records": "every"})), (6, "Yes"))
        for changes in ({"owner_pid": 12}, {"title": "Other"}, {"message": EVERY_QUESTION + "extra"},
                        {"collisions": 0}, {"collisions": 2}, {"cascade_selected": False},
                        {"file_active": False}, {"calculation_mode": 6}, {"calculation_mode": 7},
                        {"calculation_mode": None}, {"collision_records": None}, {"collision_records": "unknown"}):
            with self.subTest(changes=changes):
                self.assertIsNone(dialog_response(**(base | changes)))

    def test_every_notification_requires_preceding_explicit_yes(self) -> None:
        """Accept no inputs; acknowledge only the exact every-record notification after Yes."""
        base = {"title": CASCADE_TITLE, "message": EVERY_ACTIVE, "owner_pid": 11,
                "native_pid": 11, "count": 3, "collisions": 1, "cascade_selected": True,
                "file_active": True, "calculation_mode": 8, "collision_records": "every",
                "every_selected": True}
        self.assertEqual(dialog_response(**base), (2, "OK"))
        for changes in ({"every_selected": False}, {"collision_records": "summary"},
                        {"file_active": False}, {"owner_pid": 12}, {"message": EVERY_ACTIVE + "extra"}):
            with self.subTest(changes=changes):
                self.assertIsNone(dialog_response(**(base | changes)))
        self.assertIsNone(dialog_response(**(base | {"title": "TRIM", "message": "No cascade details available with KP Damage"})))

    def test_file_active_notification_requires_requested_cascade_selection(self) -> None:
        """Accept no inputs; preserve the prerequisite for acknowledging file activation."""
        args = (CASCADE_TITLE, FILE_ACTIVE, 11, 11, 3, 1)
        self.assertIsNone(dialog_response(*args, False, False, 8, "summary", False))
        self.assertEqual(dialog_response(*args, True, False, 8, "summary", False), (2, "OK"))
