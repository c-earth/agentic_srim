"""Verify pure TRIM response policies, exact dialog matching and history evidence."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch

from src.srim.trim.dialogs import ACTIVE, CASCADE, DETAIL, DETAIL_ACTIVE, ENGINES, KP, SPECIAL, advance_trim_dialog_state, assess_trim_histories, build_trim_dialog_policy, classify_trim_dialog, count_trim_event_records, initial_trim_dialog_state, validate_trim_native_request
from src.srim.trim.inputs import render_trim_events
from src.srim.trim.models import ResolvedTRIMInputs
from test_trim_inputs import EXPLICIT_REQUEST


POLICY = {"schema_version": 4, "engine_sha256": ENGINES["2013"], "engine_family": "2013", "input_mode": 8, "kind": "fresh", "initial_count": 0, "expected_count": 100, "autosave_ions": 100, "cascade_output": "detailed", "checkpoint_directory": "SRIM Restore", "ascii_directory": "SRIM Outputs", "response_transition_seconds": 2.0, "checkpoint_save_seconds": 10.0, "checkpoint_stable_seconds": 1.0, "shutdown_seconds": 10.0}
WINDOW = {"handle": 10, "pid": 42, "class": "#32770", "title": SPECIAL, "visible": True, "enabled": True, "capture_errors": [], "controls": [{"handle": 11, "class": "Static", "text": CASCADE}, {"handle": 12, "class": "Button", "text": "&Yes", "control_id": 6, "enabled": True, "visible": True, "pid": 42}, {"handle": 13, "class": "Button", "text": "&No", "control_id": 7, "enabled": True, "visible": True, "pid": 42}]}
CONTROL = {"kind": "fresh", "initial_count": 0, "total_ions": 100, "autosave_ions": 100}
FIXTURES = Path(__file__).parent / "fixtures/trim"


class TestTRIMDialogs(unittest.TestCase):
    """Use copied in-memory observations and committed native files without UI."""

    def test_checkpoint_inspection_across_engine_modes(self: TestTRIMDialogs) -> None:
        """Inspect every supported completion without commands and gate fallback; return None."""
        from src.srim.trim.inputs import MODES
        for family, modes in MODES.items():
            for mode in set(modes.values()):
                policy = {**POLICY, "engine_family": family, "engine_sha256": ENGINES[family], "kind": "resume", "input_mode": mode}
                state = {**initial_trim_dialog_state(policy), "stage": "resume-complete"}
                ordinary = mode in (1, 2, 3)
                ordinary_title = ordinary or family == "2008" and mode == 5
                complete = {**deepcopy(WINDOW), "title": "End of calculation" if ordinary_title else "End of TRIM.DAT calculation"}
                complete["controls"][0]["text"] = "Calculation Complete (100 Ions)" + (" Yes = Save data No = Increase number of ions" if ordinary_title else "")
                if not ordinary_title:
                    complete["controls"] = [complete["controls"][0], {**complete["controls"][1], "text": "OK", "control_id": 2}]
                action = classify_trim_dialog(complete, policy, state)
                self.assertEqual(action["decision"], "inspect-checkpoint")
                self.assertIsNone(action["command"])
                for attempted in (False, True):
                    fallback = {**state, "stage": "checkpoint-save-required", "checkpoint_fallback_attempted": attempted}
                    self.assertEqual(classify_trim_dialog(complete, policy, fallback)["decision"], "respond" if ordinary and attempted else "reject")
                changed = deepcopy(complete)
                changed["controls"][0]["text"] = changed["controls"][0]["text"].replace("100", "99")
                self.assertEqual(classify_trim_dialog(changed, policy, state)["decision"], "reject")

    def test_2008_event_prompt_replay(self: TestTRIMDialogs) -> None:
        """Inspect captured 2008 mode-five completion without granting event saving; return None."""
        policy = {**POLICY, "kind": "resume", "engine_family": "2008", "engine_sha256": ENGINES["2008"], "input_mode": 5}
        complete = {**deepcopy(WINDOW), "title": "End of calculation"}
        complete["controls"][0]["text"] = "Calculation Complete (100 Ions)\r\n\r\nYes = Save data\r\n\r\nNo  = Increase number of ions"
        for stage in ("running", "resume-complete", "checkpoint-check"):
            state = {**initial_trim_dialog_state(policy), "stage": stage}
            action = classify_trim_dialog(complete, policy, state)
            self.assertEqual(action["decision"], "inspect-checkpoint")
            self.assertIsNone(action["command"])
        for field, value in (("title", "End of TRIM.DAT calculation"), ("pid", 999), ("enabled", False), ("capture_errors", ["unavailable"])):
            self.assertEqual(classify_trim_dialog({**complete, field: value}, policy, state)["decision"], "reject")
        for index, field, value in ((0, "text", "Calculation Complete (99 Ions)"), (1, "control_id", 99), (2, "pid", 999)):
            changed = deepcopy(complete)
            changed["controls"][index][field] = value
            self.assertEqual(classify_trim_dialog(changed, policy, state)["decision"], "reject")
        self.assertEqual(classify_trim_dialog(complete, policy, {**state, "stage": "checkpoint-save-required", "checkpoint_fallback_attempted": True})["decision"], "reject")
        self.assertEqual(classify_trim_dialog(complete, {**policy, "kind": "fresh"}, {**state, "stage": "running"})["decision"], "reject")

    def test_policy_choices_and_conflicts(self: TestTRIMDialogs) -> None:
        """Check optional choices and collision conflicts without mutation; return None."""
        request = deepcopy(EXPLICIT_REQUEST)
        request["parameters"]["mode"] = "recoil-quick"
        inputs = ResolvedTRIMInputs({}, {}, request, {}, (), (("TRIM.exe", b"fixture"),))
        original = deepcopy(request)
        self.assertIsNone(build_trim_dialog_policy(inputs, CONTROL, None))
        with patch("src.srim.trim.dialogs.hashlib.sha256") as digest:
            digest.return_value.hexdigest.return_value = ENGINES["2013"]
            for choice in ("none", "statistics", "detailed"):
                policy = build_trim_dialog_policy(inputs, CONTROL, choice)
                self.assertEqual(policy["cascade_output"], choice)
                self.assertEqual(policy["schema_version"], 4)
                self.assertNotIn("resume_export_order", policy)
            request["parameters"]["outputs"]["collisions"] = "ions"
            with self.assertRaises(ValueError):
                build_trim_dialog_policy(inputs, CONTROL, "statistics")
        request["parameters"]["outputs"]["collisions"] = "none"
        self.assertEqual(request, original)

    def test_engine_hash_allowlist(self: TestTRIMDialogs) -> None:
        """Reject unknown automatic engines and preserve legacy choice; return None."""
        inputs = ResolvedTRIMInputs({}, {}, deepcopy(EXPLICIT_REQUEST), {}, (), (("TRIM.exe", b"unknown"),))
        self.assertIsNone(build_trim_dialog_policy(inputs, CONTROL, None))
        with self.assertRaises(ValueError):
            build_trim_dialog_policy(inputs, CONTROL, "none")
        for family, digest in ENGINES.items():
            policy = {**POLICY, "engine_family": family, "engine_sha256": digest, "input_mode": 6}
            self.assertEqual(initial_trim_dialog_state(policy)["stage"], "running")

    def test_exact_cascade_sequences(self: TestTRIMDialogs) -> None:
        """Match exact known messages across choices and KP stages; return None."""
        self.assertIn("\u00ba", DETAIL)
        self.assertNotIn("\u00c2", DETAIL)
        for choice in ("none", "statistics", "detailed"):
            policy = {**POLICY, "cascade_output": choice}
            state = initial_trim_dialog_state(policy)
            answer = classify_trim_dialog(WINDOW, policy, state)
            self.assertEqual(answer["command"]["response"], "No" if choice == "none" else "Yes")
        for stage, title, message, labels in (("cascade-active", SPECIAL, ACTIVE, ["OK"]), ("detail-choice", SPECIAL, DETAIL, ["Yes", "No"]), ("detail-active", SPECIAL, DETAIL_ACTIVE, ["OK"]), ("detail-choice", "TRIM", KP, ["OK"])):
            window = deepcopy(WINDOW)
            window["title"] = title
            window["controls"] = [{"class": "Static", "text": message}] + [{"handle": i + 20, "class": "Button", "text": label, "control_id": {"Yes": 6, "No": 7, "OK": 2}[label], "enabled": True, "visible": True, "pid": 42} for i, label in enumerate(labels)]
            state = {**initial_trim_dialog_state(POLICY), "stage": stage}
            self.assertEqual(classify_trim_dialog(window, {**POLICY, "input_mode": 6}, state)["decision"], "respond")

    def test_unknown_or_changed_dialog(self: TestTRIMDialogs) -> None:
        """Reject changed titles, text, controls and stages; return None."""
        for field, value in (("title", "Unexpected"), ("class", "Other"), ("enabled", False), ("capture_errors", ["unreadable"])):
            window = {**deepcopy(WINDOW), field: value}
            self.assertEqual(classify_trim_dialog(window, POLICY, initial_trim_dialog_state(POLICY))["decision"], "reject")
        state = {**initial_trim_dialog_state(POLICY), "stage": "done"}
        self.assertEqual(classify_trim_dialog(WINDOW, POLICY, state)["decision"], "reject")

    def test_no_duplicate_response(self: TestTRIMDialogs) -> None:
        """Keep pending responses waiting and reject duplicate actions; return None."""
        state = initial_trim_dialog_state(POLICY)
        event = {"queued": True, "stage_after": "cascade-active", "sequence": 1, "window": WINDOW}
        pending = advance_trim_dialog_state(state, {}, event)
        self.assertEqual(classify_trim_dialog(WINDOW, POLICY, pending)["decision"], "wait")
        with self.assertRaises(ValueError):
            advance_trim_dialog_state(pending, {}, event)
        verified = advance_trim_dialog_state(pending, {"transition": "verified"}, None)
        self.assertIsNone(verified["pending_sequence"])
        self.assertIsNone(state["pending_sequence"])

    def test_resume_checkpoint_save_without_exports(self: TestTRIMDialogs) -> None:
        """Inspect completion and require authorization for ordinary default saving; return None."""
        policy = {**POLICY, "kind": "resume", "input_mode": 2, "cascade_output": "none"}
        state = initial_trim_dialog_state(policy)
        self.assertEqual(set(state), {"stage", "pending_sequence", "expected_count", "limitations", "checkpoint_probe", "checkpoint_save_started", "checkpoint_completion", "checkpoint_started", "checkpoint_fallback_attempted"})
        complete = deepcopy(WINDOW)
        complete["title"] = "End of calculation"
        complete["controls"][0]["text"] = "Calculation Complete (100 Ions) Yes = Save data No = Increase number of ions"
        answer = classify_trim_dialog(complete, policy, state)
        self.assertEqual(answer["decision"], "inspect-checkpoint")
        self.assertIsNone(answer["command"])
        fallback = {**state, "stage": "checkpoint-save-required", "checkpoint_fallback_attempted": True}
        self.assertEqual(classify_trim_dialog(complete, policy, fallback)["next_stage"], "resume-save")
        captions = {1: "Continue without Saving TRIM", 2: "Store in another Directory / Disk", 3: "Store in SRIM directory (default)"}
        save = {**complete, "class": "ThunderRT5Form", "title": "Form1", "controls": [{"handle": 20 + key, "class": "ThunderRT5CommandButton", "text": caption, "control_id": key, "pid": 42, "enabled": True, "visible": True} for key, caption in captions.items()]}
        answer = classify_trim_dialog(save, policy, {**state, "stage": "resume-save", "checkpoint_fallback_attempted": True})
        self.assertEqual(answer["command"]["control_id"], 3)
        self.assertEqual(answer["next_stage"], "checkpoint-saved")
        for title in ("Plots", "TRIM"):
            unknown = {**save, "title": title}
            self.assertEqual(classify_trim_dialog(unknown, policy, state)["decision"], "reject")

    def test_legacy_export_policy_rejected(self: TestTRIMDialogs) -> None:
        """Reject historical policy versions and removed fields without migration; return None."""
        with self.assertRaisesRegex(ValueError, "Legacy"):
            initial_trim_dialog_state({**POLICY, "schema_version": 1})
        with self.assertRaisesRegex(ValueError, "Legacy"):
            initial_trim_dialog_state({**POLICY, "schema_version": 2})
        with self.assertRaisesRegex(ValueError, "prepare a new runtime"):
            initial_trim_dialog_state({**POLICY, "schema_version": 3})
        with self.assertRaises(ValueError):
            initial_trim_dialog_state({**POLICY, "resume_export_order": []})

    def test_event_completion_then_default_save(self: TestTRIMDialogs) -> None:
        """Keep fresh completion and reject all resumed event save forms; return None."""
        complete = {**deepcopy(WINDOW), "title": "End of TRIM.DAT calculation", "controls": [{"class": "Static", "text": "Calculation Complete (100 Ions)"}, {"handle": 12, "class": "Button", "text": "OK", "control_id": 2, "enabled": True, "visible": True, "pid": 42}]}
        policy = {**POLICY, "kind": "resume", "input_mode": 5, "cascade_output": "none"}
        state = initial_trim_dialog_state(policy)
        self.assertEqual(classify_trim_dialog(complete, policy, state)["next_stage"], "checkpoint-check")
        self.assertEqual(classify_trim_dialog(complete, {**policy, "kind": "fresh"}, state)["next_stage"], "done")
        self.assertEqual(classify_trim_dialog(complete, {**policy, "input_mode": 2}, state)["decision"], "reject")
        save = {**deepcopy(WINDOW), "class": "ThunderRT5Form", "title": "Form1", "controls": [{"handle": 20 + key, "class": "ThunderRT5CommandButton", "text": text, "control_id": key, "pid": 42, "enabled": True, "visible": True} for key, text in ((1, "Continue without Saving TRIM"), (2, "Store in another Directory / Disk"), (3, "Store in SRIM directory (default)"))]}
        ready = {**state, "stage": "resume-event-save"}
        self.assertEqual(classify_trim_dialog(save, policy, ready)["decision"], "reject")
        for stage in ("running", "done"):
            self.assertEqual(classify_trim_dialog(save, policy, {**state, "stage": stage})["decision"], "reject")
        self.assertEqual(classify_trim_dialog(save, {**policy, "kind": "fresh"}, ready)["decision"], "reject")
        self.assertEqual(classify_trim_dialog(save, policy, {**ready, "pending_sequence": 1})["decision"], "wait")
        changed = deepcopy(save)
        changed["controls"][2]["text"] = "Unknown saving choice"
        self.assertEqual(classify_trim_dialog(changed, policy, ready)["decision"], "reject")

    def test_opt_in_resume_checkpoint_policy(self: TestTRIMDialogs) -> None:
        """Seal explicit automatic resume choices and retain report-only behavior; return None."""
        inputs = ResolvedTRIMInputs({}, {}, deepcopy(EXPLICIT_REQUEST), {}, (), (("TRIM.exe", b"fixture"),))
        inputs.effective_request["parameters"]["mode"] = "recoil-quick"
        control = {**CONTROL, "kind": "resume", "initial_count": 5}
        self.assertIsNone(build_trim_dialog_policy(inputs, control, None))
        with patch("src.srim.trim.dialogs.hashlib.sha256") as digest:
            digest.return_value.hexdigest.return_value = ENGINES["2013"]
            for choice in ("none", "statistics", "detailed"):
                policy = build_trim_dialog_policy(inputs, control, choice)
                self.assertEqual(policy["kind"], "resume")
                self.assertEqual(policy["autosave_ions"], 100)
                self.assertEqual(initial_trim_dialog_state(policy)["checkpoint_probe"], None)

    def test_unavailable_history_qualification(self: TestTRIMDialogs) -> None:
        """Require statistics coverage and distinguish actual detailed tables; return None."""
        statistics = (FIXTURES / "cascade-history-2008/statistics/COLLISON.txt").read_bytes()
        detailed = (FIXTURES / "cascade-history-2008/detailed/COLLISON.txt").read_bytes()
        path = "SRIM Outputs/COLLISON.txt"
        self.assertEqual(assess_trim_histories({path: statistics}, POLICY, [])["histories"]["status"], "native-unavailable")
        full = {**POLICY, "engine_family": "2008", "engine_sha256": ENGINES["2008"], "input_mode": 7}
        self.assertEqual(assess_trim_histories({path: statistics}, full, [])["histories"]["status"], "inconsistent")
        self.assertEqual(assess_trim_histories({path: detailed}, full, [])["histories"]["status"], "available")
        self.assertEqual(assess_trim_histories({path: statistics[:200]}, POLICY, [])["histories"]["status"], "inconsistent")

    def test_exhausted_events_rejected(self: TestTRIMDialogs) -> None:
        """Reject short or malformed canonical event streams before launch; return None."""
        request = deepcopy(EXPLICIT_REQUEST)
        request["parameters"].update({"mode": "recoil-quick", "events": [{"id": "T0001", "atomic_number": 14, "energy_eV": "1000", "position_angstrom": ["100", "0", "0"], "direction_cosines": ["1", "0", "0"]}]})
        contents = render_trim_events(request)
        self.assertEqual(count_trim_event_records(contents), 1)
        inputs = ResolvedTRIMInputs({}, {}, request, {}, (), (("TRIM.exe", b"fixture"),))
        with patch("src.srim.trim.dialogs.hashlib.sha256") as digest:
            digest.return_value.hexdigest.return_value = ENGINES["2013"]
            with self.assertRaises(ValueError):
                validate_trim_native_request(inputs, total_ions=2, event_contents=contents)
        for bad in (contents[:-1], contents.replace(b"\r\n", b"\n"), contents.replace(b"1000", b"NaN")):
            with self.assertRaises(ValueError):
                count_trim_event_records(bad)

    def test_known_engine_quoted_names(self: TestTRIMDialogs) -> None:
        """Reject known doubled-quote calculation and layer names; return None."""
        for field in ("calculation", "layer"):
            request = deepcopy(EXPLICIT_REQUEST)
            if field == "calculation":
                request["parameters"]["name"] = 'quoted "name"'
            else:
                request["parameters"]["layers"][0]["name"] = 'quoted "layer"'
            inputs = ResolvedTRIMInputs({}, {}, request, {}, (), (("TRIM.exe", b"fixture"),))
            with patch("src.srim.trim.dialogs.hashlib.sha256") as digest:
                digest.return_value.hexdigest.return_value = ENGINES["2013"]
                with self.assertRaisesRegex(ValueError, "doubled-quote"):
                    validate_trim_native_request(inputs, total_ions=1, event_contents=None)
