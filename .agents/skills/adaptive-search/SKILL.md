---
name: adaptive-search
description: Optimize an explicit objective subject to conditions by adapting candidate evaluations to observed results. Use for bounded parameter search, inverse design, or adaptive sampling; not ordinary document or web search.
---

# Adaptive search

This is a temporary project-local host skill available through normal Codex discovery and explicit or implicit invocation. It may later be replaced through the self-improvement service or implemented partly as an Analysis MCP method. It does not itself supply an optimizer tool or authorize simulation, installation, registration, or promotion.

## Define the search

Convert the request into an explicit search contract before evaluating candidates:

- Objective, minimize/maximize direction, observable, units and evaluation procedure. For curve matching, specify domain, alignment, weighting and distance; do not silently choose normalization or extrapolation.
- Tunable variables, types, units, ranges and any discrete choices; fixed conditions and coupled constraints. Distinguish hard feasibility constraints from preferences. Ask about missing scientific conditions rather than inventing values.
- Acceptance criterion, baseline, uncertainty treatment and whether the request seeks a feasible solution, an improved candidate or evidence of an optimum. Multiple objectives need an agreed ranking, tradeoff or Pareto presentation.
- Authorized evaluation source, total evaluation/time/cost bounds, retry bounds, fidelity and stopping rules. Include final confirmation in the budget. Missing execution permission or limits allows planning and reuse assessment, not new runs.

If a hand-drawn target is involved, obtain calibrated curve data and its uncertainty first. An uncalibrated sketch can define qualitative shape only until physical scales are supplied.

## Use available capabilities

Inspect available methods and their actual schemas. Reuse compatible evidence, retaining source identity, conditions, units, uncertainty and validation status. Do not mix fidelities, scales or experimental and accepted evidence without an explicit comparison model.

Select the least complex suitable supported strategy. Examples include bracketing for a continuous one-dimensional monotone target, coarse-to-fine sampling for a small bounded domain, or a supported derivative-free/surrogate method for a costly black box. These are choices, not installed capabilities or default scientific protocols. Bracketing needs a verified bracket and applicable continuity/monotonicity assumptions; discrete or discontinuous problems need a suitable alternative.

If the needed evaluation/optimization method is absent, identify the gap and provide a concrete plan or method specification. Implement/register new code only within the user's authorized scope. Existing Analysis summary/comparison/rendering methods do not constitute an adaptive optimizer. Follow project configuration and native-execution restrictions when using SRIM/TRIM.

## Adapt within bounds

1. Establish a baseline and a bounded initial design that covers relevant feasible choices. Check cheap constraints before expensive evaluation.
2. Record each candidate, evaluation identity, outcome, constraint values, objective, fidelity, uncertainty and consumed budget. Failed evaluations are failures, not arbitrarily poor numerical values. Resolve pending outcomes before duplicate submission.
3. Update the feasible incumbent and select the next candidate from observed evidence. Balance exploration with refinement; justify strategy changes and retain reproducible settings/seeds when applicable.
4. Stop on the agreed acceptance/convergence rule, exhausted bounds, cancellation, unavailable capability or insufficient evidence. Search state and consumed budget survive resumption. No retry or method change resets them.
5. Confirm the selected candidate at the requested fidelity within the reserved budget. For noisy evaluations, assess repeatability and feasibility with the agreed uncertainty rule; apparent small improvements alone do not establish convergence.

Preserve gaps and failures. No feasible sample does not prove the feasible set is empty. Budget exhaustion does not establish convergence. Label surrogate predictions separately from actual evaluations; do not claim a global optimum without a suitable certificate or exhaustive coverage of the declared finite domain.

## Deliver

Return the best observed feasible candidate or an explicit unresolved outcome, objective and units, constraint margins, baseline comparison, uncertainty, evaluations/budget consumed, stop reason and confirmation status. Link the evaluation ledger and retained evidence when execution occurred. State the explored domain and limits of the optimality claim.

When a Case is in use, the Coordinator checkpoints the search contract, pending evaluation identities, outcomes and next action through supported Case tools. Ordinary host instructions do not provide durable enforcement; expose missing service-side budget enforcement before unattended multi-run execution. Candidate improvements may inform the future self-improvement service; no automatic promotion occurs.
