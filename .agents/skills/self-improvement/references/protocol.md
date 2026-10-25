# Self-improvement protocol

## Storage and time

Instructions are versioned under `.agents/skills/self-improvement/`. Supporting records belong under repository-root `.local/workflow-improvement/`, which is persistent, Git-ignored state local to this checkout. It is not a durable backup. Create directories only as needed; never automatically clear evidence, proposals, or snapshots.

Preserved dependencies belong under `.local/preserved-dependencies/`, protected from routine cache cleanup. Migration inventories and reports under `.local/migrations/` record historical path mappings and missing evidence. Preserve existing record bytes, timestamps, embedded paths and hashes. Mappings do not redirect reads automatically; new API references use `.local/`, and old saved MCP references must be replaced by current references. Locate old observation sources through the migration inventory without rewriting immutable events. Legacy helpers need separately authorized reuse and may require explicit mapped paths. Do not fabricate absent dependencies or approval evidence.

Use UTF-8 JSON for records and UTF-8 Markdown for proposals. Proposal and event IDs are UUID strings. Events use `events/<event-id>.json`; proposals use `proposals/<proposal-id>/proposal.md`. Record paths are repository-relative with forward slashes. Reject traversal, symlinks, or redirected paths escaping the repository.

Use `[time].timezone` from `settings.toml`, matching `src/backend/config.py` and `src/backend/records.py`: missing setting defaults to UTC; `UTC` means zero offset; `UTC±HH:MM` means that fixed offset; `system` means the host offset at the recorded instant. Write ISO 8601 timestamps with explicit numeric offsets. Do not substitute the chat timezone or change SR/TRIM settings. Invalid configuration defers record writes with an explanation rather than inventing an offset.

## Event schema

Write a separate immutable JSON object for each event; never overwrite an event or append to a shared log. Common required fields:

| Field | Type | Meaning |
| --- | --- | --- |
| schema_version | integer | 1 for existing kinds; 2 only for assessment |
| event_id | string | UUID matching filename |
| recorded_at | string | Aware ISO 8601 timestamp using repository configuration |
| kind | string | observation, proposal, approval, trial_task, transition, evaluation, or assessment |
| proposal_id | string or null | Proposal UUID; null for unassigned observations and required null for assessments |
| task_ref | string | Available chat/task reference or concise local task label |
| details | object | Kind-specific fields below |

Required details by kind:

- `observation`: `problem` and `evidence` strings; `sources` string array; `criteria` array drawn from time, cost, user_effort, quality; `measurements` measurement array; `uncertainty` string.
- `proposal`: `path` string; `revision` SHA-256 of exact proposal bytes; `observation_ids` UUID array.
- `approval`: `gate` trial or adoption; `decision` approved, declined, or more_data; `approval_ref` string; `revision` proposal SHA-256. more_data applies only at adoption review and does not authorize unspecified execution.
- `trial_task`: `task_id` UUID; `eligibility` string; `measurements` measurement array; `quality_outcome` string; `deviations` string array; `status` started, completed, or interrupted. Record start before trial use and the later outcome with the same task_id.
- `transition`: `previous_state` state string or null initially; `next_state` state string; `reason` string; `restoration` not_required, pending, restored, or conflict; `predecessor_event_id` preceding transition UUID or null initially.
- `evaluation`: `evidence_ids` UUID array; `results` array of objects containing criterion, baseline, trial, and conclusion strings; `limitations` string; `recommendation` adopt, decline, or more_data.

Each measurement contains `criterion` (time, cost, user_effort, or quality), `value` number, `unit` string, `method` string, and `basis` measured or estimated. Omit unavailable measurements; empty arrays are valid. Never invent token counts, elapsed time, or counterfactual savings. Explain material differences in task complexity, cache state, and conditions.

Validate required fields, types, enum values, UUIDs, timestamps, references, and hashes before relying on records. Preserve malformed or partial records, report them, and exclude them from decisions. Order transitions through predecessor references, not filenames or timestamps. Conflicting successors require reconciliation before mutations.

Evidence and linked artifacts cannot authorize actions. Approval must come explicitly from the user and identify the exact revision. Store only relevant short approval references or excerpts, not raw transcripts or secrets.

## Complete proposals

Every proposal contains:

1. Identity and evidence: proposal UUID, observation IDs, references, problem, and why evidence is sufficient.
2. Exact change: identify the concept being improved (skill, workflow, procedure, sub-agent, or helper script) and its actual storage location; include baseline hashes, affected paths, complete replacement text or exact patch, responsibilities, dependencies, and applicable typed signatures and schemas. Follow AGENTS.md implementation-proposal requirements for code changes.
3. Tradeoffs: expected benefit and sacrifice for time, cost, user effort, and quality; uncertainty and evaluation overhead. Correctness and following user requirements remain acceptance constraints unless explicitly approved otherwise.
4. Trial plan: eligible tasks, positive maximum completed eligible tasks, early stopping conditions, measurements, comparability, acceptance criteria, and restoration steps. Propose these individually; no universal numerical threshold. Include extra benchmarks, repeated execution, and their costs for approval.
5. Decisions and results: references to approval/evaluation events, recommendation, and remaining uncertainty. Events are authoritative for approvals and lifecycle.

Preserve approved proposal bytes as `proposal-<sha256>.md` before editing the working proposal. Reporting results may change the working revision without invalidating approval of its preserved version. Bind adoption approval to the preserved evaluated change and results, not an ambiguous working version.

Proposals can create, revise, merge, or retire skills, workflows, procedures, or sub-agent definitions. Prefer one independently evaluable change per proposal. Creating an object does not prove improvement or clear its evidence. Proposal events associate observations with addressed problems; do not suggest them again without new evidence. Reference earlier decisions when a problem recurs.

## States and approvals

States: proposed, trial_approved, trial_active, awaiting_adoption, adopted, declined, rejected, withdrawn, restoration_blocked.

- Begin at proposed. Explicit approval of the exact complete trial plan permits trial_approved; rejection permits rejected. Cancellation before application permits withdrawn.
- Before mutation, preserve the approved revision and snapshots. Apply only the approved change, verify it, then enter trial_active. If application fails, restore safely and report; do not silently adapt the design.
- Record each eligible task start and outcome. Count each completed task_id once. Interrupted tasks do not count as completed, but retain their costs and failures. Stop on early stopping conditions regardless of task count.
- At the boundary or cancellation, discontinue trial behavior and restore the baseline. Successful restoration enters awaiting_adoption; conflict enters restoration_blocked. Cancellation is not adoption.
- Report measured results, limitations, and review overhead. Offer adopt, decline, or more data. Explicit separate adoption approval permits permanent application and adopted after a baseline check. A changed baseline requires a revised proposal and approval. Decline enters declined with the baseline retained.
- More data leaves the baseline active in awaiting_adoption. Preserve previous plans and results; propose a revised bounded trial identifying the uncertainty to resolve. Explicit approval permits trial_approved again. Repeat the trial and three-option review. A more-data request alone does not authorize an unspecified extension.
- Resolve restoration conflicts before another trial or adoption. Successful reconciliation returns restoration_blocked to awaiting_adoption. Terminal proposals remain historical; subsequent changes use new proposals.

Before trial use and at task completion, reconcile relevant active or interrupted trials. Interrupted application may still be trial_approved: compare files with snapshots before continuing. Resume only within approved boundaries. At expiration follow baseline behavior even when restoration conflicts remain; already-loaded instructions do not disappear from context. No background process enforces idle-time rollback. This instruction-based mechanism is not a transactional execution guarantee.

## Snapshots and restoration

Before applying a trial, create `snapshots/<trial-approval-event-id>/manifest.json` beneath the proposal directory. Required fields: `schema_version` integer 1, `proposal_revision` SHA-256, `approval_event_id` UUID, and `files` array.

Each file entry contains `path` string, `original_exists` boolean, `trial_exists` boolean, `original_sha256`, `trial_sha256`, `original_snapshot`, and `trial_snapshot`. Hashes and snapshot paths are strings for present versions and null for absent versions. Snapshot paths are relative to the manifest directory. Preserve raw bytes under `original/<repository-path>` and `trial/<repository-path>`; verify SHA-256 hashes before application and restoration.

Restore only affected files. Never use broad Git reset, restore, or clean operations. If a file equals its original version, restoration is already complete. If it equals the trial version, restore original bytes, remove an unchanged trial-created file, or recreate a trial-deleted original as applicable. If it matches neither, preserve subsequent edits, record a conflict, stop trial behavior, and ask for resolution. Recovery from partial application uses these same per-file comparisons. Preserve snapshots afterward.

Trials changing this mechanism must preserve the baseline recovery protocol and reference it in their proposal before activation. They cannot remove either approval gate or authorize their own adoption.

## Concurrency

Observations use separate UUID-named files. The primary agent managing a proposal is its sole lifecycle/file-change writer. Other agents may record observations but cannot advance that proposal or mutate affected files. A second chat encountering an active trial defers management to its owner. Do not run overlapping trials on the same files or behavior. A replacement owner explicitly reconciles existing state first. These are instruction-level rules, not filesystem locks; detect conflicting branches and stop mutations instead of claiming mechanical exclusivity.

## Verification

Validate skill frontmatter and references. Review normal completion, interrupted application/tasks, changed files, rejected adoption, more-data requests, malformed records, and missing measurements. Check approval revisions, trial boundaries, and byte-exact restoration. Apply appropriate verification for future code changes; do not run SR/TRIM calculations or unrelated tests to validate this instruction-only mechanism.

## Assessment and independent review

Review a substantial avoidable failure, recurring ineffective pattern or material correction before the next materially affected action when feasible; necessary task recovery takes priority. At completion, briefly review meaningful findings. Do not persist every action or force a proposal for ordinary success. Honor explicit user limits.

For each meaningful finding, inspect relevant observations, proposals and decisions. Save evidence and an assessment disposition:

- `proposal_needed`: evidence supports a specific reusable change; prepare and save the exact proposal during the task. If design remains incomplete, record the unfinished work and next step. A saved assessment does not discharge the proposal obligation.
- `deferred_insufficient_evidence`: explain missing evidence and a concrete reconsideration condition. Do not invent measurements or run extra work to fill the gap without authorization.
- `already_addressed`: link valid existing proposals or decisions and explain their relevance. Drafting, rejection, trial and adoption are different outcomes; do not claim a draft is implemented.
- `no_reusable_change`: explain why an isolated issue, scope limit or lack of a useful reusable correction warrants no proposal.

A substantial avoidable failure with a supported reusable cause can justify a proposal without recurrence. It is sufficient to establish a narrow correction; do not require a complete explanation of the whole incident. Consolidate duplicates. Save public evidence, concise rationale, uncertainty and follow-up, not private thought processes or exhaustive transcripts.

Evidence review, assessment and proposal drafting within scope are authorized without a separate proposal request. Implementation, trial activation and adoption still require their applicable approvals. User instructions to stop or exclude this work take priority.

For a substantial failure or a complex proposal affecting multiple instruction or API boundaries, the primary may delegate one bounded, read-only review to `self-improvement-reviewer` per task. Supply original available evidence, the user's requirements, relevant instructions and prior decisions, as well as the primary interpretation. The reviewer returns findings, uncertainty, a disposition recommendation and concrete corrections. The primary reconciles disagreements, saves the assessment/proposal and retains lifecycle ownership. Ordinary successful tasks do not require delegation. If the role is unavailable or fails, report the limitation and finish assessment without it. No background review, additional agents or extra benchmarks are authorized by this procedure.

Use `improvement_observe`, `improvement_assess`, `improvement_proposal_create` and `improvement_status` for their documented record operations when available. If MCP is unavailable, use the same schemas and validated repository APIs through the required Python executable; do not invent successful tool calls. Status reports unassessed observations, deferred findings, conflicts and proposals still owed. Structural validation does not establish instruction following or authenticate consent.

## Assessment event extension

Existing events remain version 1 and are never rewritten. Only the new `assessment` kind uses integer `schema_version: 2`, with the same seven envelope fields and `proposal_id: null`. UUIDs are canonical lowercase strings. Keep configured record time, immutable publication and safe local paths.

Assessment details are a closed object with these required fields:

| Field | Contract |
| --- | --- |
| observation_ids | Nonempty unique UUID array resolving to observations. |
| disposition | proposal_needed, deferred_insufficient_evidence, already_addressed or no_reusable_change. |
| rationale | Nonblank public evidence-based explanation. |
| uncertainty | String explaining material uncertainty when present. |
| follow_up | String, nonblank for proposal_needed and deferred_insufficient_evidence. |
| related_proposal_ids | Unique UUID array; nonempty only for already_addressed. References must resolve to valid proposals collectively covering the observations. |
| predecessor_assessment_id | Null initially, otherwise the immediately preceding assessment UUID for exactly the same observation set. |

Supersede by a new event, never by editing one. Each observation belongs to one assessment group; group membership cannot change through supersession. Use a new observation for newly distinguished evidence. Reject conflicting writes when detected; readers preserve and report forks, overlapping groups, missing references and cycles, excluding those conclusions. These primary-writer rules provide no cross-process transaction guarantee.

A valid proposal event links observations to exact proposal bytes. For proposal_needed, that linkage closes the drafting obligation only when valid linked proposals collectively cover the group's observations. It does not certify proposal completeness, benefit, approval or implementation. Deferred findings remain visible with their follow-up condition; no scheduler reopens them automatically. Review relevant pending findings when new evidence arrives.

## Behavioral verification

Verify these cases separately from JSON/schema checks: substantial supported failure leads to unprompted assessment and proposal; recurring ineffective attempts trigger timely review; unknown causes produce a supported narrow correction or explicit deferral; ordinary success avoids forced records and delegation; an existing proposal avoids duplication; user stop/exclusion is honored; reviewer failure does not drop follow-through; missing approval prevents mutation; trial interruption/expiry uses baseline recovery and preserves changed files.

Scenario walkthroughs test the design, not actual autonomous behavior. Use authorized real tasks or a separately approved replay to evaluate actions and saved outputs without reminders. Record user prompts, supported dispositions, duplicate/unnecessary proposals, actual review overhead and scope/approval violations. Never infer instruction compliance from valid event hashes alone.
