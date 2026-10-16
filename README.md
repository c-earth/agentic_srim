# Agentic SRIM

Run SRIM calculations from an AI agent or a command line, then retrieve structured results with units and traceable simulation records.

Agentic SRIM connects a local SRIM installation to an MCP-compatible agent. The agent can validate inputs, run calculations, inspect results and request output pages. The command-line interface provides the same capabilities for scripts and manual use.

## Capabilities

| Capability | Calculation | Results |
|---|---|---|
| Stopping and range | SR Module tables over an energy interval | Electronic and nuclear stopping, projected range, longitudinal and lateral straggling |
| TRIM transport | Ion transport and damage in layered targets; modes 1–8 with the restrictions below | Summary, depth profiles, range distributions, backscatter, transmission, sputtering, trajectories and requested collision records |

Both capabilities preserve scientific inputs, native inputs/outputs, simulator versions, logs and checksums for each attempt. Results include units and provenance linking them to their originating run.

A successful run has state `CHECKED`: its outputs passed software format, request-matching and artifact-integrity checks. Independent scientific accuracy and convergence must be evaluated for the intended application.

## Requirements

- Windows with an interactive desktop session. TRIM is a Windows GUI program and may briefly display dialogs.
- [Python 3.13 or later](https://www.python.org/downloads/windows/).
- [Git](https://git-scm.com/downloads/win) to clone the repository.
- A separately installed [SRIM suite](https://www.srim.org/). Native integration has been exercised with SRIM-2013.
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

| Task | MCP tools |
|---|---|
| Validate inputs | `validate_stopping_range`, `validate_trim` |
| Execute calculations | `run_stopping_range`, `run_trim` |
| Read results | `get_run`, `get_stopping_range_output`, `get_trim_output` |
| Cancel or reconcile interrupted work | `cancel_run`, `recover_runs` |

A typical workflow is to gather explicit scientific inputs and sources, validate the request, obtain authorization to run, execute, and retrieve the outputs. Scientific applicability and convergence remain part of the analysis.

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
~~~

To deliberately exercise the installed simulators, set both native gates to `1` for the test invocation and restore them to `0` afterward. Five native tests perform 26 calculations across backend modes, concurrent isolated runtimes and CLI/MCP interfaces. Tests remove disposable runtimes; inspect `runtime/test_workspaces` and remove it only if it is empty. Git attributes preserve fixture bytes.
