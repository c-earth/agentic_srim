# Verification, native coverage and limits

[Documentation index](../README.md) · [Current project findings](../reviews/project-review-20261022.md) · [TRIM continuation policy](../decisions/trim-continuation.md)

## Software checks

Run all seven test directories explicitly; top-level discovery alone does not discover these namespace-directory tests. The MCP suite includes real local stdio transport and needs Windows loopback access for its asyncio event loop:

```powershell
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' -W error -m unittest discover -s test/backend -p 'test_*.py'
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' -W error -m unittest discover -s test/srim -p 'test_*.py'
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' -W error -m unittest discover -s test/knowledge -p 'test_*.py'
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' -W error -m unittest discover -s test/case -p 'test_*.py'
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' -W error -m unittest discover -s test/analysis -p 'test_*.py'
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' -W error -m unittest discover -s test/plan -p 'test_*.py'
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' -W error -m unittest discover -s test/mcp -p 'test_*.py'
```

Tests use synthetic installations and controlled native-process substitutes; passing tests do not establish universal numerical accuracy or SRIM compatibility. Recorded native checks cover representative installations and cases, not every element, mode, or checkpoint scenario. Interactive dialogs and event-mode limitations can prevent unattended completion. Hash verification proves byte identity and provenance, not physical correctness or atomic capture of a live external process.

Test counts in a dated report describe that tested snapshot. Run the families
relevant to a change and report their actual results. The current
[project review](../reviews/project-review-20261022.md) records additional known
defects; passing existing tests does not establish their absence.

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
`.cache/trim-sav-live/checkpoint-first-verification.json`. Exact source notes and
their evidence paths were preserved during documentation cleanup in the local
migration archive. Check [the active decision](../decisions/trim-continuation.md)
and [execution reference](trim-execution-format.md) for current behavior rather
than interpreting old proposal-stage text as instructions.

The [recovery guide](../guides/trim-checkpoint-recovery.md#precision-and-validation)
retains measured cell discrepancies and the continued-versus-uninterrupted
comparison. Some saved blobs are preserved and hashed but not decoded. Hashes
establish byte identity, not atomic native publication, complete structural
validation of every saved field, or independent scientific accuracy.

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
tree. The later [project review](../reviews/project-review-20261022.md) tracks
separate additional findings.
