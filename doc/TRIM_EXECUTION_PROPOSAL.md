# TRIM execution and summary/profile data proposal

Status: approved for implementation, 2026-10-21. The user approved the complete contract after confirming all five operational decisions. Implementation began after an explicit start announcement.

## Agreed scope and philosophy

Implement fresh execution, continuation from a preserved checkpoint, in-memory batch scheduling, and standalone summary/depth-profile parsing of existing results. Preserve every native file, including optional histories and binary saved arrays; interpreting those histories/arrays is outside the agreed parser scope.

Follow SR's explicit preparation, worker lease, created/running/terminal lifecycle, immutable input evidence, terminal output inventory, and verified cleanup design. Reuse backend allocations and coordination. Preserve TRIM intent, pinned engine/resource bytes, layer/component identity, and all native error evidence. Structural and confirmed native validation remains in the input APIs. Unknown limits are attempted by the executable, without speculative caps, parameter substitution, automatic retry, or unsolicited parameter suggestions.

Every detected popup is reported. Never click a button, send input, dismiss a popup, or acknowledge event completion. Popup handling is distinct from hiding the normal calculation window. A normal TRIM main window is not itself a popup. A detected popup ends the owned attempt according to the proposed policy below; its mere presence never means successful completion.

## Evidence and limits informing the design

The input implementation in TRIM_INPUT_PROPOSAL.md is complete. Its bundles contain original intent, effective intent, resolution, selected resources and installation byte snapshots. Execution must not resolve those selections again.

Native summaries appear in the working root, optional outputs under SRIM Outputs, and save files under SRIM Restore. Preserve the complete runtime, including generated Data/*.bin files. Directory-name assumptions must not lose root outputs.

Earlier UI-assisted checks covered all 15 mode/version combinations for 2008 and 2013 Standard. Those checks are not proof of unattended completion. Recoil cascade-output and event-completion dialogs are known. The latest generated-input checks left some event modes incomplete. Embedded quotes produced runtime error 9 in one probe; the individual name field responsible remains unresolved. None of these facts becomes a speculative input rejection.

Resume tests established checkpoint progress, not universally clean exits or numerical equivalence. Removing individual save files can change results despite increasing counts. A complete frozen save set is therefore required. Custom checkpoint input paths can differ from the autosave output path; use a separate checkpoint source directory and native SRIM Restore destination. Resume may display graphics or a save/increase-count dialog. Do not promise that hide_trim_window prevents native popups or later plotting windows.

## Proposed file ownership and dependencies

All paths below are relative to D:/projects/agentic_srim. New Python modules start with module docstrings. Every function/method has full annotations and a first-statement docstring describing all inputs, return values, effects and errors; classes document construction and fields. No implementation comments. New helpers or changes to these contracts require proposal revision before implementation.

| File | Purpose, contents and dependencies |
| --- | --- |
| src/srim/trim_runtime.py | Fresh and resume preparation, offline bundle loading, exact runtime verification. Depends on backend allocations/locks, metadata, trim_resolution, trim_inputs and trim_outputs. Never launches the main engine. |
| src/srim/trim_native.py | Windows process ownership, bounded native window inspection and popup evidence. Uses standard-library ctypes, subprocess and time; no third-party GUI automation dependency. Has no simulation/resource publication responsibilities. |
| src/srim/trim_execution.py | Orchestrates preflight, lifecycle, owned execution, preservation, parsing and terminal status. Depends on the other TRIM modules and shared lifecycle/locks. |
| src/srim/trim_outputs.py | Pure summary, profile and textual checkpoint parsing, plus saved-value comparisons. Uses Decimal, hashlib, re and existing TRIM validation. No filesystem, GUI, installation lookup or mutation. |
| src/srim/trim_queue.py | Prepared tasks, ordered results and bounded same-worker scheduling. Uses ThreadPoolExecutor, ordinary runner and explicit cleanup. No durable queue or parameter-sweep expansion. |
| src/srim/trim_storage.py | Keep existing input-bundle export unchanged; add whole-runtime preservation, standalone directory parsing/export and verified TRIM cleanup. Depends on metadata inventories, safe I/O and pure parsers. |
| src/srim/metadata.py | Add explicit TRIM request/resolution dispatch, continuation evidence and TRIM input roots; preserve existing SR schemas/signatures. Strengthen TRIM start-time sealing without changing legacy SR behavior. |
| src/backend/config.py | Update docstrings to describe implemented hide_trim_window behavior. No new configuration field or default. |
| test/srim/test_trim_runtime.py | Preparation, resume, offline replay, tampering and collision contracts. |
| test/srim/test_trim_native.py | Mocked process/window ownership, popup capture, timeout and cancellation contracts. |
| test/srim/test_trim_execution.py | Lifecycle, validation, error evidence and success/failure contracts with mocked native execution. |
| test/srim/test_trim_outputs.py | Real native fixtures, precision, header/row checks, incomplete reports and request comparison. |
| test/srim/test_trim_queue.py | Ordering, concurrency, independent failures, interruption and cleanup policy. |
| test/srim/test_trim_storage.py | Extend existing class for runtime preservation, standalone export and cleanup. |
| test/srim/test_metadata.py | Extend existing class for TRIM dispatch/sealing/continuation, and SR compatibility. |
| test/srim/fixtures/trim/{2008,2013}/ | Committed small native TDATA.txt, seven profile files, textual TDATA.sav and TRIMIN.sav for one ordinary case per family; fixture manifest records source hashes and case provenance. No executable or binary save arrays committed. |
| doc/guides/trim-execution.md | End-to-end fresh/resume/batch and standalone parse examples, popup behavior, result interpretation and limitations. |
| doc/reference/trim-execution-format.md | Exact runtime, lifecycle, execution, checkpoint and parsed-output schemas and APIs. |
| README.md; doc/README.md; doc/reference/api.md | Add entry points and references to implemented behavior. |
| doc/TRIM_HANDOFF.md; doc/TRIM_EXECUTION_PROPOSAL.md | Record only new durable conclusions, approval and unresolved limitations after verification. |

Auxiliary probes, scripts, native runtimes and test artifacts belong under .cache/trim-execution/. No unrelated untracked files are touched.

## Public and internal typed API catalogue

JSONObject is the existing JSON object type. Sequence, Mapping, Iterator and Callable come from collections.abc; Path from pathlib. Existing Config, ProjectPaths, CreatedDirectory, ResolvedTRIMInputs, BatchCleanupReport and RetainedWorker retain their types.

### trim_runtime.py

```python
def load_trim_input_bundle(source: Path) -> ResolvedTRIMInputs: ...
def prepare_trim_simulation(paths: ProjectPaths, config: Config,
    created: CreatedDirectory, worker: CreatedDirectory,
    inputs: ResolvedTRIMInputs) -> Path: ...
def prepare_trim_continuation(paths: ProjectPaths, config: Config,
    created: CreatedDirectory, worker: CreatedDirectory, parent_id: str,
    *, total_ions: int, autosave_ions: int,
    events: list[JSONObject] | None = None) -> Path: ...
def verify_trim_runtime(paths: ProjectPaths, simulation_id: str,
    runtime: Path) -> ResolvedTRIMInputs: ...
def _trim_runtime_manifest(files: Mapping[str, bytes]) -> JSONObject: ...
def _stage_trim_runtime(paths: ProjectPaths, config: Config,
    created: CreatedDirectory, worker: CreatedDirectory,
    inputs: ResolvedTRIMInputs, continuation: JSONObject | None,
    checkpoint_files: Mapping[str, bytes]) -> Path: ...
```

- load_trim_input_bundle: source is an absolute safe exported bundle directory. Validate exact manifest schema, safe case-unique paths, hashes/sizes, required documents, absence of unlisted files, and independent resolution replay. Return a defensively copied bundle. No writes or live resource lookup. ValueError for invalid evidence, OSError for unsafe/missing/unreadable files. Directory aliases/links are rejected.
- prepare_trim_simulation: validate empty simulation and allocated worker, copy inputs, independently replay, stage from pinned bytes and initialize lifecycle under the worker lease. Return worker runtime with created status. No installation re-read, dependency work or process launch. ValueError for invalid allocation/bundle, FileExistsError for destination collision, OSError for unsafe paths/I/O/lease contention. Retain partial record/runtime and identify them in exception notes.
- prepare_trim_continuation: parent_id identifies a sealed terminal TRIM record; copy its complete checkpoint and pinned engine/input provenance. total_ions is an absolute final count strictly above the saved count, not an increment. autosave_ions is explicitly supplied and never silently substituted. events follows the existing structured events schema; required for event continuation, absent for ordinary modes. Preserve the entire consumed event prefix; accept only appended records, never reorder or replace earlier events. Return a new prepared runtime. Validate all checkpoint evidence and confirmed fields before publications. No main process launch or changes to parent. ValueError for inconsistent/absent/unusable checkpoint or changed immutable physics; normal allocation/I/O/lock errors as above. Eligible failed parents are limited to timeout, popup and cancellation failures with complete verified checkpoint evidence.
- verify_trim_runtime: require created matching record/runtime, empty output directories, exact prepared file/directory inventory, independently replayed input, sealed native hashes, and complete continuation inventory if applicable. Return a copied bundle without native execution or writes except persistent coordination files. No aliases/installations are consulted. ValueError for schema/integrity/ownership failures, OSError for unsafe/I/O failures.
- _trim_runtime_manifest: pure deterministic sorted {schema_version:1,files:[{path,sha256,size_bytes}]} for safe case-unique relative names/byte values, including initial checkpoint files. ValueError for invalid names/content or missing required runtime files.
- _stage_trim_runtime: shared preparation implementation, taking validated new allocations, pinned inputs, optional continuation document and exact checkpoint bytes. Validate again at boundary, stage a sibling temporary tree, flush and verify, then exclusively publish runtime/record. Return Path; retain partial artifacts on errors. Caller owns no native process. Fresh checkpoint_files must be empty; resume requires a nonempty complete set. No nested worker lease acquisition.

### trim_native.py

```python
def execute_trim_process(executable: Path, runtime: Path, *,
    hide_window: bool, timeout_seconds: float,
    stdout_path: Path, stderr_path: Path,
    cancel: Callable[[], bool] | None = None) -> JSONObject: ...
def _read_trim_windows(process_id: int) -> list[JSONObject]: ...
def _read_trim_window_text(window_handle: int) -> JSONObject: ...
```

- execute_trim_process: Windows-only boundary. Require absolute regular executable/runtime, exclusive safe diagnostic destinations, finite positive timeout excluding bool, boolean hide_window and callable/None cancellation. Launch shell-free, initially suspended, assign to a Windows Job Object with kill-on-close before resuming. If job ownership cannot be established, do not execute an unowned child. Record PID/creation identity; poll at 0.1 seconds; inspect owned top-level and child windows with bounded 0.1-second native text calls. Never send input or a window-close command. stdout/stderr go directly to binary files. Return process-result JSON for exit, popup, timeout, cancellation or monitoring error. On timeout/popup/cancellation, terminate the owned job and wait before returning; retain evidence. BaseException closes owned job and waits, then propagates. Validation raises ValueError, unsupported host raises OSError, launch/monitor errors are recorded; diagnostic-write errors propagate with owned cleanup. No unrelated process is killed.
- _read_trim_windows: process_id is an owned positive PID. Return window records for that process, including main windows and popup candidates; inspect ownership/style/class rather than treating every window as a dialog. Record handle, owner handle, PID, class, title, visibility, enabled state, rectangle, readable child text, detection reason and capture errors. Report dialogs (#32770), owned top-level popup windows and main-window-disabled/modal evidence. Unknown classifications are reported conservatively. No window control, process mutation or filesystem effects. ValueError for invalid PID; OSError for native enumeration failure. Child processes in the job are enumerated and inspected by execute_trim_process, with identity checks to prevent PID reuse mistakes.
- _read_trim_window_text: input is an observed integer native window handle. Return title/control text and per-control read errors using bounded reads. No clicks, focus changes or writes. Vanished/inaccessible windows are explicit capture errors, not empty evidence interpreted as success.

Capture uses text/class/ownership evidence, not screenshots in the first implementation. This limits reporting for image-only dialogs; those are still reported with identity and capture limitations. Polling cannot guarantee capture of arbitrarily short-lived popups. If inspection fails, classify monitoring failure rather than claiming the run was popup-free.

### trim_outputs.py

```python
def parse_trim_summary(contents: bytes) -> JSONObject: ...
def parse_trim_profile(filename: str, contents: bytes) -> JSONObject: ...
def parse_trim_checkpoint(contents: bytes, trim_input: bytes) -> JSONObject: ...
def compare_trim_saved_values(checkpoint: JSONObject,
    inputs: ResolvedTRIMInputs, *, resumed: bool = False) -> JSONObject: ...
def parse_trim_outputs(files: Mapping[str, bytes],
    request: JSONObject | None = None) -> JSONObject: ...
def _trim_text(contents: bytes, source: str) -> list[str]: ...
def _trim_number(token: str, field: str, *, signed: bool = False) -> str: ...
def _trim_source(filename: str, contents: bytes) -> JSONObject: ...
```

All functions are pure, copy caller data, and use Decimal only for validation/comparison. Preserve numeric tokens exactly, including native scientific notation, signs and trailing zeroes. No normalization to binary floats, calculated profiles, rescaling, smoothing, averaging across layers, or inferred physical validity.

- parse_trim_summary: strict supported native TDATA.txt headers/sections; return version/name, projectile, ordered layers, target energy entries, calculated/transmitted/backscattered counts and printed range/straggling/vacancies-per-ion. Preserve repeated species positionally. Missing/malformed required sections raise ValueError; absent optional native statistics are null and listed as unavailable. No request is inferred from a summary.
- parse_trim_profile: filename is exactly one of RANGE.txt, VACANCY.txt, IONIZ.txt, PHONON.txt, E2RECOIL.txt, NOVAC.txt, LATERAL.txt, compared case-insensitively. Return source, version, calculated count, printed headings/units, ordered column descriptors, header statistics and ordered rows. Check table markers, mode-specific column layout, finite tokens, increasing depth and the measured version-specific row contract (normally 100). Multi-element columns are positional and never merged by element symbol. Unsupported format/filename, malformed/truncated rows or inconsistent columns raise ValueError. Native nonnegative quantities are enforced only where confirmed; signed summary skewness and signed coordinates are permitted in their applicable fields. LATERAL is a depth table with projected/radial means and stragglings, not a particle history.
- parse_trim_checkpoint: contents is textual TDATA.sav; trim_input is textual TRIMIN.sav. Parse required anchored records, version, current/final/autosave counts, species/masses/energies, mode/flags, target entries/layers, native controls and preserved TRIM input fields. Return structured saved-state evidence with both sources. Do not interpret binary arrays, final RNG values as initial seeds, or current-particle residual energy as the requested projectile energy. ValueError for missing/malformed/unsupported/contradictory records. Exact lexical patch positions for the continuation count line are recorded for byte-preserving edits.
- compare_trim_saved_values: independently validate inputs and checkpoint; compare fields actually represented in saved state against effective request. Return checked fields, differences and unverified fields. Resumed allows documented native display/plot state changes but does not excuse changed materials, masses, energies, correction or counts. Compare printed values at native displayed precision, with half a last printed unit as tolerance; no arbitrary broad relative tolerance. Mark fields unavailable for comparison explicitly. Structural errors raise ValueError; legitimate observed substitutions return differences rather than rewriting request. Count completion is evaluated separately.
- parse_trim_outputs: files is a safe case-unique relative-name/bytes mapping containing available summary/profile files and optional textual checkpoint pair. request, if supplied, is a fully explicit engine request, not high-level intent. Validate it without mutation. Resolve root/SRIM Outputs locations case-insensitively; if both copies exist, accept only byte-identical duplicates, otherwise report ambiguity. Return aggregate parsed report with per-file errors and available successful parses. Missing, corrupt, unsupported and inconsistent data remain distinct. Without request, report observed data and cross-file consistency only; never claim successful execution. Does not require binary arrays or parse optional history files.
- _trim_text: strict explicit CP1252 native-text decoding and line splitting; preserve printed content. Native OEM box-drawing bytes are decoration and not interpreted as physics. Strip an EOF byte only at a verified native end-of-file position. ValueError for invalid argument or illegal record controls.
- _trim_number: return unchanged finite decimal token after syntax/Decimal checks, disallow negative values unless signed=True. ValueError identifies field.
- _trim_source: return {path,sha256,size_bytes} for filename/bytes without effects; invalid names/types raise ValueError.

### trim_execution.py

```python
def run_trim_simulation(paths: ProjectPaths, config: Config,
    simulation_id: str, runtime: Path, *,
    timeout_seconds: float = 60.0,
    cancel: Callable[[], bool] | None = None) -> JSONObject: ...
def _trim_completion(inputs: ResolvedTRIMInputs,
    process_result: JSONObject, parsed: JSONObject,
    saved_values: JSONObject, continuation: JSONObject | None) -> JSONObject: ...
```

- run_trim_simulation: acquire worker lease through validation, start, owned native run, complete preservation, parsing and terminal publication. Validate timeout/config/cancel before ownership. Return terminal shared metadata, including expected failed attempts; no retry or deletion. Preserve stdout/stderr, process/popup evidence and entire stopped runtime before parsing. Write parser/comparison reports even for incomplete runs where meaningful. Invalid caller/state/occupied output and lock failures raise before launch. Expected integrity, launch, timeout, popup, monitoring, missing output, parser, count and substitution failures publish failed metadata with execution.json detail. Filesystem failures propagate after best-effort failure publication and notes. BaseException terminates the owned job, attempts preservation/failure publication, then re-raises.
- _trim_completion: pure classification using validated effective inputs, process result, parsed report, saved comparison and optional continuation. Success requires clean exit code 0, no popup/monitor failure/timeout, complete required summary/profile parsing, consistent native version/projectile/target/count evidence, current/final counts meeting the approved mode-specific contract, and no requested-value substitutions. No physics equivalence claim. Return {complete,reason,checks}; ValueError for malformed supplied reports. For event modes, do not silently replace requested total_ions with event count; a disagreement is a reported completion mismatch pending an explicitly approved mode-specific rule.

### trim_storage.py additions

```python
def preserve_trim_runtime(paths: ProjectPaths, simulation_id: str,
    runtime: Path) -> None: ...
def verify_preserved_trim_runtime(paths: ProjectPaths,
    simulation_id: str, runtime: Path) -> None: ...
def parse_trim_directory(source: Path, destination: Path, *,
    request: JSONObject | None = None) -> Path: ...
def cleanup_trim_batch(paths: ProjectPaths,
    worker_paths: Sequence[Path]) -> BatchCleanupReport: ...
```

- preserve_trim_runtime: caller holds worker lease and has stopped owned native processes. Exclusively stage/hash/flush/verify the entire runtime under output/raw/runtime; identical existing copy may be reused, different contents never overwritten. Return None. Reject symlinks/junctions, unsafe ancestors and mutation during copy; raise ValueError/OSError and retain source/staging with notes.
- verify_preserved_trim_runtime: require sealed terminal matching record and byte-identical runtime/snapshot inventories; return None. No effects except existing coordination files. ValueError/OSError for nonterminal, unsafe, missing or changed evidence.
- parse_trim_directory: standalone absolute source directory and new absolute destination, outside source and not an ancestor of source; safe ancestors and regular entries required. Read a quiescent existing native runtime (root or output/raw/runtime), stream-copy every file to raw/runtime with hash verification, parse only summary/profiles/textual checkpoint pair, and exclusively publish report manifest. Optional explicit request is separately preserved and validated. Return destination even for native parse errors, which are represented in results.json; invalid caller/path/collision/I/O raises and retains staging. No simulation allocation/lifecycle, native launch, engine installation lookup or modification of source. Source before/after inventories must match; this is not an atomic snapshot of a live run. Live external directories must first be stopped/frozen by their owner.
- cleanup_trim_batch: deduplicate canonical worker paths, lock each, remove only terminal completely sealed and preserved TRIM runtime directories after resolving each target within that worker's trim directory. Keep worker/sr/trim allocation directories. Return per-worker cleaned/retained outcomes using existing report types. Unknown files, active records, differing evidence or SR contents retain the worker. Permanent records never removed. Never clean automatically on an interrupted batch. Automatic cleanup is disabled by default; the caller must explicitly request it.

### trim_queue.py

```python
@dataclass(frozen=True)
class PreparedTRIMTask:
    simulation_id: str
    runtime: Path

@dataclass(frozen=True)
class TRIMTaskResult:
    task: PreparedTRIMTask
    metadata: JSONObject | None
    error: Exception | None
    elapsed_seconds: float

@dataclass(frozen=True)
class TRIMBatchResult:
    task_results: tuple[TRIMTaskResult, ...]
    cleanup: BatchCleanupReport
    elapsed_seconds: float

def run_trim_batch(paths: ProjectPaths, config: Config,
    tasks: Sequence[PreparedTRIMTask], *, max_workers: int,
    timeout_seconds: float = 60.0, cleanup: bool = False) -> TRIMBatchResult: ...
def _validate_trim_batch(paths: ProjectPaths, config: Config,
    tasks: Sequence[PreparedTRIMTask], max_workers: int,
    timeout_seconds: float, cleanup: bool) -> tuple[PreparedTRIMTask, ...]: ...
def _execute_trim_task(paths: ProjectPaths, config: Config,
    task: PreparedTRIMTask, timeout_seconds: float,
    cancel: Callable[[], bool]) -> TRIMTaskResult: ...
```

Dataclass constructors store the typed fields without I/O; class docstrings document every input/attribute. PreparedTRIMTask names an already prepared fresh or resumed record. TRIMTaskResult has exactly one of metadata/error; metadata may itself describe a native failure. Durations exclude queue waiting. TRIMBatchResult preserves task input order, cleanup outcomes and wall duration including validation/optional cleanup.

- _validate_trim_batch: snapshot sequence, reject bool/nonpositive max_workers, nonfinite/nonpositive timeout, invalid cleanup/config, duplicate IDs/paths and invalid prepared runtimes before any native launch. Empty tasks produce an empty result. Pure except file reads/coordination. ValueError/OSError/lock contention propagate.
- _execute_trim_task: measure ordinary runner with supplied cancellation; return expected terminal metadata or independently captured Exception/error. No retry/cleanup; BaseException propagates. Exactly one result channel is populated.
- run_trim_batch: bounded in-memory scheduling, earliest eligible task first, same-worker submission order, one active task per worker. Shared backend lease also excludes SR/CAB activity. Fresh/resumed tasks can coexist but continuation dependencies must already be prepared from terminal parents; this is not a dependency-graph scheduler. Ordinary errors, including popups, are recorded per task and remaining independent tasks continue. On KeyboardInterrupt/BaseException, stop dispatch, signal active owned jobs to cancel, wait for cleanup/preservation, retain all workers and re-raise. Results are input-ordered; native metadata is persisted immediately. cleanup=True invokes verified cleanup after all calls finish; cleanup=False returns an empty cleanup report and leaves runtime paths intact. No automatic allocation, retry, durable scheduling, parameter sweep, external messaging or crash recovery queue.

### Existing metadata.py functions: exact signature policy

No existing public signature changes. _validate_document(document: JSONObject, document_type: str) -> None dispatches TRIM request/resolved documents by explicit engine discriminator before SR branches. It invokes TRIM structural validation; offline full replay stays at preparation/execution boundaries to avoid import cycles.

_validate_input_files(input_files: Mapping[str, bytes] | None) -> dict[str, bytes] adds installation/, dependencies/, continuation/ and execution/ to safe evidence roots, retaining all name/collision protections. _collect_resource_references(resolved: JSONObject) -> list[JSONObject] supports TRIM's seven-role resources and recorded pointers/dependencies, sorted/deduplicated; no flattening into SR's schema. initialize_simulation retains its existing full signature and uses TRIM schema version 1 metadata with existing common lifecycle keys and existing continuation {parent_id,files}. TRIM's richer dependency/default provenance remains in its sealed resolved.json, not forced into SR's version-two CAB fields. No dependency calculation is repeated during start.

start_simulation(paths: ProjectPaths, config: Config, simulation_id: str) -> JSONObject seals every initialized TRIM input path exactly, including engine/resources/continuation and control files. Existing SR rules remain unchanged. read_simulation and finish_simulation retain signatures and current effects; common metadata failure keys remain message/exit_code, with detailed stages/codes in execution.json. Local imports at schema-dispatch boundaries prevent module-import cycles.

## Runtime and record layouts

```text
workers/worker_N/trim/<simulation_id>/
  TRIM.exe
  TRIM.IN
  TRIMAUTO
  TRIM.DAT                         event modes only
  Data/TRIM.cfg
  Data/SCOEF03.dat
  Data/VERSION
  SRIM Outputs/                    initially empty
  SRIM Restore/                    initially empty
  checkpoint/<complete parent save tree>   resume only

memory/<existing simulation root>/<simulation_id>/
  metadata.json
  input/request.json
  input/selections.json
  input/effective-request.json
  input/resolved.json
  input/resources/...
  input/installation/...
  input/dependencies/...
  input/engine/TRIM.IN
  input/engine/TRIM.DAT             when applicable
  input/engine/TRIMAUTO
  input/execution/runtime.json
  input/execution/control.json
  input/continuation/parent.json   resume only
  input/continuation/checkpoint/...
  output/raw/stdout.bin
  output/raw/stderr.bin
  output/raw/execution.json
  output/raw/runtime/<entire stopped runtime>
  output/parsed/results.json
  output/parsed/saved-values.json

<standalone destination>/
  manifest.json
  request.json                     explicit request supplied only
  raw/runtime/<entire source tree>
  parsed/results.json
```

The simulation root is ProjectPaths.simulation_memory, not a new root. Initial runtime has exactly the listed files/directories, plus complete resume checkpoint descendants. No unrelated files are imported into a fresh runtime. Fresh TRIMAUTO bytes are ASCII `1\r\n\r\n`. Resume uses `2\r\ncheckpoint\\\r\n`, with a relative path and terminal backslash as established by earlier tests. Do not infer default restore input/output identity.

JSON is UTF-8, sorted keys, indent two, terminal LF, finite JSON only. Paths are relative forward-slash, case-unique, without links, traversal, reserved Windows names or collisions. Every inventory entry is {path,sha256,size_bytes}, sorted by path; manifests do not hash themselves. Native bytes are copied unchanged except explicitly recorded continuation count/autosave/event edits. Outputs are published exclusively and sealed before terminal metadata. Working paths remain on any incomplete preservation.

## Exact report schemas

All new reports use schema_version:1 and engine:"trim". Object keys below are exact unless described as a keyed map. Null means unavailable, never zero. Numeric scientific quantities are printed decimal strings; native counts, Z values, indices, byte sizes, PIDs, handles, exit codes and line numbers are integers. Each field's native unit is explicit. No inferred numerical uncertainty is attached beyond printed precision.

- input/execution/control.json: {schema_version,engine,kind,parent_id,initial_count,total_ions,autosave_ions,event_prefix_sha256,edits}. kind is fresh/resume; parent fields null for fresh. edits is an ordered list of {path,field,before,after,source_sha256,result_sha256}; only total/autosave and appended event bytes may change for continuation. Fresh records have empty edits.
- output/raw/execution.json: {schema_version,engine,simulation_id,runtime_path,timeout_seconds,stage,process,completion,failure}. stage is preflight/launch/native/preserve/parse/verify/finished. process is null before launch or {pid,exit_code,elapsed_seconds,termination,popups,monitor_errors}; termination is exit/popup/timeout/cancelled/monitor-error/launch-error. popups is an ordered list of window evidence objects described above. completion is null or {complete,reason,checks}; checks is a list of {field,status,detail}. failure is null or {code,message}; code is preflight/launch/popup/timeout/cancelled/monitor/preservation/parse/incomplete/substitution/integrity. Shared metadata keeps its existing message/exit_code failure format.
- parsed/results.json: {schema_version,engine,summary,profiles,checkpoint,sources,issues,consistency,complete}. profiles is a map from the seven exact native profile filenames to successfully parsed profile objects; missing/malformed files have issues and no fabricated profile. complete means all required summary/profile structures parsed consistently, not execution success. sources inventories every supplied recognized file. issues is a list of {path,code,line,message}, where code is missing/format/truncated/unsupported/ambiguous/inconsistent, and line is integer or null. consistency is {request_checked,checks}; request_checked is false without an explicit request.
- A summary: {source,version,name,projectile,layers,target_energies,counts,statistics,unavailable}. projectile is {atomic_number,symbol,mass_amu,energy_keV,angle_degrees}; layers is ordered {index,name,bottom_depth_angstrom,density_g_cm3,density_atoms_cm3,composition} with composition entries {index,symbol,atomic_percent,mass_percent}; target_energies is ordered {index,symbol,displacement_eV,lattice_binding_eV,surface_binding_eV}. counts is {calculated,transmitted,backscattered}. statistics is {mean_range_angstrom,straggling_angstrom,vacancies_per_ion}; unavailable is a list of field paths.
- A profile: {source,version,calculated_ions,title,columns,rows,statistics,notes}. columns is ordered {index,label,unit,atom_entry_index,symbol}; atom_entry_index/symbol are null for non-species columns or unresolvable identity. rows is a list of lists of original numeric strings, including depth as column zero. statistics is a list of {name,value,unit}. notes retains native accuracy warnings, including low-ion ionization warnings. RANGE has depth, incident-ion density and target-entry recoil density columns; VACANCY has depth and its printed vacancy contributions; IONIZ/PHONON have depth and ion/recoil energy contributions; E2RECOIL has depth, incident-ion transfer and printed atom contributions; NOVAC has depth and replacements; LATERAL has depth, projected mean/straggling and radial mean/straggling. Exact heading/column counts must match the native variant and target-entry count where provided.
- A checkpoint: {sources,version,name,projectile,counts,mode,flags,atoms,layers,native_input,count_line}. counts is {current,total,autosave,transmitted,backscattered}; mode is native integer; flags preserves ordered native integer values with labels; atoms/layers use effective-request fields plus cumulative layer depth; native_input records parsed TRIMIN.sav controls including phase/correction/seed/EXYZ; count_line is {line,before}, used only for exact native edits. Native saved current RNG fields remain separate from initial seed evidence.
- parsed/saved-values.json: {schema_version,engine,checks,differences,unverified}; each comparison item is {field,requested,observed,precision,status}. status is equal/different/unverified; precision is exact or a printed decimal quantum string. Differences prevent execution success. Opaque binary save arrays are preserved and hashed, not declared scientifically verified.
- standalone manifest.json: {schema_version,engine,kind,files}; kind is trim-output-import. No fake simulation identity, engine hash, request or execution outcome is created for unprovenanced imported outputs.

## Continuation contract proposed for approval

Use only a terminal project record with a complete sealed saved set; arbitrary externally supplied save trees are not accepted as resumable simulations in this phase. Standalone external summary/profile parsing remains available. The parent must contain TRIMIN.sav matching the pinned engine family and material/input identity. All saved files present in the parent's save set are copied, not a guessed minimal subset. Required base saved names are the 12 observed 2008 files and six additional 2013 files recorded in TRIM_FINDINGS.txt; unknown additional saved files are retained. An incomplete checkpoint is reported unusable.

For resume, change the copied textual checkpoint's absolute total and autosave fields only; retain its current count and accumulated arrays. Record before/after hashes and edits. Keep original TRIM.IN as in the verified native continuation tests and preserve the new execution target in control.json; independent comparison uses control.json for resume count/autosave, and inherited TRIM input for immutable physics. Events may be appended only, with preserved original prefix and full file provenance. A fresh request is never silently converted to a resume request.

Resume progress is not clean completion. If the native process reaches its target but waits at a popup, record popup failure with achieved count, preserve every saved file, and allow explicit subsequent investigation. Native popup handling cannot be resolved by implementing resume differently without user approval.

Checkpoint durability means a stopped, byte-sealed copy. It does not prove the engine wrote all binary arrays atomically at the same moment. The parent records count/identity/manifest checks and that limitation. The user confirmed eligibility for timeout, popup and cancellation parents when complete checkpoint checks pass; the atomicity limitation remains explicit.

## Verification and acceptance

Use only D:/projects/agentic_srim/.venv/Scripts/python.exe for all Python commands, tests and subprocesses. No package installation is planned. Standard-library Windows APIs supply process/window ownership.

New unittest classes: TestTRIMRuntime, TestTRIMNative, TestTRIMExecution, TestTRIMOutputs, TestTRIMQueue. Extend existing TestTRIMStorage and metadata test class. Every test method has (self: ClassName) -> None and a docstring; no new setup/helper methods. Fixtures are constructed in each test with temporary paths under .cache/trim-execution/tests. The proposed behavioral methods are:

- Runtime: test_fresh_pinned_staging, test_offline_bundle_loading, test_resume_preserves_parent, test_resume_requires_complete_checkpoint, test_event_prefix_preserved, test_collision_and_tampering.
- Native: test_job_ownership_before_start, test_popup_reported_without_input, test_normal_window_allowed, test_timeout_owned_cleanup, test_cancel_owned_cleanup, test_monitor_failure, test_unrelated_process_untouched.
- Execution: test_success_requires_saved_counts, test_exit_zero_incomplete, test_popup_preserves_runtime, test_substitution_is_failure, test_parse_failure_is_preserved, test_launch_failure, test_preservation_failure_retains_worker, test_interruption_finalizes.
- Outputs: test_native_family_fixtures, test_printed_precision_and_units, test_multielement_identity, test_missing_and_truncated_profiles, test_ambiguous_duplicates, test_incomplete_summary, test_checkpoint_comparison, test_no_request_does_not_claim_success, test_native_accuracy_notes.
- Queue: test_input_order_and_same_worker_serialization, test_parallel_distinct_workers, test_preflight_before_launch, test_independent_failures, test_interrupt_cancels_active_jobs, test_explicit_cleanup.
- Storage additions: test_runtime_roundtrip, test_changed_runtime_retained, test_standalone_export, test_live_source_change_rejected, test_unsafe_import_paths, test_cleanup_preservation_guard.
- Metadata additions: test_trim_dispatch_and_sealing, test_trim_continuation_inventory, test_sr_lifecycle_unchanged.

Run full backend/SRIM regression suite with warnings as errors. Check module/class/function docstrings, all argument/return annotations including self/cls, absence of implementation comments, syntax, documentation links and diff whitespace. Verify actual signatures/schemas against the final approved document.

Native acceptance uses isolated .cache copies, starting with 2013 Standard and bounded short ordinary quick/full/monolayer runs. Parse all seven profiles and compare saved projectile/target/layer/damage/correction/control/count values. Verify complete runtime preservation and untouched installation hashes. Repeat one ordinary parser/runtime case on 2008 and Pro to establish format/engine-family handling, as confirmed by the user. Event-mode probes must demonstrate popup reporting with no clicks; they are expected failures if native interaction is required. Test popup text acquisition failure explicitly.

Resume acceptance compares an immutable parent with a new record, verifies copied full save inventories and recorded count edits, demonstrates checkpoint advancement where possible, and reports any popup rather than pretending unattended completion. Binary array physics equivalence remains unclaimed. Batch acceptance checks two distinct workers and same-worker task serialization; representative performance scaling is outside this task.

No native processes will be launched as part of writing this proposal. Implementation and native acceptance begin only after the complete design receives explicit implementation approval.

## Confirmed operational decisions

The user confirmed all five decisions after explanation:

1. Every detected popup is captured and reported without response. Terminate only the owned process job, preserve evidence and finalize failed/popup.
2. Resume may use sealed failed timeout/popup/cancellation records only when the complete saved set and textual identity/count checks pass. Preserve the explicit checkpoint-atomicity limitation; reject missing or inconsistent state.
3. Batches continue independent tasks after ordinary failures. Keep working runtimes by default (cleanup=False); explicit cleanup=True removes only verified preserved working copies. Permanent records remain.
4. Resume requires explicit absolute total_ions and autosave_ions, preserves immutable physics and the existing event prefix, and accepts only appended structured events. The source is an existing sealed project record.
5. Native verification starts with 2013 Standard and includes small 2008/Pro compatibility checks.

The user subsequently approved this complete file/API/schema/verification contract, and implementation began after an explicit implementation-start announcement.

## Approved annotation correction

The user authorized the coherent ordered-list event representation: prepare_trim_continuation uses events: list[JSONObject] | None. This corrects the draft annotation, matching the existing event schema and preserving ordering/prefix semantics; it changes no native format or behavior.

## Approved not-calculated profile amendment

During native acceptance, ion-quick exited cleanly and wrote NOVAC.txt explicitly stating that replacement collisions were not calculated with Kinchin-Pease estimates. The user approved distinguishing this from a parser error after explanation. This supersedes the universal 100-row requirement for that verified native text form only: return empty rows/columns/statistics, calculated_ions null and the original native explanation in notes. No zero-valued profile is inferred. Known full-cascade requests or checkpoints still require a numeric NOVAC table. Other numerical profiles retain the verified 100-row contract.

Native acceptance also established the 2008 seven-value versus 2013 nine-value saved flag records, automatic startup progress forms distinct from interactive popups, and a monolayer E2RECOIL heading omitting a printed data column. The parser preserves the latter as an unavailable label with an explicit note; it does not invent an element label. Popup reports remain automatic-response-free.

## Approved composition and aggregate-table amendment

A native multi-element check established that TDATA.sav stores normalized composition fractions: requested Si:O proportions 1:2 appear as approximately 0.333333:0.666667. After explanation, the user approved comparing these fractions without changing the original request, native byte files or entry ordering. compare_trim_saved_values now emits stoichiometry_fraction comparison paths, using 28-digit Decimal expected fractions and the unchanged observed tokens at printed precision. A genuinely different ratio still fails.

The same check established three-column aggregate RANGE/VACANCY/E2RECOIL quick-mode tables for multi-element targets. The user approved preserving those combined quantities with null atom-entry identities and explicit aggregate labels/notes. This supersedes the universal atom-entry-count-plus-two rule for the verified Kinchin-Pease aggregate variant. Full-cascade per-entry layouts retain their native column counts and species order. No per-element values are inferred from aggregate data. No signatures or top-level report keys changed.
