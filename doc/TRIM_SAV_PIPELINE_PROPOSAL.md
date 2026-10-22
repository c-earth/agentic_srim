# Checkpoint results in the TRIM pipeline

Status: proposed, awaiting explicit approval. No implementation is authorized by this document alone.

## Scope and baseline

Follow `TRIM_DIRECTION.md`: connect recovery v2 to resumed execution, retain completion/default-save handling, and freeze the complete checkpoint after stopping the owned processes. Fresh runs keep their native-text result contract. No GUI exports, chooser operations, native-only statistic reconstruction, recovery-algorithm changes, historical-record migration, or new native calculations are included.

The working tree already contains unrelated parser/availability edits in `trim_outputs.py`, `trim_dialogs.py`, tests, and documentation. Preserve them. Only the specific dialog changes below overlap those edits; review the diff before and after implementation. No commits, cleanup of existing files, or fixture replacement are included.

## User-visible behavior and defaults

1. `prepare_trim_continuation(..., cascade_output=None)` retains report-only dialog behavior. Its results use checkpoint recovery after natural exit, but any popup, timeout, cancellation or monitor failure remains a failed execution even if the checkpoint is recoverable.
2. Explicit `cascade_output="none"`, `"statistics"` or `"detailed"` enables the existing approved response choices for continuation. Existing mode/conflict/engine restrictions remain. No new default response is introduced.
3. All newly executed resumed records select checkpoint results explicitly from sealed control `kind="resume"`. Fresh records select native text. Missing text does not trigger an implicit fallback for fresh runs.
4. Resumed results are cumulative through the absolute requested total, including the parent count. Native text, if present, is preserved and parsed separately as diagnostic evidence; stale text cannot override recovered results or fail checkpoint completion solely because it is stale/missing.
5. Structural recovery warnings, including inferred geometry, remain visible qualifications and are not fatal by themselves. Execution success means verified pipeline completion with recovered results; it does not certify physics or native printed precision.

## Proposed files and responsibilities

| Path | Purpose, contents and dependencies |
| --- | --- |
| `src/srim/trim_results.py` (new) | Pure checkpoint-to-pipeline adapter. Imports metadata JSON types, native parser and recovery API. Keeps `trim_outputs -> trim_recovery` imports unnecessary and avoids their circular dependency. Contains the one function specified below; no classes. |
| `src/srim/trim_native.py` | Add checkpoint snapshot/probe helpers and integrate verified post-save owned-job shutdown. Uses existing regular-file/inventory utilities, checkpoint parser and recovery API. No new UI commands. |
| `src/srim/trim_dialogs.py` | Remove automatic-resume preparation rejection, distinguish verified save completion from generic `done`, and add the explicitly specified save timing field to new policies. Preserve fresh cascade classification and local scientific-availability edits. |
| `src/srim/trim_runtime.py` | Update continuation documentation and sealed policy replay for the revised policy schema. Existing full checkpoint copy, count edits, parent inventory and event-prefix behavior remain. |
| `src/srim/trim_execution.py` | Read all flat preserved checkpoint files, choose the checkpoint adapter only for resume, verify saved counts/request/integrity and native stop evidence, and publish terminal outputs. |
| `test/srim/test_trim_results.py` (new) | Fixture-driven adapter tests, including complete 2008/2013 sets, stale/absent text, malformed checkpoint and cumulative 200-ion recovery. |
| `test/srim/test_trim_native.py` | Replace temporary resume-guard expectations; exercise save gating, snapshots, owned-job stop and cleanup failures using synthetic Windows APIs. |
| `test/srim/test_trim_dialogs.py` | Test fresh/resume policy generation, schema compatibility, precise completion/save transitions and unknown-popup rejection. |
| `test/srim/test_trim_runtime.py` | Test opted-in continuation staging/replay, schema rejection and parent/event preservation. |
| `test/srim/test_trim_execution.py` | End-to-end mocked process tests for recovered continuation, finalization, qualifications, saved-value/count failures and raw checkpoint preservation. |
| `README.md`, `doc/guides/trim-execution.md`, `doc/guides/trim-checkpoint-recovery.md`, `doc/reference/trim-execution-format.md` | Document source selection, exact new report fields, stopped-checkpoint lifecycle and qualifications. Merge narrowly with existing local edits. |
| `doc/TRIM_DIRECTION.md` | Replace the temporary-guard statement with implemented behavior only after verification; record remaining native lifecycle validation limits. |

No changes to `trim_recovery.py`, `trim_outputs.py`, `trim_storage.py`, queue APIs, shared simulation metadata schemas or configuration files are proposed. Offline native directory import remains native-only. Existing standalone recovery export remains available independently.

## Typed APIs and contracts

All new/changed functions retain required docstrings, complete annotations including `self` in tests, and no implementation comments. No production classes are added.

### `src/srim/trim_results.py`

`parse_trim_checkpoint_results(files: Mapping[str, bytes]) -> JSONObject`

Input is a frozen runtime-relative mapping, using `/` separators and regular-file bytes. Validate the entire mapping using the native parser's existing safe-name/type/case-collision validation. Select only direct children of `SRIM Restore/`, strip that prefix and pass the complete flat mapping to `recover_trim_checkpoint`. Reject nested checkpoint paths as a checkpoint issue rather than silently discarding them. Retain extra safe flat checkpoint files in recovery provenance. Native root/output text is parsed with `parse_trim_outputs(files)` without an inferred request.

Return the schema below. No I/O, mutation, rendering or lifecycle changes. Invalid caller mappings raise `ValueError`. A recovery `ValueError` becomes one `checkpoint-recovery` issue with the original message and `complete=false`; filesystem errors are outside this pure API. The native diagnostic report may have issues independently. Recovery success supplies its unchanged v2 object and `complete=true`; requested-input and execution checks remain the execution layer's responsibility.

### `src/srim/trim_native.py`

`_read_trim_checkpoint_snapshot(runtime: Path) -> dict[str, bytes]`

Read every direct regular file from absolute `runtime / "SRIM Restore"`. Reject links, junctions, directories and case collisions; preserve extra safe names. Inventory before and after reads and compare sizes/hashes with read bytes. Return flat original-case names mapped to bytes. Source changes raise `ValueError`; missing/inaccessible files propagate `OSError`. No writes or process operations. A stable read is not an atomicity guarantee.

`_probe_trim_saved_checkpoint(runtime: Path, policy: JSONObject, previous: JSONObject | None, elapsed_seconds: float) -> JSONObject`

Validate policy, finite nonnegative monotonic elapsed time and the previous probe shape. Read a snapshot and recover it. Require saved current/total equal policy expected count, correct engine version and semantic/input mode, and saved autosave equal policy autosave. Hash every file. Compare with the previous successful sample; readiness requires identical whole-file inventory/hashes at least 1.0 seconds apart. A changed set starts a new candidate. Return `{ready: bool, candidate: object|null, sources: list, last_error: str|null}`; candidate contains `elapsed_seconds` and sorted `sources` only. Not-ready samples retain a useful error string. Catch only expected snapshot/recovery `ValueError` and `OSError` as not-ready; reset candidate after a failed probe. Invalid arguments raise `ValueError`. No writes, commands or termination.

`execute_trim_process(executable: Path, runtime: Path, *, hide_window: bool, timeout_seconds: float | None, stdout_path: Path, stderr_path: Path, cancel: Callable[[], bool] | None = None, dialog_policy: JSONObject | None = None) -> JSONObject`

Signature unchanged. Remove temporary automatic-resume rejection. New behavior applies only to newly sealed schema-3 resume policies. Preserve fresh/report-only behavior. Monitor all owned process windows before considering readiness. Cancellation, overall timeout, unexpected popup and monitor errors take priority over checkpoint readiness. Begin probes only after the default-save command's pending transition has been verified by disappearance of the matching dialog. Do not probe on generic `done`.

At readiness, call `TerminateJobObject` on the assigned job only, check its result, and wait until job active-process count is zero. Preserve the actual nonzero termination exit code. Use termination `checkpoint-stop`, not `exit`, and attach the stop evidence below. A natural exit of the entire job remains `exit`; a queued save command alone is never success. Natural exit before live readiness may still proceed to offline preserved-checkpoint validation, with the verified dialog sequence required for any automatic-resume success.

New shutdown wait is bounded at 10.0 seconds, polled through existing 0.1-second monitoring. Query/termination errors or a nonempty job at deadline produce `monitor-error`; never return success or preserve a supposedly stopped runtime while owned processes are still known active. Existing finalization must attempt owned-job termination/closure even if the root process has already exited and descendants remain, then verify job emptiness before closing its query handle. If emptiness cannot be established, raise `OSError` with runtime evidence and let execution fail without claiming a frozen checkpoint. Do not delete files. Keep exception notes and best-effort failure publication.

`_observe_trim_dialogs(process_id: int, runtime: Path, policy: JSONObject, state: JSONObject, elapsed_seconds: float, *, startup_state: dict[tuple[int, int], float] | None = None) -> JSONObject`

Signature unchanged. Preserve existing observation/one-command/ownership behavior. Use the new 10.0-second save-transition bound only for a pending default-save command; other response transitions remain 2.0 seconds. Track verified `checkpoint-saved` stage distinctly. No checkpoint reads in this function.

### `src/srim/trim_dialogs.py`

`build_trim_dialog_policy(inputs: ResolvedTRIMInputs, control: JSONObject, cascade_output: str | None) -> JSONObject | None`

Keep signature, pure behavior, validation and errors except remove temporary resume prohibition. New non-None policies have schema version 3 and the existing schema-2 keys plus `autosave_ions: int`, `checkpoint_save_seconds: 10.0`, `checkpoint_stable_seconds: 1.0`, `shutdown_seconds: 10.0`. Autosave must be an exact integer matching control; retain existing integer-domain acceptance rather than imposing new count bounds. These are sealed constants, not new public configuration. `None` still returns `None`. Outer control schema remains version 2 for automated policies; nested policy version 3 is independent.

`initial_trim_dialog_state(policy: JSONObject) -> JSONObject`

Keep signature. Accept schema 3 only for new automated execution; reject older schema 1/2 with a clear instruction to prepare a new runtime. Add `checkpoint_probe: None` and `checkpoint_save_started: None` to initial state, populated only by the process controller. Validate all exact keys/types/constants and counts. No migration or record edits.

`classify_trim_dialog(window: JSONObject, policy: JSONObject, state: JSONObject) -> JSONObject`

Keep signature and strict selectors. Default-save response transitions to `checkpoint-saved`, still pending until verified. Other `done` paths retain their meaning. Existing event-complete dialogs in resume event modes do not themselves establish a checkpoint-save sequence: accept natural clean exit and offline validation, but do not force stop solely on event-complete acknowledgment. Unknown dialogs still reject. Never add menu-exit or export actions.

`advance_trim_dialog_state(state: JSONObject, observation: JSONObject, action_result: JSONObject | None) -> JSONObject`

Keep signature, copying, pending transition checks and errors. Preserve new state fields; never infer save completion from a posted command. No I/O.

### `src/srim/trim_execution.py`

`_trim_completion(inputs: ResolvedTRIMInputs, process_result: JSONObject, parsed: JSONObject, saved_values: JSONObject, continuation: JSONObject | None, *, availability: JSONObject | None = None) -> JSONObject`

Signature unchanged. Native/fresh branch stays unchanged. Resume branch requires results schema 2 below, valid recovery, exact current=total=requested total and autosave, no saved-value differences, and either clean natural job exit or verified `checkpoint-stop` with empty job and matching post-preservation checkpoint hashes. Automatic ion-resume requires verified completion/default-save sequence; automatic event-resume permits clean exit after its verified event-complete sequence. No pending/failed command, popup or monitor error is allowed. Keep checks/reason/complete shape. Requested-history checks retain their existing explicit availability rules; recovered profiles cannot substitute for collision histories.

`run_trim_simulation(paths: ProjectPaths, config: Config, simulation_id: str, runtime: Path, *, timeout_seconds: float | None = 60.0, cancel: Callable[[], bool] | None = None) -> JSONObject`

Signature unchanged. Hold existing worker lease through stop verification, full preservation, recovery, hashes and terminal publication. For resume read every direct checkpoint file from the permanent frozen copy, plus current native text/history diagnostic candidates. Call new adapter, compare recovered checkpoint using existing `compare_trim_saved_values(..., resumed=True)` and separately enforce child control total/autosave. Existing runtime input integrity checks remain. Require `checkpoint-stop` source hashes to match permanent checkpoint exactly; disagreement fails integrity. Preserve results/saved-values/availability even on expected recovery failure. Failure priority: unsafe stop/monitor, process popup/timeout/cancel/launch, integrity, substitutions, checkpoint recovery, histories, completion. Do not map `checkpoint-stop` to launch failure because of its actual exit code.

### `src/srim/trim_runtime.py`

`prepare_trim_continuation(paths: ProjectPaths, config: Config, created: CreatedDirectory, worker: CreatedDirectory, parent_id: str, *, total_ions: int, autosave_ions: int, events: list[JSONObject] | None = None, cascade_output: str | None = None) -> Path`

Use the existing paths/config, allocated child/worker, sealed terminal parent, explicit total/autosave and optional appended events/response choice to stage the child runtime and return its absolute path. Keep validation, safe I/O, count edits, parent preservation, worker ownership and raised errors unchanged except permitting the new automated policy. No native launch occurs. Documentation states the new result source.

`verify_trim_runtime(paths: ProjectPaths, simulation_id: str, runtime: Path) -> ResolvedTRIMInputs`

Verify the simulation's sealed inputs and runtime under project paths and return the independently verified input bundle without writes. Keep existing inventory, parent, engine, count and request checks; replay nested schema-3 policy and reject older automated sealed policies consistently with `initial_trim_dialog_state`. Report-only schema-1 controls remain usable. Invalid schema/state/content raises `ValueError`; unsafe/missing files propagate the existing filesystem errors. Parent eligibility rules remain unchanged.

## Schemas, paths and precision

Fresh `results.json` remains native schema 1. Resume `output/parsed/results.json` uses exactly:

```
schema_version: 2
engine: "trim"
result_source: "checkpoint-recovery"
native: <unchanged native schema-1 diagnostic report>
recovery: <unchanged recovery-v2 object, or null>
issues: [{path: "SRIM Restore", code: "checkpoint-recovery", message: str}]
complete: bool
```

On success `issues=[]`; `complete` describes structural result recovery only. The nested recovery object retains summary stored tokens, decimal-string profile rows, all seven families, availability/reasons, grid/layout, sources, target-entry identities, stored quick components and warnings. No numeric conversion, native summary fabrication, inferred missing zeros or per-segment subtraction. Never feed rendered recovery text to the native parser.

New resume execution reports use `schema_version=3`, with existing execution fields plus `result_source="checkpoint-recovery"` and `checkpoint_verification` containing `{sources: list, live_sources_match: bool|null, atomicity: "not established for binary native arrays"}`. `live_sources_match=null` means natural exit without a live ready snapshot. Failure/preflight initializes verification to null. Fresh report versions remain unchanged. Availability schema and saved-values schema remain unchanged.

For opted-in resume process reports add `checkpoint_stop` initialized to null. At controlled stop it becomes `{save_verified: true, stable_seconds: number, sources: [{path: str, sha256: str, size_bytes: int}], job_empty: bool}`. Paths are flat checkpoint names, sorted exactly as recovery provenance. Keep actual exit code and dialog event evidence. Natural exits retain null. Process dictionaries are private execution evidence; readers must recognize the new termination explicitly.

All permanent files use existing exclusive JSON writers and inventories. No extra recovered text artifacts or output directories are created. Preserve the complete raw runtime, including checkpoint files not consumed numerically. Existing external recovery exports remain schema v2. Historical reports remain untouched; new reader documentation distinguishes schema/source before accessing fields.

## Lifecycle limits and concurrency

Only the assigned job is stopped; never search processes by name. Do not parallelize checkpoint sampling with saving, termination, preservation or parsing. Use monotonic times. Ten seconds after verified save transition without a ready checkpoint fails as `monitor-error` with the latest probe reason even when the overall execution timeout is disabled. The default-save transition itself also has a separate ten-second bound. Readiness means two identical valid full snapshots, not proof of atomic native saving. Revalidate after every owned process has stopped; publish the atomicity limitation unconditionally.

No timeout/cancellation/unknown popup becomes successful because a usable checkpoint exists. Parent records and installed engines remain unchanged. No automatic retry, resume, deletion or cleanup of failed artifacts.

## Verification before completion

- Adapter: exercise committed 23 version/mode sets and generalized geometries; results equal direct recovery v2 exactly, including signed values, quick NOVAC, entry ordering and warnings. Test absent/stale native texts, incomplete/malformed arrays, unsafe/case-colliding paths, nested checkpoint directories and cumulative 200-ion results.
- Lifecycle: synthetic owned jobs verify completion/save command order, pending/failed transitions, two samples a second apart, mutation resetting readiness, invalid counts/modes/autosave, save deadline, unknown popup/cancel/timeout priority, descendants after root exit, failed termination/query, bounded shutdown and exact permanent hash comparison. Generic `done` must not authorize forced stop.
- Execution: mock only native execution, use real preservation/recovery/metadata where practical; prove one 2008 and one 2013 resumed run succeeds without text, while fresh missing-text runs fail; a process timeout with good `.sav` still fails. Check every required artifact and terminal inventory; preserve parent bytes and event prefix.
- Regressions: existing fresh dialog/native/runtime tests, rejection of abandoned export/chooser paths, legacy-policy rejection, parser/recovery precision tests and complete TRIM suite with warnings as errors. New tests use descriptive `.cache/` temporary directories. Do not alter existing unrelated fixtures.
- Run all project Python commands using `D:\projects\agentic_srim\.venv\Scripts\python.exe`, including `-W error -m unittest discover -s test/srim -p test_trim*.py -q`. Quote the test pattern as required by PowerShell. Inspect the final diff and documentation schemas for consistency.
- No native calculation is part of this approval. Report that live save/stop integration is covered by synthetic lifecycle tests and existing native checkpoint fixtures, and that a new live lifecycle check has not been performed.

## Approval request

Approve the complete design, including checkpoint-source selection for resumed runs, schema-3 automated-policy compatibility boundary, the proposed 1-second/10-second timing constants, explicitly reported owned-job termination after verified save/readiness, and the stated native-validation limit. Any requested change will be resolved in this proposal before implementation starts.
