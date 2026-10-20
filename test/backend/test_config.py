"""Verify backend configuration loading with isolated workspace fixtures."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from tempfile import TemporaryDirectory
import tomllib
import unittest
from unittest.mock import patch

from src.backend.config import Config, load_config


class TestConfig(unittest.TestCase):
    """Provide isolated configuration checks using unittest's standard constructor.

    Construction accepts the inherited unittest method selection. Test methods
    create and remove temporary fixtures beneath this module's directory and
    report incorrect behavior through assertion failures.
    """

    def test_valid_configuration(self: TestConfig) -> None:
        """Verify explicit settings and drive/UNC paths; return None and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory) / "settings.toml"
            for root in (r"D:\local\srim", r"\\server\share\srim"):
                with self.subTest(root=root):
                    path.write_text(
                        f"[time]\ntimezone = 'system'\n[srim]\nroot = '{root}'\n",
                        encoding="utf-8",
                    )
                    self.assertEqual(load_config(path), Config("system", Path(root)))

    def test_timezone_default(self: TestConfig) -> None:
        """Verify absent timezone settings default to UTC; return None and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory) / "settings.toml"
            for prefix in ("", "[time]\n"):
                with self.subTest(prefix=prefix):
                    path.write_text(prefix + "[srim]\nroot = 'D:\\srim'\n", encoding="utf-8")
                    self.assertEqual(load_config(path).timezone, "UTC")

    def test_window_settings(self: TestConfig) -> None:
        """Verify self's independent boolean defaults, explicit values, reloads and immutability.

        Write and remove isolated TOML fixtures; return None. Invalid flag values
        must raise ValueError, and incorrect behavior raises assertion failures.
        No executable is launched or installation file accessed.
        """
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory) / "settings.toml"
            prefix = "[srim]\nroot = 'D:\\srim'\n"
            path.write_text(prefix, encoding="utf-8")
            default = load_config(path)
            self.assertTrue(default.hide_sr_window)
            self.assertTrue(default.hide_trim_window)
            self.assertEqual(default, Config("UTC", Path(r"D:\srim")))
            for sr in (False, True):
                for trim in (False, True):
                    with self.subTest(sr=sr, trim=trim):
                        path.write_text(prefix + f"hide_sr_window = {str(sr).lower()}\nhide_trim_window = {str(trim).lower()}\n", encoding="utf-8")
                        loaded = load_config(path)
                        self.assertIs(loaded.hide_sr_window, sr)
                        self.assertIs(loaded.hide_trim_window, trim)
            for key in ("hide_sr_window", "hide_trim_window"):
                path.write_text(prefix + f"{key} = false\n", encoding="utf-8")
                first = load_config(path)
                self.assertIs(getattr(first, key), False)
                self.assertIs(getattr(first, "hide_trim_window" if key == "hide_sr_window" else "hide_sr_window"), True)
                with self.assertRaises(FrozenInstanceError):
                    setattr(first, key, True)
                path.write_text(prefix + f"{key} = true\n", encoding="utf-8")
                self.assertIs(getattr(load_config(path), key), True)
                self.assertIs(getattr(first, key), False)
                for invalid in ("0", "1", "'true'", "'false'", "[]", "{}", "1.0"):
                    with self.subTest(key=key, invalid=invalid):
                        path.write_text(prefix + f"{key} = {invalid}\n", encoding="utf-8")
                        with self.assertRaisesRegex(ValueError, rf"srim\.{key}"):
                            load_config(path)

    def test_timezone_validation(self: TestConfig) -> None:
        """Verify timezone syntax, types, and normalization; return None and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory) / "settings.toml"
            valid = {
                "UTC": "UTC",
                "system": "system",
                "UTC+23:59": "UTC+23:59",
                "UTC-23:59": "UTC-23:59",
                "UTC+00:01": "UTC+00:01",
                "UTC-00:01": "UTC-00:01",
                "UTC+00:00": "UTC",
                "UTC-00:00": "UTC",
            }
            for value, expected in valid.items():
                with self.subTest(value=value):
                    path.write_text(
                        f"[time]\ntimezone = '{value}'\n[srim]\nroot = 'D:\\srim'\n",
                        encoding="utf-8",
                    )
                    self.assertEqual(load_config(path).timezone, expected)
            invalid = (
                "''", "'utc'", "' UTC'", "'UTC '", "'SYSTEM'", "'UTC+24:00'",
                "'UTC+00:60'", "'UTC+1:00'", "'UTC+01:0'", "'UTC+01:000'",
                "'UTC+٠١:00'", "'America/New_York'", "'UTC*01:00'", "12", "true", "[]",
            )
            for value in invalid:
                with self.subTest(value=value):
                    path.write_text(
                        f"[time]\ntimezone = {value}\n[srim]\nroot = 'D:\\srim'\n",
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(ValueError, r"time\.timezone"):
                        load_config(path)

    def test_schema_validation(self: TestConfig) -> None:
        """Verify table structure, required root, and allowed keys; return None and clean fixtures."""
        invalid = (
            ("", r"srim\.root"),
            ("[srim]\n", r"srim\.root"),
            ("[time]\ntimezone = 'UTC'\n", r"srim\.root"),
            ("srim = 12\n", "srim"),
            ("time = 12\n[srim]\nroot = 'D:\\srim'\n", "time"),
            ("[extra]\n", "extra"),
            ("[time]\nextra = 1\n[srim]\nroot = 'D:\\srim'\n", r"time\.extra"),
            ("[srim]\nroot = 'D:\\srim'\nextra = 1\n", r"srim\.extra"),
            ("[srim.root]\n", r"srim\.root"),
        )
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory) / "settings.toml"
            for content, message in invalid:
                with self.subTest(content=content):
                    path.write_text(content, encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, message):
                        load_config(path)

    def test_root_validation(self: TestConfig) -> None:
        """Verify root types and absolute paths without existence checks; return None and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory) / "settings.toml"
            for value in ("12", "true", "[]", "''", "'relative'", "'D:srim'", "'/srim'", "'\\srim'"):
                with self.subTest(value=value):
                    path.write_text(f"[srim]\nroot = {value}\n", encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, r"srim\.root"):
                        load_config(path)
            root = Path(directory) / "nonexistent-srim"
            self.assertFalse(root.exists())
            path.write_text(f"[srim]\nroot = '{root}'\n", encoding="utf-8")
            self.assertEqual(load_config(path).srim_root, root)
            self.assertFalse(root.exists())

    def test_malformed_toml(self: TestConfig) -> None:
        """Verify malformed TOML raises its original exception type; return None and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory) / "settings.toml"
            path.write_text("[broken", encoding="utf-8")
            with self.assertRaises(tomllib.TOMLDecodeError):
                load_config(path)

    def test_file_access_errors(self: TestConfig) -> None:
        """Verify missing files and unchanged mocked access errors; return None and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory) / "missing.toml"
            with self.assertRaises(FileNotFoundError):
                load_config(path)
            for error in (PermissionError("denied"), IsADirectoryError("directory")):
                with self.subTest(error=type(error).__name__):
                    with patch.object(Path, "open", side_effect=error):
                        with self.assertRaises(type(error)) as caught:
                            load_config(path)
                        self.assertIs(caught.exception, error)

    def test_immutable_configuration(self: TestConfig) -> None:
        """Verify loaded fields reject reassignment; return None and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory) / "settings.toml"
            path.write_text("[srim]\nroot = 'D:\\srim'\n", encoding="utf-8")
            config = load_config(path)
            with self.assertRaises(FrozenInstanceError):
                config.timezone = "system"
            with self.assertRaises(FrozenInstanceError):
                config.srim_root = Path(r"D:\other")

    def test_reloads_configuration(self: TestConfig) -> None:
        """Verify each call reads current contents without mutating earlier results; return None and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory) / "settings.toml"
            path.write_text("[srim]\nroot = 'D:\\srim'\n", encoding="utf-8")
            first = load_config(path)
            path.write_text(
                "[time]\ntimezone = 'system'\n[srim]\nroot = 'D:\\other'\n",
                encoding="utf-8",
            )
            self.assertEqual(load_config(path), Config("system", Path(r"D:\other")))
            self.assertEqual(first, Config("UTC", Path(r"D:\srim")))
