# Project handoff

Updated: 2026-10-16

## Current state and direction

Windows delivery: Case MCP (10 tools), SRIM MCP (10, including export_run_data), Analysis MCP (13). Branch codex/case-mcp; user authorized commit/push of the current delivery on 2026-10-16.

User direction: operational Case → SRIM → Analysis/plots → saved answers first; further patches deferred. Later requests authorize temporary skills, portable Codex setup, README audit/verification and commit/push. Broader implementation/testing, native runs, successor chats and automation remain stopped. PROJECT_CHECKLIST.md owns expanded acceptance.

Next: user-specified case, inputs and execution permission/budget; reload MCP configuration before use. Procedures: README.md.

Portable setup (2026-10-16): .gitignore exposes .codex/rules/git.rules and .agents/skills/{adaptive-search,hand-drawn-curve-detection}/SKILL.md; config.toml/python.rules stay local. AGENTS.md uses clone-relative paths. Skills validated/discovered; behavior untested. Ignore/path checks passed. README covers three-server setup and verification; 13 PowerShell/two JSON/two TOML blocks, local links, 33 tool names, two fixtures and three CLI helps passed without execution jobs. Fresh-clone execution untested. Later replace/remove skills via self-improvement, possibly as Analysis MCP methods; service/promotion deferred.

## Delivered and verified

- src/analysis/, configs/analysis.toml and agentic-analysis entry points: immutable bundles, environment identity, versions/assessments, idempotent jobs/reuse/recomputation, snapshots, budgets and pinned outputs. Trusted local execution supersedes mandatory OS/container isolation.
- Built-ins: dataset_summary/aligned_comparison/render_figures v1, experimental. Ordered sampled statistics/exact-grid comparisons; no transformations. Matplotlib 3.10.8 supports PNG/SVG/separate/overlay/shared panels. Verification, experimental operation, promotion and scientific acceptance remain distinct; no promotions.
- src/agentic_srim/storage/exports.py: indexed immutable manifest/JSONL exports with typed selection, units/normalization, conditions/lineage; native archives unchanged. Alias-only resolution checks producer indexes/hashes and snapshots bytes; no MCP-to-MCP calls or automatic simulation.
- src/case/: typed pending actions and atomic artifact/progress/resolution. Coordinator owns intent/key, job ID and reconciliation; failed links never recompute. Older revisions remain readable; attachment never completes a case or cancels shared jobs.
- One independent postprocessing.lock/job per runtime; durable startup ownership gate, Windows kill-on-close jobs, timeout/cancel/descendant cleanup and recovery. Ambiguous living ownership remains RECOVERY_REQUIRED. Queue/read/cancel are independent of method execution. POSIX support is implemented but unverified.
- Retain runtime/methods/, postprocessing.sqlite and analyses/<job_id>/attempts/<attempt_id>/. Six verification jobs: three previous-environment, three current; one test each. Renderer bundles only an import/version smoke check; regressions cover actual rendering. All methods remain experimental.

Current verification IDs: summary d51418a1b0c44b2f9e7a0298abdc1fb9; comparison 3f4f1eb755c84c78bed9f60c4ab19a2d; renderer 5689bd6d50da47b48f90533efc6cba5a. Manifests/reports: runtime/analyses/, indexed by postprocessing.sqlite.

## Latest evidence

| Check (2026-10-16; native gates 0) | Observed result |
|---|---|
| Component suite | 125 tests / 12.250 s: 120 passed, five native skipped; includes 27 Case tests and captured SR/TRIM export checks. |
| Analysis suite | 19 passed / 24.383 s: all thirteen tools over live stdio/fresh CLI, numerics, reuse/concurrency, corruption, Case recovery, timeout/cancel, actual supervisor death/descendant termination and budgets. |
| Rendering | Overlay/separate/shared panels, PNG/SVG validation, deterministic recomputation, plot reuse and failure isolation passed; PNG labels/units/source IDs/gaps visually inspected. |
| Setup/registration | Plotting extra/editable install and pip check passed. Protected local registration preserves Case/SRIM. Configured fresh launch lists 13 tools and three CHECKED v1 methods. Desktop Analysis discovery/mutations still require reload/verification. |
| Integrity | Original installation 219 files, SR fixtures four files, TRIM fixtures 450 files and production metadata.sqlite hash unchanged against this turn's baseline. No native simulations ran. |

Delivery children exited; desktop MCP services are app-managed. Disposable checks were cleaned; application archives stay. Case desktop discovery/list/search passed; desktop mutations and SRIM client freshness remain unverified.

## Persistent constraints and deferred work

- Follow AGENTS.md, required .venv, component test layout and native gates. Preserve fixtures/application audits/history; no permanent examples/probes/reports. runtime/cache/development-tests/ is allowed for disposable checks. No further tests are requested now.
- Preserve D:\local\srim\srim_2013; derive paths from configs/application.toml. Runtime ../runtime, native timeout 30 s/one attempt. Workers remain symmetric workers/worker-1/{sr_module,trim}/ siblings with shared .lock/active_attempt.json; one backend per worker. No project-local simulator duplicate/shared worker pool.
- Coordinator owns reasoning/case writes; optional subagents are bounded/read-only evidence consumers. Memory is conceptual persistence; catalog before RAG. Improvement promotion requires specific-version human approval and retained rollback/evidence. Map/labels remain in checklist; draw only when requested, one relationship at a time.
- Expanded acceptance, scientific reference/accuracy/convergence, workflow/Memory/improvement systems, case-wide enforcement, deletion/reset, clean rebuild, CI and backup/restore are deferred. Deletion design retains tombstones and blocks live references/jobs; no deletion/reset implementation is authorized. No automatic skill/workflow promotion.
- Native historical evidence remains 2026-10-15: five methods/34 CHECKED calculations (12 SR/22 TRIM), corrected owner-marker focused rerun; no second full native suite. Native hard-crash/PID gap, timeout/cancel/retry recovery, service/session-0 and science remain unverified; Analysis process checks do not close native gaps.
- GUI modes 6/7/8 conflict with Input PDF p.8-17. Mode-6 collision warning cause unknown: keep off; never substitute mode 7. Mode-8 collisions=1 requires summary/every choice and provenance. Three 500-eV events matching both choices do not generalize; recoil input does not transport external neutrons/electrons/photons.
- Withdrawn SR grid-completeness probes stay withdrawn; TRIM detail completeness unchanged. No optimizer/ion count/scientific defaults selected. Preserve Git checkpoints: no amend/rebase/force push/history deletion; local rule/app reload limits remain.
- User reports 475000-token context; notify/save/pause before approximately 427500, using an explicit estimate. No exact live counter; never initiate compaction; preserve stops after automatic compaction.
