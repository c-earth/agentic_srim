# Python API reference

[Documentation index](../README.md) · [First calculation](../guides/getting-started.md) · [Data formats](data-formats.md) · [Current findings](../reviews/project-review-20261022.md)

This reference describes the shared backend and SR APIs, with links to TRIM's input and execution references. Import from `src.backend.<module>` or `src.srim.<package>.<module>` while running from the repository root.

## SRIM source layout

| Package | Contents and starting points |
| --- | --- |
| `src/srim/common/` | Shared [JSON and cleanup types](../../src/srim/common/types.py), [verified file helpers](../../src/srim/common/files.py), [allocation checks](../../src/srim/common/allocations.py) and [simulation records/lifecycle](../../src/srim/common/records.py). |
| `src/srim/resources/` | Materials, coefficient tables, compatibility, coordinated defaults and [shared selection/material correction](../../src/srim/resources/resolution.py). |
| `src/srim/cab/` | Core/bond strengths, cache, constants, Bragg dependencies and capture/recovery. |
| `src/srim/sr/` | SR inputs, [input models](../../src/srim/sr/models.py), resolution, runtime preparation, execution, queues, outputs and storage. |
| `src/srim/trim/` | TRIM inputs, [input models](../../src/srim/trim/models.py), resolution, native execution, queues, outputs, checkpoint recovery and storage. The packaged damage seed is in `trim/data/`. |

Import definitions directly from their owning modules; package initializers do
not re-export them. For example:

```python
from src.srim.common.types import JSONObject
from src.srim.resources.defaults import switch_sr_defaults
from src.srim.sr.runtime import prepare_sr_simulation
from src.srim.trim.execution import run_trim_simulation
```

The former flat paths such as `src.srim.runtime` and `src.srim.trim_execution`
have been replaced by `src.srim.sr.runtime` and `src.srim.trim.execution`.
Update external scripts and test patch targets accordingly. Callable signatures,
stored formats and calculation behavior are unchanged by this package move.

## Shared types and behavior

Native SR/TRIM input retrieval is documented in the [input-search API and format reference](input-search-format.md), with a [Python workflow](../guides/input-search.md). Import its APIs from `src.knowledge.index`, `src.knowledge.search`, `src.knowledge.source` and `src.knowledge.schema`. The catalog starts empty and newly finalized native runs index incrementally; existing runner signatures and metadata schemas remain unchanged.

`JSONObject = dict[str, JSONValue]`, where `JSONValue` is null/bool/int/float/str or recursive lists/string-keyed dictionaries. Domain numeric quantities generally use decimal strings. See the formats page for exact distinctions. `Mapping`, `Sequence`, `Callable` come from `collections.abc`; `Path` is `pathlib.Path`; `datetime` is timezone aware when supplied to record creation.

`paths` is a `ProjectPaths` obtained from `get_project_paths` and initialized by `create_base_directories`. `config` is a validated `Config`. `worker` is an allocated `CreatedDirectory(kind="worker", ...)`; `created` is an empty allocated SRIM directory. Constructing a frozen dataclass stores fields; API entry points perform validation. Frozen fields do not make nested JSON maps immutable.

Filesystem APIs can raise `OSError` subclasses for missing files, links, access failures or nonblocking lock contention. Most domain validation raises `ValueError`; calculation failures may raise `RuntimeError`. Native calculations are not automatically retried; shared resource/metadata lock acquisition uses bounded waiting. Successful partial publications are retained; exceptions commonly add notes identifying evidence/staging paths. Resource verification can create persistent lock files even when no application data is written.

## Configuration and paths

Sources: [config.py](../../src/backend/config.py), [paths.py](../../src/backend/paths.py).

```python
Config(timezone: str, srim_root: Path, hide_sr_window: bool = True,
       hide_trim_window: bool = True)
ProjectPaths(root: Path, simulation_memory: Path, postprocessing_memory: Path,
             cases: Path, workers: Path, resources: Path)
DirectoryReport(created: tuple[Path, ...], already_existed: tuple[Path, ...])

load_config(path: Path) -> Config
get_project_paths(root: Path) -> ProjectPaths
create_base_directories(paths: ProjectPaths) -> DirectoryReport
```

- `load_config` reads TOML afresh; timezone defaults to UTC, while SRIM root is required and absolute. Optional `[srim]` keys `hide_sr_window` and `hide_trim_window` accept only actual booleans and both default to true. Unknown keys/tables/types fail. It performs no discovery, expansion, directory creation or installation preflight. Malformed TOML and file-access exceptions propagate. Config stores both immutable flags; existing two-argument construction retains hidden defaults. The TRIM flag controls initial display; the default `cascade_output=None` reports native popups without a response. Explicit reviewed policies are described in the [TRIM execution reference](trim-execution-format.md).
- `get_project_paths` derives the agreed layout without I/O; relative roots raise `ValueError`.
- `create_base_directories` creates missing directories and reports created/existing paths in encounter order. It rejects symlinks/junctions, preserves partial creation on error and has no locking/rollback.

## Directory allocation and deletion

Source: [records.py](../../src/backend/records.py).

```python
CreateRequest(kind: RecordKind, timestamp: datetime | None = None)
CreatedDirectory(kind: RecordKind, path: Path, timestamp: datetime | None, suffix: int)
DeleteRequest(kind: RecordKind, name: str, reason: str | None = None)
DeletedDirectory(kind: RecordKind, path: Path, tombstone: Path | None)
BatchReport(completed: tuple[CreatedDirectory | DeletedDirectory, ...],
            failed_index: int | None, unattempted_indices: tuple[int, ...],
            partial_paths: tuple[Path, ...])
BatchOperationError(message: str, report: BatchReport)

create_directories(paths: ProjectPaths, config: Config,
                   requests: Sequence[CreateRequest]) -> BatchReport
delete_directories(paths: ProjectPaths, config: Config,
                   requests: Sequence[DeleteRequest]) -> BatchReport
```

`RecordKind = Literal["srim", "post", "case", "worker"]`. Workers reject timestamps and have `timestamp=None`; other records accept an optional aware timestamp converted using configuration. Each successful creation returns its concrete path/suffix. Batch inputs are validated before mutation; an operational failure raises `BatchOperationError` with the ordered successes, zero-based failure/unattempted indices and partial paths, preserving its cause. Empty batches return an empty report.

Deletion recursively removes the named record. Non-worker deletion writes durable pending/deleted tombstones and reserves the deleted identity; retry can finish pending deletion. Workers have no tombstone and their suffixes can be reused. Deletion does not check resource/CAB references. Parent-directory locks serialize allocations/deletions; contention raises. Worker deletion also requires its nonblocking execution lease and rejects active use. Resource and simulation metadata operations wait up to 30 seconds for shared locks; same-thread recursive acquisition fails immediately. See [retention](../guides/resources-and-cab.md#evidence-and-retention) before deleting records or workers.

## Immutable generic resources

Source: [resources.py](../../src/backend/resources.py).

```python
ResourceReference(category: str, name: str, version: str, sha256: str, size_bytes: int)

register_resource(paths: ProjectPaths, config: Config, category: str, name: str,
                  version: str | None, source: Path | str | bytes, *,
                  provenance: Mapping[str, object] | None = None,
                  reuse_key: str | None = None) -> ResourceReference
resolve_resource(paths: ProjectPaths, category: str, name: str,
                 version: str) -> ResourceReference
verify_resource(paths: ProjectPaths, reference: ResourceReference) -> Path
```

`register_resource` stores exact bytes (`str` content is ASCII; `Path` means a regular file). `version=None` allocates beyond the largest `vN`, starting at `v1`; explicit new versions must be `vN`. Same-label/same-bytes registration reuses the reference; conflicting bytes fail. Optional SHA-256 `reuse_key` requires automatic allocation and atomically reuses verified matching content. Payload/manifest publication uses staging, synchronization and rename under the resource-root lock. Partial artifacts survive failure.

`resolve_resource` reads a concrete manifest and returns its reference without hashing payload bytes; legacy concrete labels can be read. It does not resolve the coordinated `default` selector. `verify_resource` additionally verifies reference/hash/size and returns the payload `Path`. These generic APIs do not interpret SRIM payload semantics or run engines.

## Coordinated defaults and compatibility

Sources: [resources/defaults.py](../../src/srim/resources/defaults.py), [resources/compatibility.py](../../src/srim/resources/compatibility.py).

```python
switch_sr_defaults(paths: ProjectPaths, config: Config) -> JSONObject
read_sr_defaults(paths: ProjectPaths) -> JSONObject
verify_sr_baseline(paths: ProjectPaths, config: Config, baseline: JSONObject) -> None
read_module_version(srim_root: Path) -> tuple[Path, bytes]
normalize_atomdata(contents: bytes) -> tuple[bytes, list[dict[str, object]]]
normalize_compounds(contents: bytes) -> tuple[bytes, list[dict[str, object]]]
```

- `switch_sr_defaults` imports/reuses validated installation resources, creates numerical descriptors/baseline and the H-core-zero entry, then atomically switches the coordinated pointer. It returns the generation, not a recorded-generation wrapper. No engine runs. A failed switch retains staged resources and preserves the previous pointer if publication has not occurred. A concurrent pointer change fails.
- `read_sr_defaults` returns a fresh validated generation, checking canonical pointer/baseline bytes and resource/descriptor coherence. Missing initialization raises `ValueError`. No live installation or registration is needed.
- `verify_sr_baseline` compares a numerical baseline recipe (not a generation) with the configured installation and its stored immutable recipe; it returns `None` or raises on mismatch/access errors. Its implementation reads/validates the installation including compounds, although compound bytes are excluded from numerical baseline identity.
- `read_module_version` returns the selected source path and stable bytes, preferring `VERSION`, falling back to `_VERSION` only when absent. It rejects blank-first-line/non-ASCII version data and changing/unsafe files.
- `normalize_atomdata` only repairs row 37's known symbol defect and returns changes; strict full validation is the caller's responsibility. `normalize_compounds` applies the two known comma repairs and strictly parses the result. Both are pure byte transformations and preserve unrelated bytes.

## Materials and search

Source: [resources/materials.py](../../src/srim/resources/materials.py).

```python
parse_atomdata(contents: bytes) -> JSONObject
parse_compounds(contents: bytes) -> JSONObject
apply_material_overrides(kind: Literal["atomdata", "compound"], data: JSONObject,
                         overrides: Sequence[JSONObject]) -> JSONObject
search_compounds(data: JSONObject, *, name: str | None = None,
                 name_mode: Literal["exact", "contains"] = "exact",
                 composition: Mapping[ElementKey, str] | Sequence[ElementKey] | None = None,
                 composition_mode: Literal["contains", "elements", "ratio"] = "contains",
                 atomdata: JSONObject | None = None) -> list[JSONObject]
construct_sr_target(target: JSONObject, atomdata: JSONObject,
                    compounds: JSONObject | None) -> JSONObject
create_material_variant(paths: ProjectPaths, config: Config,
                        kind: Literal["atomdata", "compound"], *,
                        source: Path | str | bytes | JSONObject | None = None,
                        base_version: str | None = None,
                        overrides: Sequence[JSONObject] = ()) -> ResourceReference
```

The parsers are pure and return new ordered structures with lexical/source provenance. ATOMDATA requires its 92 fixed identities. Compounds accept strict native CP1252 or extended UTF-8 JSON. `apply_material_overrides` copies before applying validated edits and index mapping. Although annotated as `Sequence`, overrides currently require a list/tuple.

`search_compounds` returns independent matching records in effective order, including duplicate names; absence is `[]`. Name and composition combine with AND. Exact names are case sensitive, partial names casefold. Ratio filters require a mapping; mass candidates additionally require selected natural masses and compare exact rational quantities. Invalid filters/missing masses raise rather than silently skipping candidates. Composition sequences currently require list/tuple.

`construct_sr_target` returns exactly `target`, `composition`, `bonds`, `provenance`. It resolves explicit phase, density, effective natural masses/names and optional compound lookup, rejecting SR target Z outside 1–92. The direct helper requires a `bonds` map; public resolution fills `{}` for none/supplied requests that omit it. It performs no I/O or bond inference.

`create_material_variant` requires exactly one source/base version (`default` is allowed), applies edits and publishes immutable data/provenance. Unedited native input preserves bytes; edited compounds use canonical extended JSON. It changes no defaults and runs no engine.

## User CAB and coefficient variants

Sources: [cab/strengths.py](../../src/srim/cab/strengths.py), [resources/electronic.py](../../src/srim/resources/electronic.py), [resources/nuclear.py](../../src/srim/resources/nuclear.py).

```python
create_cab_variant(paths: ProjectPaths, config: Config, kind: CABKind, *,
                   source: CABSource | None = None, base_version: str | None = None,
                   overrides: Sequence[JSONObject] = ()) -> ResourceReference
load_cab(paths: ProjectPaths, kind: CABKind, version: str) -> CABData
create_electronic_variant(paths: ProjectPaths, config: Config, *,
                          source: Path | str | bytes | None = None,
                          base_version: str | None = None,
                          replacements: Mapping[int, str | bytes] | None = None
                          ) -> ResourceReference
create_nuclear_variant(paths: ProjectPaths, config: Config, *,
                       source: Path | str | bytes | None = None,
                       base_version: str | None = None,
                       replacements: Mapping[tuple[int, int], str | bytes] | None = None
                       ) -> ResourceReference
```

`CABKind = Literal["core", "bond"]`; `ElementKey = int | str`; `BondKey = str | tuple[ElementKey, ElementKey, str]`; `CABValue = int | float | str | Decimal`; `CABDictionary = Mapping[ElementKey | BondKey, CABValue]`; `CABSource = Path | str | bytes | CABDictionary`; `CABData = dict[int, str] | dict[tuple[int, int, str], str]`.

`create_cab_variant` requires exactly source/base, validates finite nonnegative strengths and normalized keys, applies set/remove operations and registers a frozen version. Source strings/bytes/Paths contain JSON; Python mappings may use symbols/tuple bonds. A default-derived copy includes only available entries with pinned provenance. `load_cab` returns a fresh available-value mapping from a concrete/default dataset; it never recovers missing values. Duplicate normalized identities are errors.

Coefficient creation accepts either raw source or base version with a nonempty replacements mapping; mixing modes fails. Replacement mode validates all 92 electronic rows (95 quote-aware fields each) or 4,278 ordered triangular nuclear rows (six fields each), checks row/key identity and preserves untouched bytes/endings. Source-only mode registers opaque bytes and does not provide equivalent table validation; preparation validates effective tables. Both return new references, retain source/base/patch provenance and leave defaults unchanged.

## Request validation and pure serialization/parsing

Sources: [sr/inputs.py](../../src/srim/sr/inputs.py), [sr/outputs.py](../../src/srim/sr/outputs.py).

```python
validate_sr_request(request: JSONObject) -> None
validate_sr_engine_request(request: JSONObject) -> None
render_sr_input(request: JSONObject) -> bytes
parse_sr_output(contents: bytes, request: JSONObject) -> JSONObject
```

`validate_sr_request` validates version-two unresolved intent or legacy version-one numeric none/supplied input. `validate_sr_engine_request` validates complete numeric target/correction data; derived numeric correction requires schema version two. Both return `None`, perform no I/O and reject invalid schemas/values with `ValueError`.

`render_sr_input` returns deterministic ASCII/CRLF bytes after engine validation. It preserves accepted numeric strings, target order and list ordering/duplicates. It does not resolve resources or derive a correction. Engine numeric strings require unsigned fixed-point syntax, unlike the broader scientific decimal syntax allowed in material/strength data.

`parse_sr_output` validates the engine request, strict ASCII format and selected stopping-unit label and returns version-one parsed JSON with source hash/size. List output must match row count and each requested energy at printed precision; range output validates structure, increasing positive energies and containment in the requested bounds with printed-rounding tolerance. It preserves printed numeric strings. It does not require exact range endpoints/grid coverage or fully cross-check header projectile/target identity. No files are read or written.

## Resolution, preparation and execution

Sources: [sr/models.py](../../src/srim/sr/models.py), [sr/resolution.py](../../src/srim/sr/resolution.py), [sr/runtime.py](../../src/srim/sr/runtime.py), [sr/execution.py](../../src/srim/sr/execution.py), [common/records.py](../../src/srim/common/records.py).

```python
ResolvedSRInputs(request: JSONObject, selections: JSONObject,
                 effective_request: JSONObject, resolved: JSONObject,
                 snapshots: tuple[tuple[str, bytes], ...],
                 engine_files: tuple[tuple[str, bytes], ...])
SRDependencyInputs(defaults: JSONObject, resources: JSONObject, atomdata: JSONObject,
                    engine_files: tuple[tuple[str, bytes], ...])

resolve_sr_inputs(paths: ProjectPaths, config: Config, worker: CreatedDirectory,
                   request: JSONObject, selections: JSONObject, *,
                   capture_timeout_seconds: float = 30.0,
                   dependency_timeout_seconds: float = 60.0,
                   progress: Callable[[JSONObject], None] | None = None) -> ResolvedSRInputs
verify_sr_resolution(paths: ProjectPaths, request: JSONObject, selections: JSONObject,
                      resolved: JSONObject, snapshots: Mapping[str, bytes]) -> JSONObject
prepare_sr_simulation(paths: ProjectPaths, config: Config, created: CreatedDirectory,
                       worker: CreatedDirectory, request: JSONObject, selections: JSONObject, *,
                       capture_timeout_seconds: float = 30.0,
                       dependency_timeout_seconds: float = 60.0,
                       progress: Callable[[JSONObject], None] | None = None) -> Path
verify_sr_runtime(paths: ProjectPaths, simulation_id: str, runtime: Path) -> JSONObject
run_sr_simulation(paths: ProjectPaths, config: Config, simulation_id: str, runtime: Path, *,
                   timeout_seconds: float = 60.0) -> JSONObject
```

- `resolve_sr_inputs` requires version-two intent and all six selections, pins defaults once, snapshots source/effective bytes, constructs the target, publishes requested variants and resolves needed dependencies. It returns original/effective JSON and byte inventories without running the main simulation or writing its record. It can run native dependency calculations/captures. Failures retain versions, records and captures with notes.
- `verify_sr_resolution` reconstructs the effective request from preserved sources/patches, verifies references/evidence and recalculates arithmetic; it returns the numeric engine request without process execution or rereading mutable aliases/source paths.
- `prepare_sr_simulation` validates empty main/worker allocations, resolves dependencies, stages exact five-file native inputs and initializes metadata; it returns the runtime `Path` with status `created`. It never launches the main SRModule. Public preparation requires version-two intent; legacy support is at numeric parsing/rendering and low-level record/runtime boundaries.
- `verify_sr_runtime` requires a created SR record, verifies sealed inputs, runtime location/exact inventory, hashes, coefficients and resolution, then returns the engine request. It does not execute; the caller must separately serialize execution.
- `run_sr_simulation` owns its worker-specific execution lease through preflight, shell-free execution, diagnostics, raw preservation, parsing and terminal metadata. `config.hide_sr_window` must be boolean: true passes Windows `STARTUPINFO` with `STARTF_USESHOWWINDOW`/`SW_HIDE`, false passes no startup override and permits normal display. This applies to Bragg dependencies through the shared launcher; the independent TRIM flag and CAB capture GUI are not used here. Invalid SR flags raise `ValueError` before filesystem mutation/launch. Positive finite timeouts exclude bool. It returns terminal metadata, including `failed` for expected preflight/launch/timeout/nonzero/missing-output/parse failures. Caller/state/collision errors and contention can raise before ownership; filesystem/publication errors propagate after best-effort failure finalization. `KeyboardInterrupt` is re-raised after cleanup/finalization. The exact final runtime is preserved under output/raw/runtime before terminal metadata publication. No retry, overwrite or worker deletion is performed by this single-run API.

Derived-correction progress reports each bond, then each composition entry's Bragg/core pair, with fixed total `len(bonds) + 2 * len(composition)`. States are `calculated`, `cached` (including pinned default entries and the H convention), `resolved` (user data/overrides), `fallback`, and `skipped` (unneeded Bragg/core work). Every successful nonempty contribution plan reaches its total; `none`/`supplied` modes emit no dependency events. This completion precedes later evidence verification and runtime preparation, which can still fail. Callback exceptions propagate with completed evidence retained. Source validation and target construction precede explicit publication, but a later engine-request validation failure can still leave already published variants. There is no all-or-nothing transaction across preparation.

## Prepared batches and permanent runtime storage

Sources: [sr/queue.py](../../src/srim/sr/queue.py), [sr/storage.py](../../src/srim/sr/storage.py), [common/types.py](../../src/srim/common/types.py), [cab/capture.py](../../src/srim/cab/capture.py).

```python
PreparedSRTask(simulation_id: str, runtime: Path)
SRTaskResult(task: PreparedSRTask, metadata: JSONObject | None,
             error: Exception | None, elapsed_seconds: float)
RetainedWorker(worker: Path, reason: str, error: Exception | None)
BatchCleanupReport(cleaned_workers: tuple[Path, ...],
                    retained_workers: tuple[RetainedWorker, ...])
SRBatchResult(task_results: tuple[SRTaskResult, ...],
               cleanup: BatchCleanupReport, elapsed_seconds: float)
run_sr_batch(paths: ProjectPaths, config: Config, tasks: Sequence[PreparedSRTask], *,
               max_workers: int, timeout_seconds: float = 60.0) -> SRBatchResult
preserve_sr_runtime(paths: ProjectPaths, simulation_id: str, runtime: Path) -> None
verify_preserved_runtime(paths: ProjectPaths, simulation_id: str, runtime: Path) -> None
cleanup_sr_batch(paths: ProjectPaths, worker_paths: Sequence[Path]) -> BatchCleanupReport
initialize_capture_simulation(paths: ProjectPaths, config: Config,
                                created: CreatedDirectory, runtime: Path,
                                defaults: JSONObject, request: JSONObject) -> JSONObject
finish_capture_simulation(paths: ProjectPaths, config: Config, simulation_id: str,
                            runtime: Path, failure: JSONObject | None = None) -> JSONObject
```

The dataclasses are frozen containers; construction performs no validation or I/O. Batch validation checks positive integer concurrency (excluding bool), a positive finite native timeout, unique identities, configuration, verified created runtimes and empty output trees before launching anything. An empty sequence returns an empty result. A task keeps its prepared worker. Same-worker tasks preserve submission order, independent workers can overlap, and results return in input order. Ordinary failed metadata and raised Exception instances are retained separately; BaseException stops dispatch, waits for active calls and re-raises without batch cleanup. Queue waiting is excluded from per-task elapsed time; total elapsed time includes validation and cleanup. There is no persistent queue, automatic restart or cross-caller queue merging.

Preservation copies the exact link-free runtime into synchronized staging under output/raw and verifies bytes before publishing output/raw/runtime. An identical existing archive is reusable; a different archive is never overwritten. Verification requires terminal metadata, unchanged permanent input/output inventories and a matching runtime copy. Failures retain source/staging paths. Batch cleanup runs after all task calls finish and checks only submitted workers. Unfinished/unpreserved records, unrelated or TRIM files, unsafe paths, active use and legacy CAB worker references retain that worker with a reason and optional exception. Each eligible worker has its verified runtime folders deleted under its lease and parent coordination; empty worker/sr/trim directories remain for reuse, along with permanent records/resources and stable coordination directories. Other workers can still be cleaned after one retention/error.

Capture initialization validates the eight native files and diagnostic request, then records exact native/default/baseline inputs through the ordinary lifecycle with engine name sr-capture. Capture completion archives the final runtime, preserves an available valid correction JSON as parsed/result.json and finalizes metadata. Successful completion requires a successful GUI cleanup manifest; preservation/publication failures propagate with working evidence retained. New CAB references point to these permanent simulation-owned files. Historical worker-owned references remain supported without automatic migration.

## Bragg and default recovery

Sources: [cab/bragg.py](../../src/srim/cab/bragg.py), [cab/recovery.py](../../src/srim/cab/recovery.py).

```python
resolve_bragg(paths: ProjectPaths, config: Config, worker: CreatedDirectory,
              inputs: SRDependencyInputs, atomic_number: int, *,
              timeout_seconds: float = 60.0) -> CABEntryResult
populate_bragg(paths: ProjectPaths, config: Config, worker: CreatedDirectory,
               inputs: SRDependencyInputs, *, atomic_numbers: Sequence[int] | None = None,
               timeout_seconds: float = 60.0,
               progress: Callable[[JSONObject], None] | None = None) -> list[CABEntryReference]
resolve_default_bond(paths: ProjectPaths, config: Config, worker: CreatedDirectory,
                     defaults: JSONObject, bond: BondKey, *,
                     capture_timeout_seconds: float = 30.0,
                     dependency_timeout_seconds: float = 60.0) -> CABEntryResult
resolve_default_core(paths: ProjectPaths, config: Config, worker: CreatedDirectory,
                     defaults: JSONObject, atomic_number: int, *,
                     capture_timeout_seconds: float = 30.0,
                     dependency_timeout_seconds: float = 60.0) -> CABEntryResult
populate_default_cab(paths: ProjectPaths, config: Config, worker: CreatedDirectory,
                     kind: CABKind, *, entries: Sequence[ElementKey | BondKey] | None = None,
                     capture_timeout_seconds: float = 30.0,
                     dependency_timeout_seconds: float = 60.0,
                     progress: Callable[[JSONObject], None] | None = None) -> list[CABEntryReference]
```

`resolve_bragg` validates pinned effective dependencies, atomically reuses/creates their descriptor and returns an existing entry or runs an ordinary elemental dependency simulation, publishing positive electronic stopping and evidence. It accepts Z 1–92; no arbitrary Bragg import/selection API exists. `SRDependencyInputs` is an advanced pinned-bundle interface, normally supplied through resolution.

Default bond/core resolvers accept a generation, reuse existing verified entries and otherwise use original all-default numerical inputs. Bonds support exactly the 18 native types. H core returns the fixed zero convention; non-H cores obtain elemental Bragg/all bonds and capture 18 estimates. Failed thresholds and negative/nonfinite estimates raise without clamping/search. Missing calculations may launch GUI captures; cancellation cleans only owned processes. Verified inactive cache entries remain readable, but missing recovery requires equality with the verified active coordinated generation before dependency work and each capture. A switch stops the next checked operation and retains completed evidence; it does not exclude a switch after admission or prevent publication of an already admitted pinned result.

Bulk APIs return references in selected order; `None` selects all 92 elements/cores or 18 bonds. Explicit lists/tuples must be nonempty, valid and unique after normalization. They report each selected completion outside publication/execution locks, stop on callback/error/interruption and retain completed entries. Per-operation timeout does not bound the whole bulk call or GUI cleanup allowances.

## Calculated-entry store

Source: [cab/cache.py](../../src/srim/cab/cache.py).

```python
CABEntryReference(dataset: ResourceReference, dependency_key: str,
                   entry_id: str, sha256: str, size_bytes: int)
CABEntryResult(reference: CABEntryReference, calculated: bool)

find_cab_entry(paths: ProjectPaths, dataset: ResourceReference,
               dependency: JSONObject, entry_id: str) -> CABEntryReference | None
load_cab_entry(paths: ProjectPaths, reference: CABEntryReference) -> JSONObject
publish_cab_entry(paths: ProjectPaths, dataset: ResourceReference,
                  entry: JSONObject) -> CABEntryReference
```

`find_cab_entry` returns a fully verified matching reference or `None` for genuine absence. Missing/corrupt evidence for a published entry raises. `load_cab_entry` verifies descriptor, exact bytes, dependency graph, capture/simulation files and arithmetic, returning independent JSON without native execution. `publish_cab_entry` validates, stages/synchronizes and publishes under a version lock; an equal semantic result reuses its existing identity, unequal results conflict. Alternative evidence/staging is retained.

These are advanced evidence-bearing APIs. Publication validates elemental dependency identity before publishing an immutable entry; the earlier ordering defect is closed. Prefer the orchestrated resolvers for calculation publication. See [resolved correctness findings](validation.md#resolved-correctness-review) and the current source contracts.

## Shared material correction

Source: [resources/resolution.py](../../src/srim/resources/resolution.py).

`resolve_material_correction` is the common SR/TRIM correction resolver. It
retains SR's correction formulas, omitted-entry fallback behavior, fixed reference
conditions and evidence checks. Derived correction may recover missing CAB
dependencies; `none` and `supplied` do not execute dependencies. Its source
docstring defines the complete typed arguments and return contract. TRIM layers
resolve sequentially and copy the shared evidence into each layer's provenance;
see [TRIM API effects](trim-input-format.md#apis-and-effects).

## Low-level simulation lifecycle

For TRIM's validators, renderers, versioned damage resources, resolver and bundle export, see the [TRIM input API reference](trim-input-format.md#apis-and-effects). Main staging, execution, continuation, prepared batches and summary/profile parsing are documented in the [TRIM execution reference](trim-execution-format.md) and [execution guide](../guides/trim-execution.md). The shared correction API is described in [shared material correction](#shared-material-correction).

Source: [common/records.py](../../src/srim/common/records.py).

```python
initialize_simulation(paths: ProjectPaths, created: CreatedDirectory, engine: JSONObject,
                       request: JSONObject, resolved: JSONObject,
                       continuation: JSONObject | None = None, *,
                       engine_inputs: Mapping[str, bytes] | None = None,
                       input_files: Mapping[str, bytes] | None = None) -> JSONObject
read_simulation(paths: ProjectPaths, simulation_id: str) -> JSONObject
start_simulation(paths: ProjectPaths, config: Config, simulation_id: str) -> JSONObject
finish_simulation(paths: ProjectPaths, config: Config, simulation_id: str,
                   failure: JSONObject | None = None) -> JSONObject
```

`initialize_simulation` validates the empty allocation/documents/resources, writes safe input evidence, inventories it and publishes metadata last under the record-parent lock. It returns stored metadata and preserves partial writes on failure. Optional engine inputs are safe flat filenames; additional evidence uses allowed nested paths. SR continuation must be `None`; continuation transport is outside this reference's scope.

`read_simulation` returns structurally validated metadata without requiring a live installation; it does not rehash the complete record or linked evidence. `start_simulation` requires created state, verifies resources and input inventory, and returns running metadata with a timestamp. `finish_simulation` inventories outputs and returns succeeded metadata only from running state with unchanged inputs; failure completion accepts created/running and retains evidence even after input damage. Both terminal states are immutable through these APIs. Lifecycle functions do not run engines or interpret output; normal callers should use preparation/execution.
