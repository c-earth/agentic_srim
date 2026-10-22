"""Verify TRIM process-job ownership and popup/timeout handling without native launches."""

from __future__ import annotations

from pathlib import Path
import ctypes
import struct
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from test_trim_runtime import CACHE
from src.srim.trim_native import _filter_trim_startup_windows, _observe_trim_dialogs, _post_trim_command, _probe_trim_saved_checkpoint, _read_trim_checkpoint_snapshot, _read_trim_window_text, _read_trim_windows, execute_trim_process
from src.srim.trim_dialogs import ENGINES, initial_trim_dialog_state
from test_trim_dialogs import POLICY, WINDOW
from copy import deepcopy


class TestTRIMNative(unittest.TestCase):
    """Mock Windows job APIs and owned children in cache-only test directories."""

    def test_2008_event_observation_without_response(self: TestTRIMNative) -> None:
        """Retain current 2008 event completion and deadline without posting; return None."""
        policy = {**POLICY, "kind": "resume", "engine_family": "2008", "engine_sha256": ENGINES["2008"], "input_mode": 5}
        complete = {**deepcopy(WINDOW), "title": "End of calculation", "detection_reason": "dialog"}
        complete["controls"][0]["text"] = "Calculation Complete (100 Ions)\r\n\r\nYes = Save data\r\n\r\nNo  = Increase number of ions"
        state = {**initial_trim_dialog_state(policy), "events": []}
        with patch("src.srim.trim_native._read_trim_windows", return_value=[complete]) as read, patch("src.srim.trim_native._post_trim_command") as post:
            first = _observe_trim_dialogs(42, CACHE, policy, state, 1.0)
            repeated = _observe_trim_dialogs(42, CACHE, policy, first["state"], 2.0)
            self.assertEqual(repeated["checkpoint_prompt"], complete)
            self.assertEqual(repeated["state"]["checkpoint_started"], 1.0)
            self.assertEqual(repeated["state"]["checkpoint_completion"]["window"], complete)
            self.assertEqual(repeated["state"]["events"], [])
            form = {**complete, "class": "ThunderRT5Form", "title": "Form1", "detection_reason": "owned-popup"}
            read.return_value = [form]
            self.assertEqual(_observe_trim_dialogs(42, CACHE, policy, repeated["state"], 2.1)["unhandled"], [form])
            post.assert_not_called()

    def test_checkpoint_first_outcomes_and_priority(self: TestTRIMNative) -> None:
        """Verify initial readiness, fallback, failures and post-probe precedence; return None."""
        for case in ("ready", "event-missing", "ordinary-missing", "invalid", "unstable", "vanished", "cancel-after", "timeout-after"):
            with self.subTest(case=case), TemporaryDirectory(dir=CACHE) as directory:
                root = Path(directory)
                (root / "TRIM.exe").write_bytes(b"fixture")
                api = MagicMock()
                api.NtResumeProcess.return_value = 0
                api.CreateJobObjectW.return_value = 12
                process = MagicMock(pid=43, returncode=1, _handle=44)
                process.poll.return_value = None
                policy = {**POLICY, "kind": "resume", "input_mode": 2 if case == "ordinary-missing" else 5}
                state = {**initial_trim_dialog_state(policy), "stage": "checkpoint-check", "checkpoint_started": 0.0, "checkpoint_completion": {"process_id": 43, "window": WINDOW, "elapsed_seconds": 0.0}, "events": []}
                observation = {"state": state, "unhandled": [], "errors": [], "checkpoint_prompt": None if case == "vanished" else WINDOW}
                status = "invalid" if case == "invalid" else "waiting" if case == "unstable" else "unavailable" if case.endswith("missing") else "ready"
                probe = {"ready": status == "ready", "status": status, "candidate": {"elapsed_seconds": 0.0, "sources": []} if status == "ready" else None, "sources": [], "last_error": "test readiness reason"}
                second = {"state": state, "unhandled": [], "errors": ["stop after fallback authorization"]}
                process_list = ctypes.create_string_buffer(1024)
                struct.pack_into("IIQ", process_list, 0, 1, 1, 43)
                elapsed = 2.0 if case in ("ready", "cancel-after", "timeout-after") else 10.0
                with patch("src.srim.trim_native.ctypes.WinDLL", return_value=api), patch("src.srim.trim_native.subprocess.Popen", return_value=process), patch("src.srim.trim_native._observe_trim_dialogs", side_effect=[observation, second]), patch("src.srim.trim_native._probe_trim_saved_checkpoint", return_value=probe), patch("src.srim.trim_native.monotonic", side_effect=[0.0, elapsed, 11.0 if case == "timeout-after" else elapsed, elapsed, elapsed, elapsed, elapsed]), patch("src.srim.trim_native.sleep"), patch("src.srim.trim_native.ctypes.create_string_buffer", side_effect=[ctypes.create_string_buffer(144), process_list, process_list]), patch("src.srim.trim_native.wintypes.BOOL", return_value=ctypes.c_long(1)), patch("src.srim.trim_native.ctypes.POINTER", return_value=ctypes.c_void_p):
                    report = execute_trim_process(root / "TRIM.exe", root, hide_window=True, timeout_seconds=10.0 if case == "timeout-after" else None, stdout_path=root / "stdout", stderr_path=root / "stderr", dialog_policy=policy, cancel=MagicMock(side_effect=[False, True]) if case == "cancel-after" else None)
                expected = {"ready": "checkpoint-stop", "invalid": "monitor-error", "ordinary-missing": "monitor-error", "cancel-after": "cancelled", "timeout-after": "timeout"}.get(case, "checkpoint-unavailable")
                self.assertEqual(report["termination"], expected, report)
                self.assertEqual(report["dialog_events"], [])
                if case == "ready":
                    self.assertEqual(report["checkpoint_stop"]["origin"], "completed-checkpoint")
                    self.assertFalse(report["checkpoint_stop"]["save_verified"])
                elif case == "ordinary-missing":
                    self.assertTrue(report["dialog_state"]["checkpoint_fallback_attempted"])
                    self.assertEqual(report["dialog_state"]["stage"], "checkpoint-save-required")
                elif case == "event-missing":
                    self.assertIn("autosave schedule", report["checkpoint_failure"]["message"])

    def test_probe_structured_failure_classes(self: TestTRIMNative) -> None:
        """Distinguish transient snapshots, unsafe I/O, missing arrays and metadata; return None."""
        policy = {**POLICY, "kind": "resume", "input_mode": 2}
        for error, status in ((FileNotFoundError("missing"), "unavailable"), (ValueError("Checkpoint changed during snapshot"), "waiting"), (ValueError("Unsafe"), "invalid"), (OSError("read failed"), "invalid")):
            with patch("src.srim.trim_native._read_trim_checkpoint_snapshot", side_effect=error):
                result = _probe_trim_saved_checkpoint(CACHE, policy, None, 1.0)
                self.assertEqual(set(result), {"ready", "status", "candidate", "sources", "last_error"})
                self.assertEqual(result["status"], status)
        fixture = Path(__file__).parent / "fixtures/trim/recovery/srim_2013_std/mode-02"
        files = {item.name: item.read_bytes() for item in fixture.glob("*.sav")}
        metadata = {name: data for name, data in files.items() if name.casefold() in ("tdata.sav", "trimin.sav")}
        from src.srim.trim_outputs import parse_trim_checkpoint
        mapping = {name.casefold(): data for name, data in metadata.items()}
        checkpoint = parse_trim_checkpoint(mapping["tdata.sav"], mapping["trimin.sav"])
        policy["autosave_ions"] = checkpoint["counts"]["autosave"]
        with patch("src.srim.trim_native._read_trim_checkpoint_snapshot", return_value=metadata):
            self.assertEqual(_probe_trim_saved_checkpoint(CACHE, policy, None, 1.0)["status"], "unavailable")
            self.assertEqual(_probe_trim_saved_checkpoint(CACHE, {**policy, "autosave_ions": 987}, None, 1.0)["status"], "invalid")
        lines = mapping["tdata.sav"].splitlines(keepends=True)
        index = checkpoint["count_line"]["line"] - 1
        values = lines[index].split()
        values[0] = b"50"
        lines[index] = b" ".join(values) + b"\r\n"
        partial = {**files, next(name for name in files if name.casefold() == "tdata.sav"): b"".join(lines)}
        with patch("src.srim.trim_native._read_trim_checkpoint_snapshot", return_value=partial):
            self.assertEqual(_probe_trim_saved_checkpoint(CACHE, policy, None, 1.0)["status"], "unavailable")

    def test_ordinary_fallback_reobserves_completion(self: TestTRIMNative) -> None:
        """Queue Yes only for the unchanged authorized prompt and bound late forms; return None."""
        policy = {**POLICY, "kind": "resume", "input_mode": 2, "cascade_output": "none"}
        complete = {**deepcopy(WINDOW), "title": "End of calculation", "detection_reason": "dialog"}
        complete["controls"][0]["text"] = "Calculation Complete (100 Ions) Yes = Save data No = Increase number of ions"
        state = {**initial_trim_dialog_state(policy), "events": []}
        with patch("src.srim.trim_native._read_trim_windows", return_value=[complete]), patch("src.srim.trim_native._post_trim_command", return_value={"queued": True, "error": None}) as post:
            inspected = _observe_trim_dialogs(42, CACHE, policy, state, 1.0)
            post.assert_not_called()
            authorized = {**inspected["state"], "stage": "checkpoint-save-required", "checkpoint_fallback_attempted": True, "checkpoint_started": 11.0}
            queued = _observe_trim_dialogs(42, CACHE, policy, authorized, 11.1)
            self.assertEqual(queued["state"]["stage"], "resume-save")
            self.assertEqual(queued["state"]["checkpoint_started"], 11.1)
            self.assertEqual(post.call_count, 1)
            late = _observe_trim_dialogs(42, CACHE, policy, authorized, 21.0)
            self.assertEqual(late["events"], [])
            changed = {**complete, "handle": 999}
            with patch("src.srim.trim_native._read_trim_windows", return_value=[changed]):
                rejected = _observe_trim_dialogs(42, CACHE, policy, authorized, 11.2)
            self.assertEqual(rejected["unhandled"], [changed])
            self.assertEqual(post.call_count, 1)

    def test_checkpoint_probe_stability_and_identity(self: TestTRIMNative) -> None:
        """Read actual arrays and reset readiness on changes or invalid identity; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            root = Path(directory)
            source = root / "SRIM Restore"
            source.mkdir()
            fixture = Path(__file__).parent / "fixtures/trim/recovery/srim_2013_std/mode-02"
            for item in fixture.glob("*.sav"):
                shutil.copyfile(item, source / item.name)
            from src.srim.trim_recovery import recover_trim_checkpoint
            checkpoint = recover_trim_checkpoint(_read_trim_checkpoint_snapshot(root))["checkpoint"]
            policy = {**POLICY, "kind": "resume", "input_mode": 2, "autosave_ions": checkpoint["counts"]["autosave"]}
            first = _probe_trim_saved_checkpoint(root, policy, None, 1.0)
            self.assertFalse(first["ready"])
            self.assertFalse(_probe_trim_saved_checkpoint(root, policy, first["candidate"], 1.9)["ready"])
            self.assertTrue(_probe_trim_saved_checkpoint(root, policy, first["candidate"], 2.0)["ready"])
            (source / "extra.bin").write_bytes(b"preserved")
            changed = _probe_trim_saved_checkpoint(root, policy, first["candidate"], 3.0)
            self.assertFalse(changed["ready"])
            self.assertEqual(changed["candidate"]["elapsed_seconds"], 3.0)
            for update in ({"expected_count": 101}, {"autosave_ions": 987}, {"input_mode": 1}, {"engine_family": "2008", "engine_sha256": ENGINES["2008"]}):
                failed = _probe_trim_saved_checkpoint(root, {**policy, **update}, None, 4.0)
                self.assertFalse(failed["ready"])
                self.assertIsNone(failed["candidate"])
            (source / "IONIZ.sav").write_bytes(b"truncated")
            self.assertIsNone(_probe_trim_saved_checkpoint(root, policy, changed["candidate"], 4.0)["candidate"])
            (source / "nested").mkdir()
            with self.assertRaises(ValueError):
                _read_trim_checkpoint_snapshot(root)

    def test_verified_save_transition_bound(self: TestTRIMNative) -> None:
        """Allow ten seconds for default save and begin probing after disappearance; return None."""
        policy = {**POLICY, "kind": "resume", "input_mode": 2, "cascade_output": "none"}
        form = {**deepcopy(WINDOW), "class": "ThunderRT5Form", "title": "Form1", "detection_reason": "owned-popup", "controls": [{"handle": i + 20, "pid": 42, "class": "ThunderRT5CommandButton", "text": text, "control_id": i, "visible": True, "enabled": True} for i, text in ((1, "Continue without Saving TRIM"), (2, "Store in another Directory / Disk"), (3, "Store in SRIM directory (default)"))]}
        state = {**initial_trim_dialog_state(policy), "stage": "resume-save", "checkpoint_fallback_attempted": True, "events": []}
        with patch("src.srim.trim_native._read_trim_windows", return_value=[form]) as read, patch("src.srim.trim_native._post_trim_command", return_value={"queued": True, "error": None}):
            posted = _observe_trim_dialogs(42, CACHE, policy, state, 1.0)
            self.assertIsNone(posted["state"]["checkpoint_save_started"])
            self.assertFalse(_observe_trim_dialogs(42, CACHE, policy, posted["state"], 4.0)["errors"])
            self.assertTrue(_observe_trim_dialogs(42, CACHE, policy, posted["state"], 11.0)["errors"])
            read.return_value = []
            verified = _observe_trim_dialogs(42, CACHE, policy, posted["state"], 4.0)
            self.assertEqual(verified["state"]["checkpoint_save_started"], 4.0)
            self.assertIsNone(verified["state"]["pending_sequence"])

    def test_checkpoint_stop_owned_job(self: TestTRIMNative) -> None:
        """Stop an owned job after readiness and retain actual nonzero exit code; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            root = Path(directory)
            (root / "TRIM.exe").write_bytes(b"fixture")
            api = MagicMock()
            api.NtResumeProcess.return_value = 0
            api.CreateJobObjectW.return_value = 12
            process = MagicMock(pid=43, returncode=1, _handle=44)
            process.poll.return_value = None
            policy = {**POLICY, "kind": "resume"}
            state = {**initial_trim_dialog_state(policy), "stage": "checkpoint-saved", "checkpoint_save_started": 0.0, "checkpoint_completion": {"window": WINDOW}, "events": []}
            observation = {"state": state, "unhandled": [], "errors": []}
            probe = {"ready": True, "status": "ready", "candidate": {"elapsed_seconds": 0.0, "sources": []}, "sources": [], "last_error": None}
            with patch("src.srim.trim_native.ctypes.WinDLL", return_value=api), patch("src.srim.trim_native.subprocess.Popen", return_value=process), patch("src.srim.trim_native._observe_trim_dialogs", return_value=observation), patch("src.srim.trim_native._probe_trim_saved_checkpoint", return_value=probe), patch("src.srim.trim_native.monotonic", side_effect=[0.0, 2.0, 2.0, 2.0, 2.1]):
                process_list = ctypes.create_string_buffer(1024)
                struct.pack_into("IIQ", process_list, 0, 1, 1, 43)
                with patch("src.srim.trim_native.ctypes.create_string_buffer", side_effect=[ctypes.create_string_buffer(144), process_list]), patch("src.srim.trim_native.wintypes.BOOL", return_value=ctypes.c_long(1)), patch("src.srim.trim_native.ctypes.POINTER", return_value=ctypes.c_void_p):
                    report = execute_trim_process(root / "TRIM.exe", root, hide_window=True, timeout_seconds=None, stdout_path=root / "stdout", stderr_path=root / "stderr", dialog_policy=policy)
            self.assertEqual(report["termination"], "checkpoint-stop")
            self.assertEqual(report["exit_code"], 1)
            self.assertTrue(report["checkpoint_stop"]["job_empty"])
            api.TerminateJobObject.assert_called_once_with(12, 1)

    def test_event_completion_save_transition_verified(self: TestTRIMNative) -> None:
        """Inspect event completion without responses and reject subsequent save forms; return None."""
        policy = {**POLICY, "kind": "resume", "input_mode": 5, "cascade_output": "none"}
        state = {**initial_trim_dialog_state(policy), "events": []}
        complete = {**deepcopy(WINDOW), "title": "End of TRIM.DAT calculation", "detection_reason": "dialog", "controls": [{"class": "Static", "text": "Calculation Complete (100 Ions)"}, {"handle": 12, "class": "Button", "text": "OK", "control_id": 2, "enabled": True, "visible": True, "pid": 42}]}
        form = {**deepcopy(WINDOW), "class": "ThunderRT5Form", "title": "Form1", "detection_reason": "owned-popup", "controls": [{"handle": i + 20, "pid": 42, "class": "ThunderRT5CommandButton", "text": text, "control_id": i, "visible": True, "enabled": True} for i, text in ((1, "Continue without Saving TRIM"), (2, "Store in another Directory / Disk"), (3, "Store in SRIM directory (default)"))]}
        with patch("src.srim.trim_native._read_trim_windows", side_effect=[[complete], [complete], [form], []]), patch("src.srim.trim_native._post_trim_command", return_value={"queued": True, "error": None}) as post:
            first = _observe_trim_dialogs(42, CACHE, policy, state, 1.0)
            self.assertEqual(first["state"]["stage"], "checkpoint-check")
            saved = _observe_trim_dialogs(42, CACHE, policy, first["state"], 1.1)
            self.assertEqual(saved["state"]["checkpoint_started"], 1.0)
            pending = _observe_trim_dialogs(42, CACHE, policy, saved["state"], 1.2)
            self.assertIsNone(pending["state"]["checkpoint_save_started"])
            self.assertEqual(pending["unhandled"], [form])
            final = _observe_trim_dialogs(42, CACHE, policy, pending["state"], 1.3)
        post.assert_not_called()
        self.assertIsNone(final["state"]["checkpoint_save_started"])
        self.assertEqual(final["state"]["stage"], "checkpoint-check")
        self.assertIsNone(final["state"]["pending_sequence"])
        self.assertTrue(all(e["transition"] == "verified" for e in final["state"]["events"]))

    def test_save_probe_gates_and_deadline(self: TestTRIMNative) -> None:
        """Prioritize popup/cancel/timeout and bound unready saves without generic-done probes; return None."""
        for case in ("popup", "cancel", "timeout", "deadline", "late-ready", "done", "descendant"):
            with self.subTest(case=case), TemporaryDirectory(dir=CACHE) as directory:
                root = Path(directory)
                (root / "TRIM.exe").write_bytes(b"fixture")
                api = MagicMock()
                api.NtResumeProcess.return_value = 0
                api.CreateJobObjectW.return_value = 12
                process = MagicMock(pid=43, returncode=0 if case == "descendant" else 1, _handle=44)
                process.poll.return_value = 0 if case == "descendant" else None
                policy = {**POLICY, "kind": "resume"}
                state = {**initial_trim_dialog_state(policy), "stage": "done" if case == "done" else "checkpoint-saved", "checkpoint_save_started": 0.0, "events": []}
                popup = {"detection_reason": "dialog", "title": "Unknown"}
                observation = {"state": state, "unhandled": [popup] if case == "popup" else [], "errors": []}
                process_list = ctypes.create_string_buffer(1024)
                struct.pack_into("IIQ", process_list, 0, 1, 1, 43)
                probe = {"ready": case == "late-ready", "status": "ready" if case == "late-ready" else "unavailable", "candidate": {"elapsed_seconds": 0.0, "sources": []} if case == "late-ready" else None, "sources": [], "last_error": "incomplete"}
                with patch("src.srim.trim_native.ctypes.WinDLL", return_value=api), patch("src.srim.trim_native.subprocess.Popen", return_value=process), patch("src.srim.trim_native._observe_trim_dialogs", return_value=observation), patch("src.srim.trim_native._probe_trim_saved_checkpoint", return_value=probe) as probing, patch("src.srim.trim_native.monotonic", return_value=20.0) as timing, patch("src.srim.trim_native.sleep"):
                    timing.side_effect = [0.0, 20.0, 20.0, 20.0, 20.1, 20.2]
                    with patch("src.srim.trim_native.ctypes.create_string_buffer", side_effect=[ctypes.create_string_buffer(144), process_list]), patch("src.srim.trim_native.wintypes.BOOL", return_value=ctypes.c_long(1)), patch("src.srim.trim_native.ctypes.POINTER", return_value=ctypes.c_void_p):
                        report = execute_trim_process(root / "TRIM.exe", root, hide_window=True, timeout_seconds=1.0 if case == "timeout" else None, stdout_path=root / "stdout", stderr_path=root / "stderr", dialog_policy=policy, cancel=MagicMock(side_effect=[True] if case == "cancel" else [False, True]) if case in ("cancel", "done") else None)
                self.assertEqual(report["termination"], {"popup": "popup", "cancel": "cancelled", "timeout": "timeout", "deadline": "checkpoint-unavailable", "late-ready": "checkpoint-unavailable", "done": "cancelled", "descendant": "checkpoint-unavailable"}[case])
                self.assertIsNone(report["checkpoint_stop"])
                if case in ("popup", "cancel", "timeout", "done"):
                    probing.assert_not_called()
                else:
                    probing.assert_called_once()
                api.TerminateJobObject.assert_called_once_with(12, 1)

    def test_job_shutdown_failure_is_not_frozen(self: TestTRIMNative) -> None:
        """Raise with explicit unsafe-freeze evidence if termination/query or shutdown fails; return None."""
        for failure in ("terminate", "query", "deadline"):
            with self.subTest(failure=failure), TemporaryDirectory(dir=CACHE) as directory:
                root = Path(directory)
                (root / "TRIM.exe").write_bytes(b"fixture")
                api = MagicMock()
                api.NtResumeProcess.return_value = 0
                api.CreateJobObjectW.return_value = 12
                if failure == "terminate":
                    api.TerminateJobObject.return_value = 0
                if failure == "query":
                    api.QueryInformationJobObject.side_effect = OSError("cannot establish job emptiness")
                process = MagicMock(pid=43, returncode=1, _handle=44)
                process.poll.return_value = None
                process_list = ctypes.create_string_buffer(1024)
                struct.pack_into("IIQ", process_list, 0, 1, 1, 43)
                with patch("src.srim.trim_native.ctypes.WinDLL", return_value=api), patch("src.srim.trim_native.subprocess.Popen", return_value=process), patch("src.srim.trim_native.monotonic", side_effect=[0.0, 0.0, 11.0]), patch("src.srim.trim_native.ctypes.memset"), patch("src.srim.trim_native.ctypes.create_string_buffer", side_effect=[ctypes.create_string_buffer(144), process_list]):
                    with self.assertRaises(OSError) as caught:
                        execute_trim_process(root / "TRIM.exe", root, hide_window=True, timeout_seconds=None, stdout_path=root / "stdout", stderr_path=root / "stderr", cancel=MagicMock(return_value=True))
                self.assertIs(caught.exception.trim_job_stopped, False)
                self.assertTrue(any("not frozen" in note for note in caught.exception.__notes__))
                api.CloseHandle.assert_called_with(12)

    def test_startup_candidate_transitions(self: TestTRIMNative) -> None:
        """Check synthetic disappearance, hiding and readable transitions without mutation; return None."""
        candidate = {"pid": 42, "handle": 12, "owner_handle": 11, "class": "ThunderRT5Form", "title": "", "visible": True, "enabled": True, "rectangle": [645, 406, 891, 457], "controls": [], "capture_errors": ["Text length unavailable"], "detection_reason": "owned-popup"}
        for transition in ([], [{**candidate, "visible": False, "capture_errors": [], "detection_reason": None}], [{**candidate, "title": "Starting TRIM...", "capture_errors": [], "detection_reason": None}]):
            with self.subTest(transition=transition):
                state: dict[tuple[int, int], float] = {}
                original = deepcopy(candidate)
                self.assertIsNone(_filter_trim_startup_windows([candidate], state, 1.0)[0]["detection_reason"])
                self.assertEqual(_filter_trim_startup_windows(transition, state, 1.1), transition)
                self.assertEqual(candidate, original)
                self.assertEqual(state, {(42, 12): 1.0})

    def test_startup_boundaries_and_identity(self: TestTRIMNative) -> None:
        """Bound synthetic candidate lifetime and native startup time independently per PID; return None."""
        candidate = {"pid": 42, "handle": 12, "owner_handle": 11, "class": "ThunderRT5Form", "title": "", "visible": True, "enabled": True, "rectangle": [645, 406, 891, 457], "controls": [], "capture_errors": ["unavailable"], "detection_reason": "owned-popup"}
        state: dict[tuple[int, int], float] = {}
        for elapsed in (1.0, 1.2, 1.49):
            self.assertIsNone(_filter_trim_startup_windows([candidate], state, elapsed)[0]["detection_reason"])
        self.assertEqual(_filter_trim_startup_windows([candidate], state, 1.5)[0]["detection_reason"], "owned-popup")
        self.assertIsNone(_filter_trim_startup_windows([{**candidate, "pid": 43}], state, 1.5)[0]["detection_reason"])
        self.assertEqual(state, {(42, 12): 1.0, (43, 12): 1.5})
        self.assertIsNone(_filter_trim_startup_windows([candidate], {}, 4.9)[0]["detection_reason"])
        self.assertEqual(_filter_trim_startup_windows([candidate], {}, 5.0)[0]["detection_reason"], "owned-popup")
        for invalid in (-1.0, float("nan"), float("inf"), True):
            with self.assertRaises(ValueError):
                _filter_trim_startup_windows([], {}, invalid)

    def test_startup_real_dialogs_remain_reported(self: TestTRIMNative) -> None:
        """Preserve changed and simultaneous dialogs and reject broader candidate matches; return None."""
        candidate = {"pid": 42, "handle": 12, "owner_handle": 11, "class": "ThunderRT5Form", "title": "", "visible": True, "enabled": True, "rectangle": [645, 406, 891, 457], "controls": [], "capture_errors": ["unavailable"], "detection_reason": "owned-popup"}
        dialog = {**candidate, "handle": 13, "class": "#32770", "title": "Error", "detection_reason": "dialog"}
        result = _filter_trim_startup_windows([candidate, dialog], {}, 1.0)
        self.assertIsNone(result[0]["detection_reason"])
        self.assertEqual(result[1], dialog)
        for change in ({"title": "Error"}, {"capture_errors": []}, {"owner_handle": None}, {"enabled": False}, {"rectangle": [0, 0, 246, 51]}, {"controls": [{"class": "Button", "text": "OK"}]}):
            window = {**candidate, **change}
            self.assertEqual(_filter_trim_startup_windows([window], {}, 1.0), [window])

    def test_startup_opted_in_observation(self: TestTRIMNative) -> None:
        """Defer one synthetic startup candidate in opted-in mode, then fail expiry; return None."""
        candidate = {"pid": 42, "handle": 12, "owner_handle": 11, "class": "ThunderRT5Form", "title": "", "visible": True, "enabled": True, "rectangle": [645, 406, 891, 457], "controls": [], "capture_errors": ["unavailable"], "detection_reason": "owned-popup"}
        timing: dict[tuple[int, int], float] = {}
        state = {**initial_trim_dialog_state(POLICY), "events": []}
        with patch("src.srim.trim_native._read_trim_windows", return_value=[candidate]), patch("src.srim.trim_native._post_trim_command") as post:
            first = _observe_trim_dialogs(42, CACHE, POLICY, state, 1.0, startup_state=timing)
            self.assertEqual(first["unhandled"], [])
            expired = _observe_trim_dialogs(42, CACHE, POLICY, first["state"], 1.5, startup_state=timing)
            self.assertEqual(expired["unhandled"], [candidate])
            post.assert_not_called()

    def test_startup_wait_preserves_timeout_and_cancellation(self: TestTRIMNative) -> None:
        """Defer a synthetic owned form then honor timeout or cancellation and cleanup; return None."""
        candidate = {"pid": 43, "handle": 12, "owner_handle": 11, "class": "ThunderRT5Form", "title": "", "visible": True, "enabled": True, "rectangle": [645, 406, 891, 457], "controls": [], "capture_errors": ["unavailable"], "detection_reason": "owned-popup"}
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled), TemporaryDirectory(dir=CACHE) as directory:
                root = Path(directory)
                (root / "TRIM.exe").write_bytes(b"fixture")
                api = MagicMock()
                api.NtResumeProcess.return_value = 0
                api.CreateJobObjectW.return_value = 12
                process_list = ctypes.create_string_buffer(1024)
                struct.pack_into("IIQ", process_list, 0, 1, 1, 43)
                buffers = [ctypes.create_string_buffer(144), process_list]
                process = MagicMock(pid=43, returncode=1, _handle=44)
                process.poll.side_effect = [None, None, 1]
                with patch("src.srim.trim_native.ctypes.WinDLL", return_value=api), patch("src.srim.trim_native.subprocess.Popen", return_value=process), patch("src.srim.trim_native._read_trim_windows", return_value=[candidate]), patch("src.srim.trim_native.ctypes.create_string_buffer", side_effect=buffers), patch("src.srim.trim_native.wintypes.BOOL", return_value=ctypes.c_long(1)), patch("src.srim.trim_native.ctypes.POINTER", return_value=ctypes.c_void_p), patch("src.srim.trim_native.monotonic", side_effect=[0, 0.1, 0.3, 0.3, 0.3]), patch("src.srim.trim_native.sleep"):
                    report = execute_trim_process(root / "TRIM.exe", root, hide_window=True, timeout_seconds=0.2, stdout_path=root / "stdout", stderr_path=root / "stderr", cancel=MagicMock(side_effect=[False, True]) if cancelled else None)
                self.assertEqual(report["termination"], "cancelled" if cancelled else "timeout")
                self.assertEqual(report["popups"], [])
                api.TerminateJobObject.assert_called_once_with(12, 1)
                process.wait.assert_called_once()

    def test_known_responses_owned(self: TestTRIMNative) -> None:
        """Queue one recognized owned response and retain evidence; return None."""
        window = {**deepcopy(WINDOW), "detection_reason": "dialog"}
        state = {**initial_trim_dialog_state(POLICY), "events": []}
        with patch("src.srim.trim_native._read_trim_windows", return_value=[window]), patch("src.srim.trim_native._post_trim_command", return_value={"queued": True, "error": None}) as post:
            report = _observe_trim_dialogs(42, CACHE, POLICY, state, 0.1)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.args[0], 42)
        self.assertEqual(report["state"]["pending_sequence"], 1)
        self.assertEqual(report["events"][0]["transition"], "pending")

    def test_response_revalidation(self: TestTRIMNative) -> None:
        """Reject changed native text before posting any command; return None."""
        with patch("src.srim.trim_native._read_trim_window_text", return_value={"title": "changed", "controls": [], "capture_errors": []}), patch("src.srim.trim_native.ctypes.WinDLL") as api:
            with self.assertRaises(ValueError):
                _post_trim_command(42, WINDOW, {"action": "button", "target_handle": 12, "control_id": 6, "response": "Yes"})
            api.assert_not_called()

    def test_post_failure_no_retry(self: TestTRIMNative) -> None:
        """Record a queue failure once without retrying; return None."""
        state = {**initial_trim_dialog_state(POLICY), "events": []}
        with patch("src.srim.trim_native._read_trim_windows", return_value=[{**WINDOW, "detection_reason": "dialog"}]), patch("src.srim.trim_native._post_trim_command", side_effect=OSError("queue failed")) as post:
            report = _observe_trim_dialogs(42, CACHE, POLICY, state, 0.1)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(report["errors"], ["queue failed"])
        self.assertEqual(report["events"][0]["transition"], "failed")

    def test_transition_timeout(self: TestTRIMNative) -> None:
        """Reject an unchanged pending dialog after two seconds; return None."""
        window = {**deepcopy(WINDOW), "detection_reason": "dialog"}
        state = {**initial_trim_dialog_state(POLICY), "events": []}
        with patch("src.srim.trim_native._read_trim_windows", return_value=[window]), patch("src.srim.trim_native._post_trim_command", return_value={"queued": True, "error": None}) as post:
            first = _observe_trim_dialogs(42, CACHE, POLICY, state, 0.1)
            second = _observe_trim_dialogs(42, CACHE, POLICY, first["state"], 1)
            final = _observe_trim_dialogs(42, CACHE, POLICY, second["state"], 2.2)
        self.assertEqual(post.call_count, 1)
        self.assertTrue(final["errors"])
        self.assertEqual(final["state"]["stage"], "failed")

    def test_export_forms_never_responded(self: TestTRIMNative) -> None:
        """Reject synthetic abandoned export forms without posting commands; return None."""
        for title in ("TRIM", "Plots"):
            state = {**initial_trim_dialog_state(POLICY), "events": []}
            form = {**deepcopy(WINDOW), "class": "ThunderRT5Form", "title": title, "detection_reason": "owned-popup"}
            with patch("src.srim.trim_native._read_trim_windows", return_value=[form]), patch("src.srim.trim_native._post_trim_command") as post:
                report = _observe_trim_dialogs(42, CACHE, POLICY, state, 0.1)
            post.assert_not_called()
            self.assertEqual(report["unhandled"], [form])

    def test_legacy_resume_rejected_before_launch(self: TestTRIMNative) -> None:
        """Reject an obsolete synthetic resume policy before job/process creation; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            root = Path(directory)
            (root / "TRIM.exe").write_bytes(b"fixture")
            api = MagicMock()
            with patch("src.srim.trim_native.ctypes.WinDLL", return_value=api), patch("src.srim.trim_native.subprocess.Popen") as launch:
                with self.assertRaisesRegex(ValueError, "Legacy"):
                    execute_trim_process(root / "TRIM.exe", root, hide_window=True, timeout_seconds=None, stdout_path=root / "stdout", stderr_path=root / "stderr", dialog_policy={**POLICY, "kind": "resume", "schema_version": 2})
            launch.assert_not_called()
            api.CreateJobObjectW.assert_not_called()
            self.assertFalse((root / "stdout").exists())

    def test_menu_exit_rejected(self: TestTRIMNative) -> None:
        """Reject the removed menu command before inspection or native messaging; return None."""
        with patch("src.srim.trim_native._read_trim_window_text") as read, patch("src.srim.trim_native.ctypes.WinDLL") as api:
            with self.assertRaises(ValueError):
                _post_trim_command(42, WINDOW, {"action": "menu-exit", "target_handle": 10, "control_id": 7})
            read.assert_not_called()
            api.assert_not_called()

    def test_cancellation_during_response(self: TestTRIMNative) -> None:
        """Stop an opted-in owned job before responding when cancelled; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            root = Path(directory)
            (root / "TRIM.exe").write_bytes(b"fixture")
            api = MagicMock()
            api.NtResumeProcess.return_value = 0
            api.CreateJobObjectW.return_value = 12
            process = MagicMock(pid=43, returncode=1, _handle=44)
            process.poll.side_effect = [None, 1]
            with patch("src.srim.trim_native.ctypes.WinDLL", return_value=api), patch("src.srim.trim_native.subprocess.Popen", return_value=process), patch("src.srim.trim_native._observe_trim_dialogs") as observer:
                result = execute_trim_process(root / "TRIM.exe", root, hide_window=True, timeout_seconds=5, stdout_path=root / "stdout", stderr_path=root / "stderr", cancel=MagicMock(return_value=True), dialog_policy=POLICY)
            observer.assert_not_called()
            self.assertEqual(result["termination"], "cancelled")
            api.TerminateJobObject.assert_called_once_with(12, 1)

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
            with patch("src.srim.trim_native.ctypes.WinDLL", return_value=api), patch("src.srim.trim_native.subprocess.Popen", return_value=process), patch("src.srim.trim_native.monotonic", side_effect=[0, 10, 10, 10]):
                report = execute_trim_process(root / "TRIM.exe", root, hide_window=True, timeout_seconds=1, stdout_path=root / "stdout", stderr_path=root / "stderr")
            self.assertEqual(report["termination"], "timeout")
            api.TerminateJobObject.assert_called_once_with(12, 1)
            process.wait.assert_called_once()

    def test_unlimited_exit_and_cancellation(self: TestTRIMNative) -> None:
        """Run synthetic jobs beyond sixty seconds with no deadline; verify exit/cancel and return None."""
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled), TemporaryDirectory(dir=CACHE) as directory:
                root = Path(directory)
                (root / "TRIM.exe").write_bytes(b"fixture")
                api = MagicMock()
                api.NtResumeProcess.return_value = 0
                api.CreateJobObjectW.return_value = 12
                process = MagicMock(pid=43, returncode=0, _handle=44)
                process.poll.return_value = 0 if not cancelled else None
                if cancelled:
                    process.poll.side_effect = [None, None, 0]
                with patch("src.srim.trim_native.ctypes.WinDLL", return_value=api), patch("src.srim.trim_native.subprocess.Popen", return_value=process), patch("src.srim.trim_native.monotonic", side_effect=[0, 120, 121, 121]), patch("src.srim.trim_native._read_trim_windows", return_value=[]):
                    report = execute_trim_process(root / "TRIM.exe", root, hide_window=True, timeout_seconds=None, stdout_path=root / "stdout", stderr_path=root / "stderr", cancel=MagicMock(side_effect=[False, True]) if cancelled else None)
                self.assertEqual(report["termination"], "cancelled" if cancelled else "exit")
                self.assertGreater(report["elapsed_seconds"], 60)
                if cancelled:
                    api.TerminateJobObject.assert_called_once_with(12, 1)
                else:
                    api.TerminateJobObject.assert_not_called()
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
            api.QueryInformationJobObject.side_effect = [OSError("monitor unavailable"), True]
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
