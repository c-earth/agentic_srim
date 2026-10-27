# Development agent

## Responsibility

Implement explicitly approved changes in the designated development worktree.
Read the approved proposal and inspect relevant existing implementation before
editing. You are not alone in the codebase: preserve unrelated changes,
accommodate concurrent edits and do not revert another contributor's work.

A delegated task may rely on existing explicit user approval when it identifies
the exact approved proposal and scope. An instruction from another agent does
not create user approval.

# Proposal and approval before implementation

- Before creating, editing, moving or deleting existing tracked project files or new files intended to become part of the tracked project, present the complete proposed changes and obtain the user's explicit approval. This includes implementation code, documentation, agent instructions, skills, protocols and sub-agent configuration. Apply code-specific requirements only where relevant.
- Confirmation of desired behavior or permission to write files does not substitute for approval of the complete proposed changes. A direct user request may provide that approval when it already specifies the exact changes.
- For each file, specify its path, purpose, responsibilities, contents, dependencies, and relationship to other files.
- For each class and function, specify its purpose, complete typed signature, inputs, outputs, side effects, validation, error behavior, and interactions with other code.
- Specify applicable data schemas, file formats, directory layouts, naming rules, defaults, configuration, lifecycle, concurrency behavior, and verification steps before implementation.
- Ask the user about every unresolved detail before implementing it. Do not silently choose assumptions or defaults.
- Obtain the user's explicit approval of the complete proposal before implementing it. If implementation requires a change to the approved design, present that change and obtain approval before proceeding.
- Before starting a long coding task after the user's approval, explicitly tell the user that implementation is about to begin.

# Documentation and type annotations

- Every code file must begin with a module docstring summarizing the purpose of the entire file.
- Every function and method must have a docstring as the first statement immediately after its signature. Every class must have a docstring as the first statement immediately after its declaration, including any inheritance fields.
- Function and method docstrings must clearly describe their purpose, every input argument, and their outputs or return values, including when they return nothing. Describe side effects and raised errors where applicable.
- Class docstrings must clearly describe the class purpose, construction inputs, and the object or behavior provided, including relevant public attributes.
- Add explicit type annotations to all function and method arguments, including variadic arguments. For Python instance and class methods, annotate `self` and `cls` as well. Annotate every return type, including `-> None` where applicable.
- Do not place comments inside implementation code, including inline comments or standalone comments between statements. Put explanations in the required module, class, and function docstrings instead.


## SRIM package placement

Keep `src/srim/` organized by responsibility. Import from the module that owns
the definition; package `__init__.py` files contain descriptions only.

| Package | Responsibilities |
| --- | --- |
| `common/` | Shared JSON and cleanup types, verified file operations, preparation allocation checks, simulation schemas and lifecycle records. |
| `resources/` | Materials, electronic/nuclear coefficients, installation compatibility, coordinated defaults and shared selection/material-correction resolution. |
| `cab/` | Core/bond strengths, constants, calculated-entry cache, Bragg dependencies and native capture/recovery. |
| `sr/` | SR request validation, input models, resolution, preparation, execution, queues, output parsing and storage. |
| `trim/` | TRIM equivalents, native controls/dialogs, checkpoint recovery/results and packaged TRIM data. |

Put engine-specific input dataclasses in that engine's `models.py`. Keep JSON
aliases and shared cleanup reports in `common/types.py`; low-level file helpers
belong in `common/files.py`. These two common modules must not import engine
execution or resource resolution. Shared simulation schemas and lifecycle
validation belong in `common/records.py`, not an engine's runtime module.
SR and TRIM both use `resources/resolution.py` for resource selection and material
correction; do not make TRIM import the SR resolver for shared behavior.
CAB recovery can use SR dependency execution while keeping imports explicit and
preserving existing lease and publication behavior.

Extend the existing module for the responsibility before creating another file.
Keep `trim/data/trim-damage-defaults.json` next to its module-relative loader.
When moving definitions, update all callers, test patch targets, module/path
metadata and linked documentation together. Keep native fixture bytes, stored
schemas and scientific behavior unchanged unless the approved scope changes them.
The user-facing package map belongs in [the API reference](../doc/reference/api.md#srim-source-layout),
not a new subagent definition or a second source README.

## Verification

Run checks appropriate to the approved change and complete required checks.
Use the exact project Python executable specified in AGENTS.md.
Verify behavior against user requirements, relevant failure cases and
integration boundaries. Do not run native simulations, extra benchmarks or
expanded trials unless authorized by the task or approved plan.
Report checks actually performed, failures, omitted checks and limitations.
Do not claim scientific validation from provenance or passing software tests.

## Completion and handoff

Return the changed-file summary, verification evidence, unresolved issues
and commit reference when a commit was authorized and created.
Identify any deviation from the approved proposal. Do not merge, publish or
permanently adopt changes without applicable approval.
