"""Capture one SR compound correction from a fresh standalone Windows process.

Run from a directory containing SR.exe and its unchanged runtime data. This
module uses only Python's standard library and built-in Windows PowerShell/UI
Automation. It has no dependencies on the surrounding project's modules.
Windows' legacy automation providers are registered explicitly, retrying once
when their first initialization raises the observed NullReferenceException.
SelectionItemPattern alone does not fire VB5's NodeClick event, so selection is
followed by bounded mouse messages sent directly to the selected tree control.
The correction textbox is read, never written. Only the newly launched process
is targeted. Capture uses the requested timeout; cleanup has a separate bounded
five-second allowance for its automation worker and process exit, respectively.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import base64


def capture_correction(
    compound_name: str,
    output_path: Path,
    *,
    timeout_seconds: float = 30.0,
) -> str:
    """Launch SR in the current directory, select a compound and save its factor.

    Args:
        compound_name: Exact, case-sensitive compound label before the aligned
            density/composition columns in the dictionary's visible tree.
        output_path: New JSON file, absolute or relative to the current directory;
            its parent directory must already exist.
        timeout_seconds: Finite positive capture timeout, excluding booleans.

    Returns:
        Exact correction field text after successful recording and window cleanup.

    Side effects:
        Launches a fresh local SR.exe, invokes its dictionary, expands categories,
        selects the matching item and triggers its VB5 click handler. Exclusively
        writes UTF-8 JSON with compound, correction, process_id and recorded_at.
        Finally closes the dictionary and SR using their controls. On failure it
        attempts the same cleanup and terminates only its own process if needed.
        Existing output files, SRIM data and other processes are never modified.

    Raises:
        ValueError: A name, path argument or timeout has an invalid type/value.
        FileNotFoundError: SR.exe, Windows PowerShell or the output parent is absent.
        FileExistsError: The output file already exists or is concurrently created.
        RuntimeError: Windows automation, selection, correction parsing, timeout or
            cleanup fails. A recorded JSON file remains if later cleanup fails.
        OSError: Process launch or file publication fails. Interruption is re-raised
            after cleanup, with secondary cleanup errors attached as notes.
    """
    if not isinstance(compound_name, str) or not compound_name.strip() or compound_name != compound_name.strip():
        raise ValueError("compound_name must be a nonempty label without surrounding whitespace")
    if not isinstance(output_path, Path):
        raise ValueError("output_path must be a pathlib.Path")
    if type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be finite and positive")
    if os.name != "nt":
        raise RuntimeError("This standalone script requires Windows")
    directory = Path.cwd()
    executable = directory / "SR.exe"
    if not executable.is_file():
        raise FileNotFoundError(f"Run from the SRIM copy containing {executable}")
    destination = output_path.absolute()
    if os.path.lexists(destination):
        raise FileExistsError(f"Output already exists: {destination}")
    if not destination.parent.is_dir():
        raise FileNotFoundError(f"Output parent does not exist: {destination.parent}")
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    if not powershell.is_file():
        raise FileNotFoundError(f"Windows PowerShell is missing: {powershell}")
    worker = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
try {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    Add-Type -AssemblyName UIAutomationClientsideProviders
    $providerAssembly = [UIAutomationClientsideProviders.UIAutomationClientSideProviders].Assembly.GetName()
    try {
        [System.Windows.Automation.ClientSettings]::RegisterClientSideProviderAssembly($providerAssembly)
    } catch {
        $registrationError = $_.Exception
        while ($null -ne $registrationError.InnerException) { $registrationError = $registrationError.InnerException }
        if ($registrationError -isnot [System.NullReferenceException]) { throw }
        [System.Windows.Automation.ClientSettings]::RegisterClientSideProviderAssembly($providerAssembly)
    }
    $targetPid = [int]$env:SRIM_CAPTURE_PROCESS_ID
    $label = $env:SRIM_CAPTURE_COMPOUND
    $mode = $env:SRIM_CAPTURE_MODE
    $deadline = [DateTime]::UtcNow.AddSeconds([double]::Parse($env:SRIM_CAPTURE_TIMEOUT, [Globalization.CultureInfo]::InvariantCulture))
    $root = [System.Windows.Automation.AutomationElement]::RootElement
    $pidCondition = [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $targetPid)
    $windows = @($root.FindAll([System.Windows.Automation.TreeScope]::Children, $pidCondition))
    if ($mode -eq 'close') {
        $failures = @()
        foreach ($dialog in @($windows | Where-Object { $_.Current.Name -eq 'Common Compounds' })) {
            try {
                $button = $dialog.FindFirst([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::NameProperty, 'Close'))
                if ($null -eq $button) { throw 'Dictionary Close button missing' }
                ([System.Windows.Automation.InvokePattern]$button.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)).Invoke()
            } catch { $failures += 'Dictionary: ' + $_.Exception.Message }
        }
        $windows = @($root.FindAll([System.Windows.Automation.TreeScope]::Children, $pidCondition))
        foreach ($main in @($windows | Where-Object { $_.Current.Name -like 'Ion Stopping*' })) {
            try {
                $button = $main.FindFirst([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::NameProperty, 'Quit'))
                if ($null -eq $button) { throw 'SR Quit button missing' }
                ([System.Windows.Automation.InvokePattern]$button.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)).Invoke()
            } catch { $failures += 'SR: ' + $_.Exception.Message }
        }
        Start-Sleep -Milliseconds 100
        $remaining = @($root.FindAll([System.Windows.Automation.TreeScope]::Children, $pidCondition) | Where-Object { $_.Current.Name -eq 'Common Compounds' -or $_.Current.Name -like 'Ion Stopping*' })
        if ($remaining.Count -gt 0) { throw (($failures + 'SR or dictionary window remains open') -join '; ') }
        [ordered]@{ closed = $true } | ConvertTo-Json -Compress
        exit 0
    }
    do {
        $windows = @($root.FindAll([System.Windows.Automation.TreeScope]::Children, $pidCondition))
        $mains = @($windows | Where-Object { $_.Current.Name -like 'Ion Stopping*' })
        if ($mains.Count -gt 1) { throw 'Ambiguous SR main windows' }
        if ($mains.Count -eq 1) { break }
        if ([DateTime]::UtcNow -ge $deadline) { throw 'Timed out waiting for SR main window' }
        Start-Sleep -Milliseconds 100
    } while ($true)
    $main = $mains[0]
    if ($mode -eq 'prepare') {
        $button = $main.FindFirst([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::NameProperty, 'Compound Dictionary'))
        if ($null -eq $button) { throw 'Compound Dictionary button missing' }
        ([System.Windows.Automation.InvokePattern]$button.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)).Invoke()
    }
    do {
        $dialogs = @($root.FindAll([System.Windows.Automation.TreeScope]::Children, $pidCondition) | Where-Object { $_.Current.Name -eq 'Common Compounds' })
        if ($dialogs.Count -gt 1) { throw 'Ambiguous compound dialogs' }
        if ($dialogs.Count -eq 1) { break }
        if ([DateTime]::UtcNow -ge $deadline) { throw 'Timed out waiting for compound dictionary' }
        Start-Sleep -Milliseconds 100
    } while ($true)
    $dialog = $dialogs[0]
    $treeCondition = [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Tree)
    $trees = @($dialog.FindAll([System.Windows.Automation.TreeScope]::Descendants, $treeCondition) | Where-Object { -not $_.Current.IsOffscreen })
    if ($trees.Count -ne 1) { throw 'Expected exactly one visible compound tree' }
    $tree = $trees[0]
    $itemCondition = [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::TreeItem)
    if ($mode -eq 'prepare') {
        foreach ($category in $tree.FindAll([System.Windows.Automation.TreeScope]::Children, $itemCondition)) {
            $expand = [System.Windows.Automation.ExpandCollapsePattern]$category.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern)
            if ($expand.Current.ExpandCollapseState -eq [System.Windows.Automation.ExpandCollapseState]::Collapsed) { $expand.Expand() }
        }
    }
    $matches = @($tree.FindAll([System.Windows.Automation.TreeScope]::Descendants, $itemCondition) | Where-Object {
        $name = $_.Current.Name.Trim()
        $name -ceq $label -or ($name.StartsWith($label, [StringComparison]::Ordinal) -and $name.Length -gt $label.Length -and $name.Substring($label.Length) -match '^\s{2,}')
    })
    if ($matches.Count -ne 1) { throw ('Expected exactly one compound named: ' + $label + '; found ' + $matches.Count) }
    $item = $matches[0]
    $selection = [System.Windows.Automation.SelectionItemPattern]$item.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern)
    if ($mode -eq 'prepare') {
        $selection.Select()
        $itemBox = $item.Current.BoundingRectangle
        $treeBox = $tree.Current.BoundingRectangle
        if ($item.Current.IsOffscreen -or $itemBox.IsEmpty -or $treeBox.IsEmpty) { throw 'Selected compound has no visible control bounds' }
        [ordered]@{
            tree_handle = $tree.Current.NativeWindowHandle
            item_bounds = @($itemBox.X, $itemBox.Y, $itemBox.Width, $itemBox.Height)
            tree_bounds = @($treeBox.X, $treeBox.Y, $treeBox.Width, $treeBox.Height)
        } | ConvertTo-Json -Compress
    } elseif ($mode -eq 'read') {
        if (-not $selection.Current.IsSelected) { throw 'Compound selection was lost' }
        $add = $dialog.FindFirst([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::NameProperty, 'Add to Target'))
        if ($null -eq $add -or -not $add.Current.IsEnabled) { throw 'Compound click handler did not enable Add to Target' }
        $field = $main.FindFirst([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::AutomationIdProperty, '6'))
        if ($null -eq $field) { throw 'Correction field missing' }
        $value = ([System.Windows.Automation.ValuePattern]$field.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)).Current.Value
        [ordered]@{ compound = $label; correction = $value } | ConvertTo-Json -Compress
    } else { throw 'Unknown capture mode' }
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
"""
    encoded_worker = base64.b64encode(worker.encode("utf-16-le")).decode("ascii")
    command = [str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded_worker]
    environment = os.environ.copy()
    environment["SRIM_CAPTURE_COMPOUND"] = compound_name
    deadline = time.monotonic() + timeout_seconds
    process = subprocess.Popen([str(executable)], cwd=directory, creationflags=subprocess.CREATE_NO_WINDOW)
    environment["SRIM_CAPTURE_PROCESS_ID"] = str(process.pid)
    primary_error: BaseException | None = None
    correction: str | None = None
    try:
        for phase in ("prepare", "read"):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("Timed out capturing the compound correction")
            environment["SRIM_CAPTURE_MODE"] = phase
            environment["SRIM_CAPTURE_TIMEOUT"] = str(remaining)
            try:
                completed = subprocess.run(command, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=remaining, creationflags=subprocess.CREATE_NO_WINDOW)
            except subprocess.TimeoutExpired as error:
                raise RuntimeError("Timed out capturing the compound correction") from error
            if completed.returncode != 0:
                raise RuntimeError(completed.stderr.strip() or f"Automation worker failed during {phase}")
            try:
                result = json.loads(completed.stdout)
            except ValueError as error:
                raise RuntimeError("Automation worker returned invalid JSON") from error
            if phase == "prepare":
                tree_handle = result["tree_handle"]
                item_x, item_y, item_width, item_height = result["item_bounds"]
                tree_x, tree_y, tree_width, tree_height = result["tree_bounds"]
                user32 = ctypes.WinDLL("user32", use_last_error=True)
                user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
                user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
                user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
                user32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM, wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
                user32.SendMessageTimeoutW.restype = wintypes.LPARAM
                owner = wintypes.DWORD()
                class_name = ctypes.create_unicode_buffer(256)
                user32.GetWindowThreadProcessId(tree_handle, ctypes.byref(owner))
                user32.GetClassNameW(tree_handle, class_name, len(class_name))
                if owner.value != process.pid or class_name.value != "TreeViewWndClass":
                    raise RuntimeError("Compound tree does not belong to the launched SR process")
                bounds = wintypes.RECT()
                if not user32.GetClientRect(tree_handle, ctypes.byref(bounds)) or tree_width <= 0 or tree_height <= 0:
                    raise RuntimeError("Compound tree has invalid client bounds")
                client_width = bounds.right - bounds.left
                client_height = bounds.bottom - bounds.top
                point = wintypes.POINT(round(item_x + item_width / 2), round(item_y + item_height / 2))
                user32.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
                if not user32.ScreenToClient(tree_handle, ctypes.byref(point)):
                    raise RuntimeError("Could not convert the selected compound to tree client coordinates")
                x, y = point.x, point.y
                if not (0 <= x < client_width and 0 <= y < client_height and x <= 65535 and y <= 65535):
                    raise RuntimeError("Selected compound is outside its tree client bounds")
                coordinates = (y << 16) | x
                for message, buttons in ((0x200, 0), (0x201, 1), (0x202, 0)):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RuntimeError("Timed out triggering the compound click handler")
                    returned = ctypes.c_size_t()
                    if not user32.SendMessageTimeoutW(tree_handle, message, buttons, coordinates, 2, max(1, min(2000, int(remaining * 1000))), ctypes.byref(returned)):
                        raise RuntimeError(f"Tree control rejected message {message:#x}")
            else:
                if result.get("compound") != compound_name or not isinstance(result.get("correction"), str):
                    raise RuntimeError("Correction response does not match the requested compound")
                correction = result["correction"]
                try:
                    number = Decimal(correction)
                except InvalidOperation as error:
                    raise RuntimeError("Correction field is not numeric") from error
                if not number.is_finite() or number <= 0:
                    raise RuntimeError("Correction field must be finite and positive")
        document = {
            "compound": compound_name,
            "correction": correction,
            "process_id": process.pid,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        with destination.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException as error:
        primary_error = error
        raise
    finally:
        cleanup_failures: list[str] = []
        if process.poll() is None:
            environment["SRIM_CAPTURE_MODE"] = "close"
            environment["SRIM_CAPTURE_TIMEOUT"] = "5"
            try:
                closed = subprocess.run(command, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=5.0, creationflags=subprocess.CREATE_NO_WINDOW)
                if closed.returncode != 0:
                    cleanup_failures.append(closed.stderr.strip() or "Window cleanup worker failed")
                process.wait(timeout=5.0)
            except (OSError, subprocess.TimeoutExpired) as error:
                cleanup_failures.append(f"Window cleanup failed: {error}")
            if process.poll() is None:
                try:
                    process.terminate()
                    process.wait(timeout=5.0)
                except (OSError, subprocess.TimeoutExpired) as error:
                    cleanup_failures.append(f"Launched SR process remains: {process.pid}: {error}")
        if cleanup_failures:
            message = "; ".join(cleanup_failures)
            if primary_error is not None:
                primary_error.add_note(message)
            else:
                raise RuntimeError(f"Correction recorded at {destination}, but cleanup failed: {message}")
    if correction is None:
        raise RuntimeError("No correction was captured")
    return correction


def main(argv: Sequence[str] | None = None) -> int:
    """Parse argv and run one standalone correction capture.

    Args:
        argv: Explicit command-line tokens, or None to use sys.argv[1:].

    Returns:
        Zero after capture and cleanup, or one for an operational failure. Invalid
        command-line arguments raise argparse's SystemExit with status two.

    Side effects:
        Calls capture_correction, prints the captured field text to stdout, or
        prints a failure and secondary exception notes to stderr. Keyboard and
        system interruptions propagate after capture_correction performs cleanup.
    """
    parser = argparse.ArgumentParser(description="Capture one compound correction from a fresh SR.exe in the current directory.")
    parser.add_argument("--compound-name", required=True, help="Exact displayed compound label, for example Custom")
    parser.add_argument("--output", required=True, type=Path, help="New JSON output path with an existing parent directory")
    parser.add_argument("--timeout-seconds", type=float, default=30.0, help="Capture timeout in seconds (default: 30)")
    arguments = parser.parse_args(argv)
    if not arguments.compound_name.strip() or arguments.compound_name != arguments.compound_name.strip():
        parser.error("--compound-name must be nonempty and have no surrounding whitespace")
    if not math.isfinite(arguments.timeout_seconds) or arguments.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be finite and positive")
    try:
        correction = capture_correction(arguments.compound_name, arguments.output, timeout_seconds=arguments.timeout_seconds)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Capture failed: {error}", file=sys.stderr)
        for note in getattr(error, "__notes__", ()):
            print(note, file=sys.stderr)
        return 1
    print(correction)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
