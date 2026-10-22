# project_mcp: cases, analyses, reusable capabilities, and improvement evidence

Status: approved and implemented, 2026-10-22. This is the project-domain appendix to [the two-server design](MCP_SERVER_PROPOSAL.md); shared protocol/job/file contracts are in [the API appendix](MCP_API_CONTRACT_PROPOSAL.md). Original approved bytes are retained locally under `.cache/mcp-implementation/approved/`. One agent session can manage multiple cases through explicit case IDs; see the [management guide](guides/project-management.md).

The user selected a second MCP server named project_mcp, separate from srim_mcp. It manages research organization and provenance. It never launches SRIM/TRIM, changes native metadata, or determines that a scientific conclusion is valid. Existing native allocation/source-reference APIs are reused; case/post records receive their own schemas rather than engine metadata.

## Initial capabilities and their limits

| Area | Initial behavior |
| --- | --- |
| Cases | Create a titled research question, versioned notes/tags, ordered job/native/analysis references, and a status; inspect stale/missing references without silently replacing them |
| Runs | List/read native metadata and read srim_mcp job journals; resolve eligible terminal runs into pinned native references; associate these with a case |
| Analyses | Record a method and explicit input eligibility/availability requirements; attach copied method/code/data/figure/report artifacts and narrative; finalize a provenance record |
| Reusable workflows | Discover immutable approved definitions; submit candidates; initially no active definitions and no automatic workflow interpreter |
| Reusable analysis methods | Same discovery/candidate structure; initially empty; no implicit scientific method, formula, interpolation, integration, normalization, or uncertainty model |
| Reusable skills | Same registration/discovery structure; initially empty; existing repository skills remain available to the host and are not deleted or auto-registered |
| Self-improvement | Read meaningful observations/proposals/trial state; record validated observation evidence; preserve exact complete proposal bytes/hash linked to observations; inspect candidate/baseline state |

MCP standardizes access and discovery. It does not itself provide memory semantics, scientific methods, learning, or approval authority. Those behaviors must be implemented explicitly through the Python APIs and coordinating agent's approved skill workflow.

## Persistent layout

```text
case/case_<existing-backend-timestamp-and-suffix>/
  current.json
  revisions/r000001.json
  revisions/r000002.json
memory/post/post_<existing-backend-timestamp-and-suffix>/
  current.json
  revisions/r000001.json
  artifacts/<sha256>/<safe-basename>
memory/project/capabilities/{workflow,analysis,skill}/<id>/vN/
  manifest.json
  files/<safe-relative-path>
memory/project/candidates/candidate_<uuid>/
  candidate.json
  files/<safe-relative-path>
memory/project/.locks/<kind>-<id>.lock
memory/mcp/project_mcp/
  jobs/...
  objects/...
  artifacts/...
.cache/workflow-improvement/
  events/<event-uuid>.json
  proposals/<proposal-uuid>/proposal.md
```

Use backend allocation for case/post identities and timestamps. Existing empty allocated case/post directories can be initialized explicitly; a preexisting nonempty directory without this schema is incompatible and never overwritten or migrated implicitly. No root tmp/output folders. The improvement records retain their existing local ignored location and are not silently copied into a second competing event store. Immutable domain content belongs in case/memory; auxiliary validation and temporary scripts belong under .cache/.

Current-pointer JSON has exactly `{schema_version, kind, id, revision, sha256}`. Revision bytes are canonical UTF-8 sorted two-space JSON plus final LF. Revision hash covers exact stored bytes; current.json is a replaceable pointer. Each revision contains previous_sha256 (null at revision 1) and its full state, not a patch. Under a per-record lock, check expected_revision, validate complete content/references, publish the new immutable revision, then atomically replace current.json. Readers verify pointer/hash/ID/revision and the chain through the requested revision. No inference from the largest filename if a publication stopped before pointer replacement. Unpointed revisions/partial staging are reported as pending artifacts; never overwritten. A retry can return an already committed matching request revision; an uncertain partial job is not automatically retried.

Independent records can be updated concurrently. Two writers to the same record serialize and the stale expected_revision loses with conflict; no last-writer-wins notes or silently merged evidence. Case/analysis changes are separate commits: finishing an analysis does not silently update a case, and attaching a reference does not mutate the target. No cross-record transaction is claimed. Locks are held only during verification/publication, never across a native process or human approval.

## Exact case schema

```text
CaseRevision = {
  schema_version, kind, id, revision, previous_sha256,
  request_id, created_at, updated_at,
  title, question, status, tags, notes, members
}
Member = {member_id, role, note, reference}
```

kind is case. Title/question are nonblank Unicode strings, no NUL; title <= 512 characters and question/notes <= 1 MiB UTF-8 each. Status is open/paused/completed/archived. These are organizational labels, not a claim every calculation succeeded. Tags are an ordered unique list of <= 64 strings, each 1–128 characters; case-sensitive identity, no automatic scientific vocabulary. Member_id is UUID; role/note are caller-supplied strings (role 1–128, note <= 1 MiB). Members preserve explicit order and member IDs; the same source may have different roles but duplicate member IDs are errors. No partial patch language: revision requests supply full title/question/status/tags/notes/members and expected revision.

A member reference is exactly one of:

- `{kind: 'job', server: 'srim_mcp', job_id: str, request_sha256: str}`: live job link, visibly unsealed. It never automatically turns into a terminal source.
- `{kind: 'native-source', source: SourceReference}`: existing terminal native reference from src.knowledge.source, binding metadata and input inventory.
- `{kind: 'analysis', id: str, revision: int, sha256: str}`: exact sealed project analysis revision.

Case read returns the stored revision plus reference-check results `{member_id, state, reason}` where state is verified/live/stale/missing/unavailable. It does not mutate the revision to refresh references. Create/revise reject newly introduced invalid references; retained references that have since become stale can remain with their issues so a note edit does not erase history. A case can complete with documented failed or stale members; callers must not equate case status with native/scientific success.

No automatic execution plan or background schedule is attached to a case. Run launches and retries are explicit calls to srim_mcp. Deletion/archival of native evidence is not authorized by changing case status to archived.

## Exact analysis schema

```text
AnalysisRevision = {
  schema_version, kind, id, revision, previous_sha256,
  request_id, created_at, updated_at,
  title, question, status, method, inputs, eligibility,
  parameters, artifacts, findings, limitations, failure
}
Method = {kind, reference, description, citations, code_artifacts}
AnalysisInput = {source, files, purpose}
Eligibility = {allow_failed_runs, allow_checkpoint_recovery, require_complete_parser}
Artifact = {name, role, path, sha256, size_bytes, media_type, source}
```

kind is analysis; ID uses backend post identity. Status is draft/completed/failed. Method.kind is ad-hoc/registered. Registered requires exact `{collection: 'analysis', id, version, manifest_sha256}` reference plus its declared requirements; ad-hoc requires reference=null and a nonblank full method description. Description is required in both forms; citations are ordered `{title, locator}` strings, code_artifacts are names of attached code artifacts. Titles/questions use case bounds. parameters is finite caller JSON with explicit method-defined units; the server does not evaluate formulas or change numeric types. findings/limitations are Unicode narrative strings <= 1 MiB each. They are attributed caller claims, not server-validated scientific conclusions.

Inputs use existing SourceReference plus explicit FileReferences and a nonblank purpose. They pin terminal native evidence only; live jobs must first be resolved through run_source. Each selected file must belong to that exact source and verify. A valid failed run is allowed only when allow_failed_runs=true. Recovered TRIM data requires allow_checkpoint_recovery=true and retention of all recovery provenance/availability/precision/inferred-geometry warnings. require_complete_parser applies to the selected saved scientific result, not merely existence of some raw output. When false, a diagnostic analysis may attach partial or failed-run evidence with explicit limitations. None of these booleans is defaulted by the server: all three are required in AnalysisSpec. Null/absent/unavailable scientific quantities never become zero.

An analysis can be drafted before artifacts are available, but input references must verify at creation. Draft updates can attach or add evidence with optimistic revision checking. Completed/failed records are terminal; further work creates a new analysis with explicit prior-analysis citation in Method.citations. No silent return from terminal to draft. Completed means the declared provenance/artifact contract is satisfied, not that a scientific result has been independently validated. Failed requires `{code, message}` failure and preserves any artifacts; draft/completed require failure=null. Completion requires findings and limitations fields (limitations may be explicitly empty), all referenced artifacts/code present and hash-verified, and registered method output requirements fulfilled. An ad-hoc analysis must include at least one method/code/text/data/figure/report artifact and explicitly describe how it was produced.

Artifact role is method/code/data/figure/report/diagnostic. Names are unique safe relative names; original allowed local source path is retained as provenance only. Attachment copies exact regular files under post/artifacts/<sha256>/<basename> using exclusive immutable publication, verifies source before/after and preserves partial paths on failure. This snapshots local host-produced work; it does not download remote media or run code. A file is capped at 64 MiB and one attachment operation at 256 MiB, declared adapter limits, not scientific limits. Larger artifacts require an approved future streaming/reference extension. Existing hash-identical blobs can be reused; different bytes can never overwrite the same immutable blob. No arbitrary directories, links, execution, or content interpretation beyond media-type labeling.

There is no analysis_run tool in this first version. The host agent performs an explicitly selected ad-hoc method or a later approved registered method, then attaches its evidence. Adding an executable analysis method requires its own complete scientific/API proposal; the empty collection does not authorize a generic Python executor.

## Reusable collections and candidates

`CapabilityManifest` has exactly `{schema_version, collection, id, version, title, description, input_schema, output_schema, prerequisites, procedure, evidence, limitations, files, approval_reference}`. Collection is workflow/analysis/skill. IDs follow lowercase alphanumeric/hyphen rules, 1–64 characters, excluding Windows device names. Versions are vN independently per identity; references always pin version/hash. Schemas are complete closed-object JSON Schema documents. prerequisites/evidence/limitations are ordered string lists. Procedure is a declarative list of `{step_id, instruction, tool_names}` with unique step IDs; text is material for the coordinating agent, never eval or an automatic executable graph. Files are an exact safe inventory; approval_reference links a trusted host-managed approval record, but does not by itself mechanically authenticate a human.

Active collections start empty; directories are created lazily. Existing repository scientific-figures and self-improvement skills are neither removed nor redefined by this registry. A separate read-only skill inventory reports their path/hash/name/description; reading them does not mean they are approved entries in the new collection.

`Candidate = {schema_version, id, collection, proposed_manifest, rationale, evidence_references, created_at, request_id, files}`. Proposed_manifest has the CapabilityManifest fields except version and approval_reference; callers cannot supply either field in a candidate. It has no active/approved flag. Candidate_create writes a new immutable candidate and optional copied supporting files. It checks schemas and evidence hashes and reports compatibility/validation issues; it never makes a candidate executable or current. Submission is not a completed self-improvement proposal unless the full skill-required change/trial/evaluation details are separately supplied.

For a registered analysis method, input_schema validates exactly `{inputs, eligibility, parameters}` and output_schema validates exactly `{artifacts, findings, limitations}` with method-specific required artifact names/roles/units declared in those schemas. Case/analysis storage still enforces the common outer schemas. Other collection schemas describe the coordinating agent's explicit procedure inputs/outputs; no automatic graph interpreter is implied. Publication assigns a new vN and approval_reference. The approval reference is `{proposal_id, proposal_revision_sha256, approval_event_id, approval_event_sha256}` pointing to a matching approved adoption event under the existing improvement protocol; the event/proposal/candidate links and hashes must verify. The coordinating agent remains responsible for real authorization and any approved source changes.

No MCP tool can set approval_reference, promote a candidate, overwrite an active manifest, modify .agents/.codex instructions, or add arbitrary code paths to execution. After explicit user approval of an exact complete addition, the coordinating agent can implement/register that approved version using the Python publication API and repository skill instructions. The Python publication API is not an authorization authority; it verifies a supplied approval evidence reference and baseline mechanically, while the agent remains responsible for obtaining real authorization. Future versions can add a trusted human approval channel, but a caller string such as approved=true must never be accepted as equivalent.

## project_mcp tools

Includes the shared discovery/schema/example/job/artifact tools in the API appendix. All mutations require caller request_id UUID and return the same durable JobAccepted envelope. Short management jobs execute with project_mcp max_parallel=1 by default; publication is noncancellable after it starts.

| Exact protocol signature | Responsibilities, result, effects, error behavior |
| --- | --- |
| `cases_list(status: str | None = None, offset: int = 0, limit: int = 100) -> Response` | Safe current-pointer discovery with per-record issues, sorted native case IDs; no automatic initialization of empty allocated cases. |
| `case_get(case_id: str, revision: int | None = None) -> Response` | Current or exact immutable revision plus reference-check issues. Unknown not-found; invalid/corrupt record integrity. |
| `case_create(request_id: str, spec: JSONObject, allocated_id: str | None = None) -> Response` | Allocate/init a case from exact CaseSpec; an initial open status, members [], tags [], and empty notes are supplied explicitly by the caller when wanted. No omitted defaults. Existing empty allocation accepted only when explicitly named. Returns sealed revision reference. |
| `case_revise(request_id: str, case_id: str, expected_revision: int, spec: JSONObject) -> Response` | Full-state append with CAS; immutable history, no target/native mutations. Unknown/corrupt/stale revision errors retained. |
| `runs_list(engine: str | None = None, status: str | None = None, offset: int = 0, limit: int = 100) -> Response` | Read structurally validated native metadata under memory/srim, including created/running and failed; issues separate. Same semantics as srim_mcp simulation listing, no engine calls. |
| `run_get(simulation_id: str) -> Response` | Read metadata and independent integrity/evidence-readiness labels. Does not treat mere metadata validation as full evidence verification. |
| `run_source(simulation_id: str) -> Response` | Existing terminal-native project_simulation_inputs; returns exact source and file references. Unsupported capture/nonterminal inputs fail with reason. |
| `physics_job_get(job_id: str) -> Response` | Read/validate srim_mcp journal request/state and resolve its listed native record IDs, without acquiring or changing its execution ownership. Snapshot can change immediately after read. |
| `analyses_list(status: str | None = None, offset: int = 0, limit: int = 100) -> Response` | Current-pointer summaries with issues, no native computation. |
| `analysis_get(analysis_id: str, revision: int | None = None) -> Response` | Exact/current revision plus input/artifact/reference verification. Preserves caller findings separately from verifier issues. |
| `analysis_create(request_id: str, spec: JSONObject, allocated_id: str | None = None) -> Response` | Allocate/init draft post analysis after exact method/input/eligibility validation. Empty explicit allocation accepted; no scientific execution. |
| `analysis_revise(request_id: str, analysis_id: str, expected_revision: int, spec: JSONObject) -> Response` | Append a full draft state with CAS; cannot edit terminal records or refer to missing artifacts. |
| `analysis_attach(request_id: str, analysis_id: str, expected_revision: int, files: list[JSONObject]) -> Response` | Nonempty unique `{name, role, source_path, media_type}` records; source roots and caps checked. Copy/hash artifact blobs, append draft revision. Partial blobs retained on failure without invented attachment success. |
| `analysis_finish(request_id: str, analysis_id: str, expected_revision: int, status: Literal['completed', 'failed'], findings: str, limitations: str, failure: JSONObject | None) -> Response` | Reverify input/method/artifacts and append terminal revision under schema-specific success/failure rules. No inference that native run succeeded or findings are physically correct. |
| `collections_list() -> Response` | Three declared collections, counts, validation issues and initially empty entries. No disk scanning outside declared roots. |
| `collection_entries(collection: Literal['workflow', 'analysis', 'skill'], offset: int = 0, limit: int = 100) -> Response` | Immutable version references and issues; read-only. |
| `collection_entry_get(reference: JSONObject) -> Response` | Verify exact manifest/files/schema/evidence/approval reference, return definition and qualifications. No code execution. |
| `candidates_list(collection: str | None = None, offset: int = 0, limit: int = 100) -> Response` | Pending candidate summaries, separate from active entries. |
| `candidate_get(candidate_id: str) -> Response` | Hash-verified candidate and supporting file inventory, not an approval claim. |
| `candidate_create(request_id: str, spec: JSONObject, files: list[JSONObject]) -> Response` | Publish exact proposed candidate/supporting snapshots, never active capability. Empty files list explicit. |
| `repository_skills_list() -> Response` | Read-only metadata/path/hash inventory under .agents/skills plus .codex/agents. Malformed entries reported; no edits, trial activation, auto-registration, or subagent spawning. |
| `improvement_status(proposal_id: str | None = None) -> Response` | Validate and summarize existing event graphs, approvals, revisions, boundaries and restoration state; malformed/partial/conflicting records preserved and reported. Does not choose lifecycle transitions. |
| `improvement_observe(request_id: str, details: JSONObject) -> Response` | Write one meaningful observation under existing protocol after mechanical schema/reference/time validation; caller supplies problem/evidence/sources/criteria/measurements/uncertainty. Server generates UUID/time and null proposal_id. No automatic every-call observation log. |
| `improvement_proposal_create(request_id: str, proposal_id: str, observation_ids: list[str], markdown: str) -> Response` | Validate referenced observation records, required proposal sections and safe new ID/path; preserve exact UTF-8 Markdown, hash it, and append a protocol proposal event. No overwrites, approvals, trial starts or skill edits. |

CaseSpec is the writable subset `{title, question, status, tags, notes, members}`. AnalysisSpec is `{title, question, method, inputs, eligibility, parameters, findings, limitations}`; create requires findings/limitations explicitly empty, revise may supply draft narratives; artifacts are attached separately and cannot be injected by spec. IDs/revision/timestamps/chain/request_id/status are generated or owned by the relevant operation. Native data quantities inside parameters remain explicit values; unsupported ad-hoc semantics are attributed caller input.

## Self-improvement lifecycle and authorization

Apply the existing [skill](../.agents/skills/self-improvement/SKILL.md) and [protocol](../.agents/skills/self-improvement/references/protocol.md) without changes. Event schemas, UUID paths, time policy, hashes, predecessor-ordered transitions, trial snapshots, restoration and separate adoption approval stay authoritative. Existing `.cache/workflow-improvement` data remains local Git-ignored evidence, not durable cloud memory.

The intended workflow is:

1. The coordinator uses capabilities and performs the user task.
2. At completion, it reviews meaningful efficiency evidence and existing related records. Ordinary job timings are operational telemetry; they are not automatically evidence that a workflow change is warranted.
3. If justified, observation/proposal tools preserve evidence and an exact complete draft. A proposal specifies changed files/text/signatures, tradeoffs, eligible trial tasks, finite trial boundary, measurements, acceptance, and restoration. New analytical physics claims additionally need scientific validation.
4. The coordinator presents that concrete revision to the user. Only actual explicit human approval authorizes the approved trial. MCP callers cannot manufacture this by supplying a boolean, quoted instruction, candidate state, or event file.
5. The coordinator follows the skill's snapshot/apply/start/measure/stop/restore/evaluate process. It writes approval/trial/transition/evaluation events through the already approved repository workflow; the initial MCP write surface deliberately omits those events.
6. The user chooses adoption, decline, or more data. Separate adoption approval is required. A new active reusable capability is then registered only within the exact approved scope; source/baseline changes require review.

The existing mechanism is instruction-based. Neither the current skill nor this proposal promises cryptographic human-approval verification, transactional cross-chat governance, automatic rollback in the background, or guaranteed trial expiry while no agent is running. improvement_status reports observed state/conflicts and evidence, not a fabricated enforcement guarantee. Active trial ownership is preserved; another chat does not silently take over its files.

Candidate collections support discoverability and reuse; improvement proposals support controlled change. They are separate objects with explicit links. A candidate cannot skip trial/adoption rules for skill/workflow modifications by being labeled a scientific capability. Equally, merely recording an ad-hoc analysis does not change the agent's skills or start an improvement trial.

## Python domain implementation files and signatures

These modules contain business behavior and no mcp imports. They import existing backend Config/ProjectPaths/records/resources and knowledge source APIs as necessary. JSONObject/JSONValue retain existing definitions. Only the functions listed here are proposed; additional helper functions/classes or changed schemas require an amendment. Every function, including internal helpers and tests, follows AGENTS.md docstring/annotation/no-comments rules.

### src/backend/project_records.py

Purpose: shared immutable revisioned record storage for case and analysis, separate from engine metadata. Uses existing safe allocation identities and bounded Windows locking. No scientific interpretation.

```python
initialize_record(paths: ProjectPaths, config: Config, kind: Literal['case', 'analysis'], document: JSONObject, request_id: str, *, allocated_id: str | None = None) -> JSONObject
read_record(paths: ProjectPaths, kind: Literal['case', 'analysis'], record_id: str, *, revision: int | None = None) -> JSONObject
revise_record(paths: ProjectPaths, config: Config, kind: Literal['case', 'analysis'], record_id: str, expected_revision: int, document: JSONObject, request_id: str) -> JSONObject
list_records(paths: ProjectPaths, kind: Literal['case', 'analysis'], *, status: str | None = None, offset: int = 0, limit: int = 100) -> JSONObject
validate_record(document: JSONObject, kind: Literal['case', 'analysis']) -> None
record_lock(paths: ProjectPaths, kind: Literal['case', 'analysis'], record_id: str) -> ContextManager[None]
write_record_json(path: Path, document: JSONObject, *, replace: bool = False) -> JSONObject
```

initialize_record validates UUID/document before allocation, initializes new or explicitly empty case/post directory, writes revision then pointer, returns record plus reference. Partial allocation/publication survives with locations in exception notes. read_record validates safe identity, exact schema/hash/chain and returns copied JSON, with no domain input verification and no writes except lock coordination if needed. revise_record checks CAS/request ID/terminal rules, complete schema and hash chain under lock, publishes once, returns revision/reference; stale/duplicate-conflicting requests ValueError. list_records gives stable sorted ID summaries/per-record issues and page counts, no implicit migration. validate_record is pure exact structural/field validation with ValueError. record_lock yields None while holding a safe bounded per-record lock under memory/project/.locks, releases in finally; contention OSError. write_record_json implements canonical exclusive staging/fsync/publication, replace only for pointers, returns manifest; unsafe path/collision/failed publication preserves evidence and propagates OSError/ValueError. None of these functions imports src.mcp.common; storage helpers remain independent to avoid reversing dependency direction.

### src/case/records.py

Purpose: case semantics and explicit evidence associations. Use project_records for storage and knowledge/source, analysis records and validated physics-job journal reads for references.

```python
create_case(paths: ProjectPaths, config: Config, spec: JSONObject, request_id: str, *, allocated_id: str | None = None) -> JSONObject
read_case(paths: ProjectPaths, case_id: str, *, revision: int | None = None) -> JSONObject
revise_case(paths: ProjectPaths, config: Config, case_id: str, expected_revision: int, spec: JSONObject, request_id: str) -> JSONObject
list_cases(paths: ProjectPaths, *, status: str | None = None, offset: int = 0, limit: int = 100) -> JSONObject
check_case_members(paths: ProjectPaths, members: list[JSONObject]) -> list[JSONObject]
```

create_case/revise_case validate exact CaseSpec and newly introduced references, retain prior stale members with issues under the rules above, then publish once. Return stored revision/reference plus checks; no native execution. read_case rechecks references without rewriting stored content. list_cases delegates safe discovery and returns summaries/issues. check_case_members returns ordered verified/live/stale/missing/unavailable results; malformed caller references ValueError, source problems reported per member with no auto-refresh. Write errors propagate with partial evidence and no rollback of unrelated publications.

### src/analysis/records.py

Purpose: analysis provenance, method/input eligibility, artifact snapshots and terminal recording. Uses existing source/file references and project_records; no plotting library, formula engine or numerical analysis implementation.

```python
create_analysis(paths: ProjectPaths, config: Config, spec: JSONObject, request_id: str, *, allocated_id: str | None = None) -> JSONObject
read_analysis(paths: ProjectPaths, analysis_id: str, *, revision: int | None = None) -> JSONObject
revise_analysis(paths: ProjectPaths, config: Config, analysis_id: str, expected_revision: int, spec: JSONObject, request_id: str) -> JSONObject
attach_analysis_artifacts(paths: ProjectPaths, config: Config, analysis_id: str, expected_revision: int, files: list[JSONObject], request_id: str, *, allowed_roots: Sequence[Path]) -> JSONObject
finish_analysis(paths: ProjectPaths, config: Config, analysis_id: str, expected_revision: int, status: Literal['completed', 'failed'], findings: str, limitations: str, failure: JSONObject | None, request_id: str) -> JSONObject
list_analyses(paths: ProjectPaths, *, status: str | None = None, offset: int = 0, limit: int = 100) -> JSONObject
check_analysis_inputs(paths: ProjectPaths, inputs: list[JSONObject], eligibility: JSONObject) -> list[JSONObject]
```

create/revise enforce exact spec/draft state, explicit eligibility, registered/ad-hoc method validity, selected file hashes and warnings; errors ValueError/OSError before final pointer publication. read returns stored revision with separate evidence/artifact checks; stale evidence is reported, not rewritten. attach checks all names/roles/source paths/caps before copying, then source stability, blob hashes and expected revision under publication lock, appends one revision and returns artifacts/reference; staged/orphan identical blobs may survive failure without attachment. allowed_roots is an explicit nonempty safe absolute path sequence; no implicit drive access. finish enforces terminal completeness/failed reason and rechecks evidence immediately before terminal publication; registered method schemas additionally validate the exact input/output projection defined above. check_analysis_inputs returns ordered issue/availability/provenance reports and rejects disallowed failed/recovered/incomplete selections with ValueError; it never computes scientific values or repairs data. list returns paged safe summaries. No function calls a native runner or dynamically executes method text.

### src/plan/catalog.py

Purpose: independent reusable collections/candidates with immutable files and schema/evidence verification, plus read-only inventory of existing agent skills. Uses standard library, jsonschema, backend path/resource conventions, and project record I/O helpers; not the SDK.

```python
list_collections(paths: ProjectPaths) -> JSONObject
list_capabilities(paths: ProjectPaths, collection: Literal['workflow', 'analysis', 'skill'], *, offset: int = 0, limit: int = 100) -> JSONObject
read_capability(paths: ProjectPaths, reference: JSONObject) -> JSONObject
validate_capability(manifest: JSONObject) -> None
create_candidate(paths: ProjectPaths, config: Config, spec: JSONObject, files: list[JSONObject], request_id: str, *, allowed_roots: Sequence[Path]) -> JSONObject
list_candidates(paths: ProjectPaths, *, collection: str | None = None, offset: int = 0, limit: int = 100) -> JSONObject
read_candidate(paths: ProjectPaths, candidate_id: str) -> JSONObject
publish_capability(paths: ProjectPaths, config: Config, candidate_id: str, approval_reference: JSONObject, *, expected_candidate_sha256: str) -> JSONObject
inventory_repository_skills(paths: ProjectPaths) -> JSONObject
```

List/read functions validate IDs, immutable hashes/files and schemas; absent collections are empty without creating directories. validate_capability is pure and requires closed schemas/declarative procedure/evidence fields. create_candidate validates source roots/files/caps like analysis attachment, writes a new immutable candidate with a generated UUID/time, returns manifest reference; cannot publish active content. publish_capability is Python-only and requires exact candidate hash, mechanically valid explicit approval evidence/expected baseline and a new vN; verifies evidence/files and exclusive publication, returns version reference, never edits skill source or executes a trial. It is not exposed as an MCP tool and cannot authenticate a human from a plain supplied string. inventory_repository_skills safely reads only fixed .agents/skills and .codex/agents entries, returns metadata/hashes/issues, no full-machine search or instruction edits. I/O/conflict/schema problems propagate; malformed independent entries are returned as listing issues without silently trusting them.

### src/plan/improvement.py

Purpose: support the existing self-improvement protocol without changing its approval gates, record location, state semantics or restoration mechanism. This is record inspection/publication support, not an optimizer.

```python
read_improvement_status(paths: ProjectPaths, *, proposal_id: str | None = None) -> JSONObject
record_observation(paths: ProjectPaths, config: Config, details: JSONObject, request_id: str) -> JSONObject
create_improvement_proposal(paths: ProjectPaths, config: Config, proposal_id: str, observation_ids: list[str], markdown: str, request_id: str) -> JSONObject
validate_improvement_event(event: JSONObject) -> None
```

read validates all relevant immutable event fields, references/revisions, predecessor chains, snapshots and bounds; returns observed state/issues with conflicting/malformed evidence excluded from decisions. No writes or automatic state transitions. record_observation generates fresh event UUID/configured time, validates caller detail fields/source locators and finite actual/estimated measurement labels, writes exclusive UTF-8 JSON; request_id is mapped in the owning MCP job receipt rather than adding a forbidden event-schema field. The coordinator determines whether an observation is meaningful. create_proposal validates UUID/observation references and presence of protocol-required sections, writes exact caller Markdown to a new safe path, computes SHA-256, writes linked immutable proposal event and returns both references. Baseline/change completeness, evidence sufficiency, scientific truth and trial suitability still require the coordinating agent and human review; Markdown section presence does not prove them. If Markdown publication succeeds but event publication fails, preserve/report that path; never claim the proposal event exists. validate_event is pure and enforces existing event schema including per-kind details; no new approval event producer is added. OSError/ValueError/conflicts propagate with partial references; no event overwrite or automatic lifecycle repair.

### src/mcp/project_mcp adapter

`catalog.py`, `operations.py`, `__main__.py` and package docstrings have exactly the signatures in the shared API appendix. catalog describes the tools/resources/schemas/examples above and reports empty collections accurately. read_operation dispatches read-only domain APIs; write_operation dispatches cases, analyses, candidate/observation/proposal creation through the durable management job manager. It rejects attempts to submit physics tools, raw file writes, lifecycle events or capability promotion. It does not import src.mcp.srim_mcp.operations; shared native evidence reads call existing Python APIs directly.

Resources additionally include `project://cases/{id}/{revision}`, `project://analyses/{id}/{revision}`, `project://collections/{collection}`, `project://capabilities/{collection}/{id}/{version}`, `project://candidates/{id}`, and `project://improvement/{proposal_id}`. These use strict identity matching and the same verified readers; revisions are required for frozen evidence. Current-pointer views are tool results with explicit current revision/hash.

## Verification and approval criteria

New domain tests: `test/backend/test_project_records.py`, `test/case/test_records.py`, `test/analysis/test_records.py`, `test/plan/test_catalog.py`, and `test/plan/test_improvement.py`. Synthetic data covers empty allocations, immutable chains/pointers, partial publication, stale CAS, concurrent independent/same-record writers, duplicate request jobs, source deletion/tampering, created/running/failed sources, ad-hoc and empty registered methods, recovered/native distinctions, unavailable quantities, artifact caps/changes/links, terminal immutability and candidate nonpromotion.

Improvement tests use fixture observations/proposals/transitions/snapshots, never modify actual active trials or real skills. Cover malformed events, mismatched proposal hashes, conflicting successors, incomplete sections, invalid timezone, rejected/adopted/more-data observations, expiration/restoration reporting, and partial writes. A well-formed caller approval string must not enable any MCP mutation that starts a trial, publishes an active capability, or edits an instruction file.

SDK integration covers both named processes at once; a project server must not start a fake/native physics process for any of its operations. Existing backend/SRIM/knowledge regressions remain passing. No native engine experiment or scientific method benchmark is necessary to verify provenance management. Actual skill trial/governance and physical correctness remain qualified as described above.

This design includes management records and discovery from the start, while executable scientific analyses and reusable active collections remain empty. Adding those capabilities later requires concrete methods or workflow/skill changes and their own reviewable proposals. Implementation approval covers the three proposal documents together, not an implied permission to invent the initial analysis methods or to train/adopt agent behavior automatically.
