# Project Agent Instructions

## Project continuity and updates

- Before planning or performing project work, read `PROJECT_CHECKLIST.md` and `HANDOFF.md` in the project root, together with these instructions. Treat them as project context; the current user request takes precedence.
- Use `PROJECT_CHECKLIST.md` as the implementation backlog and acceptance checklist. Use `HANDOFF.md` for current verified state, decisions, limitations, and the next concrete action.
- After a prompt that changes project code, configuration, architecture, dependencies, or implementation status, update both files where affected before completing the task. For a discussion-only prompt, update them only when the user requests it or accepts a decision that changes the plan. Avoid no-op edits.
- Mark checklist items complete only when their acceptance criteria are met and supported by verification. Record the relevant file paths and verification evidence; distinguish proposed, implemented, and verified work.
- In the handoff, record what changed, checks actually run and their outcomes, unresolved issues, and the next actionable step. Include the date of meaningful updates. Never claim an unrun test or simulation passed.
- Preserve incomplete items and important decisions. If scope changes, record why an item was deferred, replaced, or removed instead of silently dropping it.
- Keep both files concise and consistent. Do not store secrets, large outputs, full chat transcripts, or private reasoning in them. Link to artifacts and logs when those exist.
- These files track work; they do not authorize running simulations, installing dependencies, contacting services, or carrying out future backlog items without a relevant user request.

## Project and documentation cleanliness

- Keep each root document focused: `README.md` owns setup, usage and operational contracts; `PROJECT_CHECKLIST.md` owns acceptance criteria, incomplete work and deferred architecture; `HANDOFF.md` owns current state, active directions, latest verification, unresolved limits and the next action. Link to the owning document instead of repeating its contents.
- Write the README for new users setting up their own computers. Use portable setup examples and explain available capabilities; omit author-specific paths, chat references, approval history and session status.
- Replace superseded status in place. Do not append chains of session closeouts, review transcripts, obsolete test counts, completed-task narratives or deleted artifact paths. Retain only history needed to preserve an active decision, approval boundary, unresolved contradiction or verification limitation.
- Keep `HANDOFF.md` normally within 800 words and 80 lines. Prefer concise tables or bullets for current evidence. Remove stale details before adding new ones; never meet the size target by dropping unresolved work, user constraints, pauses or material limitations. Explain any necessary exception briefly in the handoff.
- Preserve every incomplete acceptance requirement when consolidating the checklist. Mark completion only with evidence; explain deferrals, withdrawals and replacements. Keep completed evidence concise and avoid duplicating the handoff's execution log.
- After relevant changes, check all three documents for contradictory status, stale paths, repository state, test counts and next actions. Documentation-only housekeeping does not authorize native execution or resume paused implementation.
- Keep only application code/configuration, reusable support, regression tests/fixtures and necessary documentation in the permanent project structure. Disposable checks must clean their verified workspaces and empty parents. Preserve actual application attempt archives under the runtime retention contract; never remove fixtures or audit records merely to make the tree look clean.
- Maintain `.gitignore` for environments, runtime data, temporary/build/test caches, machine-local settings and credentials. Keep application configuration, regression fixtures and continuity documents trackable; do not use broad native-output/binary patterns that hide required fixtures. Review tracked/staged paths before commits, and keep local author configuration local rather than modifying global Git settings.
- Scope ignore rules to this project's actual layout and tools. Do not add generic editor, operating-system or unused-tool boilerplate; add patterns when a concrete need exists.
- Preserve fixture bytes in Git attributes. Native captures and their checksum metadata must not undergo automatic line-ending conversion; verify staged fixture blobs against working files when changing repository attributes or preparing the initial snapshot.
- Routine Git file changes, including deletion, are allowed within authorized work. Preserve committed checkpoints and prior versions: create new commits (or revert with a new commit) rather than amending, rebasing, force-pushing, deleting checkpoint references, expiring reflogs or pruning history. These history-changing actions need a new explicit user instruction. Never delete `.git` or bypass this boundary through alternate flag order, aliases, wrappers or APIs. Local prefix rules cover common command forms; they are not immutable backups or a complete parser of Git options.

## User directions and approval continuity

- Treat the user's directions as persistent constraints that survive turns, interruptions, compaction, and agent handoffs. Before acting, reconcile the latest request with the active directions and approval record in `HANDOFF.md`. Preserve later corrections explicitly when they supersede an earlier direction.
- Record changed scope, required layout, preferences, prohibitions, approvals, rejected proposals, pauses, and unresolved questions promptly in the continuity files. Keep a concise active record rather than relying on memory or a compaction summary. An unanswered permission question is not approval.
- Follow the filesystem and architecture agreed with the user. Ask about uncertain implementation choices or departures from that plan before implementing them. Do not request consent again for the same already approved action and scope unless permission was revoked or circumstances materially changed.
- At startup or resumption, identify the active objective, scope, stopping state, approval boundaries, unresolved decisions, and next action from the saved record. Check relevant evidence when a summary or old status conflicts with current files. If a material direction cannot be recovered from available chat history or files, ask the user instead of inventing a value or silently assuming consent.
- A status question, documentation audit, handoff request, or session-close request does not itself resume paused implementation, simulations, or GUI interaction. Preserve stops and permission revocations. Renewed permission applies only to its stated scope.
- Distinguish observed simulator behavior from documentation, third-party claims, and interpretations. Identify exact sources or artifacts, retain contradictions, and explicitly correct earlier claims. Do not infer a native defect or validated physics from one warning or an untested mapping.
- Preserve the original SRIM installation. Use isolated copies under `runtime/` for authorized native tests, retain failed-attempt evidence, and record integrity checks actually performed. Preserve the approved installation-root, backend-layout, worker-isolation, and scientific-validation decisions in the handoff.
- The user's 2026-10-15 application-only cleanup supersedes retention of historical development diagnostics, including failed probes, temporary scripts, reports and demonstration runs. Keep application code, configuration, regression tests/fixtures, the original installation and reusable worker support files. Future temporary checks must clean up their disposable workspaces; actual application runs must still retain their per-attempt audit artifacts.
- Worker layout is symmetric by the user's 2026-10-15 instruction: `runtime/workers/worker-1/{sr_module,trim}/` are sibling simulator workspaces; `.lock` and `active_attempt.json` are shared at worker root. This layout does not mimic the original SRIM installation. Each worker runs one backend at a time.
- Follow the agreed component-first test layout: backend tests under `tests/{sr_module,trim}/` with input/output/execution modules and fixtures; shared process/workspace, persistence and interface tests under `tests/{execution,storage,interfaces}/`. Do not accumulate backend tests in a top-level bundle or require renewed approval to follow this agreed organization.
- The later whole-project audit instruction authorizes removal of departures and bounded isolated execution verification with complete temporary-artifact cleanup. The root examples folder was removed; required regression request data belong in test fixtures, and MCP receives request objects directly. Do not restore example/probe/report folders as permanent application structure.
- Completed historical execution approvals do not carry forward into a new review. The current review's scope, individually pending behavior changes, withdrawn proposals and verification limits are recorded in `HANDOFF.md`; maintain that explicit list instead of an unspecified reference to remaining suggestions.
- Use the single installation root selected by `srim_directory` in `configs/application.toml`, outside this project. Preserve this host's selected root recorded in `HANDOFF.md`; derive backend locations from configuration and do not restore a project-local `srim/` duplicate.

## Context checkpoints

- The user reports a 475000-token context window and requests notification, a saved handoff, and a pause before approaching 90% (427500 tokens). Use conservative, evidence-based estimates and identify them as approximate; do not imply an exact live counter is available. Save unresolved work early rather than waiting for the threshold.
- Do not initiate or request compaction. Automatic compaction is controlled by the host and cannot be guaranteed disabled by the assistant. Preserve pending issues, approvals, verification limits, and stopping state in the continuity files. A continuity audit does not resume paused simulations or GUI testing.
- Before a substantial work batch, reassess context using available usage evidence, conversation length, and accumulated tool output. Prefer bounded searches and concise outputs. If the estimate could approach the checkpoint during that batch, notify the user, update both continuity files, and stop substantial work first. Counter uncertainty is a reason to checkpoint early, not ignore this instruction. Clarify an unknown or changed capacity in a later session instead of silently assuming one.
- After unexpected automatic compaction, first reconcile the current user directions and saved stopping state. Do not let a summary restart paused work. Report a missed checkpoint and save the handoff before substantial continuation when the user requested a stop at that boundary.

## Session close and next-agent handoff

- When the user asks to end the session, finish only the authorized closeout. Save the objective, implemented versus verified state, unresolved issues, active directions, approval boundaries, evidence paths, checks actually run, potentially open processes, and next concrete action in `HANDOFF.md`. Update affected checklist items without marking unfinished implementation complete.
- Verify that the three continuity files agree on active directions and stopping state. Give a concise closeout and end the turn. Archive when requested as part of closing the chat. Do not create a successor task, schedule future work, or restart testing without authorization.

## Python documentation style

- Place docstrings only as the first statement of a module, a function/method body immediately after its signature, or a class body immediately after its declaration (including any inheritance list).
- Do not add standalone or inline `#` comments, or explanatory string blocks elsewhere in code. Put necessary explanations, assumptions, and non-obvious decisions in the enclosing module, class, or function docstring.
- Docstrings must describe inputs and outputs. For functions/methods, describe parameters and return values; explicitly state when there are no inputs or no return value. For classes, describe construction inputs and the resulting object's purpose/public data. For modules, describe accepted inputs and exposed results or side effects as applicable.
- Specify types either in Python annotations or in the docstring; duplication is optional. Include units, array shapes, normalization, and side effects when relevant to interpreting inputs or outputs.
- Use valid Python type annotations when placing types in signatures, for example `values: numpy.ndarray`; document array dtype and shape in the docstring where needed.

## Python environment

- Project root: the directory containing this `AGENTS.md`.
- Virtual environment: `.venv` under that project root.
- Required Python interpreter: `.venv\Scripts\python.exe` under that project root on Windows. Resolve its absolute path from the current clone rather than using another computer's checkout path.
- Use this interpreter for all authorized Python scripts, tests, and Python tooling in this project. Do not use global Python, `py`, or a bare `python` command.
- Install and manage dependencies through this interpreter with `-m pip`; do not use bare `pip` or install project dependencies globally.
- Call the interpreter directly; activation is optional and is not required.
- For ordinary tests, explicitly set both `AGENTIC_SRIM_INTEGRATION` and `AGENTIC_SRIM_TRIM_INTEGRATION` to `0`; enable native gates only within relevant execution authorization. Use `-B` and `PYTHONDONTWRITEBYTECODE=1` for disposable Python checks so subprocesses also avoid bytecode artifacts. Remove the empty `runtime/test_workspaces` parent after tests, checking its contents and resolved path first.
- If the environment is missing or unusable, report the issue before selecting a different interpreter.

## PowerShell examples

From the project root:

```powershell
& .\.venv\Scripts\python.exe script.py
& .\.venv\Scripts\python.exe -m pip install <package>
& .\.venv\Scripts\python.exe -m pip check
```

These instructions specify which environment to use; they do not grant additional permission to execute commands.
