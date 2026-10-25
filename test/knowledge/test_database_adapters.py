"""Exercise pure schema recognition dispatch with synthetic code-owned recognizers."""

from __future__ import annotations

import unittest
from unittest.mock import patch
from src.knowledge.database_adapters import probe_database_adapters
from src.srim.common.types import JSONObject


class Recognizer:
    """Provide a synthetic pure probe; identity/result select its returned decision."""

    def __init__(self: Recognizer, identity: str, result: str) -> None:
        """Store identity/result for testing and return None without I/O."""
        self.adapter_id, self.adapter_version, self.result = identity, 1, result

    def probe(self: Recognizer, profile: JSONObject) -> JSONObject:
        """Return synthetic evidence for profile; mutate only the received defensive copy."""
        profile["mutated"] = True
        return {"adapter_id": self.adapter_id, "adapter_version": self.adapter_version, "result": self.result, "evidence": ["Synthetic sampled key: neutral"]}


class TestDatabaseAdapters(unittest.TestCase):
    """Verify explicit recognition states without installing a real scientific adapter."""

    def test_empty_match_ambiguous_and_insufficient(self: TestDatabaseAdapters) -> None:
        """Distinguish all result states and isolate recognizer input mutations."""
        profile = {"samples": []}
        with patch("src.knowledge.database_adapters.DATABASE_ADAPTERS", ()):
            self.assertEqual(probe_database_adapters(profile), {"state": "unknown", "candidates": []})
        for results, state in ((["match"], "known"), (["match", "match"], "ambiguous"), (["no_match"], "unknown"), (["insufficient_evidence"], "insufficient_evidence"), (["invalid"], "insufficient_evidence")):
            with self.subTest(results=results), patch("src.knowledge.database_adapters.DATABASE_ADAPTERS", tuple(Recognizer("neutral-" + str(index), result) for index, result in enumerate(results))):
                detected = probe_database_adapters(profile)
                self.assertEqual(detected["state"], state)
        self.assertEqual(profile, {"samples": []})

    def test_invalid_profiles_probe_failures_and_metadata(self: TestDatabaseAdapters) -> None:
        """Reject invalid profiles/registrations and expose ordinary probe failures as evidence."""
        for profile in ([], {"value": float("nan")}, {"value": "x" * 2097153}):
            with self.assertRaises(ValueError):
                probe_database_adapters(profile)
        recognizer = Recognizer("neutral", "match")
        with patch("src.knowledge.database_adapters.DATABASE_ADAPTERS", (recognizer,)), patch.object(recognizer, "probe", side_effect=RuntimeError("fixture")):
            self.assertEqual(probe_database_adapters({})["state"], "insufficient_evidence")
        with patch("src.knowledge.database_adapters.DATABASE_ADAPTERS", (Recognizer("invalid_name", "match"),)), self.assertRaises(ValueError):
            probe_database_adapters({})
