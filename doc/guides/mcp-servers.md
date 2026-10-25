# Local MCP servers

The two independent local servers expose the [current MCP interfaces](../reference/mcp-format.md). srim_mcp exposes 40 scientific/discovery tools; project_mcp exposes 46 management/retrieval/discovery tools.

Both servers use local stdio. Their adapters live under `src/mcp/srim_mcp/` and `src/mcp/project_mcp/`, with shared infrastructure under `src/mcp/common/`. Existing calculation APIs remain separate.

One agent session can create and work with multiple cases. Each operation selects an explicit case ID and revision; there is no session-wide current case or one-case-per-session restriction. Runs and analyses can be referenced by multiple cases without copying or transferring ownership of their evidence.

| Server | Tool group | Tools |
| --- | --- | --- |
| Both | Discovery | capabilities_list, capability_get, schema_get, example_get |
| Both | Jobs and artifacts | job_get, jobs_list, job_cancel, artifact_read |
| srim_mcp | Environment and resources | environment_get, defaults_get, defaults_switch, resources_list, resource_read, compounds_search, variant_create, cab_populate |
| srim_mcp | Validation and inputs | request_validate, input_render, sr_resolve, sr_prepare, trim_resolve, trim_bundle_load, trim_prepare |
| srim_mcp | Execution | trim_continue_prepare, run_submit, batch_submit, run_closeout, run_reconcile |
| srim_mcp | Records and results | simulations_list, simulation_get, source_get, source_read, result_read, table_read, trim_availability_get |
| srim_mcp | Recovery, search and cleanup | trim_parse, trim_recover, inputs_search, index_update, workers_cleanup |
| project_mcp | Unified evidence search | knowledge_search, knowledge_get, knowledge_links, knowledge_status, knowledge_update |
| project_mcp | External source inspection | database_sources_list, database_inspect, database_sample_read |
| project_mcp | External source preview/import and refresh (managed jobs) | database_preview, database_import |
| project_mcp | External source search and access (reads) | database_search, database_get, database_status |
| project_mcp | Cases | cases_list, case_get, case_create, case_revise |
| project_mcp | Run references | runs_list, run_get, run_source, physics_job_get |
| project_mcp | Analysis records | analyses_list, analysis_get, analysis_create, analysis_revise, analysis_attach, analysis_finish |
| project_mcp | Reusable capabilities | collections_list, collection_entries, collection_entry_get, candidates_list, candidate_get, candidate_create, repository_skills_list |
| project_mcp | Self-improvement evidence | improvement_status, improvement_observe, improvement_assess, improvement_proposal_create |

srim_mcp owns native calculation execution and verified result access. project_mcp organizes cases, references and analysis evidence. Initial reusable collections are empty, and analysis recording does not execute an unspecified scientific method. Individual TRIM execution supports cooperative cancellation; active SR execution uses its configured timeout. See the [interface reference](../reference/mcp-format.md) and [project guide](project-management.md) for record contracts, side effects and limitations. Use `schema_get` and `capability_get` for exact current tool arguments.

## Setup and launch

The original calculation APIs retain their standard-library implementation. MCP and the new management schema features use optional dependencies declared in requirements-mcp.txt:

```powershell
D:\projects\agentic_srim\.venv\Scripts\python.exe -m pip install -r requirements-mcp.txt
```

Configure the existing native installation/time preferences in settings.toml. mcp.toml separately sets adapter concurrency, queue, request/reply/file limits and explicit read-only external input roots. Neither startup imports defaults nor launches an engine. The SDK is pinned to 2.3.0; CLI extras are not required.

From D:\projects\agentic_srim, start the selected server as a stdio child process:

```powershell
D:\projects\agentic_srim\.venv\Scripts\python.exe -m src.mcp.srim_mcp --root D:\projects\agentic_srim
D:\projects\agentic_srim\.venv\Scripts\python.exe -m src.mcp.project_mcp --root D:\projects\agentic_srim
```

These are separate processes; stdout is reserved for MCP and diagnostics use stderr. Each server holds one workspace lock; a duplicate instance of the same server fails while the other named server can coexist. Import from the repository root as src.mcp.*; do not put src/ on PYTHONPATH, which could shadow the installed mcp SDK.

The project registers both servers in [.codex/config.toml](../../.codex/config.toml), using explicit command/args/cwd entries. The [host registration example](../examples/mcp-servers.toml) mirrors these entries for reference. Codex loads project configuration for trusted projects, as documented in the [official OpenAI configuration guidance](https://developers.openai.com/plugins/build/plugins). Reload the project or start a new Codex session after changing this configuration. Codex uses named mcp_servers TOML entries with command/args/cwd as documented in the [official OpenAI MCP guide](https://learn.chatgpt.com/docs/extend/mcp). Host permission policy still controls how it presents tools and approvals.

## First calculation through tools

1. Discover capabilities, inspect environment_get, read the relevant schema/example, and choose parameters/units explicitly.
2. Call request_validate for pure syntax checking. Inspect defaults_get; use an explicit defaults_switch job only when an import/switch is intended.
3. For SR, submit sr_prepare. For TRIM, submit trim_resolve and then trim_prepare using the returned bundle hash. Preparation may publish selected variants or execute missing CAB dependencies; main execution remains separate.
4. Poll job_get, then read the job's immutable result reference with artifact_read. Pass its PreparedReference into run_submit with an explicit timeout preference.
5. Inspect terminal job/native status, result_read manifest/summary, availability and relevant table_read pages. Never equate a successful read with physical validity or replace unavailable values with zero.
6. Use project_mcp to associate the explicit job/source/analysis references with any relevant cases. See [multiple cases and analysis records](project-management.md).

Every mutating call requires a fresh UUID for a new request. Retries of the same intended request reuse its original UUID to recover the same accepted job. The [interface reference](../reference/mcp-format.md) explains structured results, chunk reads, cancellation, forced-termination/restart limits and worker quarantine. Unsupported active cancellation returns an explicit reason; no automatic retry or interrupted-worker release is performed.

## Verification

Synthetic tests use temporary workspaces under .cache/mcp-verification and fake native installations/process substitutes. The installed SDK is exercised through both real module/stdin/stdout entry points, including multiple cases in one client session. These checks verify adapters and management contracts. They do not run a new native physics experiment or certify every native mode. Host registration is configured separately in `.codex/config.toml` as described above.

For current test commands, recorded native coverage and known limitations, see the [verification reference](../reference/validation.md). Historical delivery totals describe their original tested snapshot.

## Close out started runs

Call `run_closeout` with explicit `simulation_ids` for every started task and
dependency run. A `complete=false` report lists stale, uncertain or invalid
records; created-only preparations are separate. `run_reconcile` is a durable
mutating job accepting the same IDs plus a fresh `request_id`. It finalizes a
running record as failed only when matching durable stop evidence and the worker
lease establish safety. Review its `issues` and final `closeout` report.

Scientific-server startup and graceful shutdown run the same safe recovery for
simulations linked to ended jobs. Reports are retained in
`memory/mcp/srim_mcp/recovery/startup_<uuid>.json` or `shutdown_<uuid>.json`.
Quarantined workers stay quarantined, even after metadata recovery. A missing or
changed stop proof, uncertain native ownership or failed metadata write remains
unresolved. No native process is started, stopped or retried by reconciliation.
See the [report and evidence contracts](../reference/mcp-format.md#run-closeout-and-reconciliation).


## External source tools and settings reload

The `databases` family includes three registration/inspection reads,
`database_preview`, `database_import` and `database_reindex` managed jobs, and
`database_search`, `database_get` and `database_status` reads. The installed
`snumat-trim-v1` adapter supports external TRIM inputs and six calculated
summary quantities. External searches use a separate catalog; native/project
records remain available through the existing `knowledge_*` tools.

After installing or changing adapter/tool code, restart the existing
`project_mcp` server and reconnect the client if needed to refresh tool discovery.
No separate server registration is needed for another database using a supported
schema. Source operations reload `knowledge.database_paths` from
`settings.toml`, so adding or removing registered paths needs no server restart.
Removing a registration revokes source reads while retaining derived history.

Adding data in a supported schema needs an explicit `database_preview`, followed
by `database_import` with the reviewed plan hash. The same workflow refreshes
an existing catalog: add new identities, version changes, skip unchanged records,
and report missing records while preserving history. After import, explicitly run
`database_reindex` with the imported generation. It builds searchable fields from
the retained records without another preview; search refuses stale or missing
indexes until the build finishes. A changed scientific format
may require an adapter update and server restart. Startup, searches and file
changes never import or refresh sources automatically. See
[external onboarding](input-search.md#external-database-registration-and-onboarding).
