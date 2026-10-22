# Execute TRIM and inspect summary/profile data

[Documentation index](../README.md) · [Input preparation](trim-inputs.md) · [Execution reference](../reference/trim-execution-format.md)

TRIM execution starts from a resolved, pinned input bundle. Preparing inputs and staging a runtime do not launch the main calculation. The runner uses an isolated worker directory, records status and diagnostics, preserves the entire stopped runtime, and parses summary/depth-profile data before publishing terminal metadata.

Use the same project and resource store as the [input guide](trim-inputs.md). The exported bundle can be loaded and independently replayed without an installation lookup. Staging and lifecycle initialization also verify immutable resource references in the project store. They do not resolve current aliases again or replace pinned executable/coefficient bytes with current installation files.

## Run a prepared input bundle

This example consumes the input guide's `.cache/trim-example-input` export. It allocates a fresh simulation and worker. The exported bundle remains unchanged.

```python
"""Stage an exported TRIM bundle and run it with preserved evidence."""

import json
from pathlib import Path
from src.backend.config import load_config
from src.backend.paths import get_project_paths
from src.backend.records import CreateRequest, create_directories
from src.srim.trim_runtime import load_trim_input_bundle, prepare_trim_simulation
from src.srim.trim_execution import run_trim_simulation

paths = get_project_paths(Path.cwd())
config = load_config(paths.root / "settings.toml")
bundle = load_trim_input_bundle(paths.root / ".cache/trim-example-input")
worker = create_directories(paths, config, [CreateRequest("worker")]).completed[0]
created = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
runtime = prepare_trim_simulation(paths, config, created, worker, bundle)
metadata = run_trim_simulation(
    paths, config, created.path.name, runtime, timeout_seconds=90.0
)
results = json.loads((created.path / "output/parsed/results.json").read_bytes())
execution = json.loads((created.path / "output/raw/execution.json").read_bytes())
print(metadata["status"], metadata["failure"])
print(results["issues"])
```

Inspect `metadata["status"]`: expected native failures return `failed` metadata. A clean exit alone is insufficient. Success also requires consistent summary/profile data, the requested saved ion count and no detected parameter substitution. Some preflight failures have only execution/raw evidence, so read parsed files only when present.

`hide_trim_window` controls initial display, independently of SR's setting. TRIM can display windows later. With the default cascade_output=None, every detected interactive popup is recorded without a response; the runner terminates its owned process job, preserves the runtime and returns a popup failure. Fresh runs may explicitly opt into reviewed button responses through cascade_output, subject to the [execution reference](../reference/trim-execution-format.md). This includes native completion questions. Popup text, classes, ownership and capture errors are in `execution["process"]["popups"]`. Startup progress forms and the ordinary calculation window are distinguished from interactive dialogs. No parameter is silently changed and no failed run is retried automatically.

## Read preserved data

`output/parsed/results.json` contains `summary`, `profiles`, optional textual `checkpoint`, exact source hashes, `issues` and consistency checks. `output/parsed/saved-values.json` records requested-versus-saved comparisons and fields that remain unverified. Numerical values preserve printed strings and units; scientific quantities are not converted to binary floats.

| Profile | Structured data |
| --- | --- |
| RANGE.txt | Ion and target-entry recoil depth distributions |
| VACANCY.txt | Knock-on/vacancy contributions by depth |
| IONIZ.txt | Ion/recoil ionization energy by depth |
| PHONON.txt | Printed ion/recoil phonon quantities by depth |
| E2RECOIL.txt | Energy transferred to recoils by depth |
| NOVAC.txt | Replacement-collision profile when calculated |
| LATERAL.txt | Projected/radial lateral ranges and stragglings versus depth |

Native accuracy notes remain in each profile. Repeated target species are kept as positional entries rather than merged by symbol. Some monolayer E2RECOIL files omit a heading for a printed numerical column; the parser retains the values and records the missing-label limitation.

Kinchin-Pease quick-mode RANGE/VACANCY/E2RECOIL tables contain aggregate recoil quantities. Their combined columns have no atom-entry identity; the parser does not invent separate values for each element. Full-cascade per-entry columns retain their native order, including repeated species in different layers.

Quick-mode NOVAC may explicitly state that replacement collisions were not calculated. That verified native form is represented as unavailable: empty rows/columns, `calculated_ions=null` and the native explanation in notes. It is never converted into zero replacements. A request or checkpoint identifying a full-cascade mode still requires a numeric NOVAC table.

TRIM writes files in the working root, `SRIM Outputs` and `SRIM Restore`. All locations, including generated data files, optional particle histories, binary arrays and empty directories, are preserved under `output/raw/runtime`. The first parser does not interpret individual collision/particle/EXYZ histories or binary accumulated arrays.

## Resume into a new simulation

Continuation currently requires `cascade_output=None` (the default), retaining report-only popup behavior. Explicit automatic choices raise ValueError before creating a runtime: automatic continuation awaits checkpoint `.sav` result/lifecycle integration. The GUI result-export route has been removed. [Checkpoint recovery](trim-checkpoint-recovery.md) exists separately; recovered data is not currently substituted for required native text outputs or used to waive execution completion checks. See the [current direction](../TRIM_DIRECTION.md).

Resume accepts a sealed terminal project record. Successful parents are eligible; failed parents must have ended through popup, timeout or cancellation and have a complete verified saved-state set. A missing/inconsistent checkpoint is rejected. The parent is never edited.

```python
"""Prepare a new continuation record from an eligible preserved parent."""

from src.srim.trim_runtime import prepare_trim_continuation

child = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
resume_worker = create_directories(paths, config, [CreateRequest("worker")]).completed[0]
resumed_runtime = prepare_trim_continuation(
    paths, config, child, resume_worker, created.path.name,
    total_ions=2000, autosave_ions=1000,
)
resumed_metadata = run_trim_simulation(
    paths, config, child.path.name, resumed_runtime, timeout_seconds=90.0
)
```

`total_ions` is an absolute final count. A checkpoint at 1,000 with a target of 2,000 requests 1,000 additional ions. Both total and autosave are explicit. Layers, materials, projectile and physics settings remain inherited. The copied checkpoint's count/autosave edits and hashes are recorded in `input/execution/control.json`; the original resolved request stays intact.

TRIM saves target composition as fractions. A requested Si:O ratio of `1:2` is checked against saved fractions of about `0.333333:0.666667`, within printed precision. Saved-value checks name this derived quantity `stoichiometry_fraction`; the original proportions and serialized input bytes stay preserved.

Event-driven continuation additionally requires `events: list[JSONObject]`: the complete original ordered event list followed by appended records. Changed, removed or reordered earlier records are rejected. Native event-count interpretation is verified during execution; the runner never replaces the requested total with the event-list length.

Resume uses the complete preserved save set, not TDATA.sav alone. File/hash and textual identity checks do not establish that TRIM wrote every binary array atomically at one instant, or prove numerical equivalence with an uninterrupted calculation. A native save/increase-count dialog after reaching the target is still a reported popup failure; progress and clean unattended completion remain separate facts.

## Schedule prepared simulations

Prepare every task before dispatch. Fresh and resumed tasks may be mixed, but a continuation's parent must already be terminal; this queue does not schedule unresolved dependencies.

```python
"""Run two independently allocated tasks using the same pinned input bundle."""

from src.srim.trim_queue import PreparedTRIMTask, run_trim_batch

tasks = []
for index in range(2):
    allocation = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
    batch_worker = create_directories(paths, config, [CreateRequest("worker")]).completed[0]
    staged = prepare_trim_simulation(paths, config, allocation, batch_worker, bundle)
    tasks.append(PreparedTRIMTask(allocation.path.name, staged))
batch = run_trim_batch(paths, config, tasks, max_workers=2, timeout_seconds=90.0)
for outcome in batch.task_results:
    print(outcome.task.simulation_id, outcome.metadata, outcome.error)
```

Results retain input order. Tasks on one worker run in submission order; different workers may run concurrently. The shared worker lease also excludes simultaneous SR/CAB work. An ordinary failure does not stop independent tasks. Interrupting a batch stops dispatch, signals active owned jobs to cancel, waits for their evidence preservation, retains workers and re-raises the interruption. The queue exists only in memory.

Working runtimes remain by default. `cleanup=True` explicitly requests deletion of verified terminal working copies after the batch; permanent records remain. Cleanup retains unknown, active, changed, unpreserved or unsafe data and reports each outcome. `cleanup_trim_batch(paths, worker_paths)` provides the same explicit verified cleanup separately.

## Parse existing outputs without execution

```python
"""Freeze and parse an existing stopped native runtime into a new export."""

from src.srim.trim_storage import parse_trim_directory

destination = paths.root / ".cache/trim-parsed-export"
parse_trim_directory(
    created.path / "output/raw/runtime", destination,
    request=bundle.effective_request,
)
imported = json.loads((destination / "parsed/results.json").read_bytes())
print(imported["complete"], imported["issues"])
```

The source must be stopped/quiescent; before/after hash verification is not an atomic snapshot of a live external process. Destination must be new, with an existing safe parent, and must not overlap the source. Every source file is copied, while only summary/profile/textual checkpoint files are parsed. Native parse errors are stored in the report; I/O, unsafe paths, collisions and a changing source raise while retaining staging evidence.

Omit `request` when provenance is unknown. The report then states that no request was checked and does not invent a simulation identity or execution success. `complete` describes parsing consistency, not the historical success of an imported run. Use a persistent export destination when evidence must survive cache cleanup.

## Optional execution deadlines

Single runs accept `timeout_seconds=None` to disable the overall execution deadline. The default remains 60 seconds; positive finite seconds set a deadline. Cancellation and monitoring remain active with unlimited execution.

Batches support separate settings for each participating worker:

```python
batch = run_trim_batch(
    paths, config, tasks,
    max_workers=3,
    timeout_seconds=60.0,
    worker_timeouts={
        "worker_1": 300.0,
        "worker_2": None,
        "worker_3": 900.0,
    },
)
```

Use the actual worker directory names associated with the prepared tasks. Unknown names and invalid timeout values are rejected before launch. Unlisted workers use the batch default; `timeout_seconds=None` makes that default unlimited. Each task starts a fresh clock when its native attempt starts, including successive tasks on the same worker. Queue waiting, preservation and parsing do not consume that budget.

A timed-out task is stopped and its runtime preserved. Other workers and subsequent queued tasks continue; retry or checkpoint continuation requires a separate request. Unlimited runs remain blocking until they finish or are stopped. Execution evidence records unlimited as JSON `null`. Existing cleanup and owned-process safety rules still apply.
