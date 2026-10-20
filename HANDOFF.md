# Handoff - 2026-10-20

## Current SR state

CAB integration and conservative installation compatibility fixes are implemented. Earlier state and native recovery evidence are in `doc/CAB_HANDOFF.md`; completed CAB scope is in `doc/history/CAB_INTEGRATION_TRACKER.md`. At visibility-feature completion the suite passed 127 tests (39 backend, 88 SRIM). The subsequent prepared-batch implementation and latest verification are recorded below. User authorized this handoff update and local commit; no publication requested.

Earlier SR execution checks were recorded at `.cache/sr-execution-verified-iet9uq8y/integration-check.json` (79-row range, four-row ordered list with duplicate energy, forced timeout and installation integrity).

## Independent TIN/TRIM investigation

`doc/TIN_FINDINGS.txt` records the verified input/mode mappings and qualifications. No TIN/TRIM implementation was added or approved. Remaining investigation: complete TRIM input formats, dependencies, output controls, unattended batch lifecycle and resume; some modes still require dialogs.

Historical evidence was recorded in `.cache/tin-investigation-20261019/{work,tmp}`. Relocation verified 9,616 file hashes; archive-manifest.json retains pre-relocation hashes, while damage-report paths were updated. These ignored artifacts are not included in the commit.

## Cache cleanup

The user requested clearing the project cache on 2026-10-20. Six processes running from the cache were closed, and all contents were deleted while retaining the empty `.cache/` directory. Historical evidence paths in the documents are no longer available locally; recorded results remain historical summaries.

## Fresh recovery after cache cleanup

Both native versions passed fresh 18-bond recovery, carbon-core recovery and methane execution with 44 successful capture cleanups each. Numerical results match exactly and installation hashes are unchanged. New evidence: `.cache/fresh-recovery-both-9p7q5ne2/verification.json`. The historical GUI cleanup timeout did not reproduce; no implementation changed. See `doc/CAB_HANDOFF.md` for qualifications.

## Non-TRIM documentation review

The review and unresolved discrepancy details are in [doc/reviews/non-trim-review.md](doc/reviews/non-trim-review.md); current user entry points are [README.md](README.md) and [doc/README.md](doc/README.md). Historical CAB proposal/catalog/tracker/research and compound-comparison files moved to `doc/history/`. The user approved documentation-only changes; implementation fixes require a separate complete proposal and approval. No source, tests or settings changed in this review.

## SRModule window settings

The separately approved visibility feature is implemented. `settings.toml` now documents independent `[srim]` boolean keys `hide_sr_window` and `hide_trim_window`, both defaulting to true. SRModule main/Bragg launches use `STARTF_USESHOWWINDOW`/`SW_HIDE` when hidden and no startup override for normal display. The TRIM flag is validated/stored only; no TRIM runner exists. CAB capture GUI behavior is unchanged.

Native verification: `.cache/sr-window-u9n0p6jn/verification.json` records 12 successful range/list runs, byte-identical output within each mode across both visibility settings, and unchanged installation inputs. Eight additional observation runs succeeded. `window-observations-second.json` records 138 samples, including 63 per setting, with no targetable native windows observed in either mode. Visible-mode UI was therefore not confirmed, and sampling does not prove absence of transient windows. These ignored artifacts are local only.

All 127 tests, source docstring/annotation/comment checks, configuration parsing, public-signature/documentation checks and Git whitespace checks passed. The four implementation findings in the non-TRIM review remain open; the visibility feature does not resolve them. No further work remains for the approved visibility scope.

## Prepared batches and permanent CAB evidence

The approved implementation is complete. `src/srim/queue.py` provides `PreparedSRTask`, `SRTaskResult`, `SRBatchResult` and `run_sr_batch`: an in-memory queue of existing prepared simulations, with an explicit concurrency limit, submission order within each worker and input-ordered results. Different workers run concurrently; same-worker tasks wait. Ordinary task failures are collected independently. Interruption stops dispatch, waits for active bounded calls and retains working files. Preparation and worker assignment remain outside the queue.

`src/backend/records.py` now provides stable per-worker execution leases under `worker/.locks/worker_N/`, with brief parent coordination and busy-worker deletion protection. Resource/metadata operations wait up to 30 seconds for shared locks. CAB GUI captures retain a separate project-wide capture lease.

`src/srim/storage.py` preserves and verifies exact final runtimes under `memory/srim/<simulation_id>/output/raw/runtime/`. Original SR executable/coefficient bytes are also preserved during preparation. `src/srim/capture_records.py` gives new CAB GUI captures permanent simulation records with engine identity `sr-capture`, native/default/baseline input snapshots and permanent output evidence. New CAB entries reference these simulation records; legacy worker-owned evidence remains readable.

Final user policy: save evidence immediately, clean verified runtime folders only after the whole batch, and keep empty `worker_N/sr/` and `worker_N/trim/` directories for reuse. `BatchCleanupReport.cleaned_workers` records completed cleanup; `retained_workers` explains incomplete preservation, unfinished/unrelated/TRIM files, unsafe paths, active use or legacy dependencies. This applies to future execution only. No existing evidence was migrated and historical `.cache` experiments were left unchanged. Direct single runs/recovery preserve evidence but require explicit cleanup. Permanent simulation/resource records are not evicted.

Latest verification passed all 146 tests with warnings as errors (41 backend, 105 SRIM), including worker reuse, concurrent overlap/limits, same-worker ordering, failure isolation, interruption, preservation failure, native-call isolation, and reloading a carbon core plus all 18 bond dependencies after runtime cleanup. All 43 Python files passed syntax/docstring/annotation/comment checks; current guide snippets, local links/anchors, public API coverage and Git whitespace checks passed. Current usage and contracts are in `doc/guides/getting-started.md`, `doc/guides/resources-and-cab.md`, and `doc/reference/{api,data-formats}.md`.

Local native evidence: `.cache/queue-benchmark-v2_b5iw9/verification.json` records 64 successful SRModule runs with identical 79-row results and unchanged installation hashes. `.cache/cab-permanent-native-a9l2xoj7/verification.json` records successful native H-C recovery and identical CAB-entry reload after verified cleanup. Both checks preceded the final amendment retaining empty workers; final retention/reuse behavior was then verified by the full test suite. These ignored artifacts are local only. No new TRIM implementation was added, and the four pre-existing non-TRIM review findings remain outside this change.

## Worker scaling - RETEST REQUIRED

The user deferred the performance conclusion until a representative TRIM workload can be tested. The existing 16-run SRModule comparison at 1, 2, 4 and 8 workers is preliminary; it does not establish TRIM scaling. Repeat that worker-count comparison when TRIM execution is available. No TRIM implementation or runs are requested by this status update.
