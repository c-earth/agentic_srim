"""Own Windows TRIM jobs and apply optional sealed, verified dialog responses."""

from __future__ import annotations

from collections.abc import Callable
import ctypes
from copy import deepcopy
from ctypes import wintypes
import math
import os
from pathlib import Path
import struct
import subprocess
from time import monotonic, sleep

from src.srim.metadata import JSONObject
from src.srim.runtime import _read_regular_bytes
from src.srim.trim_dialogs import advance_trim_dialog_state, classify_trim_dialog, initial_trim_dialog_state


def _read_trim_window_text(window_handle: int) -> JSONObject:
    """Read observed window_handle title and children with bounded native calls.

    Return title, controls and capture errors; never focus, click or dismiss.
    Raise ValueError for invalid handles and OSError on unsupported platforms.
    """
    if type(window_handle) is not int or window_handle <= 0:
        raise ValueError("Expected a positive native window handle")
    if os.name != "nt":
        raise OSError("TRIM native inspection requires Windows")
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM, wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
    user.SendMessageTimeoutW.restype = wintypes.LPARAM
    user.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetWindow.restype = wintypes.HWND
    user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.IsWindow.argtypes = [wintypes.HWND]
    user.GetDlgCtrlID.argtypes = [wintypes.HWND]
    user.GetParent.argtypes = [wintypes.HWND]
    user.GetParent.restype = wintypes.HWND
    user.IsWindowEnabled.argtypes = [wintypes.HWND]
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    result: JSONObject = {"title": "", "controls": [], "capture_errors": []}
    pending = [window_handle]
    visited: set[int] = set()
    while pending:
        handle = pending.pop()
        if handle in visited:
            continue
        visited.add(handle)
        if not user.IsWindow(handle):
            result["capture_errors"].append(f"Window {handle} vanished")
            continue
        count = ctypes.c_size_t()
        if not user.SendMessageTimeoutW(handle, 0x000E, 0, 0, 0x0002, 100, ctypes.byref(count)):
            result["capture_errors"].append(f"Text length unavailable for {handle}")
        else:
            buffer = ctypes.create_unicode_buffer(count.value + 1)
            returned = ctypes.c_size_t()
            if not user.SendMessageTimeoutW(handle, 0x000D, len(buffer), ctypes.addressof(buffer), 0x0002, 100, ctypes.byref(returned)):
                result["capture_errors"].append(f"Text unavailable for {handle}")
            elif handle == window_handle:
                result["title"] = buffer.value
            else:
                class_buffer = ctypes.create_unicode_buffer(256)
                user.GetClassNameW(handle, class_buffer, len(class_buffer))
                pid = wintypes.DWORD()
                user.GetWindowThreadProcessId(handle, ctypes.byref(pid))
                parent = user.GetParent(handle)
                result["controls"].append({"handle": handle, "class": class_buffer.value, "text": buffer.value, "control_id": user.GetDlgCtrlID(handle), "enabled": bool(user.IsWindowEnabled(handle)), "visible": bool(user.IsWindowVisible(handle)), "parent_handle": int(parent) if parent else None, "pid": pid.value})
        child = user.GetWindow(handle, 5)
        siblings: set[int] = set()
        while child and int(child) not in siblings:
            siblings.add(int(child))
            pending.append(int(child))
            child = user.GetWindow(child, 2)
    return result


def _read_trim_windows(process_id: int) -> list[JSONObject]:
    """Inspect positive owned process_id's top-level windows without controlling them.

    Return class/owner/text/rectangle/visibility and conservative popup reasons.
    Native dialogs, owned windows and disabled-main evidence are reported.
    Raise ValueError for invalid PID and OSError for native inspection failures.
    """
    if type(process_id) is not int or process_id <= 0:
        raise ValueError("Expected a positive process ID")
    if os.name != "nt":
        raise OSError("TRIM native inspection requires Windows")
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetTopWindow.argtypes = [wintypes.HWND]
    user.GetTopWindow.restype = wintypes.HWND
    user.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetWindow.restype = wintypes.HWND
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.IsWindowEnabled.argtypes = [wintypes.HWND]
    windows: list[JSONObject] = []
    handle = user.GetTopWindow(None)
    seen: set[int] = set()
    while handle and int(handle) not in seen:
        seen.add(int(handle))
        owner_pid = wintypes.DWORD()
        if not user.GetWindowThreadProcessId(handle, ctypes.byref(owner_pid)):
            raise ctypes.WinError(ctypes.get_last_error())
        if owner_pid.value == process_id:
            class_buffer = ctypes.create_unicode_buffer(256)
            if not user.GetClassNameW(handle, class_buffer, len(class_buffer)):
                raise ctypes.WinError(ctypes.get_last_error())
            rect = wintypes.RECT()
            if not user.GetWindowRect(handle, ctypes.byref(rect)):
                raise ctypes.WinError(ctypes.get_last_error())
            owner = user.GetWindow(handle, 4)
            enabled = bool(user.IsWindowEnabled(handle))
            visible = bool(user.IsWindowVisible(handle))
            main_window = class_buffer.value == "ThunderRT5MDIForm"
            reason = "dialog" if class_buffer.value == "#32770" else "disabled-main" if main_window and not enabled else "owned-popup" if owner and visible and not main_window else "disabled-main" if not enabled and visible else None
            text = _read_trim_window_text(int(handle)) if reason else {"title": "", "controls": [], "capture_errors": []}
            if class_buffer.value == "ThunderRT5Form" and text["title"] == "Starting TRIM..." and not any(control["text"] or control["class"] not in ("ThunderRT5PictureBox", "Static") for control in text["controls"]) and not text["capture_errors"]:
                reason = None
            windows.append({"handle": int(handle), "owner_handle": int(owner) if owner else None, "pid": process_id, "class": class_buffer.value, "title": text["title"], "visible": visible, "enabled": enabled, "rectangle": [rect.left, rect.top, rect.right, rect.bottom], "controls": text["controls"], "detection_reason": reason, "capture_errors": text["capture_errors"]})
        handle = user.GetWindow(handle, 2)
    return windows


def _post_trim_command(process_id: int, window: JSONObject, command: JSONObject) -> JSONObject:
    """Revalidate process_id/window/command and post one asynchronous notification.

    Return queue acceptance evidence. Never focus or retry. Raise ValueError
    on stale text, controls, parentage or ownership, OSError on native failure.
    process_id must already have been verified as a member of the owned job.
    """
    if os.name != "nt":
        raise OSError("TRIM responses require Windows")
    if command.get("action") != "button":
        raise ValueError("Unrecognized TRIM command")
    current = _read_trim_window_text(window["handle"])
    if window.get("pid") != process_id or current["capture_errors"] or current["title"] != window["title"] or current["controls"] != window["controls"]:
        raise ValueError("TRIM dialog changed before response")
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.IsWindow.argtypes = [wintypes.HWND]
    user.IsWindowEnabled.argtypes = [wintypes.HWND]
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.IsChild.argtypes = [wintypes.HWND, wintypes.HWND]
    user.GetDlgCtrlID.argtypes = [wintypes.HWND]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user.PostMessageW.restype = wintypes.BOOL
    target = command["target_handle"]
    for handle in (window["handle"], target):
        pid = wintypes.DWORD()
        if not user.IsWindow(handle) or not user.IsWindowEnabled(handle) or not user.IsWindowVisible(handle) or not user.GetWindowThreadProcessId(handle, ctypes.byref(pid)) or pid.value != process_id:
            raise ValueError("TRIM response target ownership/state changed")
    if not user.IsChild(window["handle"], target) or user.GetDlgCtrlID(target) != command["control_id"]:
        raise ValueError("TRIM response target parent/control changed")
    if not user.PostMessageW(window["handle"], 0x0111, command["control_id"], target):
        raise ctypes.WinError(ctypes.get_last_error())
    return {"queued": True, "error": None}


def _filter_trim_startup_windows(windows: list[JSONObject], state: dict[tuple[int, int], float], elapsed_seconds: float) -> list[JSONObject]:
    """Copy windows and briefly defer only the observed unreadable startup form.

    windows contains job-vetted observations; state stores first-seen elapsed
    times by PID/handle for this attempt. elapsed_seconds is finite nonnegative
    native time. Return copied observations, updating only state and sending no
    messages. Defer for less than 0.5 seconds, only before native second five.
    Raise ValueError for invalid elapsed time; malformed observations propagate
    errors to the caller's monitoring-failure handling. Never reset deadlines.
    """
    if type(elapsed_seconds) not in (int, float) or not math.isfinite(elapsed_seconds) or elapsed_seconds < 0:
        raise ValueError("Invalid startup observation time")
    result = deepcopy(windows)
    for window in result:
        if window.get("detection_reason") == "owned-popup" and window.get("class") == "ThunderRT5Form" and window.get("owner_handle") and window.get("visible") and window.get("enabled") and window.get("rectangle") == [645, 406, 891, 457] and window.get("title") == "" and window.get("capture_errors") and not any(control["text"] or control["class"] not in ("ThunderRT5PictureBox", "Static") for control in window.get("controls", [])):
            identity = (window["pid"], window["handle"])
            first_seen = state.setdefault(identity, elapsed_seconds)
            if elapsed_seconds < 5.0 and elapsed_seconds - first_seen < 0.5:
                window["detection_reason"] = None
    return result


def _observe_trim_dialogs(process_id: int, runtime: Path, policy: JSONObject, state: JSONObject, elapsed_seconds: float, *, startup_state: dict[tuple[int, int], float] | None = None) -> JSONObject:
    """Observe owned process_id and advance supplied response state under policy.

    runtime is validated for caller compatibility; no export files are read.
    elapsed_seconds bounds pending responses.
    Optional startup_state stores per-attempt startup candidate deadlines;
    None retains direct-call behavior without startup deferral.
    Return state, events, unhandled windows and errors. Send at most one command,
    never retry. Invalid arguments raise ValueError; native failures are returned.
    """
    initial_trim_dialog_state(policy)
    if not runtime.is_absolute() or not math.isfinite(elapsed_seconds) or elapsed_seconds < 0:
        raise ValueError("Invalid TRIM observation controls")
    windows = _read_trim_windows(process_id)
    if startup_state is not None:
        windows = _filter_trim_startup_windows(windows, startup_state, elapsed_seconds)
    result: JSONObject = {"state": deepcopy(state), "events": [], "unhandled": [], "errors": []}
    popups = [w for w in windows if w["detection_reason"]]
    dialogs = [w for w in popups if w["class"] == "#32770" and w["visible"] and w["enabled"]]
    candidates = [w for w in popups if w["detection_reason"] == "owned-popup" and w["visible"] and w["enabled"]]
    pending = state.get("pending_sequence")
    if pending is not None:
        previous = state["events"][pending - 1]
        same = any(w["handle"] == previous["window"]["handle"] and w["title"] == previous["window"]["title"] and [(c["class"], c["text"]) for c in w["controls"]] == [(c["class"], c["text"]) for c in previous["window"]["controls"]] for w in popups)
        if same:
            if elapsed_seconds - previous["elapsed_seconds"] >= policy["response_transition_seconds"]:
                result["errors"].append("Posted TRIM command did not advance; no retry")
                result["state"]["events"][pending - 1]["transition"] = "failed"
                result["state"]["events"][pending - 1]["error"] = result["errors"][-1]
                result["state"]["stage"] = "failed"
            return result
        result["state"] = advance_trim_dialog_state(result["state"], {"transition": "verified"}, None)
        result["state"]["events"][pending - 1]["transition"] = "verified"
    if len(dialogs) + len(candidates) > 1:
        result["unhandled"] = popups
        return result
    if not dialogs and not candidates:
        if popups:
            since = result["state"].setdefault("transient_since", elapsed_seconds)
            if elapsed_seconds - since >= policy["response_transition_seconds"]:
                result["unhandled"] = popups
        else:
            result["state"].pop("transient_since", None)
        return result
    result["state"].pop("transient_since", None)
    window = (dialogs or candidates)[0]
    classified = classify_trim_dialog(window, policy, result["state"])
    if classified["decision"] == "reject":
        result["unhandled"] = popups
        return result
    if classified["decision"] == "respond":
        command = classified["command"]
        event: JSONObject = {"sequence": len(result["state"]["events"]) + 1, "elapsed_seconds": elapsed_seconds, "process_id": process_id, "window": deepcopy(window), "stage_before": result["state"]["stage"], "stage_after": classified["next_stage"], **command, "message": "WM_COMMAND/BN_CLICKED", "queued": False, "transition": "pending", "error": None}
        try:
            event.update(_post_trim_command(process_id, window, command))
            result["state"] = advance_trim_dialog_state(result["state"], classified, event)
        except (ValueError, OSError) as error:
            event.update({"error": str(error), "transition": "failed"})
            result["errors"].append(str(error))
        result["state"]["events"].append(event)
        result["events"].append(event)
    return result


def execute_trim_process(executable: Path, runtime: Path, *, hide_window: bool, timeout_seconds: float | None, stdout_path: Path, stderr_path: Path, cancel: Callable[[], bool] | None = None, dialog_policy: JSONObject | None = None) -> JSONObject:
    """Run executable in runtime under a Windows kill-on-close job with bounded monitoring.

    hide_window controls initial display; timeout_seconds is positive and finite
    or None to disable the overall execution deadline.
    Write exact diagnostics exclusively to stdout_path/stderr_path; cancel may
    request termination. Return exit/popup/timeout/cancellation/monitor evidence.
    dialog_policy opts into sealed reviewed fresh responses; None only reports
    popups. Automatic continuation raises ValueError pending checkpoint .sav
    integration, before creating a job or launching a process.
    Never kill unrelated processes. Validation/I/O errors raise;
    BaseException closes the owned job and waits before propagating.
    """
    if os.name != "nt":
        raise OSError("TRIM execution requires Windows")
    if type(hide_window) is not bool or (timeout_seconds is not None and (type(timeout_seconds) not in (int, float) or timeout_seconds <= 0 or not math.isfinite(timeout_seconds))) or (cancel is not None and not callable(cancel)):
        raise ValueError("Invalid TRIM process controls")
    for path in (executable, runtime, stdout_path, stderr_path):
        if not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts:
            raise ValueError("TRIM process paths must be absolute and canonical")
        for ancestor in (*reversed(path.parents), path):
            if ancestor.is_symlink() or ancestor.is_junction():
                raise OSError("Unsafe TRIM process path")
    _read_regular_bytes(executable)
    if executable.parent != runtime or not runtime.is_dir() or stdout_path == stderr_path:
        raise ValueError("Invalid TRIM runtime/diagnostic paths")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    native = ctypes.WinDLL("ntdll")
    native.NtResumeProcess.argtypes = [wintypes.HANDLE]
    native.NtResumeProcess.restype = wintypes.LONG
    started = monotonic()
    startup_state: dict[tuple[int, int], float] = {}
    result: JSONObject = {"pid": None, "exit_code": None, "elapsed_seconds": 0.0, "termination": "launch-error", "popups": [], "monitor_errors": []}
    dialog_state = initial_trim_dialog_state(dialog_policy) if dialog_policy is not None else None
    if dialog_policy is not None and dialog_policy["kind"] == "resume":
        raise ValueError("Automatic continuation awaits checkpoint .sav integration; GUI exports were removed")
    if dialog_state is not None:
        dialog_state["events"] = []
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        result["monitor_errors"].append(str(ctypes.WinError(ctypes.get_last_error())))
        result["elapsed_seconds"] = monotonic() - started
        return result
    process: subprocess.Popen[bytes] | None = None
    try:
        limits = ctypes.create_string_buffer(144 if ctypes.sizeof(ctypes.c_void_p) == 8 else 112)
        struct.pack_into("I", limits, 16, 0x2000)
        if not kernel.SetInformationJobObject(job, 9, limits, len(limits)):
            result["monitor_errors"].append(str(ctypes.WinError(ctypes.get_last_error())))
            return result
        with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
            startup = subprocess.STARTUPINFO()
            if hide_window:
                startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startup.wShowWindow = subprocess.SW_HIDE
            try:
                process = subprocess.Popen([str(executable)], cwd=runtime, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, startupinfo=startup, creationflags=0x00000004 | subprocess.CREATE_NO_WINDOW)
                result["pid"] = process.pid
                if not kernel.AssignProcessToJobObject(job, wintypes.HANDLE(int(process._handle))):
                    raise ctypes.WinError(ctypes.get_last_error())
                if native.NtResumeProcess(wintypes.HANDLE(int(process._handle))) != 0:
                    raise OSError("Cannot resume owned TRIM process")
            except OSError as error:
                result["monitor_errors"].append(str(error))
                return result
            while True:
                try:
                    if cancel is not None and cancel():
                        result["termination"] = "cancelled"
                        break
                    elapsed = monotonic() - started
                    if timeout_seconds is not None and elapsed >= timeout_seconds:
                        result["termination"] = "timeout"
                        break
                    process_list_size = 1024
                    while True:
                        process_list = ctypes.create_string_buffer(process_list_size)
                        if kernel.QueryInformationJobObject(job, 3, process_list, len(process_list), None):
                            break
                        if ctypes.get_last_error() != 234:
                            raise ctypes.WinError(ctypes.get_last_error())
                        process_list_size *= 2
                    active_count = struct.unpack_from("I", process_list, 4)[0]
                    pointer_size = ctypes.sizeof(ctypes.c_void_p)
                    pids = [int.from_bytes(process_list.raw[8 + i * pointer_size:8 + (i + 1) * pointer_size], "little") for i in range(active_count)]
                    for pid in pids:
                        owned = kernel.OpenProcess(0x1000, False, pid)
                        if not owned:
                            if ctypes.get_last_error() not in (87, 1168):
                                raise ctypes.WinError(ctypes.get_last_error())
                            continue
                        try:
                            member = wintypes.BOOL()
                            if not kernel.IsProcessInJob(owned, job, ctypes.byref(member)):
                                raise ctypes.WinError(ctypes.get_last_error())
                            if not member.value:
                                continue
                            if dialog_state is None:
                                windows = _filter_trim_startup_windows(_read_trim_windows(pid), startup_state, elapsed)
                                result["popups"].extend(window for window in windows if window["detection_reason"])
                            else:
                                observation = _observe_trim_dialogs(pid, runtime, dialog_policy, dialog_state, elapsed, startup_state=startup_state)
                                dialog_state = observation["state"]
                                result["popups"].extend(observation["unhandled"])
                                result["monitor_errors"].extend(observation["errors"])
                        finally:
                            kernel.CloseHandle(owned)
                    if result["monitor_errors"]:
                        result["termination"] = "monitor-error"
                        break
                    if result["popups"]:
                        if not any(window["detection_reason"] == "dialog" for window in result["popups"]):
                            sleep(0.1)
                            for pid in pids:
                                owned = kernel.OpenProcess(0x1000, False, pid)
                                if not owned:
                                    if ctypes.get_last_error() not in (87, 1168):
                                        raise ctypes.WinError(ctypes.get_last_error())
                                    continue
                                try:
                                    member = wintypes.BOOL()
                                    if not kernel.IsProcessInJob(owned, job, ctypes.byref(member)):
                                        raise ctypes.WinError(ctypes.get_last_error())
                                    if member.value:
                                        result["popups"].extend(window for window in _read_trim_windows(pid) if window["detection_reason"] == "dialog")
                                finally:
                                    kernel.CloseHandle(owned)
                        result["termination"] = "popup"
                        break
                    if process.poll() is not None and active_count == 0:
                        result["termination"] = "exit"
                        break
                except Exception as error:
                    result["monitor_errors"].append(str(error))
                    result["termination"] = "monitor-error"
                    break
                sleep(0.1)
    finally:
        if process is not None:
            if process.poll() is None:
                kernel.TerminateJobObject(job, 1)
                if process.poll() is None:
                    process.kill()
            kernel.CloseHandle(job)
            process.wait(timeout=10)
            result["exit_code"] = process.returncode
        else:
            kernel.CloseHandle(job)
        result["elapsed_seconds"] = monotonic() - started
        if dialog_state is not None:
            result["dialog_events"] = dialog_state.pop("events")
            dialog_state.pop("transient_since", None)
            if result["termination"] == "exit":
                if dialog_state["pending_sequence"] is not None:
                    result["dialog_events"][dialog_state["pending_sequence"] - 1]["transition"] = "verified"
                    dialog_state["pending_sequence"] = None
                dialog_state["stage"] = "done"
            result["dialog_state"] = dialog_state
    return result
