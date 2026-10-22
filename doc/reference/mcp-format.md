# MCP interfaces and record formats

[Server guide](../guides/mcp-servers.md) · [Project management](../guides/project-management.md)

Two independent local stdio servers expose the Python APIs. Scientific execution stays in srim_mcp; project organization stays in project_mcp. Their protocol code is grouped under src/mcp. Existing source APIs import no MCP modules. Each server has a separate workspace singleton lock, job journal and tool namespace.

## Discovery and contracts

Call capabilities_list for compact cards, capability_get for exact side effects/compatibility/units/examples, schema_get for expanded Draft 2020-12 schemas, and example_get for editable requests. A capability detail can describe an advanced Python-only API and intentionally have no executable tool_names. Implementation, MCP exposure, engine compatibility, reviewed automation, output availability and evidence strength are distinct fields.

The scientific catalog exposes 38 tools, 61 capability cards and 28 examples. It includes SR list stopping and engine-grid ranges; all version-dependent TRIM modes; layers/isotopes/damage controls; events; resources/CAB; native and recovered results; conditional histories; prepared execution/batches; input search; and cleanup. Eight descriptive TRIM mode cards retain the seven-mode 2008 mapping and eight-mode 2013 mapping. Examples are input templates, not scientific recommendations.

environment_get reports installation.sr and installation.trim readiness and file identities independently. Default-resource readiness is separate from executable presence and from reviewed TRIM dialog-policy compatibility.

project_mcp exposes 32 tools. It records multiple independent cases, terminal native evidence, analysis provenance, unapproved capability candidates and improvement evidence. Active reusable workflow/analysis/skill collections start empty. It has no native execution, generic code executor, trial-approval or candidate-promotion tool.

Improvement events, proposals and trial snapshots use `.local/workflow-improvement/`.
This is persistent Git-ignored worktree state, not a backup. Preserved historical
cache dependencies use protected `.local/.cache/`; figure records use
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

## Limits and ownership

mcp.toml is separate from the strict existing settings.toml. Defaults are four scientific parallel slots, one management slot, 64 pending jobs per server, 16-MiB requests, 1-MiB structured replies, 64-MiB parsed JSON files and 256-KiB verified file chunks. These are adapter limits, not physical/native caps. Large saved results remain readable through chunks rather than silent truncation. Analysis/candidate attachment caps are 64 MiB per file and 256 MiB per operation.

Batch max_workers reserves that many global scientific slots. Shared workers are FIFO; independent workers may overlap. Existing worker leases remain final authority against other Python clients. A null worker selection allocates a new worker at dispatch, without a hidden worker reuse/scaling heuristic. SR batches retain their existing mandatory verified cleanup; TRIM cleanup is explicit and defaults to retention. Cleanup never deletes permanent evidence.

allowed_input_roots adds explicit read-only external directories; an empty list permits the repository root only. Fixed native installation reads use settings.toml through existing APIs. Caller paths do not inherit arbitrary installation-directory browsing rights. Traversal, device/stream names, symlinks/junctions and case collisions are rejected. File reads verify hashes, sizes and stable identities; Windows handle/path timestamps are compared within each stat API rather than assuming their ctime representations match.

## Cancellation, restart and qualifications

Queued jobs can be cancelled before dispatch. Active individual TRIM execution uses the existing cooperative cancellation callback and records completion only after owned native shutdown/preservation. Active SR is timeout-only; preparation/CAB, publication and active batches have queued-only cancellation. A racing successful completion remains success. TRIM timeout can be null; an unlimited active TRIM batch can delay graceful shutdown indefinitely. Disconnect/shutdown cannot invent cancellation support the backend lacks.

Graceful shutdown stops admissions, cancels queued jobs, requests supported cancellation and waits for active finalization. Forced termination can leave SR children or partial publication. On restart, unfinished jobs become interrupted, their workers are quarantined, and requests are never automatically re-executed. There is no MCP auto-release tool: reconcile native activity/evidence explicitly before an approved recovery change, or use newly allocated workers. Released Python locks alone do not prove native processes stopped. Exactly-once native execution after a crash is not guaranteed.

TRIM continuation keeps the approved checkpoint-first lifecycle, saved-value checks, configuration qualifications and recovery provenance. Stable checkpoints do not prove atomic binary saving or independent physical accuracy. Fresh native-text and recovered checkpoint results remain distinct. Quick-mode uncalculated quantities remain unavailable. F-button/chooser exports and external collection imports are outside this interface.

## Resources

The srim:// and project:// schemes expose capability/schema/example indexes and identity templates, job snapshots and artifact manifests. project:// additionally exposes case and analysis revisions, collection entries/candidates and improvement state. Tools provide equivalent discovery when a host does not expose resources conveniently. URI parameters are identities, never arbitrary filesystem paths. Initial subscriptions, sampling, elicitation, prompts and remote HTTP transports are not advertised.
