---
name: self-improvement
description: Review efficiency evidence for this repository's agent skills, workflows, procedures, and sub-agent definitions; prepare specific improvements and manage user-approved trials and adoption. Consult at task completion and before using trial behavior.
---

# Self-improvement

Improve working methods within this repository using evidence. Follow existing repository instructions and the user's current task scope.

Read [the protocol](references/protocol.md) before recording evidence, preparing proposals, or managing trials.

At task completion, briefly review meaningful efficiency observations and relevant accumulated evidence. Evidence recording and review are authorized. Do not record every action or manufacture evidence. Suggest a change only for a recurring pattern across comparable tasks or one substantial avoidable inefficiency with a clear cause. Explain why evidence is sufficient; consolidate duplicates and avoid repeating addressed suggestions without new evidence.

Prepare the exact proposed change, benefits, sacrifices, uncertainty, evaluation overhead, and trial boundaries. Obtain explicit approval of the complete proposal before applying it. At the trial boundary, stop trial behavior, restore the baseline when safe, and report actual results. Offer adopt, decline, or more data. Permanent adoption requires a separate approval. More data requires an additional concrete trial plan and approval before reactivation.

Before using trial behavior, check its recorded state and reconcile interruptions or expiration. Preserve evidence and decisions. Changing a sub-agent definition does not authorize spawning an agent. Do not create background jobs, expand task scope, or execute unapproved benchmark work.
## Improvement targets and storage

Skills and sub-agent definitions are distinct agent structures. Workflows and procedures describe content within skills; they are not additional native file types.

| Concept | Storage | Purpose |
| --- | --- | --- |
| Skill | `.agents/skills/<name>/SKILL.md` | An independently discoverable capability. |
| Workflow | Skill body or `references/<name>.md` | A sequence of stages, decisions, and coordination. |
| Procedure | Skill body or `references/<name>.md` | Focused instructions for one operation. |
| Sub-agent | `.codex/agents/<name>.toml` | A delegated role and its configuration. |
| Helper script | Skill's `scripts/` directory | A repeatable executable operation. |

A workflow or procedure needs a separate skill only when independent discovery is useful. A sub-agent is appropriate when work needs a delegated role; documenting a sequence alone does not require one. Every proposal must identify the concept being improved and its actual storage location. This self-improvement skill contains an improvement workflow and procedures for evidence recording, trials, and restoration.
