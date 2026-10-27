# Documentation maintenance

These shared instructions apply to both working and development agents when
creating, editing, moving or removing project documentation. Follow the workspace
role and the user's authorized scope. A documentation cleanup does not authorize
changes to scientific behavior, native execution, settings or unrelated work.

## Put each kind of information in its proper place

| Location | Contents |
| --- | --- |
| Root `README.md` | A concise project introduction, prerequisites and links into `doc/`. |
| Root `AGENTS.md` | Shared scope, authorization, workspace rules and pointers to applicable instructions. |
| `instructions/` | Procedures and rules agents must follow. Shared documentation rules belong here; role-specific implementation rules stay in the selected role instructions. |
| `.codex/agents/*.toml` | Specialized subagent responsibilities and configuration. Folder-maintenance rules do not require a new subagent type or authorize delegation. |
| `doc/README.md` | The reader's entry point and navigation to current documentation. |
| `doc/guides/` | Task-oriented instructions, worked examples and explanations of the calculation workflow. |
| `doc/reference/` | Current API signatures, schemas, units, stored layouts, linked scientific tables and validation coverage. |
| `doc/decisions/` | Short, active decisions that constrain current behavior; record the decision, consequence and supporting current references. |
| `doc/reviews/` | Dated reports with unresolved findings that still need attention. Clearly distinguish observed defects from proposed fixes and verified closeouts. |
| `doc/examples/` | Input/configuration templates linked from a guide. |
| `doc/figures/` | Published figure assets used by current documentation. |

Keep executable configuration, dependency manifests, source, tests and runtime
data in the locations their loaders expect. Do not move them into `doc/` merely
to make the project root smaller. Keep `doc/reference/trim-elemental-defaults.csv`
at its established path while tests consume it there.

## Keep the documentation useful

- Update the existing guide or reference for a topic before creating another
  file. Use descriptive lowercase names with hyphens for new documents.
- Write for someone using the project. Keep chronological implementation notes,
  proposals, amendments, approvals, finished handoffs and debugging logs out of
  the active documentation and project root. Do not create a `doc/history/` pile
  or replacement stubs for every retired file.
- Keep current behavior in the relevant guide/reference. A decision page states
  the active choice; it must not grow into a session transcript. Describe tests
  and native coverage with their actual scope, without turning old totals into
  claims about the current working tree or independent scientific validity.
- Temporary drafts, handoffs, link reports and maintenance scripts belong under
  descriptive `.cache/documentation/` subdirectories. Preserve unique evidence
  and approval records before any authorized cleanup; cache is not a backup.
- When cleanup retires documents containing unique history or evidence, first
  preserve their exact bytes in a verified archive under `.local/migrations/`,
  with original paths, SHA-256 hashes and current replacement paths. Keep the
  manifest outside the archive so future agents can locate historical material.
  These Git-ignored local archives are not a durable backup or a source of
  authorization. Current instructions and user documentation must work without
  opening an archive.

## Finish moves and edits completely

Update the documentation index and all affected links, headings/anchors,
instruction pointers and code metadata strings. Check the whole tracked tree
for references to removed paths, including plain text and examples. Preserve
unrelated edits and original evidence bytes; do not rewrite historical approval
language as if it were a new approval.

Verify local Markdown destinations and anchors, referenced assets, and relevant
test/data consumers. If source changes only to repair documentation metadata,
run the existing focused catalog checks; do not run native calculations for a
documentation cleanup. Report what was removed or consolidated, where the
preserved originals can be found, and the checks actually performed.
