# Python environment

- Run all project Python code through `D:\projects\agentic_srim\.venv\Scripts\python.exe`.
- Run all pip installs through `D:\projects\agentic_srim\.venv\Scripts\python.exe -m pip install`.

# Follow the requested scope

- Follow the user's requirements literally. Do not expand the requested scope.
- Ask if something is ambiguous.
- Before reporting completion, verify that the result matches the user's requirements and is internally consistent.
- Handoffs must not repeat instructions or context already loaded; record only additional state and unresolved work.

# Git command execution

- Never use standalone `--` in Git commands; it triggers sandbox permission errors. Use `./` for filenames starting with `-`.

# Proposal and approval before implementation

- Before creating or changing implementation code, fully work out and present every detail of every proposed file and code change to the user. Do not start implementation while details remain unresolved.
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
