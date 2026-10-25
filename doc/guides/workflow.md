# How calculations and records work

[Documentation index](../README.md) · [First SR calculation](getting-started.md) · [Validation and limits](../reference/validation.md)

## 1. Import defaults and select resources

`switch_sr_defaults(paths, config)` imports installation data into `memory/resources` and publishes one coordinated default generation for atomic data, compounds, electronic/nuclear coefficients, cores, bonds, and Bragg data. Repeating it against the same effective installation reuses matching resources; it does not run the engine or recover all CAB entries. `read_sr_defaults` reads the existing generation without importing anything.

Resources are immutable `vN` versions with payload hashes and sizes. Version numbers are independent between resource kinds. The selector `default` resolves to concrete references during preparation; it is not a promise to use whatever defaults happen to be current when execution starts. New variants do not move default pointers.

Selections choose a version or source, explicit overrides, and `create_version`. With `False`, edits are local snapshots for one calculation; with `True`, effective data are also published as a reusable immutable version. Original sources, effective data, and provenance are retained. Successful publications survive a later preparation failure.

Installation import normalizes a small documented set of known source defects without editing the SRIM installation. Other malformed data are rejected. User imports remain strict. See [resources and variants](resources-and-cab.md).

## 2. Resolve material and physics inputs

A direct SR target supplies phase, density, and positive atomic proportions. A compound target selects a one-based record from the effective compound database. Search after applying compound overrides: record indices can change and differ between installations. Names can be duplicated, so names alone are not stable identifiers.

Target natural masses come from selected atomic data; an explicitly supplied projectile mass stays explicit. Mass-based compound records are converted to atomic proportions using effective natural masses. Nonzero bond counts on mass-based records are rejected because their atomic bond-count scale is unspecified. Density and mass overrides do not automatically recalculate other material properties.

SR correction can be disabled, supplied explicitly, or derived from CAB inputs. Direct derived targets require explicit bond counts; the system does not infer chemistry or valence.

## 3. Derive CAB correction and reuse dependencies

With an active bond of nonzero strength, the derived factor is:

```text
factor = (sum(atom_count × core_strength) + sum(bond_count × bond_strength))
         / sum(atom_count × elemental_electronic_stopping)
```

The denominator is calculated through a pinned hydrogen reference recipe using the effective atomic data and coefficients. Arithmetic uses local Decimal precision 28 with round-half-even. Common rescaling of both atom and bond counts preserves the factor. If no positive-count bond has nonzero strength, the factor is exactly `1.0`, and unnecessary core/Bragg recovery is skipped.

Missing active default strengths can trigger SRModule dependencies and serialized SR GUI captures. Hydrogen's default core convention is zero; non-H core recovery combines 18 one-bond estimates. Recovery retains its baseline separately from an overridden main calculation. User datasets have different missing-entry behavior: missing cores fall back to elemental Bragg stopping and missing bonds to zero.

Calculated entries are reused only when matching resources and linked evidence verify. Missing/corrupt evidence raises an error rather than silently regenerating it. `populate_default_cab` can recover a chosen subset; requesting every core is substantially more work. See [CAB recovery and retention](resources-and-cab.md).

## 4. Prepare, execute, and finalize

Allocation gives each simulation a permanent record and each worker an execution directory. SR preparation resolves inputs, stages the native runtime, and initializes metadata. It may run CAB dependencies, but the main calculation starts only through `run_sr_simulation`.

The main record progresses from `created` to `running` to `succeeded` or `failed`. Execution verifies staged inputs, launches an isolated native process, preserves available raw/runtime evidence, parses results, and finalizes inventories and metadata. Terminal records cannot be rerun in place; allocate a new simulation for another attempt.

Always inspect returned metadata. Expected operational failures return `status="failed"`; invalid caller state, worker contention, and filesystem/publication errors can raise. Failed preparation can leave dependencies and partial allocations. There is no automatic rollback or retry.

## 5. Prepare and run TRIM

TRIM shares SR's common resources and adds an independently selected damage table through `switch_trim_defaults`. Initialize both explicitly for a new workspace. `resolve_trim_inputs` resolves layers, isotope masses, density, CAB corrections, and displacement/lattice/surface binding energies into a pinned bundle. It does not allocate a main simulation or stage/start TRIM, although derived CAB dependencies can execute.

Component overrides take priority over damage defaults. Surface binding otherwise uses the selected atomic sublimation value when positive, or 2 eV when zero. These defaults describe input policy, not material-specific validation. Layers keep separate native atom entries even when the same element occurs in multiple layers.

Bundles can be exported and loaded without resolving aliases again. `prepare_trim_simulation` stages a fresh runtime from pinned bytes and verifies resource references; `run_trim_simulation` executes it. Success requires consistent parsed data and saved controls, not just process exit. With the default `cascade_output=None`, detected interactive popups, including completion questions, are recorded without a response; owned processes are stopped, evidence is preserved, and the attempt fails. Explicit `cascade_output` policies enable only the reviewed responses described in the [execution guide](trim-execution.md).

`prepare_trim_continuation` creates a new child from a complete verified checkpoint of an eligible terminal parent. Physics and materials remain inherited; `total_ions` is the absolute final count, not the additional count. A failed parent is eligible only for supported popup/timeout/cancellation endings with a complete checkpoint. Continuation does not establish bit-for-bit equivalence with an uninterrupted run.

Resumed execution records validated native colour/help-text rewrites in `output/parsed/config-integrity.json`, while protecting the secondary-recoil limit and label size. Other input changes remain failures. Automatic continuation sends no completion or Save response when a stable final checkpoint is ready. If unavailable after ten seconds, ordinary continuation may save once; event continuation fails with `checkpoint-required` and requires a final-covering autosave schedule. The observed 2008 event Yes/No prompt is inspection-only. New automated policies and resumed reports use version 4; see [current direction](../decisions/trim-continuation.md).

The native parser handles summaries, depth profiles, and textual checkpoint values. The resumed pipeline additionally recovers depth/lateral profiles from complete saved arrays, retaining source hashes and precision warnings. Full stopped runtimes preserve optional particle histories and all remaining arrays. Quick-mode aggregate recoil columns are not assigned invented per-element identities, and unavailable replacement-collision data are not treated as zeros. See [TRIM inputs](trim-inputs.md) and [execution, continuation, and parsing](trim-execution.md).

Core TRIM results and optional collision histories have different verification status. An isolated, UI-assisted investigation completed 35 runs across all 23 supported version/mode combinations in 2008, 2013 Standard and 2013 Pro, each saving 100/100 particles or events. The tested modes produced their summary and seven profile-file families; quick-mode replacement data can explicitly be unavailable, as described above. This establishes operational core-output coverage for the tested workload, not independent physics validity or complete coverage of every optional output.

Optional detailed recoil histories remain limited: 2008 full recoil (input mode 7) produced additional recoil-detail tables when requested. In 2013 Standard/Pro full recoil (input mode 6), cascade statistics were produced, but the reviewed popup path did not offer detailed histories and instead displayed a misleading KP-damage message. In 2013 quick recoil (input mode 8), the detail choice was offered but Yes/No produced identical files for the tested 100-event workload. The possible connection to mode renumbering is unconfirmed. These UI-assisted checks did not add production response handlers; report-only execution still reports interactive popups as failures. See [core results versus optional histories](trim-execution.md#core-results-versus-optional-collision-histories) for the matrix and qualifications.

## 6. Search saved native inputs

`src.knowledge` maintains a local SQLite catalog under `memory/knowledge`. Newly finalized standalone runs register incrementally; prepared batches index finalized runs together. Both succeeded and failed records qualify if their inputs verify. Created/running records and CAB GUI captures are excluded. Indexing failures are reported separately and do not change native execution status.

`search_inputs` filters exact saved input quantities, target composition/layers, projectile, engine, and status. It does not search output values, invoke models, or use embeddings. Searches recheck source evidence; unknown values are not guessed. Pagination can pin a catalog generation, and file access verifies ownership, hashes, and sizes.

The catalog starts empty and does not discover historical runs automatically. `update_index` accepts explicitly selected IDs; `rebuild_index` replaces membership with an explicit nonempty ID list. External imports and NERSC migration remain deferred. See the [search guide](input-search.md).

## Concurrency and cleanup

Prepare tasks before dispatch. `run_sr_batch` accepts `PreparedSRTask` objects; `run_trim_batch` accepts `PreparedTRIMTask` objects. Both use an in-memory queue, preserve submission order in returned results, and serialize tasks sharing a worker while allowing different workers to execute concurrently. The worker lease also excludes competing SR/TRIM/CAB work. CAB GUI capture has a separate serialization lock. Shared metadata/resource operations use bounded locks.

Batch concurrency limits simultaneous execution, not the number of worker directories. Adapter resolution and preparation calls allocate a new worker when `worker_id` is omitted; passing an eligible worker ID enables reuse. Keep a task-owned pool sized to the requested concurrency across batches, and reuse its IDs explicitly. Retained runtime evidence or unresolved ownership can block reuse and must be reported. Both cleanup APIs retain worker allocation directories, so directory counts alone do not establish concurrency or wasted runtime storage.

SR batches clean verified working runtimes after the whole batch finishes and retain empty workers. TRIM batches retain working runtimes by default; request `cleanup=True` explicitly. Separate `cleanup_sr_batch` and `cleanup_trim_batch` APIs provide verified cleanup. Unknown, active, changed, unsafe, or unpreserved contents are retained and reported. Direct runs do not automatically delete working runtimes. Cleanup outcomes are separate from calculation success.

Execution failures attempt [failure finalization](../reference/api.md#low-level-simulation-lifecycle) independently of evidence-preservation failures. New SR/TRIM executions preserve launch intent and proven native shutdown in `lifecycle/launch.json` and `lifecycle/stopped.json` beside permanent metadata. Scientific MCP startup and graceful shutdown reconcile proven-stopped runs linked to terminal or interrupted jobs and preserve recovery reports while retaining worker quarantine. Call [run_closeout and run_reconcile](../reference/mcp-format.md#run-closeout-and-reconciliation) with all task/dependency simulation IDs for an executable check or explicit recovery. Every running or invalid scoped record remains visible. A force-killed native attempt without durable stop proof stays unresolved; an interrupted job, old timestamp or released lock does not prove process death. Unstarted preparations remain separate from started runs.

## Files, evidence, and retention

```text
settings.toml                 Installation, timezone, window preferences
src/backend/                  Configuration, paths, allocation, resource storage
src/srim/common/              Shared types, verified files and simulation records
src/srim/resources/           Materials, coefficients, defaults and shared resolution
src/srim/cab/                 Strengths, cache, Bragg dependencies and capture/recovery
src/srim/sr/                  SR inputs, resolution, execution, parsing and storage
src/srim/trim/                TRIM inputs, execution, outputs and checkpoint recovery
src/knowledge/                Native input catalog, filters, verified file access
doc/                          Guides, API/schema references, recorded investigations
test/{backend,srim,knowledge,case,analysis,plan,mcp}/ Automated tests
memory/resources/             Immutable resource versions and calculated entries
memory/srim/<simulation_id>/   Permanent SR, TRIM, and CAB capture records
memory/knowledge/             Input-search catalog and update reports
worker/worker_N/               Isolated working runtimes
memory/post/ and case/         Revisioned analysis provenance and research-case records
src/mcp/                      Two separate adapters and shared protocol/job infrastructure
.cache/                       Auxiliary examples, exports, probes, and renders
```

The contents of `memory/`, `case/` and `worker/` are local runtime state and are
Git-ignored, except for `.gitkeep` directory placeholders. This includes imported
and calculated resources, simulation and analysis records, input-search catalogs,
reusable capability and candidate snapshots, MCP journals and artifacts, and
worker runtimes. New checkouts do not receive these records from Git. Preserve
them through separately verified copies or archives; Git exclusion provides no
backup and does not authorize deletion. Source, packaged defaults, test fixtures,
published reference assets and shared project configuration remain versioned in
their documented locations.

New execution lifecycle evidence belongs in `memory/srim/<simulation_id>/lifecycle/`; preserve that directory with its record even though it is outside sealed input/output inventories. Legacy records may have no stop proof and are not automatically recovered.

Each permanent simulation record contains `metadata.json`, `input/` for requests/selections/effective inputs and provenance, and `output/` for raw evidence and parsed results. The stopped runtime is preserved under `output/raw/runtime/`. SR's parsed result is `output/parsed/result.json`; TRIM uses `output/parsed/results.json` plus `saved-values.json`. Available files depend on how far execution reached; preflight failures may have no parsed output.

Keep `memory/resources` and permanent dependency simulation records together when archiving. CAB GUI captures have permanent records with engine identity `sr-capture`. Older entries can still reference worker capture directories; preserve those too. Deleting linked evidence breaks cache verification. Generic deletion is not a reference-aware garbage collector, and there is no complete historical installation archive. Use a persistent destination instead of `.cache` for exports you need to retain.


Agent workspace roles and maintenance procedures are defined in [AGENTS.md](../../AGENTS.md) and `instructions/`. Persistent local figure, improvement and migration records live under `.local/`; preserve them during cache cleanup.
