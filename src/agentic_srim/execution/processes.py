"""Execute a copied native simulator with bounded time and cooperative cancellation."""

import os
from pathlib import Path
import subprocess
import time
from typing import Callable

from agentic_srim.storage.artifacts import write_json

class CancelledError(RuntimeError):
    """Construct from an error message; represents a cancelled native calculation."""


def is_process_alive(pid: int) -> bool:
    """Return whether input pid still exists, without sending a Windows signal.

    Windows uses a query-only handle; inability to inspect a process raises an
    error so recovery refuses to reuse a potentially active workspace.
    """
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            error = ctypes.get_last_error()
            if error == 87:
                return False
            raise ctypes.WinError(error)
        try:
            code = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                raise ctypes.WinError(ctypes.get_last_error())
            return code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def run(workspace: Path, archive: Path, timeout_seconds: float, cancellation: Path, executable: str = "SRModule.exe",
        poll_hook: Callable[[int], None] | None = None) -> int:
    """Execute in workspace; write stdout/stderr to archive and return exit code.

    timeout_seconds bounds native runtime. A cancellation marker requests stop.
    Kills and reaps the process on any interrupted wait; raises on timeout/cancel.
    executable selects a copied executable basename. poll_hook receives the native
    PID while waiting and may perform backend-specific completion handling.
    Windows uses hidden startup.
    Abrupt worker death is not yet supervised with a Windows job object.
    """
    if Path(executable).name != executable:
        raise ValueError("Executable must be a basename")
    if cancellation.exists():
        raise CancelledError("Cancellation requested")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    startup = None
    if os.name == "nt":
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
    with (archive / "stdout.log").open("xb") as stdout, (archive / "stderr.log").open("xb") as stderr:
        process = subprocess.Popen([str(workspace / executable)], cwd=workspace,
                                   stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL,
                                   creationflags=flags, startupinfo=startup)
        try:
            write_json(archive / "native_process.json", {"pid": process.pid})
            deadline = time.monotonic() + timeout_seconds
            while process.poll() is None:
                if cancellation.exists():
                    raise CancelledError("Cancellation requested")
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"{executable} exceeded {timeout_seconds:g} seconds")
                if poll_hook is not None:
                    poll_hook(process.pid)
                time.sleep(min(0.05, max(0.001, deadline - time.monotonic())))
            return process.returncode
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
