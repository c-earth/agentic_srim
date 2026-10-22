"""Verify bounded exact-byte file access and exclusive MCP publication."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from src.mcp.common.files import acquire_lock, allowed_source, publish_files, publish_json, read_verified_chunk, safe_path
from test_contracts import Fixture


class TestFiles(Fixture):
    """Check traversal/device/stream protection, hashes, bounds and lock ownership."""

    def test_verified_chunks_and_safety(self: TestFiles) -> None:
        """Preserve exact binary slices, reject changed hashes and unsafe relative names."""
        payload = bytes(range(256)) * 3000
        source = self.root / "source.bin"
        source.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        result = read_verified_chunk(source, digest, len(payload), offset=260000, length=10000)
        self.assertEqual(base64.b64decode(result["content"]), payload[260000:270000])
        for relative in ("../escape", "C:/escape", "x:stream", "NUL", "x\\y", "x./y", "x//y"):
            with self.assertRaises(ValueError):
                safe_path(self.root, relative)
        with self.assertRaises(ValueError):
            allowed_source(self.science, str(self.repository / "README.md"), directory=False)
        source.write_bytes(b"changed")
        with self.assertRaises(ValueError):
            read_verified_chunk(source, digest, len(payload), offset=0, length=1)

    def test_publication_and_recursive_locks(self: TestFiles) -> None:
        """Verify exclusive publication, safe replacement and recursive-lock rejection."""
        target = self.root / "record.json"
        publish_json(target, {"value": "1.20"})
        with self.assertRaises(FileExistsError):
            publish_json(target, {"value": "2"})
        publish_json(target, {"value": "2"}, replace=True)
        result = publish_files(self.root / "bundle", {"nested/data.bin": b"exact"})
        self.assertEqual(result["files"][0]["sha256"], hashlib.sha256(b"exact").hexdigest())
        with acquire_lock(self.root / "lease.lock", timeout_seconds=0):
            with self.assertRaises(OSError):
                with acquire_lock(self.root / "lease.lock", timeout_seconds=0):
                    pass
