# Handoff

## SRModule input and runtime preparation

- Added `src/srim/inputs.py`: `validate_sr_request(request)` and `render_sr_input(request)`. The explicit version-one schema supports energy ranges and ordered lists of 1–1,000 energies, preserves decimal strings, and renders ASCII CRLF `SR.IN` with output name `stopping.txt`.
- Added `src/srim/runtime.py`: `prepare_sr_simulation(paths, config, created, worker, request, electronic, nuclear)` returns `worker/<worker_id>/sr/<simulation_id>`. Callers supply existing empty simulation/worker allocations and complete electronic/nuclear resource selections.
- Runtime preparation copies exact selected coefficient bytes plus `SRModule.exe` and `VERSION` from the configured installation's `SR Module` directory. The module VERSION's trimmed first line supplies engine.version; installed Data/VERSION is not substituted.
- Five synchronized runtime files publish through a staging-directory rename. Record inputs include `SR.IN`, exact `VERSION`, and a sorted five-file hash/size manifest in `input/engine/runtime.json`. Source attribution and transformation provenance are retained. Preparation leaves status created and never launches an executable.
- `initialize_simulation` now accepts optional keyword-only `engine_inputs: Mapping[str, bytes] | None = None`. It validates safe flat filenames and exact bytes before writes, inventories prepared files, and publishes metadata last. Existing callers remain compatible.
- Errors retain staging/runtime/record evidence with exception notes; preparation does not overwrite existing destinations, retry, clean up, or mark simulations failed. Worker-parent locking nests simulation/resource operations in that order.

## Verification

- All 78 tests pass with warnings treated as errors: 38 backend and 40 SRIM tests, including 11 new tests for request schemas, serialization, provenance, isolation, integrity, collisions, links, lock contention, and publication failures.
- Module/class/function docstring, argument/return annotation, and no-comment checks passed for all six changed Python files. No installed executable was run; tests use synthetic installations.

## Remaining work

Design and obtain approval for SRModule execution and output parsing integrated with simulation lifecycle transitions, including runtime-manifest verification before launch and collection of `stopping.txt`. Live executable compatibility remains unverified. CAB baseline experiments and CAB calculation remain deferred.
