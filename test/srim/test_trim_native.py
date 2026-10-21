"""Verify TRIM process-job ownership and popup/timeout handling without native launches."""

from __future__ import annotations

from pathlib import Path
import ctypes
import struct
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from test_trim_runtime import CACHE
from src.srim.trim_native import _read_trim_window_text, _read_trim_windows, execute_trim_process


class TestTRIMNative(unittest.TestCase):
    """Mock Windows job APIs and owned children in cache-only test directories."""

    def test_job_ownership_before_start(self: TestTRIMNative) -> None:
        """Require assignment before resume and clean exit job closure; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            root = Path(directory)
            (root / "TRIM.exe").write_bytes(b"fixture")
            api = MagicMock()
            api.NtResumeProcess.return_value = 0
            api.CreateJobObjectW.return_value = 12
            api.AssignProcessToJobObject.return_value = 1
            process = MagicMock(pid=43, returncode=0, _handle=44)
            process.poll.return_value = 0
            calls = MagicMock()
            calls.attach_mock(api.AssignProcessToJobObject, "assign")
            calls.attach_mock(api.NtResumeProcess, "resume")
            with patch("src.srim.trim_native.ctypes.WinDLL", return_value=api), patch("src.srim.trim_native.subprocess.Popen", return_value=process):
                report = execute_trim_process(root / "TRIM.exe", root, hide_window=True, timeout_seconds=5, stdout_path=root / "stdout", stderr_path=root / "stderr")
            self.assertEqual(report["termination"], "exit")
            self.assertEqual([call[0] for call in calls.mock_calls], ["assign", "resume"])
            api.CloseHandle.assert_called_once_with(12)

    def test_popup_reported_without_input(self: TestTRIMNative) -> None:
        """Capture popup evidence and terminate the owned job without messaging UI; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            root = Path(directory)
            (root / "TRIM.exe").write_bytes(b"fixture")
            api = MagicMock()
            api.NtResumeProcess.return_value = 0
            api.CreateJobObjectW.return_value = 12
            process_list = ctypes.create_string_buffer(1024)
            struct.pack_into("IIQ", process_list, 0, 1, 1, 43)
            buffers = [ctypes.create_string_buffer(144), process_list]
            process = MagicMock(pid=43, returncode=1, _handle=44)
            process.poll.side_effect = [None, 1]
            popup = {"detection_reason": "dialog", "title": "Native error", "controls": []}
            with patch("src.srim.trim_native.ctypes.WinDLL", return_value=api), patch("src.srim.trim_native.subprocess.Popen", return_value=process), patch("src.srim.trim_native._read_trim_windows", return_value=[popup]), patch("src.srim.trim_native.ctypes.create_string_buffer", side_effect=buffers), patch("src.srim.trim_native.wintypes.BOOL", return_value=ctypes.c_long(1)), patch("src.srim.trim_native.ctypes.POINTER", return_value=ctypes.c_void_p):
                report = execute_trim_process(root / "TRIM.exe", root, hide_window=True, timeout_seconds=5, stdout_path=root / "stdout", stderr_path=root / "stderr")
            self.assertEqual(report["termination"], "popup")
            self.assertEqual(report["popups"], [popup])
            api.TerminateJobObject.assert_called_once_with(12, 1)
            api.SendMessageW.assert_not_called()

    def test_timeout_owned_cleanup(self: TestTRIMNative) -> None:
        """Timeout closes only the assigned process job and waits for its child; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            root = Path(directory)
            (root / "TRIM.exe").write_bytes(b"fixture")
            api = MagicMock()
            api.NtResumeProcess.return_value = 0
            api.CreateJobObjectW.return_value = 12
            process = MagicMock(pid=43, returncode=1, _handle=44)
            process.poll.side_effect = [None, 1]
            with patch("src.srim.trim_native.ctypes.WinDLL", return_value=api), patch("src.srim.trim_native.subprocess.Popen", return_value=process), patch("src.srim.trim_native.monotonic", side_effect=[0, 10, 10]):
                report = execute_trim_process(root / "TRIM.exe", root, hide_window=True, timeout_seconds=1, stdout_path=root / "stdout", stderr_path=root / "stderr")
            self.assertEqual(report["termination"], "timeout")
            api.TerminateJobObject.assert_called_once_with(12, 1)
            process.wait.assert_called_once()

    def test_cancel_owned_cleanup(self: TestTRIMNative) -> None:
        """A requested cancellation terminates the owned job and returns its reason; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            root = Path(directory)
            (root / "TRIM.exe").write_bytes(b"fixture")
            api = MagicMock()
            api.NtResumeProcess.return_value = 0
            api.CreateJobObjectW.return_value = 12
            process = MagicMock(pid=43, returncode=1, _handle=44)
            process.poll.side_effect = [None, 1]
            with patch("src.srim.trim_native.ctypes.WinDLL", return_value=api), patch("src.srim.trim_native.subprocess.Popen", return_value=process):
                report = execute_trim_process(root / "TRIM.exe", root, hide_window=False, timeout_seconds=5, stdout_path=root / "stdout", stderr_path=root / "stderr", cancel=MagicMock(return_value=True))
            self.assertEqual(report["termination"], "cancelled")

    def test_monitor_failure(self: TestTRIMNative) -> None:
        """Treat native monitoring failure as a failed owned attempt; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            root = Path(directory)
            (root / "TRIM.exe").write_bytes(b"fixture")
            api = MagicMock()
            api.NtResumeProcess.return_value = 0
            api.CreateJobObjectW.return_value = 12
            api.QueryInformationJobObject.side_effect = OSError("monitor unavailable")
            process = MagicMock(pid=43, returncode=1, _handle=44)
            process.poll.side_effect = [None, 1]
            with patch("src.srim.trim_native.ctypes.WinDLL", return_value=api), patch("src.srim.trim_native.subprocess.Popen", return_value=process):
                report = execute_trim_process(root / "TRIM.exe", root, hide_window=True, timeout_seconds=5, stdout_path=root / "stdout", stderr_path=root / "stderr")
            self.assertEqual(report["termination"], "monitor-error")
            self.assertIn("monitor unavailable", report["monitor_errors"][0])
        text_api = MagicMock()
        text_api.IsWindow.return_value = 1
        text_api.GetWindow.return_value = 0
        text_api.SendMessageTimeoutW.return_value = 0
        with patch("src.srim.trim_native.ctypes.WinDLL", return_value=text_api):
            text_report = _read_trim_window_text(100)
        self.assertEqual(text_report["title"], "")
        self.assertTrue(text_report["capture_errors"])
        text_api.SendMessageW.assert_not_called()

    def test_normal_window_allowed(self: TestTRIMNative) -> None:
        """Treat the enabled TRIM MDI calculation window as normal despite its owner; return None."""
        api = MagicMock()
        api.GetTopWindow.return_value = 100
        api.GetWindow.side_effect = [99, 0]
        api.IsWindowEnabled.return_value = 1
        api.IsWindowVisible.return_value = 1
        class_buffer = ctypes.create_unicode_buffer("ThunderRT5MDIForm", 256)
        with patch("src.srim.trim_native.ctypes.WinDLL", return_value=api), patch("src.srim.trim_native.wintypes.DWORD", return_value=ctypes.c_ulong(43)), patch("src.srim.trim_native.ctypes.POINTER", return_value=ctypes.c_void_p), patch("src.srim.trim_native.ctypes.create_unicode_buffer", return_value=class_buffer), patch("src.srim.trim_native._read_trim_window_text") as text:
            windows = _read_trim_windows(43)
        self.assertEqual(len(windows), 1)
        self.assertIsNone(windows[0]["detection_reason"])
        text.assert_not_called()

    def test_unrelated_process_untouched(self: TestTRIMNative) -> None:
        """Skip windows owned by another PID without reading or controlling them; return None."""
        api = MagicMock()
        api.GetTopWindow.return_value = 100
        api.GetWindow.return_value = 0
        with patch("src.srim.trim_native.ctypes.WinDLL", return_value=api), patch("src.srim.trim_native.wintypes.DWORD", return_value=ctypes.c_ulong(44)), patch("src.srim.trim_native.ctypes.POINTER", return_value=ctypes.c_void_p), patch("src.srim.trim_native._read_trim_window_text") as text:
            windows = _read_trim_windows(43)
        self.assertEqual(windows, [])
        text.assert_not_called()
