# TRIM config-integrity and event-save amendment

Status: proposed; explicit approval required before implementation. This amends the approved checkpoint pipeline design, not recovery mathematics.

## Evidence and scope

The three live cases in `.cache/trim-sav-live/extended-20261022T111648Z-86210dcc.json` reached saved current/total/autosave 200. Ordinary 2008 and 2013 Pro failed only because native continuation rewrote staged colour/help-text config fields. Standard specified-ion-full verified its event-complete acknowledgment, then failed on a default-save form because the classifier had entered `done`. The recorded parents, installations and settings are unchanged.

Implement two narrow fixes: qualify validated presentation changes to staged `Data/TRIM.cfg` in resumed runs, and recognize event completion followed by default saving. Fresh config checks stay byte-exact. Recovery algorithms, source preservation, owned-job rules, precision warnings, deadlines, histories, event-prefix validation and all other input-integrity checks remain. No GUI result exports or chooser operations are included. Preserve existing unrelated local changes and historical failed records. Do not commit, delete artifacts or alter installations/settings.

## Files, responsibilities and dependencies

| Path | Proposed contents and relationships |
| --- | --- |
| `src/srim/trim_config.py` (new) | Pure strict config parser and before/after assessment described below. Depends on standard-library hashing/regex, `JSONObject`, and `trim_dialogs.ENGINES`. No filesystem/process access or classes. |
| `src/srim/trim_execution.py` | Import config assessment; publish a config-integrity sidecar for resumed attempts reaching integrity verification; qualify only this exact file when permitted. Update `_trim_completion` to accept verified event-save sequences and retain verified event natural exit. |
| `src/srim/trim_dialogs.py` | Correct event-resume completion stage and reuse the existing strict default-save selector. No new command or user option. |
| `test/srim/test_trim_config.py` (new) | Pure config tests covering observed layouts/rewrites, protected values, unknown layouts and unsupported engines. |
| `test/srim/test_trim_execution.py` | Verify terminal acceptance/rejection, sidecar contents, untouched evidence, ordered event-save completion and continued fresh byte-exact enforcement. |
| `test/srim/test_trim_dialogs.py` | Verify fresh versus resumed event completion, exact default-save matching, mode/stage restrictions and duplicate/unknown rejection. |
| `test/srim/test_trim_native.py` | Verify the corrected event route uses existing pending-save, readiness and owned-job shutdown behavior. No production native-controller edits are required. |
| `README.md`, `doc/guides/trim-execution.md`, `doc/reference/trim-execution-format.md`, `doc/TRIM_DIRECTION.md` | Document qualification rules, sidecar schema, corrected event path, regression/live results and remaining evidence limits. Preserve existing local documentation edits. |

No changes to runtime preparation, shared metadata, queue APIs, recovery, native text parsing, configuration settings or historical fixtures are proposed. Auxiliary extraction, test fixtures derived from local native evidence and live reports belong under `.cache/trim-sav-live/`; new tests use cache-only temporary directories. Existing native evidence remains immutable.

## New pure config functions

### `_parse_trim_config(contents: bytes) -> JSONObject`

Private function in `trim_config.py`. Input is one complete config byte string. Return parsed colour fields, label size, secondary-recoil limit and help text; do not mutate input or perform I/O. Wrong types, unknown layouts, malformed integers or unrecognized text raise `ValueError` with the offending section/field. ASCII decoding errors become `ValueError`.

Support exactly the observed 31-line layout after normalizing CRLF/LF endings; a single terminating newline is optional. Reject bare CR, extra lines, non-ASCII/control characters and unknown headings. Require these exact headings/blank positions (zero-based):

- 0: `----TRIM.cfg : TRIM Color Configuration----`
- 2: `[Ion Colors - MOVE, STOP]`
- 5: `[12 Recoil Colors - MOVE, STOP]`
- 19: `[Distribution Plot Labels - SIZE, COLOR]`
- 22: `[4 Distribution Plot Colors]`
- 25: `[Plot Background, Outer Box, Axes, Layer Lines, Layer Names]`
- 28: `Maximum number of secondary recoils (default=20000)`
- Blank lines: 1, 4, 18, 21, 24 and 27.

Numeric lines contain decimal digits separated/padded by ASCII spaces only: line 3 has two colours; lines 6–17 each have the exact sequential entry index 1–12 followed by two colours; line 20 has label size and colour; line 23 has four colours; line 26 has five colours; line 29 has the secondary-recoil limit. Reject extra/missing tokens and non-integer tokens. Colours must be in the observed 24-bit domain 0–16777215. Label size and recoil limit are unsigned integer tokens; this parser does not invent additional native acceptance bounds.

Line 30 must be exactly either `Note: 100k recoils requires extra 2MB of memory, 1M recoils requires 35MB.` or `Note: 100k recoils requires 2MB of memory, 1M recoils requires 35MB.` No other help-text changes qualify.

Return exactly `{colours, label_size, maximum_secondary_recoils, help_text}`. Colours is a mapping of stable field names to integers: `ion.move`, `ion.stop`, `recoil.01.move`/`.stop` through `recoil.12.move`/`.stop`, `label.colour`, `distribution.1`–`.4`, and `plot.background`, `plot.outer_box`, `plot.axes`, `plot.layer_lines`, `plot.layer_names`. The other values are integers and the original validated help-text string. Whitespace/newline normalization is used only for comparison; source bytes are never rewritten.

### `assess_trim_config_change(before: bytes, after: bytes, *, engine_sha256: str) -> JSONObject`

Public pure function in `trim_config.py`. Inputs are sealed initial config bytes, stopped preserved config bytes and the pinned executable digest. Return the config-integrity report below; do not read/write files, modify inputs or change lifecycle state. Nonbytes or a malformed digest (not exactly 64 lowercase hex digits) raise `ValueError`.

1. Hash both inputs. Byte-identical inputs return `status="equal"`, empty changes, null reason and null protected-values object. They need no layout interpretation; this preserves existing unchanged synthetic/unknown config behavior.
2. If bytes differ, qualification is allowed only for a digest in the current pinned `ENGINES` allowlist. Unknown engines return `different` with an explicit reason.
3. Parse both using `_parse_trim_config`. Parser errors become `different` reports with a useful reason and no qualification; they do not escape as malformed output exceptions.
4. Require identical `label_size` and `maximum_secondary_recoils`. Either change returns `different`, even if colours also changed. Preserve both observed protected values in the report. Do not equate installed 20,000 and 200,000 limits: compare each run with its own sealed initial value.
5. Otherwise accept changed colours, either allowlisted help-text spelling, and numeric-line spacing/newline differences as `qualified`. Unknown headings, extra content and changed protected fields never qualify. A purely formatting rewrite is explicitly reported as such.

Reports have exactly:

```
schema_version: 1
engine: "trim"
path: "Data/TRIM.cfg"
engine_sha256: str
status: "equal" | "qualified" | "different"
before: {sha256: str, size_bytes: int}
after: {sha256: str, size_bytes: int}
protected_values: null | {
  label_size: {before: int, after: int},
  maximum_secondary_recoils: {before: int, after: int}
}
changes: [{field: str, before: int | str, after: int | str,
           classification: "presentation" | "protected" | "formatting"}]
reason: str | null
```

Sort semantic changes by field name. Help-text classification is presentation. When bytes differ but parsed objects are equal, add one formatting change with field `serialization`, before/after source hashes and classification formatting. Invalid/unknown formats may have an empty changes list; reason explains failure. `equal` and `qualified` have null reason; `different` has a nonempty reason.

## Execution integration

Keep the complete existing typed signature:

`run_trim_simulation(paths: ProjectPaths, config: Config, simulation_id: str, runtime: Path, *, timeout_seconds: float | None = 60.0, cancel: Callable[[], bool] | None = None) -> JSONObject`

Existing arguments, return metadata, worker lease, process ownership, preservation, failure priority and filesystem/exception behavior stay unchanged. For resumed attempts reaching verification, read the original config from the sealed `input/installation/Data/TRIM.cfg`, the observed config from `output/raw/runtime/Data/TRIM.cfg`, and the executable digest from verified pinned input bytes. The original must match the initial runtime-manifest entry's hash/size; a mismatch remains an integrity failure, never a new baseline.

Write the assessment exclusively to `output/parsed/config-integrity.json`, whether equal, qualified or different. Both original and final config bytes remain in their existing preserved locations. The existing manifest loop may waive a byte mismatch only for exact canonical `Data/TRIM.cfg`, only on resume, and only when this report is qualified. Different reports remain failed/integrity. All other runtime inputs retain exact hash/size checks. Fresh execution retains existing behavior and produces no new config sidecar. Preflight/launch/preservation failures may have no sidecar. A missing/unreadable config remains an I/O failure with existing finalization/evidence handling.

Do not add fields to `execution.json` or change its schema. The additional parsed sidecar is covered by the existing terminal output inventory. Availability, recovered-results and saved-values schemas remain unchanged. No reader migration or historical rewriting is needed.

## Correct event completion/save handling

Keep `classify_trim_dialog(window: JSONObject, policy: JSONObject, state: JSONObject) -> JSONObject` and its pure input/output/validation/error contracts.

An exact `End of TRIM.DAT calculation` / `Calculation Complete (<expected count> Ions)` / single OK response has these destinations:

- Fresh event policies: existing `done` behavior.
- Resumed event policies (native input modes outside ordinary 1/2/3): new `resume-event-save` stage.
- Ordinary resumed modes: do not recognize the event-complete dialog as their completion route.

Allow the existing default-save `ThunderRT5Form`/`Form1` selector in `resume-save` or `resume-event-save`. It still requires exactly the three known buttons with correct IDs, ownership and state, and sends only default button 3. It transitions to pending `checkpoint-saved`; only verified disappearance clears pending status and starts readiness monitoring. Generic `done` cannot recognize save forms or authorize forced termination. Unrecognized/changed dialogs remain failures.

No new policy fields, public defaults, UI commands, timing constants or schema versions are needed: nested policy schema 3 remains the same, with corrected handling of an observed native sequence. State/event reports can contain the new stage string. Existing state advancement and native controller already verify posted responses and gate readiness on `checkpoint-saved`.

Keep `_trim_completion(inputs: ResolvedTRIMInputs, process_result: JSONObject, parsed: JSONObject, saved_values: JSONObject, continuation: JSONObject | None, *, availability: JSONObject | None = None) -> JSONObject` and its pure contracts. Recognize an ordered verified ordinary completion-to-save pair, or an ordered verified event completion-to-`resume-event-save` followed by its default save-to-`checkpoint-saved` pair. Require no failed/pending events, correct event-mode identity and final `checkpoint-saved`. Continue checking process termination, recovery, exact counts, request agreement, hashes and histories independently. Reordered or incomplete sequences cannot pass.

Retain natural event-mode exit if it occurs after verified event completion: require clean owned exit code 0, final `done`, no popup/monitor/pending failure and the verified event-completion event (new `resume-event-save` or earlier `done` evidence). Natural exit still requires the stopped checkpoint and all normal result checks; it is not forced merely because the completion acknowledgment was posted. If the save form appears, it must be handled and verified through the new route.

## Regression and live verification

All code, including tests, follows module/class/function docstring rules, explicit argument/return annotations and no implementation comments. No new test helper signatures are needed: new unittest methods use `(self: <test class>) -> None`, take no other inputs, return None and perform only cache-local I/O/mocked native calls.

- Config tests: byte-identical unknown format; both observed changed configs; every colour group; allowlisted help-text and formatting changes; unchanged differing per-installation recoil limits; changed recoil limit/label size; mixed protected/presentation mutation; unknown engine, headings, extra/truncated lines, bad entry indices, nonnumeric/out-of-range colours and invalid encoding. Assert exact source hashes, protected values, status and reasons.
- Execution tests: resumed qualified config change can succeed with all other checks satisfied; protected/unknown config changes fail integrity; mismatched baseline fails; fresh changed config still fails; sidecar and original/final bytes are sealed; other changed engine/input files remain failures. Extend existing real-preservation/mocked-process tests rather than bypassing integrity.
- Event tests: verified completion/save/probe/stop sequence; save form rejected in running/fresh/done and accepted only after correct resumed completion; pending or failed/reordered commands cannot succeed; event completion without save cannot permit checkpoint-stop; clean natural event exit remains supported; ordinary/fresh behavior and unknown-popup/cancel/timeout failure semantics remain.
- Run the complete TRIM suite with warnings as errors using `D:\projects\agentic_srim\.venv\Scripts\python.exe -W error -m unittest discover -s test/srim -p 'test_trim*.py' -q`, plus focused checks when repairing a relevant failure. Check the final diff, annotations/docstrings and report documentation.
- After offline checks pass, rerun exactly the three previously failed live cases via `.cache/trim-sav-live/run_extended_checks.py`: 2008 ion-full, 2013 Pro ion-full, Standard specified-ion-full. Each uses fresh 100-to-200 records, one worker per isolated cache project, sequential execution and 60-second attempt deadlines: at most six native calculations. Keep installations/settings/parents unchanged, recover only frozen preserved checkpoints and verify event prefix/input preservation. No old record reuse, deletion or success overrides. Report any remaining failure without silently broadening the design.
- Preserve new evidence and update current-direction documentation only with observed results. Do not claim general scientific accuracy, atomic native saving or universal mode coverage.

## Approval

Approve this complete amendment, including the strict presentation-only config qualification, unchanged protected settings, corrected event save stage, config sidecar, regression tests and six-calculation maximum live rerun. Implementation starts only after that approval; any design change requires a further explicit amendment.
