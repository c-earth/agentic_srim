"""Verify strict config presentation qualification and protected recoil settings."""

from __future__ import annotations

import hashlib
import unittest

from src.srim.trim.config import _parse_trim_config, assess_trim_config_change
from src.srim.trim.dialogs import ENGINES


CONFIG = ("\r\n".join(["----TRIM.cfg : TRIM Color Configuration----", "", "[Ion Colors - MOVE, STOP]", " 255 0 ", "", "[12 Recoil Colors - MOVE, STOP]", *[f" {i} 33023 5614335 " for i in range(1, 13)], "", "[Distribution Plot Labels - SIZE, COLOR]", " 6 8388608 ", "", "[4 Distribution Plot Colors]", " 255 8388608 65280 16776960 ", "", "[Plot Background, Outer Box, Axes, Layer Lines, Layer Names]", "16777215 255 0 0 0", "", "Maximum number of secondary recoils (default=20000)", " 20000 ", "Note: 100k recoils requires extra 2MB of memory, 1M recoils requires 35MB."]) + "\r\n").encode("ascii")


class TestTRIMConfig(unittest.TestCase):
    """Construct pure unittest cases with fixed portable config bytes and no I/O."""

    def test_equal_and_argument_validation(self: TestTRIMConfig) -> None:
        """Accept equal unknown bytes and reject malformed arguments; return None."""
        result = assess_trim_config_change(b"unknown", b"unknown", engine_sha256="0" * 64)
        self.assertEqual(result["status"], "equal")
        self.assertIsNone(result["protected_values"])
        self.assertEqual(result["changes"], [])
        for digest in (None, "short", "A" * 64):
            with self.assertRaises(ValueError):
                assess_trim_config_change(CONFIG, CONFIG, engine_sha256=digest)
        with self.assertRaises(ValueError):
            assess_trim_config_change("not bytes", CONFIG, engine_sha256=ENGINES["2013"])

    def test_all_presentation_fields_and_limits(self: TestTRIMConfig) -> None:
        """Qualify each colour group/help spelling without conflating limits; return None."""
        parsed = _parse_trim_config(CONFIG)
        self.assertEqual(len(parsed["colours"]), 36)
        for limit in (20000, 200000):
            original = CONFIG.replace(b" 20000 \r\n", f" {limit} \r\n".encode())
            for line_index in (3, *range(6, 18), 20, 23, 26):
                lines = original.decode().splitlines()
                tokens = lines[line_index].split()
                tokens[-1] = "123456"
                lines[line_index] = " ".join(tokens)
                after = ("\r\n".join(lines) + "\r\n").encode()
                result = assess_trim_config_change(original, after, engine_sha256=ENGINES["2013"])
                self.assertEqual(result["status"], "qualified")
                self.assertEqual(result["protected_values"]["maximum_secondary_recoils"], {"before": limit, "after": limit})
                self.assertTrue(all(change["classification"] == "presentation" for change in result["changes"]))
        after = CONFIG.replace(b"requires extra 2MB", b"requires 2MB").replace(b" 12 33023 5614335 ", b" 12 255 0 ")
        result = assess_trim_config_change(CONFIG, after, engine_sha256=ENGINES["2008"])
        self.assertEqual(result["status"], "qualified")
        self.assertEqual(result["before"]["sha256"], hashlib.sha256(CONFIG).hexdigest())
        self.assertEqual(result["after"]["size_bytes"], len(after))
        self.assertIn("help_text", [change["field"] for change in result["changes"]])
        formatted = CONFIG.replace(b"\r\n", b"\n").replace(b"\n 255 0 \n", b"\n255   0\n")[:-1]
        self.assertEqual(assess_trim_config_change(CONFIG, formatted, engine_sha256=ENGINES["2013"])["changes"][0]["classification"], "formatting")

    def test_protected_and_unknown_changes(self: TestTRIMConfig) -> None:
        """Reject changed label/recoil controls and unreviewed engines; return None."""
        for after in (CONFIG.replace(b" 20000 \r\n", b" 200000 \r\n"), CONFIG.replace(b" 6 8388608 ", b" 8 8388608 "), CONFIG.replace(b" 20000 \r\n", b" 100 \r\n").replace(b" 255 0 ", b" 1 2 ")):
            result = assess_trim_config_change(CONFIG, after, engine_sha256=ENGINES["2013"])
            self.assertEqual(result["status"], "different")
            self.assertIn("Protected", result["reason"])
            self.assertTrue(any(change["classification"] == "protected" for change in result["changes"]))
        self.assertEqual(assess_trim_config_change(CONFIG, CONFIG + b"x", engine_sha256="0" * 64)["status"], "different")

    def test_unknown_layout_and_malformed_values(self: TestTRIMConfig) -> None:
        """Reject unsupported sections, controls, shapes, encodings and tokens; return None."""
        for after in (CONFIG + b"extra\r\n", CONFIG[:-30], CONFIG.replace(b"Ion Colors", b"Ion Options"), CONFIG.replace(b" 12 33023", b" 11 33023"), CONFIG.replace(b" 255 0 ", b" 16777216 0 "), CONFIG.replace(b" 255 0 ", b" -1 0 "), CONFIG.replace(b" 255 0 ", b" x 0 "), CONFIG.replace(b" 255 0 ", b" 1 2 3 "), CONFIG.replace(b" 255 0 ", b" 1\t2 "), CONFIG.replace(b"\r\n", b"\r"), CONFIG.replace(b"\r\n\r\n", b"\r\n \r\n", 1), CONFIG.replace(b"requires extra", b"requires unknown"), CONFIG.replace(b"----TRIM", b"\xff---TRIM")):
            with self.subTest(after=after[:50]):
                result = assess_trim_config_change(CONFIG, after, engine_sha256=ENGINES["2013"])
                self.assertEqual(result["status"], "different")
                self.assertTrue(result["reason"])
