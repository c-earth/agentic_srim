# Handoff

Continue filesystem design for the SRIM/TRIM MCP project in `D:\projects\agentic_srim`.

The SR investigation summary is in [`.cache/outputs/sr-simulation-summary.txt`](.cache/outputs/sr-simulation-summary.txt).

## Agreed structure

```text
src/
  backend/
  srim/
  case/
  analysis/
  memory/
  plan/
memory/
  srim/
    srim_yyyymmdd_hhmmss_n/
  post/
    post_yyyymmdd_hhmmss_n/
case/
  case_yyyymmdd_hhmmss_n/
worker/
  worker_n/
    sr/
    trim/
```

## Agreed responsibilities

- `memory/srim`: raw and parsed simulation outputs.
- `memory/post`: processed results and evidence.
- `case`: goals and answers supported by evidence in postprocessing records.
- `memory` broadly: durable records, reusable knowledge, and future RAG/database storage.
- `src/plan`: workflow management and reuse of skills and workflows.
- `worker/worker_n/{sr,trim}`: temporary execution directories. One worker owns one task at a time, whether SR, TRIM, or another future engine.
- Shared configuration and filesystem support live in `src/backend`; engine and workflow modules retain their existing locations.
- Simulations may exist independently. Postprocessing references simulations; cases reference postprocessing evidence.
- Record every simulation attempt in `memory/srim`, including failed attempts. Explicit deletion must leave a durable trace; its exact representation remains to be designed.

## Agreed naming and configuration

- Numeric suffixes are unpadded and start at 1: `_1`, `_2`, ..., `_100`. This applies to records and workers. Sort suffixes numerically rather than lexicographically.
- `n` in the layout above represents that numeric suffix.
- Project configuration is stored in `settings.toml` at the project root, using double-quoted string values with escaped Windows backslashes.
- `[time].timezone` accepts `system`, `UTC`, or a fixed offset formatted `UTC±HH:MM`. Named timezones are excluded.
- `system` follows the Windows local timezone at execution, including daylight saving. Fixed offsets do not observe daylight saving.
- The current configured timezone is `UTC`.
- `[srim].root` is the absolute SRIM installation root. Executable and resource paths will be derived from it.
- The current configured root is `D:\local\srim\srim_2013_std`.

## Current state

The base directories contain `.gitkeep` files. No worker instances or timestamped records exist. The earlier premature implementation was removed; the configuration and base-directory implementations below were subsequently approved individually and implemented. Project instructions are in `AGENTS.md`.

`settings.toml` was approved and created with double-quoted strings and escaped Windows backslashes.

- `src/backend/config.py` provides immutable `Config` and `load_config(path)`. Configuration paths are explicit. Missing timezone settings default to UTC; `srim.root` is required. Unknown sections and keys, incorrect types, invalid timezone formats, and non-absolute Windows roots are rejected. Fixed offsets allow hours 00–23 and minutes 00–59; signed zero offsets normalize to UTC. Installation existence is not checked. File-access errors and TOML parsing errors propagate unchanged. The `system` setting is retained for future timestamp generation.
- `src/backend/paths.py` provides immutable `ProjectPaths`, `DirectoryReport`, read-only `get_project_paths(root)`, and explicit `create_base_directories(paths)`. Roots must be absolute. The memory parent is derived from `simulation_memory.parent`. Setup reports created and already-existing directories, including missing ancestors it creates but excluding existing ancestors above the project root. Failures retain partial work and attach a human-readable partial report to the original filesystem exception.
- Symlinks and Windows junctions are rejected at required directories and their ancestors, including above the project root, with `OSError(errno.ELOOP)`. Checks are not atomic with filesystem operations; concurrent replacement remains possible. Reports describe observations during each call, without locking or rollback.
- `test/backend/test_config.py` and `test/backend/test_paths.py` contain 21 passing standard-library unittest tests. Link rejection uses mocked detection and needs no Windows link privileges. Fixtures are created beneath `test/backend` and automatically removed by `TemporaryDirectory`.

Run both backend test files from the project root with `D:\projects\agentic_srim\.venv\Scripts\python.exe -B -m unittest discover -s test/backend -p "test_*.py" -v`.

## Next step

Continue the filesystem design proposal one file at a time. The user wants to review every file individually and explicitly approve complete designs before implementation. Configuration loading, base-directory handling, and their tests are approved and implemented.

Metadata schemas, timestamp generation APIs, suffix allocation and collision handling, deletion traces, and worker lifecycle/concurrency behavior beyond exclusive ownership remain unresolved. Timestamp timezone selection and unpadded suffix formatting are settled as described above. Do not implement the remaining filesystem behavior without an approved proposal.
