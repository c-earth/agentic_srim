# First SR calculation

[Documentation index](../README.md) · [API reference](../reference/api.md) · [Data formats](../reference/data-formats.md)

This walkthrough uses the implemented stopping/range workflow. It uses correction `none` to avoid default CAB GUI recovery during the first calculation. SRIM must already be installed and able to run its SR Module.

## Configure this checkout

Use a Python 3.13+ virtual environment at `.venv`. The existing checkout's interpreter is `D:\projects\agentic_srim\.venv\Scripts\python.exe`. Run the example from `D:\projects\agentic_srim` so `src` imports resolve. There is no `pyproject.toml`, `setup.py`, CLI entry point or third-party Python dependency to install.

Set the installation path in `settings.toml`, for example:

```toml
[time]
timezone = "UTC"

[srim]
root = 'D:\local\srim\srim_2013_std'
hide_sr_window = true
hide_trim_window = true
```

TOML literal strings above preserve backslashes. With double-quoted TOML strings, double each backslash. Configuration accepts only these tables/keys. `srim.root` is required and absolute; `time.timezone` accepts `UTC`, `system`, or `UTC±HH:MM` with hours 00–23 and minutes 00–59. Signed zero becomes `UTC`. Loading checks the path syntax; installation files are checked when defaults are imported.

Both window settings are optional booleans and default to `true`. `hide_sr_window = true` requests hidden SRModule GUI startup for main calculations and Bragg dependencies; `false` uses the executable's normal display behavior. This setting is read from the `Config` passed to execution, so reload the TOML after changing it. It does not make execution asynchronous. `hide_trim_window` is independent and reserved for the future TRIM runner; it currently launches or hides nothing. CAB recovery's separate SR GUI automation remains visible and unchanged.

Initialization reads `SR Module/SRModule.exe`, `SR Module/VERSION` (or `_VERSION` if `VERSION` is absent), `SR.exe`, and `Data/{VERSION,ATOMDATA,SCOEF03.dat,SNUC03.dat,Compound.dat}`. Windows filename matching is case insensitive. Known installation defects are normalized in memory and recorded; the installation is not edited. Arbitrary malformed files are rejected.

## Allocate, prepare, execute and read

The following complete Python example calculates hydrogen stopping in a solid carbon target at 126 and 1000 keV. Target mass and name come from the selected ATOMDATA. The projectile mass remains the explicitly requested value.

```python
"""Run one SR stopping calculation from the repository root and print its rows."""

import json
from pathlib import Path

from src.backend.config import load_config
from src.backend.paths import create_base_directories, get_project_paths
from src.backend.records import CreateRequest, create_directories
from src.srim.defaults import switch_sr_defaults
from src.srim.execution import run_sr_simulation
from src.srim.runtime import prepare_sr_simulation

config = load_config(Path("settings.toml"))
paths = get_project_paths(Path.cwd())
create_base_directories(paths)
generation = switch_sr_defaults(paths, config)

allocations = create_directories(
    paths, config, [CreateRequest("worker"), CreateRequest("srim")]
)
worker, simulation = allocations.completed

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
print(simulation.path)
print(metadata["status"])
if metadata["status"] != "succeeded":
    raise RuntimeError(metadata["failure"])

result_path = simulation.path / "output" / "parsed" / "result.json"
result = json.loads(result_path.read_text(encoding="utf-8"))
print(result["stopping_units"])
for row in result["rows"]:
    print(row)
```

If saved as `example.py` in the repository root, run it with:

```powershell
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' .\example.py
```

`switch_sr_defaults` imports/reuses seven coordinated resource versions and publishes the fixed hydrogen-core-zero convention. It does not recover other cores/bonds or run an engine. Repeating it with the same effective installation reuses matching resources. All six selection keys remain required even when correction is `none`; `compound` must be `None` for a direct target.

Allocation creates a new worker and simulation. Preparation resolves selections and stages a five-file runtime, then initializes the record with status `created`. With `derived` correction, preparation may first run missing Bragg dependencies and GUI captures. The returned runtime has not run the main calculation. `run_sr_simulation` verifies prepared inputs and executes it once; terminal records cannot be rerun in place. Allocate a new simulation for another run.

## Change the calculation

For range/straggling output, replace only the energy object:

```python
"""Select an engine-generated energy range for the existing request."""

request["parameters"]["energy"] = {
    "mode": "range", "minimum_keV": "1", "maximum_keV": "1000"
}
```

The engine chooses intermediate range energies; the API does not accept a range step. List mode preserves requested ordering and duplicates and returns stopping values only. See all eight [stopping-unit codes](../reference/data-formats.md#units-and-results).

For a compound target, use `{"source": "compound", "record_index": N, "phase": "solid"}` with the effective one-based index and set `selections["compound"]` to a selection object. Search the selected database first rather than assuming record indices agree between installations. For automatic CAB correction use `{"mode": "derived"}` and provide direct-target bonds or a compound record with bond information. The [resource guide](resources-and-cab.md) explains the calculation and fallbacks.

## Inspect records and failures

Records are saved under `memory/srim/srim_YYYYMMDD_HHMMSS_N`. Important files are:

- `input/request.json`: original intent.
- `input/selections.json`, `input/effective-request.json`, `input/resolved.json`: selection intent, engine-ready request and provenance.
- `input/resources/`: original/effective source snapshots.
- `input/engine/{SR.IN,VERSION,runtime.json}`: generated input, logical version and five-file runtime hashes.
- `output/raw/{stopping.txt,stdout.bin,stderr.bin,execution.json}`: available exact output and diagnostics.
- `output/parsed/result.json`: parsed successful output with numeric spelling preserved.
- `output/raw/runtime/`: verified copy of the final working-directory contents, including native inputs and available generated files.
- `metadata.json`: lifecycle, input/output inventories and failure details.

Some raw files are absent if preflight or launch failed. Expected execution failures return failed metadata; invalid caller state, contention and filesystem/publication failures may raise exceptions. Interruptions propagate after best-effort finalization. Inspect exception notes for retained records, versions and capture paths. Failed preparation can leave dependencies and an empty/partial main allocation without main metadata; there is no automatic rollback, retry or cleanup.

Worker execution uses a separate nonblocking lease for each worker. Different workers can execute concurrently; direct competing calls on the same worker raise. Metadata/resource locks wait up to 30 seconds for brief shared operations. CAB GUI captures retain a separate project-wide capture lock. Separate timeout settings apply to each GUI capture (30 seconds), dependency SR run (60 seconds) and main SR run (60 seconds). GUI cleanup has additional bounded waits, so the capture timeout is not a total wall-clock deadline.

## Queue prepared simulations

The batch API accepts existing prepared simulations. Allocation, resource selection, dependency recovery and preparation happen before queuing. A simulation keeps the worker where it was prepared; prepare across four different workers to permit four concurrent native runs. Windows schedules the resulting processes without CPU affinity settings.

```python
"""Execute previously prepared simulations and inspect task and cleanup outcomes."""

from src.srim.queue import PreparedSRTask, run_sr_batch

tasks = [PreparedSRTask(simulation_id, runtime) for simulation_id, runtime in prepared_runs]
batch = run_sr_batch(paths, config, tasks, max_workers=4)
for item in batch.task_results:
    if item.error is not None:
        print(item.task.simulation_id, item.error)
    else:
        print(item.task.simulation_id, item.metadata["status"])
for retained in batch.cleanup.retained_workers:
    print("Worker retained:", retained.worker, retained.reason)
```

`prepared_runs` above is your sequence of `(simulation_id, runtime)` pairs returned by your preparation workflow; the example does not allocate or rerun the earlier completed walkthrough simulation. The complete batch is validated before launch. Tasks on one worker preserve submission order; the earliest task on an available worker starts next. Results return in input order, and individual failures do not stop other tasks. `timeout_seconds=60.0` bounds each native call, excluding queue waiting; `batch.elapsed_seconds` also includes validation and cleanup.

Each run preserves its permanent record immediately. Runtime folders stay until all task calls finish. Then cleanup checks each submitted worker and clears its runtime folders only when every working runtime has matching terminal permanent evidence and no unrelated/TRIM files or legacy CAB dependencies require retention. Failed runs are eligible after verified preservation. A preparation failure or failed archive retains working files. Empty worker, sr and trim directories remain for reuse. Ordinary direct `run_sr_simulation` calls preserve evidence but do not clean runtimes; `cleanup_sr_batch` is available for explicit cleanup after other operations.

The queue lives only in the calling process. Interruption stops new dispatch, cancels work not started, waits for active bounded calls and retains workers before re-raising. Pending work does not automatically resume after a restart. Separate batch callers are not merged into one queue; a worker busy in another caller is reported as a task error.

**Worker scaling: RETEST REQUIRED.** The existing comparison of 16 short SRModule calculations at 1, 2, 4 and 8 workers is preliminary. Performance conclusions are deferred until the same worker-count comparison can be repeated with a representative TRIM workload after TRIM execution is implemented.

Derived-correction progress reports every bond and Bragg/core contribution, including cached, resolved, fallback and skipped work, and reaches its fixed total on successful contribution resolution. That total does not mean runtime preparation or later evidence verification has finished. Callback exceptions propagate; completed evidence remains retained. The dedicated bulk APIs report each selected entry.
