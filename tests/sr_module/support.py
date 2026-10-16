"""Accept repository fixtures; expose isolated SR test runtimes and a fixture-backed runner."""

import json
from pathlib import Path
import shutil
import tempfile
import unittest

from agentic_srim.backends.sr_module.input import OUTPUT_NAME
from agentic_srim.capabilities.stopping_range.inputs import Request
from agentic_srim.config import Settings


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
REQUEST = FIXTURES / "request.json"
NATIVE = FIXTURES / "installed_example.txt"


class IsolatedTest(unittest.TestCase):
    """Construct unittest cases; expose isolated runtime/settings and explicit SR fixture inputs."""

    def setUp(self) -> None:
        """Accept no inputs; allocate a temporary workspace and explicit request."""
        parent = ROOT / "runtime" / "test_workspaces"
        parent.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.temporary.cleanup)
        self.runtime = Path(self.temporary.name).resolve()
        self.assertTrue(self.runtime.is_relative_to(parent.resolve()))
        self.data = json.loads(REQUEST.read_text(encoding="utf-8"))
        self.request = Request.from_dict(self.data)
        installation = Settings.load(ROOT / "configs" / "application.toml")
        self.settings = Settings(installation.sr_module_directory, self.runtime)

    def simulate(self, workspace: Path, archive: Path, timeout: float, cancellation: Path) -> int:
        """Accept runner arguments; assert stale cleanup, copy fixture, return zero."""
        self.assertEqual(workspace, self.runtime / "workers" / "worker-1" / "sr_module")
        self.assertFalse((workspace / OUTPUT_NAME).exists())
        shutil.copyfile(NATIVE, workspace / OUTPUT_NAME)
        (archive / "stdout.log").write_bytes(b"")
        (archive / "stderr.log").write_bytes(b"")
        return 0
