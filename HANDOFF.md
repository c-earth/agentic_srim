# Handoff

## SRModule execution and parsing

- Added `src/srim/outputs.py`: `parse_sr_output(contents, request)` validates ASCII range/list formats, all eight stopping-unit labels, range conversion footers, numeric values, list counts/order, and printed energy precision. Results preserve numeric strings, mixed units, duplicate list energies, original requested energies, and raw-output hashes/sizes.
- Added `src/srim/execution.py`: `verify_sr_runtime(paths, simulation_id, runtime)` checks the prepared five-file manifest, initialized input inventory, engine identity, and resource provenance. `run_sr_simulation(paths, config, simulation_id, runtime, *, timeout_seconds=60.0)` runs synchronously under the global worker-parent lock and integrates the existing lifecycle.
- Outputs are exclusive binary `output/raw/stdout.bin`, `stderr.bin`, and available `stopping.txt`, plus version-one `output/raw/execution.json` and successful `output/parsed/result.json`. Runtime files remain available. Inputs and runtime bytes are rechecked after execution, including unsuccessful exits.
- Expected execution/parsing failures return failed metadata. Invalid caller/state/collision checks and lock contention raise without taking ownership. Filesystem/publication errors propagate after best-effort evidence preservation and failed finalization. Interruptions are re-raised. Failed metadata publication can leave the preceding status; exception notes identify secondary failures and retained paths.
- Added captured ASCII CRLF range/list fixtures and twelve workflow tests in `test/srim/test_runtime.py`.

## Verification

- All 90 tests pass with warnings treated as errors: 38 backend and 52 SRIM tests. Coverage includes transformed resources, malformed manifests, terminal guards, lock contention, timeouts, failed exits, corrupted inputs, output publication failures, and interruption.
- Live checks using the configured installation succeeded for a 79-row range and a four-row ordered list with a duplicate energy. A forced timeout produced failed metadata. Raw output, input/output inventories, and runtime hashes were verified; installed executable/version/coefficient hashes remained unchanged.
- Live evidence: `.cache/sr-execution-verified-iet9uq8y/integration-check.json` and its simulation records. Earlier format probes also confirmed all eight unit labels.
- Module/class/function docstrings, complete argument/return annotations, absence of Python comments, fixture ASCII/CRLF, and Python whitespace checks passed for the changed code. `git diff --check` passed.

## Remaining work

CAB derivation mode, including baseline experiments and correction-factor calculation, is excluded from the current plan for now. SR requests continue to use the existing none or supplied correction modes.
