"""Verify explicit pipeline checkpoint results against frozen recovery evidence."""

from __future__ import annotations

from pathlib import Path
import unittest

from src.srim.trim_results import parse_trim_checkpoint_results
from src.srim.trim_recovery import recover_trim_checkpoint


FIXTURES = Path(__file__).parent / "fixtures/trim/recovery"


class TestTRIMResults(unittest.TestCase):
    """Use unittest construction and committed checkpoint bytes without native I/O."""

    def test_preserved_recovery_matrix(self: TestTRIMResults) -> None:
        """Keep all 23 modes and generalized/cumulative recovery values exact; return None."""
        cases = sorted(FIXTURES.glob("*/mode-*"))
        self.assertEqual(len(cases), 23)
        cases += sorted((FIXTURES / "generalized").iterdir())
        cases += sorted((FIXTURES / "comparison-200").iterdir())
        for case in cases:
            saved = {p.name: p.read_bytes() for p in case.glob("*.sav")}
            if not saved:
                continue
            with self.subTest(case=case.name):
                files = {"SRIM Restore/" + name: data for name, data in saved.items()}
                before = dict(files)
                result = parse_trim_checkpoint_results(files)
                self.assertTrue(result["complete"])
                self.assertFalse(result["native"]["complete"])
                self.assertEqual(result["recovery"], recover_trim_checkpoint(saved))
                files["TDATA.txt"] = b"stale native summary"
                self.assertEqual(parse_trim_checkpoint_results(files)["recovery"], result["recovery"])
                self.assertEqual({key: files[key] for key in before}, before)

    def test_invalid_or_nested_checkpoint(self: TestTRIMResults) -> None:
        """Retain checkpoint errors independently from native missing-file issues; return None."""
        case = FIXTURES / "srim_2013_std/mode-02"
        files = {"SRIM Restore/" + p.name: p.read_bytes() for p in case.glob("*.sav")}
        ioniz = next(name for name in files if name.casefold().endswith("/ioniz.sav"))
        for changes in ({ioniz: b"broken"}, {"SRIM Restore/sub/extra.sav": b"nested"}):
            result = parse_trim_checkpoint_results({**files, **changes})
            self.assertFalse(result["complete"])
            self.assertIsNone(result["recovery"])
            self.assertEqual(result["issues"][0]["code"], "checkpoint-recovery")
        self.assertFalse(parse_trim_checkpoint_results({})["complete"])
        for changes in ({"../escape": b""}, {ioniz.swapcase(): b"collision"}, {"bad": "not bytes"}):
            with self.assertRaises(ValueError):
                parse_trim_checkpoint_results({**files, **changes})
