# Agentic SRIM

Run SRIM calculations from an AI agent or a command line, then retrieve structured results with units and traceable simulation records.

Agentic SRIM connects a local SRIM installation to an MCP-compatible agent. The agent can validate inputs, run calculations, inspect results and export selected data. Separate Case and Analysis servers persist case checkpoints and execute versioned postprocessing methods. The command-line interfaces support setup and inspection.

## Capabilities

| Capability | Calculation | Results |
|---|---|---|
| Stopping and range | SR Module tables over an energy interval | Electronic and nuclear stopping, projected range, longitudinal and lateral straggling |
| TRIM transport | Ion transport and damage in layered targets; modes 1–8 with the restrictions below | Summary, depth profiles, range distributions, backscatter, transmission, sputtering, trajectories and requested collision records |

Both capabilities preserve scientific inputs, native inputs/outputs, simulator versions, logs and checksums for each attempt. Results include units and provenance linking them to their originating run.

A successful run has state `CHECKED`: its outputs passed software format, request-matching and artifact-integrity checks. Independent scientific accuracy and convergence must be evaluated for the intended application.

## First operational case

Use the three local MCP servers with a manual Coordinator procedure; no workflow engine, reference-ingestion system or RAG setup is required for this path.

1. Obtain the user's scientific inputs, requested outputs, run permission and budget. Create a Case and record the objective/constraints. Case budget fields retain evidence; they do not enforce a case-wide spending limit.
2. Validate the explicit SRIM request, then perform only the authorized calculation. Checkpoint the returned run ID. Software CHECKED does not establish scientific accuracy or convergence.
3. Export the required table/profile from its exact CHECKED attempt. Discover and pin an intact, current-environment verified Analysis method/version/checksum.
4. Save a pending Case action containing the exact submission/key. Submit Analysis, checkpoint its ID, poll to a terminal state and retrieve pinned outputs. Use a separate rendering job only when plots are requested.
5. Atomically link the verified result and related Case progress/action resolution. Save the answer with sources, units and limitations. Stop or request further authorization instead of starting an autonomous sweep.

Interrupted submissions/links replay saved keys; they do not trigger new calculations. The detailed tool contracts below support this procedure. Scientific validation and expanded operational hardening can be added independently later.

## Shared Codex setup

The repository includes the host instructions in `AGENTS.md`, discoverable skills under `.agents/skills/`, and reviewed Git command rules in `.codex/rules/git.rules`. Open the clone as a Codex project and follow the setup below to create its `.venv`, configure the simulator location, and register the SRIM, Case and Analysis MCP servers. The agent follows the manual case loop above; a separate workflow engine or self-improvement service is not implemented.

Codex discovers repository skills automatically. The included `adaptive-search` and `hand-drawn-curve-detection` skills guide constrained optimization and image-to-curve preparation; they provide instructions rather than installed numerical methods. Project Git rules load at startup after the project is trusted; review them before trusting the clone and restart Codex if needed. See the [official skill discovery](https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills) and [project rule documentation](https://learn.chatgpt.com/docs/agent-configuration/rules).

Machine-specific `.codex/config.toml` MCP registration and optional `.codex/rules/python.rules` interpreter approval remain ignored. Use the [complete Codex registration example](#codex-project-configuration) below with this clone's absolute paths. `.gitignore` exposes only the reviewed Git rule within `.codex/`; explicitly allowlist other portable setup files when adding them. Environments, runtime stores, credentials and local approval settings are not shared.

## Requirements

- Windows with an interactive desktop session for native SRIM/TRIM execution. TRIM is a GUI program and may briefly display dialogs.
- [Python 3.13 or later](https://www.python.org/downloads/windows/).
- [Git](https://git-scm.com/downloads/win) to clone the repository.
- A separately installed [SRIM suite](https://www.srim.org/) for native calculations. Native integration has been exercised with SRIM-2013; Case and Analysis can run without the simulator.
- An MCP client or AI agent supporting local stdio servers, if using the agent interface.

SRIM executables and reference data are not bundled. Follow the simulator's installation and licensing requirements.

## Setup

Run these commands in PowerShell.

### 1. Clone and install

Use a directory where you have write access:

~~~powershell
git clone https://github.com/c-earth/agentic_srim.git
Set-Location agentic_srim
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -e .
& .\.venv\Scripts\python.exe -m pip check
~~~

The `python` command in the environment-creation step must resolve to Python 3.13 or later. Once the environment exists, use its interpreter for package operations. Activation is optional.

Create a convenient command variable:

~~~powershell
$srim = (Resolve-Path .\.venv\Scripts\agentic-srim.exe).Path
& $srim --help
~~~

Run the following examples from the cloned repository directory. Recreate `$srim` when opening a new PowerShell window.

### 2. Configure the simulator

Edit [configs/application.toml](configs/application.toml), replacing the installation path with the location on your computer:

~~~toml
srim_directory = 'C:\SRIM-2013'
runtime_directory = '../runtime'
timeout_seconds = 30
max_attempts = 1
~~~

The installation root must contain:

~~~text
C:\SRIM-2013\
  TRIM.exe
  Data\
  SR Module\
    SRModule.exe
    SCOEF03.dat
    SNUC03.dat
    VERSION
~~~

Keep the simulator installation outside the runtime directory. Calculations use isolated copies of support files, preserving the original installation.

`runtime_directory` stores generated workspaces, run records and results. Relative paths resolve from the configuration file's directory; `../runtime` therefore creates `runtime/` at the repository root. The timeout applies per attempt and must be positive and at most 3600 seconds. `max_attempts` allows 1–3 attempts.

### 3. Check input handling

The supplied regression requests provide small software test cases. Their physical values and sampling counts are not reviewed scientific reference cases.

~~~powershell
& $srim validate tests/sr_module/fixtures/request.json
& $srim validate tests/trim/fixtures/mode_2/request.json --capability trim
~~~

Validation prints normalized data and rendered simulator input without starting a calculation.

## Run a calculation

### Stopping and range

Run the supplied SR request, retain its ID, and retrieve the table:

~~~powershell
$run = & $srim run tests/sr_module/fixtures/request.json | ConvertFrom-Json
$runId = $run.id
$run.state
& $srim show $runId
& $srim output $runId --capability stopping_range
~~~

`show` returns metadata and lifecycle history; `output` returns calculated values. Energies are reported in keV, range and straggling in nm, and stopping in the requested native unit.

SR Module selects the energy grid within the requested interval; exact endpoints need not appear and no interpolation is applied. Straggling describes a physical distribution width.

### TRIM transport

~~~powershell
$trimRun = & $srim run tests/trim/fixtures/mode_2/request.json --capability trim | ConvertFrom-Json
$trimRunId = $trimRun.id
& $srim show $trimRunId
& $srim output $trimRunId RANGE.txt --capability trim --offset 0 --limit 100
& $srim output $trimRunId COLLISON.txt --capability trim --offset 0 --limit 100
~~~

TRIM output is paged by native filename. Follow `next_offset` for subsequent records; it is null at the end. Each page contains at most 1000 records. Optional files must be enabled in the request.

Available families include `RANGE.txt`, `LATERAL.txt`, `IONIZ.txt`, `PHONON.txt`, `VACANCY.txt`, `E2RECOIL.txt`, `NOVAC.txt`, `RANGE_3D.txt`, `BACKSCAT.txt`, `TRANSMIT.txt`, `SPUTTER.txt`, `TRIMOUT.txt`, `EXYZ.txt` and `COLLISON.txt`. The run result also includes the `TDATA.txt` summary.

### Supply your own inputs

Copy a regression request to a file of your choice, edit it, then validate before running:

~~~powershell
Copy-Item tests/trim/fixtures/mode_2/request.json trim-request.json
& $srim validate trim-request.json --capability trim
& $srim run trim-request.json --capability trim
~~~

Replace the physical values and update the `sources` descriptions to identify their origin. Every request declares `schema_version: 1`.

| Input | Stopping and range | TRIM |
|---|---|---|
| Projectile | Atomic number and mass in u | Atomic number, mass in u, beam energy in keV and angle in degrees |
| Target | Composition, constituent masses, density in g/cm³, phase and compound correction | Element definitions plus 1–8 layers with composition, density, width in nm, phase and compound correction |
| Calculation | Positive increasing energy interval in keV and stopping-unit code | Mode, count, seed, depth window, stopping-power version and output switches |
| Damage/event data | Not applicable | Element displacement/lattice/surface energies in eV; explicit events for modes 4–8 |

Composition uses relative atomic amounts, not mass fractions. TRIM supports 1–12 tracked element columns. External events specify energy in eV, position in nm and normalized direction cosines. Modes 4–8 require one supplied event per requested event. Recoil modes 6–8 consume supplied recoil events; they do not transport external neutrons, electrons or photons.

For SR, `phase: "solid"` selects the condensed-phase flag, including liquids. For TRIM, a `0,0` depth window selects the whole target, and `random_seed: 0` explicitly requests the native default seed. Other scientific choices are supplied explicitly. Complete contracts are defined in [SR inputs](src/agentic_srim/capabilities/stopping_range/inputs.py) and [TRIM inputs](src/agentic_srim/capabilities/trim/inputs.py), and exposed through MCP tool schemas.

### TRIM modes and restrictions

| Mode | Calculation | Collision output |
|---|---|---|
| 1 | Incident ions, quick KP damage | Off or ion summaries |
| 2 | Incident ions, full cascades | Off, ion records, or ion and recoil details |
| 3 | Monolayer steps / surface sputtering | Off, ion records, or ion and recoil details |
| 4 | Explicit incident-ion events, quick KP damage | Off or ion summaries |
| 5 | Explicit incident-ion events, full cascades | Off, ion records, or ion and recoil details |
| 6 | Explicit recoil events, full cascades | Off |
| 7 | Explicit recoil events, full cascades with monolayer steps | Off, ion records, or ion and recoil details |
| 8 | Explicit recoil events, quick KP damage | Off or KP records with an explicit summary/every choice |

Mode numbering follows the installed SRIM-2013 GUI. Its Input PDF p. 8-17 assigns different meanings to modes 6/7 and omits mode 8. Mode-6 collision output is disabled because this installation produces a collision-activation warning; its internal cause is unknown. KP modes 1/4/8 reject full recoil-cascade output.

For mode 8 with `outputs.collisions: 1`, supply `outputs.collision_records: "summary"` or `"every"`. This selects the native collision-file dialog response and is recorded in provenance. For other combinations, omit the field or set it to null. Approved dialogs are handled automatically; unexpected dialogs remain untouched and the attempt is bounded by its timeout.

## Connect an AI agent through MCP

Add a local **stdio** server in your MCP client:

| Setting | Value |
|---|---|
| Command | Absolute path to `.venv/Scripts/python.exe` in your clone |
| Arguments | `-m agentic_srim.interfaces.cli --config` followed by the absolute path to `configs/application.toml`, then `mcp` |
| Working directory | Absolute path to the cloned repository |

For clients using a `mcpServers` JSON configuration, adapt this example to your paths:

~~~json
{
  "mcpServers": {
    "agentic_srim": {
      "command": "C:/path/to/agentic_srim/.venv/Scripts/python.exe",
      "args": [
        "-m",
        "agentic_srim.interfaces.cli",
        "--config",
        "C:/path/to/agentic_srim/configs/application.toml",
        "mcp"
      ]
    }
  }
}
~~~

Configuration formats vary by client. Use the executable and arguments above in its local-server settings. An absolute configuration path allows startup outside the repository directory. Set the client tool timeout high enough for all attempts plus preparation and verification; 120 seconds accommodates the default one-attempt configuration.

Restart the server after changing Python code. Agent calls pass complete `request` objects directly; they do not load request files by filename. The agent/client supplies the language model and controls execution approvals.

### Codex project configuration

After installing the package, merge these tables into your clone's ignored `.codex/config.toml`, preserving unrelated settings. Replace every `C:/path/to/agentic_srim` with the absolute path to your clone. Use a trusted project so Codex loads this configuration; restart Codex after saving. The [official MCP configuration guide](https://learn.chatgpt.com/docs/extend/mcp?surface=cli) describes these stdio settings.

~~~toml
[mcp_servers.agentic_srim]
command = "C:/path/to/agentic_srim/.venv/Scripts/python.exe"
args = ["-B", "-m", "agentic_srim.interfaces.cli", "--config", "C:/path/to/agentic_srim/configs/application.toml", "mcp"]
cwd = "C:/path/to/agentic_srim"
tool_timeout_sec = 120

[mcp_servers.case]
command = "C:/path/to/agentic_srim/.venv/Scripts/python.exe"
args = ["-B", "-m", "case", "--config", "C:/path/to/agentic_srim/configs/case.toml", "mcp"]
cwd = "C:/path/to/agentic_srim"

[mcp_servers.analysis]
command = "C:/path/to/agentic_srim/.venv/Scripts/python.exe"
args = ["-B", "-m", "analysis", "--config", "C:/path/to/agentic_srim/configs/analysis.toml", "mcp"]
cwd = "C:/path/to/agentic_srim"
~~~

Confirm discovery of ten SRIM, ten Case and thirteen Analysis tools. Read-only `list_cases` and `list_methods` calls work against a new empty runtime; empty lists are expected before creating cases or registering methods. Prepare Analysis methods using the [installation and verification steps](#analysis-and-figures) below before submitting numerical or plotting jobs. Connecting the servers does not start a simulation or install methods. For standalone Case/Analysis use, omit the SRIM table.

The project skills are available as `$adaptive-search` and `$hand-drawn-curve-detection`, or Codex can select them from matching requests. Their availability does not install an optimizer or image-processing method, create a case, or authorize a run. After setup, begin with the [manual case procedure](#first-operational-case).

### SRIM tools

| Task | MCP tools |
|---|---|
| Validate inputs | `validate_stopping_range`, `validate_trim` |
| Execute calculations | `run_stopping_range`, `run_trim` |
| Read results | `get_run`, `get_stopping_range_output`, `get_trim_output` |
| Cancel or reconcile interrupted work | `cancel_run`, `recover_runs` |
| Export selected existing data without simulation | `export_run_data` |

A typical workflow is to gather explicit scientific inputs and sources, validate the request, obtain authorization to run, execute, and retrieve the outputs. Scientific applicability and convergence remain part of the analysis.

## Case management

Case MCP is a separate stdio server in the `case` package. It uses the same Python environment and requires no simulator installation. The existing SRIM CLI/MCP configuration continues to use `agentic_srim`.

`configs/case.toml` sets `runtime_directory = "../runtime"` and optional `[stores]` producer aliases; paths resolve relative to that configuration file. Case writes create `runtime/cases.sqlite` alongside the simulation `metadata.sqlite`. Server startup and read-only requests do not initialize storage. Keep all cases that should find each other in the same configured case runtime.

Add another local server to your MCP client:

| Setting | Value |
|---|---|
| Command | Absolute path to your clone's `.venv/Scripts/python.exe` |
| Arguments | `-B -m case --config` followed by the absolute path to `configs/case.toml`, then `mcp` |
| Working directory | Absolute path to the cloned repository |

The module entry point works with the editable installation. A refreshed installation also exposes `agentic-case`; no additional runtime dependencies are required. All three servers advertise MCP protocol baseline `2024-11-05`.

| Task | Case MCP tools |
|---|---|
| Create or branch | `create_case`, `branch_case` |
| Find and resume | `list_cases`, `search_cases`, `get_case` |
| Revise or checkpoint | `update_case`, `record_case_progress` |
| Attach producer-owned evidence | `link_case_artifact` |
| Inspect history or readable views | `get_case_history`, `get_case_overview` |

Tools accept version-1 JSON contracts published by `tools/list`; [contracts](src/case/contracts.py) and [record schemas](src/case/records.py) define the complete fields. Tool results contain JSON in MCP text content. Failures set `isError` and return an `error` object with a stable `code` and a human-readable `message`.

For example, pass this object directly to `create_case`:

~~~json
{
  "title": "Review archived profiles",
  "original_request": "Compare the available archived depth profiles and report limitations.",
  "actor": "coordinator",
  "reason": "Record the user's case objective",
  "idempotency_key": "profile-review-create-1",
  "content": {
    "constraints": ["Use existing archives"],
    "prohibitions": ["Do not start simulations"],
    "plan": ["Find applicable archives", "Review provenance and limitations"],
    "next_action": "Find applicable archives"
  }
}
~~~

Creation returns `case` with a stable 32-character lowercase hexadecimal ID and revision 1. Titles need not be unique; use the ID to disambiguate. Metadata includes schema version, timestamps and actor fields. The original request is immutable; record revised interpretations in `objective` and the revision history.

Every mutation requires `actor`, `reason` and `idempotency_key`. Updates, progress checkpoints and artifact links also require `case_id` and `expected_revision`. Retry the exact same request with the same key after an uncertain outcome: the original committed response is returned even if newer revisions exist. Reusing the key with different arguments fails with `idempotency_conflict`; stale updates fail with `revision_conflict`. Use a new key for a new operation. Snapshot, audit entry and retry response commit together.

`update_case` takes a `patch`; `record_case_progress` takes `progress`. Supplied fields replace their current values, including complete lists; omitted fields are preserved. Both can save several related fields atomically. Narrative `decisions`, `findings`, `analyses` and `answers` entries contain `text`, optional `limitations`, and optional `artifact_refs` with `owner`/`artifact_id` pairs already linked to the case. Approvals record scope, decision and source. Budgets record optional `run_limit`, `wall_time_seconds` and notes. These fields retain evidence; Case MCP neither grants execution permission nor enforces simulation budgets.

Status is `active`, `paused`, `blocked`, `completed` or `cancelled`. Blocked cases require an unresolved issue; completion requires a recorded answer and does not imply scientific acceptance. Completed/cancelled cases must first be reopened using a separate `patch` containing only `{"status":"active"}` before editing. Archiving uses a separate `{"archived":true}` patch, preserves status/history, and excludes the case from ordinary searches. Unarchive with `{"archived":false}` before further edits. Reopening/unarchiving never starts work automatically. Version 1 retains all revisions/retry records indefinitely and provides no deletion operation.

`branch_case` requires `parent_case_id`, `parent_revision`, a new title/original request and mutation audit fields. It creates a new active case with a pinned parent reference. Constraints, prohibitions, assumptions, budget evidence and artifact links carry over; approvals, plans, answers and other working content start empty. The Coordinator must review inherited restrictions and applicability before continuing.

Lists/searches default to 20 summaries, support limits 1–100, and return `next_cursor` for the next ID-ordered page. Filters include `status` and `include_archived`; search performs literal Unicode case-insensitive matching over current case content. Each call reads a coherent database snapshot; pages across calls do not freeze later changes. History uses `after_revision`/`next_revision` and limits 1–100. `get_case` accepts an optional exact `revision`. A snapshot is bounded to 256 KiB, collections to 100 entries, and incoming stdio lines to 512 Ki characters.

Artifact links require `owner`, `artifact_id`, `kind`, `purpose` and `provenance`. Multiple cases can link the same producer identity. Optional `relative_path` locators resolve within the case runtime, and optional `sha256` checksums require a locator. Absolute paths, traversal and escaping symlinks are rejected. Optional typed `reference` objects resolve SRIM exports and Analysis output/diagnostic attempts through configured aliases, verifying their producer index and sealed bytes. Analysis artifact IDs may be the job ID or `job_id:attempt_id`. Reads report current availability; checksum work is limited to 64 MiB total per read. A verified file means its bytes match the recorded hash, not that its science is accepted. Generic references without resolvable locators remain unresolved. Case MCP never copies, modifies or deletes producer archives.

Human-readable overviews are generated on demand from authoritative records; there is no independently editable cached overview. To inspect a case from PowerShell, substitute its returned ID:

~~~powershell
$caseId = 'paste-the-returned-case-id-here'
& .\.venv\Scripts\python.exe -B -m case --config configs/case.toml show $caseId
& .\.venv\Scripts\python.exe -B -m case --config configs/case.toml overview $caseId --revision 1
~~~

`pending_actions` is an additive version-1 list; older revisions read with an empty list. Each action has `schema_version: 1`, fresh `action_id`, `kind: "analysis"`, `state: "intent"` and the complete immutable `submit_analysis` request in `submission`. Checkpoint intent through `record_case_progress`, submit with its saved key, then checkpoint `state: "submitted"` and the returned `analysis_id`. On continuation, replay the saved submission to recover a lost response. IDs/requests cannot change or disappear after checkpointing. To finish, call `link_case_artifact` with the verified producer `reference`, `resolve_action_id`, and optional `progress`; artifact attachment, progress and transition to `resolved` commit at one expected case revision. Lost link responses replay the same key; stale revisions require rereading. Linking failure never requests recomputation. Case archive/branch operations do not cancel shared jobs or complete cases automatically.

## Analysis and figures

Analysis is a separate `analysis` package/stdio server using trusted local Python with the user's permissions. Its numerical methods need no third-party dependencies or simulator installation. Rendering uses the optional pinned [Matplotlib](https://matplotlib.org/stable/users/explain/figure/backends.html) extra with the noninteractive Agg backend:

~~~powershell
& .\.venv\Scripts\python.exe -m pip install -e '.[plotting]'
& .\.venv\Scripts\python.exe -m pip check
~~~

Register the bundled methods explicitly; numerical-only users can skip installing the plotting extra:

~~~powershell
& .\.venv\Scripts\python.exe -B -m analysis --config configs/analysis.toml install-builtins --actor 'local operator' --reason 'Install reviewed bundled methods'
~~~

This explicitly registers `dataset_summary`, `aligned_comparison` and `render_figures`, each at version 1, without running method code. It returns exact method references. Registration never grants verification, scientific acceptance or human promotion. Ordinary server startup and discovery create no missing stores or bundles. Install-builtins reuses existing version-1 records; use discovery to review their pinned checksums rather than assuming a software upgrade replaces a stored bundle.

For each method you intend to use, submit `verify_method` with `schema_version: 1`, its returned exact `method` reference, `limits: {}` to accept configured defaults, and `actor`/`reason`/a fresh `idempotency_key`. Poll `get_analysis` using the returned `verification_id` as `analysis_id` until it reaches a terminal state; require `CHECKED` before use. Verify the numerical methods for numerical work and also `render_figures` for plots. Verification runs trusted Python tests and retains their artifacts; it does not run SRIM/TRIM or grant scientific approval. A renderer verification needs the plotting dependency. Reverify after environment changes.

Add a local stdio MCP server with your environment interpreter, arguments `-B -m analysis --config <absolute-config-path> mcp`, and the clone as its working directory. The console entry point is `agentic-analysis`. For Codex, a trusted project can use a `[mcp_servers.analysis]` table in its ignored `.codex/config.toml`; see the [official MCP configuration guidance](https://learn.chatgpt.com/docs/extend/mcp?surface=cli). No API key is needed for these local servers.

`configs/analysis.toml` selects the runtime, optional `python` interpreter (default: server interpreter), `[stores]` aliases and `[limits]` ceilings. All configured paths resolve relative to that file. An alias identifies a producer runtime; callers never supply a host input path. This service does not create environments or install method dependencies. Requirements pin exact distribution versions and Python major/minor or full version. Verification/execution preserve interpreter hash, OS/Python/dependency inventory and service-code hashes in an exact environment fingerprint. A changed environment requires new verification; earlier records remain available.

| Task | Analysis tools |
|---|---|
| Discover exact versions and current eligibility | `list_methods`, `get_method`, `get_method_history` |
| Seal new code and record supervised tests | `register_method`, `verify_method` |
| Submit, find and inspect jobs | `submit_analysis`, `list_analyses`, `get_analysis` |
| Read pinned tables, figures or diagnostics | `get_analysis_output` |
| Control/reconcile jobs | `cancel_analysis`, `retry_analysis`, `recover_analyses` |
| Append scoped validity/promotion evidence | `record_method_assessment` |

All thirteen tools advertise their complete schemas in `tools/list` ([contracts](src/analysis/contracts.py)). JSON rejects unknown fields, duplicate keys, nonfinite values and nesting beyond 32; messages are at most 16 MiB. Responses carry `schema_version: 1`; errors contain stable `code`, `message` and `details`. Discovery/history/status default to 20/max 100 records. `list_analyses` supports kind, state, method ID/version and source-ID filters. Cursors preserve filters/order across pages, without freezing subsequent state changes.

Register new methods with `schema_version`, stable `method_id`, next positive integer `version`, `specification`, a bounded `files` map, and `actor`/`reason`/`idempotency_key`. Each method's sequence starts at 1 and advances independently without overwrite. Bundles accept up to 64 UTF-8 text files/2 MiB, including `method.py` and `tests/test*.py`; paths are portable relative names with no traversal, links, archives or caller host paths. Registration never imports the bundle. Specifications include description/applicability, input/parameter/output schemas, exact `requirements`, `python`, `reproducible`, explicitly pinned `external_state` (including a declaration of no randomness), and exact `components` for compositions. The supported schema subset is defined in [records.py](src/analysis/records.py); unsupported keywords fail explicitly.

The fixed callable is `method.py:run(inputs, parameters, context)`. Inputs are verified manifest dictionaries keyed by caller names; context supplies their snapshot directories, output/scratch directories, effective limits, exact environment and copied component bundle locations keyed by `method_id@version`. Compositions allow at most 32 transitive pinned versions. Return exactly `schema_version`, `summary`, `tables`, `figures` and `limitations`. Table descriptors include name/path/columns with type/nullability/units/normalization/row count. JSONL files contain row arrays in declared column order; bounded output retrieval returns named rows. Figure descriptors include path/format/width/height. The supervisor independently checks schemas, file inventories, row types, dimensions, bounds and hashes. Unlisted, missing or invalid files prevent `CHECKED`. Partial failed outputs are diagnostic only.

`verify_method` requires `schema_version: 1`, exact `method: {method_id, version, bundle_sha256}`, explicit `limits` (an empty object accepts defaults) and audit fields. It promptly returns a durable `verification_id`, also exposed as `analysis_id` with `kind: "verification"`. Poll `get_analysis`; retrieve its report through the same output tool. Bundled unittest discovery must run at least one non-skipped test and pass. Missing dependencies or failing tests produce a retained failed attempt. Every execution requires intact pinned bundles/components and passing verification for the exact environment. A test pass covers those tests, not scientific accuracy.

Export existing data through SRIM's `export_run_data`: pass schema version 1, exact `run_id`/`attempt_id`, a configured transport `store_id`, ordered `datasets`, and audit fields. Each dataset selects `name`, optional ordered `columns`, and optional `offset`/`count`; omission means the complete dataset. SR table name is `stopping_range`; TRIM names are its parsed profile/detail filenames. Exports require a sealed CHECKED result and preserve units, normalization, request conditions, parser/source hashes, selection and scientific limitations. Publication is atomic under `runtime/exports/<export_id>/`; the producer indexes it in `metadata.sqlite` and reconciles interrupted same-key publication. Native archives are unchanged.

The returned pinned reference has `schema_version`, `owner: "srim"`, `store_id`, `export_id` and `manifest_sha256`. Pass references directly as named `inputs` to `submit_analysis`, together with `schema_version: 1`, exact `method`, `parameters`, `limits` and audit fields. Inputs are verified against their producer index, files and source record, then copied/verified before method startup. Unknown aliases, damaged/deleted sources, unsupported schemas and escaping links fail explicitly. The Coordinator relays references; neither service calls another MCP server or starts simulations to obtain missing data.

Built-in parameters are explicit:

- `dataset_summary`: `source: {input, dataset}`, `coordinate`, `value_columns`. Returns row count/coordinate range and min/max with coordinates (first occurrence on ties) plus arithmetic row mean for each selected value. Rejects empty, nonfinite, malformed, duplicated or unordered coordinates. Means are unweighted sampled-row statistics.
- `aligned_comparison`: `reference: {input, dataset}`, ordered nonempty `candidates: [{key, input, dataset}, ...]`, `coordinate`, `value_columns`. Every candidate must match selected columns/units/normalization and exact increasing grid. It returns candidate minus reference, absolute difference, percentage difference and zero-reference flags, plus max-absolute difference and row RMSE. Percentages are null whenever the reference is zero, including zero/zero. Candidate keys and source identities are unique; any incompatibility fails the entire batch with the candidate identified. There is no interpolation, conversion or renormalization.
- `render_figures`: `input` names a saved numerical-result reference. Supply `formats` (PNG/SVG), `layout` (separate/panels/overlay), `share_x`/`share_y`, `curves` (original/difference/absolute/percentage), `x_scale`/`y_scale` (linear/log), `x_limits`/`y_limits` (empty for automatic), `columns`/`candidates` (empty for all), `labels` (optional overrides in an object), `legend`, and integer pixel `width`/`height`. Plots preserve sampled values/units/normalization/source identities; undefined percentages leave gaps. Log scales reject nonpositive selected values. Shared y axes require compatible units. Summary plots support original curves. Rendering is a separate job and cannot change numerical results; failure leaves those results intact.

Successful status includes an output reference with analysis/attempt IDs and manifest hash; add the configured producer `store_id` when relaying it as another job's input. `get_analysis_output` requires `analysis_id`, exact `attempt_id`, pinned `output_sha256` and `name`: use `summary`, `figures`, a table name, or `diagnostics`. Tables default to 100/max 1000 rows with offset pagination. Figure references include format/dimensions/size/checksum and a runtime-relative location. Diagnostics instead pin the attempt's `archive_sha256` and expose bounded, outcome-labelled logs, including failures; such references use `kind: "attempt"` and cannot be numerical inputs. Reads verify seals and never rerun computation. Historical inspection exposes current validity separately from original verification.

Canonical identity includes source identities/data/selection/parser, exact method bundle/components, parameters and environment. Audit fields and transport aliases do not affect numerical identity. Reproducible identical submissions with fresh keys share the current generation and its original budgets, including queued/running/failed outcomes; finished results must remain intact and eligible. Same-key replay recovers the same job ID. An explicit `recompute_of` pins the current identical generation and creates a linked successor; concurrent stale recomputation targets fail. Reads never recompute damaged outputs. Nonreproducible methods do not automatically share fresh-key work.

Assessments append immutable scoped revisions: `experimental`, `approved`, `deprecated` or `invalidated`. Scope is explicit `source_ids` (empty means all); unknown scope fields fail. Experimental verified execution remains labelled; deprecated exact selection warns. Applicable invalidation blocks new execution and ordinary reuse while preserving historical inspection. Approved status requires exact method/scope/decision, a human `evidence_id` included in supporting evidence, source and statement in `human_approval`; actor strings and test passes are insufficient. This local record system retains Coordinator-forwarded evidence and does not authenticate people. Promotions never silently migrate existing analyses to newer versions.

One independent `postprocessing.lock` supervises one Analysis/verification child per runtime, separately from SRIM workers. Ownership is durably recorded before method startup. Windows uses kill-on-close jobs; POSIX uses process groups and a parent-lifetime pipe. Timeout/cancellation terminates owned descendants; recovery refuses ambiguous living PIDs/groups without killing an unrelated process. Abruptly interrupted work requires explicit reconciliation and never reruns automatically. Windows behavior is regression-tested; macOS/Linux remain unverified. This is trusted execution, with no filesystem/network containment or hard CPU/RAM/disk quota.

Default bounds are 60 seconds per attempt, 180 cumulative seconds, and one attempt. Explicit requests may permit at most three manually requested attempts. Additional ceilings: 64 MiB/1,000,000 input rows/64 columns per dataset, 32 candidates plus reference, 256 MiB retained outputs, 2 MiB combined logs (truncation is marked while pipes drain), 32 figure files and 4096 pixels per dimension. Requests can lower service ceilings, not raise them; unsupported bounds fail. Accepted jobs/retries retain their settings/budgets across restart. Server configuration edits apply to new submissions after restart. Queue time is excluded; preparation/execution/validation count toward cumulative use. Retry/cancel cannot reset budgets. Archiving a consumer case does not cancel a shared job; explicit job cancellation affects all consumers.

Analysis stores `postprocessing.sqlite`, immutable `methods/<method_id>/<version>/` bundles and `analyses/<analysis_id>/attempts/<attempt_id>/` snapshots/logs/outcomes/seals under its configured runtime. Keep these together when transferring retained data; database store UUIDs persist. There is no deletion/reset API. On-demand inspection is available from PowerShell with `& .\.venv\Scripts\python.exe -B -m analysis --config configs/analysis.toml show <analysis_id>`; replace the ID placeholder with the returned job ID.

## Run management and stored data

Run commands return JSON on stdout and print the run ID to stderr. Exit codes are 0 for success, 1 for an unsuccessful/incomplete calculation and 2 for a handled input, configuration or storage error. A verified `CHECKED` calculation may carry a separate worker warning while retaining its result.

Each runtime executes one backend at a time. For concurrent work, copy the configuration into separate files and assign distinct runtime directories, such as `../runtime/workspace-1` and `../runtime/workspace-2`. Select a configuration before the subcommand:

~~~powershell
& $srim --config configs/second.toml run trim-request.json --capability trim
~~~

Both runtimes may use the same unchanged simulator installation. Each has its own database, lock and workspaces; a busy worker rejects another run.

To cancel from another PowerShell window, recreate `$srim` and use the active run ID:

~~~powershell
& $srim cancel $runId
~~~

Ctrl+C during a CLI run also requests cancellation. Recovery should be requested only after prior native processes have exited:

~~~powershell
& $srim recover
~~~

Recovery reconciles interrupted records and preserves available evidence; it does not rerun or resume a calculation. MCP calls are sequential, so cancellation during an active call requires a separate CLI process. Abrupt process-tree termination has a known gap between launching a native process and recording its PID; confirm process exit before recovery. Service/session-0 operation is unverified.

~~~text
runtime/
  metadata.sqlite
  workers/worker-1/
    .lock
    active_attempt.json
    sr_module/
    trim/
  runs/<run_id>/attempts/<attempt_id>/
~~~

Attempt archives retain requests, native inputs, available outputs, logs, provenance, parsed results and checksum manifests. Retrieval verifies archived files against stored analysis. Keep the runtime directory when retaining calculation evidence. The owner marker remains after completion and identifies the last workspace owner.

## Troubleshooting

| Symptom | Check |
|---|---|
| Python or command not found | Confirm Python 3.13+ is installed, the environment exists and package installation completed |
| Simulator files missing | Point `srim_directory` at the complete suite root |
| Configuration not found | Run from the clone directory or pass an absolute `--config` path before the subcommand |
| Worker busy or recovery required | Wait for active work; confirm native processes exited before recovery |
| Checksum mismatch | Preserve the affected run; use a fresh runtime after changing the simulator installation |
| Missing optional TRIM output | Enable the output and choose a compatible calculation mode |
| Native timeout | Inspect attempt logs and unexpected dialogs before adjusting the timeout |

## Development checks

Ordinary regression tests use captured fixtures and mocks:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:AGENTIC_SRIM_INTEGRATION = '0'
$env:AGENTIC_SRIM_TRIM_INTEGRATION = '0'
& .\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
& .\.venv\Scripts\python.exe -B -m unittest discover -s tests/analysis -v
~~~

Analysis tests run separately because their component directory shares the installed package name; they include real trusted Python children and optional rendering, without invoking simulators. Install the plotting extra for the complete rendering suite. To deliberately exercise the installed simulators, set both native gates to `1` for the test invocation and restore them to `0` afterward. Five native tests perform 26 calculations across backend modes, concurrent isolated runtimes and CLI/MCP interfaces. Tests remove disposable runtimes; inspect `runtime/test_workspaces` and remove it only if it is empty. Git attributes preserve fixture bytes.
