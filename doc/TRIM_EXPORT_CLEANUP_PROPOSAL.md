# Remove abandoned resume-export code

Status: explicitly approved by the user on 2026-10-22; implementation completed, with verification recorded below. This is source cleanup, not completion of checkpoint-result integration. The user's current direction is `.sav` recovery; F exports and choosers remain abandoned.

## Resulting behavior and boundary

Fresh opt-in cascade handling remains. Existing report-only continuation remains. Automatic continuation that opts into dialog handling is rejected before staging/native launch until checkpoint-result/lifecycle integration is separately designed. It currently cannot complete verified result production, so cleanup must not leave it waiting indefinitely or imply success after saving alone.

Retain pure completion/save selectors for the future `.sav` route, with the final default-save response transitioning to done (meaning dialog sequence finished, not verified execution success). Do not add a new native-success outcome, guessed Exit command, forced-success termination or recovery substitution. The normal completion verifier still requires its existing native output checks.

## src/srim/trim_dialogs.py

Purpose remains pure policy validation, known dialog classification and history availability, using existing standard-library/project dependencies. No new classes/functions/imports.

Affected signatures stay:

```python
build_trim_dialog_policy(inputs: ResolvedTRIMInputs, control: JSONObject,
    cascade_output: str | None) -> JSONObject | None
initial_trim_dialog_state(policy: JSONObject) -> JSONObject
classify_trim_dialog(window: JSONObject, policy: JSONObject,
    state: JSONObject) -> JSONObject
advance_trim_dialog_state(state: JSONObject, observation: JSONObject,
    action_result: JSONObject | None) -> JSONObject
```

- Delete EXPORTS and resume_export_order. Policy schema becomes version 2 with exactly the current fields except resume_export_order; its other values/defaults remain. This is the nested dialog policy version; the enclosing opted-in execution control remains version 2. Historical policy version 1 with export fields is explicitly rejected, not silently repaired. No record migration or historical byte edits.
- build validates its existing inputs and returns independent policy/None without I/O. None keeps report-only behavior for fresh/resume. A non-None choice with control.kind=resume raises a clear ValueError that automatic continuation awaits checkpoint `.sav` integration and GUI exports were removed. Existing fresh engine/mode/collision validation remains.
- initial validates the exact version-2 policy and returns `{stage, pending_sequence, expected_count, limitations}`; remove completed_exports. Preserve kind validation for pure completion/save-selector tests, but it does not authorize execution of automatic continuations. Raise ValueError for malformed/legacy policies. No writes.
- classify removes Plots/F-button and export-gated menu Exit selectors and all resume-export/resume-exit states. Preserve known fresh dialogs and resume completion/default checkpoint-save matching. Change default-save's next_stage to done; counts/ownership/class/layout checks remain. Unknown export forms return reject. Return existing decision/command shapes without effects.
- advance keeps its copied-state/no-duplicate/no-retry semantics and limitation evidence, removing only any export-specific assumptions/docstring claims. No I/O. Existing invalid transition errors remain.

## src/srim/trim_native.py

Purpose remains owned native execution, startup filtering and optional known responses. Keep all signatures:

```python
_post_trim_command(process_id: int, window: JSONObject,
    command: JSONObject) -> JSONObject
_observe_trim_dialogs(process_id: int, runtime: Path, policy: JSONObject,
    state: JSONObject, elapsed_seconds: float, *,
    startup_state: dict[tuple[int, int], float] | None = None) -> JSONObject
execute_trim_process(executable: Path, runtime: Path, *, hide_window: bool,
    timeout_seconds: float | None, stdout_path: Path, stderr_path: Path,
    cancel: Callable[[], bool] | None = None,
    dialog_policy: JSONObject | None = None) -> JSONObject
```

- post accepts only the existing checked button notification. Delete menu-exit handling and reject any non-button action with ValueError before sending. Revalidation, parent/PID checks and OSError behavior remain. No focus/keyboard/chooser actions added.
- observe removes standard output-file polling, completed-export tracking, file_samples, Plots form discovery, F-button dispatch and export-driven main Exit. Retain startup filtering, known-dialog observations, command progression bounds, unknown-dialog refusal and evidence shapes. No filesystem reads are needed for exports; runtime path validation remains for compatibility. It never launches/kills or retries.
- execute validates opted-in policy before process creation; a valid resume policy raises the same clear unsupported-until-`.sav`-integration ValueError. This closes direct-native API bypasses of the preparation guard. Delete final file_samples cleanup. Keep existing jobs, timeout/cancellation, popup handling, raw evidence and startup state. Its final dialog_state omits completed_exports because the new policy has no export state. Other process report keys remain unchanged.

## src/srim/trim_runtime.py

Preserve the typed signatures and responsibilities of prepare_trim_simulation, prepare_trim_continuation, _stage_trim_runtime and verify_trim_runtime from the existing original proposal/API. No new parameters/defaults/functions. Their existing build_trim_dialog_policy calls propagate the new policy and early ValueError. Ensure rejection occurs before creating a runtime or launching anything. Existing legacy report-only controls remain version 1; opted-in fresh controls remain version 2 with nested policy version 2. Verification reconstructs that exact new policy. Existing prepared legacy opt-in policies fail preflight; old terminal evidence remains available through existing readers and is not rewritten.

## Tests

Existing constructors/helpers stay; every new/changed method has its first-statement docstring and full signature `(self: <existing test class>) -> None`. No new test helper/class.

- test/srim/test_trim_dialogs.py: remove EXPORTS import and policy field; POLICY uses nested schema version 2. Replace test_resume_save_export_exit_plan with TestTRIMDialogs.test_resume_checkpoint_save_without_exports, testing exact completion/save selections and final done state. Add test_legacy_export_policy_rejected and test_opt_in_resume_requires_checkpoint_integration. Verify no export keys in new state/policy and unknown Plots/chooser forms reject. Preserve fresh history/preflight tests.
- test/srim/test_trim_native.py: replace TestTRIMNative.test_resume_export_directory_guard with test_export_forms_never_responded, checking unknown owned Plots/chooser forms cause no command. Add test_opt_in_resume_rejected_before_launch and test_menu_exit_rejected, asserting no native launch/message. Adapt existing policy fixture use to the new nested version; preserve startup, ownership, timeout and cancellation tests. For the existing opted-in cancellation test use a fresh policy, preserving its cancellation-before-response purpose.
- test/srim/test_trim_runtime.py: add TestTRIMRuntime.test_opt_in_resume_rejected_without_staging using a sealed synthetic terminal parent/checkpoint and explicit choice, checking ValueError, unchanged parent and no new runtime. Keep report-only continuation tests. Fresh policy sealing/tamper tests use nested version 2.

No changes to trim_recovery.py, recovery fixtures, scientific parsers or trim_execution.py are proposed. No new fixture, dependency, setting, worker concurrency rule, lifecycle result schema or native run is included.

## Documentation

- doc/reference/trim-execution-format.md: document new nested policy/state shape, explicit old-policy rejection, continued report-only support and temporary automatic-continuation guard.
- doc/TRIM_POPUP_PROPOSAL.md: keep historical withdrawn-branch banner; point readers to the current cleanup and `.sav` direction, without reviving its export requirements.
- doc/TRIM_DIRECTION.md: replace the pending-legacy inventory with the actual removal/guard status after verified implementation; keep `.sav` result/lifecycle integration explicitly unfinished.
- doc/guides/trim-execution.md: explain automatic continuation awaits checkpoint-result integration and recovery data is not currently substituted for native outputs. Preserve existing report-only examples.

## Verification and cleanup

Run targeted dialog/native/runtime tests, then the full TRIM suite with warnings as errors via `D:\projects\agentic_srim\.venv\Scripts\python.exe`. Check syntax, docstrings, annotations, no implementation comments, documentation links/fences and whitespace. Search production code/tests for EXPORTS, resume_export_order, completed_exports, resume-export, file_samples, Plots/F dispatch and menu-exit to ensure export implementation is gone; negative tests/documentation may name rejected behavior. No scientific calculation or GUI experiment.

No automatic deletion of older unrelated evidence, recovery data or working-tree parser/cascade changes. The recent chooser diagnostic/proposals were already retired separately. Preserve the committed startup fix and timeout support. A commit/handoff may follow at the user's request; this proposal does not request a trial or change agent skills.

## Approval

Approval authorizes exactly this removal and temporary opt-in-continuation guard, including the policy-version decision. It does not authorize dropping `.sav` integration from the project goal. That integration needs a separate complete proposal before code changes. Do not replace the removed GUI route with another export experiment.

## Implementation verification — 2026-10-22

Completed the approved removal and guards. All 43 targeted tests passed (11 dialog, 21 native-runner, 11 runtime), followed by all 127 TRIM tests with warnings as errors. Six changed Python files passed syntax, module/class/function docstrings, complete argument/return annotations and absence-of-comments checks. Production searches found no export ordering/state, file-sampling export progression or menu-exit implementation. Names retained in negative tests assert rejection/absence. Documentation links/fences and Git whitespace checks passed.

The sealed-parent test verifies automatic-continuation rejection creates no staging directory or child input record, launches no process and changes no parent bytes. Direct-native rejection occurs before job/process creation. Pure completion/default-save selectors, report-only continuation, fresh responses, startup filtering, optional deadlines and recovery regression tests remain covered. No native process or GUI experiment was run for this cleanup. No `.sav` pipeline integration or successful automatic continuation is claimed. The user subsequently requested a handoff and local commit; the [cleanup handoff](TRIM_CLEANUP_HANDOFF.md) records that commit's narrower boundary and standalone verification.
