# Event continuation: stop from the completed autosave checkpoint

Status: superseded by `TRIM_CHECKPOINT_FIRST_PROPOSAL.md`, following the twelve no-response probes. This historical event-only design was never approved or implemented. The shared proposal requires its own explicit approval.

## Why the approved event fix is insufficient

The approved config/event amendment is implemented and all 143 TRIM tests pass. Its authorized live rerun passed ordinary 2008 and 2013 Pro continuation. The Standard specified-ion-full case now verifies event completion and default-save responses, but native default save then opens a directory chooser. The runner correctly reports failed/popup. Evidence: `.cache/trim-sav-live/extended-20261022T113704Z-c76320d8-verification.json` and the complete records named there. All installations, parent records and settings are unchanged.

Do not automate that chooser. Instead, use a complete validated autosave checkpoint while the known default-save prompt is still open, without pressing any save button. The earlier event case `.cache/trim-sav-live/check-20261022T111745Z-91b05001` already preserved a recoverable current/total/autosave 200 checkpoint before any default-save response, supporting this design. File stability and structural validation still cannot prove atomic native saving or physics accuracy.

## Scope and files

| Path | Change and dependencies |
| --- | --- |
| `src/srim/trim_dialogs.py` | In event-resume save stages, strictly recognize the existing three-button Form1 as a wait condition, not a response. Ordinary resume saving stays unchanged. |
| `src/srim/trim_native.py` | Record that exact owned prompt; probe completed autosave arrays while it remains present; stop only the assigned job after stability. Uses existing probe/recovery/job APIs. |
| `src/srim/trim_execution.py` | Validate the new explicitly labeled autosave-stop evidence, verified event completion and recorded prompt, plus existing frozen recovery/count/integrity requirements. Depends on the existing strict classifier for prompt validation. |
| `test/srim/test_trim_dialogs.py`, `test/srim/test_trim_native.py`, `test/srim/test_trim_execution.py` | Add event-prompt waiting, no-save-command, owned-stop and negative evidence cases; retain ordinary save and config regression checks. |
| `README.md`, `doc/guides/trim-execution.md`, `doc/reference/trim-execution-format.md`, `doc/TRIM_DIRECTION.md` | Explain event autosave origin, exact evidence union, failure cases and measured verification limits. |

No new production module, class, public option or recovery algorithm. Preserve config qualification, all unrelated edits, historical records and the ordinary default-save route. Auxiliary artifacts remain under `.cache/trim-sav-live/`. No chooser selector, path reader, export command or focus/keyboard operation is added.

## Exact function changes

### `classify_trim_dialog(window: JSONObject, policy: JSONObject, state: JSONObject) -> JSONObject`

Keep pure behavior, input validation, pending-command suppression and strict known controls. In resume event mode only (input mode outside 1/2/3), an exact Form1 with the existing three button captions, IDs, visibility, enabled state and ownership, observed at `resume-event-save` or `event-checkpoint`, returns `{decision: "wait", reason: null, next_stage: "event-checkpoint", command: null}`. Never respond to the form in this branch. Event-completion acknowledgment still transitions to `resume-event-save`; fresh event completion stays unchanged. An ordinary `resume-save` retains its existing default-button response. Generic done, unknown stages, incomplete/mutated forms and directory choosers do not qualify.

### `_observe_trim_dialogs(process_id: int, runtime: Path, policy: JSONObject, state: JSONObject, elapsed_seconds: float, *, startup_state: dict[tuple[int, int], float] | None = None) -> JSONObject`

Keep existing inputs, owned-PID validation, result structure, pending-transition verification, bounded waits and error behavior. When the classifier returns the new event wait condition, set copied state.stage to `event-checkpoint`, initialize existing checkpoint_save_started only if null, and return an additional `checkpoint_prompt` containing a deep copy of the exact observed form. This clock is the autosave-readiness deadline in this branch, not evidence of a save command. Never append a posted-command event or call `_post_trim_command` for this prompt. Subsequent observations must revalidate the whole prompt. Other results do not contain checkpoint_prompt. No new state key is needed.

### `execute_trim_process(executable: Path, runtime: Path, *, hide_window: bool, timeout_seconds: float | None, stdout_path: Path, stderr_path: Path, cancel: Callable[[], bool] | None = None, dialog_policy: JSONObject | None = None) -> JSONObject`

Signature, side effects, filesystem errors and owned-job cleanup remain unchanged. At each monitor iteration reset a local current prompt to None; retain the observer's checkpoint_prompt only after current membership verification. Monitor all owned windows first. Unexpected popup, cancellation, timeout and monitor errors retain priority.

Probe in event-checkpoint only with a currently validated prompt, no pending command and verified event completion. Use existing `_probe_trim_saved_checkpoint` unchanged: full coherent version/mode-matching saved set, exact requested current/total/autosave, two identical source inventories at least 1.0 seconds apart. The existing checkpoint_save_seconds constant bounds readiness at 10.0 seconds after first verified prompt observation and never resets. If the prompt disappears without clean natural exit, do not authorize forced stopping from a stale prompt; reset the candidate and retain the original deadline. Unknown popups remain failures. Failure to obtain the complete saved set remains monitor-error; do not press Save to repair it.

On readiness, set termination checkpoint-stop and use existing checked owned-job termination, ten-second empty-job bound and permanent preservation. Ordinary checkpoint_stop evidence remains unchanged. Event autosave evidence has exactly:

```
save_verified: false
completion_verified: true
checkpoint_origin: "completed-autosave"
stable_seconds: finite number >= 1.0
sources: <existing sorted full flat checkpoint hash/size inventory>
prompt: <exact currently validated owned Form1 observation>
job_empty: bool
```

Do not describe autosave recovery as a verified default-save command. Retain actual exit code. Ordinary save probes still require checkpoint-saved after verified default saving. Generic done never authorizes forced stop. No native or checkpoint files are edited.

### `_trim_completion(inputs: ResolvedTRIMInputs, process_result: JSONObject, parsed: JSONObject, saved_values: JSONObject, continuation: JSONObject | None, *, availability: JSONObject | None = None) -> JSONObject`

Keep pure return shape `{complete, reason, checks}` and existing malformed-argument behavior. Add a separate event-autosave checkpoint-stop variant: require event mode, the exact evidence fields/values above, job_empty true, source hashes equal recovered frozen sources, all posted events verified with no errors/pending command, an event-completion acknowledgment into resume-event-save and final state event-checkpoint. Validate recorded prompt by calling the strict classifier with the sealed policy and final state, requiring its event-checkpoint wait result. Missing/unknown prompt or incorrect origin/save/completion labels fail completion. No default-save command is required or credited. Other result, request, count, autosave, histories and integrity checks remain independent.

Retain ordinary verified completion/default-save handling and clean natural event exit with a verified event acknowledgment. Do not reinterpret timeout, popup, cancellation or monitor failure as success. `_trim_completion` does not itself read files, control processes or publish metadata.

## Compatibility and schemas

Public signatures and defaults stay unchanged; no new config values or global limits. Nested policy remains schema 3 because fields and user-selected responses remain the same; private state gains only a stage string. Execution remains schema 3; document checkpoint_stop as the two explicit evidence variants. Recovered results, config sidecar, saved-values, availability and terminal inventories are unchanged. Existing evidence is never rewritten. A stopped working runtime is frozen and revalidated exactly as before, including live/permanent hash matching and config qualification.

## Verification and run budget

All functions/classes/tests retain complete annotations/docstrings and no implementation comments. New unittest methods use typed self and return None; no new production helper signatures are planned.

1. Dialog tests prove only the exact event save form yields wait; ordinary saving still responds; fresh, done, pending, unknown/mutated prompt, chooser and wrong mode remain rejected/waiting as appropriate.
2. Native tests verify one event-completion acknowledgment and zero save commands, current prompt ownership, repeated prompt revalidation, full checkpoint probes, stability, empty-job termination, original readiness deadline, disappearing prompt, incomplete/mutating arrays and unknown-popup/cancel/timeout precedence.
3. Completion tests reject spoofed origins, save_verified true in the autosave variant, missing completion, missing/altered prompt, wrong mode, pending/failed events, nonempty job and mismatched frozen hashes. Keep natural-exit and ordinary save tests, config protection and preserved event-prefix coverage.
4. Run the complete TRIM suite with warnings as errors and style/diff checks using the exact project interpreter `D:\projects\agentic_srim\.venv\Scripts\python.exe`.
5. After offline checks pass, run only one fresh Standard specified-ion-full 100-event parent and one child to 200 using the existing isolated run_case helper: maximum two native calculations, sequential, sixty-second attempt bounds, no retry. Verify event prefix and complete staged input unchanged; stopped recovery equal to direct frozen recovery; exact counts; current-prompt autosave evidence; matching live/permanent hashes; unchanged parent/installation/settings. Preserve all evidence and report any remaining failure.

## Approval request

Approve this event-only change and its two-calculation live check. It replaces pressing the event default-save button with explicit completed-autosave validation while the known prompt is open. It does not authorize chooser/export automation. Implementation requires this additional approval because the live behavior differs from the previously approved save sequence.
