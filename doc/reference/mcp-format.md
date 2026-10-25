# MCP interfaces and record formats

[Server guide](../guides/mcp-servers.md) · [Project management](../guides/project-management.md)

Two independent local stdio servers expose the Python APIs. Scientific execution stays in srim_mcp; project organization stays in project_mcp. Their protocol code is grouped under src/mcp. Existing source APIs import no MCP modules. Each server has a separate workspace singleton lock, job journal and tool namespace.

## Run diagnostic report

Both servers publish `mcp.run-diagnostics.v1`. `simulation_get` and `run_get`
include it as `diagnostics`; scientific `result_read` exposes the same object
through section `diagnostics`, without requiring parsed output or input replay.
Existing metadata, lifecycle status and job/error envelopes remain unchanged.

The closed report contains `schema_version` (1), `simulation_id`, `run_status`,
`investigation_status`, `recorded_failure`, `observations`, `unresolved`,
`evidence`, `issues` and `omitted_observations`. Investigation status is
`not-terminal`, `no-recorded-failure`, `reported` or `incomplete`; it is not a
scientific validation status. `recorded_failure` preserves the existing metadata
message/exit-code object or null.

Each observation contains `category`, nullable `code`, `message`, `evidence` and
`cause_status`. Categories distinguish native, parser, monitoring,
input-validation, input-integrity, saved-value-mismatch, output-requirement,
preservation, timeout, cancellation and unknown findings. `recorded` means
producer-attributed; `unresolved` means no supported classification. Neither
establishes an independent root-cause investigation. Evidence entries pair a
simulation file reference with a JSON pointer; references pin exact hashes and
sizes without requiring successful input reconstruction.

The reader verifies inventoried execution, parsed-result, saved-value,
configuration-integrity and availability JSON where present. It records missing
execution evidence and rejects malformed, unsupported, unsafe, changed or
oversized files as issues. It does not scan arbitrary files, parse native output
again or expose exception stacks. Metadata changes during a read fail as stale.
Existing metadata readers may create their normal lock files.

Reports contain at most 64 observations and 64 distinct unresolved messages;
messages have a 512-character limit with an explicit truncation marker.
`omitted_observations` counts findings beyond the observation limit. Evidence
references pin the original reports; full file reads retain existing source
eligibility checks. They do not grant new artifact_read permissions. Reads use `max_result_bytes`
per diagnostic file and retain the normal final `max_response_bytes` check.
Raw native outputs remain available through existing verified file reads, subject
to their eligibility checks. The report does not decide scientific exclusion,
change execution state or launch recovery, retry or cleanup.

## Run closeout and reconciliation

Scientific tools `run_closeout` (read-only) and `run_reconcile` (mutating) accept
`simulation_ids`: 1–1000 unique canonical `srim_YYYYMMDD_HHMMSS_N` IDs. They do not
silently broaden scope to the workspace. `run_reconcile` additionally uses the
standard fresh UUID `request_id` and durable job admission. No force, cleanup,
process-kill or retry parameter exists. Dependency IDs must be included explicitly.

`srim.run-closeout.v1` has exactly `schema_version` (1), `complete`, `rows`,
`counts` and `issues`. Each sorted row contains `simulation_id`, nullable
`metadata_status`, `classification`, nullable `worker_id`/`runtime_path`, sorted
unique `job_ids`/`job_states`, boolean `stop_evidence`, `recovery_action` and
`issues`. Terminal metadata classifies as `terminal`; created metadata is
`unstarted`; running metadata with valid proof is `stale-stopped`; running
without proof and with an ended job snapshot is `stale-unresolved`; other
running records are `running-unresolved`; unreadable/invalid metadata is
`invalid`. Explicit tool reads do not supply job snapshots; startup/shutdown
callbacks correlate their validated journal snapshots. No job state establishes
native process death. `recovery_action` is `none`, `reconcile`,
`investigate-native-ownership` or `repair-record`.

`counts` contains exactly `terminal`, `unstarted`, `stale_stopped`,
`stale_unresolved`, `running_unresolved` and `invalid` nonnegative integers.
`complete` means every started scoped run is terminal and no row is invalid;
unstarted preparations are reported separately. Row issues are `{code,message}`;
report issues add `simulation_id`. Invalid/missing records are never omitted.
The report is finite JSON and remains subject to normal reply-size limits.

Recovery returns exactly `{schema_version:1, recovered_ids, unresolved_ids,
issues, closeout}`. IDs are sorted; issues are `{simulation_id,code,message}`.
The final closeout is re-read. Under the worker lease, recovery revalidates stop
proof and inputs, preserves available runtime evidence and independently calls
failure finalization. Preservation errors remain reported even if failure
metadata publishes. The failure message is `Orchestration interrupted after
native execution stopped; recovered during lifecycle reconciliation`, with the
proven stop exit code. Recovery never infers parser success or publishes
succeeded status. Terminal records remain unchanged. Unresolved/invalid IDs
produce outcome `partial` and failed adapter-job state; inspect the full report.

Immutable evidence lives at `memory/srim/<id>/lifecycle/`:

| File | Exact schema-1 fields in addition to schema_version |
| --- | --- |
| launch.json | simulation_id, engine (sr or trim), execution_id (UUID), worker_id, runtime_path, input_inventory_sha256, recorded_at |
| stopped.json | simulation_id, execution_id, launch_sha256, recorded_at, reason (exit, timeout or controller-stopped), exit_code (integer or null) |

`runtime_path` is `worker/<worker_id>/<engine>/<simulation_id>`.
`input_inventory_sha256` hashes sorted-key compact UTF-8 JSON of sealed
`metadata.inputs` with `ensure_ascii=False` and finite values. `launch_sha256`
hashes exact launch-file bytes. Timestamps use configured aware record time.
New executions publish launch intent before starting and stop proof only after
native shutdown is established. Identical semantic stopped evidence is
idempotent; conflicts are rejected. Recovery checks execution UUID, engine,
worker/runtime identity, launch hash, unchanged inputs and stable evidence bytes.
These documents are outside sealed input/output inventories and must be retained
with the permanent record. They establish lifecycle evidence, not physics validity.

Scientific startup marks unfinished jobs interrupted and retains worker
quarantine before recovery. After owned execution threads join, graceful
shutdown performs the same recovery sweep. Snapshots of terminal job states
supply scope. The immutable report filenames are
`memory/mcp/srim_mcp/recovery/startup_<uuid>.json` and `shutdown_<uuid>.json`.
Ordinary callback failure produces `{schema_version:1,recovery_error:{code,
message}}`, logs the issue and keeps diagnostic access available. Report-write
failure is logged and does not claim durable report publication; successful
metadata writes are not rolled back. Interruptions release the server lease.
Project-server callbacks default to none and do not recover native metadata.

A force-kill during native execution without durable stop proof, legacy runs
without lifecycle evidence, and records with corrupt evidence stay explicitly
unresolved. Released Python locks, old timestamps or interrupted jobs never
substitute for native-stop evidence. Reconciliation does not stop or restart
processes, release quarantine, clean workers or automatically retry scientific
work. An explicit closeout of all task IDs is still required even after startup
recovery; startup scope is the known job journal, not every historical record.

## Discovery and contracts

Call capabilities_list for compact cards, capability_get for exact side effects/compatibility/units/examples, schema_get for expanded Draft 2020-12 schemas, and example_get for editable requests. A capability detail can describe an advanced Python-only API and intentionally have no executable tool_names. Implementation, MCP exposure, engine compatibility, reviewed automation, output availability and evidence strength are distinct fields.

The scientific catalog exposes 40 tools, 63 capability cards and 28 examples. It includes SR list stopping and engine-grid ranges; all version-dependent TRIM modes; layers/isotopes/damage controls; events; resources/CAB; native and recovered results; conditional histories; prepared execution/batches; input search; and cleanup. Eight descriptive TRIM mode cards retain the seven-mode 2008 mapping and eight-mode 2013 mapping. Examples are input templates, not scientific recommendations.

environment_get reports installation.sr and installation.trim readiness and file identities independently. Default-resource readiness is separate from executable presence and from reviewed TRIM dialog-policy compatibility.

Use one MCP case per user-defined scientific objective, including multiple cases
within one query. Honor explicit case counts or separation before case-specific
work; shared workflow paths or evidence do not merge cases. Track cases
independently and share pinned evidence through explicit references. The agent
applies these boundaries; MCP does not infer them from query text or add required
target, recipe or validation fields. Cleanup is not a prerequisite.

project_mcp exposes 47 tools. It records multiple independent cases, terminal native evidence, analysis provenance, unapproved capability candidates and improvement evidence. Active reusable workflow/analysis/skill collections start empty. It has no native execution, generic code executor, trial-approval or candidate-promotion tool.

improvement_assess accepts closed project.assessment.v2 details plus the standard request_id and publishes one immutable version-2 assessment event through the normal mutation-job envelope. It grants no approval. Existing event kinds remain version 1. improvement_status retains its existing fields and adds assessments and pending_assessments; malformed records and chain conflicts remain issues. See the [protocol](../../.agents/skills/self-improvement/references/protocol.md#assessment-event-extension) for the exact record contract.

Improvement events, proposals and trial snapshots use `.local/workflow-improvement/`.
This is persistent Git-ignored worktree state, not a backup. Preserved historical
cache dependencies use protected `.local/preserved-dependencies/`; figure records use
`.local/scientific-figures/`. Migration inventories under `.local/migrations/`
map historical paths without rewriting evidence bytes or automatically redirecting
filesystem/MCP reads. New references use `.local/`; old saved `.cache/` references
are not aliases. Obtain current references by reading the current records.
Restart the project MCP server through its owning session after updating the
implementation; already-running processes may retain the old storage behavior.

The wire tool result uses structuredContent; SDK 2.3.0 Python clients access result.structured_content. Every structured tool response has:

```text
{schema_version, ok, data, error, warnings, references}
error = null | {code, message, fields, retryable, partial_references}
```

ok means the tool operation succeeded. It does not establish native execution or scientific success. Inspect native_status, job state, parse completeness, availability and warnings separately. Domain errors set isError on the wire; Python SDK clients expose is_error. Error codes distinguish invalid arguments, missing/unavailable/unsupported data, conflict, unsafe paths, integrity, busy/queue-full, excessive sizes, interruption, I/O and internal failure.

JSON quantities preserve native decimal strings and explicit units. Counts/indices exclude booleans. Duplicate JSON keys, nonfinite numbers, unsupported schema keys and implicit transformations are rejected. Native source/result versions remain unchanged. Important schema IDs include sr.request.v2, sr.engine-request.v2, sr.selections.v1, sr.result.v1, trim.request.v1, trim.engine-request.v1, trim.selections.v1, knowledge.filter.v1, mcp.job.v1, project.case.v1 and project.analysis.v1. Tool schemas are also readable as tool.<tool_name>.

## Mutations and results

All mutations require a caller-generated canonical UUID request_id. The server validates the tool schema, durably accepts the request and returns job_id/state/operation/request_sha256. Reusing the same UUID and same canonical operation/arguments returns that job. A changed payload with the same UUID is a conflict. The UUID is excluded from the canonical payload digest.

Use job_get with after_sequence and wait_seconds (0–30) to poll without a long-running calculation tool call. It returns at most 100 newer progress events. Job states are queued/running/cancelling/succeeded/failed/cancelled/interrupted. Known callback/adapter phases are recorded without invented percentages or ion counts. Completed jobs reference immutable result.json or error.json; read their verified base64 chunks with artifact_read. result.json has schema_version, operation, outcome, data, references and warnings. Partial batches preserve every member result and have failed adapter state with outcome partial.

Scientific preparation yields a PreparedReference:

```text
{engine, simulation_id, worker_id, runtime_manifest_sha256}
```

Runtime paths are derived from IDs and verified by existing native APIs. TRIM resolution yields a BundleReference `{id, manifest_sha256}` for a frozen exported input bundle; replay does not resolve mutable aliases again. SR resolution produces inspection evidence only; later SR preparation resolves its supplied selections again. Terminal native records are never rerun in place. Continuation allocates a new child and preserves inherited physics; total_ions is the absolute final total.

Adapter journals are under memory/mcp/<server>/jobs/job_<uuid> with request.json, state.json, numbered progress files and sealed result/error JSON. Objects and exported artifacts are under that server's objects/ and artifacts/. Existing native records remain under memory/srim, immutable resources under memory/resources, and workers under worker/worker_N. No host registration is performed by starting either server.

Initial acceptance writes and synchronizes request.json and queued state.json in
`jobs/.admission-job_<uuid>/`, then renames that complete directory to
`jobs/job_<uuid>/` before registering it with the scheduler. Failed staging is
retained and reported; it is never scheduled or replayed. An explicit retry of
an unpublished staged request UUID may create a fresh job. A complete published
admission interrupted before acknowledgement retains its accepted identity and
becomes interrupted on restart under the normal durable-job rules.

`jobs_list` includes `issues_total` and `issues` for recognized incomplete
admissions alongside the existing `total`, `offset`, `limit` and `matches`:

```text
issues = [{id, code: "incomplete-admission", path, message, request_id, worker_ids}]
```

The id is a canonical job ID; path is its repository-relative directory location.
Unpublished stages and empty/temporary-only legacy allocations have null
request_id and empty worker_ids. A valid legacy request.json without state.json
reports its UUID and explicit workers. Stages are checked as safe directories
without parsing unfinished contents. Visible legacy allocations may otherwise
contain only regular `.mcp-<lowercase-alphanumeric-or-underscore>` temporary
files. Other unexpected or malformed committed evidence still prevents startup.

Issues are copied snapshots collected at startup and on failed publication,
deduplicated and sorted by path. They are independently paged with the same
offset/limit, regardless of the accepted-job state filter; total/matches count
accepted jobs only. These paths support inspection; partial admissions are not
accepted jobs or immutable artifact references. No background scan or repair
occurs.

## Limits and ownership

mcp.toml is separate from the strict existing settings.toml. Defaults are four scientific parallel slots, one management slot, 64 pending jobs per server, 16-MiB requests, 1-MiB structured replies, 64-MiB parsed JSON files and 256-KiB verified file chunks. These are adapter limits, not physical/native caps. Large saved results remain readable through chunks rather than silent truncation. Analysis/candidate attachment caps are 64 MiB per file and 256 MiB per operation.

Batch max_workers reserves that many global scientific slots. Shared workers are FIFO; independent workers may overlap. Existing worker leases remain final authority against other Python clients. A null worker selection allocates a new worker at dispatch, without a hidden worker reuse/scaling heuristic. SR batches retain their existing mandatory verified cleanup; TRIM cleanup is explicit and defaults to retention. Cleanup never deletes permanent evidence.

allowed_input_roots adds explicit read-only external directories; an empty list permits the repository root only. Fixed native installation reads use settings.toml through existing APIs. Caller paths do not inherit arbitrary installation-directory browsing rights. Traversal, device/stream names, symlinks/junctions and case collisions are rejected. File reads verify hashes, sizes and stable identities; Windows handle/path timestamps are compared within each stat API rather than assuming their ctime representations match.

## Cancellation, restart and qualifications

Queued jobs can be cancelled before dispatch. Active individual TRIM execution uses the existing cooperative cancellation callback and records completion only after owned native shutdown/preservation. Active SR is timeout-only; preparation/CAB, publication and active batches have queued-only cancellation. A racing successful completion remains success. TRIM timeout can be null; an unlimited active TRIM batch can delay graceful shutdown indefinitely. Disconnect/shutdown cannot invent cancellation support the backend lacks.

Graceful shutdown stops admissions, cancels queued jobs, requests supported cancellation and waits for active finalization. Forced termination can leave SR children or partial publication. On restart, unfinished jobs become interrupted, their workers are quarantined, and requests are never automatically re-executed. There is no MCP auto-release tool. Scientific startup and graceful shutdown reconcile only durable proven-stopped records linked to ended jobs, preserving a separate recovery report; explicit run_reconcile uses the same rules. Missing stop proof remains unresolved. Recovered metadata does not release quarantine; reconcile native activity/evidence before any separately authorized worker recovery. Released Python locks alone do not prove native processes stopped. Exactly-once native execution after a crash is not guaranteed.

Recognized incomplete admissions are retained as listing issues while unrelated
valid jobs remain accessible. A visible request-only legacy admission reserves
its UUID and quarantines its explicit workers; retrying that UUID conflicts
until explicitly reconciled. New sequence numbers exceed those in both complete
journals and valid visible request-only admissions. Hidden unpublished staging
reserves neither workers nor sequence numbers. Complete accepted journals remain
authoritative when an older incomplete attempt shares a UUID or sequence with a
later retry; the issue names that accepted identity. Workers named by the old
visible attempt remain conservatively quarantined even when its later retry
completed successfully. No missing state is synthesized and no native activity
is inferred from a missing journal. Directory rename provides atomic visibility,
without guaranteeing durability through every hardware/filesystem failure.

TRIM continuation keeps the approved checkpoint-first lifecycle, saved-value checks, configuration qualifications and recovery provenance. Stable checkpoints do not prove atomic binary saving or independent physical accuracy. Fresh native-text and recovered checkpoint results remain distinct. Quick-mode uncalculated quantities remain unavailable. F-button/chooser exports and external collection imports are outside this interface.

## Resources

The srim:// and project:// schemes expose capability/schema/example indexes and identity templates, job snapshots and artifact manifests. project:// additionally exposes case and analysis revisions, collection entries/candidates and improvement state. Tools provide equivalent discovery when a host does not expose resources conveniently. URI parameters are identities, never arbitrary filesystem paths. Initial subscriptions, sampling, elicitation, prompts and remote HTTP transports are not advertised.


## Unified retrieval tools

Project MCP exposes knowledge_search, knowledge_get, knowledge_links,
knowledge_status and the mutating knowledge_update. Discovery publishes
knowledge.query.v1 and knowledge.output-filter.v1. The complete Python/JSON
contract is in [unified retrieval](input-search-format.md#unified-retrieval-apis).

knowledge_update accepts operation initialize/update/reconcile. update takes
explicit {kind,reference} identities; reconcile defaults to registered scope,
previews when expected_plan_sha256 is null, and applies only an unchanged digest.
Project scope is an explicit published-record inventory. Existing request UUID,
job, cancellation and artifact-result behavior applies. Cancellation is checked
before atomic index publication; already published records remain unaffected.
Read responses honor max_result_bytes and fail too-large rather than returning
truncated JSON. Reduce page size or read a specific table to inspect large results.
Incoming links/coverage describe indexed records, not a complete project audit.

Scientific inputs_search/index_update remain unchanged public interfaces.


## External database inspection tools

Family `databases` provides three read-only tools through project_mcp:

| Tool | Required arguments | Data schema |
| --- | --- | --- |
| database_sources_list | none | database.source-list.v1 |
| database_inspect | source_id | database.inspection.v1 |
| database_sample_read | reference | database.sample-read.v1 |

`database.sample-reference.v1` describes the closed input reference. These are
separate from native source/file references. Schema discovery exposes all four
envelopes and `tool.<name>`. Tool results retain the ordinary MCP response wrapper.
`max_result_bytes` caps serialized result data; `file_chunk_bytes` additionally
caps each prefix (oversized reads/reports fail `too-large`).

Each call reloads registered paths. IDs cannot authorize arbitrary paths or
revoked registrations. Ancestors, descendants and archive members are checked;
reparse points and unsafe/ambiguous members are rejected. No extraction, SQL
connection, dataset code execution or index mutation occurs. Unknown schema
recognition recommends an agent skill, which the server does not execute.
See [external source inspection](input-search-format.md#external-source-inspection)
for exact shapes, intrinsic limits and sample-only verification qualifications.

## External database import and retrieval tools

The existing project server adds six tools alongside bounded source discovery:

| Tool | Arguments beyond the normal envelope | Operation |
| --- | --- | --- |
| database_preview | source_id, timeout_seconds=600, resume_operation_id=null | Managed staging/verification job; no search publication |
| database_import | source_id, expected_plan_sha256, timeout_seconds=600 | Managed atomic import/refresh job |
| database_reindex | source_id, expected_generation, timeout_seconds=600 | Managed exact search-index build from imported records |
| database_search | source_id, query, offset=0, limit=20, expected_generation=null, timeout_seconds=60 | Read compact imported input/summary hits; original evidence not_checked |
| database_get | reference, verify_evidence=true, timeout_seconds=240 | Read exact external record and evidence verification |
| database_status | source_id | Read catalog/staged-plan status without source scanning |

Preview, import and reindex require fresh UUID request_id values with normal idempotent
job admission, job_get progress and verified artifact_read result chunks.
Ready previews yield succeeded jobs without publishing searchable records.
Committed imports yield succeeded jobs; failed/cancelled reports retain those
job outcomes. Cancellation callbacks are checked during verification; an
InterruptedError before commit becomes a cancelled job. No scientific worker
or native simulation is allocated.

Read results obey max_result_bytes and the normal response cap. Search uses
schema-version-2 compact hits and a 131,072-byte data budget; follow next_offset
when byte limits or exclusions shorten a page. Local page records are verified,
while original evidence is explicitly not_checked. database_get reads one exact
full record and verifies original archives/members by default. Search/get use
cooperative deadlines in 1..240 seconds. Preview/import/reindex allow 1..3600
seconds; all three default to600 seconds. No deadline changes the client cutoff.
Tool schemas are closed and registered as tool.<name>. External schema IDs are
database.external-reference.v1, database.external-query.v1,
database.external-preview.v1, database.external-report.v1 and
database.external-record.v1. Native reference and knowledge query schemas are
not widened to accept external records.

Installing adapter/tool code requires restarting project_mcp through its owning
session and refreshing the client's connection/tool inventory. Adding supported
records or editing registered paths needs no code restart: paths reload on calls,
and data changes require an explicit preview, approved import and reindex. A
reindex changes only the derived search projection; it reuses imported records
and requires no source preview. No watcher or
automatic startup/search refresh is installed. See the
[external contract](input-search-format.md#external-import-and-search) and
[usage workflow](../guides/input-search.md#preview-import-and-search-snumat-v1).

External preview and import accept optional timeout_seconds (integer 1..3600,
default 600). The chosen deadline spans all source passes, normalization and
publication; the source reader honors that shared deadline. Expiration before
commit prevents publication. Native tool contracts are unchanged.


External `database_preview` accepts optional `resume_operation_id` (canonical
lowercase UUID or null, default null), also accepted by
`preview_external_import(..., timeout_seconds=600, resume_operation_id=None)`.
Resume selects a terminal failed preview with complete normalization in this
project. Fresh full fingerprints, exact source inventory/member hashes, cached
record schemas/digests and unchanged catalog generation are required. Partial,
corrupt, unsafe or stale caches fail explicitly; no fresh-normalization fallback
occurs. Immutable derived records are hardlinked into a new staging operation;
unsupported hardlinks fail explicitly. Original failed evidence and rejection
diagnostics remain preserved. The default path still creates a fresh preview.
Resume does not apply new mappings to prior rejections or publish searchable
records; the new ready plan still requires explicit import approval.
