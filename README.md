# Agentic SRIM

Repository: [c-earth/agentic_srim](https://github.com/c-earth/agentic_srim).

The `stopping_range` capability runs the installed SR Module in energy-interval mode and returns stopping, range, and straggling with units and source-run provenance. The `trim` capability supports calculation codes 1–8 with the installed-version output restrictions below. All eight modes, both mode-8 collision-record choices, native integration, and fresh CLI/MCP execution/paging have software verification. Scientific accuracy validation is deferred at the user's request; successful software checks produce `CHECKED`, never scientifically `ACCEPTED` evidence.

## Installation

Use Python 3.13 or later and a project-local `.venv`. This project's required interpreter is `D:\projects\agentic_srim\.venv\Scripts\python.exe`. Someone cloning elsewhere must create their own virtual environment and substitute their checkout/interpreter paths in these commands.

```powershell
Set-Location 'D:\projects\agentic_srim'
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' -m pip install -e .
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' -m pip check
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' -m agentic_srim.interfaces.cli --help
```

An editable install points the virtual environment at `src/agentic_srim`; Python source edits take effect on the next process launch. Restart running processes after edits. Reinstall after changing dependencies or entry points. For a regular installation, replace `-e .` with `.`. There are no third-party runtime dependencies; the build backend is pinned in `pyproject.toml` to setuptools 84.0.0. The existing environment's unrelated packages are not application requirements. A clean-environment rebuild has not yet been exercised.

Install SRIM separately and configure `configs/application.toml`. The package neither downloads nor bundles SRIM. Set the single `srim_directory` installation root in TOML. It must contain `TRIM.exe`, `Data/`, and `SR Module/`; the latter contains `SRModule.exe`, `SCOEF03.dat`, `SNUC03.dat`, and `VERSION`. Both backend paths are derived from that root; moving an unchanged installation only requires updating this setting. Keep its installation and runtime directories disjoint. Relative configuration paths resolve against the TOML file's directory.

The local Git repository tracks application code, configuration, regression fixtures and project documentation. `.gitignore` excludes the project virtual environment, project-local simulator copies, runtime data, disposable `tmp/` work, local `.codex` settings, Python bytecode and packaging outputs. `.gitattributes` preserves every regression fixture byte without line-ending conversion. SRIM is installed separately; simulator redistribution/deployment licensing review remains pending. See `Data/LEGAL.rtf` beneath the configured SRIM root before distribution.

## Run the capability

MCP callers pass a complete `request` object directly, using the tool's input schema. No example files are loaded by MCP. For CLI commands below, create your own `request.json` and TRIM request files with explicit scientific inputs; the filenames are user-supplied paths.

```powershell
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' -m agentic_srim.interfaces.cli validate request.json
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' -m agentic_srim.interfaces.cli run request.json
```

`agentic-srim` is also installed as a console entry point in `.venv/Scripts`. `run` prints the run ID to stderr and a JSON report to stdout. It returns 0 for a verified `CHECKED` result with no calculation error, including a result accompanied by a late worker-exit warning; 1 for an unsuccessful or unfinished calculation; and 2 for handled command, validation and SQLite storage errors. Set `$runId` to an actual returned run ID before using these commands:

```powershell
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' -m agentic_srim.interfaces.cli show $runId
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' -m agentic_srim.interfaces.cli output $runId --capability stopping_range
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' -m agentic_srim.interfaces.cli cancel $runId
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' -m agentic_srim.interfaces.cli recover
```

The SR regression request in `tests/sr_module/fixtures/request.json` copies the installed SR.IN hydrogen/1,3-propanediol values. It is not a reviewed scientific reference or an implicit default. Every calculation requires ion Z/mass, target composition/masses, density, phase, compound correction, an increasing positive energy interval, stopping-unit code, and source descriptions. Masses use u, density g/cm3, energy keV, and composition relative numbers of atoms (not mass fractions). `solid` selects SR's condensed-phase flag, including liquids; `gas` selects its gas flag. Compound correction is an explicit positive multiplier. The native input contract follows `HELP - TRIM Input.pdf`, pages 8-33 through 8-35, and `SR Module/HELP- SR Module.rtf`, both beneath the configured SRIM root.

Output energies are normalized to keV and all three lengths to nm. Stopping retains the requested native unit (codes 1-8 are listed in `src/agentic_srim/capabilities/stopping_range/outputs.py`). SR Module chooses the energy grid, so exact requested endpoints need not appear; no interpolation is performed. SR custom energy lists remain outside the original interval-table slice. TRIM transport and layered targets are available through the separate `trim` capability below. Physical straggling is not an estimate of scientific/numerical error. Random seeds and ion counts are inapplicable to these deterministic tables and are recorded as null.

## TRIM transport

```powershell
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' -m agentic_srim.interfaces.cli validate trim-request.json --capability trim
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' -m agentic_srim.interfaces.cli run trim-request.json --capability trim
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' -m agentic_srim.interfaces.cli output $runId COLLISON.txt --capability trim --offset 0 --limit 100
```

`show`, `cancel`, and `recover` work for either capability. `show`/MCP `get_run` return verified run metadata and lifecycle history. `output --capability stopping_range`/MCP `get_stopping_range_output` return the SR table with units and provenance. `output --capability trim`/MCP `get_trim_output` return a bounded page (maximum 1000 records) of a named TRIM profile or detail file. Retrieval verifies archived manifests and persisted analysis first. Only TRIM takes a filename and paging arguments; output defaults to stopping_range, matching validate/run. TRIM regression requests use explicit small sampling counts and are not scientific defaults or convergence studies.

| Mode | Calculation | Collision output |
|---|---|---|
| 1 | Incident ions with quick KP damage | Off or ion collision summaries |
| 2 | Incident ions with full cascades | Off, ion collisions, or ion plus recoil details |
| 3 | Monolayer collision steps / surface sputtering | Off, ion collisions, or ion plus recoil details |
| 4 | Explicit incident-ion list with quick KP damage | Off or ion collision summaries |
| 5 | Explicit incident-ion list with full cascades | Off, ion collisions, or ion plus recoil details |
| 6 | Explicit recoil list with full cascades | Off; collision levels 1/2 reproduce a native warning in this installation |
| 7 | Explicit recoil list with full cascades and monolayer steps | Off, ion collisions, or ion plus recoil details |
| 8 | Explicit recoil list with quick KP damage | Off, or collision level 1 with explicit summary/every records; full cascade requests rejected |

KP modes 1/4/8 reject full cascade output before execution. Modes 1/4 support KP collision summaries. The installed GUI exports 6=full recoil cascades, 7=full cascades with monolayer steps, and 8=quick KP. The older input PDF assigns 6=quick and 7=full and omits 8 in that passage (p. 8-17); it does not establish this installed GUI's numbering.

Mode-6 collision requests reproduce `No cascade details available with KP Damage` even with GUI-matched inputs and manual dialog handling. The adapter rejects collision levels 1/2 for this mode. The internal cause is unknown; the observation does not establish a physics defect or justify relabelling/substituting a calculation mode.

Mode 8 with collision level 1 requires `outputs.collision_records` to be `"summary"` (No to “Would you like every collision filed?”) or `"every"` (Yes, followed by the exact “Every Collision will be filed.” notification). The choice must be explicit; omission/null is rejected for this combination. For every other mode/output combination this field is null or omitted. Both CLI and MCP use this same validation. The choice enters the execution hash and per-attempt provenance even when the native input/output bytes are identical. Three-event regression requests are retained as `tests/trim/fixtures/{summary_mode_8,every_mode_8}/request.json`, not scientific defaults.

Required scientific inputs include beam Z/mass/energy/angle, count, seed, calculation mode, 1–12 independently tracked element columns, 1–8 layers, every constituent's displacement/lattice/surface energy, layer composition/density/phase/Bragg correction, depth window, stopping-version flag, output switches, particle list, and source descriptions. Inputs use nm, keV for the beam, and eV for damage/event energies. Layer widths, depth-window endpoints and particle coordinates must also remain finite when converted from nm to native Angstrom units; overflow is rejected before storage or execution. Composition uses relative atomic amounts; duplicate atomic numbers are allowed as distinct tracked columns. A `0,0` depth window explicitly selects the entire target. `random_seed = 0` explicitly requests TRIM's native default seed. No material, count, or seed is silently selected.

Modes 4–8 require a `particles` array with one event per requested ion/event (up to 99999). Each record supplies an event label, Z, energy in eV, position in nm, and normalized direction cosines. The adapter renders CRLF `TRIM.DAT` with ten header lines and a final CRLF. Modes 4/5 require the declared incident species; modes 6–8 require a target species present at the starting depth. Modes 6–8 consume recoil data supplied by another calculation; they do not transport neutrons/electrons/photons. Any conversion from per-event averages to per-external-primary results must be supplied separately, as described on Input p. 8-18. Biological targets and isotope/mixing applications use these explicit layer/element inputs; no reviewed material catalog is implied.

Parsed native text outputs are `TDATA.txt`, `RANGE.txt`, `LATERAL.txt`, `IONIZ.txt`, `PHONON.txt`, `VACANCY.txt`, `E2RECOIL.txt`, `NOVAC.txt`, `RANGE_3D.txt`, `BACKSCAT.txt`, `TRANSMIT.txt`, `SPUTTER.txt`, `TRIMOUT.txt`, `EXYZ.txt`, and `COLLISON.txt` (KP summaries, full-damage ion records, and detailed recoil cascades). All raw outputs and native restore snapshots are preserved, including binary `.SAV` files. Native `TRIMIN.sav` data fields must match the rendered request; comment lines are ignored. The parser quarantines missing requested files, malformed tables, mismatched identities and count mismatches covered by its checks. These checks do not establish that every possible truncation of a detail file is detected.

Profile depths and individual positions normalize to nm; energy profiles use eV/(nm event), range densities use 1/nm, and native phonon counts retain the printed count units. Each profile retains the native unit and printed normalization. The installed program may print a fractional normalization count in profiles; integer completed events come from `TDATA.txt`. Already-normalized profiles are never divided by the event count again. The initially absent last-recoil field in some external-event `EXYZ` files is null. Detailed records stream into checksummed JSONL artifacts and are read by pages. Native versions/format differences are retained in provenance; this installation prints SRIM-2013.00 for TRIM while SR Module uses its own header.

TRIM is a Windows GUI executable with batch execution, not a true headless CLI. The approved Win32 handler verifies the launched process, dialog class, exact text, control ID, button label, and button owner. It handles matching-count completion, the requested recoil cascade-file choice, and file-active notification after Yes. Mode 8/collisions=1 additionally handles the explicit summary/every choice after acknowledged file activation, and acknowledges its every-record notification only after an explicit Yes. Mode 7 did not show the startup cascade-file question in the prior diagnostic controls. Interactions and unexpected dialog text are archived. Unexpected/error prompts remain untouched and fail at the configured timeout. These actions require no assistant Computer Use connection. A Windows session capable of running native windows is required; service/session-0 operation is unverified. Native dialogs can appear briefly.

## Execution and audit

One Python worker process owns an OS-locked persistent workspace. Support files are copied once, then installation and workspace fingerprints are checked before reuse. A busy worker rejects another calculation; there is no background queue scheduler. Each SR attempt removes the expected old output and replaces SR.IN. Installation changes require selecting a fresh runtime directory. Operational settings default to 30 seconds per attempt and one attempt, bounded to at most 3600 seconds and three attempts.

Both backends share this lock. SR uses `runtime/workers/worker-1/sr_module/`; TRIM uses sibling `runtime/workers/worker-1/trim/` with `TRIM.exe` and copied `Data/`, plus private native output/restore directories. The shared `.lock` and `active_attempt.json` stay at worker root. Only one backend runs at a time. Every TRIM attempt clears old generated outputs and restore files before a fresh calculation. Archives add `TRIM.IN`, `TRIMAUTO`, optional `TRIM.DAT`, `native/` with original subdirectories, and `processed/*.jsonl`. Recursive manifests cover all files. Retry attempts start fresh; the application does not expose TRIMAUTO resume/continuation, and recovery never resumes or reruns native work. Snapshots remain available for audit.

Independent runtime roots can run concurrently, each with its own metadata database and worker lock. Native regression coverage verifies overlapping SR/SR, TRIM/TRIM and mixed backend pairs, then rechecks sealed results after workspace reuse. There is no shared-runtime worker pool. The owner marker remains after completion and identifies the last attempt; its presence alone does not indicate unfinished work.

```text
runtime/
  metadata.sqlite
  workers/worker-1/
    .lock
    active_attempt.json
    sr_module/
    trim/
  runs/$runId/attempts/<attempt_id>/
    SR.IN
    request.json
    provenance.json
    SR_OUTPUT.txt
    stdout.log
    stderr.log
    native_process.json
    process.json
    result.json
    outcome.json
    manifest.json
```

Actual application runs retain their per-attempt audit artifacts. Disposable test/probe runtimes are cleaned after verification. Simulator support files are prepared in worker workspaces on first use.

Failed attempts preserve whatever artifacts were produced, so some listed files may be absent. Retries stay under the same run ID and never count as independent replicates. SQLite schema version 1 stores runs, attempts, events, and processed results; artifact manifests link file sizes and SHA-256 checksums. Results link their raw file, analysis version, physical hash, execution hash, run ID, and attempt ID. Both capabilities normalize proportional atomic amounts and exclude presentation names and sources from physical hashes. SR also normalizes target ordering and excludes stopping-unit selection. TRIM preserves tracked element-column order, layer order and the supplied event sequence; sampling and output controls belong in its execution hash. Execution hashing additionally includes rendered inputs, simulator/data fingerprints, application/analysis versions and execution bounds, with the SR stopping-unit code or TRIM sampling/output settings as applicable. Distinct physical hashes are not a scientific applicability test.

The lifecycle is `QUEUED -> RUNNING -> COLLECTED -> PARSED -> CHECKED`, with `FAILED`, `CANCELLED`, and `QUARANTINED` outcomes. Corrupt, incomplete, or mismatched native output is quarantined without retry. Metadata retrieval reads run, attempt, event and result rows in one database transaction, so concurrent completion cannot combine states from different snapshots. `show` verifies manifest checksums and compares persisted results against archived analysis before returning run metadata. Application code never overwrites sealed attempt artifacts; filesystem permissions do not enforce WORM immutability.

Use cancellation from another CLI process while a run is active. Ctrl-C requests cancellation and waits for worker cleanup. Recovery is explicit and never reruns a calculation. It holds the worker lock, refuses recovery if a recorded native PID is still alive or cannot be inspected, and preserves interrupted-attempt artifacts. Existing `RUNNING` attempts block worker reuse until recovery. Hard termination immediately between process creation and recording its PID is not covered by a Windows job-object supervisor; confirm prior native processes have exited before recovery. Synthetic interrupted-attempt recovery is tested; abrupt worker/process-tree termination is not yet an acceptance-verified feature.

Abnormal Python worker exits preserve their exit code and up to the last 4000 decoded stderr characters. Unfinished calculations retain these diagnostics in their error and event history, with attempt states and sealed files unchanged for explicit recovery. A zero exit without a terminal run state is also reported as incomplete execution. MCP run calls return `isError=true` for these failures. If a worker exits abnormally after committing a `CHECKED` result, the diagnostic is recorded separately as a `WORKER_WARNING` event and appears in the run metadata's optional `warnings` list, without changing its calculation error or sealed files. The result must still pass archive verification; if it does, the run call succeeds (CLI exit 0, MCP `isError=false`). Later `show`/`get_run` calls retain the warning. Runs without warnings keep their existing response shape.

## MCP

Start the server with an absolute configuration path when launching outside the project directory:

```powershell
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' -m agentic_srim.interfaces.cli --config 'D:\projects\agentic_srim\configs\application.toml' mcp
```

The server exposes nine tools: `validate_stopping_range`, `run_stopping_range`, `get_stopping_range_output`, `validate_trim`, `run_trim`, `get_trim_output`, `get_run`, `cancel_run`, and `recover_runs`. Scientific request fields are identical to CLI JSON inputs. Startup, discovery, and validation never launch either simulator. Calls are sequential; cancellation during an active call requires a separate CLI process. The implementation uses [MCP 2024-11-05 stdio framing](https://modelcontextprotocol.io/specification/2024-11-05/basic/transports), [initialization](https://modelcontextprotocol.io/specification/2024-11-05/basic/lifecycle), and [tool contracts](https://modelcontextprotocol.io/specification/2024-11-05/server/tools).

On this machine, `.codex/config.toml` registers `mcp_servers.agentic_srim` with absolute interpreter/configuration/working-directory paths, a 10-second startup timeout and 120-second tool timeout. `.codex/` is machine-local and excluded from Git; adjust registration paths for another checkout. Restart an existing server after source/tool-definition edits. Increase the client tool timeout when application execution bounds increase. New capabilities require explicit CLI/MCP routing.

Validation tools advertise `readOnlyHint: true` and never start simulations or change storage. Other tools use conservative effect annotations; metadata inspection may initialize storage. Tool hints and local interpreter allow rules do not grant simulation authorization. Current-source subprocess verification and desktop-connected server freshness are separate; current session limits are in [HANDOFF.md](HANDOFF.md).

## Verification

Tests follow the component-first layout: backend input/output/execution tests and fixtures under `tests/{sr_module,trim}/`, TRIM dialog tests under `tests/trim/`, and shared tests under `tests/{execution,storage,interfaces}/`. Ordinary tests use fixtures and mocks. Set both native gates explicitly to avoid inheriting an enabled switch; suppress bytecode in this process and its children:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:AGENTIC_SRIM_INTEGRATION = '0'
$env:AGENTIC_SRIM_TRIM_INTEGRATION = '0'
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' -B -m unittest discover -s tests -v
```

Tests remove their disposable workspaces. After a test run, inspect `runtime/test_workspaces` and remove the parent if empty; retain and inspect unexpected contents before cleanup. Application run archives follow the separate retention contract above.

Native verification requires execution authorization. This command enables all five integration tests and disables both switches again afterward:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:AGENTIC_SRIM_INTEGRATION = '1'
$env:AGENTIC_SRIM_TRIM_INTEGRATION = '1'
try {
    & 'D:\projects\agentic_srim\.venv\Scripts\python.exe' -B -m unittest discover -s tests -v
} finally {
    $env:AGENTIC_SRIM_INTEGRATION = '0'
    $env:AGENTIC_SRIM_TRIM_INTEGRATION = '0'
}
```

The SR switch enables one test with two installed-example calculations, comparing normalized rows and installation checksums; native SR coverage uses stopping-unit code 5. The TRIM switch enables two tests with 12 calculations: ten mode/collision-choice cases and two optional-output-off controls. Both switches together enable two additional tests: eight calculations across concurrent independent runtimes and four via fresh CLI/stdio MCP processes. A full native suite therefore contains five native tests and 26 calculations. Fixtures retain native inputs, outputs and checksums; historical capture paths in provenance may no longer exist. Regression agreement does not establish independent scientific accuracy, convergence or hard process-tree termination.

Current verified results, integrity checks and unverified behavior are recorded in [HANDOFF.md](HANDOFF.md). The acceptance backlog and deferred architecture are in [PROJECT_CHECKLIST.md](PROJECT_CHECKLIST.md); contributor and cleanup rules are in [AGENTS.md](AGENTS.md).
