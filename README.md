# agentic_srim

Python utilities for reproducible SRIM stopping and range calculations on Windows. The project wraps the installed SR stopping/range engine, manages material and coefficient variants, and derives core-and-bond (CAB) compound corrections with traceable calculation evidence.

Use it to:

- Calculate electronic/nuclear stopping at an ordered list of energies, including duplicates, or calculate stopping, range and straggling over an energy range.
- Search compound dictionaries and construct targets from explicit atomic proportions or compound records, including mass-based compositions.
- Compare immutable versions of atomic data, electronic/nuclear coefficients and CAB strengths without editing the SRIM installation.
- Recover default CAB strengths on demand, reuse matching calculations, and retain requests, effective inputs, hashes, diagnostics and outputs for inspection.

The repository currently provides Python APIs. It has no command-line application, agent orchestration service or packaged installer. Case and postprocessing directories can be allocated, but analysis workflows for them are not implemented. TRIM execution is not implemented and is outside this documentation review.

## Requirements

- Windows and Python 3.13 or newer. The code uses Windows locking and `os.path.isreserved`; the reviewed environment is Python 3.13.13.
- An existing SRIM installation with its numerical data and working runtime dependencies. SRIM executables and installation data are not distributed here.
- Windows PowerShell 5.1 and an interactive Windows desktop for missing default CAB recovery through the SR GUI. Ordinary SRModule execution uses an isolated subprocess.

Project Python dependencies are from the standard library; no pip dependency installation is needed for the current code. Run from this repository root so imports such as `src.srim.runtime` resolve. In this checkout, use `D:\projects\agentic_srim\.venv\Scripts\python.exe` for all project Python commands.

## Start here

1. Edit `settings.toml`: set `[srim].root` to the absolute installation path and optionally choose `[time].timezone` (default `UTC`). The checked-in installation path is specific to this workstation.
2. Follow the [first SR calculation](doc/guides/getting-started.md). It covers initialization, a complete request, execution and reading results.
3. Use [materials, resources and CAB](doc/guides/resources-and-cab.md) for compound search, local overrides, version creation and targeted recovery.

Window visibility is controlled independently in `settings.toml` under `[srim]`: `hide_sr_window = true` and `hide_trim_window = true`. Both default to `true` when omitted. Set `hide_sr_window = false` for normal SRModule window display; this also controls Bragg dependency runs. The TRIM flag is validated and stored for a future runner. CAB recovery's separate `SR.exe` capture GUI keeps its existing visible workflow.

Preparation may run missing dependency calculations; the main calculation starts only when `run_sr_simulation` is called. List mode returns stopping values; range mode also returns projected range and straggling. Engine execution returns metadata with `status="succeeded"` or `status="failed"` for expected operational failures, so always inspect the status.

For concurrent execution, prepare simulations across separate workers, then call `run_sr_batch(paths, config, tasks, max_workers=4)` with `PreparedSRTask(simulation_id, runtime)` objects. The in-memory queue preserves order within each worker and returns results in submission order. Each run saves its permanent evidence immediately; after the whole batch finishes, verified runtime folders are cleaned together and empty workers remain available for reuse. CAB GUI captures now have permanent simulation records too. See the [batch example](doc/guides/getting-started.md#queue-prepared-simulations) and [retention rules](doc/guides/resources-and-cab.md#evidence-and-retention).

## Documentation and verification

| Need | Read |
| --- | --- |
| Browse the documentation | [Documentation index](doc/README.md) |
| Find a Python API | [API reference](doc/reference/api.md) |
| Build requests or inspect stored records | [Data formats and layouts](doc/reference/data-formats.md) |
| See discrepancies found in the review | [Non-TRIM review](doc/reviews/non-trim-review.md) |
| Inspect previously recorded native checks | [CAB status and evidence](doc/CAB_HANDOFF.md) |

Run both test directories explicitly; top-level discovery alone does not discover these namespace-directory tests:

```powershell
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' -W error -m unittest discover -s test/backend -p 'test_*.py'
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' -W error -m unittest discover -s test/srim -p 'test_*.py'
```

The 2026-10-20 review passed 124 existing tests with warnings as errors and found additional contract discrepancies documented in the review. Tests use synthetic installations and controlled subprocesses; they do not establish universal numerical accuracy or installation compatibility. Previously recorded native checks cover representative 2008 and 2013 Standard cases, including all 18 bonds, carbon and methane, not every core or installation.

Preserve `memory/resources`, dependency simulation records and any legacy referenced worker capture directories together. Calculated entries verify their linked evidence; deleting that evidence makes reuse fail. See the [retention guidance](doc/guides/resources-and-cab.md#evidence-and-retention) before removing runtime data.
