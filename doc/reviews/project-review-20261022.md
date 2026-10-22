# Project review — 2026-10-22

Reviewed base commit `14e3162`, starting from a clean working tree. The review covered all tracked Python modules, seven test directories, current documentation and static resources, and every preexisting top-level `.cache/` entry. Three delegated reviewers independently examined TRIM, the remaining code, and documentation/cache; the primary reviewer ran the suites, checked repository hygiene, and reconciled the findings.

**Result:** all **354 tests passed with warnings treated as errors**. Seven additional behavioral defects were confirmed by controlled probes; two resource-bound concerns were verified separately. Existing tests do not cover those failure cases. Documentation corrections were applied; executable code and tests were not changed. No native SR/TRIM calculation, dependency installation or Git commit was performed. After the review, the user authorized the cache cleanup recorded below; all flagged files were removed and preservation checks passed.

Detailed local evidence is under `.cache/project-review-20261022/`. Those files are ignored local artifacts, not a durable backup. Paths below are repository-relative. This report preserves conclusions if the auxiliary evidence is later archived or removed.

## Confirmed code findings

Priorities: P2 is an actionable correctness/reliability defect; P3 is a lower-impact reporting defect. The probes demonstrate what the APIs accept, without claiming that an ordinary unmodified native run naturally produces those inputs. Corrections require a separately approved complete implementation proposal under [AGENTS.md](../../AGENTS.md).

### R1 · P2 · Impossible TRIM summary counts can finalize as succeeded

Source: [trim_outputs.py](../../src/srim/trim_outputs.py), lines 104–108 and 441.

Summary outcome counts are individually parsed but are not bounded by calculated particles or reconciled with checkpoint outcome counts. A frozen five-ion fixture was changed only from transmitted `0` to `999`. The real `run_trim_simulation` path still finalized `succeeded`, with `failure=null`, no parser issues and no saved-value differences; the unchanged checkpoint recorded transmitted `0`.

Only the native execution boundary was replaced with a frozen-output producer. Preparation verification, runtime preservation, parsing, comparisons and terminal publication ran normally. Existing saved-count tests exercise checkpoint current/total counts rather than these outcome invariants.

Suggested correction boundary: define mode-aware outcome-count invariants and summary/checkpoint consistency rules, then reject contradictory evidence before successful finalization. Add a regression preserving the five-ion/999-transmitted trigger. Avoid assuming a universal partition formula without checking native mode semantics.

Evidence: `.cache/project-review-20261022/trim/full-runner-probes.json`.

### R2 · P2 · Wrong TRIM target chemistry passes consistency checks

Source: [trim_outputs.py](../../src/srim/trim_outputs.py), lines 441, 454–459 and 468–472.

For an explicit silicon request with an unchanged silicon checkpoint, changing the summary composition from `Si=100%/100%` to `Au=1%/1%` still finalized `succeeded`. Separately, changing target-energy species or a RANGE target column from Si to Au returned `complete=true`, `issues=[]`. The parser preserves printed identities but does not fully reconcile them with effective inputs and checkpoint identities.

Existing multielement fixtures verify positional column descriptors; they do not reject incorrect printed species or composition. A stale or inconsistent native file can consequently be accepted alongside a valid checkpoint.

Suggested correction boundary: compare summary species, meaningful composition totals/proportions, target-energy species and nonaggregate profile symbols with the effective ordered atom entries. Specify printed-precision tolerances and retain quick-mode aggregate/repeated-species distinctions before implementation.

Evidence: `trim/full-runner-probes.json` and `trim/parser-probes.json` under the local review directory.

### R3 · P2 · History availability can be reported without numerical history data

Source: [trim_dialogs.py](../../src/srim/trim_dialogs.py), lines 216–220.

`assess_trim_histories` relies on particle markers and a small set of phrases. A 1,278-byte synthetic file containing `For Ion 1..100` and four marker phrases, without collision/recoil numerical tables, was accepted as detailed history `available`. Removing numerical rows while retaining markers in a committed history fixture gives the same result.

This does not assert that core physics is invalid. It means an explicitly requested optional-history completeness check can certify an artifact without its claimed contents. Existing truncation tests remove coverage markers and miss this case.

Suggested correction boundary: define structural content requirements for each supported native history layout, or qualify the status as marker/coverage detection until such validation exists. Regressions should retain markers while removing or corrupting the table contents.

Evidence: `.cache/project-review-20261022/trim/history-probes.json`.

### R4 · P2 · Checkpoint recovery accepts negative non-energy quantities

Source: [trim_recovery.py](../../src/srim/trim_recovery.py), lines 245–256.

A valid-format VACANCY Variant vector containing one `Single(-1)` and 199 Empty records decoded successfully. `parse_trim_checkpoint_results` returned `complete=true`, no issues and `-0.01` vacancies per incident ion. Shape and finiteness checks pass, but profile-domain checks are missing. The native text parser already rejects negative non-energy profiles.

Suggested correction boundary: apply quantity-specific domain validation to recovered non-energy profiles. Preserve intentionally signed recoil-energy values and the separately documented lateral-variance roundoff policy. Add equivalent negative-domain regression coverage to recovery.

Evidence: `.cache/project-review-20261022/trim/parser-probes.json`.

### R5 · P2 · Interrupted MCP job admission can prevent server restart

Source: [jobs.py](../../src/mcp/common/jobs.py), lines 151–153; restart reader at line 73.

Submission creates a visible job directory, publishes `request.json`, then publishes `state.json`. A controlled initial-state publication `OSError` left a request-only directory before acceptance was returned. A fresh `JobManager.start()` then raised `FileNotFoundError` on the missing state file and prevented the server from starting.

Retention of partial evidence is appropriate; allowing one incomplete admission to disable unrelated jobs is the defect. The approved MCP contract calls for atomic acceptance. Current FIFO/idempotency/restart tests do not inject failures between the initial publication steps.

Suggested correction boundary: publish a complete staged admission atomically and explicitly reconcile retained incomplete admissions during startup. Preserve quarantining/no-automatic-reexecution rules. Test each publication boundary and verify unrelated accepted jobs remain inspectable after restart.

Evidence: `.cache/project-review-20261022/platform/probes.json`.

### R6 · P2 · Analysis completion accepts diagnostic artifacts as code evidence

Source: [analysis/records.py](../../src/analysis/records.py), lines 171–175.

Completion checks declared code artifact names against attached names, without checking artifact roles. Actual create/attach/finish calls completed an ad-hoc analysis whose only artifact had role `diagnostic`, while `method.code_artifacts=['declared-code.txt']` referred to that artifact. No domain I/O was mocked in this probe.

The approved project contract describes attached code artifacts and meaningful ad-hoc completion evidence; diagnostic-only content does not satisfy those stated roles. This is a provenance-status defect, separate from evaluating the scientific merit of an analysis.

Suggested correction boundary: resolve the allowed role policy explicitly, require declared code references to point to the selected code role, and define ad-hoc terminal-evidence requirements. Test diagnostic-only completion, mismatched declared roles and valid completion.

Evidence: `.cache/project-review-20261022/platform/probes.json`.

### R7 · P3 · Partial project publications disappear from listings

Source: [project_records.py](../../src/backend/project_records.py), lines 136–138.

An injected first current-pointer publication failure left an immutable `revisions/r000001.json` in a case allocation. `list_records` returned `total=0`, `matches=[]`, `issues=[]` because it silently skips allocations without `current.json`. The approved contract promises pending-artifact reporting for unpointed revisions/partial staging.

Suggested correction boundary: report incomplete initialized allocations and pending revisions without guessing current state from the largest filename. Keep untouched raw allocations distinguishable. Test both initial pointer failure and a later revision-pointer failure.

Evidence: `.cache/project-review-20261022/platform/pending-record-probe.json`.

## Verified resource-bound concerns

These are separate from the seven behavioral defects. No deliberate memory exhaustion, extreme stress test or arbitrary-file escape was attempted.

| ID | Source | Verified behavior | Suggested design direction |
| --- | --- | --- | --- |
| C1 · P2 | [knowledge/schema.py](../../src/knowledge/schema.py), lines 204, 264, 307, 362–366 | The accepted nine-character value `1e1000000` expands to 1,000,001 fixed-point characters. Exact `Fraction` operations also scale with exponent magnitude. Request byte caps do not bound this work. | Bound representation/operation complexity or avoid giant fixed-point intermediates. Keep this separate from physical input limits. |
| C2 · P2 | [plan/catalog.py](../../src/plan/catalog.py), lines 178–182 and 271 | Candidate evidence checks its claimed size, then reads the current source in full before comparing actual size/hash. A small claimed size does not bound allocation for a large changed/forged source. Source-confirmed; no large-file stress run. | Stream with an actual-byte cap and stable identity checks before whole-file allocation; test an oversized actual file with a small declared size. |

## Test results and environment diagnosis

All commands used `D:\projects\agentic_srim\.venv\Scripts\python.exe`, Python 3.13.13, with `-W error`.

| Suite | Tests | Result | Unittest time |
| --- | ---: | --- | ---: |
| backend | 42 | passed | 3.067 s |
| srim | 273 | passed | 607.088 s |
| knowledge | 19 | passed | 13.579 s |
| case | 2 | passed | 0.306 s |
| analysis | 2 | passed | 0.262 s |
| plan | 2 | passed | 0.175 s |
| mcp | 14 | passed outside sandbox | 16.362 s |
| **Total** | **354** | **all passed** | |

Suites initially ran in independent processes with at most three suites concurrent. No tracked file changed during that run. The sandboxed MCP attempt stalled before test setup while Windows asyncio initialized its event loop: faulthandler showed `socket._fallback_socketpair` blocked in `accept`. It was not a project assertion failure or an SDK shutdown diagnosis. A bounded socketpair check passed outside the sandbox, then all 14 MCP tests passed there. The original blocked MCP process tree was identity-checked and stopped; its incomplete log and the diagnostic remain intact. The initial runner result is preserved separately from the reconciled passing result.

`pip check` passed. Installed MCP/jsonschema versions match `requirements-mcp.txt`: 2.3.0 and 4.26.0. No dependency was installed or upgraded. Native execution was mocked or replaced with committed frozen fixtures; these results do not establish independent scientific validity or all-mode native compatibility.

Evidence: `.cache/project-review-20261022/tests/{final-results.json,results.json,mcp-outside-sandbox.json,mcp-protocol-diagnostic.log}` and individual suite logs. The outside-sandbox record retains the final actual tool-output excerpt and measured unittest result.

## Documentation and static-file coherence

Applied 14 verified replacements across eight existing files, plus navigation to this report:

- [README](../../README.md): all seven test families and commands; report-only default versus explicit reviewed TRIM policies; removal of stale-guide supersedence wording.
- [First SR calculation](../guides/getting-started.md): optional MCP dependency exception and active TRIM visibility setting.
- [TRIM input guide](../guides/trim-inputs.md) and [input reference](../reference/trim-input-format.md): implemented execution/monitoring/queue/resume links replace future-runner claims.
- [API reference](../reference/api.md) and the comment in [settings.toml](../../settings.toml): popup wording now identifies report-only behavior. Configuration values are unchanged.
- [TRIM validation](../reference/validation.md#trim-native-checks): checkpoint integration is complete. [MCP guide](../guides/mcp-servers.md): delivery commit is `14e3162`.

Historical approved proposals and original failure records remain unchanged. Earlier test totals and superseded design notes retain their historical context. Known implementation gaps are recorded above rather than silently described as corrected.

Checks passed:

- 108 tracked Python files parsed; every module/class/function has its required docstring, every function argument/return is annotated, and no code comment tokens were found. This is a structural check, not a claim that every docstring is semantically exhaustive.
- All 40 tracked JSON and five TOML files parsed; JSON checks also rejected duplicate keys and nonfinite tokens. There were no violations.
- All 39 fixture manifests and 878 listed files matched recorded SHA-256 hashes and sizes.
- Runtime damage seed, CSV and Markdown tables agree across all 92 rows. The surface-rule audit matched 72 positive sublimation entries and 20 zero-to-2 eV entries.
- All 22 README/current-guide Python snippets parsed; checked project imports and directly imported function keywords matched the source AST. Examples were not run against a native installation.
- Local documentation links/anchors and code fences passed before remediation and were rechecked after editing. External websites were not revalidated.

## Revised cache deletion rule — user amendment

After the initial review, the user clarified on 2026-10-22: **anything recoverable by running SRIM/TRIM is considered deletable**. This supersedes the original cache KEEP decisions based only on preserving original native/scientific evidence. Exact historical bytes and process timings are not required for an output to qualify as regenerable.

The approved deletion selection contained **99,021 files / 3,900,433,019 bytes (3.63 GiB; 91.7% of the original cache bytes)**. About 335.66 MiB remains unselected for replay inputs, unique records, remaining source differences and unrelated research/figures. The selected total includes installation rebuilds and GUI recaptures, shown separately from transport simulation outputs in the revised register.

The revised register is `.cache/project-review-20261022/cache-reclassification/report.md`, with the exact selected paths in `deletable-files.jsonl` and totals in `summary.json` beside it. It separates native result/checkpoint regeneration, SRIM/TIN GUI recapture, reconstructible installation support and verified retained duplicates. Original recipes, requests, events, configuration, unique scripts, custom tables, human notes and approval records remain available. A continuation may require a fresh parent run before its checkpoint can be regenerated.

The user then explicitly requested removal of the flagged files. Cleanup removed all **99,021 selected files / 3,900,433,019 logical bytes (3.63 GiB)**, plus the flagged empty MCP test directory, with no deletion failures. A complete original-family scan confirmed every selected file absent and all **25,658 retained preflight files** unchanged in size, last-write time and file identity. Source, test and tracked fixture hashes match the pre-review baseline. No recursive directory deletion or native run was performed; root permanent project storage is outside this cleanup. The execution report is `.cache/project-review-20261022/cache-reclassification/cleanup-execution/20261022T183736Z-2656796d/report.md`. The initial inventory and former conservative classifications below are historical audit evidence.

## Initial cache cleanup register — superseded retention policy

The preexisting-cache snapshot contained **38 top-level entries, 124,678 regular files and 4,252,395,107 logical bytes (3.96 GiB)**, excluding this review's outputs. Temporary test workspaces can change during execution. No symlink/junction/reparse point was found in the audited cache entries. No cache contents were deleted.

The complete per-entry classifications, exact selected paths, reasons and retained-copy references are in `.cache/project-review-20261022/docs-cache/report.md`, `annotated-cache-inventory.json` and `cleanup-candidates.json`.

| Classification | Selected size | What is flagged |
| --- | ---: | --- |
| Regenerable, after confirming no active use | **14.36 MiB**, 245 files across 22 nonoverlapping paths | Completed one-off transformation/style helpers; synthetic TRIM test residue; empty MCP test-workspace directory; two bytecode directories; headless preview browser profile; eleven PDF QA renders. |
| Conditional | **54.88 MiB**, 37 paths | 33 legacy figure files with exact retained-package matches, plus four source/test-checkout families needing Git reconstruction and unique-delta checks. |
| Keep | Remaining native/scientific/approval/unfinished evidence | Current checkpoint-first attempts, original failures, native matrices and controls, figure packages and unique figures, paused research, approvals and workflow records. |

Specific boundaries:

- `.cache/trim-default-checks/` is the largest family at 2,179,617,423 bytes. It remains **KEEP**: unfinished matrices and original controls/source spans support qualified conclusions. A resolved implementation issue does not make its only scientific evidence disposable.
- Preserve `.cache/trim-sav-live/`, the broad popup/input/execution/dependency families, CAB evidence and native worker benchmarks. A synthetic test cannot recreate those original native bytes.
- Conditional source snapshots: `.cache/timeout-commit-snapshot/`, `.cache/trim-commit/`, `.cache/trim-recovery/` and `.cache/repo-review-c-earth/`. Their deletion requires recoverable commit/source verification, retention of unique patches/reports and no active use. They were not proven fully reconstructible during this audit.
- Preserve `.cache/scientific-figures/packages/` and archive verification when considering proven legacy duplicates. `.cache/figure-self-improvement/fig1.png` has no packaged match and stays **KEEP**.
- The generated `.cache/trim-energy-defaults-20261020/chrome-preview/` profile is regenerable; original element captures, data, plot artifacts and paused interaction-verification context remain.
- Historical abandoned-branch helpers are not runnable next steps. No export/chooser experiment or restoration is proposed by this review.
- `.cache/workflow-improvement/` retains immutable evidence and decisions under its protocol; do not automatically clear it.

## General coding and filesystem recommendations

1. Address R5 admission/restart reliability and R1/R2 result-integrity acceptance before expanding automation. Treat R3/R4/R6 as separate, explicitly scoped contracts; preserve signed-energy and native-mode qualifications.
2. Add focused failure-boundary regressions for these demonstrated defects. Existing new case/analysis/plan coverage is concentrated in two test methods per family; the total passing count should not substitute for interruption/concurrency/role-validation coverage. No coverage percentage was measured.
3. Propose one discoverable command/CI job that runs all seven namespace test directories with warnings as errors and a bounded process deadline. Top-level unittest discovery alone misses these suites. Transport-capable execution needs the verified Windows loopback prerequisite.
4. Keep current guides and references authoritative, and label historical handoffs/proposals at entry points. Update the owning guide when behavior changes instead of adding a supersedence disclaimer elsewhere. A reusable documentation checker or test runner is an implementation/workflow proposal, not part of this documentation-only remediation.
5. Apply the revised user-directed regeneration rule above when selecting cache cleanup. Original native evidence is eligible when its functional contents can be regenerated. Keep the replay inputs and unique non-simulation records, and use the exact-path register rather than deleting mixed directories wholesale; no automatic garbage collector exists.
6. Keep `memory/NERSC_TRIM_20k/` untouched. It contains roughly 648 MiB of ignored downloaded data in the filesystem snapshot; migration/import remains deferred. Do not confuse it with cache or project-owned native simulation records.
7. The pre-remediation filesystem scan found no unignored untracked files, no links/reparse points in `src`, `test`, `memory`, `case` or `worker`, and no root `tmp/` or `output/`. Existing ignored Python bytecode is regenerable. `case/` and `worker/` contain only their tracked placeholders; generated MCP coordination files are not proof of a running server and should not be removed blindly. This review adds the present report as a new documentation file.
8. Measure checkpoint polling and fixture setup costs on a representative workload before optimizing them. Repeated full checkpoint hashing/decoding and the 607-second SRIM test suite are observations; no attributable performance improvement or representative TRIM scaling conclusion was established here.

## Remaining qualifications

Some required checkpoint blobs (including STOPPING/SPUTTER and 2013 3D files) are retained and hashed but not decoded by the recovery routines. Their presence is not complete structural validation of all native state. Resumed optional recoil-history coverage has no equivalent live acceptance case in the current two-mode continuation matrix. Stable hashes establish byte identity, not atomic native publication or independently validated physics.

The four original findings in [the earlier correctness review](../reference/validation.md#resolved-correctness-review) remain resolved and are not repeated here. The active [checkpoint-first direction](../decisions/trim-continuation.md) is preserved. No abandoned export/chooser branch was revived.

The repository self-improvement skill was consulted for closeout. No trial, adoption, workflow change or extra benchmark was performed; any new observation is local under its prescribed event storage. Follow-up code changes require the complete per-file/signature/schema/verification proposal and explicit approval required by AGENTS.md.
