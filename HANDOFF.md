# Handoff - 2026-10-21

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

## TRIM dependency and resume investigation - 2026-10-20

New results are recorded in `doc/TRIM_FINDINGS.txt`, with local evidence in `.cache/trim-dependencies-20261020/verification-summary.json` and its sibling reports/runtimes. All 15 supported version/mode combinations (2008 1-7; 2013 Standard 1-8) passed fresh full/reduced-bundle comparisons: all generated outputs matched within each pair. The reduced Data bundle is `TRIM.cfg`, `SCOEF03.dat`, `VERSION`, plus executable/input/control files and runtime directories. `TRIMAUTO` is the control file; `TRIMAUTO.TXT` is documentation. Smart App Control initially blocked 2013; the user turned it off and the retry succeeded. Source executable/Data bytes remained unchanged.

All 15 modes advanced autosaved resume checkpoints; event modes advanced to event 2 after supplying a second TRIM.DAT event. These probes were deliberately terminated after checkpoint/bounded observation, so clean unattended resume completion is not verified. Resume opens graphics and may show dialogs. Exhausted event input caused recoil runtime errors in exploratory probes. Incomplete saved-state inventories can silently change accumulated results; preserve the whole state set. Both relative and absolute resume input paths worked, but autosaves went to default `SRIM Restore`, not the custom input directory. Some uninterrupted/resumed ionization/phonon binary files differ; numerical significance and checkpoint atomicity remain unresolved. The document has detailed qualifications and evidence names. No implementation code changed.

## Elemental damage-energy correlation plot - paused at user request

Captured all 92 H-U elemental defaults from isolated 2013 Standard TIN UI, selecting atomic number in a single-component solid layer. Dataset: `.cache/trim-energy-defaults-20261020/energies.json` and `energies.csv`; checks, source hashes and correlations: `summary.json`. Rechecked N and Si after changing other elements; their UI trees are preserved in `recheck-nitrogen.txt` and `recheck-silicon.txt`. TIN requires more installation layout than the reduced TRIM engine bundle; isolated editor files are retained in that evidence directory.

Displacement defaults: 81 elements at 25 eV, five at 5 eV, H at 10 eV, Si/Ge at 15 eV, C/N/O at 28 eV. Lattice defaults: 85 at 3 eV, five at 1 eV, Si/Ge at 2 eV. Pearson r: displacement/lattice 0.943203; displacement/surface 0.157981; lattice/surface 0.137904. These describe TIN defaults, not measured material correlations. Surface defaults are not simply raw ATOMDATA sublimation values for every element: H has surface 2 eV while its sublimation entry is zero. The exact general fallback/default implementation remains unproven.

Three-panel interactive fragment: `C:/Users/Tokisaki963/.codex/visualizations/2026/10/20/01a15951-d57c-7d22-a9ee-fca8a623a779/trim-energy-correlations.html`. It embeds the 92 rows, displays all energy pairs, labels exact overlap groups, and provides nearest-point element details. Initial headless visual check passed (`.cache/trim-energy-defaults-20261020/preview.png`); JavaScript parses. An immediate first draw was subsequently added. Hover verification is unfinished: the temporary standalone QA wrapper initially failed because its extracted HTML retained numeric quote entities, not because a chart defect was demonstrated. That wrapper decoding was corrected in `interaction-check.html`; the final headless rerun was declined, then the user explicitly stopped and requested a handoff. Do not claim hover/mobile verification passed. No final plot has yet been delivered. Remaining work when requested: verify the corrected QA wrapper/interaction and narrow layout, then show the plot with the default-table qualification. Do not restart the 92-element capture.

## TRIM damage-energy research - 2026-10-20

Additional website findings and source links are recorded in the final section of `doc/TRIM_FINDINGS.txt`. Practical parameter selection depends on the intended output; some Quick Kinchin-Pease dpa protocols deliberately set lattice binding to zero, so physical/default binding advice is not universal. No material-specific values or protocol were chosen. Next clarification, if continuing this topic: target composition/phase and intended output (range, dpa or sputtering). This turn changed documentation only; no native runs or implementation changes.

## Figure tooling verification - 2026-10-21

The repository-local scientific-figures skill was discovered by this session after creation. Its configured figure-reviewer agent has not been activated by name: the session's sub-agent interface did not expose custom-agent selection. An independent general sub-agent followed the configured instructions in a read-only exercise using conceptual and quantitative design briefs; it identified supported issues and withheld unavailable visual/statistical checks. Named-agent activation and review of actual rendered project figures remain unverified.

Skill metadata, local reference links, agent TOML syntax, and the bundled PDF's SHA-256 match passed checks. The bundled skill validator could not run because the project interpreter lacks PyYAML; dependency-free structural checks passed instead, and no packages were installed. Root tmp/ and output/ directories containing the earlier PDF extraction/renders were removed at the user's request.

## TRIM defaults investigation - 2026-10-21

The complete session record is [TRIM input defaults](doc/reference/trim-input-defaults.md), including the 92-element table, empirical lookup rules, source references and final Standard compound-coverage histogram. This expands the earlier H-only sublimation comparison: all 92 surface defaults match positive ATOMDATA sublimation values or the observed zero-to-2 eV branch. Native causality and a published general fallback rule remain unverified.

The final Standard-only compound audit has 64 completed contexts and 404 retained/fresh observations across all 53 elements represented in presets, with no energy discrepancies. Full mode/mixture/ion/compound matrices remain incomplete. Queues are stopped, owned executables closed and copied dictionaries restored. [TRIM handoff](doc/TRIM_HANDOFF.md) records evidence locations and pending work. No production code changed.
