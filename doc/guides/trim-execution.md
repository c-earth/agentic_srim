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

Inspect `metadata["status"]`: expected native failures return `failed` metadata. A clean exit alone is insufficient. Fresh success requires consistent native summary/profile data; resumed success requires complete recovered checkpoint data. Both require the requested saved ion count and no detected parameter substitution. Some preflight failures have only execution/raw evidence, so read parsed files only when present.

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

## Core results versus optional collision histories

The popup investigation completed 35 isolated, UI-assisted runs covering all 23 supported version/mode combinations: 2008 input modes 1-7 and 2013 Standard/Pro input modes 1-8. Every run exited with code 0 and saved Current=Total=100. Each tested mode produced its core summary and the seven profile-file families. Quick-mode estimates do not supply full recoil trajectories, and NOVAC can explicitly report replacement data as not calculated; an unavailable quantity is not a zero result. These checks establish core-output availability for the tested workload, not independent physics validity or every possible material, energy, mode/output combination.

Core damage/profile results do not require optional individual collision histories. A completed core calculation must not be described as providing detailed histories when those histories were not produced:

| Version and input mode | Core result | Optional detailed recoil history through the reviewed popup path |
| --- | --- | --- |
| 2008 mode 6: quick recoil | Summary and quick-damage profiles produced | No detail choice; native message says details are unavailable with KP damage. Full recoil trajectories are outside the quick approximation. |
| 2008 mode 7: full recoil | Summary and full-cascade profiles produced | Available: choosing every collision adds recoil energy/position tables; 409,452 bytes versus 137,036 bytes for cascade statistics in this workload. |
| 2013 Standard/Pro mode 6: full recoil | Summary, full-cascade profiles and requested cascade statistics produced | Gap: no detail choice was offered. The native message says "No cascade details available with KP Damage" despite the established full-recoil mapping. |
| 2013 Standard/Pro mode 8: quick recoil | Summary and quick-damage profiles produced | The detail question appeared, but Yes/No produced byte-identical 137,264-byte files. This does not establish detailed recoil histories. |
| Other supported modes | Core summary/profile files produced | Cascade questions were not offered with the tested optional-output flags set to zero; these choice branches were inapplicable. |

For each tested pair of Yes-branch runs, the seven profile files and TDATA.txt were byte-identical. Selecting No at the first cascade-file question produced no COLLISON.txt; selecting Yes produced a nonempty file. In modes without a detail choice, the second planned branch was a repeat of the same available path, not a successful request for an unavailable option. The 2013 full-recoil history gap is limited to this reviewed path; alternative collision-output controls were not investigated here.

The 2013 popup inconsistency may reflect an incomplete update after input modes were renumbered (2008 quick/full 6/7; 2013 quick/full/monolayer 8/6/7). That is an inference, not a source-code diagnosis, and must not be used to relabel the actual calculation. The 2013 full-recoil input code 6 saved runtime code 7, consistent with the established mapping.

The tests used a 1000-Angstrom silicon layer, seed 12345 and 100 particles/events: 50 keV argon for ordinary/specified-ion modes and 1 keV silicon recoils for recoil modes. Event inputs began 100 Angstrom deep with direction (1,0,0). Full settings, hashes, comparison results and links to each independent environment are retained locally in `.cache/popup-review/mode-version-matrix/README.md`. This Git-ignored evidence is absent from fresh checkouts and may be removed; the conclusions and limitations above are retained in this guide.

These investigations used explicitly authorized UI responses. The experiment itself did not implement or approve a production popup-response handler. Report-only execution stops its owned job and reports failed/popup when an interactive dialog is detected; UI-assisted core-output coverage does not establish unattended completion. Later fresh opt-in handling and checkpoint continuation integration are separate implementation behavior.

## Resume into a new simulation

Continuation defaults to `cascade_output=None`, retaining report-only popup behavior. Explicit `"none"`, `"statistics"` or `"detailed"` enables the reviewed responses subject to existing mode/engine/collision restrictions. New automated policies use nested schema 4; prepare a new runtime for older automated policies. Historical records are not rewritten.

Every resumed run selects [checkpoint recovery](trim-checkpoint-recovery.md) explicitly. `output/parsed/results.json` is schema 2 with `result_source="checkpoint-recovery"`, unchanged recovery-v2 data in `recovery`, and native-text diagnostics in `native`. Results are cumulative through the child's absolute total. Missing or stale native profiles do not invalidate checkpoint results. Requested collision histories remain a separate output requirement.

Automatic ordinary and event continuation first inspects the exact completion prompt without responding. Two valid final checkpoint snapshots with identical hashes at least one second apart permit an owned-job stop with `origin="completed-checkpoint"`. Initial inspection is bounded at ten seconds. Only an unavailable ordinary checkpoint permits one verified Yes/default-save fallback, recorded as `origin="explicit-save"`. Event continuation with no final checkpoint fails with `checkpoint-required`; explicitly select an autosave schedule covering the final count before preparing a new attempt. Invalid or unstable checkpoints never authorize saving as a repair. Fresh completion and report-only handling remain unchanged.

The ordinary save-form wait, default-save transition, post-save readiness and job shutdown each have separate ten-second bounds; the Yes transition retains two seconds. A clean natural exit can succeed with complete frozen final checkpoint data and no pending/error evidence. Controlled stops retain the actual exit code, the verified completion prompt and matching live/preserved hashes. No chooser interaction is supported.

The measured 2008 specified-ion-full continuation uses the same `End of calculation` Yes/No prompt as ordinary mode. Its version/mode-specific selector recognizes this only for checkpoint inspection; it never authorizes an event Save response. Standard/Pro specified-ion-full use `End of TRIM.DAT calculation`. This distinction does not broaden unmeasured 2008 modes.

The pipeline checks job shutdown before freezing the runtime, recovers from the permanent copy, compares live/preserved hashes for controlled stops, and verifies requested values and exact total/autosave counts. Popup, timeout, cancellation and monitor failures still fail even with recoverable checkpoints. If shutdown cannot be verified, failure evidence remains in the record and working runtime; no frozen-runtime claim is made.

Native continuation can rewrite staged `Data/TRIM.cfg` presentation fields. Resumed runs write `output/parsed/config-integrity.json` with original/final hashes, `equal`/`qualified`/`different` status and observed changes. Qualification requires a pinned known engine and the strict observed config layout: only colours, two known help-text spellings and numeric spacing/LF/CRLF serialization may change. Label size and the maximum-secondary-recoil setting must match their own sealed original values; 20,000 and 200,000 are not interchangeable. Unknown content, changed protected settings or a mismatched baseline still fail integrity. Both config byte versions remain preserved. Fresh runs and all other inputs retain exact hash checks.

Recovery retains stored precision, availability and inferred-geometry warnings. Stable snapshots do not establish atomic native array saves or independent physics validation. The approved shared checkpoint-first design and verification evidence are recorded in the [current direction](../decisions/trim-continuation.md).

The isolated integration commit passes all 146 TRIM regressions with warnings as errors. The mixed development tree also passed 151, including five tests for separate pending parser work. Native checks in that mixed tree verified the twelve intended ion-full/specified-ion-full scenarios across 2008, Standard and Pro: six final-autosave no-response successes, three ordinary explicit-save successes and three expected event checkpoint-required failures. Fourteen attempts were retained because the first two 2008 event prompt failures were preserved before the approved selector correction and two additional checks. Source installations, settings and parent records remained unchanged.

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

Resume uses the complete preserved save set, not TDATA.sav alone. File/hash and textual identity checks do not establish that TRIM wrote every binary array atomically at one instant, or prove numerical equivalence with an uninterrupted calculation. Report-only continuation still reports the completion dialog as a popup failure. Automatic completion requires the verified checkpoint-first lifecycle described above.

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
