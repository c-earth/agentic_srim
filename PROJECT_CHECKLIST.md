# Project implementation checklist

Updated: 2026-10-15

Build an auditable SRIM/TRIM system with durable execution, reusable scientific evidence and evaluated improvements. Software success is CHECKED; scientific acceptance is deferred. This file owns acceptance/backlog items; README.md owns usage and HANDOFF.md owns current evidence, constraints and stopping state. Recording a task does not authorize executing it.

## Completed foundation

| Status | Delivered behavior | Retained evidence |
|---|---|---|
| [x] | Installable package, CLI, explicit configuration and local stdio MCP routing for nine tools | pyproject.toml, configs/application.toml, interfaces/, tests/interfaces/; local registration is ignored by Git |
| [x] | Explicit version-1 capability inputs/results, units, source provenance and physical/execution hashes | capabilities/{stopping_range,trim}/, domain/specifications.py, backend input/output tests |
| [x] | Direct SR Module/TRIM backends, shared process dispatch, exclusive worker lock, sibling workspaces, copied-once support and stale-output cleanup | backends/{sr_module,trim}/, execution/, backend execution tests |
| [x] | SQLite metadata/events/results, coherent reads, manifests, sealed-attempt linkage and verified retrieval | storage/, tests/{storage,execution,interfaces}/ |
| [x] | SR interval-table parsing; TRIM summary, seven profile and seven detail families with bounded retrieval | Backend output tests/fixtures; native integration methods |
| [x] | Input/session/storage error handling, worker diagnostics and completed-result warnings | tests/interfaces/test_{cli,mcp}.py, tests/execution/test_workers.py, backend input tests |
| [x] | Native all-mode and CLI/MCP coverage, separate-runtime concurrency and archive isolation | Five native methods; latest results and exact limits in HANDOFF.md |
| [x] | Continuity rules, docstring conventions, application-only cleanup and local Git setup with project-specific ignore rules | AGENTS.md, HANDOFF.md, .gitignore; repository checks recorded in the handoff |

Latest software evidence (2026-10-15): 89 ordinary tests passed, five native tests skipped in the final ordinary run; all five native methods passed across the full native run and corrected focused rerun. All 34 calculations reached CHECKED. Full details are recorded once in HANDOFF.md. These checks do not establish scientific accuracy, all possible truncation detection, or hard-worker-crash recovery.

## Open acceptance backlog

Unchecked items remain proposed or partial. Deferred items require a relevant new request before implementation.

### Scientific reference and planning

- [ ] Confirm a scientific reference case, ion/material sources, comparison metrics, accuracy targets, and scientific runtime budget before choosing scientific defaults. Explicitly deferred by the user on 2026-10-15. All scientific request fields remain required; `tests/sr_module/fixtures/request.json` copies the installed example solely for software regression. Operational bounds are explicit in `configs/application.toml`.

### Reliable simulation foundation

- [ ] Establish reproducible application dependencies. Implemented: no third-party runtime dependencies; setuptools 84.0.0 build pin; editable install and `pip check` passed. Documented in `README.md`. Clean-environment rebuild remains unexercised.
- [ ] Define typed, versioned domain schemas for materials, specifications, campaigns, runs, attempts, analyses, evidence, and answers. Initial capability-local `Request`/`Element` and `Row`/`Table`, version-1 JSON contracts, units/normalization/sources, and invalid-input checks are implemented (`capabilities/stopping_range/{inputs,outputs}.py`, `tests/sr_module/test_{input,output,execution}.py`). Broader scientific entities remain pending.
- [ ] Persist workflow states and bounded retry, timeout, cancellation, and recovery behavior. Implemented: SQLite events, bounded attempts, timeout/cancellation cleanup, recovery, and sealed-outcome reconciliation. Tests cover mocked timeout/cancellation cleanup, queued cancellation, and synthetic abandoned-attempt recovery. Hard worker termination/native process-tree supervision remains unverified; PID-recording gap documented in `README.md`. Retries share run ID.
- [ ] Implement scientific validation and convergence procedures. Deferred by user on 2026-10-15; no independent reference, numerical accuracy tolerance, uncertainty protocol, or evaluation suite chosen. Successful runs are `CHECKED`, not scientifically `ACCEPTED`.
- [ ] Deliver one scientifically validated CLI reference report. Acceptance: independent references and accuracy criteria pass with auditable source/run lineage. Historical software demonstrations were deleted under application-only cleanup and do not satisfy scientific acceptance.

### Auditable question answering

- [ ] Implement reference-document ingestion with source identifiers, checksums, and parameter provenance. Acceptance: retrieved statements resolve to their source locations and versions.
- [ ] Implement structured numerical retrieval and full-text search; add embeddings when justified by retrieval evaluations. Acceptance: metadata compatibility filters precede semantic ranking; numerical answers come from structured records.
- [ ] Define evidence records with observables, units, uncertainty, applicability, limitations, validation status, and artifact references. Acceptance: only accepted evidence supports ordinary result reuse; failed runs remain searchable for diagnostics.
- [ ] Make indexes rebuildable and version embedding/chunking settings. Acceptance: rebuilding preserves links to authoritative records and source artifacts.
- [ ] Define host-side coordinator, researcher, planner, and reviewer instructions and structured tool contracts where needed. Replaces the proposed application-side agents at the user's direction on 2026-10-15: the main host owns reasoning and agent roles. Acceptance: tool boundaries enforce validation and execution limits; host roles cannot bypass them or execute arbitrary generated simulation code.
- [ ] Implement the query workflow: interpret, check applicability, retrieve, reuse or plan, review, execute, analyze, review results, and answer. Acceptance: sufficient existing evidence avoids unnecessary simulations; revisions and spending are bounded.
- [ ] Persist answer-to-claim-to-evidence lineage and concise decision records. Acceptance: a human can audit an answer without reading a full chat transcript.
- [ ] Build held-out scientific, retrieval, and end-to-end evaluations. Acceptance: criteria cover numerical correctness, evidence applicability, citations, runtime, unnecessary runs, and recovery behavior; agent agreement alone never counts as validation.

### Expansion and measured improvement

- [ ] Define capability manifests and registration. Acceptance: each capability declares supported questions, required inputs, assumptions, planning rules, analyses, output schema, version, and reference cases.
- [ ] Package the initial application as a capability and document adding another. Acceptance: new applications reuse execution/storage without modifying the core workflow for application-specific rules.
- [ ] Add additional concurrent workers only after isolation tests. Independent-runtime isolation is verified by `tests/execution/test_workers.py:NativeWorkspaceTests` (four overlapping native pairs, repeated successfully on 2026-10-15). A shared-runtime worker pool is still unimplemented; each runtime owns its own database and one worker. Acceptance: independent jobs cannot overwrite inputs/outputs or share unsafe process state.
- [ ] Track user feedback, failures, evidence coverage, and planning performance. Acceptance: proposed improvements are attributable to versioned evidence and evaluated before release.
- [ ] Add adaptive sampling where justified. Acceptance: stopping criteria and budget limits are explicit and tested against a baseline.
- [ ] Add surrogate models only when sufficient validated data exist. Acceptance: training lineage, held-out error, domain limits, and prediction labels are stored; unsupported queries fall back to simulation or clarification.
- [ ] Establish versioned promotion/rollback for prompts, retrieval, protocols, and models. Acceptance: campaign-related data do not leak across training/evaluation splits and candidate changes meet defined release criteria.

### Long-term operations and audit

- [ ] Document architecture, scientific assumptions, capability procedures, and important design decisions.
- [ ] Define backup/restore, artifact retention, schema migration, and index-rebuild procedures. Acceptance: restore is exercised on a representative dataset.
- [ ] Add structured logs and resource accounting. Acceptance: jobs, queries, artifacts, and agent calls share traceable identifiers with no secrets in logs.
- [ ] Establish CI for deterministic checks and the opt-in native suite. Five native tests cover 26 calculations per full run; see README.md for gates. Local verification is complete; CI configuration remains pending.
- [ ] Review simulator distribution/licensing requirements before packaging or remote deployment; document the selected deployment approach.

## Deferred layout decisions

Keep implemented modules in the current component layout described by README.md. Create files only when a concrete responsibility is implemented.

| Group | Agreed disposition |
|---|---|
| domain/ | specifications.py is shared now; broader schemas remain pending |
| knowledge/, shared workflows/, standalone schemas/ | Deferred until evidence reuse, a cross-capability deterministic sequence or external format consumers require them |
| agents/ | Omitted: the host manages reasoning and roles |
| Shared analysis/, capability registry, catalog/materials/ | Add when actual reuse/discovery or reviewed material definitions require them; no initial protocol directory |
| evaluations/ | Add after selecting a scientific reference with sources, metrics and thresholds; generated reports belong under runtime/ |
| docs/, scripts/ | Add for concrete needs without duplicating root documents or retaining disposable probes |
| patches/, notebooks/ | Keep absent until a dependency fix or specific interactive exploration requires them |
| runtime/retrieval/, separate result database | Add only when warranted; current SQLite stores operational metadata and processed results |

Original SR grid-completeness proposal was withdrawn; TRIM detail-completeness behavior is unchanged for insufficient evidence. Their implementation/probes are not active backlog work. Preserve the existing tests and request physics; fixture description/checksum corrections are complete. Historical development diagnostics and demonstrations were removed by explicit cleanup direction.
