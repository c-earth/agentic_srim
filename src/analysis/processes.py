"""Accept child commands and bounded logs; supervise owned process trees.

Windows jobs use kill-on-close and POSIX jobs use a new process group plus a
parent-lifetime pipe. The child waits for durable ownership acknowledgement
before importing any method. No access isolation or CPU/RAM quota is provided.
"""

from contextlib import contextmanager
import os
from pathlib import Path
import signal
import subprocess
import threading
import time

from analysis.records import AnalysisError


@contextmanager
def locked(runtime: Path):
    """Accept runtime; yield exclusive postprocessing ownership or raise busy; unlock on exit."""
    runtime.mkdir(parents=True, exist_ok=True)
    path = runtime / "postprocessing.lock"
    with path.open("a+b") as stream:
        stream.seek(0)
        if not stream.read(1):
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise AnalysisError("worker_busy", "Another postprocessing dispatcher owns this runtime") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class WindowsJob:
    """Construct for a new subprocess; own its descendants until confirmed termination."""

    def __init__(self, process: subprocess.Popen) -> None:
        """Accept a gated child process; assign a kill-on-close job; return nothing."""
        import ctypes
        from ctypes import wintypes
        self.ctypes = ctypes
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        self.kernel.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        self.kernel.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
        self.kernel.QueryInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p)
        self.kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        class Basic(ctypes.Structure):
            """Construct without inputs; expose Windows JOBOBJECT_BASIC_LIMIT_INFORMATION fields."""
            _fields_ = [("ProcessTime", ctypes.c_int64), ("JobTime", ctypes.c_int64), ("Flags", wintypes.DWORD),
                        ("Minimum", ctypes.c_size_t), ("Maximum", ctypes.c_size_t), ("Active", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("Priority", wintypes.DWORD), ("Scheduling", wintypes.DWORD)]
        class Extended(ctypes.Structure):
            """Construct without inputs; expose Windows extended limits and IO accounting fields."""
            _fields_ = [("Basic", Basic), ("IO", ctypes.c_uint64 * 6), ("ProcessMemory", ctypes.c_size_t),
                        ("JobMemory", ctypes.c_size_t), ("PeakProcess", ctypes.c_size_t), ("PeakJob", ctypes.c_size_t)]
        info = Extended()
        info.Basic.Flags = 0x2000
        try:
            if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            if not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            self.kernel.CloseHandle(self.handle)
            raise

    def close(self) -> None:
        """Accept no inputs; terminate and confirm every owned process exited; return nothing."""
        ctypes, kernel = self.ctypes, self.kernel
        class Accounting(ctypes.Structure):
            """Construct without inputs; expose basic Windows process accounting fields."""
            _fields_ = [("User", ctypes.c_int64), ("Kernel", ctypes.c_int64), ("PeriodUser", ctypes.c_int64),
                        ("PeriodKernel", ctypes.c_int64), ("Faults", ctypes.c_uint32), ("Total", ctypes.c_uint32),
                        ("Active", ctypes.c_uint32), ("Terminated", ctypes.c_uint32)]
        try:
            if not kernel.TerminateJobObject(self.handle, 1):
                raise ctypes.WinError(ctypes.get_last_error())
            deadline = time.monotonic() + 5
            while True:
                info = Accounting()
                if not kernel.QueryInformationJobObject(self.handle, 1, ctypes.byref(info), ctypes.sizeof(info), None):
                    raise ctypes.WinError(ctypes.get_last_error())
                if info.Active == 0:
                    return
                if time.monotonic() > deadline:
                    raise AnalysisError("recovery_required", "Owned process tree termination is unconfirmed")
                time.sleep(0.01)
        finally:
            kernel.CloseHandle(self.handle)


def run(command: list[str], directory: Path, token: str, deadline: float, cancelled, record_owner, log_bytes: int) -> int:
    """Accept gated command/context/deadline/callbacks/log ceiling; return exit code or raise.

record_owner receives PID/group/supervisor identity before the startup gate opens.
Both pipes continue draining after the shared retention cap; truncation is marked.
"""
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    if cancelled():
        raise AnalysisError("cancelled", "Cancellation requested during preparation")
    if time.monotonic() >= deadline:
        raise AnalysisError("timed_out", "Preparation exhausted the accepted wall-time budget")
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "MPLBACKEND": "Agg", "MPLCONFIGDIR": str(directory / "scratch" / "matplotlib")}
    process = subprocess.Popen(command, cwd=directory, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env=environment, creationflags=flags, start_new_session=os.name != "nt")
    job, readers = None, []
    remaining = [max(0, log_bytes - 96)]
    guard = threading.Lock()
    def drain(source, destination: Path) -> None:
        """Accept pipe/log path; retain bounded bytes while draining to EOF; return nothing."""
        truncated = False
        with destination.open("xb") as stream:
            while chunk := source.read(8192):
                with guard:
                    count = min(len(chunk), remaining[0])
                    remaining[0] -= count
                stream.write(chunk[:count])
                truncated |= count < len(chunk)
            if truncated and log_bytes >= 96:
                stream.write(b"\n[log truncated; pipe fully drained]\n")
        source.close()
    try:
        if os.name == "nt":
            job = WindowsJob(process)
        for pipe, name in ((process.stdout, "stdout.log"), (process.stderr, "stderr.log")):
            reader = threading.Thread(target=drain, args=(pipe, directory / name), daemon=True)
            reader.start()
            readers.append(reader)
        record_owner({"pid": process.pid, "group": process.pid, "supervisor_pid": os.getpid(), "token": token, "platform": os.name})
        process.stdin.write((token + "\n").encode())
        process.stdin.flush()
        while process.poll() is None:
            if cancelled():
                raise AnalysisError("cancelled", "Cancellation requested")
            if time.monotonic() >= deadline:
                raise AnalysisError("timed_out", "Per-attempt/cumulative wall-time limit exceeded")
            time.sleep(0.02)
        return process.returncode
    finally:
        try:
            if job:
                job.close()
            elif os.name != "nt":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            elif process.poll() is None:
                process.kill()
            process.wait(timeout=5)
        finally:
            process.stdin.close()
            for reader in readers:
                reader.join(timeout=5)
            if any(reader.is_alive() for reader in readers):
                raise AnalysisError("recovery_required", "Owned log pipes remain active")
            for stream in (process.stdout, process.stderr):
                if not stream.closed:
                    stream.close()
