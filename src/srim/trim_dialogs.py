"""Validate sealed TRIM response policies and classify reviewed native evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import hashlib
import re

from src.srim.metadata import JSONObject
from src.srim.trim_inputs import MODES
from src.srim.trim_resolution import ResolvedTRIMInputs


ENGINES = {"2008": "759e9ebb974cfbef82f8f5d7754a8d54ebc77ba96d36243bd279ad0b93a8c000", "2013": "8b8b910615426b38d6c9898f1c45ac0f376192283970d006dd3c170ce81affe2"}
SPECIAL = "TRIM: Special for Neutron/Electron Damage"
CASCADE = "Would you like a file of each Cascade ? [Y] This activates the file SRIM Outputs\\COLLISON.txt and stores calculation results for EVERY cascade."
ACTIVE = "File SRIMOutput\\COLLISON.txt now active."
DETAIL = "Would you like every collision filed? [Yes] º This stores EVERY COLLISION in COLLISON.txt as º well as the statistics of each cascade."
DETAIL_ACTIVE = "Every Collision will be filed."
KP = "No cascade details available with KP Damage"


def normalize_trim_dialog_text(text: str) -> str:
    """Return whitespace-normalized text without effects; reject nonstrings."""
    if not isinstance(text, str):
        raise ValueError("Expected dialog text")
    return " ".join(text.split())


def count_trim_event_records(contents: bytes) -> int:
    """Validate canonical contents and return its event count without effects.

    Require ten headers, CRLF, ASCII, eight finite numbers per ID and EOF.
    Raise ValueError for malformed bytes, records or numeric fields.
    """
    if not isinstance(contents, bytes) or not contents.endswith(b"\x1a"):
        raise ValueError("TRIM.DAT: missing canonical EOF")
    try:
        text = contents[:-1].decode("ascii")
        if text.replace("\r\n", "").find("\n") >= 0 or "\r" in text.replace("\r\n", "") or not text.endswith("\r\n"):
            raise ValueError("TRIM.DAT: expected CRLF")
        lines = text[:-2].split("\r\n")
        if len(lines) <= 10 or any(not line or any(ord(c) < 32 for c in line) for line in lines):
            raise ValueError("TRIM.DAT: incomplete headers/records")
        for line in lines[10:]:
            tokens = line.split()
            if len(tokens) != 9 or re.fullmatch(r"[!-~]{1,5}", tokens[0]) is None or any(not Decimal(token).is_finite() for token in tokens[1:]):
                raise ValueError("TRIM.DAT: malformed event")
    except (UnicodeError, InvalidOperation) as error:
        raise ValueError("TRIM.DAT: invalid ASCII/numeric record") from error
    return len(lines) - 10


def validate_trim_native_request(inputs: ResolvedTRIMInputs, *, total_ions: int, event_contents: bytes | None) -> None:
    """Validate inputs' known native restrictions against total_ions and events.

    Read pinned bytes only; return None. Raise ValueError for affected quoted
    names or insufficient canonical events, without changing requests or files.
    """
    digest = hashlib.sha256(dict(inputs.engine_files)["TRIM.exe"]).hexdigest()
    p = inputs.effective_request["parameters"]
    if digest == ENGINES["2013"]:
        names = [("parameters.name", p["name"]), *((f"parameters.layers[{i}].name", layer["name"]) for i, layer in enumerate(p["layers"]))]
        for field, name in names:
            if '"' in name:
                raise ValueError(f"{field}: tested doubled-quote encoding unsupported by engine {digest}")
    if digest in ENGINES.values() and not p["mode"].startswith("ion-"):
        if event_contents is None or count_trim_event_records(event_contents) < total_ions:
            raise ValueError("TRIM.DAT: fewer complete events than requested absolute total")


def build_trim_dialog_policy(inputs: ResolvedTRIMInputs, control: JSONObject, cascade_output: str | None) -> JSONObject | None:
    """Return a sealed policy for inputs/control/choice without changing them.

    None preserves report-only behavior. Raise ValueError for unknown engines,
    modes, choices, conflicting collision flags or invalid target counts.
    Continuation uses verified checkpoint saving and recovery in the pipeline.
    """
    if cascade_output is None:
        return None
    if cascade_output not in ("none", "statistics", "detailed"):
        raise ValueError("cascade_output must be none, statistics, detailed or None")
    p = inputs.effective_request["parameters"]
    digest = hashlib.sha256(dict(inputs.engine_files)["TRIM.exe"]).hexdigest()
    family = p["engine_family"]
    if digest != ENGINES.get(family):
        raise ValueError("Automatic TRIM responses require a verified executable")
    recoil = p["mode"] in ("recoil-quick", "recoil-full")
    if not recoil and cascade_output != "none":
        raise ValueError("Cascade histories require recoil-quick or recoil-full")
    if recoil and p["outputs"]["collisions"] != "none":
        raise ValueError("cascade_output conflicts with outputs.collisions")
    if control["kind"] not in ("fresh", "resume") or type(control["initial_count"]) is not int or type(control["total_ions"]) is not int or not 0 <= control["initial_count"] < control["total_ions"]:
        raise ValueError("Invalid TRIM response counts/kind")
    if type(control.get("autosave_ions")) is not int:
        raise ValueError("Invalid TRIM autosave count")
    return {"schema_version": 4, "engine_sha256": digest, "engine_family": family, "input_mode": MODES[family][p["mode"]], "kind": control["kind"], "initial_count": control["initial_count"], "expected_count": control["total_ions"], "autosave_ions": control["autosave_ions"], "cascade_output": cascade_output, "checkpoint_directory": "SRIM Restore", "ascii_directory": "SRIM Outputs", "response_transition_seconds": 2.0, "checkpoint_save_seconds": 10.0, "checkpoint_stable_seconds": 1.0, "shutdown_seconds": 10.0}


def initial_trim_dialog_state(policy: JSONObject) -> JSONObject:
    """Validate policy and return independent initial state without effects.

    Raise ValueError for unsupported or legacy schema, engine, selectors or controls.
    Resume policies distinguish verified checkpoint saves from generic completion.
    """
    if isinstance(policy, dict) and policy.get("schema_version") in (1, 2, 3):
        raise ValueError("Legacy dialog policies are unsupported; prepare a new runtime")
    keys = {"schema_version", "engine_sha256", "engine_family", "input_mode", "kind", "initial_count", "expected_count", "autosave_ions", "cascade_output", "checkpoint_directory", "ascii_directory", "response_transition_seconds", "checkpoint_save_seconds", "checkpoint_stable_seconds", "shutdown_seconds"}
    if not isinstance(policy, dict) or set(policy) != keys or type(policy["schema_version"]) is not int or policy["schema_version"] != 4 or policy["engine_family"] not in ENGINES or policy["engine_sha256"] != ENGINES.get(policy["engine_family"]) or type(policy["input_mode"]) is not int or policy["input_mode"] not in MODES[policy["engine_family"]].values() or policy["kind"] not in ("fresh", "resume") or policy["cascade_output"] not in ("none", "statistics", "detailed") or type(policy["initial_count"]) is not int or type(policy["expected_count"]) is not int or type(policy["autosave_ions"]) is not int or not 0 <= policy["initial_count"] < policy["expected_count"] or policy["checkpoint_directory"] != "SRIM Restore" or policy["ascii_directory"] != "SRIM Outputs" or any(type(policy[key]) is not float or policy[key] != value for key, value in (("response_transition_seconds", 2.0), ("checkpoint_save_seconds", 10.0), ("checkpoint_stable_seconds", 1.0), ("shutdown_seconds", 10.0))):
        raise ValueError("Invalid TRIM dialog policy")
    return {"stage": "running", "pending_sequence": None, "expected_count": policy["expected_count"], "limitations": [], "checkpoint_probe": None, "checkpoint_save_started": None, "checkpoint_completion": None, "checkpoint_started": None, "checkpoint_fallback_attempted": False}


def classify_trim_dialog(window: JSONObject, policy: JSONObject, state: JSONObject) -> JSONObject:
    """Classify one window under policy/state, returning decision and command.

    Perform no effects. Unknown observations return reject; malformed policy
    raises ValueError. A pending command always waits and is never duplicated.
    """
    initial_trim_dialog_state(policy)
    reject: JSONObject = {"decision": "reject", "reason": "Unrecognized TRIM dialog or sequence", "next_stage": state["stage"], "command": None}
    if state["pending_sequence"] is not None:
        return {**reject, "decision": "wait", "reason": None}
    if window.get("capture_errors") or not window.get("visible") or not window.get("enabled") or type(window.get("pid")) is not int or window["pid"] <= 0 or type(window.get("handle")) is not int or window["handle"] <= 0:
        return reject
    stage = state["stage"]
    count = policy["expected_count"]
    recoil = policy["input_mode"] in ((6, 7) if policy["engine_family"] == "2008" else (6, 8))
    completion = "resume-complete" if policy["kind"] == "resume" else "event-complete"
    choice = policy["cascade_output"]
    if stage == "resume-save" and policy["kind"] == "resume" and policy["input_mode"] in (1, 2, 3) and state.get("checkpoint_fallback_attempted") is True and window.get("class") == "ThunderRT5Form" and window.get("title") == "Form1":
        controls = window.get("controls", [])
        expected_save = {1: "Continue without Saving TRIM", 2: "Store in another Directory / Disk", 3: "Store in SRIM directory (default)"}
        if len(controls) == 3 and all(c.get("class") == "ThunderRT5CommandButton" and c.get("text") == expected_save.get(c.get("control_id")) and c.get("pid") == window.get("pid") and c.get("enabled") and c.get("visible") for c in controls) and {c["control_id"] for c in controls} == set(expected_save):
            button = next(c for c in controls if c["control_id"] == 3)
            return {"decision": "respond", "reason": None, "next_stage": "checkpoint-saved", "command": {"action": "button", "target_handle": button["handle"], "control_id": 3, "response": expected_save[3]}}
        return reject
    rules = []
    if stage == "running" and recoil:
        rules.append((SPECIAL, CASCADE, {"Yes", "No"}, "No" if choice == "none" else "Yes", completion if choice == "none" else "cascade-active"))
    if stage == "cascade-active":
        rules.append((SPECIAL, ACTIVE, {"OK"}, "OK", "detail-choice"))
    if stage == "detail-choice":
        rules.append((SPECIAL, DETAIL, {"Yes", "No"}, "Yes" if choice == "detailed" else "No", "detail-active" if choice == "detailed" else completion))
        if policy["input_mode"] == 6:
            rules.append(("TRIM", KP, {"OK"}, "OK", completion))
    if stage == "detail-active":
        rules.append((SPECIAL, DETAIL_ACTIVE, {"OK"}, "OK", completion))
    if stage == completion or stage == "running" and not recoil or policy["kind"] == "resume" and stage in ("checkpoint-check", "checkpoint-save-required"):
        if (policy["kind"] == "fresh" or policy["input_mode"] not in (1, 2, 3)) and not (policy["kind"] == "resume" and policy["engine_family"] == "2008" and policy["input_mode"] == 5):
            if stage != "checkpoint-save-required":
                rules.append(("End of TRIM.DAT calculation", f"Calculation Complete ({count} Ions)", {"OK"}, "OK", "checkpoint-check" if policy["kind"] == "resume" else "done"))
        if policy["kind"] == "resume" and (policy["input_mode"] in (1, 2, 3) or policy["engine_family"] == "2008" and policy["input_mode"] == 5):
            if stage != "checkpoint-save-required" or policy["input_mode"] in (1, 2, 3) and state.get("checkpoint_fallback_attempted") is True:
                rules.append(("End of calculation", f"Calculation Complete ({count} Ions) Yes = Save data No = Increase number of ions", {"Yes", "No"}, "Yes", "resume-save" if stage == "checkpoint-save-required" else "checkpoint-check"))
    if window.get("class") != "#32770" or any(c.get("class") not in ("Static", "Button") for c in window.get("controls", [])):
        return reject
    message = normalize_trim_dialog_text(" ".join(c["text"] for c in window["controls"] if c["class"] == "Static"))
    buttons = [c for c in window["controls"] if c["class"] == "Button"]
    labels = [normalize_trim_dialog_text(c["text"].replace("&", "")) for c in buttons]
    for title, text, expected, response, next_stage in rules:
        if window["title"] == title and message == normalize_trim_dialog_text(text) and len(labels) == len(expected) and set(labels) == expected:
            target = buttons[labels.index(response)]
            control_id = {"Yes": 6, "No": 7, "OK": 2}[response]
            if target.get("control_id") != control_id or not target.get("enabled") or not target.get("visible") or target.get("pid") != window.get("pid"):
                return reject
            if any(c.get("pid") != window.get("pid") or not c.get("enabled") or not c.get("visible") or c.get("control_id") != {"Yes": 6, "No": 7, "OK": 2}[label] for c, label in zip(buttons, labels)):
                return reject
            if next_stage == "checkpoint-check":
                return {"decision": "inspect-checkpoint", "reason": None, "next_stage": next_stage, "command": None}
            return {"decision": "respond", "reason": None, "next_stage": next_stage, "command": {"action": "button", "target_handle": target["handle"], "control_id": control_id, "response": response}}
    return reject


def advance_trim_dialog_state(state: JSONObject, observation: JSONObject, action_result: JSONObject | None) -> JSONObject:
    """Return copied state after observation and optional queued action_result.

    No effects or retries. Raise ValueError for duplicate or unqueued actions;
    verified observations clear pending sequence and retain limitation evidence.
    """
    result = deepcopy(state)
    if action_result is not None:
        if result["pending_sequence"] is not None or action_result.get("queued") is not True:
            raise ValueError("TRIM command was duplicated or not queued")
        result["stage"] = action_result["stage_after"]
        result["pending_sequence"] = action_result["sequence"]
        if any(c.get("text") == KP for c in action_result["window"].get("controls", [])):
            result["limitations"].append("native-kp-no-trajectories")
    elif observation.get("transition") == "verified":
        result["pending_sequence"] = None
    elif observation.get("transition") == "failed":
        result["stage"] = "failed"
    return result


def assess_trim_histories(files: Mapping[str, bytes], policy: JSONObject, events: Sequence[JSONObject]) -> JSONObject:
    """Return output availability from stopped files, policy and response events.

    Validate policy, inspect exact native structure and count coverage, and
    retain native history qualifications. No I/O or mutation; malformed caller
    arguments raise ValueError, missing native data returns an evidenced status.
    """
    initial_trim_dialog_state(policy)
    requested = policy["cascade_output"]
    path = "SRIM Outputs/COLLISON.txt"
    matches = [name for name in files if name.casefold() == path.casefold()]
    evidence: list[JSONObject] = []
    limitations: list[JSONObject] = []
    status = "not-requested" if requested == "none" else "missing"
    if matches:
        data = files[matches[0]]
        text = data.decode("cp1252")
        evidence.append({"path": matches[0], "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)})
        counts = [int(n) for n in re.findall(r"For Ion\s+(\d+):", text)]
        statistics = bool(data) and counts == list(range(1, policy["expected_count"] + 1)) and "For All Ions to date" in text and "Average Vacancies/Ion" in text
        details = "Start of New Cascade" in text and "Recoil Atom Energy(eV)" in text
        if requested != "none":
            status = "available" if statistics and (requested == "statistics" or details) else "inconsistent"
            quick = policy["input_mode"] == (6 if policy["engine_family"] == "2008" else 8)
            kp = any(any(c.get("text") == KP for c in e.get("window", {}).get("controls", [])) and e.get("queued") and e.get("transition") == "verified" for e in events)
            if requested == "detailed" and statistics and not details and (quick or policy["engine_family"] == "2013" and policy["input_mode"] == 6 and kp):
                code = "native-kp-no-trajectories" if quick else "native-2013-full-detail-unavailable"
                status = "native-unavailable"
                limitations.append({"code": code, "message": "Verified native engine supplied cascade statistics without detailed recoil trajectories.", "requested": requested, "observed": "statistics", "evidence": deepcopy(evidence)})
    return {"schema_version": 1, "engine": "trim", "cascade_output": requested, "core_verified": False, "histories": {"path": path, "requested": requested, "status": status, "evidence": evidence}, "limitations": limitations}
