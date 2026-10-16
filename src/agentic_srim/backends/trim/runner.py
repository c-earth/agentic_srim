"""Accept TRIM execution paths and bounds; expose isolated native run operations."""

from pathlib import Path
import os
import shutil
import json

from agentic_srim.execution.processes import run as run_native
from agentic_srim.storage.artifacts import checksum, write_json


def prepare(source: Path, workspace: Path) -> dict:
    """Copy TRIM.exe and Data once from source; return support-file fingerprints.

    workspace is the locked worker's TRIM subdirectory. Output and restore folders
    are private to that workspace. Changed source or copied data is rejected.
    """
    workspace.mkdir(parents=True, exist_ok=True)
    files = [source / "TRIM.exe", *sorted((source / "Data").glob("*"))]
    files = [p for p in files if p.is_file()]
    required = {"TRIM.exe", "Data/SCOEF03.DAT", "Data/SNUC03.dat", "Data/POTCOEF03.DAT", "Data/TRIM.cfg", "Data/VERSION"}
    fingerprint = {p.relative_to(source).as_posix(): checksum(p) for p in files}
    if not required.issubset(fingerprint):
        raise ValueError("TRIM installation is missing required executable/reference files")
    marker = workspace / "installation.json"
    if marker.exists():
        import json
        if json.loads(marker.read_text(encoding="utf-8")) != fingerprint:
            raise ValueError("TRIM installation changed; select a fresh runtime directory")
    else:
        for name in fingerprint:
            destination = workspace / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / name, destination)
        write_json(marker, fingerprint)
    if any(checksum(workspace / name) != value for name, value in fingerprint.items()):
        raise ValueError("TRIM worker executable/reference data checksum mismatch")
    for name in ("SRIM Outputs", "SRIM Restore"):
        (workspace / name).mkdir(exist_ok=True)
    return fingerprint


def clear_outputs(workspace: Path) -> None:
    """Remove prior generated TRIM files inside input workspace; return nothing.

    Never touches Data or executable support files. Restore snapshots are cleared
    because each application attempt is a fresh calculation, not a native resume.
    """
    for name in ("SRIM Outputs", "SRIM Restore"):
        directory = workspace / name
        for path in directory.iterdir():
            if path.is_symlink() or not path.is_file():
                raise ValueError("Unexpected entry in private TRIM output directory")
            path.unlink()
    for path in workspace.glob("*.txt"):
        path.unlink()
    for name in ("TRIM.IN", "TRIM.DAT", "TRIMAUTO"):
        (workspace / name).unlink(missing_ok=True)


def collect(workspace: Path, archive: Path) -> None:
    """Copy current native outputs and restore files into archive; return nothing.

    Inputs identify the worker and its owning attempt. Preserves all generated
    files with native subdirectories, including snapshots on failed attempts.
    """
    paths = [*workspace.glob("*.txt")]
    for name in ("SRIM Outputs", "SRIM Restore"):
        paths.extend((workspace / name).glob("*"))
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise ValueError("Unexpected generated TRIM entry")
        destination = archive / "native" / path.relative_to(workspace)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if checksum(destination) != checksum(path):
                raise ValueError("Refusing to replace a collected TRIM artifact")
        else:
            shutil.copy2(path, destination)


def completion_matches(title: str, message: str, owner_pid: int, native_pid: int, count: int) -> bool:
    """Return whether dialog text/PID match the approved native completion and count."""
    return (owner_pid == native_pid and title == "End of TRIM.DAT calculation"
            and message.rstrip("\r\n") == f"Calculation Complete ({count} Ions)")


CASCADE_TITLE = "TRIM: Special for Neutron/Electron Damage"
CASCADE_QUESTION = "Would you like a file of each Cascade ? [Y]\r\n\r\nThis activates the file SRIM Outputs\\COLLISON.txt\r\n and stores calculation results for EVERY cascade."
FILE_ACTIVE = "File SRIMOutput\\COLLISON.txt now active."
EVERY_QUESTION = "Would you like every collision filed? [Yes]\r\n\r\n \u00ba  This stores EVERY COLLISION in COLLISON.txt as\r\n \u00ba  well as the statistics of each cascade."
EVERY_ACTIVE = "Every Collision will be filed."


def dialog_response(title: str, message: str, owner_pid: int, native_pid: int, count: int,
                    collisions: int | None, cascade_selected: bool, file_active: bool,
                    calculation_mode: int | None, collision_records: str | None,
                    every_selected: bool) -> tuple[int, str] | None:
    """Return an approved control ID/label for exact PID/text and request/state inputs.

    No Windows interaction occurs here. Mode-8 summary/every responses require
    the acknowledged cascade-file activation sequence. Its extra notification
    is permitted only after an explicit every selection. Unknown states return
    None, including KP warning dialogs and missing or incompatible choices.
    """
    if owner_pid != native_pid:
        return None
    if completion_matches(title, message, owner_pid, native_pid, count):
        return 2, "OK"
    if title != CASCADE_TITLE:
        return None
    if collisions is not None and message == CASCADE_QUESTION:
        return (6, "Yes") if collisions else (7, "No")
    if cascade_selected and message == FILE_ACTIVE:
        return 2, "OK"
    if calculation_mode == 8 and collisions == 1 and cascade_selected and file_active:
        if message == EVERY_QUESTION and collision_records in ("summary", "every"):
            return (6, "Yes") if collision_records == "every" else (7, "No")
        if message == EVERY_ACTIVE and collision_records == "every" and every_selected:
            return 2, "OK"
    return None


def acknowledge_completion(pid: int, count: int, observations: list | None = None,
                           collisions: int | None = None, cascade_selected: bool = False,
                           file_active: bool = False, calculation_mode: int | None = None,
                           collision_records: str | None = None, every_selected: bool = False) -> dict | None:
    """Answer an exact approved Windows dialog for PID/count/output choice; return action.

    Uses native Win32 messages without an assistant/computer-use session. Requires
    the exact dialog class, title, static message, process owner, OK control ID and
    button text. The separately approved recoil cascade-file prompt follows the
    collisions selection. Unknown/error dialogs are untouched and timeout-bounded.
    """
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes
    user = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = (callback_type, wintypes.LPARAM)
    user.EnumWindows.restype = wintypes.BOOL
    user.EnumChildWindows.argtypes = (wintypes.HWND, callback_type, wintypes.LPARAM)
    user.EnumChildWindows.restype = wintypes.BOOL
    user.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user.GetWindowThreadProcessId.restype = wintypes.DWORD
    user.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user.GetWindowTextW.restype = ctypes.c_int
    user.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user.GetClassNameW.restype = ctypes.c_int
    user.GetDlgItem.argtypes = (wintypes.HWND, ctypes.c_int)
    user.GetDlgItem.restype = wintypes.HWND
    user.SendMessageTimeoutW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                                        wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t))
    user.SendMessageTimeoutW.restype = wintypes.LPARAM
    windows = []

    @callback_type
    def enumerate_window(handle, context):
        """Receive native handle/context; append owned windows and return continue flag."""
        owner = wintypes.DWORD()
        user.GetWindowThreadProcessId(handle, ctypes.byref(owner))
        if owner.value == pid:
            windows.append(handle)
        return True

    def text(handle, class_name: bool = False) -> str:
        """Return bounded window text/class for handle; empty results cannot match."""
        buffer = ctypes.create_unicode_buffer(512)
        method = user.GetClassNameW if class_name else user.GetWindowTextW
        method(handle, buffer, len(buffer))
        return buffer.value

    user.EnumWindows(enumerate_window, 0)
    for handle in windows:
        title = text(handle)
        if text(handle, True) != "#32770":
            continue
        messages = []

        @callback_type
        def enumerate_child(child, context):
            """Receive child/context; collect static dialog text and return continue flag."""
            if text(child, True) == "Static":
                messages.append(text(child))
            return True

        user.EnumChildWindows(handle, enumerate_child, 0)
        if observations is not None:
            observations.append({"title": title, "messages": messages})
        owner = wintypes.DWORD()
        user.GetWindowThreadProcessId(handle, ctypes.byref(owner))
        if len(messages) != 1:
            continue
        response = dialog_response(title, messages[0], owner.value, pid, count, collisions,
                                   cascade_selected, file_active, calculation_mode,
                                   collision_records, every_selected)
        if response is None:
            continue
        control_id, expected = response
        button = user.GetDlgItem(handle, control_id)
        button_owner = wintypes.DWORD()
        user.GetWindowThreadProcessId(button, ctypes.byref(button_owner))
        if not button or button_owner.value != pid or text(button, True) != "Button" or text(button) not in (expected, "&" + expected):
            continue
        result = ctypes.c_size_t()
        if not user.SendMessageTimeoutW(button, 0x00F5, 0, 0, 2, 100, ctypes.byref(result)):
            raise RuntimeError("TRIM completion acknowledgment failed")
        return {"pid": pid, "title": title, "message": messages[0], "button": expected, "ion_count": count}
    return None


def run(workspace: Path, archive: Path, timeout_seconds: float, cancellation: Path,
        ion_count: int | None = None, collisions: int | None = None,
        calculation_mode: int | None = None, collision_records: str | None = None) -> int:
    """Execute copied TRIM with bounds/count; return exit code and log acknowledged completion.

    ion_count enables approved completion handling; collisions additionally enables
    the approved cascade-file choice for recoil modes. Mode 8 and an explicit
    collision_records value enable its summary/every question and, after Yes,
    its exact notification. None leaves that additional sequence disabled.
    """
    handled = set()
    observed = set()
    cascade_selected = False
    file_active = False
    every_selected = False

    def completion(pid: int) -> None:
        """Check native PID for the one approved completion; log action and return nothing."""
        nonlocal cascade_selected, file_active, every_selected
        observations = []
        action = acknowledge_completion(pid, ion_count, observations, collisions, cascade_selected,
                                        file_active, calculation_mode, collision_records,
                                        every_selected) if ion_count is not None else None
        if action:
            key = (action["title"], action["message"])
            if key in handled:
                raise RuntimeError("Repeated native dialog after acknowledgment")
            handled.add(key)
            if action["message"] == CASCADE_QUESTION and action["button"] == "Yes":
                cascade_selected = True
            if action["message"] == FILE_ACTIVE and action["button"] == "OK":
                file_active = True
            if action["message"] == EVERY_QUESTION and action["button"] == "Yes":
                every_selected = True
            with (archive / "native_interactions.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(action) + "\n")
        for observation in observations:
            key = json.dumps(observation, sort_keys=True)
            if key not in observed:
                observed.add(key)
                with (archive / "native_dialogs.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(key + "\n")

    return run_native(workspace, archive, timeout_seconds, cancellation, executable="TRIM.exe", poll_hook=completion)
