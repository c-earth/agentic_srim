# MCP implementation handoff

Closeout date: 2026-10-22. Base commit: `66203fa`.

## Additional closeout state

The complete approved implementation and documentation are ready for commit. The [server guide](guides/mcp-servers.md) contains the saved tool table and usage; the [interface reference](reference/mcp-format.md) and [project guide](guides/project-management.md) describe the implemented contracts. No implementation work remains outstanding under the approved proposal.

After implementation verification, the user requested project-local Codex compatibility. Both server registrations are now in `.codex/config.toml`, matching `doc/examples/mcp-servers.toml`. The installed Codex CLI's `mcp list --json` reported both enabled with the expected absolute interpreter, module arguments and workspace directory. This validates configuration discovery, not attachment to an already-running chat. Reloading the project or starting a new session remains the host activation step.

The original local `.cache/mcp-implementation/verification.json` records 354 passing tests with warnings treated as errors: MCP 14, backend 42, case 2, analysis 2, plan 2, SRIM 273 and knowledge 19. Its statement that host registration was unapplied describes that earlier verification boundary; the registration above happened later. The recorded implementation hashes allow checking the tested code without repeating unchanged long regressions. Approved proposal snapshots remain under `.cache/mcp-implementation/approved/`. These ignored local records are not durable backups or committed artifacts.

The real SDK stdio test created two independent cases through one client session; separate case tests verified interleaved revisions and one analysis reference shared by two cases. No native physics experiment was added for this task. Existing calculation source, settings, installations and historical scientific records were not edited.

Commit closeout reran all 14 MCP tests with warnings as errors: passed in 15.461 seconds. All 33 recorded Python/configuration/dependency file hashes remained unchanged from the original verification. Staged whitespace, Python documentation/type/comment, documentation-link and TOML checks passed. The handoff and project registration are included in the delivery commit.

`memory/mcp/{srim_mcp,project_mcp}/server.lock` appeared after host configuration discovery. These generated coordination files are ignored and excluded from the commit; no runtime evidence was deleted. Their presence does not independently establish a running process. No native runs or automatic job recovery should be inferred from configuration discovery.

Self-improvement review found no active trials or malformed lifecycle records. One local observation records avoidable SDK import/attribute assumptions; no workflow change, trial or adoption was applied.

## Remaining qualifications

Native installation/mode readiness, reviewed dialog policies, parser completeness and independent scientific validity remain separate from MCP transport/configuration success. The approved catalogs preserve those distinctions. A new native validation campaign, remote transport, automatic trial/adoption, or design changes require their own scoped work; none is pending as part of this commit.
