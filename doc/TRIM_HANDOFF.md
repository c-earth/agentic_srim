# TRIM investigation handoff — 2026-10-21

The durable record is [TRIM input defaults](reference/trim-input-defaults.md), including the complete [92-element CSV](reference/trim-elemental-defaults.csv), reconstructed lookup rules, sources and coverage histogram. It expands the narrower 2026-10-20 default findings in `TRIM_FINDINGS.txt`; older transport/dependency findings remain separate.

## Current state

All 92 elemental triples agree across 2008, 2013 Standard and Professional. Elemental phase checks completed across all three. The final compound audit is **2013 Standard only**: 64 unique successful contexts, 404 retained/fresh observations, 53 represented elements and no energy discrepancies. The other 39 elements have no compound preset, but have elemental baseline/phase evidence. Forty-nine represented elements remain below 20 Standard compound observations.

All queues are stopped, owned executables closed and copied dictionaries restored; original installation hashes remained unchanged. Ordinary compounds occupy a whole initially empty layer. The known water chemistry prompt has an explicitly approved exact-match Yes policy; unknown warnings stop the case.

## Pending work

- Complete unfinished mode, random-mixture, incident-ion, compound and compound-phase matrices if requested. Preserve Standard-only scope unless changed by the user. Do not restart completed elemental baselines.
- Resolve incomplete UI captures and confirmation/identity failures before interpreting them as scientific differences. Manual-order blank-row diagnostics did not establish exported-input or transport behavior.
- A controlled ATOMDATA change could distinguish dynamic lookup from an embedded or cached equivalent. This has not been performed. The zero-to-2 eV surface rule matches all installed data, but native causality and a published general statement remain unverified.
- Production integration is separate work. Preserve per-entry overrides and different-layer entries; displayed defaults are not universal physical parameters.

## Local evidence inventory

These paths are under ignored `.cache/trim-default-checks/`; they are not committed and may disappear during cache cleanup.

| File or directory | Purpose |
| --- | --- |
| `phase-and-compound-progress.json` | All three baseline vectors and earlier completed phase/compound results |
| `atomdata-sublimation-comparison.json` | All native sublimation rows, hashes and surface comparisons |
| `standard-targeted-conclusion.json` | Final deduplicated Standard counts, audit failures and source-run references |
| `standard-targeted-audited-rows.json` | Final 404 retained/fresh observations |
| `twenty-minute-snapshot.json`, `timed-stop.json`, `low-count-frozen-audit.json` | Broad-run cutoff and frozen audit |
| `targeted-scope-stop.json`, `low-count-std-only-plan.json` | Scope narrowing and continuation |
| `active-run.json` | Last queue state; no active investigation queue |
| `runs/` | Raw manifests, actions, fields, popup and cleanup/restoration records |
| `element_context.py`, `compound_defaults.py`, `phase_defaults.py`, `common.py` | Standalone local research runners and shared implementation |

Runners support explicit single-preset isolation and exact-success resume with source-hash validation. Inspect the current CLI and manifests before continuing; interrupted results are not successful resume candidates. `runs/phase-retry-20261021T103103` is the separately retained titanium retry and lacks the standard source-hash manifest schema.

The committed table, reference and figure preserve the final findings without the cache. No production implementation code changed in this documentation task.
