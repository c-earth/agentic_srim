"""Accept repository fixtures and unittest discovery; expose execution/test_processes.py checks without native execution unless opted in."""

import os
import subprocess
import sys
from unittest.mock import MagicMock, patch
from agentic_srim.execution.processes import CancelledError, is_process_alive, run
from sr_module.support import IsolatedTest


class ProcessTests(IsolatedTest):
    """Construct unittest cases; verify execution contracts and report failures."""

    def test_timeout_kills_and_reaps_native_process(self) -> None:
        """Accept no inputs; simulate an over-budget native process and verify cleanup."""
        process = MagicMock(pid=123)
        process.poll.return_value = None
        archive = self.runtime / "attempt"
        archive.mkdir()
        with patch("agentic_srim.execution.processes.subprocess.Popen", return_value=process), patch("agentic_srim.execution.processes.time.monotonic", side_effect=[0, 2]):
            with self.assertRaises(TimeoutError):
                run(self.runtime, archive, 1, self.runtime / "cancel")
        process.kill.assert_called_once()
        process.wait.assert_called_once()

    def test_running_cancellation_kills_native_process(self) -> None:
        """Accept no inputs; create a cancellation during polling and verify cleanup."""
        marker = self.runtime / "cancel"
        process = MagicMock(pid=123)

        def poll() -> None:
            """Accept no inputs; create cancellation marker and return running status."""
            marker.touch()
            return None

        process.poll.side_effect = poll
        archive = self.runtime / "attempt"
        archive.mkdir()
        with patch("agentic_srim.execution.processes.subprocess.Popen", return_value=process):
            with self.assertRaises(CancelledError):
                run(self.runtime, archive, 10, marker)
        process.kill.assert_called_once()
        process.wait.assert_called_once()

    def test_process_liveness_is_read_only(self) -> None:
        """Accept no inputs; check own and exited Python process without signals."""
        self.assertTrue(is_process_alive(os.getpid()))
        process = subprocess.Popen([sys.executable, "-c", "pass"])
        process.wait(timeout=10)
        self.assertFalse(is_process_alive(process.pid))
