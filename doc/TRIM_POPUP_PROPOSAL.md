# TRIM popup responses, output availability and parser corrections

> **Historical proposal; resume-export branch withdrawn on 2026-10-22.** The current decision is [TRIM continuation direction](TRIM_DIRECTION.md). Do not implement or resume the F-button, chooser, export-directory or 2008 export-mapping work below. The user selected `.sav` recovery and requested this detour be abandoned. Fresh cascade and checkpoint-save handling remain relevant; their old approval does not authorize restarting GUI result exports. The old status and approval language below are historical, not current next steps.

The [approved export cleanup](TRIM_EXPORT_CLEANUP_PROPOSAL.md) removes the old export selectors/state and introduces nested policy version 2. Automatic continuation is guarded pending `.sav` integration. The old policy/state and export acceptance requirements below are superseded, not outstanding implementation tasks.

Status: complete implementation proposal for user review; implementation is not approved yet. This document proposes code changes but does not authorize them. The standalone diagnostic under `.cache/popup-review/native-response-proof/` is separate from production.

## Objective and confirmed decisions

Allow explicitly prepared TRIM runs to answer the reviewed native questions without Computer Use, while retaining owned-process isolation, sealed inputs, complete raw evidence, strict core-result verification and the existing created/running/succeeded-or-failed lifecycle.

The user confirmed TRIM-only implementation; TIN policies are documentation only. Reject the verified failing embedded-quote encoding before launch on the verified affected 2013 executable. Completed runs with valid core results may succeed when a verified native design limitation prevents requested detailed histories, but the unavailable output and limitation must be explicit. Missing or inconsistent core results, native runtime errors, substitutions, timeouts, unsafe observations and unknown dialogs remain failures. A known limitation never authorizes changing physical inputs, inventing events, replacing values with zero or claiming unavailable histories were produced.

For TIN, documented policies are: accept material loading only for the intended material and layer; retain the previously approved exact chemistry response; accept the density warning only when displayed layer, gas phase and density match the explicit request; otherwise stop without substituting a density. The proposal does not launch TIN or add a TIN service, GUI workflow or dependency.

## Evidence and its boundaries

- The cascade matrix completed 35 isolated UI-assisted runs across 23 supported version/mode combinations, all saved 100/100. Collision flags 1 and 2 in 12 further report-only probes did not remove the first cascade question. Existing collision flags are therefore not treated as a verified replacement for the separate cascade choices.
- The standalone Python proof answered the Standard mode-8 statistics and detailed-request sequences through WM_COMMAND/BN_CLICKED, without Computer Use, screen capture, mouse, keyboard or user responses. Both exited 0 at 100/100. All eight core text files and COLLISON.txt match manually answered baselines byte-for-byte. Its strict overall report remains failed because the existing parser rejects a printed negative E2RECOIL value and misinterprets saved mode numbering.
- Resume review verified default checkpoint saving, alternate checkpoint saving, skipping the final explicit save, increasing the absolute total, cancellation, standard profile exports and normal Exit. This was UI-assisted, not yet a direct-message production acceptance test. The original checkpoint was preserved. Skipping the final save did not erase prior autosaves.
- Isolated Standard name tests: calculation-name quotes caused error 9; layer-name quotes caused error 5; a no-quotes control completed 5/5. Both failed processes nevertheless returned exit code 0 after their error was dismissed. The tested encoding doubles embedded quotes. No alternative encoding was verified.
- An exhausted one-event file with a two-event continuation target reproduced error 9 at saved 1/2. This is incomplete requested input/core execution, not a successful-core-output exception.

Local evidence indexes: `.cache/popup-review/mode-version-matrix/README.md`, `cascade-file-controls/README.md`, `native-response-proof/README.md`, `06-resume-save-or-increase/README.md`, `06b-resume-increase-ions/README.md`, `06c-resume-alternate-save/README.md`, `06d-resume-without-final-save/README.md`, `06e-resume-cancel-increase/README.md` and `08-name-isolation/README.md`. These paths are local removable evidence, not runtime dependencies.

Verified engine SHA-256 values, lowercase:

| Engine | SHA-256 |
| --- | --- |
| 2008 | `759e9ebb974cfbef82f8f5d7754a8d54ebc77ba96d36243bd279ad0b93a8c000` |
| 2013 Standard and Pro, byte-identical TRIM.exe | `8b8b910615426b38d6c9898f1c45ac0f376192283970d006dd3c170ce81affe2` |

Only these engines receive automated responses under this proposal. An unrecognized executable remains usable with legacy report-only execution; explicitly requesting automatic responses for it is rejected before staging. Supporting another binary requires evidence and a separately approved amendment.

## User-facing control and compatibility

Add one preparation keyword, `cascade_output: str | None = None`, to fresh and continuation preparation. No settings.toml key, high-level request-schema field, existing outputs.collisions value, batch task field or runtime file-format flag changes.

| Value | Behavior |
| --- | --- |
| `None` | Preserve existing report-only popup behavior and version-one execution controls. No automatic responses. |
| `"none"` | Enable reviewed automatic completion handling; answer No if the cascade-file question appears. |
| `"statistics"` | Enable reviewed handling; answer Yes to the cascade-file question and No to every-collision details when offered. |
| `"detailed"` | Enable reviewed handling; answer Yes to both when offered, retaining explicit native limitations. |

Values are exact, case-sensitive strings; booleans and other types are invalid. All non-None values opt into automatic handling, including `"none"`. Ordinary and specified-ion modes allow only None or `"none"`; their existing outputs.collisions flag retains its existing independent meaning. Recoil-monolayer allows None or `"none"` because no separate cascade-file interface was verified for that mode. Statistics/detailed are limited to recoil-quick and recoil-full.

For automatically handled recoil-quick/full runs, require existing outputs.collisions=`"none"`, including when cascade_output=`"none"`. The two controls can affect the same COLLISON.txt, and their combined semantics were not established. Reject the conflicting combination rather than silently overriding either. Legacy report-only runs retain their existing collision flags. Testing and supporting combined controls is outside this change.

This default preserves compatibility. A new caller explicitly selects `cascade_output="none"` for automatic core-only execution, or the desired history choice for supported recoil modes. Continuation callers explicitly select it again; it is not silently inherited. Batches use each prepared runtime's sealed policy; no global policy or per-batch override is introduced.

## Execution and data schemas

### Sealed controls

Existing `input/execution/control.json` schema version 1 stays byte-compatible. For an opted-in run, schema version 2 has the same keys plus exactly `dialog_policy`. Its policy object has these exact keys:

```text
schema_version: 1
engine_sha256: lowercase 64-character hex string
engine_family: "2008" | "2013"
input_mode: integer native TRIM.IN code
kind: "fresh" | "resume"
initial_count: nonnegative integer
expected_count: positive absolute requested final integer
cascade_output: "none" | "statistics" | "detailed"
checkpoint_directory: "SRIM Restore"
ascii_directory: "SRIM Outputs"
response_transition_seconds: 2.0
resume_export_order: ["RANGE.txt", "LATERAL.txt", "IONIZ.txt", "PHONON.txt", "E2RECOIL.txt", "damage"]
```

The `damage` action produces both VACANCY.txt and NOVAC.txt. Relative directory names are fixed, never caller-supplied arbitrary paths. The total native timeout remains the caller's existing positive finite timeout, including all UI handling and export work. The two-second bound limits progression after one queued command and is capped by remaining native time. No extra timeout extension or retry occurs.

Reconstruct the policy from pinned executable/input bytes, canonical requested choice and fresh/resume counts during verification. Reject edited keys, paths, hashes, timing or counts. JSON uses existing UTF-8, sorted keys, two-space indentation, finite values and terminal LF rules. Initial runtime files remain the existing exact native set; policy is sealed record evidence, not an undocumented TRIM input file.

### Native process evidence

Report-only native results keep their original keys. Opted-in results add `dialog_events` and `dialog_state` to `{pid, exit_code, elapsed_seconds, termination, popups, monitor_errors}`. `popups` retains unhandled observations; successfully handled observations are retained in dialog_events and do not themselves force failure. Termination values and owned-job cleanup stay unchanged.

Each dialog event has exact keys:

```text
sequence: positive integer
elapsed_seconds: nonnegative finite float from native start
process_id: positive integer
window: captured native window object
stage_before: string from the state catalogue below
stage_after: string from the state catalogue below
action: "button" | "menu-exit"
target_handle: positive integer
control_id: integer
response: observed button label or "Exit"
message: "WM_COMMAND/BN_CLICKED" | "WM_COMMAND/menu"
queued: boolean
transition: "pending" | "verified" | "failed"
error: null or diagnostic string
```

Preserve raw title/control text and normalized comparison text in the captured window object. Child control evidence is extended with `control_id`, `enabled`, `visible`, `parent_handle` and `pid`; keep existing handle/class/text. Revalidate before each action. dialog_state is `{stage, pending_sequence, expected_count, completed_exports, limitations}`, with pending_sequence null or an event sequence. No process-global mutable state is used.

### Output availability

Write `output/parsed/availability.json` for opted-in runs, including failed attempts. Exact top-level keys: `{schema_version, engine, cascade_output, core_verified, histories, limitations}` with schema_version 1 and engine `"trim"`. core_verified is boolean based on final completion, not file existence alone. Histories has `{path, requested, status, evidence}`, where path is `"SRIM Outputs/COLLISON.txt"`, requested is the selected cascade value, status is `"not-requested"`, `"available"`, `"native-unavailable"`, `"missing"`, `"inconsistent"` or `"unverified"`, and evidence is a list of relative file/source or dialog-sequence references.

Each limitation is `{code, message, requested, observed, evidence}` with string code/message, JSON requested/observed values and a reference list. Initial codes are `native-kp-no-trajectories`, `native-2013-full-detail-unavailable` and `native-signed-energy-transfer`. These are allowlisted qualifications, not a generic error-to-warning switch. The last records preserved signed native energy data without asserting its physical cause.

For opted-in runs, execution.json becomes version 2 and adds exactly `availability` containing the same availability object. Existing completion keys `{complete, reason, checks}` and shared metadata/failure schemas stay unchanged. The runner's returned metadata still uses succeeded/failed; documentation requires inspecting availability alongside status. A small public read API below provides verified access. Old records remain readable; calling that new API on a record without availability raises FileNotFoundError, rather than inventing historical availability.

## Native state machine and response rules

State names: `running`, `cascade-choice`, `cascade-active`, `detail-choice`, `detail-active`, `kp-limitation`, `event-complete`, `resume-complete`, `resume-save`, `resume-export-range`, `resume-export-lateral`, `resume-export-ionization`, `resume-export-phonons`, `resume-export-recoil-energy`, `resume-export-damage`, `resume-export-destination`, `resume-export-acknowledgment`, `resume-exit`, `done`, `failed`.

State stores the pending command and previous dialog fingerprint. Never resend while awaiting a transition. A changed window/message, disappearance, required generated file or clean owned exit verifies the applicable transition. Reappearance of an already completed stage is unexpected; a recycled handle alone is not proof of a new dialog. Preserve failed/unknown observations and terminate the owned job on uncertainty.

Matching normalizes whitespace and Windows button accelerator ampersands only. Preserve wording, path punctuation, numbers, degree/list markers and mode identity. Require the expected native window class, complete known message, current stage, unique expected button set, exact positive owned PID and current child relationship. Standard Yes/No/OK IDs are 6/7/2. Re-read title/control text, IDs and ownership immediately before posting. Do not focus windows, use coordinates, send keystrokes or depend on Computer Use.

1. Cascade question: title `TRIM: Special for Neutron/Electron Damage`; complete message begins `Would you like a file of each Cascade ? [Y]` and includes `This activates the file SRIM Outputs\\COLLISON.txt` and `and stores calculation results for EVERY cascade.` Require exactly Yes/No. None-choice selects No; statistics/detailed select Yes. Preserve the entire exact normalized message fixture, not prefix-only matching.
2. Activation: same title, exact `File SRIMOutput\\COLLISON.txt now active.` with OK only; acknowledge only after Yes.
3. Detail question: same title, exact recorded `Would you like every collision filed? [Yes]` message including the two native list markers and description of COLLISON.txt. Require Yes/No. Statistics selects No; detailed selects Yes.
4. Detailed activation: exact `Every Collision will be filed.` with OK only, only after detailed Yes.
5. KP limitation: title `TRIM`, exact `No cascade details available with KP Damage`, OK only. Permit only the recorded engines/input modes: 2008 mode 6 and 2013 mode 6, after activation. Preserve actual requested semantic mode and saved-mode interpretation. Record the applicable limitation; acknowledge without fabricating a detail selection.
6. Fresh event completion: title `End of TRIM.DAT calculation`, exact `Calculation Complete (<expected_count> Ions)`, OK only. Permit only an event-driven mode at its expected completion stage. Count mismatch fails. Await real process exit; do not terminate it as a successful substitute.
7. Resume completion: title `End of calculation`, exact expected count plus `Yes = Save data` and `No = Increase number of ions`, Yes/No only. Always select Yes at the sealed target. Never increase a count inside the running record; future work uses the existing new-child continuation API. A different count fails.
8. Resume save form: title `Form1`, expected native VB form and exactly the three reviewed command buttons. Select `Store in SRIM directory (default)`, control ID 3. Never select alternate destinations or skip saving automatically. Retain complete native SAV files in runtime/SRIM Restore; inherited checkpoint/ stays unchanged.
9. Resume exports: once the save form closes and the owned calculation window is enabled at the expected count, select the six F buttons in the owned Plots form, using the child chain, native command-button class and IDs: Range 34; Lateral 33; Ionization 32; Phonons 31; Energy to Recoils 30; Damage Events 29. Revalidate the form/ID/layout before each button. Same-caption F buttons in the 3D section are not interchangeable. This mapping follows the observed successful export/file results.
10. Destination chooser: recognize the reviewed complete `Please select where you would like to save ASCII textfiles containing the Data arrays used in the Plots.` message and exactly one OK command button. Read the full selected native path, resolve its literal Windows identity and require this runtime's SRIM Outputs. If it is unavailable, truncated, elsewhere or ambiguous, stop; do not navigate to a guessed directory or type a path. Post OK only for the verified destination. Directory/list controls are inspection-only.
11. Export follow-up: permit only a recorded acknowledgment identifying the exact expected current output(s), count/context and canonical directory; select its sole OK. No generic OK rule. If the known export emits no acknowledgment, verify the expected files were generated and the form returned before proceeding. Required files must be present, nonempty and no longer changing across two consecutive native polls before moving to the next export; final parsers and sealed-copy verification remain authoritative. The 3D damage acknowledgment is not part of this automated sequence.
12. Resume exit: after all eight standard text outputs are generated and the saved textual checkpoint records the exact target, send the observed File/Exit menu command ID 7 to the owned enabled MDI main form. Await real native exit; preserve and parse afterward. No successful forced termination.

For all actions, use a checked asynchronous Windows command notification as demonstrated by the diagnostic. No global observer monkeypatch in production. The existing job membership check encloses observations; revalidate dialog/control PID and parentage before sending. Posting success is only queue acceptance; every event needs subsequent transition verification. Native/API failure, capture errors or progression timeout fail the owned attempt without blind retries.

Raw runtime-error dialogs, including errors 5 and 9, are never acknowledged as success by this state machine. Preserve evidence and stop. The no-quotes/control and exhausted-event findings inform preflight checks, not a broad rule accepting error dialogs as native flaws.

## Core verification and known missing-history behavior

Success still requires: clean owned native exit, no monitor/response errors or unhandled popup, unchanged sealed initial native files, consistent parsed summary and all seven expected profile families, complete checkpoint, exact saved current/total/autosave, and saved-value comparisons without substitutions. For native quick-mode NOVAC's explicit not-calculated form, preserve unavailable data; do not require an invented numerical table.

For statistics requests, require a nonempty COLLISON.txt with the verified cascade-statistics structure and expected count coverage. For detailed requests in 2008 full recoil, require the verified recoil-detail table headings as well as statistics. Missing/malformed required history remains failure unless an exact known-unavailable rule below applies. Availability inspection identifies structures and count coverage, not scientific interpretation of every collision row.

Allow succeeded with explicit native-unavailable history only for:

- Verified quick recoil: full trajectories are not calculated. For 2013 mode 8, the offered detail choice produced the same statistics file; do not claim the choice generated detailed trajectories. For 2008 mode 6, retain its explicit limitation message.
- Verified 2013 full recoil input mode 6: the recorded KP-limitation branch blocks the detail choice but supplies cascade statistics. If detailed output was requested, core verification and actual nonempty expected statistics must still pass. Preserve `native-2013-full-detail-unavailable`. The popup does not convert this into quick mode. A possible renumbering oversight remains unconfirmed.

Unknown missing files, malformed tables, lack of core results or an unrecognized engine are not covered by this exception. Merely seeing a known message is insufficient if outputs fail verification. None-choice does not require a history file and does not authorize erasing incidental outputs.

## Parser and preflight changes

### Saved mode identity

Keep `TRIM.IN` input mapping unchanged. Add an explicit saved-runtime mapping in trim_outputs:

| Meaning | 2008 input / saved | 2013 input / saved |
| --- | --- | --- |
| Ordinary and specified-ion modes | 1-5 / same | 1-5 / same |
| Recoil quick | 6 / 6 | 8 / 6 |
| Recoil full | 7 / 7 | 6 / 7 |
| Recoil monolayer | Unsupported | 7 / 8 |

Use that mapping for checkpoint.mode comparisons and KP NOVAC compatibility. Keep checkpoint.native_input.mode comparisons against the input mapping. Unknown saved modes are not relabeled to a plausible calculation. Standalone parsing uses the checkpoint version and explicit saved mapping; when a request also exists, both semantic identities must agree.

### Signed printed E2RECOIL values

The observed 2013 KP recoil table prints `-2000.00E-04` in column 1 (zero-based, after depth). For the verified SRIM-2013.00 three-column KP E2RECOIL layout only, allow a finite signed token in that column and preserve its exact string. Keep depths increasing/nonnegative, other columns nonnegative and all existing structural checks. Do not generalize signed acceptance to every table or convert negative values to zero/absolute values. Add a profile note and availability qualification when a negative token occurs, naming its source rows. No physical cause, conservation correction or universal native bug is inferred.

### Quoted names

At staging/preflight, inspect calculation and resolved layer names when the pinned executable hash matches the verified 2013 binary. If either contains ASCII double quotes, reject with a field-specific ValueError identifying the tested unsupported doubled-quote encoding and engine hash. Preserve the original request; never remove or rename it automatically. Stage-time rejection occurs before native launch and before creating a runtime. Existing input-bundle parsing/replay remains able to inspect historical quoted requests. Do not claim all encodings or all other engines are proven unsupported.

### Exhausted event input

For the two verified engines' supported event modes, require staged canonical TRIM.DAT to contain at least the requested absolute final number of complete event records, including the preserved prefix for a continuation. Total remains the caller's total, not the file length. Require the existing nonempty ordered prefix/appended-event rules, ten headers, ASCII/CRLF representation, eight numeric fields after the event ID, and canonical terminal EOF. Reject missing/short/malformed input before launch; never invent an event or rerun automatically. Unknown engines retain executable-evaluated bounds outside automatic policy. No new general size, energy or seed caps.

## Files and complete function catalogue

All changed/new Python modules start with module docstrings. Every changed/new class/function/method has its required docstring, fully typed arguments including self/cls, return annotation, side effects and raised errors. No implementation comments. No unlisted production classes, nested functions or public APIs are planned. Imports are standard library and existing project modules only.

### New src/srim/trim_dialogs.py

Purpose: pure validation, policy construction, state classification and history availability; no Windows calls, launches or filesystem writes. Depends on existing JSONObject/ResolvedTRIMInputs, immutable snapshots and Decimal; imported by runtime/native/execution. Owns engine allowlist, stage/message/control selectors and policy/report validators. No classes.

```python
normalize_trim_dialog_text(text: str) -> str
count_trim_event_records(contents: bytes) -> int
validate_trim_native_request(inputs: ResolvedTRIMInputs, *, total_ions: int,
    event_contents: bytes | None) -> None
build_trim_dialog_policy(inputs: ResolvedTRIMInputs, control: JSONObject,
    cascade_output: str | None) -> JSONObject | None
initial_trim_dialog_state(policy: JSONObject) -> JSONObject
classify_trim_dialog(window: JSONObject, policy: JSONObject,
    state: JSONObject) -> JSONObject
advance_trim_dialog_state(state: JSONObject, observation: JSONObject,
    action_result: JSONObject | None) -> JSONObject
assess_trim_histories(files: Mapping[str, bytes], policy: JSONObject,
    events: Sequence[JSONObject]) -> JSONObject
```

- normalize: string input; return whitespace-normalized comparison text; no punctuation loss; ValueError on nonstring.
- count: canonical event-file bytes; validate headers/record widths/numbers/EOF without rewriting; return event count; ValueError on malformed bytes.
- validate: verified pinned inputs, explicit target total and optional actual staged event bytes; check known-engine quotes and event coverage; return None; ValueError on confirmed invalid input, no I/O/mutation.
- build: inputs plus version-one base fresh/resume controls and proposed choice; validate engine/mode/flags/choice/counts; return the exact policy or None; ValueError on unsupported/conflicting controls; no modification of supplied objects.
- initial: validated policy; return a new state with no pending commands and no completed exports/limitations; ValueError on malformed policy.
- classify: one captured window, validated policy and state; return `{decision, reason, next_stage, command}` where decision is respond/wait/reject, reason null/string, command null or `{action, target_handle, control_id, response}`; selectors must satisfy the state rules. No response is sent here. Malformed arguments raise ValueError; unknown valid observations return reject with reason.
- advance: state, next window/file/process observation and optional queued/native action result; return copied new state, verifying pending transitions and accumulating limitation/export evidence. No retries or native calls. Malformed or impossible state transition raises ValueError; unchanged pending state is retained until its bound expires in native monitoring.
- assess: stopped-runtime relative byte mapping, policy and response events; return the availability object with evidence, known limitations and history status. Invalid caller/schema arguments raise ValueError; native missing/malformed histories are reported statuses. No filesystem access or claimed physics validation.

### src/srim/trim_runtime.py

Purpose remains sealed fresh/continuation staging and independent verification. Imports pure policy/preflight helpers. Exact changed signatures:

```python
prepare_trim_simulation(paths: ProjectPaths, config: Config,
    created: CreatedDirectory, worker: CreatedDirectory,
    inputs: ResolvedTRIMInputs, *, cascade_output: str | None = None) -> Path
prepare_trim_continuation(paths: ProjectPaths, config: Config,
    created: CreatedDirectory, worker: CreatedDirectory, parent_id: str, *,
    total_ions: int, autosave_ions: int,
    events: list[JSONObject] | None = None,
    cascade_output: str | None = None) -> Path
_stage_trim_runtime(paths: ProjectPaths, config: Config,
    created: CreatedDirectory, worker: CreatedDirectory,
    inputs: ResolvedTRIMInputs, continuation: JSONObject | None,
    checkpoint_files: Mapping[str, bytes], *,
    cascade_output: str | None) -> Path
verify_trim_runtime(paths: ProjectPaths, simulation_id: str,
    runtime: Path) -> ResolvedTRIMInputs
```

Existing positional inputs/outputs and allocation ownership stay as documented. Public preparation returns the new runtime Path, writes sealed native/input evidence and may retain partial staging on existing I/O errors. Pass the explicit new choice to _stage. After computing actual fresh/resume files/counts but before creating a staging directory, validate native request and build policy. None publishes existing control v1; opted-in publishes control v2. Preserve parent inventories and complete checkpoint copying. ValueError rejects bad choices/known-invalid names/events before main launch; OSError/collision semantics remain. verify reads sealed evidence, accepts exact v1/v2 control shapes, reconstructs the v2 policy and runs preflight again; returns replayed inputs without writes. Historical records are not migrated or rewritten.

### src/srim/trim_native.py

Purpose becomes owned Windows execution with bounded verified responses. Imports pure policy classification/transition helpers; retains suspended start, explicit cwd, Windows job assignment, cancellation, timeout and stopped-process waiting. Exact functions:

```python
_read_trim_window_text(window_handle: int) -> JSONObject
_read_trim_windows(process_id: int) -> list[JSONObject]
_post_trim_command(process_id: int, window: JSONObject,
    command: JSONObject) -> JSONObject
_observe_trim_dialogs(process_id: int, runtime: Path, policy: JSONObject,
    state: JSONObject, elapsed_seconds: float) -> JSONObject
execute_trim_process(executable: Path, runtime: Path, *, hide_window: bool,
    timeout_seconds: float, stdout_path: Path, stderr_path: Path,
    cancel: Callable[[], bool] | None = None,
    dialog_policy: JSONObject | None = None) -> JSONObject
```

- Existing readers retain their positive-handle/PID and Windows-only validation, bounded message reads and OSError behavior; extend child evidence, read required main/Plots form text in export stages, and expose owned VB forms/choosers without broad popup exemptions. They never respond themselves.
- post: PID, fresh window observation and classified command; recheck job-vetted PID, current dialog/control ownership, parentage, text, IDs, class, enabled/visible state. Post one checked asynchronous command. Return queue-result evidence; ValueError rejects stale/unsafe selection, OSError reports native failures. No retry, screenshot, activation, focus or keyboard.
- observe: called only after existing job-membership validation; read process windows and required generated files under the owned runtime, classify/advance the supplied state, send at most one new command, and return `{state, events, unhandled, errors}`. elapsed_seconds is finite nonnegative time from native start. Reject malformed arguments; report native monitoring/action errors for terminal classification. Transient disabled-main/modal-construction states may wait only within the documented bound. It never launches or kills a process.
- execute: retain all prior arguments/path checks and output/cleanup contracts. With None behave report-only; with policy validate it before creating the process, maintain one local state, call the new observer inside the existing monitoring loop and return augmented evidence. Pending failure returns monitor-error or popup with exact evidence, and cleanup still terminates only its own job. BaseException still cleans/waits and re-raises. No monkeypatching of production observers.

### src/srim/trim_outputs.py

Purpose remains pure native parsing/comparison. Keep report/row formats and all raw strings. Exact changed/new functions:

```python
saved_trim_mode(version: str, input_mode: int) -> int
saved_trim_mode_name(version: str, saved_mode: int) -> str
parse_trim_profile(filename: str, contents: bytes) -> JSONObject
compare_trim_saved_values(checkpoint: JSONObject, inputs: ResolvedTRIMInputs,
    *, resumed: bool = False) -> JSONObject
parse_trim_outputs(files: Mapping[str, bytes],
    request: JSONObject | None = None) -> JSONObject
```

The two new helpers validate supported version/code types; return saved code or descriptive mode using the explicit table, without I/O; unsupported/ambiguous values raise ValueError. compare uses saved mapping only for checkpoint.mode and existing input mapping for native_input.mode; existing requested values, tolerances and resumed exemptions remain. parse_trim_outputs uses semantic saved-mode identity for NOVAC and request consistency, preserving failures for actual full-cascade missing tables. parse_trim_profile applies only the narrow signed-column exception above and appends source-qualified notes; other malformed/negative rows still raise ValueError. Existing parse_trim_outputs handling retains per-file native issues rather than suppressing exceptions as success. No physical correction, generalized sign relaxation or input schema changes.

### src/srim/trim_execution.py

Purpose remains lifecycle execution, evidence publication and final verification. New imports: policy/availability helpers. Exact signatures:

```python
_trim_completion(inputs: ResolvedTRIMInputs, process_result: JSONObject,
    parsed: JSONObject, saved_values: JSONObject,
    continuation: JSONObject | None, *,
    availability: JSONObject | None = None) -> JSONObject
run_trim_simulation(paths: ProjectPaths, config: Config,
    simulation_id: str, runtime: Path, *, timeout_seconds: float = 60.0,
    cancel: Callable[[], bool] | None = None) -> JSONObject
read_trim_output_availability(paths: ProjectPaths,
    simulation_id: str) -> JSONObject
```

completion receives prior reports plus optional availability, validates argument schemas and returns the unchanged complete/reason/checks structure without effects. Handled event records do not fail the process assertion; unhandled popups do. Apply only the explicit history exception, never core exceptions beyond the parser's verified NOVAC representation. Missing/inconsistent required history without an allowlisted limitation adds a failing completion check.

run retains its public signature, worker lease, immutable input verification, lifecycle and exception contracts. Pass the sealed policy to native execution, preserve the stopped entire runtime before parsing, inspect stopped optional history bytes, publish availability and version-two execution evidence only for opted-in records, then seal metadata. The currently selected output file mapping for summary/profiles stays unchanged; history inspection uses separate relevant bytes and does not send histories into aggregate parsers. Record failed attempts' availability as unverified or an evidenced status; never manufacture core_verified=true. Add failure code `output-requirement` for missing/inconsistent required histories outside known limitations; existing failure codes remain.

read availability accepts existing ProjectPaths and a terminal TRIM identity, verifies the record's sealed output inventory, reads/validates exact availability schema and returns a copied object. No writes. Invalid state/engine/schema raises ValueError, unsafe I/O raises OSError, absent old-record availability raises FileNotFoundError.

No changes to trim_queue, shared metadata schemas or worker-lock code are needed: tasks already carry independent prepared runtimes and use run_trim_simulation. No production code outside the five modules listed above is planned.

## Tests and native fixture files

New `test/srim/test_trim_dialogs.py`: module docstring and `class TestTRIMDialogs(unittest.TestCase)` with no custom constructor/state/helpers. Each method has `(self: TestTRIMDialogs) -> None`, a first-statement docstring describing its constructed inputs, assertions and temporary-file effects, and no implementation comments. Methods:

| Method | Required verification |
| --- | --- |
| test_policy_choices_and_conflicts | None/three choices, engine/mode/flag conflicts and no caller mutation |
| test_engine_hash_allowlist | Exact two hashes accepted, unknown opt-in rejected |
| test_exact_cascade_sequences | All observed statistics/detailed/none and KP-limit stages |
| test_unknown_or_changed_dialog | Changed title/message/layout/count/stage returns reject |
| test_no_duplicate_response | Pending command is never queued twice; transition bounds enforced |
| test_resume_save_export_exit_plan | Exact save choice, six F IDs, directory identity and Exit selector |
| test_unavailable_history_qualification | Correct known-code/engine/mode evidence gates; missing core never waived |
| test_exhausted_events_rejected | Complete canonical event prefix/count accepted; insufficient/malformed events rejected |
| test_known_engine_quoted_names | Each field separately rejected for verified 2013 engine; no silent rewrite |

Extend existing test classes, preserving constructor and unrelated tests. Each listed method has the complete signature `(self: <existing class>) -> None`, module/class context as already present, and a required descriptive docstring. No new helper/class signatures are planned:

- `test/srim/test_trim_native.py`, TestTRIMNative: test_known_responses_owned, test_response_revalidation, test_post_failure_no_retry, test_transition_timeout, test_resume_export_directory_guard, test_cancellation_during_response. Verify actual message parameters with mocks, owned-only targeting, unknown observation failure, unrelated windows untouched, no focus/key/screen APIs, and unchanged None-policy report-only behavior. Existing popup-report tests remain meaningful for None.
- `test/srim/test_trim_runtime.py`, TestTRIMRuntime: test_policy_sealed_and_replayed, test_policy_tampering, test_legacy_control_compatible, test_quote_preflight_before_staging, test_event_count_preflight. Use fresh .cache allocations; assert correct v1/v2 shapes, explicit continuation choice, parent unchanged and no launch on invalid requests.
- `test/srim/test_trim_outputs.py`, TestTRIMOutputs: test_saved_mode_numbering, test_recoil_quick_novac_compatibility, test_signed_kp_recoil_energy_preserved, test_other_negative_profiles_rejected, test_wrong_mode_still_inconsistent. Verify raw -2000.00E-04 and notes; test request/native-input/saved-mode agreement and genuine replacement-table failures.
- `test/srim/test_trim_execution.py`, TestTRIMExecution: test_handled_dialogs_require_verified_core, test_known_history_gap_succeeds_with_limitation, test_unknown_history_gap_fails, test_runtime_error_zero_exit_fails, test_availability_reader_verifies_inventory, test_resume_exports_before_success. Verify metadata lifecycle, all preserved reports, tampered/legacy availability handling and missing/incorrect core failures.

New native fixtures, copied byte-for-byte from the completed proof/matrix without line conversion:

```text
test/srim/fixtures/trim/recoil-quick-2013/
    TDATA.txt RANGE.txt VACANCY.txt IONIZ.txt PHONON.txt
    E2RECOIL.txt NOVAC.txt LATERAL.txt TDATA.sav TRIMIN.sav manifest.json
test/srim/fixtures/trim/cascade-history-2008/
    statistics/COLLISON.txt detailed/COLLISON.txt manifest.json
```

The first ten native files come from native-response-proof/std8-statistics (root text files and SRIM Restore saved files). Two history files come from mode-version-matrix/srim_2008/mode-07/{cascade-only,every-collision}/SRIM Outputs. Each UTF-8 manifest contains exactly `{schema_version:1, source:string, files:[{path, sha256, size_bytes}]}`; source is the repository-relative local evidence label, files sorted by forward-slash relative path, manifest excludes itself. Existing recursive .gitattributes rules already preserve `.txt`/`.sav`; no attribute change is needed. Fixtures validate observed formats, not physics.

## Documentation changes

| File | Exact responsibility and content relationship |
| --- | --- |
| README.md | Explain explicit preparation choice/default, automatic versus report-only behavior, successful-core/native-unavailable exception, availability report, parser qualifications and TIN exclusion; link current guide. |
| doc/guides/trim-execution.md | Add runnable fresh/continuation examples using the exact new keyword, output-availability reading, mode/engine support, direct-message mechanism, resume save/export/exit sequence, error policy and limitations. Replace outdated universal report-only wording with the None-policy distinction. |
| doc/reference/trim-execution-format.md | Publish exact signatures, v1/v2 controls/reports, policy/events/availability schemas, failure code, stage/timing/ownership rules and read API. |
| doc/reference/api.md | Update existing preparation signatures and add availability reader, with links to exact contracts. |
| doc/guides/trim-inputs.md | Clarify verified quote preflight and event coverage at staging while retaining historical bundle/readability and no speculative caps. |
| doc/reference/trim-input-format.md | Correct current runner cross-reference and describe unchanged input schemas/collision flags versus separate sealed execution choice and mode-number distinction. |
| doc/TRIM_HANDOFF.md | Record only additional implementation/acceptance state and unresolved limitations; do not repeat loaded instructions or claim UI-assisted review proves all production paths. |
| doc/README.md | Add this proposal under design references and point current behavior to guide/reference after implementation. |
| doc/TRIM_POPUP_PROPOSAL.md | Preserve this complete design and approval/acceptance amendments; no raw transcripts. |

No SKILL, agent definition, source dataset, installation file, global setting, unrelated documentation, package/dependency file or user project record is changed. Auxiliary scripts/renders/test runtimes/evidence remain under .cache. No commit, deployment, permanent background task or automatic cleanup is included.

## Lifecycle, concurrency and recovery

Each policy belongs to one sealed prepared runtime and process job. Existing worker leases cover native responses, exports, preservation, parsing and finalization. Different worker jobs may run concurrently with separate state objects; all commands target their verified PID/control handles, with no shared cursor/focus. Same-worker tasks remain serialized by existing queue behavior. No batch-level observer monkeypatch.

created -> running begins only after preflight. Known runtime errors, unhandled dialogs, response uncertainty, timeout/cancellation, preservation errors or invalid core outputs produce the existing failed lifecycle and evidence retention. Completed known-limitation cases may become succeeded only after all nonwaived core checks. No in-place increase-count automation or automatic fresh-to-resume conversion. A subsequent continuation always receives a new simulation ID, explicit counts and an immutable parent.

On interruption, stop dispatch through existing mechanisms, terminate only assigned jobs, wait, preserve available bytes and publish failure evidence before re-raising. Never erase a partially exported or failed runtime. Cleanup remains explicit and inventory-verified. Checkpoint atomicity and equivalence with uninterrupted physical calculations remain unclaimed.

## Verification before reporting implementation complete

1. Run meaningful unit tests above, then required full SRIM/backend/knowledge regressions with warnings as errors using only `D:\projects\agentic_srim\.venv\Scripts\python.exe`. No new unrelated benchmarks.
2. Check syntax, all docstrings/signatures/self annotations, absence of comments, exact schema/signature agreement with this proposal, documentation links/fences and Git whitespace. Validate all new fixture raw/staged hashes and byte sizes.
3. Native acceptance in fresh .cache environments through production APIs, not observer monkeypatches: the six affected engine/mode pairs with none/statistics/detailed choices (18 fresh runs), plus 2013 Standard and 2008 ordinary full-cascade 5->6 continuation with core-only policy (2 runs). Standard/Pro executable equality is recorded; exercise both installation resource sets in the six-pair set.
4. For each fresh run, verify native exit, exact 100/100, handled sequence/ownership, original inputs and installation hashes unchanged, all core outputs parsed/compared and appropriate history availability/limitations. Compare applicable outputs to preserved matrix evidence. The requested detailed option must never claim trajectories in KP mode or pretend 2013 full detail was supplied.
5. For both continuations, verify default final checkpoint, six standard exports into the correct runtime, parsed 6-ion outputs, real normal exit and unchanged parent/input checkpoint. Direct native form/chooser control capture and command behavior are acceptance gates; if an unrecognized control/message variant appears, stop that attempt, preserve it and present the required amendment before changing the design. Do not silently fall back to Computer Use or a guessed selector.
6. Verify invalid known-engine quotes and insufficient event continuation are rejected before launch. Use mocked zero-exit native errors to retain failure classification. Verify None-policy legacy behavior, two distinct workers' independent message targets and cancellation/unknown-dialog paths without extra scientific runs.
7. Publish counts, successes, failures, limitations and retained evidence. A failing acceptance gate remains open; budget/time pressure does not authorize completion claims. Consult the repository self-improvement skill at completion; evidence does not authorize workflow changes.

## Approval requested

Approval of this document authorizes only the listed code/docs/fixtures, opt-in defaults, schemas, lifecycle exception, parser treatment and bounded verification. In particular, it approves the proposed separate cascade_output preparation keyword; that API decision has not previously been approved. It also approves preserving the narrow signed native E2RECOIL column rather than suppressing the parser error or altering its values. No implementation begins until explicit approval. A needed design change requires another concrete amendment and approval before proceeding.
