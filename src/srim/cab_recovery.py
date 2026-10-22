"""Recover default CAB strengths with isolated owned GUI captures and immutable evidence."""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping, Sequence
import ctypes
from ctypes import wintypes
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal, DecimalException, InvalidOperation, localcontext
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
from tempfile import mkdtemp
import time

from src.backend.config import Config
from src.backend.paths import ProjectPaths
from src.backend.records import CreateRequest, CreatedDirectory, _lock_capture, _lock_worker, _validate_paths, create_directories
from src.backend.resources import ResourceReference, _hash_file, _read_json, _write_json, verify_resource
from src.srim.bragg import resolve_bragg
from src.srim.cab import BondKey, CABKind, ElementKey, _SYMBOLS, _atomic_number, _normalize_entries
from src.srim.cab_cache import CABEntryReference, CABEntryResult, _dependency_key, find_cab_entry, load_cab_entry, publish_cab_entry
from src.srim.cab_constants import CAB_PRECISION, CAB_ROUNDING, CAB_UNITS, CAPTURE_MANIFEST, MOCK_HEADER, SR_BONDS
from src.srim.defaults import read_sr_defaults, verify_sr_baseline
from src.srim.compatibility import read_module_version
from src.srim.inputs import render_sr_input
from src.srim.material import parse_atomdata
from src.srim.metadata import JSONObject, start_simulation
from src.srim.capture_records import initialize_capture_simulation, finish_capture_simulation
from src.srim.runtime import _read_regular_bytes, _validate_preparation_allocations


def _capture_correction(
    runtime: Path,
    compound_name: str,
    output_path: Path,
    *,
    timeout_seconds: float = 30.0,
) -> str:
    """Launch SR in runtime, select compound_name and save its exact factor to output_path.

    Args:
        runtime: Absolute owned isolated runtime directory; no global cwd change.
        compound_name: Exact, case-sensitive compound label before the aligned
            density/composition columns in the dictionary's visible tree.
        output_path: New JSON file, absolute or relative to runtime;
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
        raise ValueError("timeout_seconds must be finite and nonnegative")
    if os.name != "nt":
        raise RuntimeError("Integrated correction capture requires Windows")
    if not isinstance(runtime, Path) or not runtime.is_absolute():
        raise ValueError("runtime must be an absolute owned Path")
    directory = runtime
    for path in (*reversed(directory.parents), directory):
        if path.is_symlink() or path.is_junction():
            raise OSError("Capture runtime contains a link or junction")
    executable = directory / "SR.exe"
    if not executable.is_file():
        raise FileNotFoundError(f"Run from the SRIM copy containing {executable}")
    destination = output_path if output_path.is_absolute() else directory / output_path
    if not destination.is_relative_to(directory):
        raise ValueError("Capture output must belong to the owned runtime")
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
            for log_name, value in (("stdout.bin", completed.stdout), ("stderr.bin", completed.stderr)):
                with (directory / log_name).open("ab") as log:
                    log.write(value.encode("utf-8"))
                    log.flush()
                    os.fsync(log.fileno())
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
                    raise RuntimeError(f"Correction field is not numeric: {correction!r}") from error
                if not number.is_finite() or number < 0:
                    raise RuntimeError("Correction field must be finite and nonnegative")
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
                for log_name, value in (("stdout.bin", closed.stdout), ("stderr.bin", closed.stderr)):
                    with (directory / log_name).open("ab") as log:
                        log.write(value.encode("utf-8"))
                        log.flush()
                        os.fsync(log.fileno())
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


def _render_mock_compound(atomic_number: int, bond: BondKey, count: int) -> bytes:
    """Return exact native one-atom mock bytes for atomic_number/bond/positive count.

    Use the pinned header and native 18-column order, density one and Custom label.
    Perform no I/O; raise ValueError for unsupported identities/counts or serialization.
    """
    if type(atomic_number) is not int or not 1 <= atomic_number <= 92 or type(count) is not int or count < 1:
        raise ValueError("Mock requires an SR element and positive integer bond count")
    canonical = next(iter(_normalize_entries("bond", {bond: "0"})))
    if canonical not in SR_BONDS:
        raise ValueError("Mock bond must be one of the 18 SR types")
    vector = [str(count) if item == canonical else "0" for item in SR_BONDS]
    lines = ["*Custom", f'"Mock {_SYMBOLS[atomic_number - 1]}", 1.0, 1, {atomic_number}, 1', " ".join(vector), "$Synthetic CAB recovery input.", "!"]
    return MOCK_HEADER + ("\r\n".join(lines) + "\r\n").encode("ascii")


def _estimate_bond_count(correction: str) -> int:
    """Return max(1,ceil(1/correction)) with exact integer ceiling and no I/O.

    correction is a positive finite captured decimal string. Raise ValueError for
    zero/negative/nonfinite/malformed input; never search, retry, round or clamp.
    """
    try:
        if not isinstance(correction, str):
            raise ValueError("Captured correction must be a decimal string")
        _normalize_entries("core", {1: correction})
        number = Decimal(correction)
        if number <= 0:
            raise ValueError("Initial one-bond correction is zero or negative; cannot estimate threshold")
        numerator, denominator = number.as_integer_ratio()
        return max(1, (denominator + numerator - 1) // numerator)
    except (DecimalException, OverflowError) as error:
        raise ValueError("CAB arithmetic exceeds supported decimal limits") from error


def _recover_bond_value(correction: str, hydrogen_stopping: str, count: int) -> str:
    """Return correction*hydrogen_stopping/count as a local-context decimal string without I/O.

    Require positive finite decimal strings and an actual positive integer count;
    raise ValueError for invalid/negative/nonfinite data. Preserve no global context.
    """
    try:
        if type(count) is not int or count < 1 or not isinstance(correction, str) or not isinstance(hydrogen_stopping, str):
            raise ValueError("Bond recovery requires decimal strings and a positive count")
        _normalize_entries("core", {1: correction, 2: hydrogen_stopping})
        if Decimal(correction) <= 0 or Decimal(hydrogen_stopping) <= 0:
            raise ValueError("Bond recovery correction/stopping must be positive")
        with localcontext() as context:
            context.prec, context.rounding = CAB_PRECISION, CAB_ROUNDING
            value = Decimal(correction) * Decimal(hydrogen_stopping) / count
        if not value.is_finite() or value < 0:
            raise ValueError("Invalid recovered bond strength")
        return str(value)
    except (DecimalException, OverflowError) as error:
        raise ValueError("CAB arithmetic exceeds supported decimal limits") from error


def _recover_core_value(captures: Sequence[JSONObject], elemental_stopping: str, bond_values: Mapping[str, str]) -> JSONObject:
    """Return all 18 core estimates/mean/min/max/spread from captures and selected strengths.

    captures provides bond, correction and capture_index; elemental_stopping and
    bond_values are positive/nonnegative decimal strings. Validate complete unique
    native bonds and every estimate. Perform no I/O; raise ValueError for missing,
    negative or nonfinite results without clamping or an arbitrary spread tolerance.
    """
    try:
        if not isinstance(captures, (list, tuple)) or len(captures) != 18 or not isinstance(elemental_stopping, str):
            raise ValueError("Core recovery requires exactly 18 captures and stopping text")
        _normalize_entries("core", {1: elemental_stopping})
        if Decimal(elemental_stopping) <= 0:
            raise ValueError("Core elemental stopping must be positive")
        expected = [f"z{left:03d}-z{right:03d}-" + {"-": "s", "=": "d", "#": "t"}[order] for left, right, order in SR_BONDS]
        if not isinstance(bond_values, Mapping) or set(bond_values) != set(expected):
            raise ValueError("Core recovery needs all 18 default bond strengths")
        seen: set[str] = set()
        estimates: list[JSONObject] = []
        values: list[Decimal] = []
        with localcontext() as context:
            context.prec, context.rounding = CAB_PRECISION, CAB_ROUNDING
            for capture in captures:
                if not isinstance(capture, dict) or set(capture) != {"bond", "correction", "capture_index"} or capture["bond"] not in expected or capture["bond"] in seen or type(capture["capture_index"]) is not int or not 0 <= capture["capture_index"] < 18:
                    raise ValueError("Invalid/duplicate core capture identity")
                seen.add(capture["bond"])
                correction, strength = capture["correction"], bond_values[capture["bond"]]
                if not isinstance(correction, str) or not isinstance(strength, str):
                    raise ValueError("Core recovery quantities must be strings")
                _normalize_entries("core", {1: correction, 2: strength})
                value = Decimal(correction) * Decimal(elemental_stopping) - Decimal(strength)
                if not value.is_finite() or value < 0:
                    raise ValueError(f"Negative/nonfinite core estimate for {capture['bond']}: {value}")
                estimates.append({**capture, "bond_strength": strength, "value": str(value)})
                values.append(value)
            return {"estimates": estimates, "mean": str(sum(values, Decimal(0)) / 18), "minimum": str(min(values)), "maximum": str(max(values)), "spread": str(max(values) - min(values))}
    except (DecimalException, OverflowError) as error:
        raise ValueError("CAB arithmetic exceeds supported decimal limits") from error


def _make_capture_request(atomdata: JSONObject, atomic_number: int) -> JSONObject:
    """Return the diagnostic request for atomdata/atomic_number without I/O.

    Require SR atomic number 1..92 and valid native atomic rows. Preserve the
    existing fixed hydrogen energy, solid target and correction-none settings;
    raise ValueError for invalid element identity and propagate invalid row data.
    """
    if type(atomic_number) is not int or not 1 <= atomic_number <= 92:
        raise ValueError("Invalid capture atomic number")
    atoms, z = atomdata, atomic_number
    hydrogen = atoms["rows"][0]["isotope_mass_amu"]
    with localcontext() as context:
        context.prec, context.rounding = CAB_PRECISION, CAB_ROUNDING
        energy = format(125 * Decimal(hydrogen), "f")
    request = {"schema_version": 1, "parameters": {"projectile": {"atomic_number": 1, "mass_amu": format(Decimal(hydrogen), "f")}, "target": {"phase": "solid", "density_g_cm3": "1.0", "elements": [{"atomic_number": z, "name": atoms["rows"][z - 1]["name"], "stoichiometry": "1", "mass_amu": format(Decimal(atoms["rows"][z - 1]["natural_mass_amu"]), "f")}]}, "correction": {"mode": "none", "applied_factor": "1.0"}, "stopping_units": 7, "energy": {"mode": "list", "values_keV": [energy]}}}
    return request


def _prepare_capture_runtime(paths: ProjectPaths, config: Config, created: CreatedDirectory, worker: CreatedDirectory, defaults: JSONObject, compound: bytes) -> Path:
    """Stage pinned defaults/compound for created in worker under paths/config.

    Verify the numerical baseline, create exactly the sufficient eight-file native
    runtime with generated manifest and correction-none initialization, and return
    its owned Path. Never touch installed files. Raise ValueError for invalid input
    or baseline drift; propagate I/O errors with surviving directory evidence.
    """
    _validate_preparation_allocations(paths, created, worker)
    if not isinstance(worker, CreatedDirectory) or worker.kind != "worker" or worker.path != paths.workers / f"worker_{worker.suffix}" or not isinstance(compound, bytes):
        raise ValueError("Capture requires allocated worker and compound bytes")
    from src.srim.material import parse_compounds
    parsed = parse_compounds(compound)
    if len(parsed["records"]) != 1:
        raise ValueError("Capture runtime requires one mock compound")
    baseline = _read_json(paths.resources / "baselines" / f'{defaults["baseline_id"]}.json')
    verify_sr_baseline(paths, config, baseline)
    files = {"SR.exe": _read_regular_bytes(config.srim_root / "SR.exe"), "SR.exe.manifest": CAPTURE_MANIFEST, "Data/Compound.dat": compound, "Data/VERSION": _read_regular_bytes(config.srim_root / "Data" / "VERSION")}
    for role, filename in (("atomdata", "ATOMDATA"), ("electronic", "SCOEF03.dat"), ("nuclear", "SNUC03.dat")):
        reference = ResourceReference(**defaults["resources"][role])
        contents = _read_regular_bytes(verify_resource(paths, reference))
        if (hashlib.sha256(contents).hexdigest(), len(contents)) != (reference.sha256, reference.size_bytes):
            raise ValueError("Pinned capture resource changed while reading")
        files["Data/" + filename] = contents
    atoms = parse_atomdata(files["Data/ATOMDATA"])
    z = parsed["records"][0]["elements"][0]["atomic_number"]
    request = _make_capture_request(atoms, z)
    files["SR.IN"] = render_sr_input(request)
    parent = worker.path / "sr"
    for entry in (*reversed(parent.parents), parent):
        if entry.is_symlink() or entry.is_junction():
            raise OSError("Capture parent contains a link or junction")
    if created.kind != "srim" or created.path.parent != paths.simulation_memory or not created.path.is_dir():
        raise ValueError("Capture requires an allocated simulation")
    runtime = parent / created.path.name
    runtime.mkdir()
    try:
        (runtime / "Data").mkdir()
        for name, contents in sorted(files.items()):
            with (runtime / name).open("xb") as stream:
                stream.write(contents)
                stream.flush()
                os.fsync(stream.fileno())
        verify_sr_baseline(paths, config, baseline)
        return runtime
    except BaseException as error:
        error.add_note(f"Capture preparation retained runtime: {runtime}")
        raise


def _capture_mock(paths: ProjectPaths, config: Config, worker: CreatedDirectory, defaults: JSONObject, atomic_number: int, bond: BondKey, count: int, *, timeout_seconds: float) -> JSONObject:
    """Capture one mock atomic_number/bond/count under paths/config/worker and pinned defaults.

    timeout_seconds bounds the owned GUI operation. Hold the worker lease and
    dedicated capture lock; preserve inputs/diagnostics and capture manifest in
    an allocated permanent simulation record before returning evidence,
    then return correction plus exact file evidence. No nested engine calculation.
    On errors/interruption retain diagnostics, perform owned cleanup and re-raise.
    """
    compound = _render_mock_compound(atomic_number, bond, count)
    if type(timeout_seconds) not in (int, float) or timeout_seconds <= 0 or not math.isfinite(timeout_seconds):
        raise ValueError("Capture timeout must be positive and finite")
    with _lock_worker(paths, worker.path), _lock_capture(paths):
        created = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
        runtime = _prepare_capture_runtime(paths, config, created, worker, defaults, compound)
        request = _make_capture_request(parse_atomdata((runtime / "Data/ATOMDATA").read_bytes()), atomic_number)
        initialize_capture_simulation(paths, config, created, runtime, defaults, request)
        start_simulation(paths, config, created.path.name)
        names = ("SR.exe", "SR.exe.manifest", "SR.IN", "Data/ATOMDATA", "Data/Compound.dat", "Data/SCOEF03.dat", "Data/SNUC03.dat", "Data/VERSION")
        inputs: list[JSONObject] = []
        for name in sorted(names):
            checksum, size = _hash_file(runtime / name)
            inputs.append({"kind": "simulation", "id": created.path.name, "path": "output/raw/runtime/" + name, "sha256": checksum, "size_bytes": size})
        for name in ("stdout.bin", "stderr.bin"):
            with (runtime / name).open("xb") as stream:
                stream.flush()
                os.fsync(stream.fileno())
        manifest = {"schema_version": 1, "status": "running", "inputs_before": inputs, "inputs_after": [], "correction": None, "process_id": None, "started_at": datetime.now(timezone.utc).isoformat(), "finished_at": None, "cleanup": False, "error": None}
        try:
            correction = _capture_correction(runtime, "Custom", runtime / "correction.json", timeout_seconds=timeout_seconds)
            manifest["correction"] = correction
            raw = _read_json(runtime / "correction.json")
            manifest["process_id"] = raw["process_id"]
            after = []
            for name in sorted(names):
                checksum, size = _hash_file(runtime / name)
                after.append({"kind": "simulation", "id": created.path.name, "path": "output/raw/runtime/" + name, "sha256": checksum, "size_bytes": size})
            manifest["inputs_after"] = after
            if inputs != after:
                raise ValueError("Capture runtime inputs changed during GUI execution")
            baseline = _read_json(paths.resources / "baselines" / f'{defaults["baseline_id"]}.json')
            verify_sr_baseline(paths, config, baseline)
            manifest.update({"status": "succeeded", "finished_at": datetime.now(timezone.utc).isoformat(), "cleanup": True})
            _write_json(runtime / "manifest.json", manifest)
            evidence: JSONObject = {"inputs": inputs}
            for role, name in (("manifest", "manifest.json"), ("correction", "correction.json"), ("stdout", "stdout.bin"), ("stderr", "stderr.bin")):
                checksum, size = _hash_file(runtime / name)
                evidence[role] = {"kind": "simulation", "id": created.path.name, "path": "output/raw/runtime/" + name, "sha256": checksum, "size_bytes": size}
            finish_capture_simulation(paths, config, created.path.name, runtime)
            return {"correction": correction, "evidence": evidence}
        except BaseException as error:
            manifest.update({"status": "failed", "finished_at": datetime.now(timezone.utc).isoformat(), "error": str(error) or type(error).__name__})
            try:
                if (runtime / "correction.json").is_file():
                    raw = _read_json(runtime / "correction.json")
                    manifest["correction"], manifest["process_id"] = raw.get("correction"), raw.get("process_id")
                with (runtime / "stderr.bin").open("ab") as stream:
                    stream.write((str(error) + "\n").encode("utf-8", errors="backslashreplace"))
                    stream.flush()
                    os.fsync(stream.fileno())
                _write_json(runtime / "manifest.json", manifest)
            except BaseException as secondary:
                error.add_note(f"Capture evidence publication also failed: {secondary}")
            try:
                finish_capture_simulation(paths, config, created.path.name, runtime, {"message": str(error) or type(error).__name__, "exit_code": None})
            except BaseException as secondary:
                error.add_note(f"Capture preservation also failed: {secondary}")
            error.add_note(f"Capture evidence retained: {runtime}; record: {created.path}")
            raise


def _require_active_recovery_generation(paths: ProjectPaths, defaults: JSONObject) -> None:
    """Require paths' verified active generation to equal validated defaults.

    Return None without writes when generations match; raise ValueError for an
    inactive, uninitialized or invalid active generation. Filesystem errors
    propagate. This boundary check does not lock out later default switches.
    """
    if read_sr_defaults(paths) != defaults:
        raise ValueError("An inactive default generation cannot recover missing entries")


def resolve_default_bond(paths: ProjectPaths, config: Config, worker: CreatedDirectory, defaults: JSONObject, bond: BondKey, *, capture_timeout_seconds: float = 30.0, dependency_timeout_seconds: float = 60.0) -> CABEntryResult:
    """Resolve bond from pinned all-default inputs using paths/config/worker and per-operation timeouts.

    Reuse verified entries first. Otherwise anchor H Bragg, capture one/n/n-1 with
    count reuse, verify the displayed threshold and publish the immutable strength.
    Return reference plus calculation state. Zero initial capture or threshold
    failure raises without search/retry, retaining all capture/dependency evidence.
    Never mix baselines; propagate cancellation after owned cleanup. Check active
    defaults before missing recovery dependencies and each capture; cached inactive
    entries remain readable. A switch stops the next checked operation, not an
    already admitted operation or publication of its pinned result.
    """
    from src.srim.defaults import _validate_default_generation
    from src.srim.resolution import _make_dependency_inputs, _resolve_selection
    _validate_default_generation(defaults)
    canonical = next(iter(_normalize_entries("bond", {bond: "0"})))
    if canonical not in SR_BONDS:
        raise ValueError("Default recovery supports only the 18 SR bond types")
    for timeout in (capture_timeout_seconds, dependency_timeout_seconds):
        if type(timeout) not in (int, float) or timeout <= 0 or not math.isfinite(timeout):
            raise ValueError("Recovery timeouts must be positive and finite")
    dataset = ResourceReference(**defaults["resources"]["bond"])
    descriptor_path = verify_resource(paths, dataset)
    descriptor = _read_json(descriptor_path)
    if descriptor.get("baseline_id") != defaults["baseline_id"]:
        raise ValueError("Default bond descriptor baseline mismatch")
    identifier = f"z{canonical[0]:03d}-z{canonical[1]:03d}-" + {"-": "s", "=": "d", "#": "t"}[canonical[2]]
    matches = sorted(descriptor_path.parent.glob(f"entries/*/{identifier}.json"))
    if len(matches) > 1:
        raise ValueError("Multiple default bond entries identify one baseline result")
    if matches:
        path = matches[0]
        checksum, size = _hash_file(path)
        reference = CABEntryReference(dataset, path.parent.name, identifier, checksum, size)
        load_cab_entry(paths, reference)
        return CABEntryResult(reference, False)
    _require_active_recovery_generation(paths, defaults)
    baseline = _read_json(paths.resources / "baselines" / f'{defaults["baseline_id"]}.json')
    verify_sr_baseline(paths, config, baseline)
    resources: JSONObject = {}
    snapshots: dict[str, bytes] = {}
    for role in ("atomdata", "electronic", "nuclear"):
        resources[role], contents = _resolve_selection(paths, config, role, {"version": defaults["resources"][role]["version"], "overrides": [], "create_version": False}, defaults)
        snapshots.update(contents)
    encoded = (json.dumps(defaults, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    recorded = {"generation": defaults, "sha256": hashlib.sha256(encoded).hexdigest(), "size_bytes": len(encoded)}
    engine = {"SRModule.exe": _read_regular_bytes(config.srim_root / "SR Module" / "SRModule.exe"), "VERSION": read_module_version(config.srim_root)[1], "SCOEF03.dat": snapshots["resources/electronic/effective"], "SNUC03.dat": snapshots["resources/nuclear/effective"]}
    inputs = _make_dependency_inputs(recorded, resources, parse_atomdata(snapshots["resources/atomdata/effective"]), engine)
    _require_active_recovery_generation(paths, defaults)
    hydrogen = resolve_bragg(paths, config, worker, inputs, 1, timeout_seconds=dependency_timeout_seconds)
    hydrogen_value = load_cab_entry(paths, hydrogen.reference)["value"]
    dependency = json.loads(json.dumps(descriptor["dependency"]))
    dependency["entries"] = [asdict(hydrogen.reference)]
    cached = find_cab_entry(paths, dataset, dependency, identifier)
    if cached is not None:
        return CABEntryResult(cached, False)
    captures: list[JSONObject] = []
    observed: dict[int, tuple[str, int]] = {}
    try:
        _require_active_recovery_generation(paths, defaults)
        first = _capture_mock(paths, config, worker, defaults, 1, canonical, 1, timeout_seconds=capture_timeout_seconds)
        observed[1] = (first["correction"], 0)
        captures.append(first["evidence"])
        count = _estimate_bond_count(first["correction"])
        for requested_count in ([count, count - 1] if count > 1 else [count]):
            if requested_count not in observed:
                _require_active_recovery_generation(paths, defaults)
                capture = _capture_mock(paths, config, worker, defaults, 1, canonical, requested_count, timeout_seconds=capture_timeout_seconds)
                observed[requested_count] = (capture["correction"], len(captures))
                captures.append(capture["evidence"])
        if Decimal(observed[count][0]) < 1 or (count > 1 and Decimal(observed[count - 1][0]) >= 1):
            raise ValueError("Estimated bond threshold failed displayed-precision verification; no retry/search performed")
        value = _recover_bond_value(observed[count][0], hydrogen_value, count)
        calculation = {key: descriptor["procedure"][key] for key in ("method", "version", "precision", "rounding")}
        calculation.update({"initial_correction": first["correction"], "estimated_count": count, "captures_by_count": [{"count": number, "capture_index": index, "correction": reading} for number, (reading, index) in observed.items()], "hydrogen_stopping": hydrogen_value, "value": value, "threshold_verified": True})
        entry = {"schema_version": 1, "kind": "bond", "entry_id": identifier, "value": value, "units": CAB_UNITS, "dependency": dependency, "simulations": [], "captures": captures, "calculation": calculation}
        verify_sr_baseline(paths, config, baseline)
        return CABEntryResult(publish_cab_entry(paths, dataset, entry), True)
    except BaseException as error:
        error.add_note(f"Bond recovery {identifier} retained captures: " + json.dumps(captures, sort_keys=True))
        error.add_note("Hydrogen Bragg reference retained: " + json.dumps(asdict(hydrogen.reference), sort_keys=True))
        raise


def resolve_default_core(paths: ProjectPaths, config: Config, worker: CreatedDirectory, defaults: JSONObject, atomic_number: int, *, capture_timeout_seconds: float = 30.0, dependency_timeout_seconds: float = 60.0) -> CABEntryResult:
    """Resolve atomic_number core using paths/config/worker and pinned all-default generation.

    Per-operation timeouts bound capture/Bragg dependencies. Reuse cached entries;
    H returns fixed zero. Missing non-H values use elemental Bragg and all 18 default
    bonds, then capture each one-bond estimate and publish its validated mean/spread.
    Return reference/calculation state. Retain evidence on error/interruption and
    never recover with a different baseline or clamp negative estimates. Check
    active defaults before missing dependencies and every capture; inactive cache
    entries remain readable. Switches stop the next checked operation without
    rolling back completed evidence or forbidding an admitted result's publication.
    """
    from src.srim.defaults import _validate_default_generation
    from src.srim.resolution import _make_dependency_inputs, _resolve_selection
    _validate_default_generation(defaults)
    if type(atomic_number) is not int or not 1 <= atomic_number <= 92:
        raise ValueError("Default cores require atomic numbers 1 through 92")
    for timeout in (capture_timeout_seconds, dependency_timeout_seconds):
        if type(timeout) not in (int, float) or timeout <= 0 or not math.isfinite(timeout):
            raise ValueError("Recovery timeouts must be positive and finite")
    dataset = ResourceReference(**defaults["resources"]["core"])
    descriptor_path = verify_resource(paths, dataset)
    descriptor = _read_json(descriptor_path)
    if descriptor.get("baseline_id") != defaults["baseline_id"]:
        raise ValueError("Default core descriptor baseline mismatch")
    identifier = f"z{atomic_number:03d}"
    matches = sorted(descriptor_path.parent.glob(f"entries/*/{identifier}.json"))
    if len(matches) > 1:
        raise ValueError("Multiple default cores identify one baseline result")
    if matches:
        path = matches[0]
        checksum, size = _hash_file(path)
        reference = CABEntryReference(dataset, path.parent.name, identifier, checksum, size)
        load_cab_entry(paths, reference)
        return CABEntryResult(reference, False)
    if atomic_number == 1:
        raise ValueError("Published H core convention entry is missing")
    _require_active_recovery_generation(paths, defaults)
    baseline = _read_json(paths.resources / "baselines" / f'{defaults["baseline_id"]}.json')
    verify_sr_baseline(paths, config, baseline)
    resources: JSONObject = {}
    snapshots: dict[str, bytes] = {}
    for role in ("atomdata", "electronic", "nuclear"):
        resources[role], contents = _resolve_selection(paths, config, role, {"version": defaults["resources"][role]["version"], "overrides": [], "create_version": False}, defaults)
        snapshots.update(contents)
    encoded = (json.dumps(defaults, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    recorded = {"generation": defaults, "sha256": hashlib.sha256(encoded).hexdigest(), "size_bytes": len(encoded)}
    engine = {"SRModule.exe": _read_regular_bytes(config.srim_root / "SR Module" / "SRModule.exe"), "VERSION": read_module_version(config.srim_root)[1], "SCOEF03.dat": snapshots["resources/electronic/effective"], "SNUC03.dat": snapshots["resources/nuclear/effective"]}
    inputs = _make_dependency_inputs(recorded, resources, parse_atomdata(snapshots["resources/atomdata/effective"]), engine)
    _require_active_recovery_generation(paths, defaults)
    elemental = resolve_bragg(paths, config, worker, inputs, atomic_number, timeout_seconds=dependency_timeout_seconds)
    elemental_value = load_cab_entry(paths, elemental.reference)["value"]
    references: list[JSONObject] = [asdict(elemental.reference)]
    strengths: dict[str, str] = {}
    for bond in SR_BONDS:
        result = resolve_default_bond(paths, config, worker, defaults, bond, capture_timeout_seconds=capture_timeout_seconds, dependency_timeout_seconds=dependency_timeout_seconds)
        references.append(asdict(result.reference))
        strengths[result.reference.entry_id] = load_cab_entry(paths, result.reference)["value"]
    dependency = json.loads(json.dumps(descriptor["dependency"]))
    dependency["entries"] = references
    cached = find_cab_entry(paths, dataset, dependency, identifier)
    if cached is not None:
        return CABEntryResult(cached, False)
    captures: list[JSONObject] = []
    observations: list[JSONObject] = []
    try:
        for bond, reference in zip(SR_BONDS, references[1:], strict=True):
            _require_active_recovery_generation(paths, defaults)
            capture = _capture_mock(paths, config, worker, defaults, atomic_number, bond, 1, timeout_seconds=capture_timeout_seconds)
            observations.append({"bond": reference["entry_id"], "correction": capture["correction"], "capture_index": len(captures)})
            captures.append(capture["evidence"])
        recovered = _recover_core_value(observations, elemental_value, strengths)
        calculation = {key: descriptor["procedure"][key] for key in ("method", "version", "precision", "rounding")}
        calculation.update({"elemental_stopping": elemental_value, **recovered})
        entry = {"schema_version": 1, "kind": "core", "entry_id": identifier, "value": recovered["mean"], "units": CAB_UNITS, "dependency": dependency, "simulations": [], "captures": captures, "calculation": calculation}
        verify_sr_baseline(paths, config, baseline)
        return CABEntryResult(publish_cab_entry(paths, dataset, entry), True)
    except BaseException as error:
        error.add_note(f"Core recovery {identifier} retained captures: " + json.dumps(captures, sort_keys=True))
        error.add_note("Core dependencies retained: " + json.dumps(references, sort_keys=True))
        raise


def populate_default_cab(paths: ProjectPaths, config: Config, worker: CreatedDirectory, kind: CABKind, *, entries: Sequence[ElementKey | BondKey] | None = None, capture_timeout_seconds: float = 30.0, dependency_timeout_seconds: float = 60.0, progress: Callable[[JSONObject], None] | None = None) -> list[CABEntryReference]:
    """Populate selected entries of default kind under paths/config/worker with operation timeouts.

    None means all 92 cores or 18 native bonds in stable order. Pin defaults once,
    validate all identifiers before work and report cached/calculated progress after
    lock release. Return references in selected order. First error/callback/cancellation
    propagates with completed entries retained for reuse; no rollback or retry.
    """
    if kind not in ("core", "bond") or (entries is not None and not isinstance(entries, (list, tuple))):
        raise ValueError("Expected core/bond kind and entry sequence or None")
    if progress is not None and not callable(progress):
        raise ValueError("progress must be callable or None")
    chosen = list(range(1, 93)) if kind == "core" and entries is None else list(SR_BONDS) if entries is None else list(entries)
    if not chosen:
        raise ValueError("Bulk recovery entries cannot be empty")
    normalized: list[object] = []
    for item in chosen:
        key = _atomic_number(item) if kind == "core" else next(iter(_normalize_entries("bond", {item: "0"})))
        if (kind == "core" and key > 92) or (kind == "bond" and key not in SR_BONDS) or key in normalized:
            raise ValueError("Duplicate or unsupported default recovery entry")
        normalized.append(key)
    for timeout in (capture_timeout_seconds, dependency_timeout_seconds):
        if type(timeout) not in (int, float) or timeout <= 0 or not math.isfinite(timeout):
            raise ValueError("Recovery timeouts must be positive and finite")
    defaults = read_sr_defaults(paths)
    completed: list[CABEntryReference] = []
    try:
        for key in normalized:
            result = resolve_default_core(paths, config, worker, defaults, key, capture_timeout_seconds=capture_timeout_seconds, dependency_timeout_seconds=dependency_timeout_seconds) if kind == "core" else resolve_default_bond(paths, config, worker, defaults, key, capture_timeout_seconds=capture_timeout_seconds, dependency_timeout_seconds=dependency_timeout_seconds)
            completed.append(result.reference)
            if progress is not None:
                progress({"kind": kind, "entry_id": result.reference.entry_id, "state": "calculated" if result.calculated else "cached", "completed": len(completed), "total": len(normalized)})
        return completed
    except BaseException as error:
        error.add_note("Bulk completed entries retained: " + json.dumps([asdict(reference) for reference in completed], sort_keys=True))
        raise
