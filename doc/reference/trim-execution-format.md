# TRIM execution APIs and record formats

[Execution guide](../guides/trim-execution.md) · [Input format](trim-input-format.md) · [Continuation decision](../decisions/trim-continuation.md)

This reference covers the Windows runner, continuation, in-memory scheduling, preservation and summary/depth-profile parsing. Existing SR signatures and legacy records remain supported. Import modules from the repository root. New APIs use existing `Config`, `ProjectPaths`, `CreatedDirectory`, `JSONObject`, `ResolvedTRIMInputs` and cleanup report types.

## Public interfaces

```python
load_trim_input_bundle(source: Path) -> ResolvedTRIMInputs
prepare_trim_simulation(paths: ProjectPaths, config: Config,
    created: CreatedDirectory, worker: CreatedDirectory,
    inputs: ResolvedTRIMInputs, *, cascade_output: str | None = None) -> Path
prepare_trim_continuation(paths: ProjectPaths, config: Config,
    created: CreatedDirectory, worker: CreatedDirectory, parent_id: str, *,
    total_ions: int, autosave_ions: int,
    events: list[JSONObject] | None = None,
    cascade_output: str | None = None) -> Path
verify_trim_runtime(paths: ProjectPaths, simulation_id: str,
    runtime: Path) -> ResolvedTRIMInputs

run_trim_simulation(paths: ProjectPaths, config: Config,
    simulation_id: str, runtime: Path, *, timeout_seconds: float | None = 60.0,
    cancel: Callable[[], bool] | None = None) -> JSONObject

PreparedTRIMTask(simulation_id: str, runtime: Path)
TRIMTaskResult(task: PreparedTRIMTask, metadata: JSONObject | None,
    error: Exception | None, elapsed_seconds: float)
TRIMBatchResult(task_results: tuple[TRIMTaskResult, ...],
    cleanup: BatchCleanupReport, elapsed_seconds: float)
run_trim_batch(paths: ProjectPaths, config: Config,
    tasks: Sequence[PreparedTRIMTask], *, max_workers: int,
    timeout_seconds: float | None = 60.0,
    worker_timeouts: Mapping[str, float | None] | None = None,
    cleanup: bool = False) -> TRIMBatchResult

parse_trim_summary(contents: bytes) -> JSONObject
parse_trim_profile(filename: str, contents: bytes) -> JSONObject
parse_trim_checkpoint(contents: bytes, trim_input: bytes) -> JSONObject
compare_trim_saved_values(checkpoint: JSONObject, inputs: ResolvedTRIMInputs,
    *, resumed: bool = False) -> JSONObject
parse_trim_outputs(files: Mapping[str, bytes],
    request: JSONObject | None = None) -> JSONObject
parse_trim_checkpoint_results(files: Mapping[str, bytes]) -> JSONObject

preserve_trim_runtime(paths: ProjectPaths, simulation_id: str, runtime: Path) -> None
verify_preserved_trim_runtime(paths: ProjectPaths,
    simulation_id: str, runtime: Path) -> None
parse_trim_directory(source: Path, destination: Path, *,
    request: JSONObject | None = None) -> Path
cleanup_trim_batch(paths: ProjectPaths,
    worker_paths: Sequence[Path]) -> BatchCleanupReport
```

Preparation/loading/verification are in `trim_runtime`; execution in `trim_execution`; task classes and scheduling in `trim_queue`; native parsers/comparison in `trim_outputs`; checkpoint result adaptation in `trim_results` using `trim_recovery`; filesystem preservation/export/cleanup in `trim_storage`. The native implementation boundary is `trim_native.execute_trim_process`; ordinary callers should use the lifecycle runner.

Paths supplied for bundles, runtimes and standalone imports are absolute and link-free. Preparation requires an empty simulation allocation and a valid worker allocation. Bundle loading validates the exact export manifest and independently replays pinned evidence. It does not read installations or current aliases. Metadata initialization/start also verify referenced immutable project resources. Preparation neither reruns material resolution nor launches TRIM.

Invalid caller types/layouts/state, collisions and worker contention can raise before execution. Expected native/preflight/timeout/popup/parse/substitution failures return terminal failed metadata. Filesystem/publication errors propagate with retained artifact notes after best-effort finalization. Interruption terminates owned native processes, attempts preservation and failure publication, then re-raises. Timeouts are None (unlimited) or positive finite int/float values excluding bool and apply per native attempt; queue waiting, preservation and parsing are separate. The default remains 60 seconds. Batch worker_timeouts maps exact participating worker directory names to timeout overrides; absent names use the batch timeout_seconds. Unknown keys, invalid mappings and invalid values raise ValueError before dispatch. Overrides are copied before dispatch without modifying the caller mapping. Each task starts a fresh clock, including successive tasks on one worker. Timeout affects only the owned task; other workers and subsequent queued tasks continue. Unlimited disables only the overall execution deadline; cancellation and monitoring remain active. Batches still wait for all active tasks. Initial window visibility follows `config.hide_trim_window`.

## Runtime and provenance

Runtime path is `worker/worker_N/trim/<simulation_id>` under `ProjectPaths.workers`. Initial fresh files are exactly TRIM.exe, TRIM.IN, TRIMAUTO, optional TRIM.DAT, and Data/{TRIM.cfg,SCOEF03.dat,VERSION}, plus empty SRIM Outputs and SRIM Restore directories. Fresh TRIMAUTO is ASCII `1\r\n\r\n`. Resume adds the complete copied `checkpoint/` tree and uses `2\r\ncheckpoint\\\r\n`; native autosaves go to SRIM Restore.

Permanent record root is `ProjectPaths.simulation_memory/<simulation_id>`. Input-only bundle documents/resources/installation/dependency snapshots remain under `input/`. Additional files are:

| Path | Contents |
| --- | --- |
| input/engine/TRIMAUTO | Exact automatic fresh/resume control bytes |
| input/execution/runtime.json | Initial runtime file hashes and sizes |
| input/execution/control.json | Fresh/resume identity, counts and explicit edit provenance |
| input/continuation/parent.json | Parent identity, sealed inventories and checkpoint atomicity limitation |
| input/continuation/checkpoint/… | Complete staged continuation bytes, including extended TRIM.DAT when applicable |
| output/raw/stdout.bin; stderr.bin | Exact binary process diagnostics, when launch reached diagnostic creation |
| output/raw/execution.json | Process, popup, completion and failure evidence |
| output/raw/runtime/… | Entire stopped runtime, including native root outputs and all subdirectories |
| output/parsed/results.json | Aggregate summary/profile/checkpoint parsing and consistency report |
| output/parsed/saved-values.json | Requested/saved comparison evidence |

Shared metadata keeps its existing `created → running → succeeded/failed` lifecycle and `{message, exit_code}` failure object. Expected preflight failure may go directly from created to failed. New TRIM inputs are fully sealed at initialization and checked before start. Terminal records remain immutable through lifecycle APIs. Continuation is the existing `{parent_id, files}` metadata object, where files inventory `input/continuation/`.

JSON uses UTF-8, sorted keys, two-space indentation, finite JSON values and terminal LF. Inventory entries are `{path, sha256, size_bytes}`, sorted by forward-slash relative path. Case-insensitive collisions, links and unsafe names are rejected. A manifest does not inventory itself. Source numeric tokens remain strings; counts, indices, atomic numbers, process identifiers and byte sizes are integers. Null means unavailable, never zero.

## Report schemas

The principal report objects are listed below. Nested native-parser fields are defined in [trim_outputs.py](../../src/srim/trim_outputs.py), runtime controls in [trim_runtime.py](../../src/srim/trim_runtime.py), and resumed result adaptation in [trim_results.py](../../src/srim/trim_results.py).

| Object | Exact top-level keys |
| --- | --- |
| runtime.json | schema_version, files |
| control.json | schema_version, engine, kind, parent_id, initial_count, total_ions, autosave_ions, event_prefix_sha256, edits |
| execution.json | schema_version, engine, simulation_id, runtime_path, timeout_seconds, stage, process, completion, failure |
| results.json | schema_version, engine, summary, profiles, checkpoint, sources, issues, consistency, complete |
| Summary | source, version, name, projectile, layers, target_energies, counts, statistics, unavailable |
| Profile | source, version, calculated_ions, title, columns, rows, statistics, notes |
| Textual checkpoint | sources, version, name, projectile, counts, mode, flags, atoms, layers, native_input, count_line |
| saved-values.json | schema_version, engine, checks, differences, unverified |
| Standalone manifest | schema_version, engine, kind, files |

Report-only `schema_version` is 1; reports carrying engine use `"trim"`. `control.kind` is fresh/resume. Resume total_ions is an explicit absolute final count greater than saved current count; autosave is explicit. Count edits preserve original checkpoint current count and accumulated arrays. All prior event records stay byte-equivalent and ordered; only new structured events may be appended.

Fresh and resumed runs may explicitly opt into reviewed responses through `cascade_output="none"`, `"statistics"` or `"detailed"`, subject to existing engine/mode/collision checks. Their execution control is version 2 with nested `dialog_policy` schema 4. Its exact fields are `{schema_version, engine_sha256, engine_family, input_mode, kind, initial_count, expected_count, autosave_ions, cascade_output, checkpoint_directory, ascii_directory, response_transition_seconds, checkpoint_save_seconds, checkpoint_stable_seconds, shutdown_seconds}`. Fixed directories remain `SRIM Restore` and `SRIM Outputs`. Timing constants are respectively 2.0, 10.0, 1.0 and 10.0 seconds. Counts are exact integers; existing autosave integer acceptance is retained. Final `dialog_state` is `{stage, pending_sequence, expected_count, limitations, checkpoint_probe, checkpoint_save_started, checkpoint_completion, checkpoint_started, checkpoint_fallback_attempted}`. Completion is null or `{process_id, window, elapsed_seconds}`; initial inspection/form-wait time is null or a finite nonnegative number, and the fallback flag starts false. A non-null checkpoint probe is `{elapsed_seconds, sources}`; save-start time is measured from verified save-form disappearance. Legacy nested policies 1/2/3 are rejected with instructions to prepare a new runtime. Historical records are not rewritten.

Resumed execution selects checkpoint recovery explicitly for report-only and automatic runs. Default `None` keeps report-only popup handling. Automatic ordinary and event completion enters `checkpoint-check` without a command. Two valid full snapshots at least one second apart, while the current exact completion prompt remains verified, permit owned-job stopping. Initial readiness expires after ten seconds. Only unavailable ordinary state authorizes one `checkpoint-save-required` Yes response into `resume-save`, followed by the verified default-save command into `checkpoint-saved`. Its form wait, default-save transition and distinct post-save readiness each have ten-second bounds; the Yes transition has two seconds. Event policies never acknowledge completion or accept a save form. Missing final event checkpoints require an explicit final-covering autosave schedule. Invalid state fails monitoring; unstable state and vanished prompts fail readiness without fallback. Unknown popups, cancellation and timeout take priority, including after a probe. Natural clean empty-job exit remains valid with complete frozen results and no pending/error evidence. Generic `done` never authorizes forced stopping.

For resumed attempts reaching integrity verification, `output/parsed/config-integrity.json` records the assessment of sealed `input/installation/Data/TRIM.cfg` against preserved `output/raw/runtime/Data/TRIM.cfg`. It has exactly `{schema_version, engine, path, engine_sha256, status, before, after, protected_values, changes, reason}`. Version is 1, engine trim, path `Data/TRIM.cfg`, and status equal/qualified/different. Before/after are `{sha256, size_bytes}`; protected_values is null or `{label_size: {before, after}, maximum_secondary_recoils: {before, after}}`. Changes are `{field, before, after, classification}` with presentation/protected/formatting classifications, sorted semantic field names, and a serialization/hash entry for formatting-only changes. Reason is null for equal/qualified and nonempty for different. The pure `trim_config.assess_trim_config_change(before: bytes, after: bytes, *, engine_sha256: str) -> JSONObject` performs this assessment without I/O.

The resumed engine-family 2008/input-mode 5 selector specifically expects `End of calculation`, exact expected-count Yes=Save/No=Increase text and Yes/No button IDs 6/7. It returns inspect-checkpoint with command null at running, resume-complete or checkpoint-check, and rejects checkpoint-save-required even with the fallback flag set. It does not accept the event-specific title for this combination. Standard/Pro event selectors and all fresh behavior remain unchanged. This observed prompt difference grants no event save action and changes no version-4 schema or timing.

Byte-equal config needs no layout interpretation. A changed config qualifies only for the pinned engine allowlist and the strict observed 31-line ASCII layout, preserving both label size and secondary-recoil limit. Known colour changes, the two observed `requires [extra] 2MB` help-text spellings and numeric-line spacing/LF/CRLF differences are permitted. Unknown headings/content, malformed colours, protected changes and baseline/manifest mismatch fail integrity. The original and final bytes remain unmodified. Only exact canonical `Data/TRIM.cfg` on resumed execution can receive this qualification; fresh config and all other input files remain byte-exact. The sidecar uses existing output inventory sealing without altering execution/result/availability schema versions. Attempts failing before verification may lack it.

Report-only process evidence is `{pid, exit_code, elapsed_seconds, termination, popups, monitor_errors}`. Opted-in results additionally contain `dialog_events` and `dialog_state`, recording verified reviewed button responses. Opted-in resume also has `checkpoint_stop`, initially null, or exactly `{origin, completion_observed, completion_prompt, save_verified, stable_seconds, sources, job_empty}` after a controlled stop. Origin is `completed-checkpoint` with save_verified false, or ordinary `explicit-save` with save_verified true. Completion_observed is true; completion_prompt retains the first full verified observation. Stable_seconds is finite and at least 1.0. Resume also has `checkpoint_failure`, null or exactly `{status, phase, message}`: status unavailable/waiting/invalid, phase initial/save-transition/post-save, and nonempty reason. Each source is `{path, sha256, size_bytes}` using original-case flat checkpoint names, sorted by path. Termination is exit, checkpoint-stop, checkpoint-unavailable, popup, timeout, cancelled, monitor-error or launch-error. `checkpoint-stop` retains the actual nonzero exit code and is accepted only with strictly classified completion evidence, the origin-appropriate command sequence, stable hashes, empty job and matching permanent checkpoint. Direct stops require zero completion/Save commands; explicit-save requires the ordered verified ordinary Yes/default-save pair. Popup records include handle, owner_handle, pid, class, title, visible, enabled, rectangle, controls, detection_reason and capture_errors. Report-only execution sends no response; opted-in execution sends only checked button notifications, never a menu Exit, focus or keyboard action. Windows job membership protects ownership during inspection; the child starts suspended and is assigned to a kill-on-close job before it resumes. Shutdown verifies the entire job, including descendants after root exit. If quiescence cannot be established, execution raises with failure evidence and does not preserve a supposedly frozen copy. Polling and bounded text capture cannot guarantee observation of arbitrarily brief dialogs; inaccessible evidence is recorded conservatively.

During the first five seconds of native execution, an unreadable startup candidate may be re-observed for less than 0.5 seconds from its first observation. It must be an owned, visible, enabled `ThunderRT5Form` at rectangle `[645, 406, 891, 457]`, with a blank title, text-capture errors and no captured controls except empty `ThunderRT5PictureBox`/`Static` controls. This narrow rule applies to report-only and opted-in execution. Disappearance or hiding clears the candidate; readable `Starting TRIM...` uses the existing startup exemption. Changed or simultaneous real dialogs retain normal handling. Persistent unreadability fails as a popup at the bound; repeated observations never reset the per-PID/handle deadline. Cancellation and the native timeout remain active and may stop earlier. The rule sends no responses and does not establish calculation progress or success. Timing state is local to each attempt; saved report schemas are unchanged.

Completion is `{complete, reason, checks}`; each check is `{field, status, detail}`. An execution failure is `{code, message}` with codes preflight, launch, popup, timeout, cancelled, monitor, preservation, parse, checkpoint-recovery, checkpoint-required, output-requirement, incomplete, substitution or integrity. `execution.stage` identifies the operation reached. Expected failure classification is independent of the shared metadata failure format.

New resumed `execution.json` reports use schema 4, retaining existing fields and adding `result_source="checkpoint-recovery"` and `checkpoint_verification`. Availability is present only for opted-in responses, as in fresh reports. Verification is initially null and later `{sources, live_sources_match, atomicity}`. Sources cover every preserved checkpoint file. `live_sources_match` is true/false for a controlled stop with a live snapshot or null for natural exit. Atomicity always states `"not established for binary native arrays"`. Fresh execution report schemas remain unchanged. Historical resumed schema-3 records remain historical evidence; they are not rewritten. Version 4 retains their top-level keys and changes the nested lifecycle evidence.

Resumed `results.json` has exactly `{schema_version, engine, result_source, native, recovery, issues, complete}` with version 2, engine trim and source checkpoint-recovery. `native` is the unchanged schema-1 native diagnostic report, without inferred request checks; absent/stale native text does not determine recovered completeness. `recovery` is the unchanged recovery-v2 object, or null on recovery failure. Recovery issues are `{path: "SRIM Restore", code: "checkpoint-recovery", message}`. Complete means structurally valid recovered results, independently of lifecycle success. `parse_trim_checkpoint_results(files: Mapping[str, bytes]) -> JSONObject` is pure: unsafe caller mappings raise ValueError; recovery failures become issues. Only direct regular checkpoint files are supported, including additional safe files. The pipeline recovers from the preserved stopped runtime and checks exact child count/autosave and saved input values. Seven profile families, cumulative normalization, decimal strings, distinct entries, not-calculated availability, stored precision and inferred-geometry warnings retain their recovery-v2 meanings. Collision histories remain independently required. The following native profile fields apply to fresh reports and the nested `native` report, not to the recovered result envelope.

`results.profiles` maps exact canonical filenames to parsed profile objects. `issues` entries are `{path, code, line, message}`. Missing output, malformed/truncated output, ambiguous duplicate locations and inconsistent native values are retained as issues. Root and SRIM Outputs copies must match byte for byte if both exist. `consistency` contains `request_checked` and a list of checks. `complete` is a parsing-consistency result; standalone imports never imply historical execution success.

Profile columns are ordered `{index, label, unit, atom_entry_index, symbol}`; index is zero-based and atom entries are one-based. Null symbols/entries mean not applicable or unavailable identity. Rows are lists of original numerical strings with depth in column zero. Numerical tables use the verified 100-row native format; unsupported/truncated variants are reported. The approved exception is native quick-mode NOVAC text explicitly stating that replacement collisions were not calculated: rows/columns/statistics are empty, calculated_ions is null and notes retain the native explanation. Known full-cascade requests/checkpoints still require the numeric table. Exact values and units are retained, not rescaled or merged by species. Notes retain native warnings and missing headings.

Saved-value comparisons are `{field, requested, observed, precision, status}`. Numeric comparisons use half the last printed unit; categorical/count comparisons are exact. Native initial controls are distinguished from evolving random-generator state. Resumed runs use explicit continuation count/autosave controls and inherited physics. Binary saved arrays remain hash-preserved and unverified scientifically.

Saved stoichiometry is fractional: checks named `layers[i].stoichiometry_fraction[j]` compare 28-digit Decimal fractions derived from the original requested proportions with unchanged printed saved fractions. Raw proportions remain in the preserved request and native input; the comparison does not alter them. Known quick-mode RANGE/VACANCY/E2RECOIL aggregate tables have three columns with null atom-entry indices and explicit aggregate labels/notes, rather than a fabricated column for every element. Full-cascade per-entry layouts still require the appropriate target-entry count.

## Continuation, batches and external results

Continuation requires a complete sealed saved-state set: the twelve observed 2008 files, plus six additional 3D files for 2013. Additional saved files are retained. Successful terminal parents and eligible failed popup/timeout/cancelled parents can be continued after identity, inventory and count checks. Arbitrary external checkpoints are not accepted as resumable project records. Checkpoint atomicity across binary arrays and physical equivalence with uninterrupted execution remain unestablished.

Batch results are input-ordered; each task has exactly one of metadata/error. The queue starts the earliest eligible task, serializes each worker and continues independent ordinary failures. A shared worker lease prevents simultaneous SR/CAB/TRIM use. Interruption stops dispatch and cancels active owned jobs. The queue is not durable, a dependency scheduler, a parameter-sweep generator or an automatic retry mechanism.

Cleanup is explicit, verifies terminal permanent inventories and working/preserved file and directory equality, and keeps worker allocation directories. Unknown or unsafe files, SR contents, unfinished records and changed evidence retain the worker with an explanation. Permanent simulation/resource data is not deleted.

Standalone import requires a quiescent runtime directory and a new disjoint destination. It stream-copies every file into `raw/runtime`, writes `parsed/results.json`, optionally preserves an explicit request and publishes a hash manifest with `kind="trim-output-import"`. No request, engine executable identity or simulation success is inferred when absent. Native parser issues return a report; unsafe I/O, source mutation and publication failures raise with retained staging paths.

The execution.json timeout_seconds field records the effective per-attempt timeout as a positive number, or null for unlimited. An unlimited execution deadline does not disable save/readiness/shutdown bounds. Existing records are not rewritten. No automatic retry or continuation is performed. The shared checkpoint-first design supersedes event acknowledgment/default-save behavior; see [current direction](../decisions/trim-continuation.md) for measured regression and native verification. Stable snapshots and stopped-copy validation do not prove atomic saving or independent scientific accuracy.
