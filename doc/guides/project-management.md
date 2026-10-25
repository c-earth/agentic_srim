# Cases, analyses and reusable capabilities

[Server/tool guide](mcp-servers.md) · [Record/interface reference](../reference/mcp-format.md)

Use project_mcp to organize evidence and srim_mcp to prepare/run calculations. An agent session can create multiple cases for different tasks, interleave updates to them, and attach the same run or analysis to several cases. Every update names its case ID and expected revision explicitly. There is no session-wide current-case pointer or exclusive session owner.

## Multiple cases in one session

Call case_create twice with different request UUIDs and explicit specs, for example:

```json
{
  "request_id": "00000000-0000-0000-0000-000000000001",
  "spec": {
    "title": "Stopping study",
    "question": "How do the selected stopping values change with energy?",
    "status": "open",
    "tags": ["stopping"],
    "notes": "",
    "members": []
  }
}
```

The second case can describe a layered-target continuation study and use a fresh UUID. Poll each returned job, then read its result reference with artifact_read to obtain the distinct case IDs/revisions. Keep those references in the coordinating agent's task context. To edit one case, call case_revise with that case_id, expected_revision and a complete updated spec. Omitted fields are not silently retained; a stale revision fails with conflict rather than overwriting another task's work.

Case status open/paused/completed/archived is an organizational label. Completing or archiving a case neither certifies calculations nor deletes any native evidence. Case revisions are immutable JSON under case/<case_id>/revisions, with a verified current.json pointer and preceding revision hash. Explicitly named empty backend allocations can be initialized; unknown older layouts are not migrated implicitly.

Case and analysis listings report an `issues` entry when a recognizable allocation
has revision or publication staging artifacts but no current.json pointer. Valid
records remain listed, and untouched empty allocations remain omitted. Issues
are independent of status filters and match pagination. A listing never repairs
the pointer or promotes an unpointed revision. During creation an interrupted
publication can appear as such an issue; a failed later update with a valid
pointer continues to expose the last published revision.

## Attach calculations explicitly

Submit the actual calculation through srim_mcp, then add a case member with a fresh member_id, role, note and reference. A live job member pins server/job/request hash but remains visibly unsealed. It never silently becomes a successful terminal result.

After the calculation ends, call run_source or srim_mcp source_get to obtain the existing native SourceReference and listed FileReferences. Add a separate native-source member if that evidence belongs in the case. Eligible failed runs can also provide diagnostic evidence. Case reads report missing/stale members rather than replacing them or dropping them from history. The same explicit source can belong to several independent cases.

physics_job_get is a read-only view of the scientific server's journal; project_mcp cannot use it to start, cancel, retry, resume or edit a calculation. Newly allocated/running records are available through run metadata tools, while pinned native input evidence requires the existing terminal-source eligibility checks.

## Record an analysis

analysis_create records a draft question, explicit method, selected source/files, parameters and required eligibility booleans. The method is ad-hoc with a full description or references an exact registered analysis version. Active registered collections initially contain no methods. No analysis_run or arbitrary Python executor is exposed.

The three eligibility flags are required: allow_failed_runs, allow_checkpoint_recovery and require_complete_parser. They prevent an agent from silently treating diagnostic failures, partial output or recovered checkpoints as equivalent to successful fresh native results. Recovered input retains its source hashes, availability, precision and inferred-geometry warnings.

The coordinating agent carries out the chosen method separately within its authorized task. analysis_attach snapshots local method/code/data/figure/report files into immutable hash-addressed analysis artifacts, after root/cap/source-stability checks. Original source files can later change without redefining the snapshot. Attach reproducible method evidence and then call analysis_finish with findings, limitations and explicit completed/failed status. Completion verifies the declared provenance/artifact contract; findings remain caller-attributed scientific claims.

For completed analyses, each exact case-sensitive filename in
`method.code_artifacts` must identify an attachment with role `code` or `method`.
Written method documents remain valid for manual analyses. Missing names and
other roles produce a clear error before publishing a revision. Completion still
requires nonempty findings and at least one attachment, including when the
declared list is empty; registered-method schema checks also apply. Failed
completion retains its existing evidence and failure-reason requirements. This
rule checks metadata consistency and does not execute or scientifically validate
the method. Historical terminal records remain readable without rewriting them.

Analysis records use backend post IDs under memory/post and their own revision schema. Completed/failed analyses are terminal; further work gets a new analysis record. A completed analysis revision can be referenced by multiple cases. Cross-record commits are explicit: finalizing an analysis does not silently revise its cases. artifact_read accepts exact listed case/analysis revision and analysis artifact references; native files remain accessible through the scientific source reader.

## Reuse and self-improvement

collections_list reports workflow, analysis and skill collections separately. Candidate creation preserves an unapproved definition, explicit schemas/declarative procedure, supporting file hashes, rationale and evidence. It does not activate a workflow, add a scientific algorithm, edit instructions or grant execution authority. Existing repository skills are inventoried separately and remain intact.

Use improvement_observe for meaningful evidence, improvement_assess for a saved evidence-based disposition, and improvement_proposal_create for exact complete proposal Markdown linked to observations. The [self-improvement skill](../../.agents/skills/self-improvement/SKILL.md) and [protocol](../../.agents/skills/self-improvement/references/protocol.md) define review triggers, selective independent review, follow-through, explicit trial approval, snapshots, restoration and separate adoption approval. improvement_status reports valid assessment chains, unassessed/deferred/conflicting findings and proposals still owed alongside existing event graphs and trial counts. Version-1 events remain readable; only assessment events use version 2. A structurally valid draft closes a drafting obligation, not an implementation or quality requirement. No background service enforces reconsideration, expiry or rollback.

No MCP tool creates approval events, starts a trial, promotes a candidate, edits skills, or adopts behavior. After real user approval, the coordinating agent performs the specifically approved repository workflow. The Python-only publication API checks exact candidate/proposal/adoption hashes before making a new immutable reusable version; record checks cannot cryptographically authenticate human consent. Files under `.local/workflow-improvement/` remain persistent local ignored evidence rather than durable cloud memory or a backup. Preserved dependencies live under protected `.local/preserved-dependencies/`. Routine repository-root cache cleanup must not remove these stores. Consult `.local/migrations/` inventories for historical paths; mappings do not automatically redirect old reads.
