# Workspace review 2026 10 25

The review of commit `360e1fd` found five runtime defects and one outdated test
assertion. These findings remain open; the subsequent workspace cleanup and
documentation edits do not fix them. Stored database content was excluded;
database and search implementation was included.

## Open findings

| Priority | Finding and location | Required correction |
| --- | --- | --- |
| P1 | [CAB capture](../../src/srim/cab/recovery.py), lines 565–566, can finalize a failed record while native shutdown is unconfirmed. A synthetic surviving process lost its durable PID; closeout reported completion and runtime cleanup was allowed. | Persist launch identity immediately; require verified stop evidence and block cleanup/reuse while ownership is unresolved. |
| P2 | [External TAR readers](../../src/knowledge/external_sources.py), lines 265–271 and 586–590, accept a regular member with size `-1` and request `read(-1)`, bypassing chunk limits. | Reject negative effective sizes before payload processing in both readers; test malformed GNU/base-256 headers. |
| P2 | [Search validation](../../src/knowledge/schema.py), lines 148–151 and 180–184, rejects signed seeds and incidence angles accepted by TRIM. Sealed synthetic records with seed `-1` or angle `-10` failed indexing. | Align record and query validation with the accepted TRIM input domain, including seed query validation at lines 235–236. |
| P2 | [CAB setup](../../src/srim/cab/recovery.py), lines 519–530, marks a run started before fallible hashing/log setup enters its finalization handler. | Protect all post-start work or perform setup before marking the run running; test hash, log-open and synchronization failures. |
| P2 | [MCP shutdown](../../src/mcp/common/jobs.py), lines 400–405, can skip notification, joins and lease release when cancellation journal publication raises. | Finish owned shutdown despite publication errors; cover queued and cooperative jobs. |
| P2 | [Queue failure-isolation test](../../test/srim/test_queue.py), line 154, expects a retained worker although all three terminal runs have sealed permanent runtime copies. | Expect one cleaned worker and zero retained workers; assert permanent evidence and inventory preservation. |

These are proposed corrections, not implemented fixes. CAB staging retention is
an existing documented policy, not an additional defect. No broad refactor is
required for the workspace cleanup.

## Verification

Across reconciled runs, **478 of 479 distinct tests passed**. The remaining
failure was the queue assertion above, reproduced independently. The controlled
check verified all three permanent runtime trees and their sealed inventories.

| Suite | Result |
| --- | --- |
| TRIM | 167 passed |
| Other SRIM | 134 passed, 1 failed |
| Backend | 45 passed |
| Knowledge | 69 passed after the hardlink-dependent test was rerun outside the sandbox |
| Case, analysis, plan | 2, 4 and 8 passed respectively |
| MCP | 49 passed outside the sandbox after subprocess/transport testing stalled inside it |

The review checked 242 local Markdown destinations/anchors across 33 files and
found no broken link. Installed dependency consistency passed `pip check`.
Tests and fault probes used synthetic data and controlled native substitutes;
no new native scientific calculation or real database import/reindex was run.
These results describe the reviewed snapshot, not independent scientific
validation or an all-tests-pass current tree.

## Retained review evidence

The exact original report, reproduction scripts/results and pre-edit reference
pages are preserved locally in
`.local/migrations/workspace-cleanup-2026-10-25/documentation-and-review-evidence.zip`.
The sibling `manifest.json` maps original paths to archive members with SHA-256
hashes. This ignored archive is local evidence, not a durable backup. It does
not restore the older missing TRIM/defaults investigation artifacts.

The cleanup removes disposable simulation records, worker runtime copies,
terminal MCP job history and scratch files. Databases, configured resources,
source, tests, the Python environment and protected local state remain.
Unmodified native indexes may retain references to the removed runs; resolving
those entries requires a separate database maintenance scope.
