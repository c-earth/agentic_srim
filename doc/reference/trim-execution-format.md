# TRIM execution APIs and record formats

[Execution guide](../guides/trim-execution.md) · [Input format](trim-input-format.md) · [Approved contract](../TRIM_EXECUTION_PROPOSAL.md)

This reference covers the Windows runner, continuation, in-memory scheduling, preservation and summary/depth-profile parsing. Existing SR signatures and legacy records remain supported. Import modules from the repository root. New APIs use existing `Config`, `ProjectPaths`, `CreatedDirectory`, `JSONObject`, `ResolvedTRIMInputs` and cleanup report types.

## Public interfaces

```python
load_trim_input_bundle(source: Path) -> ResolvedTRIMInputs
prepare_trim_simulation(paths: ProjectPaths, config: Config,
    created: CreatedDirectory, worker: CreatedDirectory,
    inputs: ResolvedTRIMInputs) -> Path
prepare_trim_continuation(paths: ProjectPaths, config: Config,
    created: CreatedDirectory, worker: CreatedDirectory, parent_id: str, *,
    total_ions: int, autosave_ions: int,
    events: list[JSONObject] | None = None) -> Path
verify_trim_runtime(paths: ProjectPaths, simulation_id: str,
    runtime: Path) -> ResolvedTRIMInputs

run_trim_simulation(paths: ProjectPaths, config: Config,
    simulation_id: str, runtime: Path, *, timeout_seconds: float = 60.0,
    cancel: Callable[[], bool] | None = None) -> JSONObject

PreparedTRIMTask(simulation_id: str, runtime: Path)
TRIMTaskResult(task: PreparedTRIMTask, metadata: JSONObject | None,
    error: Exception | None, elapsed_seconds: float)
TRIMBatchResult(task_results: tuple[TRIMTaskResult, ...],
    cleanup: BatchCleanupReport, elapsed_seconds: float)
run_trim_batch(paths: ProjectPaths, config: Config,
    tasks: Sequence[PreparedTRIMTask], *, max_workers: int,
    timeout_seconds: float = 60.0, cleanup: bool = False) -> TRIMBatchResult

parse_trim_summary(contents: bytes) -> JSONObject
parse_trim_profile(filename: str, contents: bytes) -> JSONObject
parse_trim_checkpoint(contents: bytes, trim_input: bytes) -> JSONObject
compare_trim_saved_values(checkpoint: JSONObject, inputs: ResolvedTRIMInputs,
    *, resumed: bool = False) -> JSONObject
parse_trim_outputs(files: Mapping[str, bytes],
    request: JSONObject | None = None) -> JSONObject

preserve_trim_runtime(paths: ProjectPaths, simulation_id: str, runtime: Path) -> None
verify_preserved_trim_runtime(paths: ProjectPaths,
    simulation_id: str, runtime: Path) -> None
parse_trim_directory(source: Path, destination: Path, *,
    request: JSONObject | None = None) -> Path
cleanup_trim_batch(paths: ProjectPaths,
    worker_paths: Sequence[Path]) -> BatchCleanupReport
```

Preparation/loading/verification are in `trim_runtime`; execution in `trim_execution`; task classes and scheduling in `trim_queue`; pure parsers/comparison in `trim_outputs`; filesystem preservation/export/cleanup in `trim_storage`. The native implementation boundary is `trim_native.execute_trim_process`; ordinary callers should use the lifecycle runner.

Paths supplied for bundles, runtimes and standalone imports are absolute and link-free. Preparation requires an empty simulation allocation and a valid worker allocation. Bundle loading validates the exact export manifest and independently replays pinned evidence. It does not read installations or current aliases. Metadata initialization/start also verify referenced immutable project resources. Preparation neither reruns material resolution nor launches TRIM.

Invalid caller types/layouts/state, collisions and worker contention can raise before execution. Expected native/preflight/timeout/popup/parse/substitution failures return terminal failed metadata. Filesystem/publication errors propagate with retained artifact notes after best-effort finalization. Interruption terminates owned native processes, attempts preservation and failure publication, then re-raises. Timeouts are positive finite int/float values excluding bool and apply per native attempt; queue waiting, preservation and parsing are separate. Initial window visibility follows `config.hide_trim_window`.

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

The [approved contract](../TRIM_EXECUTION_PROPOSAL.md#exact-report-schemas) specifies the full nested field catalogue. The implementation's principal objects are:

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

`schema_version` is 1; reports carrying engine use `"trim"`. `control.kind` is fresh/resume. Resume total_ions is an explicit absolute final count greater than saved current count; autosave is explicit. Count edits preserve original checkpoint current count and accumulated arrays. All prior event records stay byte-equivalent and ordered; only new structured events may be appended.

Process evidence is `{pid, exit_code, elapsed_seconds, termination, popups, monitor_errors}`. Termination is exit, popup, timeout, cancelled, monitor-error or launch-error. Popup records include handle, owner_handle, pid, class, title, visible, enabled, rectangle, controls, detection_reason and capture_errors. No button, focus or close message is sent. Windows job membership protects ownership during inspection; the child starts suspended and is assigned to a kill-on-close job before it resumes. Polling and bounded text capture cannot guarantee observation of arbitrarily brief dialogs; inaccessible evidence is recorded conservatively.

Completion is `{complete, reason, checks}`; each check is `{field, status, detail}`. An execution failure is `{code, message}` with codes preflight, launch, popup, timeout, cancelled, monitor, preservation, parse, incomplete, substitution or integrity. `execution.stage` identifies the operation reached. Expected failure classification is independent of the shared metadata failure format.

`results.profiles` maps exact canonical filenames to parsed profile objects. `issues` entries are `{path, code, line, message}`. Missing output, malformed/truncated output, ambiguous duplicate locations and inconsistent native values are retained as issues. Root and SRIM Outputs copies must match byte for byte if both exist. `consistency` contains `request_checked` and a list of checks. `complete` is a parsing-consistency result; standalone imports never imply historical execution success.

Profile columns are ordered `{index, label, unit, atom_entry_index, symbol}`; index is zero-based and atom entries are one-based. Null symbols/entries mean not applicable or unavailable identity. Rows are lists of original numerical strings with depth in column zero. Numerical tables use the verified 100-row native format; unsupported/truncated variants are reported. The approved exception is native quick-mode NOVAC text explicitly stating that replacement collisions were not calculated: rows/columns/statistics are empty, calculated_ions is null and notes retain the native explanation. Known full-cascade requests/checkpoints still require the numeric table. Exact values and units are retained, not rescaled or merged by species. Notes retain native warnings and missing headings.

Saved-value comparisons are `{field, requested, observed, precision, status}`. Numeric comparisons use half the last printed unit; categorical/count comparisons are exact. Native initial controls are distinguished from evolving random-generator state. Resumed runs use explicit continuation count/autosave controls and inherited physics. Binary saved arrays remain hash-preserved and unverified scientifically.

Saved stoichiometry is fractional: checks named `layers[i].stoichiometry_fraction[j]` compare 28-digit Decimal fractions derived from the original requested proportions with unchanged printed saved fractions. Raw proportions remain in the preserved request and native input; the comparison does not alter them. Known quick-mode RANGE/VACANCY/E2RECOIL aggregate tables have three columns with null atom-entry indices and explicit aggregate labels/notes, rather than a fabricated column for every element. Full-cascade per-entry layouts still require the appropriate target-entry count.

## Continuation, batches and external results

Continuation requires a complete sealed saved-state set: the twelve observed 2008 files, plus six additional 3D files for 2013. Additional saved files are retained. Successful terminal parents and eligible failed popup/timeout/cancelled parents can be continued after identity, inventory and count checks. Arbitrary external checkpoints are not accepted as resumable project records. Checkpoint atomicity across binary arrays and physical equivalence with uninterrupted execution remain unestablished.

Batch results are input-ordered; each task has exactly one of metadata/error. The queue starts the earliest eligible task, serializes each worker and continues independent ordinary failures. A shared worker lease prevents simultaneous SR/CAB/TRIM use. Interruption stops dispatch and cancels active owned jobs. The queue is not durable, a dependency scheduler, a parameter-sweep generator or an automatic retry mechanism.

Cleanup is explicit, verifies terminal permanent inventories and working/preserved file and directory equality, and keeps worker allocation directories. Unknown or unsafe files, SR contents, unfinished records and changed evidence retain the worker with an explanation. Permanent simulation/resource data is not deleted.

Standalone import requires a quiescent runtime directory and a new disjoint destination. It stream-copies every file into `raw/runtime`, writes `parsed/results.json`, optionally preserves an explicit request and publishes a hash manifest with `kind="trim-output-import"`. No request, engine executable identity or simulation success is inferred when absent. Native parser issues return a report; unsafe I/O, source mutation and publication failures raise with retained staging paths.
