# SRIM-2008 specified-ion-full completion prompt amendment

Status: approved by the user's “ok” after the requested explanation of this amendment and its two additional checks. Implemented and verified: 151 regressions pass, and both additional native checks match expectations. The original twelve-case run and its two failed prompt records remain unchanged. Final evidence is recorded in `TRIM_DIRECTION.md` and `.cache/trim-sav-live/checkpoint-first-verification.json`.

## Observed discrepancy

The first 2008 specified-ion-full production child reached 200 with complete recovered results, but correctly failed `popup` without sending commands. Its exact dialog is `End of calculation`, with the existing expected-count Yes=Save/No=Increase text and buttons 6/7. The approved shared design restricted that prompt to ordinary input modes 1/2/3. The 2008 event input mode is 5. This distinction was already present in the no-response investigation and was missed when writing the shared design.

Evidence: `.cache/trim-sav-live/checkpoint-first-20261022T122100Z-9672333a/report.json` and `.cache/trim-sav-live/no-save-20261022T115211Z-dc08d244/report.json`. Standard/Pro specified-ion-full use `End of TRIM.DAT calculation`; their selectors remain unchanged. No historical record is edited or reclassified.

## Exact implementation change

Only `src/srim/trim_dialogs.py` changes production behavior, in existing pure `classify_trim_dialog(window: JSONObject, policy: JSONObject, state: JSONObject) -> JSONObject`. Its input, return schema, validation, errors, dependencies, ownership checks and lack of side effects remain unchanged.

For **resume + engine_family 2008 + input_mode 5 only**, recognize the existing exact `End of calculation` Yes/No completion as `inspect-checkpoint`, command null, next stage `checkpoint-check`. Permit this at the already approved completion stages (running/non-recoil, resume-complete, checkpoint-check). Exclude this combination from the event-specific `End of TRIM.DAT calculation` selector. Neither `checkpoint-save-required` nor any save form is authorized for input mode 5, even if a forged fallback flag is true. Ordinary modes 1/2/3 retain their existing fallback. All fresh behavior remains unchanged. Other 2008 modes are not broadened based on this one measured mode.

Specifically, within the existing completion-rule block:

1. Add `and not (policy["kind"] == "resume" and policy["engine_family"] == "2008" and policy["input_mode"] == 5)` to the event-specific rule condition.
2. Expand the ordinary-title rule condition from resume and mode in 1/2/3 to resume and (mode in 1/2/3 or family 2008 and mode 5).
3. Its fallback-stage guard becomes `stage != "checkpoint-save-required" or policy["input_mode"] in (1, 2, 3) and state.get("checkpoint_fallback_attempted") is True`.

No new function, class, policy field, schema version, timing constant, request default, file format or lifecycle stage. Nested policy 4 and resumed report 4 remain; the current uncommitted version-4 implementation is still under verification. `trim_native.py`, `trim_execution.py`, recovery, config qualification and all preservation/integrity rules remain unchanged. The event branch still stops without response if ready, and fails checkpoint-required after ten seconds if unavailable. No chooser/export/keyboard/focus behavior.

## Regression files and contracts

- `test/srim/test_trim_dialogs.py`: update `test_checkpoint_inspection_across_engine_modes(self: TestTRIMDialogs) -> None` to use the observed 2008-mode-5 title while retaining semantic-mode fallback restrictions. Add `test_2008_event_prompt_replay(self: TestTRIMDialogs) -> None` using an in-memory exact observation matching captured title/text/control metadata, verifying inspection at each allowed stage, wrong title/count/control/ownership rejection, no fallback and unchanged fresh behavior. No file writes; returns None; assertion errors report regressions.
- `test/srim/test_trim_native.py`: add `test_2008_event_observation_without_response(self: TestTRIMNative) -> None`, mocking the existing window reader/poster with that owned prompt. Verify current completion evidence, unchanged initial deadline across repeats, zero command events/post calls and rejection of an unauthorized save form. No native process or external write; returns None; failures are unittest assertions.
- `test/srim/test_trim_execution.py`: add `test_2008_event_stop_prompt_replay(self: TestTRIMExecution) -> None`, using policy 4, a complete recovered-count envelope and completed-checkpoint evidence to verify success without commands and failure for mutated prompt or explicit-save origin. No native process or file writes; returns None; failures are unittest assertions.

All methods retain required first-statement docstrings and annotations, including self. No implementation comments. Existing test fixtures and historical records remain unchanged.

README, `doc/guides/trim-execution.md`, `doc/reference/trim-execution-format.md` and `doc/TRIM_DIRECTION.md` will state this narrow version/mode selector distinction and measured results. The shared proposal will reference this approved amendment if approval is given. No unrelated documentation or code is changed.

## Verification and additional native budget

Run targeted dialog/native/execution evidence tests, the affected-file annotation/docstring/comment check, and all TRIM regressions with warnings as errors using `D:\projects\agentic_srim\.venv\Scripts\python.exe -W error -m unittest discover -s test/srim -p 'test_trim*.py' -q`.

Only after the original twelve cases finish and offline checks pass, create **at most two additional new 2008 specified-ion-full children** from the same unchanged 100-event parent, one autosave 200 and one autosave 10000, total 200 and timeout 60 seconds each. No new parent, concurrent native run, retry of an existing record, source-installation/settings change, or other version/mode check. Use `.cache/trim-sav-live/verify_checkpoint_first_2008_event.py`, a copy of the approved harness restricted to this single parent/mode and these two autosaves; main retains typed `main() -> None`, no arguments, same report/integrity/error contracts, expected case count two.

Expected outcomes: autosave 200 succeeds with completed-checkpoint origin, zero responses, full recovery/live/permanent hashes, empty owned job and unchanged config; autosave 10000 fails checkpoint-required after bounded inspection with zero responses and no chooser. Verify both staged event inputs/prefixes, parent, installations and settings unchanged. Preserve all original failures and new evidence. Do not retry or broaden if either check fails.

Approval is required because this corrects an explicitly approved selector restriction and exceeds the original twelve-attempt budget by two. It is required by the repository's `AGENTS.md` section “Proposal and approval before implementation,” not by automatic approval review.
