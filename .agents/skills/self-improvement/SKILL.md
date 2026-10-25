---
name: self-improvement
description: Review this repository's agent skills, workflows, procedures and sub-agent definitions after substantial avoidable failures, repeated ineffective attempts, material user corrections, and at task completion. Assess reusable improvements, save supported proposals without a separate request, and manage explicitly approved trials and adoption.
---

# Self-improvement

Improve working methods using evidence within the user's task scope. Read [the protocol](references/protocol.md) before recording findings, preparing proposals or managing trials.

Review substantial avoidable failures, recurring ineffective attempts and material corrections when they can affect the next action; recover the user's task first when necessary. Briefly review meaningful findings at task completion. A correction triggers assessment, not an automatic proposal. Ordinary successful work needs no persistent record unless it supplies meaningful evidence.

For a meaningful finding, inspect relevant prior records, assess evidence and save a disposition using the protocol. When evidence supports a specific reusable change, prepare and save the complete proposal without waiting for the user to request one. If evidence or design is incomplete, state what remains and the next useful step; do not treat an observation alone as completed follow-through. Honor explicit user instructions to stop or exclude improvement work.

Evidence review, assessment and proposal drafting within scope are authorized. Implementation requires the applicable explicit approval and workspace role. Trial activation and permanent adoption retain their separate approval gates. Do not create background jobs, expand scope, manufacture measurements or run unapproved benchmarks.

Use the protocol's selective independent review when warranted and authorized there. The primary agent owns the assessment, reconciliation, saved proposal and lifecycle; a reviewer cannot approve or apply changes.

Before trial behavior, reconcile its recorded state, interruptions and boundaries. At the boundary stop trial behavior, restore the baseline safely, and report actual results with adopt, decline or more-data options. Preserve the baseline recovery protocol when trialing changes to this mechanism.

Store evidence and proposals under repository-root `.local/workflow-improvement/`; preserved dependencies belong under protected `.local/preserved-dependencies/`. These are Git-ignored local state, not a durable backup. Consult `.local/migrations/` for historical mappings without rewriting old evidence.

## Improvement targets

| Concept | Storage |
| --- | --- |
| Skill | `.agents/skills/<name>/SKILL.md` |
| Workflow or procedure | Skill body or `references/<name>.md` |
| Sub-agent role | `.codex/agents/<name>.toml` |
| Helper script | Skill's `scripts/` directory |

Identify the actual concept and storage location in every proposal. Separate a workflow into a discoverable skill only when independent discovery is useful; a sequence alone does not require a sub-agent.
