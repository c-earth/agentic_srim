# Shared checkpoint-first TRIM continuation

Status: explicitly approved by the user; implementation and the bounded verification plan authorized. Supersedes the unapproved event-only autosave proposal. No implementation or additional native run is authorized by this document alone.

The subsequently approved [2008 event-prompt amendment](TRIM_2008_EVENT_PROMPT_AMENDMENT.md) corrects the completion selector for resumed 2008 input mode 5 and authorizes two additional new children after the original twelve-case verification. Its exact observed Yes/No prompt is inspection-only; it never grants event Save responses.

## Evidence and intended behavior

The twelve no-response probes in `.cache/trim-sav-live/no-save-20261022T115211Z-dc08d244/report.json` tested ion-full and specified-ion-full on 2008, Standard and Pro, continuing 100 to 200. All six autosave-200 cases had recoverable final checkpoints before any acknowledgment. All six autosave-10000 cases lacked the final checkpoint; their restore source still contained 100 ions. Config files remained byte-identical without responses. Parents, installations, settings and event inputs were unchanged.

New automatic continuation first inspects the completed checkpoint while the exact native completion dialog is open. It sends no completion or Save response when a valid final checkpoint is ready. If that checkpoint remains unavailable, ordinary continuation may use its already verified explicit-save route. Event continuation must fail with a clear final-checkpoint requirement rather than entering the observed chooser path. Never change requested autosave settings silently. Fresh TRIM, SR/CAB, offline recovery, ordinary explicit-save qualification and unrelated local edits remain unchanged.

This behavior applies to explicit automatic policies (`cascade_output` non-None). `None` retains report-only popup failure behavior; it does not silently become automatic success. Natural clean exit with a valid frozen final checkpoint remains a supported completion path.

## Proposed files and responsibilities

| Path | Purpose, contents, dependencies and relationships |
| --- | --- |
| `src/srim/trim_dialogs.py` | Seal policy schema 4; strictly recognize completion as an inspection condition; retain ordinary save only as a controller-authorized fallback; initialize the state fields below. Existing engine/mode/cascade validation remains. |
| `src/srim/trim_native.py` | Observe completion without responding; classify/probe saved state; implement bounded initial inspection, one ordinary fallback and post-save inspection; stop and verify the owned job. Depends on existing checkpoint parser/recovery, safe snapshot reader, dialog classifier and Windows job APIs. |
| `src/srim/trim_execution.py` | Validate the two checkpoint-stop evidence variants and natural exit; report unavailable-checkpoint failure; publish new resumed execution schema while retaining frozen count/request/hash/config/history checks. |
| `test/srim/test_trim_dialogs.py` | Completion inspection, mode/policy/stage restrictions, fallback authorization and schema-boundary tests. |
| `test/srim/test_trim_native.py` | Current-prompt ownership, structured probe outcomes, two samples, deadlines, no-response stop, one-shot save fallback and priority/cleanup tests. |
| `test/srim/test_trim_execution.py` | Real preservation and mocked native attempts exercising terminal success/failure, evidence validation, schema compatibility and unchanged integrity/history behavior. |
| `test/srim/test_trim_runtime.py` | New sealed-policy replay and rejection of older prepared automated policies. Parent/event-prefix preparation remains unchanged. |
| `README.md`, `doc/guides/trim-execution.md`, `doc/reference/trim-execution-format.md`, `doc/TRIM_DIRECTION.md` | Document the shared decision rule, exact schemas, preparation compatibility, fallback limits and measured checks. |
| `doc/TRIM_EVENT_AUTOSAVE_AMENDMENT.md` | Mark superseded without deleting its historical design/evidence. |

No new production module or class. No recovery-math, native-text parser, config-assessment, queue, public configuration or shared metadata changes. No chooser/export selector, directory reader or keyboard/focus action. Auxiliary harnesses/evidence stay under `.cache/trim-sav-live/`. Do not alter installed engines, settings, existing records/fixtures or unrelated edits; no commits or cleanup are included.

## Policy, state and compatibility

New automated policies have nested schema_version 4, with exactly the existing schema-3 fields and timing values: response_transition_seconds 2.0, checkpoint_save_seconds 10.0, checkpoint_stable_seconds 1.0, shutdown_seconds 10.0. No new user-facing setting. Outer automated control remains schema 2. Nested versions 1/2/3 are rejected with an instruction to prepare a new runtime; historical records are never rewritten. Report-only controls remain schema 1.

Initial state retains `{stage, pending_sequence, expected_count, limitations, checkpoint_probe, checkpoint_save_started}` and adds:

```
checkpoint_completion: null | {
    process_id: int, window: <full native completion observation>,
    elapsed_seconds: finite nonnegative number
}
checkpoint_started: null | finite nonnegative number
checkpoint_fallback_attempted: false | true
```

New stage `checkpoint-check` means inspecting final saved state while completion is still open. `checkpoint-save-required` authorizes one ordinary completion/Yes response only after the initial unavailable-checkpoint deadline. Existing `resume-save` and `checkpoint-saved` stages retain their ordinary fallback meanings. Resumed event completion no longer acknowledges into `resume-event-save` or presses its save form. Generic `done` never authorizes forced stopping.

## Typed functions and detailed behavior

### `build_trim_dialog_policy(inputs: ResolvedTRIMInputs, control: JSONObject, cascade_output: str | None) -> JSONObject | None`

Signature and pure inputs/output/error contracts stay unchanged. `None` returns None. Explicit choices retain existing pinned-engine, mode, collision, count and autosave validation. Return the same policy keys with nested version 4. No filesystem or native effects.

### `initial_trim_dialog_state(policy: JSONObject) -> JSONObject`

Validate exact version-4 policy keys/types/constants and return a new state with the extra fields above, initially null/null/false. Reject legacy or malformed policies with ValueError. No I/O or caller mutation.

### `classify_trim_dialog(window: JSONObject, policy: JSONObject, state: JSONObject) -> JSONObject`

Keep pure classification, strict policy validation, ownership/control/text requirements and pending-command suppression. In opted-in resume, recognize these exact completion messages only at running/non-recoil or the appropriate post-cascade resume-complete stage, then at checkpoint-check:

- Ordinary native modes 1/2/3: `End of calculation`, expected-count completion text with Yes=Save / No=Increase, exactly the existing Yes/No buttons and IDs.
- Event native modes outside 1/2/3: `End of TRIM.DAT calculation`, expected-count completion text, exactly the existing OK button/ID.

Return `{decision: "inspect-checkpoint", reason: null, next_stage: "checkpoint-check", command: null}`. Never post these responses in the initial inspection path. A different count, mode, owner, capture error, changed text/controls or unrelated popup remains rejected.

Only an ordinary policy at checkpoint-save-required with checkpoint_fallback_attempted true can classify that same completion dialog as the existing Yes response into resume-save. Reuse the exact three-button Form1 default-save selector only at ordinary resume-save, posting button 3 once and transitioning to pending checkpoint-saved. Event policies never accept that fallback or save form. Fresh cascade/event handling remains unchanged. No selector is added for a chooser.

### `advance_trim_dialog_state(state: JSONObject, observation: JSONObject, action_result: JSONObject | None) -> JSONObject`

Signature and pure copying/pending/duplicate error contracts remain. Preserve the added state fields through existing transitions. Never infer a save from checkpoint inspection, and never reset the initial deadline merely because the same prompt is observed again.

### `_read_trim_checkpoint_snapshot(runtime: Path) -> dict[str, bytes]`

Unchanged signature and implementation: absolute canonical runtime, flat safe full saved-file set, matching before/after inventories, no source mutation. Missing paths raise FileNotFoundError, changing reads raise the existing specific ValueError, unsafe/other I/O errors propagate. Stable reads remain an atomicity limitation.

### `_probe_trim_saved_checkpoint(runtime: Path, policy: JSONObject, previous: JSONObject | None, elapsed_seconds: float) -> JSONObject`

Keep inputs, argument validation, no-write contract and current candidate `{elapsed_seconds, sources}`. Read the complete flat checkpoint and use the existing checkpoint parser and recovery API. Require exact version, semantic/input mode, requested total/autosave and valid counts. Return exactly:

```
ready: bool
status: "ready" | "waiting" | "unavailable" | "invalid"
candidate: null | {elapsed_seconds: number, sources: list}
sources: <existing source hash/size list>
last_error: null | str
```

- Missing directory/required files, or a well-formed identity-matching checkpoint whose current count is below the requested final count, is unavailable. Reset candidate. Missing fields are not treated as zero.
- A valid final recovery with only one sample, a changed candidate inventory, or the snapshot reader's specifically identified changed-during-read condition is waiting. Preserve/restart the candidate as appropriate, without resetting the phase deadline.
- Matching valid full snapshots at least 1.0 seconds apart are ready. Keep all source files in the hash comparison.
- Unsafe paths, malformed records/arrays, unsupported geometry/version, wrong total/autosave/mode, current count beyond expected, contradictory identities and other unexpected read/recovery failures are invalid. They never authorize saving as a repair. Preserve the actual reason. The specific transient changed-during-read error is the only ValueError converted to waiting; other snapshot ValueErrors are invalid.

If textual metadata is available, validate it before labeling a missing array set unavailable. Invalid method arguments still raise ValueError; expected checkpoint-read/recovery failures become the structured outcomes. No warnings, precision bounds, normalized values or recovery algorithms change.

### `_observe_trim_dialogs(process_id: int, runtime: Path, policy: JSONObject, state: JSONObject, elapsed_seconds: float, *, startup_state: dict[tuple[int, int], float] | None = None) -> JSONObject`

Keep existing owned-PID observation, startup filtering, pending-transition verification and at-most-one-command behavior. On inspect-checkpoint, copy the first completion observation into checkpoint_completion, set stage checkpoint-check and checkpoint_started once, and return an extra `checkpoint_prompt` with the current full observation. Revalidate the same prompt/identity on subsequent loops. Append no command event for observation alone. Missing or changed prompts cannot authorize forced stopping from stale evidence. Return existing state/events/unhandled/errors, plus checkpoint_prompt only when currently verified.

For the ordinary fallback, retain posted Yes/default-save evidence. Once Yes is queued, start a bounded ten-second wait for the save form; keep the existing two-second response-transition bound for the pending Yes command. Default-save pending transition retains its ten-second bound. After its verified disappearance, initialize checkpoint_save_started for the distinct ten-second post-save probe phase. Do not retry a failed command.

### `execute_trim_process(executable: Path, runtime: Path, *, hide_window: bool, timeout_seconds: float | None, stdout_path: Path, stderr_path: Path, cancel: Callable[[], bool] | None = None, dialog_policy: JSONObject | None = None) -> JSONObject`

Signature, diagnostics, suspended launch/job assignment and absolute/link-free path requirements remain. Monitor all current owned windows before acting; reset the local current prompt each iteration. Unknown popup, monitoring error, cancellation and overall timeout take priority, including rechecking cancellation/deadline after a potentially lengthy checkpoint probe. A clean natural empty-job exit takes the normal exit path and is validated offline.

Initial inspection lasts at most ten seconds from first verified completion observation:

1. If the current prompt is verified and the probe is ready before the deadline, stop the assigned job without any completion/Save response.
2. Invalid checkpoint state fails with monitor-error and its actual reason, without a save attempt.
3. At the deadline, only unavailable state on an ordinary mode authorizes the one-shot fallback: set checkpoint_fallback_attempted true and checkpoint-save-required, then reobserve/revalidate the prompt before posting Yes. No stale-window command is allowed.
4. Unavailable state on an event mode fails with termination checkpoint-unavailable and a message explaining that no final checkpoint exists and a final-covering autosave schedule is required. Do not acknowledge completion or press Save.
5. Unstable/changing state, a missing current prompt without clean exit, or failure to establish readiness within the deadline fails with checkpoint-unavailable and the specific readiness reason, without fallback. Do not silently overwrite an invalid/unstable checkpoint.

Ordinary fallback must complete its verified Yes/default-save sequence and obtain a ready checkpoint within the separate ten-second post-save phase. A save-form wait or post-save readiness deadline produces checkpoint-unavailable with a phase-specific reason; invalid post-save state produces monitor-error. It may not reenter fallback or invoke any chooser. Unknown dialogs remain failures. Timeouts/cancellation are never converted to checkpoint success. `timeout_seconds=None` does not disable phase/transition/shutdown bounds. Requested autosave values and checkpoint bytes are never changed by this decision logic.

Use existing checked owned-job termination, empty-job verification, actual exit code and ten-second shutdown bound. Both successful controlled stops have termination checkpoint-stop and the evidence schema below. Cleanup/quiescence failures retain the existing unsafe-freeze exception behavior and never publish a supposedly frozen successful checkpoint.

### `_trim_completion(inputs: ResolvedTRIMInputs, process_result: JSONObject, parsed: JSONObject, saved_values: JSONObject, continuation: JSONObject | None, *, availability: JSONObject | None = None) -> JSONObject`

Keep pure `{complete, reason, checks}`, malformed-argument validation and independent count/request/history checks. Accept either a clean natural owned exit code 0 with complete frozen final results and no popup/monitor/pending failure, or a controlled stop with the exact new evidence and matching frozen sources. For a no-response stop, validate the recorded completion prompt through the strict classifier using its sealed version-4 policy and checkpoint-check state; require no completion or Save command events. Earlier verified cascade-choice events may exist. For explicit-save origin require ordinary mode and ordered, verified completion/Yes then default-save events, no errors or pending sequence, and final checkpoint-saved state. Wrong mode, missing/mutated prompt, unsupported origin, contradictory save flag, reordered commands or mismatched live/frozen hashes fail completion.

### `run_trim_simulation(paths: ProjectPaths, config: Config, simulation_id: str, runtime: Path, *, timeout_seconds: float | None = 60.0, cancel: Callable[[], bool] | None = None) -> JSONObject`

Keep lease, preflight, full preservation, recovered source selection, config qualification, original-baseline validation, request comparison and terminal publication. New resumed reports use schema 4 as below. Map process checkpoint-unavailable to failed code checkpoint-required, preserving a clear reason from the native report. Native report includes `checkpoint_failure: null | {status, phase, message}` for structured probe/deadline failures; use this message rather than a misleading launch or parse error. Integrity failures and unknown popups remain independent failures. Do not rewrite any old failed or successful record.

## Report schemas

New resumed `execution.json` uses schema_version 4 with the same top-level fields as resumed schema 3. Fresh execution remains unchanged. Result schema 2/recovery v2, config sidecar v1, saved-values and availability are unchanged. Existing inventories seal any changed evidence normally.

New opted-in resume process reports have existing fields plus checkpoint_failure, null or exactly `{status, phase, message}`. Status is unavailable/waiting/invalid; phase is initial/save-transition/post-save; message is a nonempty string. This records structured checkpoint/readiness failures, not unrelated cancellation or unknown-popup failures. checkpoint_stop is null or exactly:

```
origin: "completed-checkpoint" | "explicit-save"
completion_observed: true
completion_prompt: <first full verified owned native completion observation>
save_verified: bool
stable_seconds: finite number >= 1.0
sources: <sorted flat checkpoint path/hash/size inventory>
job_empty: bool
```

For completed-checkpoint origin, save_verified is false and completion/save commands are absent. For explicit-save origin it is true and the ordinary save sequence must be verified. Preserve actual process exit code; do not label a job termination as clean exit. Native failures retain their failure termination and checkpoint_failure where applicable; they do not gain success from a usable checkpoint. Natural exit has checkpoint_stop null. Recorded prompt alone never establishes saved results: recovery, exact counts, protected config/input checks and live/permanent hashes are all required.

No historical migration. Readers/documentation must distinguish resumed report schema 4 from older schema 3; the existing availability reader does not change because its sidecar schema stays the same. Existing automatic prepared policy versions need new preparation, while their terminal evidence remains readable as historical evidence.

## Verification

Every affected code file/function/class keeps required docstrings and complete annotations, including test self; no implementation comments. New unittest methods have `(self: <test class>) -> None`, no additional arguments, cache-only I/O and mocked Windows APIs. No new production helper signature is planned beyond the existing functions listed above.

- Pure/dialog tests: exact completion recognition for each mode/version; no response during initial inspection; wrong count/control/owner/mode/phase rejected; ordinary fallback only after controller authorization; no event fallback/save/chooser; pending suppression; old-policy rejection and new replay; fresh cascade/completion behavior unchanged.
- Probe tests: unavailable versus invalid versus changing; metadata validation before missing-file qualification; count/total/autosave/mode errors; full source hashing; two valid samples a second apart; reset on changes; no changed recovery math/precision.
- Native tests: current prompt and job membership required; zero completion/Save commands for ready checkpoints; exactly one ordinary save sequence for unavailable checkpoints; event missing-checkpoint failure with zero responses; deadline equality/boundaries, disappearing prompt, no retry, no stale evidence; post-probe cancel/timeout precedence; root/descendant cleanup and frozen-copy requirements.
- Execution tests: both origin variants, natural clean exit, malformed/contradictory evidence, output inventory sealing, structured failure/schema fields, config/history protections and event-prefix preservation. Historical report versions are not mutated.
- Run all TRIM regressions with warnings as errors through `D:\projects\agentic_srim\.venv\Scripts\python.exe -W error -m unittest discover -s test/srim -p 'test_trim*.py' -q`; inspect diff, documentation, annotations/docstrings and absence of abandoned chooser/export code.

After offline checks pass, perform at most twelve sequential native continuations from the six already preserved 100-ion/event parents in the no-response investigation, using new child records and sixty-second attempt bounds. No additional fresh native parent is needed. Check each installation/mode with autosave 200 and 10000:

| Case | Expected new production outcome |
| --- | --- |
| All six final-autosave cases | Success, completed-checkpoint origin, zero completion/Save responses, exact count/recovery/live hashes; configs unchanged. |
| Three ordinary autosave-10000 cases | Success through one verified explicit-save fallback, exact final count/recovery/live hashes; only validated config qualification allowed. |
| Three event autosave-10000 cases | Expected failed/checkpoint-required after bounded inspection, zero responses, no chooser; original request/autosave/event prefix preserved. |

Verify source installations/settings/parent records unchanged and retain full evidence. Do not retry or broaden failed cases, override metadata or claim universal mode coverage. These live checks still do not certify atomic binary saves or independently validated physics. Consult the repository self-improvement skill at completion without starting an unapproved workflow trial.

## Approval request

Approve this complete shared design, including the ten-second checkpoint-first inspection, one-shot ordinary fallback, event checkpoint-required failure when final autosave is absent, policy/report version boundary and twelve-continuation live verification budget. No settings will be silently adjusted. Implementation starts only after explicit approval; any design change needs another amendment.
