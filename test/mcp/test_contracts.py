"""Verify strict MCP JSON/configuration contracts with isolated project fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.backend.paths import create_base_directories
from src.mcp.common.contracts import canonical_bytes, error_response, load_context, load_json, make_response, validate_document


class Fixture(unittest.TestCase):
    """Provide temporary project/configuration state beneath repository .cache only."""

    def setUp(self: Fixture) -> None:
        """Create self's isolated workspace and validated contexts; return None."""
        self.repository = Path(__file__).absolute().parents[2]
        base = self.repository / ".cache" / "mcp-verification"
        base.mkdir(parents=True, exist_ok=True)
        self.temporary = TemporaryDirectory(dir=base)
        self.root = Path(self.temporary.name)
        self.assertTrue(self.root.is_relative_to(base))
        self.root.joinpath("settings.toml").write_text('[time]\ntimezone = "UTC"\n[srim]\nroot = ' + json.dumps(str(self.root / "no-native-installation")) + '\n', encoding="utf-8")
        self.root.joinpath("mcp.toml").write_bytes((self.repository / "mcp.toml").read_bytes())
        self.science = load_context(self.root, "srim_mcp")
        self.project = load_context(self.root, "project_mcp")
        self.paths, self.config = self.project.paths, self.project.config
        create_base_directories(self.paths)

    def tearDown(self: Fixture) -> None:
        """Remove self's verified isolated workspace after all owned work has stopped."""
        self.assertTrue(self.root.is_relative_to(self.repository / ".cache" / "mcp-verification"))
        self.temporary.cleanup()


class TestContracts(Fixture):
    """Exercise finite JSON, duplicate rejection, strict schemas and explicit config."""

    def test_json_and_envelopes(self: TestContracts) -> None:
        """Check nested duplicate/nonfinite rejection and deterministic finite responses."""
        for payload in (b'{"a":1,"a":2}', b'{"x":{"a":1,"a":2}}', b'{"v":NaN}', b'[]', b'\xff'):
            with self.assertRaises(ValueError):
                load_json(payload)
        self.assertEqual(load_json(b'{"x":[],"empty":{},"v":"1.20"}'), {"x": [], "empty": {}, "v": "1.20"})
        self.assertEqual(canonical_bytes({"b": "1.20", "a": 1}), b'{\n  "a": 1,\n  "b": "1.20"\n}\n')
        self.assertTrue(make_response({"value": "1.20"})["ok"])
        self.assertEqual(error_response(ValueError("conflict: stale"))["error"]["code"], "conflict")
        schema = {"type": "object", "additionalProperties": False, "required": ["count"], "properties": {"count": {"type": "integer"}}}
        with self.assertRaises(ValueError):
            validate_document({"count": True}, schema)

    def test_configuration_limits(self: TestContracts) -> None:
        """Reject unknown config, relative roots, booleans and inconsistent limits."""
        original = self.root.joinpath("mcp.toml").read_text()
        for changed in (original.replace("max_parallel = 4", "max_parallel = true"), original.replace("max_parallel = 4", "max_parallel = 33"), original + '\nunknown = 2\n', original.replace("file_chunk_bytes = 262144", "file_chunk_bytes = 9999999")):
            self.root.joinpath("mcp.toml").write_text(changed)
            with self.assertRaises(ValueError):
                load_context(self.root, "srim_mcp")
        with self.assertRaises(ValueError):
            load_context(Path("relative"), "srim_mcp")
