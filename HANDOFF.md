# Project handoff

Updated: 2026-10-15

## State and next action

SR Module interval tables and TRIM modes 1–8 are implemented and software-checked through shared execution/storage and CLI/MCP interfaces. Scientific validation remains deferred: successful runs are CHECKED, never scientifically ACCEPTED.

Native verification and cleanup are complete. Git uses main and repository-local c-earth author settings; public origin https://github.com/c-earth/agentic_srim was created and main pushed with user approval. README now targets new users with portable Windows setup, capabilities, CLI and generic MCP configuration; author/session references are excluded by AGENTS.md. Minimal ignore rules and fixtures remain. Implementation and native execution remain paused; wait for the next request.

## Active constraints

- Follow AGENTS.md. Use D:\projects\agentic_srim\.venv\Scripts\python.exe; do not install dependencies or resume simulations without relevant authorization.
- Local `.codex/rules/git.rules` allows routine Git file changes/new commits/reverts and `push origin main`; checkpoint/history changes need explicit instruction. AGENTS.md prohibits bypasses through flags/aliases/APIs. Prefix rules are limited; reload requires restarting Codex with the trusted project layer. The rule file stays ignored/local.
- Preserve the external installation D:\local\srim\srim_2013. Derive paths from configs/application.toml: runtime ../runtime, timeout 30 seconds, one attempt. Never restore a project-local SRIM duplicate.
- Each runtime has one locked worker with sibling workers/worker-1/{sr_module,trim}/ workspaces and shared .lock/active_attempt.json. Separate runtimes can run concurrently; no shared-runtime worker pool exists. The marker retains the last owner after completion.
- Keep component-first tests under tests/{sr_module,trim,execution,storage,interfaces}/. Preserve regression fixtures and actual application attempt archives. Remove disposable test/probe workspaces; no permanent examples/probes/reports folders.
- The host owns AI reasoning. Deferred architecture stays absent until needed; see PROJECT_CHECKLIST.md. No application-side agents or empty placeholder groups.
- SR grid-completeness proposal (original item 5) was withdrawn; do not resume its implementation/probes without explicit direction. TRIM detail-completeness proposal (item 6) remains unchanged for insufficient evidence. No pending approved fixes remain.
- Context checkpoint: user-reported capacity 475000 tokens; notify, save and pause before approximately 427500. No exact live counter exists. Do not request compaction; preserve stops after host-triggered compaction.

## Latest verification evidence

Checks on 2026-10-15 used the required interpreter, -B and inherited bytecode suppression.

| Check | Observed result |
|---|---|
| Full native-enabled suite | 94 tests / 61.932 s: 93 passed; one new test incorrectly expected owner-marker deletion. All 26 native calculations reached CHECKED. |
| Corrected workspace test | 1 passed / 6.002 s, eight more calculations. Corrected the assertion to the retained-owner contract; application behavior unchanged. |
| Combined native coverage | All five native methods passed across those runs; 34 calculations total (12 SR, 22 TRIM). No second full native run. |
| Final ordinary suite, both gates 0 | 94 tests / 24.543 s: 89 passed, five native skipped. |
| Structural/fixture checks | 44 Python files passed AST/docstring-placement/no-comment checks; three TOML files parsed; 452 fixture checksums verified. |
| Integrity and cleanup | Before/after hashes matched 219 installation files, 4 SR fixtures, 450 TRIM fixtures and production metadata.sqlite. SQLite integrity_check=ok; all four tables empty. Disposable runtimes, empty test parent and bytecode removed; no native process visible. |
| Documentation/Git follow-up | 506 tracked paths reviewed; 454 fixture blobs byte-identical. Portable README: ten PowerShell blocks, JSON/TOML examples and three local links checked; nine MCP tools documented, two requests validated read-only. All 26 open items retained. No suite/installation/simulator rerun; application source/settings unchanged. |
| Local Git rules | Native execpolicy checker passed 39 forms: 21 allow (including existing Python rule), 15 prompt, three unmatched. Tested commands were not executed. Existing Python policy preserved; app reload not performed. |

Retained native evidence: tests/{sr_module,trim}/test_execution.py covers SR fixture rows, all eight TRIM modes, both mode-8 choices and optional outputs off. tests/execution/test_workers.py:NativeWorkspaceTests verifies four overlapping SR/SR, TRIM/TRIM and mixed pairs, independent archives and sealed retrieval after reuse. tests/interfaces/test_mcp.py verifies fresh native CLI/MCP execution, nine tools and bounded pages for fourteen TRIM output families.

Backend/shared tests retain regression coverage for storage integrity, input validation, native parsing and interface/worker diagnostics.

## Remaining limits

Active timeout/cancellation, retries and abandoned recovery have mocked/synthetic coverage; native interfaces checked completed-run cancellation and empty recovery. Forced process-tree death/PID-recording gap, service/session-0 operation, clean-environment rebuild, CI, backup/restore and independent scientific accuracy/convergence remain unverified.

Installed GUI modes 6=full recoil, 7=full monolayer, 8=quick KP conflict with Input PDF p. 8-17 (6=quick/7=full, no 8). Mode-6 collision levels 1/2 reproduce a warning even with GUI-matched controls; internal cause is unknown. Keep mode-6 collisions off and KP full-cascade rejection; never substitute mode 7. Mode-8/collisions=1 requires explicit summary/every, owner-checked dialog handling and provenance. Three tested 500-eV events gave identical records for both choices; do not generalize. Recoil inputs do not transport external neutrons/electrons/photons.

Fresh subprocesses exercised current source. The desktop MCP connection was not explicitly restarted; its loaded-code freshness and host approval prompts remain unknown. An MCP server may remain open; no native processes were visible at the verification closeout.
