"""Accept repository fixtures and unittest discovery; expose execution/test_workspaces.py checks without native execution unless opted in."""

import subprocess
import sys
from agentic_srim.config import Settings
from agentic_srim.execution.workspaces import locked
from sr_module.support import IsolatedTest


class WorkspaceTests(IsolatedTest):
    """Construct unittest cases; verify execution contracts and report failures."""

    def test_worker_lock_excludes_another_process(self) -> None:
        """Accept no inputs; verify a second Python process cannot lock the worker."""
        code = "from pathlib import Path; import sys; from agentic_srim.execution.workspaces import locked; context=locked(Path(sys.argv[1])); context.__enter__()"
        with locked(self.runtime):
            result = subprocess.run([sys.executable, "-c", code, str(self.runtime)], capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"worker is busy", result.stderr)

    def test_config_protects_source_and_bounds(self) -> None:
        """Accept no inputs; verify disjoint paths and finite operational bounds."""
        for source, runtime in [(self.runtime, self.runtime), (self.runtime, self.runtime / "child"), (self.runtime / "child", self.runtime)]:
            with self.assertRaises(ValueError):
                Settings(source, runtime)
        for timeout in (0, float("inf"), True, 3601):
            with self.assertRaises(ValueError):
                Settings(self.settings.sr_module_directory, self.runtime, timeout)
