"""Accept fixture names; expose isolated test runtimes without native execution."""

import json
from pathlib import Path
import shutil
import tempfile
import unittest

from agentic_srim.capabilities.trim.inputs import Request
from agentic_srim.config import Settings


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
CASES = tuple(f"mode_{mode}" for mode in range(1, 9)) + ("summary_mode_8", "every_mode_8")


def fixture_request(mode: int | str = 2) -> Request:
    """Return a validated request for an integer mode or a captured variant name."""
    name = f"mode_{mode}" if isinstance(mode, int) else mode
    return Request.from_dict(json.loads((FIXTURES / name / "request.json").read_text()))


class IsolatedTest(unittest.TestCase):
    """Construct unittest cases; expose temporary runtime paths and explicit requests."""

    def setUp(self) -> None:
        """Accept no inputs; allocate a temporary runtime beneath the project."""
        parent = ROOT / "runtime" / "test_workspaces"
        parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(temporary.cleanup)
        self.runtime = Path(temporary.name).resolve()
        installation = Settings.load(ROOT / "configs" / "application.toml")
        self.settings = Settings(installation.sr_module_directory, self.runtime, trim_directory=installation.trim_directory)
        self.request = fixture_request()
        self.sequence = 0

    def copy_fixture(self, mode: int | str = 2) -> Path:
        """Copy a captured mode/variant into a fresh temporary archive; return its path."""
        self.sequence += 1
        archive = self.runtime / f"archive_{self.sequence}"
        name = f"mode_{mode}" if isinstance(mode, int) else mode
        shutil.copytree(FIXTURES / name, archive)
        return archive
