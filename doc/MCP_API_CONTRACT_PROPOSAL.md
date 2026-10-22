# MCP protocol, scientific tools, and implementation contract

Status: approved and implemented, 2026-10-22. Part of [the two-server proposal](MCP_SERVER_PROPOSAL.md). Names below are the approved public names. Original approved bytes are retained locally under `.cache/mcp-implementation/approved/`. A change to a schema, default, signature, file boundary, or lifecycle contract requires an amendment before implementation.

## Shared vocabulary and encoding

Use existing `JSONObject` and `JSONValue` aliases from `src.srim.metadata`. All new JSON documents have `schema_version=1` unless they embed an unchanged existing domain document. No NaN/infinity, duplicate object keys, unknown schema fields, bool-as-integer coercion, implicit numeric-string conversion, formula parsing, or unit inference. JSON Schema is Draft 2020-12. Native request schemas and validators remain authoritative for scientific semantics.

New identifiers are `job_<uuid>`, `object_<uuid>`, `artifact_<uuid>`, `candidate_<uuid>`, and `capability_<lowercase-hyphenated-name>`. UUIDs use lowercase canonical UUID spelling. Native simulation/worker/case/post identities retain their existing backend names. Paths are owner-relative forward-slash strings, never identities. Revisions are positive actual integers; hashes are lowercase SHA-256. Times use the existing project timezone policy; unavailable values are explicit null.

Every tool returns the same outer envelope, with exact keys:

```text
Response = {schema_version, ok, data, error, warnings, references}
error = null | {code, message, fields, retryable, partial_references}
warnings = [{code, message, source}]
references = [Reference]
Reference = {kind, id, revision, path, sha256, size_bytes}
```

`data` is null on an adapter error and otherwise has the operation-specific schema. `fields` is a list of JSON-pointer strings. `source` is a resource URI or null. `revision`, `path`, `sha256`, and `size_bytes` are nullable where the reference has no sealed content yet. Reference kinds are job, object, artifact, simulation, case, analysis, capability, improvement-event, and proposal. A pending job reference is not frozen evidence. Existing native `SourceReference`/`FileReference` objects remain unchanged inside operation-specific payloads; the new envelope does not replace them.

Adapter error codes: invalid-argument, not-found, incompatible, unavailable, unsafe-path, integrity, conflict, busy, queue-full, too-large, unsupported, interrupted, io, internal. Only actual lock/queue contention is retryable; native retry is always explicit. Domain `ValueError` becomes invalid-argument for caller validation or integrity for verified saved evidence; operation branches must make that distinction. OSError preserves a short message and safe partial references, with FileNotFoundError/not-found and FileExistsError/conflict. Unexpected exceptions become internal and retain a stderr diagnostic plus a job diagnostic reference. Do not serialize arbitrary exception objects, callback objects, bytes, Paths, or stack traces into protocol data.

Protocol/JSON-RPC failures use SDK-native protocol errors. Tool/domain failures use `isError=true` with the structured Response. A submitted native calculation can return terminal failed metadata inside a successfully read job response: `ok=true` means the read operation succeeded, not that the calculation succeeded. `job.state`, `native_status`, `parser_complete`, and scientific warnings remain distinct.

## Capability and schema catalog

`CapabilityCard = {id, title, family, summary, tool_names, implementation, exposure}`. `CapabilityDetail` extends this with `input_schema_ids`, `output_schema_ids`, `parameters`, `units`, `defaults`, `effects`, `compatibility`, `automation`, `availability`, `evidence`, `limitations`, `example_ids`, and `python_apis`. Family is discovery/resources/sr/trim/execution/results/search/retention/cases/analysis/reuse/improvement.

Parameter entries are `{pointer, description, value_type, unit, required, default, enum, minimum, maximum, bounds_kind, reason, source}`. Null bounds are accompanied by a reason. `bounds_kind` is schema/native-observed/adapter/unknown. `implementation` is implemented/planned/absent. `exposure` is mcp/python-only/description-only. Compatibility contains installed identity plus supported families/modes and rejection reasons. Automation states report-only/reviewed-policy/interactive/unsupported. Evidence entries are `{claim, kind, reference, scope}`; kind is source/synthetic/native-fixture/native-run/inferred/unverified. Claims about fixtures and runs cite existing retained evidence; missing evidence is reported rather than upgraded by a catalog author.

Catalog schema IDs are stable names such as `sr.request.v2`, `sr.engine-request.v2`, `sr.selections.v1`, `trim.request.v1`, `trim.engine-request.v1`, `trim.selections.v1`, `trim.bundle-manifest.v1`, `trim.recovery.v2`, `knowledge.filter.v1`, `mcp.job.v1`, `project.case.v1`, and `project.analysis.v1`. Schema version numbers identify the actual embedded domain format. An object called v1 in an adapter must not relabel an existing v2 native document.

Reuse the full field contracts in [data-formats](reference/data-formats.md), [TRIM input proposal](TRIM_INPUT_PROPOSAL.md), [TRIM execution reference](reference/trim-execution-format.md), [checkpoint recovery guide](guides/trim-checkpoint-recovery.md), and [input-search format](reference/input-search-format.md). Catalog JSON Schemas must expand those existing field definitions, including closed-object alternatives, required nulls, one-based indices, numeric-string syntax, and engine-version differences. They must not contain unconstrained `{type: object}` placeholders for a known request format. The implementation tests must validate both catalog schemas and source validators against the same positive/negative fixtures. Schema resources are static JSON data assembled by catalog functions; no source-code execution or signature scraping at runtime.

Basic tool arguments are also closed objects. The signatures in the following tables specify required parameters, types and defaults; omission is allowed only where a default is shown. Union variants are discriminated by `kind` or `engine`. All arrays preserve caller order unless explicitly described as sorted. Catalog/example/tool/resource listings sort by stable ID/name. Paging uses offset >= 0 and limit 1–1000, default 100. Operations exposing expected_generation reject a changed generation rather than mixing pages; ordinary live metadata listings are explicitly snapshots per call. Native search keeps its own UUID generation semantics.

## Tools on both servers

These are protocol signatures, not additional per-tool Python wrappers. The catalogs declare them; shared protocol handlers validate and dispatch to the operation functions specified below. Each signature returns Response with the data type described here.

| Exact signature | Data, responsibilities, effects, and errors |
| --- | --- |
| `capabilities_list(family: str | None = None, query: str | None = None, offset: int = 0, limit: int = 100, expected_generation: str | None = None) -> Response` | Paged cards, total and catalog SHA-256 generation. Query casefold-matches ID/title/summary; family must be a catalog family. Reads only. |
| `capability_get(capability_id: str) -> Response` | One complete detail. Unknown identity is not-found. Installed checks are explicit cached-in-call reads, never default import or native launch. |
| `schema_get(schema_id: str) -> Response` | Exact expanded schema, schema hash and version. Not-found for unknown ID. |
| `example_get(example_id: str) -> Response` | Template request, purpose, prerequisites, expected result shape and limitations. Does not run it. |
| `job_get(job_id: str, after_sequence: int = 0, wait_seconds: float = 0.0) -> Response` | Snapshot plus up to 100 progress events newer than the supplied sequence. Wait 0–30 finite seconds, interrupted by shutdown; wait is asynchronous. Terminal snapshots include result references. No native polling invented beyond existing API callbacks. |
| `jobs_list(state: str | None = None, offset: int = 0, limit: int = 100) -> Response` | This server's jobs, sorted creation sequence. Invalid state fails. No cross-server mutation or implicit restart. |
| `job_cancel(job_id: str) -> Response` | Returns `{accepted, state, cancellation, reason}`. Queued work can be cancelled; active cancellation only where the backend supports it. No process-name kill or fabricated cancellation success. |
| `artifact_read(reference: Reference, offset: int = 0, length: int = 262144) -> Response` | Hash-verified chunk `{encoding: base64, content, offset, size_bytes, eof}`. Must name a listed artifact file and supply its hash/size; changed, unsafe or missing evidence fails. A smaller configured cap overrides the signature ceiling and is advertised. |

Every mutating scientific tool below also requires `request_id: str` as its first argument. It must be a canonical UUID supplied by the caller. Such tools return a JobAccepted object `{job_id, state, operation, request_sha256}` immediately after the request is durably accepted. They do not wait for native work. Repeating the same request ID/name/canonical arguments returns the same job; changed arguments conflict. This rule includes preparation, defaults import, variant publication, index updates, and cleanup.

## srim_mcp tools

| Exact signature after any required request_id | Data and backend interaction |
| --- | --- |
| `environment_get() -> Response` | Python/SDK versions, configured native installation identity, default-pointer readiness, server limits, cancellation matrix. Stable installation inspection only; missing installation is reported per capability so offline result reads remain usable. |
| `defaults_get(kind: Literal['sr', 'trim']) -> Response` | `read_sr_defaults` or `read_trim_defaults`; missing defaults unavailable; never imports. |
| `defaults_switch(request_id: str, kind: Literal['sr', 'trim'], damage_version: str = 'builtin') -> Response` | Job: exact existing switch API; SR rejects non-builtin damage_version. Publishes/reuses resources and switches only requested pointer. No automatic second switch. |
| `resources_list(category: str | None = None, name: str | None = None, offset: int = 0, limit: int = 100) -> Response` | Enumerates safe immutable manifests; returns verified ResourceReferences and per-entry issues, never trusts directory names alone. No calculated-entry recovery. |
| `resource_read(reference: JSONObject, section: Literal['manifest', 'payload', 'entries'] = 'manifest', offset: int = 0, limit: int = 100) -> Response` | Backend reference verification. Manifest JSON, paged parsed material/CAB/damage rows/available calculated-entry summaries, or a binary payload reference. Electronic/nuclear opaque bytes are not interpreted as trusted physical data. |
| `compounds_search(compound_selection: JSONObject, atomdata_selection: JSONObject, filters: JSONObject, offset: int = 0, limit: int = 100) -> Response` | Apply selected effective material sources/overrides without publishing, then `search_compounds`; return source hash/index identity and matches. `create_version` must be false for both selections. Filters mirror name/name_mode/composition/composition_mode arguments; atomdata resolves mass-based ratios. |
| `variant_create(request_id: str, spec: JSONObject) -> Response` | Job calls appropriate material/CAB/electronic/nuclear/damage variant API. Spec discriminator and wire normalization below; returns immutable ResourceReference. Does not move defaults. |
| `cab_populate(request_id: str, kind: Literal['core', 'bond'], entries: list[JSONValue], worker_id: str | None = None, capture_timeout_seconds: float = 30.0, dependency_timeout_seconds: float = 60.0) -> Response` | Job calls `populate_default_cab` for an explicit nonempty unique list. Full-population request uses the explicitly enumerated 92 or 18 catalog identities rather than an accidental null. Preserves progress and evidence. Bragg reuse/creation is reachable through SR/TRIM resolution; arbitrary Bragg recipes remain Python-only. |
| `request_validate(engine: Literal['sr', 'trim'], request: JSONObject, explicit: bool = False) -> Response` | Pure existing validator; returns `{valid: true, checked: 'schema', not_checked: [...]}`. No selections/default resolution, dependency execution, or claimed native viability. |
| `input_render(engine: Literal['sr', 'trim'], request: JSONObject) -> Response` | Pure rendering of an explicit numeric engine request. Returns exact encoding/hash/size and base64 native file contents, including optional TRIM.DAT. Reject too-large rather than truncating. No unresolved-input resolution. |
| `sr_resolve(request_id: str, request: JSONObject, selections: JSONObject, worker_id: str | None = None, capture_timeout_seconds: float = 30.0, dependency_timeout_seconds: float = 60.0) -> Response` | Job calls resolve_sr_inputs; persists a pinned inspection object with JSON documents and byte inventories. Main SR not executed. This is not a public SR replay bundle; a later sr_prepare resolves its supplied selections again. |
| `sr_prepare(request_id: str, request: JSONObject, selections: JSONObject, worker_id: str | None = None, capture_timeout_seconds: float = 30.0, dependency_timeout_seconds: float = 60.0) -> Response` | Job allocates a new simulation and optionally worker, calls existing prepare API and returns PreparedReference. Resource resolution/CAB dependencies may execute. No automatic main run. |
| `trim_resolve(request_id: str, request: JSONObject, selections: JSONObject, worker_id: str | None = None, capture_timeout_seconds: float = 30.0, dependency_timeout_seconds: float = 60.0) -> Response` | Job resolves and writes a verified immutable TRIM input bundle to server object storage. No main allocation/execution. Returns BundleReference with manifest hash. |
| `trim_bundle_load(request_id: str, source: JSONObject) -> Response` | Job verifies an approved local exported bundle and copies it to a new server-owned immutable object. Sources and manifest rechecked; no native run or alias re-resolution. |
| `trim_prepare(request_id: str, bundle: JSONObject, worker_id: str | None = None, cascade_output: str | None = None) -> Response` | Job verifies BundleReference, allocates simulation/optional worker and calls prepare_trim_simulation. cascade_output is null/none/statistics/detailed with existing policy checks; no inferred response preference. |
| `trim_continue_prepare(request_id: str, parent_id: str, total_ions: int, autosave_ions: int, worker_id: str | None = None, events: list[JSONObject] | None = None, cascade_output: str | None = None) -> Response` | Job allocates child and invokes prepare_trim_continuation, retaining exact parent/checkpoint eligibility/count/event checks. No in-place rerun. |
| `run_submit(request_id: str, prepared: JSONObject, timeout_seconds: float | None = 60.0) -> Response` | Job verifies PreparedReference and runs existing SR or TRIM lifecycle API. SR rejects null timeout; TRIM supports it. Active TRIM uses the cancellation callback; active SR has timeout-only cancellation. |
| `batch_submit(request_id: str, engine: Literal['sr', 'trim'], tasks: list[JSONObject], max_workers: int, timeout_seconds: float | None = 60.0, worker_timeouts: JSONObject | None = None, cleanup: bool | None = None) -> Response` | Job executes existing prepared-batch API; no parameter expansion or preparation inside the batch. Explicit nonempty PreparedReferences, matching engine, unique simulation IDs. SR rejects worker_timeouts/null timeout/cleanup=false because its existing batch API always performs verified cleanup; use individual run_submit jobs to retain SR working runtimes. Cleanup null means existing SR automatic cleanup or TRIM retain default; SR true is also accepted. TRIM passes explicit cleanup through. Per-worker override keys/values retain native validation. Active batch cancellation unsupported in this initial adapter; queued cancellation supported. |
| `simulations_list(engine: str | None = None, status: str | None = None, offset: int = 0, limit: int = 100) -> Response` | Safe native-record metadata discovery, sorted IDs, includes created/running/failed and CAB captures with per-record read issues. Does not treat input-search membership as a complete run list. |
| `simulation_get(simulation_id: str) -> Response` | Structurally validated metadata, inventory listings and current available reference types. Metadata read alone is explicitly not a full hash verification. |
| `source_get(simulation_id: str) -> Response` | Existing project_simulation_inputs for eligible terminal native SR/TRIM records; returns existing verified source/inputs/file references, without requiring catalog membership. CAB GUI capture records remain metadata/evidence only. |
| `source_read(reference: JSONObject, file: JSONObject, offset: int = 0, length: int = 262144) -> Response` | Verify exact native source and listed file hash/size, then return bounded base64 chunk. Chunk implementation streams and rechecks the whole selected file; no 64-MiB whole-file allocation through the old helper. |
| `result_read(simulation_id: str, section: str = 'summary') -> Response` | Recheck terminal metadata/file inventories and return requested saved result section plus source/provenance/availability. Section enum: summary/checkpoint/consistency/issues/saved-values/config-integrity/availability/manifest. No reparse or overwrite. SR summary includes units/energy mode/source and row count; tables are separate. Missing section unavailable. |
| `table_read(simulation_id: str, table: str, offset: int = 0, limit: int = 100) -> Response` | Verify result file, select SR rows, native TRIM profile or recovered profile; return columns/units/availability/total/page and provenance. Table IDs are listed by result manifest; no JSON-path evaluation. Unknown table not-found, not-calculated table unavailable with reason, never zeros. |
| `trim_availability_get(simulation_id: str) -> Response` | Existing read_trim_output_availability plus required source verification. Explicitly report unavailable when no opted-in availability report exists. |
| `trim_parse(request_id: str, source: JSONObject, request: JSONObject | None = None) -> Response` | Job calls existing parse_trim_directory on approved frozen source and new artifact destination; returns parse report/artifact reference. No external collection indexing or historical success assertion. |
| `trim_recover(request_id: str, source: JSONObject) -> Response` | Job calls export_trim_checkpoint_recovery on complete frozen approved source/new artifact destination. Preserves recovery-v2 labels/warnings and all outputs. Does not resume or override native lifecycle. |
| `inputs_search(filters: JSONObject | None = None, offset: int = 0, limit: int = 100, expected_generation: str | None = None) -> Response` | Exact existing search_inputs semantics. Does not search results or import old membership automatically. |
| `index_update(request_id: str, operation: Literal['initialize', 'update', 'rebuild'], simulation_ids: list[str]) -> Response` | Job calls corresponding existing index API. Initialize requires []; update/rebuild retain native membership rules; rebuild explicit/nonempty. Separate report failures never change native status. |
| `workers_cleanup(request_id: str, engine: Literal['sr', 'trim'], worker_ids: list[str]) -> Response` | Job invokes verified cleanup for explicit unique workers; returns cleaned/retained with reasons. Empty worker allocations remain. No delete_directories/permanent deletion tool. |

result_read/table_read and other structured-file readers enforce max_result_bytes before loading a JSON document. A larger file returns too-large with a verified file reference usable through source_read/artifact_read; it is not partially parsed or silently truncated. This is an adapter memory bound, not an engine output limit.

No `sr_run`/`trim_run` convenience with hidden preparation is necessary: the explicit prepare + run sequence covers fresh calculations while keeping resolution side effects visible. Agents can compose it using documented examples. The catalog explicitly maps advanced pure helpers, low-level native monitoring, calculated-entry publication, normalization, lifecycle initialization/finalization, and arbitrary Bragg recipes to their Python interfaces; these are not exposed as bypasses around verified orchestration.

### Wire specifications used above

`SourceSpec` is exactly one of `{kind: 'artifact-directory', reference: Reference}` or `{kind: 'local-directory', path: str}`. Local paths must be absolute, within the root or configured read roots, link-free and frozen throughout the operation. The artifact-directory reference pins a manifest which covers all directory files. A local caller's assertion of quiescence alone does not establish atomic checkpoint saving; source-before/after hashes still apply.

`PreparedReference = {engine, simulation_id, worker_id, runtime_manifest_sha256}`. Runtime path is derived from validated IDs, never accepted from the client. Before execution verify the original manifest and existing runtime API. Created records prepared outside MCP can be referenced if the caller supplies an exact matching manifest hash/worker identity; no server-private handle is required.

`BundleReference = {id, manifest_sha256}` refers to a persisted TRIM bundle. New SR resolution objects have `{id, engine: 'sr', request, selections, effective_request, resolved, snapshots, engine_files}` where byte inventories contain `{path, sha256, size_bytes}` and corresponding bytes are stored under the object's files tree. These objects are inspection evidence, not a new SR replay API. TRIM uses existing exported bundle format unchanged.

`VariantSpec` has kind atomdata/compound/core/bond/electronic/nuclear/damage and exactly one source or base_version. Source is `{kind: 'inline', encoding: 'utf8' | 'base64', content: str}` or `{kind: 'local-file', path: str}` or `{kind: 'json', value: JSONObject}` for material/CAB/damage only. Local source roots are checked. Material/CAB/damage accept overrides (default []) and reject replacements. Coefficients accept replacements only in base mode; wire replacements are ordered unique rows `{key: int | list[int], row: str}` and are converted to integer/tuple dictionary keys only after identity validation. The underlying APIs retain their strict/opaque source distinctions. Unknown or mutually exclusive fields fail before queuing.

Native selections retain their exact version/file/inline formats. Paths inside them receive the same local-root checks before resolution, including file sources nested in layer-related input. `create_version` remains explicitly supplied by the native selection schema. Tool transport never supplies it implicitly. Native schemas, counts, collision choices, event vectors, output flags, and correction defaults are not loosened by accepting JSONObject at the internal dispatch boundary.

## Durable jobs, ownership, and cancellation

Each server owns `memory/mcp/<server>/jobs/job_<uuid>/` with immutable `request.json`, atomically replaced `state.json`, append-only numbered event JSON files under `events/`, and sealed `result.json` or `error.json`. Output objects/artifacts live under the same server's `objects/` and `artifacts/`. Job state is operational bookkeeping, not native metadata. Owner request contains `{schema_version, id, server, request_id, operation, arguments, request_sha256, created_at, sequence}`. The hash covers canonical operation/arguments, sorted keys, compact UTF-8 JSON, excluding the request ID.

State has exact fields `{schema_version, id, state, phase, updated_at, sequence, worker_ids, simulation_ids, native_status, parser_complete, cancellation, progress, result, error}`. `state` is queued/running/cancelling/succeeded/failed/cancelled/interrupted. Phase is accepted/allocating/resolving/preparing/executing/preserving/parsing/indexing/cleanup/finalizing/finished, nullable when the existing backend cannot report a reliable phase. Native runner start/end and existing progress callbacks can update phase; never infer percent/remaining ions from a wall clock. `progress` is null or the unmodified last domain callback with its source and sequence. Native metadata status is created/running/succeeded/failed or null; parser_complete is bool or null. Cancellation is queued-only/cooperative/timeout-only/unsupported/complete. Result/error are null or sealed references. Every transition is synchronized and durably published before notifying waiting readers.

The write dispatcher returns exactly `{outcome, data, references, warnings}`, with outcome succeeded/failed/partial/cancelled. The saved JobResult adds schema_version and operation to those fields. Native failed metadata produces outcome failed with that metadata retained in data; any failed/error batch member produces partial if some succeeded, otherwise failed. Explicit successful TRIM cancellation produces cancelled. Ordinary management success produces succeeded. The manager maps partial to state failed while retaining per-member results. An exception additionally produces error.json. Reading any terminal job remains a successful protocol operation even when the job outcome is failed. Job status never rewrites native status; indexing/cleanup issues remain their own fields/results.

Progress transport is `{source, phase, worker_ids, simulation_ids, data}`. Source is adapter/domain; phase is one of the declared phases or null; ID lists contain only validated owned/selected identities and may be empty. Data is an independent unmodified copy of the original domain callback, or adapter phase detail. The manager records newly allocated worker/simulation IDs durably on an adapter event before subsequent preparation/execution, updates state under its short lock, and appends the numbered event. This supplies ownership/quarantine bookkeeping without a dispatcher reaching into JobManager internals. Domain callbacks cannot substitute arbitrary IDs for already reserved workers.

Acceptance writes the request and queued state atomically before returning a job ID. A process-held per-server workspace lock prevents two srim_mcp instances or two project_mcp instances from dispatching the same workspace. Request IDs index existing requests durably; a process restart never silently re-executes an existing accepted request. Accepted unfinished jobs become interrupted until inspected. Referenced native records are read but not rewritten to manufacture a terminal state; interrupted job evidence can identify partial records and working paths. No durable execution queue/restart promise is added to the native APIs.

Do not claim exactly-once native execution: a crash can occur after dispatch but before the adapter stores its result. Repeated request IDs return that existing uncertain/interrupted job. A new attempt requires a new explicit request ID and appropriate new native record. Server startup quarantines worker IDs referenced by interrupted unfinished jobs; the initial adapter does not offer an unsafe automatic release. Manual reconciliation must establish no surviving owned/native activity and preserved evidence before a separate approved recovery/release change. Allocating a new worker avoids reusing that quarantined working directory.

Queued cancellation has no native effects. Active individual TRIM execution sets the callback Event and waits for native owned-job stop/preservation/finalization. It returns cancelled only after native evidence confirms the cancellation ending; a racing successful completion returns success. Active SR execution exposes timeout-only behavior: cancel returns accepted=false with the remaining configured timeout policy, without raising an asynchronous exception into another thread. Resolution/CAB recovery, batch execution, default/resource publication and project writes are queued-cancellable only; an in-progress commit is allowed to finish. Phase changes expose the current cancellation mode.

On graceful shutdown, stop admissions, cancel queued work, signal active individually cancellable TRIM runs, and wait for all active noncancellable calls and finalization. Active SR and CAB work can take their existing per-operation bounds; a TRIM batch with timeout None can wait indefinitely. Advertise that fact before batch submission and in capabilities. Do not kill the server on an invented global deadline or leave publication half-complete intentionally. Forced host termination is outside the graceful guarantee: TRIM's owned native job has its existing kill-on-close behavior; SR may leave a native child. Restart must quarantine unfinished worker identities and cannot declare the machine idle from a released Python lock alone.

The scheduler admits at most max_pending queued jobs. max_parallel is a global slot budget per server, not just a ThreadPoolExecutor size. Individual operations reserve one slot and their selected worker; batches reserve their requested max_workers slots and all participating worker IDs until completion. Reject a batch exceeding the configured slot budget. The earliest eligible queued job dispatches; jobs sharing a worker are FIFO. Existing native leases remain final authority against other Python callers and SR/CAB/TRIM sharing. A lock failure is reported, without hidden retries. A null worker allocates a new worker only after dispatch and records it before preparation; no unreviewed scaling/reuse heuristic. Batch result order and native cleanup behavior remain documented independently of execution order.

Do not hold case/analysis, adapter-journal, or global queue locks while running native APIs or waiting on their callbacks. Progress callbacks enqueue snapshots and return; they cannot call back into resource publication. Shared resource locks retain existing bounded behavior. Both server clocks use monotonic elapsed measurements and existing configured wall-clock time rules.

## Resources and host behavior

Each server lists `srim://capabilities`, `srim://schemas`, `srim://examples` or `project://capabilities`, `project://schemas`, `project://examples`. Templates are `scheme://capabilities/{id}`, `scheme://schemas/{id}`, `scheme://examples/{id}`, `scheme://jobs/{id}`, and `scheme://artifacts/{id}/manifest`. Project additionally exposes case/analysis/capability/improvement references from its appendix. IDs are validated before lookup; resource parameters never become arbitrary filesystem paths. Resource output uses the same bounded readers/schema hashes as tools. Initial subscriptions/list-change notifications/prompts/sampling/elicitation are not advertised; polling/tools work without host-specific support. Read-only tool annotations are accurate hints, not an authorization mechanism.

The server supplies concise initialization instructions: discover before choosing inputs; quantities require explicit units; validate then prepare; preparation may execute dependencies; inspect native status and availability; preserved checkpoints are the continuation result route; project organization lives in the other server. A host may attach only one server and still use its independent capabilities.

## Files and complete Python callable boundaries

The following is the proposed source inventory. New helper callables beyond this catalog require a proposal amendment before coding. All source files begin with module docstrings; every function/method/class has the required argument/return/effect/error docstring and complete annotations, including self/cls. Implementation code contains no comments. Local aliases are used below only to keep signatures readable:

`ServerName = Literal['srim_mcp', 'project_mcp']`; `Progress = Callable[[JSONObject], None]`; `Cancel = Callable[[], bool]`; `ReadOperation = Callable[[str, JSONObject, ServerContext], JSONObject]`; `WriteOperation = Callable[[str, JSONObject, ServerContext, Cancel, Progress], JSONObject]`. SDK types come from installed mcp/mcp.types; no local class shadows the SDK package.

### src/mcp/common/contracts.py

Owns configuration, strict envelope/schema validation and wire conversion; imports standard library, jsonschema, existing Config/ProjectPaths/JSONObject. Defines frozen dataclass `ServerContext(name: ServerName, paths: ProjectPaths, config: Config, limits: JSONObject)`; construction stores these fields, validation happens in load_context. It does not own a job manager or mutable global configuration.

```python
load_context(root: Path, server: ServerName) -> ServerContext
load_json(contents: bytes) -> JSONObject
canonical_bytes(document: JSONObject) -> bytes
validate_document(document: JSONObject, schema: JSONObject) -> None
make_response(data: JSONValue = None, *, error: JSONObject | None = None, warnings: list[JSONObject] | None = None, references: list[JSONObject] | None = None) -> JSONObject
error_response(error: Exception, *, integrity: bool = False, references: list[JSONObject] | None = None) -> JSONObject
```

load_context requires absolute safe root, reads explicit settings.toml/mcp.toml, validates exactly the main proposal's config and returns an immutable context; no installation preflight, directories, or engines. load_json rejects duplicate keys/non-object/nonfinite data and invalid UTF-8 with ValueError. canonical_bytes validates finite JSON and returns sorted two-space UTF-8/final-LF bytes; request hashes separately use compact canonical encoding inside jobs. validate_document checks schema validity and values, raising ValueError with JSON-pointer paths and no writes. make_response returns a copied validated envelope without I/O; error_response performs the deterministic mapping above, with no raw exception leakage.

### src/mcp/common/files.py

Owns bounded safe I/O, hashes, exclusive publication, and server locking; no scientific interpretation. Uses standard library and existing agreed path/config conventions.

```python
safe_path(root: Path, relative: str) -> Path
allowed_source(context: ServerContext, path: str, *, directory: bool) -> Path
read_verified_chunk(path: Path, sha256: str, size_bytes: int, *, offset: int, length: int) -> JSONObject
publish_json(path: Path, document: JSONObject, *, replace: bool = False) -> JSONObject
publish_files(destination: Path, files: Mapping[str, bytes]) -> JSONObject
acquire_lock(path: Path, *, timeout_seconds: float = 30.0) -> ContextManager[None]
```

safe_path validates owner-relative paths and every existing ancestor, rejects traversal/drive/UNC/device/case-collision/link/junction escapes; returns Path, writes nothing. allowed_source validates root allowlist, string representation and actual file/directory kind; returns safe absolute Path. read_verified_chunk validates actual integers/range, streams full-file hash/size with stable before/after file identity, returns requested base64 slice plus verified total; bounded memory, no writes; mismatch ValueError, unsafe/missing I/O OSError. publish_json writes/fsyncs exclusive staging then publishes exclusively or replaces only operational state explicitly permitted by caller, returning path/hash/size; publish_files validates all safe unique names, writes/fsyncs a new directory and manifest with hash/size inventory, rechecks bytes before rename; no overwrite/rollback, partial locations in exception notes. acquire_lock is a typed contextmanager over a regular lock file with bounded msvcrt acquisition, yields None, releases in finally; startup server locks use timeout=0 and stay held for process lifetime. Locks reject same-thread recursion and unsafe paths, raise OSError on contention.

### src/mcp/common/jobs.py

Owns durable adapter jobs and scheduling, independently of native lifecycle. `JobManager(context: ServerContext, execute: WriteOperation)` is a mutable service object exposing context and owning queue/threads/Events/conditions/request-ID index. Constructor initializes in-memory state only; start performs locked disk startup and interrupted-job reconciliation. It never imports scientific modules.

```python
JobManager.__init__(self: JobManager, context: ServerContext, execute: WriteOperation) -> None
JobManager.start(self: JobManager) -> None
JobManager.submit(self: JobManager, operation: str, arguments: JSONObject, request_id: str) -> JSONObject
JobManager.get(self: JobManager, job_id: str, *, after_sequence: int = 0) -> JSONObject
JobManager.list(self: JobManager, *, state: str | None = None, offset: int = 0, limit: int = 100) -> JSONObject
JobManager.wait(self: JobManager, job_id: str, after_sequence: int, timeout_seconds: float) -> JSONObject
JobManager.cancel(self: JobManager, job_id: str) -> JSONObject
JobManager.shutdown(self: JobManager) -> None
JobManager.dispatch(self: JobManager) -> None
JobManager.execute_one(self: JobManager, job_id: str) -> None
JobManager.progress(self: JobManager, job_id: str, event: JSONObject) -> None
JobManager.cancelled(self: JobManager, job_id: str) -> bool
JobManager.transition(self: JobManager, job_id: str, changes: JSONObject) -> None
```

start acquires this server's singleton lock, reads/validates journal records, marks unfinished jobs interrupted and quarantines their workers; malformed entries fail startup with retained evidence. submit validates UUID, known operation/arguments/reservation limits before acceptance, enforces deduplication/capacity and persists queued request/state before enqueue; returns JobAccepted, raises ValueError/OSError without invented rollback. get/list return independent validated snapshots with bounded progress/pagination; unknown ID raises ValueError mapped not-found. wait waits on a condition up to 30 seconds and returns a new get snapshot; called through asyncio.to_thread so the protocol loop remains responsive. cancel performs only the phase-specific behavior described above; shutdown implements graceful semantics and returns None after releasing ownership. dispatch is the scheduler-thread target, returns None on shutdown; executes earliest eligible jobs without queue locks held. execute_one is a worker-thread target; invokes the bound WriteOperation, catches expected/ordinary exceptions, publishes result/error and terminal state, releases reservations in finally, preserving native status. progress validates/copies event and atomically increments durable event sequence; callback failure propagates so the domain runner retains its existing failure behavior. cancelled is the pure synchronized per-job Event predicate. transition validates allowed transitions/field values and atomically replaces state; terminal state cannot be replaced except unfinished-to-interrupted reconciliation during start. Functions accepting job_id validate canonical identity before reading/writing; OSError is retained/reported rather than ignored.

### src/mcp/common/protocol.py

Owns SDK integration only, using the installed low-level Server with explicit typed input/output schemas; no decorator-generated unconstrained scientific JSONObject schemas. `ProtocolAdapter(context: ServerContext, catalog: JSONObject, read: ReadOperation, write: WriteOperation)` stores validated catalog/context/callbacks, creates JobManager, and supplies all SDK callbacks.

```python
ProtocolAdapter.__init__(self: ProtocolAdapter, context: ServerContext, catalog: JSONObject, read: ReadOperation, write: WriteOperation) -> None
ProtocolAdapter.input_schema(self: ProtocolAdapter, name: str) -> Mapping[str, JSONValue] | None
async def list_tools(self: ProtocolAdapter, context: ServerRequestContext[object], params: PaginatedRequestParams | None) -> ListToolsResult
async def call_tool(self: ProtocolAdapter, context: ServerRequestContext[object], params: CallToolRequestParams) -> CallToolResult
async def list_resources(self: ProtocolAdapter, context: ServerRequestContext[object], params: PaginatedRequestParams | None) -> ListResourcesResult
async def list_resource_templates(self: ProtocolAdapter, context: ServerRequestContext[object], params: PaginatedRequestParams | None) -> ListResourceTemplatesResult
async def read_resource(self: ProtocolAdapter, context: ServerRequestContext[object], params: ReadResourceRequestParams) -> ReadResourceResult
async def serve(self: ProtocolAdapter) -> None
ProtocolAdapter.run(self: ProtocolAdapter) -> None
```

The six async functions shown are methods on ProtocolAdapter. Their return annotations describe awaited results, not nested Awaitable objects. The context type is the SDK's ServerRequestContext with unused lifespan result object; no domain context is taken from a caller-supplied SDK payload.

input_schema returns a fresh known schema or None, without effects. Listings return deterministic SDK objects and stable cursors tied to catalog generation. call_tool validates request size/name/schema, serves shared discovery/job tools or invokes read in asyncio.to_thread, or submits the registered mutation through JobManager. It returns structured content plus a concise textual reference, with response cap enforcement; SDK invocation cancellation stops only waiting/read operations, not an already accepted job. read_resource maps only declared URIs/templates to the same bounded operations, validates IDs and returns JSON/text resources. serve creates the low-level SDK Server with these exact callbacks, enters its stdio transport context and awaits its run operation through initialization/shutdown; it returns None and closes transport in finally. run owns singleton/jobs lifespan and calls asyncio.run(self.serve()), closes cleanly in finally, stderr logs only; startup config/SDK errors propagate. No HTTP/SSE/roots-driven arbitrary files/remote auth are exposed.

### Server-specific packages

Both `src/mcp/srim_mcp/catalog.py` and `src/mcp/project_mcp/catalog.py` expose exactly:

```python
build_catalog(context: ServerContext) -> JSONObject
```

Returns independently copied capability/schema/example/tool/resource declarations, after consistency and schema validation. Reads fixed repository documentation/source identifiers and installed identity where relevant; no imports/default switches/native runs/publication. Missing live installation produces capability unavailability, not deletion of offline schemas. Incompatible/invalid static catalogs raise ValueError.

Both `operations.py` files expose exactly:

```python
read_operation(name: str, arguments: JSONObject, context: ServerContext) -> JSONObject
write_operation(name: str, arguments: JSONObject, context: ServerContext, cancel: Cancel, progress: Progress) -> JSONObject
```

Name dispatch is a fixed allowlist from the catalog. read_operation performs exactly tabled read/validation effects and returns data, not an envelope. write_operation performs exactly the named accepted job, calls existing API or proposed project API, emits actual phase/progress events, returns the exact outcome/data/references/warnings structure above and never recursively submits jobs or calls the other server. Native dataclass results become copied JSON structures with errors and references separated. Invalid name/args raises ValueError; expected native failure stays metadata; I/O/validation/publication propagate to the shared mapper with partial references. No eval, arbitrary import path, dynamic callable resolution, shell execution, or invented lifecycle finalization is permitted.

Both `__main__.py` files expose `main(argv: Sequence[str] | None = None) -> int`: parse exactly --root absolute path, construct the fixed named context/catalog/adapter and run; return 0 after graceful stop or 1 for startup/fatal error with stderr explanation. Unknown arguments fail via argparse. New package `__init__.py` files contain module docstrings only. Exact entry points are the main proposal's module launch commands.

## Changes outside MCP source

New case/analysis/reuse Python modules are fully specified in [the project appendix](PROJECT_MCP_PROPOSAL.md); they contain no SDK/protocol imports. Existing SR/TRIM/backend/knowledge source signatures, native schemas, physics, active continuation logic, and parser behavior remain unchanged. Add requirements-mcp.txt and mcp.toml with the exact main-proposal contents. Update README.md to distinguish optional MCP dependencies from the existing standard-library Python APIs, document the two entry points, explicit environment/setup requirements and links; update doc/README.md with server/case/analysis guides. Documentation must correct only statements made stale by this implementation; no unrelated rewrite.

Add `doc/guides/mcp-servers.md` with startup, tool workflow, failure/cancel/restart limitations, host example and troubleshooting; `doc/reference/mcp-format.md` with the final accepted schemas/catalog; `doc/guides/project-management.md` with cases/analysis/reuse/improvement examples. New host example `doc/examples/mcp-servers.toml` contains only two named server registrations, absolute required Python command, module args, --root and repository cwd. No user configuration is edited automatically.

## Verification before reporting implementation complete

Add synthetic tests under `test/mcp/` with these exact responsibilities: `test_contracts.py` (strict schemas, JSON/config boundaries); `test_files.py` (chunk integrity, traversal/junction/collision/source-change/publication); `test_catalog.py` (coverage, schemas, examples, modes, units, source qualification); `test_jobs.py` (dispatch/reservations/FIFO/duplicates/races/cancel/restart/quarantine); `test_protocol.py` (real installed SDK stdio client against both module entry points, structured results/resources/size bounds); `test_srim_adapter.py` (fake existing APIs, failures and precise argument forwarding); `test_project_adapter.py` (all project tools, evidence/approval boundaries); plus domain tests in the project appendix. Each test callable follows AGENTS.md documentation/annotation rules; exact test functions follow the implementation behaviors above without changing production contracts. No new ad-hoc performance benchmark is needed.

Use temporary project trees under repository-root .cache/mcp-verification/ for SDK processes and fixtures. Spawn every project Python child with `D:\projects\agentic_srim\.venv\Scripts\python.exe` explicitly; do not replace it with sys.executable, bare python, an activation script, or a relative path. Dependency installation uses that executable's -m pip install. All shell invocations start directly with that executable when applicable to match the saved command approval.

Verify source-dependent request schemas with representative valid/invalid existing fixture requests, all 23 documented mode mappings, unknown/unavailable outputs, exact scientific strings/units and preserved provenance. Validate SDK callbacks against installed 2.3.0, protocol negotiation rather than a hardcoded latest spec, stdout purity, two-server simultaneous startup and same-server duplicate rejection. Use fake native programs/backends; SDK integration tests must never launch SRIM/TRIM/CAB GUIs accidentally.

Run affected tests then existing backend/SRIM/knowledge regressions with warnings as errors, plus docstring/annotations/no-code-comments checks, local documentation links, and git diff whitespace. Native engines/installed files, existing settings, existing records/skills/resources, and historical lifecycle results must remain unchanged. Real native smoke checks would need a separate agreed workload and authorization; synthetic adapter checks cannot be called independent native/physics validation.
