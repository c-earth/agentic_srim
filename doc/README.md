# Documentation

Start with the [project README](../README.md) for what the project does and its prerequisites. The current user-facing scope is SR stopping/range calculation, material/coefficient resources, core-and-bond (CAB) corrections, and TRIM input preparation, execution, continuation, batching and summary/profile data.

## Choose a task

For continuation work, first read the [current `.sav` recovery direction](TRIM_DIRECTION.md). The F-button/directory-chooser export branch is abandoned; historical proposals and unfinished export code do not authorize restarting it.

| Task | Documentation |
| --- | --- |
| Configure the project and run a first calculation | [Getting started](guides/getting-started.md) |
| Search verified native SR/TRIM inputs | [Input search](guides/input-search.md) |
| Inspect input-search APIs, filters and references | [Input-search reference](reference/input-search-format.md) |
| Search compounds, edit data, create variants or recover CAB entries | [Materials, resources and CAB](guides/resources-and-cab.md) |
| Find function signatures, return values, side effects and error behavior | [Python API reference](reference/api.md) |
| Construct request/selection JSON or inspect records and units | [Data formats and layouts](reference/data-formats.md) |
| Resolve layered TRIM inputs and export a reproducible bundle | [TRIM input guide](guides/trim-inputs.md) |
| Execute/resume TRIM, schedule batches and parse existing outputs | [TRIM execution guide](guides/trim-execution.md) |
| Inspect runtime, lifecycle and parsed-result contracts | [TRIM execution reference](reference/trim-execution-format.md) |
| Inspect TRIM input schemas, resources, APIs and limits policy | [TRIM input format](reference/trim-input-format.md) |
| Read the code/documentation discrepancy review | [Non-TRIM review](reviews/non-trim-review.md) |
| Inspect recorded native validation and limitations | [CAB status and evidence](CAB_HANDOFF.md) |
| Inspect the 92-element TRIM defaults, rules and coverage | [TRIM input defaults](reference/trim-input-defaults.md) |
| Continue the TRIM default investigation | [TRIM handoff](TRIM_HANDOFF.md) |

## Historical design and investigation

These documents preserve decisions, experiments and delivery history. Proposal-era approval language is historical; use the current guides/reference and review for today's behavior.

- [CAB integration proposal](history/CAB_INTEGRATION_PROPOSAL.md): approved design and superseding compatibility amendment.
- [CAB API catalog](history/CAB_API_CATALOG.md): original implementation contracts and planned test inventory.
- [CAB integration tracker](history/CAB_INTEGRATION_TRACKER.md): completed delivery checklist.
- [CAB research notes](history/CAB_RESEARCH_NOTES.md): historical experimental observations and deleted evidence inventory.
- [Compound comparison](history/COMPOUND_COMPARISON.txt): narrower historical SR/TIN field-value comparison across installations.

Historical `.cache/` paths are relative to the repository root. Earlier artifacts were deleted at the user's request on 2026-10-20. New evidence generated afterward is explicitly distinguished in the CAB status page; raw artifacts are ignored and not portable repository contents. Documentation files here are not runtime resources.

## Separate investigation outside this review

[TIN findings](TIN_FINDINGS.txt) and [TRIM findings](TRIM_FINDINGS.txt) preserve earlier native investigations. The [2026-10-21 default reference](reference/trim-input-defaults.md) records the complete table and subsequent compound/phase evidence; [TRIM handoff](TRIM_HANDOFF.md) records later implementation and acceptance state. Use the execution guide/reference for current runtime behavior; UI-assisted historical probes do not establish unattended support for every native mode.
