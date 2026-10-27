# Verification, native coverage and limits

[Documentation index](../README.md) · [TRIM continuation policy](../decisions/trim-continuation.md)

## Software checks

Run all six test directories explicitly; top-level discovery alone does not discover these namespace-directory tests. The MCP suite includes real local stdio transport and needs Windows loopback access for its asyncio event loop:

```powershell
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' -W error -m unittest discover -s test/backend -p 'test_*.py'
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' -W error -m unittest discover -s test/srim -p 'test_*.py'
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' -W error -m unittest discover -s test/knowledge -p 'test_*.py'
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' -W error -m unittest discover -s test/case -p 'test_*.py'
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' -W error -m unittest discover -s test/analysis -p 'test_*.py'
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' -W error -m unittest discover -s test/mcp -p 'test_*.py'
```

Tests use synthetic installations and controlled native-process substitutes; passing tests do not establish universal numerical accuracy or SRIM compatibility. Recorded native checks cover representative installations and cases, not every element, mode, or checkpoint scenario. Interactive dialogs and event-mode limitations can prevent unattended completion. Hash verification proves byte identity and provenance, not physical correctness or atomic capture of a live external process.

Recorded test counts describe their tested snapshots. Run the families relevant
to a change and report their actual results; passing tests does not establish
universal correctness.

The [2026-10-25 workspace review](../reviews/2026-10-25-workspace-review.md)
reconciled 478 passing tests out of 479 across the reviewed snapshot and required
sandbox reruns. One stale queue-cleanup assertion failed; the suite was not fully
green. The review records unresolved defects separately from test coverage and
does not establish independent scientific validity.

The unified/external retrieval release check passed 177 tests across backend
(45), knowledge (69), case (2), analysis (4), plan (8) and MCP (49), with warnings
treated as errors. These were synthetic software/stdio checks; no new native
simulation, full-source preview, import or performance benchmark was run for
release finalization. The source/index verification measurements below retain
their stated dataset and machine scope.

Numeric search regressions cover decimal-string length and exponent boundaries,
unit shifts, query composition, SearchRecord quantities, rejection before exact
arithmetic, and real MCP endpoint responses. The exact limits and historical
projection compatibility are specified in the
[input-search reference](input-search-format.md#numeric-processing-limits). The C1
closeout passed 22 knowledge and 21 MCP tests with warnings treated as errors;
these are results from that development working-tree snapshot. No native
calculation or exhaustion benchmark was performed.

## CAB native checks

The completed 2026-10-20 checks exercised fresh native recovery on SRIM-2008 and
2013 Standard: all 18 bonds, carbon across 18 estimates, methane record 92, and
44 successful capture cleanups per installation. Bond calculations, carbon
estimates and methane parsed outputs matched between the two versions. All eight
installation input hashes remained unchanged. This covers those workloads, not
all 91 cores or general cross-version equivalence.

For the methane case at 126 keV, electronic/nuclear stopping was `7.226E+00` /
`9.171E-03` eV/(1E15 atoms/cm2). The historical carbon mean was
`6.268410214222222222222222222`, spread `0.005650190`, and CAB factor
`1.004815621499447362865268308`. Native-import checks recorded 345 compounds for
2008 and 357 for 2013 Standard. Known import normalization is documented in the
[resource guide](../guides/resources-and-cab.md).

The earlier five-second GUI cleanup timeout did not reproduce in the fresh full
runs; its cause remains unconfirmed. Earlier raw artifacts were cleared on
2026-10-20. Subsequent fresh evidence was recorded under
`.cache/fresh-recovery-both-9p7q5ne2/verification.json`; historical paths identify
the original evidence location and do not guarantee current local availability.

## TRIM native checks

| Area | Recorded coverage | Boundary |
| --- | --- | --- |
| Fresh input/runtime investigation | Fifteen 2008/2013 Standard version/mode pairs passed full versus reduced Data-bundle comparisons. | This dependency check does not establish unattended completion for all modes. |
| Core outputs and optional histories | Thirty-five isolated UI-assisted runs covered 23 version/mode combinations across 2008, Standard and Pro. | Optional history behavior varies; UI-assisted completion does not establish every production automation path. |
| Offline checkpoint recovery | Twenty-three native fixture combinations cover seven 2008 modes and eight modes in each 2013 edition. | Recovered values retain stored-precision, unavailable-statistic and inferred-layout qualifications. |
| Checkpoint-first continuation | Twelve final scenarios in fourteen attempts cover ion-full and specified-ion-full on three installations. | Only these two modes/workloads were exercised; the two initial 2008 prompt failures remain failed records. |

In the final continuation matrix, all six final-autosave cases succeeded without
completion or Save commands. Three ordinary missing-autosave cases succeeded with
one Yes/default-save pair each. Three event missing-autosave cases failed
`checkpoint-required` without commands or chooser interaction. Successful
controlled stops verified an empty owned job, exact requested counts and matching
live/preserved/direct-recovery results. Parent records, installations and settings
were unchanged in the recorded checks.

The concise aggregate was recorded at
`.cache/trim-sav-live/checkpoint-first-verification.json`. That aggregate and its
original source notes are unavailable in this workspace; no corresponding
historical documentation archive is identifiable in the local migration
inventory. Check [the active decision](../decisions/trim-continuation.md) and
[execution reference](trim-execution-format.md) for current behavior.

The [recovery guide](../guides/trim-checkpoint-recovery.md#precision-and-validation)
retains measured cell discrepancies and the continued-versus-uninterrupted
comparison. Some saved blobs are preserved and hashed but not decoded. Hashes
establish byte identity, not atomic native publication, complete structural
validation of every saved field, or independent scientific accuracy.
Resumed optional recoil-history coverage has no equivalent live acceptance case
in the two-mode continuation matrix.

## Native input investigations

Earlier TIN/TRIM format and compound comparisons are historical investigations.
The useful operational contracts are maintained in the
[TRIM input reference](trim-input-format.md),
[TRIM defaults and complete table](trim-input-defaults.md),
[SR data formats](data-formats.md), and
[native input-search reference](input-search-format.md). Research notes do not
authorize new native runs or establish material-specific scientific parameters.

## Resolved correctness review

The four 2026-10-20 non-TRIM findings were closed on 2026-10-22: prepublication
elemental dependency identity, active-generation checks before missing CAB
work/captures, complete shared correction progress, and printed-precision SR
range containment. The recorded closeout passed 333 tests with warnings treated
as errors: 41 backend, 273 SRIM and 19 knowledge. It performed no new native run.
Those totals are historical closeout evidence, not a new check of the current
tree.

## Resolved reliability review

The subsequent development review closed four retained findings:

- MCP initial admission publishes the complete request/state directory before
  scheduling. Recognized partial admissions are retained and reported without
  replay; uncertain legacy request IDs and explicit workers stay reserved.
- Completed analyses require each declared code_artifacts attachment to have
  role code or method, preserving written procedures as valid method evidence.
- Case/analysis listings report recognizable initial publications missing their
  current pointer without repairing or promoting a revision.
- Input search bounds decimal-string length and original/converted scaling
  exponents before exact arithmetic or fixed-point expansion.

The first three fixes passed 70 tests with warnings treated as errors: backend
44, analysis 4, case 2 and MCP 20. The subsequent numeric-limit fix passed 43:
knowledge 22 and MCP 21. These are separate implementation snapshots with
overlapping MCP coverage, not a combined full-suite result. Fourteen regression
tests were added in total. No native simulation or exhaustion benchmark was run.

The current contracts and retained recovery/compatibility boundaries are in the
[MCP reference](mcp-format.md), [project guide](../guides/project-management.md)
and [input-search reference](input-search-format.md#numeric-processing-limits).


## Unified retrieval checks

The knowledge tests cover closed queries, exact numeric conversions, same-row
output witnesses, signed profile values, explicit not-calculated availability,
SR list/range projections, native and recovered TRIM fixture profiles, source
tampering, output byte caps, current/history revisions, missed notifications,
reconciliation digest conflicts, idempotent updates and publication after source
unlock. Interleaved publication tests exercise short catalog locks and captured
generation membership. Analysis lifecycle tests keep intact findings discoverable
when an attached artifact is missing.

MCP adapter/catalog checks cover the five retrieval tools, schema discovery,
response caps and cancellation. These are synthetic software and preserved-fixture
checks; they do not establish independent scientific validity or large-collection
performance. No backfill or new native calculation is part of this verification.

## External database discovery verification

The synthetic foundation suite is `test/knowledge/test_database_sources.py` and
`test/knowledge/test_database_adapters.py`, with registration cases in
`test/backend/test_config.py` and tool/schema/stdio cases in `test/mcp/`.
It checks dynamic registration/revocation, missing paths, prefix integrity,
packaging, unsafe members, read budgets, recognition states and no pool/source
mutation. Fixtures are neutral and cache-owned. SQLite checks recognize its
magic header only. MCP tests verify read/result caps and unknown-source discovery
following live settings edits without restarting that test server.

Use the commands above for the focused backend/knowledge/MCP suites. Validate
the repository skill with:

```powershell
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' 'C:\Users\Tokisaki963\.codex\skills\.system\skill-creator\scripts\quick_validate.py' '.agents/skills/external-database-onboarding'
```

These are software checks of registration and bounded inspection. They do not
by themselves validate an external scientific mapping, import, whole archive
integrity or search over external records. Live onboarding must separately inspect
fresh source evidence, obtain adapter/import design approval and verify actual
indexing and representative source-backed searches. No native calculation is
needed to test this foundation.

### SNUMAT external input and summary checks

The external adapter adds focused suites in
`test/knowledge/test_external_schema.py`, `test_snumat_adapter.py`,
`test_external_sources.py` and `test_external_index.py`. They exercise exact
decimal/unit predicates, source/member identities, precision and publisher
claims, safe archive reading, preview without publication, atomic import,
refresh/history, stale plans, cancellation and evidence-backed searches.
The MCP catalog, adapter and protocol suites cover external schema discovery,
job/read classification, exact-plan dispatch, result caps and cancellation.

Use unittest discovery with the required interpreter; no pytest installation is
required. Run the knowledge suite and the focused MCP files:

```powershell
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' -W error -m unittest discover -s test/knowledge -p 'test_*.py'
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' -W error -m unittest discover -s test/mcp -p 'test_catalog.py'
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' -W error -m unittest discover -s test/mcp -p 'test_project_adapter.py'
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' -W error -m unittest discover -s test/mcp -p 'test_protocol.py'
```

Synthetic tests do not establish all-source compatibility or scientific validity.
Selected real TDATA parser checks are bounded compatibility checks only.
Full-dataset counts, rejection rates, disk costs and representative imported
searches require the separately requested preview and approved real import.
No full-source import or new native calculation is implied by this test coverage.


### Exact external search projection checks

Synthetic verification on 2026-10-25 passed 15 external database tests, five MCP
catalog tests and five project-adapter tests. The external tests cover every
structured field/operator, exact unit/range comparisons, unknown values, literal
Unicode matching, managed reindex dispatch, generation mismatch and revocation
before publication, publication failure preserving the old index, cooperative
deadlines, schema-v2 compact pages and exclusion-aware next offsets. Page tests
use the actual canonical MCP serializer and envelope. Grouped-reader tests count
one compressed archive hash and one TAR scan for multiple requested members and
exercise member conflicts, unsafe TAR entries, deadlines and size limits.

The six broader MCP protocol cases passed across the suite run and a focused
rerun against the then-current project-tool inventory. These synthetic
results do not establish full-dataset search latency or current original-archive
verification.

Working-checkout activation and one managed reindex of the existing 19,998
records subsequently passed. The build took 717.120 seconds and produced a
783,548,416-byte derived index. Five public MCP stdio searches completed in
7.400–8.609 seconds: the known ion-count query still returned 34 matches, the
default page returned 20 of 19,998 records in 25,721 response bytes, and the saved
range, material-element and literal-case queries passed. One verified full-record
get completed in 5.031 seconds. Instrumentation observed one compressed archive
open and one TAR scan for six owned members. These measurements describe these
checks on this dataset and machine, without a universal latency guarantee.

All 19,998 record/head digests equal the imported approved plan; the catalog
generation is unchanged and the two deferred NaN cases remain absent. No source
preview, import or native simulation was performed for this activation. The
fresh verification client/server closed after the checks. After the user
reconnected the app, its database_status call confirmed 19,998 current records,
a ready search index with the matching generation and no issues; the new
database_reindex tool was exposed. The app reconnect is complete. No additional
rebuild or import was required.
