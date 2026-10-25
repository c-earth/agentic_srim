# agentic_srim

Python APIs and local MCP tools for reproducible SRIM stopping/range and layered
TRIM calculations on Windows. The project pins material and coefficient inputs,
runs native executables in isolated workers, preserves results and provenance,
and searches saved inputs, calculated outputs, cases and analyses.

## Start here

| Task | Read |
| --- | --- |
| Install prerequisites and run a first SR calculation | [Getting started](doc/guides/getting-started.md) |
| Use the scientific and project-management MCP tools | [MCP server guide](doc/guides/mcp-servers.md) |
| Prepare layered TRIM inputs | [TRIM input guide](doc/guides/trim-inputs.md) |
| Run or continue TRIM and inspect saved results | [TRIM execution guide](doc/guides/trim-execution.md) |
| Understand resources, workers and saved records | [Calculation workflow](doc/guides/workflow.md) |
| Import, index and search an external database | [External database onboarding](doc/guides/input-search.md#external-database-registration-and-onboarding) |
| Find all guides, APIs, schemas and limitations | [Documentation index](doc/README.md) |

## Requirements

Use Windows, Python 3.13+ and an existing working SRIM installation. Executables
and installation data are not distributed here. Default CAB recovery also needs
Windows PowerShell 5.1 and an interactive desktop. There is no packaged installer.

Run project Python commands from the repository root through
`D:\projects\agentic_srim\.venv\Scripts\python.exe`. Optional MCP and management
features use the dependencies in [requirements-mcp.txt](requirements-mcp.txt).
Configure your native installation in [settings.toml](settings.toml); adapter
limits and permitted input roots are in [mcp.toml](mcp.toml).

## Agent workspace setup

When using an agent in this repository, choose a role for each checkout or
worktree and create `.local/agent-role.json` with exactly these two fields:

```json
{
  "schema_version": 1,
  "role": "working"
}
```

- `working`: Use the agent for calculations, analysis and project tasks through
  existing tools. Implementation changes belong in a separate development worktree.
- `development`: Use the agent to implement explicitly approved code changes in
  a designated development worktree. Set `"role": "development"` in the example.

The marker selects the applicable [agent instructions](AGENTS.md). It is local,
Git-ignored configuration: configure each new worktree separately, since cloning,
branching and merging do not supply it. Preserve `.local/` during cleanup.

## Results and limitations

SR provides stopping values and engine-generated range grids. TRIM supports
layered input preparation, fresh execution, checkpoint continuation, prepared
batches and summary/profile parsing. Unified retrieval preserves explicit source
identities and provides exact filters and text search. Native run finalizations
and case/analysis publications update their indexes automatically. Supported
external databases use explicit registration, preview, approved import and a
separate search-index build. The SNUMAT adapter preserves simulation inputs and
six output summaries; external records remain separate from native retrieval.
Original databases and generated indexes are not distributed in Git. See the
[workflow](doc/guides/workflow.md) for the calculation and storage lifecycle.

Continued TRIM results use preserved checkpoint recovery under the
[current continuation decision](doc/decisions/trim-continuation.md). Fresh native
text, recovered values and unavailable quantities retain their distinct meanings.
Read the [validation coverage](doc/reference/validation.md) before relying on a
particular native mode or result.

Agents should start with [AGENTS.md](AGENTS.md); documentation maintenance rules
are in [instructions/documentation.md](instructions/documentation.md).
