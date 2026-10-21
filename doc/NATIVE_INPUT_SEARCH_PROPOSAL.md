# Native SR/TRIM input search

Status: implemented and verified, 2026-10-21.
Implementation approved by the user in this chat; work started 2026-10-21. The user additionally approved using sealed continuation controls/checkpoint inputs for resumed TRIM requested counts and appended event inputs. This supersedes the parent effective request for those continuation fields only; immutable physics still derives from verified parent inputs.
Date: 2026-10-21.

This is the first-version implementation contract. It supersedes the implementation scope of [the earlier import proposal](IMPORT_SEARCH_PROPOSAL.md). External-data designs there are deferred.

## Scope

Start with an empty local catalog and automatically index newly finalized project-generated SR/TRIM simulations. Python API only; structured input search/filter and source references for future post-processing. No external imports, NERSC work, CLI, internet/model/vector service, output-value indexing, scientific analysis or automatic historical backfill. Existing simulations and memory/NERSC_TRIM_20k remain untouched.

Approved behaviors: incremental single-run updates, one update after a prepared batch; both terminal native statuses included, optional status filter; AND between fields, OR within in-lists; exact quantities with explicit units; inclusive bounds; energy single/list/range matching; target label/element/composition distinctions and explicit layer selection; unknown fields remain unknown; default 100 results, maximum 1,000, stable ID ordering and optional generation pinning. Index failures cannot change simulation status.

Precise implementation contracts and defaults below remain proposed until approval of this document. Amend before adding unlisted functions/classes or changing the design. Announce implementation start after approval.

## Native evidence and architecture

Keep existing Config, ProjectPaths, IDs, lifecycle schemas, runner signatures, task/result classes, worker ownership and native storage unchanged. Derive new paths from ProjectPaths.root. Use existing native metadata readers, safe file/hash/inventory helpers, input validators/renderers, and locks. No installation/default lookup or engine launch.

Native simulations already save effective-request.json and exact SR.IN/TRIM.IN/optional TRIM.DAT bytes. Verify saved input inventories and require existing renderers to reproduce those preserved input bytes. Project searchable fields from the verified effective request, retaining exact evidence and numerical spellings. No generic reverse parser is necessary for this scope. Legacy explicit SR requests are eligible only when existing validation/rendering and preserved input bytes agree. Unsupported/damaged evidence is excluded with an issue rather than inferred from outputs.

Unknown values represent unavailable/inapplicable fields, not permission to trust a corrupted input. Ordinary failed runs remain searchable if their inputs verify. Exclude unfinished records and sr-capture. TRIM requested settings remain distinct from saved-state substitutions, output summaries and achieved counts.

## Storage and schemas

```text
memory/knowledge/
  .records.lock
  index.sqlite
  updates/update_<uuid>.json
  .staging/rebuild_<uuid>.sqlite
```

UUIDs identify catalog generations and diagnostic operations only. Native simulation IDs remain srim_YYYYMMDD_HHMMSS_N. No import IDs/collections or settings fields. Initialization creates an empty catalog, never scans historical simulations, and never clears an existing catalog. First automatic update may initialize it. Search with no catalog returns an empty result and generation=null without creating storage.

JSON is UTF-8, schema_version=1, sorted keys, two-space indentation, final newline, duplicate keys rejected, allow_nan=False. Unknown keys rejected. Quantities are finite decimal strings, counts/indices actual integers excluding bool. Use exact Decimal/Fraction arithmetic, not binary floats. Preserve original input tokens. Hashes lowercase SHA-256, sizes nonnegative integers. File paths safe owner-relative forward-slash paths; project roots absolute and link/junction-free. Diagnostic times aware UTC ISO strings; native timestamps unchanged.

Exact public shapes, every key required and nullable fields represented by null:

- SourceReference = {kind,id,metadata_sha256,input_inventory_sha256}. kind=simulation only. id canonical native ID. Hash exact metadata.json bytes and canonical sorted metadata.inputs inventory JSON respectively. Canonical inventory JSON uses sorted keys, separators (',',':'), UTF-8, no final newline.
- FileReference = {owner,path,sha256,size_bytes,role}. owner=SourceReference. path must occur in owner input/output inventory. role=input/output/other. metadata.json is accessed through source-record API, not self-inventoried.
- Issue = {simulation_id,code,field,message}. field nullable; codes unsupported/malformed/missing/integrity/conflicting/stale/ineligible/indexing_error.
- Observation = {field,value,unit,evidence}. value follows existing JSONValue, numerical quantities strings; unit canonical or null. evidence list of {file,json_pointer}; pointer into saved JSON or null for its exact rendered native counterpart.
- SearchRecord = {schema_version,reference,origin,execution_status,inputs,observations,files,issues}. origin=native. execution_status=succeeded/failed. files sorted FileReferences from saved inventories without opening outputs.
- SearchResult = {schema_version,generation,total,offset,limit,matches,excluded}. matches SearchRecords sorted lexicographically by simulation ID. excluded Issues for stale/rejected entries. total valid matches before paging; generation UUID or null if no catalog.
- UpdateReport = {schema_version,operation_id,state,generation,attempted,indexed,skipped,rejected,issues,completed_at}. operation_id=update_<uuid>, state=succeeded/partial/failed; generation nullable on catalog failure; ID lists sorted/deduplicated; issues Issues. Indexed IDs reported only after commit.

inputs = {engine,engine_family,mode,mode_code,projectile,energy,target_label,layers,incidence_angle_degrees,total_ions,random_seed,events}:

- engine sr/trim; family observed 2008/2013 or null. SR mode list/range and null mode_code. TRIM uses existing descriptive MODES key and family-specific code.
- projectile null or {atomic_number,mass_amu}. Ordinary serialized projectile; event runs expose one only when all event species agree and input establishes mass. No mass inferred from a symbol or nominal control entry.
- energy null or {kind,values_keV,minimum_keV,maximum_keV}. kind single/list/range; ordered single/list values retain duplicates, bounds null; continuous range values empty and bounds explicit. Event energies come from saved event input, not nominal TRIM.IN energy.
- target_label saved TRIM calculation/target name; null for SR without a target-wide label. No compound label invented from component names.
- layers ordered {index,label,phase,thickness_angstrom,density_g_cm3,components}; index one-based, components ordered {atomic_number,mass_amu,atomic_proportion}. Preserve repeated isotope identities and include positive proportions only. SR has one homogeneous target, null label/thickness; not a finite TRIM layer.
- incidence_angle_degrees ordinary TRIM serialized convention, no surface/normal conversion; null for SR/event modes without a scalar angle.
- total_ions requested TRIM count, not completed count/event-list length; random_seed initial control, not saved evolving RNG. Both null for SR; seed zero remains zero.
- events null for ordinary modes or ordered {id,atomic_number,energy_keV} from verified saved event input. Other event fields remain in evidence without becoming filters.

## Filters

Filter = {schema_version:1,clauses:[...]}; omitted filters means no clauses. Clause = {field,operator,value,unit,layer}. Validate every key/type/operator/unit before reads. Different clauses AND; in/contains_any provide OR. Valid contradictory clauses return no matches.

| Field | Operators/value |
| --- | --- |
| engine, mode, execution_status, target_label | eq(string), in(nonempty string list), unknown(null) |
| projectile.atomic_number | eq(integer), in(integer list), unknown |
| projectile.mass_amu | eq(decimal), between([lower-or-null,upper-or-null]), unknown; amu |
| energy | eq/between/unknown; eV,keV,MeV,GeV |
| target.elements | contains_all(integer list), contains_any(integer list), unknown |
| target.composition | eq(component list), unknown; layer required |
| layer_count | eq(integer), between(integer bounds), unknown; TRIM only |
| layer.thickness | eq/between/unknown; A,nm,um,mm,cm,m; layer required |
| layer.density | eq/between/unknown; g/cm3; layer required |
| incidence_angle | eq/between/unknown; deg |
| total_ions, random_seed | eq(integer), in(integer list), between(integer bounds), unknown |

Decimals are query strings; bool never integer. Dimensional numeric predicates require unit; other fields unit=null. unknown always value/unit=null. Unknown fields never match ordinary predicates. Bounds inclusive, ordered, at least one non-null. Equality exact after exact unit conversion, no tolerance. Atomic numbers positive integers; do not invent a new native engine cap. Caller translates symbols to Z. Labels Unicode casefold exact; no formula/natural-language inference.

For layer fields caller chooses positive layer index, any or all; all requires nonempty known matching layers. Several any clauses can match different layers; a common explicit index enforces co-location. Missing selected layer matches only unknown. SR supports density/composition selector 1/any/all; thickness/layer_count unavailable. target.elements uses the target union.

Composition query entries {atomic_number,mass_amu,atomic_proportion}: all masses null means elemental proportions aggregated by Z; all masses specified means isotope/mass-specific. Mixed forms and duplicate query identities invalid. Exact ratios ignore common scale/order (1:2 equals 2:4). Missing source masses cannot prove mass-specific equality; never average layers.

Single/list energy matches any specified value. Continuous SR range matches eq by membership and between by interval overlap; return energy.kind to avoid implying an output sample. Event projectile-Z and energy clauses must share a single event witness, including when multiple clauses are supplied. Mass-specific event filtering requires actual mass evidence. Do not substitute nominal TRIM.IN energy/angle for event settings.

offset >=0, limit 1..1000 default 100, no bool. Stable ID ordering; no relevance ranking. expected_generation mismatch raises ValueError. A generation pins catalog membership/projections, not immunity to externally changed files; stale current evidence is excluded even when generation matches.

## Index lifecycle and concurrency

Standard-library sqlite3, user_version=1:

- records(id TEXT PRIMARY KEY,metadata_sha256 TEXT,input_inventory_sha256 TEXT,engine TEXT,mode TEXT,execution_status TEXT,projection_json TEXT).
- issues(id TEXT PRIMARY KEY,issue_json TEXT), storing an Issue list for a rejected registered ID.
- meta(key TEXT PRIMARY KEY,value TEXT), keys schema_version/generation/projector_version; version native-input-search-v1.

Use parameterized categorical narrowing, bounded projection batches and exact Python numeric predicates, never SQLite REAL scientific comparisons. DELETE journal, synchronous FULL. Use existing Windows byte-lock mechanics on knowledge with 30-second timeout. Database generation changes atomically with changed rows in one transaction; unchanged verified records retain it. No separate manifest/commit marker.

Update touches only supplied IDs. Project and validate outside knowledge lock; recheck metadata/input identities before transaction publication. Invalid supplied records lose stale searchable rows and receive issues. Initialize if absent, without scanning memory/srim. Search holds knowledge lock for a consistent result page; verify candidate metadata and current input inventories before accepting matches. No output content reads, unrelated source scans, implicit updates or model calls.

Explicit rebuild takes an explicit nonempty ID list, stages a database and replaces catalog membership under lock. Check prior generation to reject concurrent updates instead of losing them. No directory discovery/backfill. Rebuild supports corrupt-index recovery from caller-specified records. Empty-ID destructive reset is not supported. Incompatible schema rejected, not silently migrated.

Index reports are separate operational artifacts. Invalid API arguments raise ValueError; operational/individual-record failures return failed/partial UpdateReports. Report/log writes best-effort and cannot change simulation results. Commit-success plus report-write failure remains a committed catalog update with diagnostic issue when possible. BaseException propagates after database cleanup; immutable terminal evidence stays terminal.

finish_simulation retains its signature/schema. Publish/reread terminal metadata inside parent lock, release that lock, notify indexing through a local import, return the same metadata. Ignore captures. Ordinary indexing/reporting/logging failures must not reach native runner failure classification or rewrite terminal state. Standalone updates synchronously index only that record. Indexing acquires no worker lease; the runner may still hold its own lease.

Prepared batches use a ContextVar default None with a per-project collector dictionary containing threading.Lock, ID set and ownership token. Wrap dispatch/executor shutdown in collector context; propagate separate copy_context() per task. After active calls finish, owner flushes once, including finalized runs on exceptional batch exit. Restore context in finally. Same-project nesting shares an owner, different projects separate; no process-global suppression set/background service. Preserve task order, cancellation, timeout, cleanup and result classes. Index/report failure cannot mask an original exception. Lock ordering: no record-parent lock retained when acquiring knowledge lock. Source projection releases its short parent-read lock before catalog publication. Search may briefly acquire parent lock while holding knowledge lock.

## Files and typed contracts

All paths relative to D:/projects/agentic_srim. Standard library only; follow AGENTS.md docstrings/full annotations/no implementation comments. No new class. No unlisted helpers preauthorized; amend if needed.

| File | Purpose/responsibilities/dependencies |
| --- | --- |
| src/knowledge/schema.py | Pure exact schemas, filter validation, units/composition/event predicates. Existing JSON aliases, Decimal/Fraction. |
| src/knowledge/source.py | Native evidence/projected input schema and stable file access. Existing metadata validators/renderers/safe readers plus schema; no installations or output parsing. |
| src/knowledge/index.py | Initialization/incremental/rebuild, report writing, notification and context-local batch collectors. source/schema/sqlite3/contextvars/threading/logging/uuid. |
| src/knowledge/search.py | Read-only filters, paging, generation and stale-source exclusion. source/schema/sqlite3, no index mutation. |
| src/srim/metadata.py | Only finish_simulation post-lock notification/control-flow/docstring; signature and metadata unchanged. |
| src/srim/queue.py; src/srim/trim_queue.py | Collector context and copied task contexts in existing batch bodies/docstrings, scheduling/result signatures unchanged. |
| test/knowledge/test_schema.py | Units/exact math, fields, layers, event correlation and unknown predicates. |
| test/knowledge/test_source.py | Saved-byte/effective-input agreement, ordinary/event SR/TRIM, legacy supported requests, tampering and lazy file access. |
| test/knowledge/test_index.py | Empty start, no backfill, supplied-only updates, reports, transactions, rebuild conflicts and collectors. |
| test/knowledge/test_search.py | Input/status filters, pages/generation, stale/missing sources, read-only no-catalog behavior. |
| test/srim/test_metadata.py; test/srim/test_queue.py; test/srim/test_trim_queue.py | Extend native integration tests: released locks, failure containment, context propagation/one flush, exceptions and unchanged results/cleanup. |
| doc/guides/input-search.md; doc/reference/input-search-format.md | Usage and implemented API/schema/concurrency contracts. |
| README.md; doc/README.md; doc/reference/api.md | Add verified native-search links/entry points, no external import claims. |
| doc/NATIVE_INPUT_SEARCH_PROPOSAL.md; doc/IMPORT_SEARCH_PROPOSAL.md | Current approval contract and explicit deferred-history pointer respectively. |

No changes to Config/ProjectPaths/settings, input/output renderers/parsers, runner modules or native layout. Tests use synthetic workspaces under .cache/input-search/, no mutable live data. No extra fixture files needed; reuse existing fixtures/renderers plus independently specified expected values.

JSONObject/JSONValue existing aliases; Path pathlib.Path; Sequence/Iterator collections.abc. APIs return independent JSON/bytes. All functions have first-statement docstrings documenting each input, output, side effect and errors.

```python
def validate_search_document(document: JSONObject, kind: str) -> None: ...
def convert_quantity(value: str, source_unit: str, target_unit: str) -> str: ...
def matches_filters(record: JSONObject, filters: JSONObject) -> bool: ...
```

schema.py: validate exact kind reference/file/issue/observation/record/filters/result/update-report and nested invariants; return None, no effects, ValueError with field path on invalid/unknown kind. convert_quantity validates finite decimal and registered compatible units, returns exact fixed-point string without rounding; ValueError on incompatibility, no effects. matches_filters validates both envelopes and returns bool using all filter rules, no I/O/mutation; invalid inputs ValueError.

```python
def project_simulation_inputs(paths: ProjectPaths, simulation_id: str) -> JSONObject: ...
def read_source_record(paths: ProjectPaths, reference: JSONObject) -> JSONObject: ...
def read_source_file(paths: ProjectPaths, reference: JSONObject,
    file: JSONObject, *, max_bytes: int = 67108864) -> bytes: ...
```

source.py: project_simulation_inputs takes agreed absolute paths/canonical ID, verifies terminal metadata/inventories, saved effective JSON and exact render/input agreement, rechecks source mutation, returns SearchRecord. No output-content/install/default reads or data writes except existing lock files; invalid/ineligible/unsupported/integrity errors ValueError, unsafe/missing/I/O OSError. read_source_record takes sealed reference and returns projection only if both pinned hashes match; stale/schema ValueError, I/O OSError, no data writes. read_source_file validates owner, exact listed reference/path containment and positive max_bytes (64 MiB default), reads only selected bytes, checks hash/size, returns bytes. Oversize/schema/integrity ValueError, unsafe/missing/I/O OSError; no parsing/writes.

```python
def initialize_index(paths: ProjectPaths) -> JSONObject: ...
def update_index(paths: ProjectPaths, simulation_ids: Sequence[str]) -> JSONObject: ...
def rebuild_index(paths: ProjectPaths, simulation_ids: Sequence[str]) -> JSONObject: ...
def notify_simulation_finished(paths: ProjectPaths, metadata: JSONObject) -> None: ...
@contextmanager
def collect_index_updates(paths: ProjectPaths) -> Iterator[None]: ...
def _write_update_report(paths: ProjectPaths, report: JSONObject) -> None: ...
def _log_index_failure(report: JSONObject) -> None: ...
```

index.py contracts:

- initialize_index: validated project; create safe empty schema under lock if absent, return {schema_version:1,generation,created}, created bool. Existing valid catalog unchanged, no historical scan. Invalid/corrupt schema ValueError, I/O/lock OSError.
- update_index: validate/deduplicate canonical IDs in a non-string sequence before effects. Empty list succeeds without initialization. Index only supplied records, transaction and separate UpdateReport; API validation ValueError, operational/record errors reported partial/failed, BaseException propagated after cleanup. No source changes; idempotent retry.
- rebuild_index: same input rules but list must be nonempty. Explicit replacement membership, staged database, generation conflict protection, UpdateReport. API validation ValueError; operational errors failed report; interruption retains staging with notes. No scan or simulation-data changes.
- notify_simulation_finished: current published terminal metadata/project; ignore captures/noneligible engines, collect ID or call update_index for this ID; return None, metadata unchanged. Contain ordinary index/report/log exceptions; do not silently swallow KeyboardInterrupt/SystemExit. No worker/parent lock while collecting.
- collect_index_updates: yield None with context-local shared collector as described; owner flush after shutdown, restore tokens finally, retain original exception on a simultaneous flush error and attach notes. Ordinary indexing errors separately logged/reported. No simulation/process mutations or global collector.
- _write_update_report: validate UpdateReport; create safe updates directory and exclusively atomically publish update_<uuid>.json; return None. Invalid data ValueError, I/O OSError; callers contain diagnostic errors. No simulation evidence writes.
- _log_index_failure: valid report; emit operational state/issues through module logger; return None, contain ordinary custom-handler errors. No logger/network configuration or direct source writes.

```python
def search_inputs(paths: ProjectPaths, filters: JSONObject | None = None, *,
    offset: int = 0, limit: int = 100,
    expected_generation: str | None = None) -> JSONObject: ...
```

search.py: validate args before reads; absent catalog returns empty SearchResult without writes, pinned non-null generation mismatch ValueError. Existing catalog read under lock, exact filters, evidence checks and valid total/page. Invalid args/generation/schema ValueError; I/O/lock OSError; individual stale entries in excluded. No implicit refresh/model/engine/output reads or persisted query.

Modified existing signatures remain:

```python
def finish_simulation(paths: ProjectPaths, config: Config, simulation_id: str,
    failure: JSONObject | None = None) -> JSONObject: ...
def run_sr_batch(paths: ProjectPaths, config: Config,
    tasks: Sequence[PreparedSRTask], *, max_workers: int,
    timeout_seconds: float = 60.0) -> SRBatchResult: ...
def run_trim_batch(paths: ProjectPaths, config: Config,
    tasks: Sequence[PreparedTRIMTask], *, max_workers: int,
    timeout_seconds: float = 60.0, cleanup: bool = False) -> TRIMBatchResult: ...
```

Existing argument validation/state/error semantics unchanged. finish_simulation adds best-effort post-lock index notification; returns unchanged reread terminal metadata. Batch functions add collector/copy_context boundaries, flushing only after active work finishes; retain current order/timeouts/cancellation/cleanup/results and original errors. Update their docstrings. No task/helper signatures change.

## Verification and delivery

Before coding: obtain approval of this complete native proposal, check working tree and announce implementation start. Propose any additional changes before coding them.

Test independent expected input fields using existing renderers/fixtures, SR lists/ranges, TRIM ordinary/events/layers/repeated identities, preserved precision, zero seed, legacy SR, invalid schemas and byte/inventory disagreement. Verify filter units/composition/layers/shared-event witnesses, unknowns, failed native inclusion, count/pagination/generation and stale references. Outputs never become filters.

Verify empty startup despite historical records, no backfill/unrelated scans, only supplied-ID updates, no-op generations, rejected-record removal, transaction rollback, corrupt schema recovery, explicit rebuild concurrent-generation rejection and diagnostic-write failure. Native tests prove metadata unchanged and parent lock released before notification. Batch tests prove per-task contexts, single flush, nesting/projects, concurrent standalone tasks, failed/interrupted batches and unchanged cleanup/results. Validate requested file bytes against exact references without adding analysis.

Synthetic benchmarks separately measure one update, batch update, search and rebuild; report actual record counts/wall time and limitations. No NERSC inspection/migration or native physics reruns required. Run all project Python through the exact interpreter:

```powershell
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' -W error -m unittest discover -s test/knowledge -p 'test_*.py'
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' -W error -m unittest discover -s test/backend -p 'test_*.py'
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' -W error -m unittest discover -s test/srim -p 'test_*.py'
```

Verify annotations/docstrings/no implementation comments, document links/fences/schemas/signatures, and native API compatibility. Update guides only after passing checks. Report actual work/tests and explicitly deferred external-data scope. Design approval does not itself establish implementation completion.

## Implementation verification

Implemented the four src/knowledge modules, post-lock native finalization notification, copied-context SR/TRIM batch collection, separate update diagnostics and user documentation. Only newly finalized native records enter automatically; historical records and NERSC files were not migrated, indexed or changed. No live physics engine was launched for acceptance.

Verification: 19 knowledge tests, 41 backend tests and 179 SR/TRIM tests passed with warnings treated as errors (239 total). Tests cover ordinary/event continuation projection from sealed checkpoint/control inputs, exact units/ratios and event witnesses, layer selectors, empty startup/no backfill, incremental/no-op updates, transaction rollback, rebuild concurrent-change rejection, stale evidence, diagnostic failure containment and one flush per prepared batch. Earlier full-suite executor-argument assertion was updated for copied-context submission and the full regression rerun passed.

Local acceptance logs: .cache/input-search/knowledge-tests.log and srim-regression.log. Static/benchmark evidence is stored in the same cache directory and is not a durable backup. The backend command completed successfully with 41 tests. New/modified implementation retains required module/function/class docstrings, complete argument/return annotations and no implementation comments.

One cached-filesystem synthetic benchmark (32 small SR records, no native runs): one new-record update 0.091 s; unchanged update 0.042 s; 31-record batch update 1.993 s; search of 32 records 0.904 s; explicit 32-record rebuild 0.945 s. These are observations from this workstation, not large sealed-input or production performance guarantees. Current source integrity verification adds I/O proportional to selected candidate inputs.
