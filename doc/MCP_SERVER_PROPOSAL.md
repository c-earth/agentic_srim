# Two local MCP servers for agentic SRIM/TRIM

Status: approved and implemented, 2026-10-22. This document and its two contract appendices formed the complete proposal approved by the user. Original approved bytes are retained locally under `.cache/mcp-implementation/approved/`. Current usage and the complete server/tool table are in the [server guide](guides/mcp-servers.md).

## Decisions and recommendation

Provide two independent local stdio servers named **srim_mcp** and **project_mcp**. Put all MCP-specific code in new packages beneath `src/mcp/`, separate from the existing calculation, backend, and knowledge modules:

```text
src/mcp/
  __init__.py
  common/
    __init__.py
    contracts.py
    files.py
    jobs.py
    protocol.py
  srim_mcp/
    __init__.py
    __main__.py
    catalog.py
    operations.py
  project_mcp/
    __init__.py
    __main__.py
    catalog.py
    operations.py
```

The business APIs remain independently usable Python APIs. Neither server connects to the other server. An agent coordinates them using explicit simulation, job, case, analysis, and capability references. MCP code may import existing domain modules; existing domain modules must not import MCP code or require the SDK to remain usable.

Launch from the repository root with `-m src.mcp.srim_mcp` and `-m src.mcp.project_mcp`. Do not add `src/` to `PYTHONPATH`: ordinary `import mcp` must resolve the installed SDK, while project adapters are `src.mcp.*`.

Use the installed official Python SDK **mcp==2.3.0**, local JSON Schema validation, and the existing native APIs. This is an adapter and management proposal, not a new physics engine. No native execution, configuration change, server registration, source implementation, or scientific analysis algorithm is authorized by preparing this proposal.

Detailed contracts:

- [MCP API and implementation contract](MCP_API_CONTRACT_PROPOSAL.md): files, schemas, tool signatures, capability discovery, lifecycle, concurrency, errors, and verification.
- [Project management and self-improvement contract](PROJECT_MCP_PROPOSAL.md): cases, analysis records, reusable collections, evidence, and approval boundaries.

## Why two MCP servers

MCP is useful here because agents can discover named operations, their typed inputs, and supporting resources without writing new Python glue for each request. A Python API remains the implementation underneath; a separate MCP protocol layer exposes it to different local agent hosts.

| Server | Owns | Does not own |
| --- | --- | --- |
| srim_mcp | Scientific capability discovery; materials/resources/defaults; SR/TRIM validation, preparation and execution; calculation jobs; native records/results/checkpoints; native input search; verified worker cleanup | Cases, research questions, analysis narratives, reusable-workflow registration, skill modification |
| project_mcp | Cases; references to calculation jobs and terminal native evidence; analysis provenance/artifacts; reusable workflow/analysis/skill discovery and candidates; self-improvement evidence and proposal support | Native process launch/control, physical-result generation, native success/failure editing, automatic adoption of agent changes |

`project_mcp` can read a physics job snapshot through its on-disk reference. It cannot submit, cancel, retry, resume, or reclassify that physics job. Native run management belongs to `srim_mcp`; grouping runs into a study belongs to `project_mcp`.

## What agents must be able to discover

The capability catalog is a primary interface. Tools alone are insufficient: an agent also needs the parameter rules, units, installed engine identity, output meanings, and the distinction between accepted inputs and native-verified behavior. Every capability includes its callable interface or an explicit reason it is not exposed for execution.

### Scientific coverage

| Family | Capability and controls | Boundary to disclose |
| --- | --- | --- |
| SR stopping | Electronic/nuclear stopping at 1–1,000 requested energies; order and duplicates retained; projectile Z/mass; homogeneous elemental or compound target; density/phase; eight stopping-unit choices | SR unresolved and explicit-engine schemas differ; quantities use the native string conventions |
| SR range | Minimum/maximum energy; engine-generated energy grid; stopping, projected range, longitudinal and lateral straggling | No requested energy-grid step or arbitrary range-grid interpolation; endpoint/grid completeness is not guaranteed |
| Materials | Effective compound lookup by name/composition/ratio; direct compositions; natural or explicit masses where supported; local overrides and immutable versions | Compound indices depend on the selected effective database; names can repeat; chemistry/bonds are not inferred |
| Physics resources | Atomic, compound, electronic, nuclear, core, bond, and TRIM damage data; explicit default initialization; existing version/file/inline selection; immutable variants | Defaults never switch implicitly; imported coefficients and overrides retain their existing validation distinctions |
| CAB/Bragg | None/supplied/derived correction; explicit bond counts; verified cache reuse; selected default core/bond recovery; pinned Bragg dependencies | Missing default recovery can run native dependencies and visible GUI captures; bulk work can be expensive; no invented missing strengths |
| TRIM target | Ordered layers; thickness, density, solid/gas phase; component masses/isotopes; displacement, lattice-binding, surface-binding energies; per-layer CAB | Repeated native atom entries remain distinct; unknown native bounds remain unknown |
| TRIM modes | Ion quick/full/monolayer; specified-ion quick/full; recoil quick/full; 2013 recoil monolayer | Explicit version-dependent numeric mapping; syntax support, automatic-dialog support, and native evidence are separate fields |
| TRIM event controls | Structured event ID, atomic number, energy, position, direction cosines; enough events for requested count; inherited prefix on resume | No silent vector normalization, event relocation, reseeding, or fabricated events |
| TRIM controls | Absolute particle/event total, seed, autosave, angle, plot type/depth interval, optional range/backscatter/transmission/sputtering/collision/EXYZ outputs, reviewed cascade choice | Exact 90-degree input is rejected; automatic dialogue policies require a reviewed engine hash; optional output availability is conditional |
| Execution | Separate preparation and run; convenience fresh submission; explicit batches; worker selection, concurrency, finite SR timeout and nullable TRIM timeout | Preparation/resolution can already execute CAB dependencies; no automatic retries or implicit worker scaling |
| TRIM continuation | Child record from verified terminal parent; absolute final count and explicit autosave; optional appended events | Checkpoint-first completion; eligible failed parents only; no change to inherited physics; no GUI export/chooser branch |
| Results/evidence | Metadata, input/output inventories, parsed SR/native TRIM/recovered TRIM reports, paged tables, availability, raw-file references | Scientific quantities preserve strings/units; failed records can have evidence; parsing completeness differs from execution success |
| Frozen checkpoint recovery | In-memory/native preserved checkpoint recovery and labeled export; seven recovered profile families; provenance and inferred-layout warnings | Requires a complete frozen checkpoint; not native text precision/format, atomic-save proof, or independent physical validation |
| Search and retention | Existing exact native input search and explicit index maintenance; generation-pinned pagination; verified source reads; verified working-runtime cleanup | No output-value search, external importer, deletion of permanent evidence, or general garbage collection |

Mode mapping to publish verbatim from `src/srim/trim_inputs.py`:

| Descriptive mode | 2008 | 2013 Standard/Pro |
| --- | --- | --- |
| ion-quick | 1 | 1 |
| ion-full | 2 | 2 |
| ion-monolayer | 3 | 3 |
| specified-ion-quick | 4 | 4 |
| specified-ion-full | 5 | 5 |
| recoil-quick | 6 | 8 |
| recoil-full | 7 | 6 |
| recoil-monolayer | unavailable | 7 |

The catalog must report the measured coverage accurately. Existing core-output/recovery fixtures cover 23 version/mode combinations. That does not establish unattended execution for all combinations or validate their physics. The checkpoint-first continuation evidence covers the two measured modes and workloads described in [TRIM_DIRECTION.md](TRIM_DIRECTION.md), with separate optional-history limitations. No additional native experiments are part of this proposal-writing task.

### Knowledge depth without flooding the tool list

An initial `capabilities_list` returns compact cards. `capability_get` returns one complete capability with the request/result schemas, units, bounds, side effects, examples, qualifications, source/API references, and verification references. `schema_get` and `example_get` also work as tools because some hosts make resource-only content harder for agents to retrieve. Equivalent MCP resources provide host-driven discovery.

Capability status has independent axes: implementation, executable exposure, installed compatibility, automation policy, result availability, and evidence strength. A catalog entry cannot collapse these into a single misleading `supported=true`. Unknown physical/native bounds use null with a reason and source; configured request-size limits are labeled adapter limits.

Examples must cover SR ordered list with duplicates, SR range, direct/compound targets, correction choices, layered TRIM, specified events, damage overrides, fresh execution, continuation, batches, timeout/cancel limitations, checkpoint recovery, verified results, and exact input search. They are editable request templates, not scientific recommendations.

## Agent workflow

1. Discover the relevant capability and schema; inspect installed identity and resource/default readiness.
2. Create a case through `project_mcp` if the task needs a persistent research context.
3. Choose inputs explicitly. Validate without running dependencies. Resolve/prepare only after the agent understands their disclosed side effects.
4. Submit a calculation job through `srim_mcp`, attach its reference to the case, and poll bounded status calls. Do not hold an MCP call open for an entire native run.
5. Inspect terminal native status, completion checks, output availability, provenance, and warnings. Read only the required result sections/table pages.
6. Record an analysis plan and later attach the host-produced artifacts and method evidence through `project_mcp`. Initial reusable analytical collections are empty; this phase adds management, not unspecified scientific algorithms.
7. When meaningful repeated work or a substantial avoidable inefficiency appears, record evidence and draft an exact improvement proposal using the existing skill. Trial/adoption requires the existing human approvals.

## Self-improvement: what MCP contributes

I will use the existing [self-improvement skill](../.agents/skills/self-improvement/SKILL.md) and [protocol](../.agents/skills/self-improvement/references/protocol.md) as the authority for evidence, proposals, trials, restoration, and adoption. It does not require MCP to function. MCP makes the relevant records and reusable capabilities easier for agents to find and use.

The initial system supplies observation/proposal support and read-only trial-state inspection. It does not give an agent a tool that converts its own approval string into authorization, edits skills automatically, starts trials automatically, or adopts changes. The coordinating agent obtains actual user approval of a concrete revision, performs the authorized change, evaluates it, and records the result under the skill protocol. This remains an agent-followed process, not an autonomous optimizer or a mechanically enforced approval authority.

Keep scientific analysis methods distinct from improvements to agent skills/workflows. New scientific methods require agreed inputs, units, arithmetic, uncertainty treatment, availability rules, provenance, and validation. Registering a method description cannot establish scientific correctness.

Reusable workflow, analysis-method, and skill collections start empty. Existing repository skills and records are preserved. Listing those existing skills is an inventory operation, not automatic registration/adoption. Approved reusable collections can grow later through concrete additions under the existing approval process.

## Proposed delivery boundary

Deliver both servers in one implementation scope after approval, in reviewable stages:

1. Shared contracts, configuration, protocol handling, capability/schema/example discovery, verified file access, and dependency declaration.
2. `srim_mcp` read/validate operations, asynchronous mutations, preparation/execution/batches, lifecycle/status/cancel reporting, and result browsing over current APIs.
3. Separate Python case/analysis/collection APIs and the `project_mcp` adapter, plus evidence/proposal support and read-only improvement-state inspection.
4. Synthetic SDK-client integration and repository regressions; documentation and an example host registration fragment. Register the servers in the actual host only when explicitly requested; the example is sufficient to review this scope.

Do not add external imports/NERSC migration, a web interface, remote HTTP/authentication, a parameter-sweep generator, new physics/postprocessing algorithms, arbitrary Python/shell execution tools, permanent deletion, auto-retry, automatic study execution, or the abandoned F-button/export-chooser pathway. These boundaries do not hide existing Python capabilities: the catalog describes advanced APIs that remain Python-only and explains why.

## Proposed configuration and dependencies

Keep existing strict `settings.toml` unchanged. Add a separate `mcp.toml` with schema version 1 and exactly these tables/keys:

```toml
schema_version = 1

[srim_mcp]
max_parallel = 4
max_pending = 64
max_request_bytes = 16777216
max_response_bytes = 1048576
max_result_bytes = 67108864
file_chunk_bytes = 262144
allowed_input_roots = []

[project_mcp]
max_parallel = 1
max_pending = 64
max_request_bytes = 16777216
max_response_bytes = 1048576
max_result_bytes = 67108864
file_chunk_bytes = 262144
allowed_input_roots = []
```

These are proposed adapter defaults for approval, not SRIM physical limits. An empty input-root list adds no external directories; the repository root is always available. Nonempty entries must be existing absolute link-free directories and are read-only. Native installation reads use only the fixed installation configuration and existing domain APIs; a caller cannot use that exception for arbitrary file browsing. Exports are created under server-owned artifact storage and cannot overwrite arbitrary caller paths. File chunks are at most the configured value; JSON replies exceeding the cap return references/pagination rather than silent truncation. Oversized unpageable requests fail before mutation.

All integer configuration values are positive actual integers excluding bool; `max_parallel` is 1–32, `max_result_bytes` must be at least `max_response_bytes`, and `file_chunk_bytes` cannot exceed `max_response_bytes // 4`. The result-size limit bounds in-memory JSON parsing; larger result files remain available through streaming verified chunks. Unknown tables/keys, duplicate roots/case collisions, malformed TOML, invalid values, or unsafe roots fail startup. No environment-variable expansion or hidden configuration discovery. The explicit `--root` selects one workspace; both servers run with that root as working directory. Project-configured timezone applies to project records; elapsed durations use monotonic clocks. Existing knowledge reports retain their existing UTC convention.

Proposed `requirements-mcp.txt` exact contents:

```text
mcp==2.3.0
jsonschema==4.26.0
```

Both are already installed in this checkout. Declare JSON Schema as a direct dependency because the adapter will import it; do not rely on an accidental transitive dependency. Do not introduce SDK imports into existing APIs. Full transitive lockfile generation is not included; these two pins alone do not freeze every transitive package. CLI extras and a framework migration are unnecessary.

Example launch commands:

```powershell
D:\projects\agentic_srim\.venv\Scripts\python.exe -m src.mcp.srim_mcp --root D:\projects\agentic_srim
D:\projects\agentic_srim\.venv\Scripts\python.exe -m src.mcp.project_mcp --root D:\projects\agentic_srim
```

One process per server/workspace, enforced by separate process-held locks. The two server processes can coexist. All protocol output goes to stdout; diagnostic logging goes to stderr. Initialization reads configuration/catalogs but never imports defaults, starts an engine, backfills a catalog, or creates a case implicitly.

## Review and approval

The following are now resolved by the user: two servers; their names; local stdio; MCP-specific code grouped in `src/mcp/` outside existing API folders. This proposal supplies concrete recommendations for all remaining implementation choices, including the declared limits, asynchronous job behavior, SR cancellation limitation, case/analysis semantics, empty reusable registries, and evidence-only improvement tools.

Approval must cover all three proposal files as one revision. Any requested change will be incorporated before implementation; no unresolved choice will be silently selected during coding. No automatic Codex settings changes or native benchmark runs are required for approval.

## Evidence and sources

Repository baseline inspected: commit `66203fab5ccdb687ccb3a5ec1819d07db17c6eec`, clean tracked working tree before proposal creation. Current API source, `HANDOFF.md`, the active TRIM direction, and the documented deferred planning boundaries take precedence over stale historical statements. `src/case`, `src/analysis`, `src/plan`, and `src/memory` currently contain allocation/placeholders rather than implemented management workflows. The installed SDK's public signatures were inspected locally; do not substitute v1 FastMCP examples for the installed v2 API.

Primary external references checked 2026-10-22:

- [Official Python SDK](https://py.sdk.modelcontextprotocol.io/): tools, resources, local transports, typed schemas; implementation must match installed 2.3.0.
- [MCP tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools): named operations and input/output schemas. Let the SDK negotiate supported protocol versions rather than hand-implementing wire versions.
- [MCP resources](https://modelcontextprotocol.io/specification/2026-07-28/server/resources): URI-addressed contextual data and templates. Tools remain available for discovery where hosts do not expose resources conveniently.

The architecture recommendation, server split, lifecycle choices, limits, and project schemas are this project's proposed design, not requirements imposed by the protocol.
