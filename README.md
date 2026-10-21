# agentic_srim

Python APIs for reproducible SRIM stopping/range and layered TRIM calculations on Windows. The system resolves material and coefficient choices into pinned inputs, runs native SRIM executables in isolated workers, preserves calculation evidence, and makes saved simulation inputs searchable.

The current project is a Python library used from the repository root. There is no CLI application, packaged installer, model integration, or agent orchestration service. Case and postprocessing directories can be allocated, but their analysis workflows are not implemented.

## What the system does

| Capability | Current behavior |
| --- | --- |
| SR stopping | Electronic and nuclear stopping at a supplied energy list; requested order and duplicates are preserved. |
| SR range | Engine-generated energy grid with stopping, projected range, and straggling; no user-defined grid step. |
| Materials and coefficients | Compound search, direct compositions, local overrides, and immutable reusable resource versions. |
| Core-and-bond (CAB) correction | Derived compound correction, on-demand default-strength recovery, and reuse backed by verified evidence. |
| TRIM | Layered input resolution/export, fresh execution, checkpoint continuation into a new record, and prepared batches. |
| TRIM output | Summary and depth-profile parsing, saved-input comparisons, and preservation of complete stopped runtimes. |
| Input search | Exact structured filters over verified inputs of registered native SR/TRIM records, including failed runs. |

## Setup and first calculation

You need Windows, Python 3.13+, and an existing working SRIM installation with its numerical data and native runtime dependencies. Executables and installation data are not distributed here. Default CAB recovery additionally requires Windows PowerShell 5.1 and an interactive desktop for the SR GUI.

The current Python code uses only the standard library. There is no `pyproject.toml`, `setup.py`, or pip dependency installation step. Use a Python 3.13+ virtual environment at `.venv`; in this checkout, run all project Python commands through `D:\projects\agentic_srim\.venv\Scripts\python.exe`. Run commands from `D:\projects\agentic_srim` so `src` imports resolve.

Edit `settings.toml` to point to your installation:

```toml
[time]
timezone = "UTC"

[srim]
root = 'D:\local\srim\srim_2013_std'
hide_sr_window = true
hide_trim_window = true
```

The checked-in path is workstation-specific. TOML single-quoted strings preserve Windows backslashes; double them inside double-quoted strings. The configuration accepts only these tables and keys. `srim.root` must be absolute. Timezone defaults to `UTC` and also accepts `system` or fixed `UTC±HH:MM` offsets. Loading configuration checks syntax; importing defaults checks installation files.

Both window flags default to `true`. SR's flag covers main SRModule and Bragg dependency runs; CAB recovery uses a separate visible `SR.exe` GUI. TRIM's flag controls initial display, but native windows and dialogs may appear later. Reload configuration after editing it.

Run the following in a Python session using the required interpreter, or save it under `.cache/first-sr.py` and run it from the repository root:

```powershell
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' .\.cache\first-sr.py
```

```python
"""Calculate hydrogen stopping in carbon and print the preserved results."""

import json
from pathlib import Path

from src.backend.config import load_config
from src.backend.paths import create_base_directories, get_project_paths
from src.backend.records import CreateRequest, create_directories
from src.srim.defaults import switch_sr_defaults
from src.srim.execution import run_sr_simulation
from src.srim.runtime import prepare_sr_simulation

paths = get_project_paths(Path.cwd())
config = load_config(paths.root / "settings.toml")
create_base_directories(paths)
switch_sr_defaults(paths, config)

allocation = create_directories(
    paths, config, [CreateRequest("worker"), CreateRequest("srim")]
)
worker, simulation = allocation.completed
request = {
    "schema_version": 2,
    "parameters": {
        "projectile": {"atomic_number": 1, "mass_amu": "1.008"},
        "target": {
            "source": "direct",
            "phase": "solid",
            "density_g_cm3": "2.267",
            "elements": [{"atomic_number": 6, "stoichiometry": "1"}],
        },
        "correction": {"mode": "none", "applied_factor": "1.0"},
        "stopping_units": 7,
        "energy": {"mode": "list", "values_keV": ["126", "1000"]},
    },
}
selections = {
    role: {"version": "default", "overrides": [], "create_version": False}
    for role in ("atomdata", "electronic", "nuclear", "core", "bond")
}
selections["compound"] = None
runtime = prepare_sr_simulation(
    paths, config, simulation, worker, request, selections
)
metadata = run_sr_simulation(
    paths, config, simulation.path.name, runtime, timeout_seconds=60.0
)
print(simulation.path, metadata["status"])
if metadata["status"] != "succeeded":
    raise RuntimeError(metadata["failure"])
result = json.loads(
    (simulation.path / "output/parsed/result.json").read_text(encoding="utf-8")
)
print(result["stopping_units"], result["rows"])
```

This example disables CAB correction to avoid GUI recovery. Decimal quantities are supplied as strings, and parsed numerical spelling is preserved. All six selection keys are required even when correction is disabled. For range output, replace the energy object with `{"mode": "range", "minimum_keV": "1", "maximum_keV": "1000"}`. See the [SR walkthrough](doc/guides/getting-started.md) and [units/request reference](doc/reference/data-formats.md).

## How the system works

### 1. Import defaults and select resources

`switch_sr_defaults(paths, config)` imports installation data into `memory/resources` and publishes one coordinated default generation for atomic data, compounds, electronic/nuclear coefficients, cores, bonds, and Bragg data. Repeating it against the same effective installation reuses matching resources; it does not run the engine or recover all CAB entries. `read_sr_defaults` reads the existing generation without importing anything.

Resources are immutable `vN` versions with payload hashes and sizes. Version numbers are independent between resource kinds. The selector `default` resolves to concrete references during preparation; it is not a promise to use whatever defaults happen to be current when execution starts. New variants do not move default pointers.

Selections choose a version or source, explicit overrides, and `create_version`. With `False`, edits are local snapshots for one calculation; with `True`, effective data are also published as a reusable immutable version. Original sources, effective data, and provenance are retained. Successful publications survive a later preparation failure.

Installation import normalizes a small documented set of known source defects without editing the SRIM installation. Other malformed data are rejected. User imports remain strict. See [resources and variants](doc/guides/resources-and-cab.md).

### 2. Resolve material and physics inputs

A direct SR target supplies phase, density, and positive atomic proportions. A compound target selects a one-based record from the effective compound database. Search after applying compound overrides: record indices can change and differ between installations. Names can be duplicated, so names alone are not stable identifiers.

Target natural masses come from selected atomic data; an explicitly supplied projectile mass stays explicit. Mass-based compound records are converted to atomic proportions using effective natural masses. Nonzero bond counts on mass-based records are rejected because their atomic bond-count scale is unspecified. Density and mass overrides do not automatically recalculate other material properties.

SR correction can be disabled, supplied explicitly, or derived from CAB inputs. Direct derived targets require explicit bond counts; the system does not infer chemistry or valence.

### 3. Derive CAB correction and reuse dependencies

With an active bond of nonzero strength, the derived factor is:

```text
factor = (sum(atom_count × core_strength) + sum(bond_count × bond_strength))
         / sum(atom_count × elemental_electronic_stopping)
```

The denominator is calculated through a pinned hydrogen reference recipe using the effective atomic data and coefficients. Arithmetic uses local Decimal precision 28 with round-half-even. Common rescaling of both atom and bond counts preserves the factor. If no positive-count bond has nonzero strength, the factor is exactly `1.0`, and unnecessary core/Bragg recovery is skipped.

Missing active default strengths can trigger SRModule dependencies and serialized SR GUI captures. Hydrogen's default core convention is zero; non-H core recovery combines 18 one-bond estimates. Recovery retains its baseline separately from an overridden main calculation. User datasets have different missing-entry behavior: missing cores fall back to elemental Bragg stopping and missing bonds to zero.

Calculated entries are reused only when matching resources and linked evidence verify. Missing/corrupt evidence raises an error rather than silently regenerating it. `populate_default_cab` can recover a chosen subset; requesting every core is substantially more work. See [CAB recovery and retention](doc/guides/resources-and-cab.md).

### 4. Prepare, execute, and finalize

Allocation gives each simulation a permanent record and each worker an execution directory. SR preparation resolves inputs, stages the native runtime, and initializes metadata. It may run CAB dependencies, but the main calculation starts only through `run_sr_simulation`.

The main record progresses from `created` to `running` to `succeeded` or `failed`. Execution verifies staged inputs, launches an isolated native process, preserves available raw/runtime evidence, parses results, and finalizes inventories and metadata. Terminal records cannot be rerun in place; allocate a new simulation for another attempt.

Always inspect returned metadata. Expected operational failures return `status="failed"`; invalid caller state, worker contention, and filesystem/publication errors can raise. Failed preparation can leave dependencies and partial allocations. There is no automatic rollback or retry.

### 5. Prepare and run TRIM

TRIM shares SR's common resources and adds an independently selected damage table through `switch_trim_defaults`. Initialize both explicitly for a new workspace. `resolve_trim_inputs` resolves layers, isotope masses, density, CAB corrections, and displacement/lattice/surface binding energies into a pinned bundle. It does not allocate a main simulation or stage/start TRIM, although derived CAB dependencies can execute.

Component overrides take priority over damage defaults. Surface binding otherwise uses the selected atomic sublimation value when positive, or 2 eV when zero. These defaults describe input policy, not material-specific validation. Layers keep separate native atom entries even when the same element occurs in multiple layers.

Bundles can be exported and loaded without resolving aliases again. `prepare_trim_simulation` stages a fresh runtime from pinned bytes and verifies resource references; `run_trim_simulation` executes it. Success requires consistent parsed data and saved controls, not just process exit. Every detected interactive popup, including completion questions, is recorded without an automatic response; owned processes are stopped, evidence is preserved, and the attempt fails.

`prepare_trim_continuation` creates a new child from a complete verified checkpoint of an eligible terminal parent. Physics and materials remain inherited; `total_ions` is the absolute final count, not the additional count. A failed parent is eligible only for supported popup/timeout/cancellation endings with a complete checkpoint. Continuation does not establish bit-for-bit equivalence with an uninterrupted run.

The parser handles summaries, depth profiles, and textual checkpoint values. Full stopped runtimes preserve optional particle histories and binary arrays, but these are not interpreted by the current parser. Quick-mode aggregate recoil columns are not assigned invented per-element identities, and unavailable replacement-collision data are not treated as zeros. See [TRIM inputs](doc/guides/trim-inputs.md) and [execution, continuation, and parsing](doc/guides/trim-execution.md). Earlier input/setup guide statements that the TRIM runner or visibility flag is only planned are superseded by the current execution guide and implementation.

### 6. Search saved native inputs

`src.knowledge` maintains a local SQLite catalog under `memory/knowledge`. Newly finalized standalone runs register incrementally; prepared batches index finalized runs together. Both succeeded and failed records qualify if their inputs verify. Created/running records and CAB GUI captures are excluded. Indexing failures are reported separately and do not change native execution status.

`search_inputs` filters exact saved input quantities, target composition/layers, projectile, engine, and status. It does not search output values, invoke models, or use embeddings. Searches recheck source evidence; unknown values are not guessed. Pagination can pin a catalog generation, and file access verifies ownership, hashes, and sizes.

The catalog starts empty and does not discover historical runs automatically. `update_index` accepts explicitly selected IDs; `rebuild_index` replaces membership with an explicit nonempty ID list. External imports and NERSC migration remain deferred. See the [search guide](doc/guides/input-search.md).

## Concurrency and cleanup

Prepare tasks before dispatch. `run_sr_batch` accepts `PreparedSRTask` objects; `run_trim_batch` accepts `PreparedTRIMTask` objects. Both use an in-memory queue, preserve submission order in returned results, and serialize tasks sharing a worker while allowing different workers to execute concurrently. The worker lease also excludes competing SR/TRIM/CAB work. CAB GUI capture has a separate serialization lock. Shared metadata/resource operations use bounded locks.

SR batches clean verified working runtimes after the whole batch finishes and retain empty workers. TRIM batches retain working runtimes by default; request `cleanup=True` explicitly. Separate `cleanup_sr_batch` and `cleanup_trim_batch` APIs provide verified cleanup. Unknown, active, changed, unsafe, or unpreserved contents are retained and reported. Direct runs do not automatically delete working runtimes. Cleanup outcomes are separate from calculation success.

## Files, evidence, and retention

```text
settings.toml                 Installation, timezone, window preferences
src/backend/                  Configuration, paths, allocation, resource storage
src/srim/                     SR/CAB/TRIM inputs, resolution, execution, parsing
src/knowledge/                Native input catalog, filters, verified file access
doc/                          Guides, API/schema references, recorded investigations
test/{backend,srim,knowledge}/ Automated tests
memory/resources/             Immutable resource versions and calculated entries
memory/srim/<simulation_id>/   Permanent SR, TRIM, and CAB capture records
memory/knowledge/             Input-search catalog and update reports
worker/worker_N/               Isolated working runtimes
memory/post/ and case/         Allocatable directories; analysis not implemented
.cache/                       Auxiliary examples, exports, probes, and renders
```

Each permanent simulation record contains `metadata.json`, `input/` for requests/selections/effective inputs and provenance, and `output/` for raw evidence and parsed results. The stopped runtime is preserved under `output/raw/runtime/`. SR's parsed result is `output/parsed/result.json`; TRIM uses `output/parsed/results.json` plus `saved-values.json`. Available files depend on how far execution reached; preflight failures may have no parsed output.

Keep `memory/resources` and permanent dependency simulation records together when archiving. CAB GUI captures have permanent records with engine identity `sr-capture`. Older entries can still reference worker capture directories; preserve those too. Deleting linked evidence breaks cache verification. Generic deletion is not a reference-aware garbage collector, and there is no complete historical installation archive. Use a persistent destination instead of `.cache` for exports you need to retain.

## Verification and important limits

Run all three test directories explicitly; top-level discovery alone does not discover these namespace-directory tests:

```powershell
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' -W error -m unittest discover -s test/backend -p 'test_*.py'
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' -W error -m unittest discover -s test/srim -p 'test_*.py'
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' -W error -m unittest discover -s test/knowledge -p 'test_*.py'
```

Tests use synthetic installations and controlled native-process substitutes; passing tests do not establish universal numerical accuracy or SRIM compatibility. Recorded native checks cover representative installations and cases, not every element, mode, or checkpoint scenario. Interactive dialogs and event-mode limitations can prevent unattended completion. Hash verification proves byte identity and provenance, not physical correctness or atomic capture of a live external process.

Known non-TRIM contract discrepancies remain in the [review](doc/reviews/non-trim-review.md), including direct default-recovery restrictions and preparation progress reporting. Do not rely on progress callbacks always reaching their declared total. Consult the current guides and source when historical proposals describe planned behavior.

## Further documentation

| Need | Read |
| --- | --- |
| Browse all documentation | [Documentation index](doc/README.md) |
| SR examples and batches | [Getting started](doc/guides/getting-started.md) |
| Compound search, resource variants, CAB and retention | [Materials, resources and CAB](doc/guides/resources-and-cab.md) |
| Function signatures and error behavior | [API reference](doc/reference/api.md) |
| SR schemas, units and stored layouts | [Data formats](doc/reference/data-formats.md) |
| TRIM input schemas and damage defaults | [Input format](doc/reference/trim-input-format.md), [default policy](doc/reference/trim-input-defaults.md) |
| TRIM execution and continuation contracts | [Execution reference](doc/reference/trim-execution-format.md) |
| Search schemas, filters and references | [Input-search reference](doc/reference/input-search-format.md) |
| Recorded native CAB validation | [CAB status and evidence](doc/CAB_HANDOFF.md) |
