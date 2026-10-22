# Figure artifacts and records

Use repository-root `.local/scientific-figures/` for this skill's artifacts and records. This is persistent local, Git-ignored storage, not a durable backup. Deliver ZIPs when requested; do not automatically clear any records.

Preserved cache dependencies live under `.local/.cache/`, which is protected persistent storage rather than routine scratch. Historical packages and records retain their original bytes, embedded paths and hashes. Consult `.local/migrations/` inventories and reports for old-to-new paths; these mappings do not redirect reads automatically. Legacy scripts may require mapped inputs and outputs before separately authorized reuse. Do not recreate missing evidence or treat a preserved historical helper as authorization to run it.

## Identity and layout

Give each figure a descriptive lowercase ASCII kebab-case ID independent of publication numbering, for example `rag-system`. Reuse its folder for revisions of the same scientific message. Use a new ID for a distinct figure; resolve ambiguous identity with the user. Never combine unrelated figure families. Check existing folders before choosing an ID; do not overwrite another figure or follow redirected paths outside the repository.

Use this layout, creating optional directories only when needed:

```text
.local/scientific-figures/<figure-id>/
  README.md
  references/
  versions/v001/
    figure.svg
    figure.pdf
    figure.png
    source/
    notes.md
    caption.md
    discussion.md
    changes.md
    review.md
    verification.md
  packages/
```

README.md identifies the message, latest delivered version, current work, and relative links to versions and packages. Version IDs are `v001`, `v002`, and subsequent integers with at least three digits, allocated as one greater than the highest existing number, including incomplete versions. Never reuse or renumber a version. A delivered version is immutable; corrections create a new version. Store incomplete work in its allocated version and label it incomplete in README.md and verification.md. On interruption, inspect saved state before resuming; never treat incomplete exports as delivered.

SVG, PDF, and PNG are the default exports for a vector scientific diagram. Preserve the actual editable original for other figure types; record requested or technically unavailable formats explicitly instead of fabricating exports. Keep temporary scripts, renders, and checks inside that version's source/ directory or an optional checks/ directory. Put shared reference materials in references/ and record original locations and provenance in notes.md. Copy only relevant materials when permitted; otherwise record their locations and availability.

## Automatic checkpoints

The authoring agent saves records when planning is ready, after each meaningful revision, after review, and before delivery. This is part of the active task, not continuous conversation capture or a background job. Saving records does not authorize unapproved figure or code changes. Before generating, save the exact authoring source or generation prompt and reference identifiers in source/. Preserve an executable script when script-based rendering is used, with the exact interpreter command, dependencies and versions, data inputs, parameters, fonts, and random seeds when applicable. For external generation, record tool/model information if exposed and state that exact reproduction may be unavailable. Never claim reproduction was tested unless it was.

Required records use UTF-8 Markdown, clear headings, relative artifact links, and explicit `unknown`, `unavailable`, or `not assessed` values rather than invented details:

- notes.md: scientific message, evidence and original source locations, supported claims versus schematic examples, audience, dimensions, publication constraints, palette/role/arrow meanings, concise public design rationale, limitations, and reproduction instructions.
- caption.md: current caption draft, panel/abbreviation/encoding definitions, source attributions, and unresolved claims. Mark it a draft until approved.
- discussion.md: relevant user requests, feedback, and user-facing design decisions with available chat/turn references; identify summaries as summaries and quotes as quotes. Record only task-relevant material. Do not save hidden reasoning, private scratch work, secrets, or unrelated chat history. If history is unavailable, say so.
- changes.md: preceding version or initial draft, requested corrections, actual changes, unresolved requests, and approval references where required. Record improvement suggestions here; changing the skill itself still follows the self-improvement protocol.
- review.md: actual review results, inspected version and evidence, unresolved findings, and reviewer identity when used; write `not assessed` if no review occurred. The parent saves reports returned by read-only reviewers; storage requirements do not authorize delegation.
- verification.md: complete/incomplete status, export availability, checks actually run and outcomes, intended-size visual inspection status, missing checks, and reproduction status. Failed checks remain visible until resolved in a subsequent entry or version.

At each checkpoint update only the active, undelivered version's records and README.md. Preserve earlier decisions with dated entries rather than silently replacing their history. Do not create a new version solely for a conversational acknowledgment. After delivery, further artifact or record changes belong to a new version linked to its predecessor.

## Packages and concurrency

When ZIP delivery is requested, place the ZIP under that figure's packages/ using `<figure-id>-<version>.zip`. Include the delivered exports, source/, all version records, relevant references, and a manifest listing included paths and SHA-256 hashes. Include historical versions only when requested. Verify archive integrity and member hashes before delivery; never overwrite an existing package. Existing legacy packages may remain where originally delivered and be linked from README.md.

One authoring agent owns a figure folder during a task. Reviewers receive stable version paths and do not edit them. If another active writer or conflicting version allocation is found, stop writes and coordinate ownership; these instructions are not filesystem locks. Do not delete, move, or reorganize legacy artifacts automatically. For a requested revision of a legacy figure, copy the selected input into the new figure's references/, identify its original path and version, and continue using the next available new-layout version. Record absent historical sources without reconstructing them as originals.
