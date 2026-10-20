# Documentation

Start with the [project README](../README.md) for what the project does and its prerequisites. The current user-facing scope is SR stopping/range calculation, material/coefficient resources and core-and-bond (CAB) corrections.

## Choose a task

| Task | Documentation |
| --- | --- |
| Configure the project and run a first calculation | [Getting started](guides/getting-started.md) |
| Search compounds, edit data, create variants or recover CAB entries | [Materials, resources and CAB](guides/resources-and-cab.md) |
| Find function signatures, return values, side effects and error behavior | [Python API reference](reference/api.md) |
| Construct request/selection JSON or inspect records and units | [Data formats and layouts](reference/data-formats.md) |
| Read the code/documentation discrepancy review | [Non-TRIM review](reviews/non-trim-review.md) |
| Inspect recorded native validation and limitations | [CAB status and evidence](CAB_HANDOFF.md) |

## Historical design and investigation

These documents preserve decisions, experiments and delivery history. Proposal-era approval language is historical; use the current guides/reference and review for today's behavior.

- [CAB integration proposal](history/CAB_INTEGRATION_PROPOSAL.md): approved design and superseding compatibility amendment.
- [CAB API catalog](history/CAB_API_CATALOG.md): original implementation contracts and planned test inventory.
- [CAB integration tracker](history/CAB_INTEGRATION_TRACKER.md): completed delivery checklist.
- [CAB research notes](history/CAB_RESEARCH_NOTES.md): historical experimental observations and deleted evidence inventory.
- [Compound comparison](history/COMPOUND_COMPARISON.txt): narrower historical SR/TIN field-value comparison across installations.

Historical `.cache/` paths are relative to the repository root. Earlier artifacts were deleted at the user's request on 2026-10-20. New evidence generated afterward is explicitly distinguished in the CAB status page; raw artifacts are ignored and not portable repository contents. Documentation files here are not runtime resources.

## Separate investigation outside this review

[TIN findings](TIN_FINDINGS.txt) and [TRIM findings](TRIM_FINDINGS.txt) remain in their existing locations and were not edited or reviewed as transport implementation. Their presence does not indicate that TIN/TRIM execution has been implemented.
