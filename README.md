# agentic_srim

Python APIs and local MCP tools for reproducible SRIM stopping/range and layered
TRIM calculations on Windows. The project pins material and coefficient inputs,
runs native executables in isolated workers, preserves results and provenance,
and searches saved simulation inputs.

## Start here

| Task | Read |
| --- | --- |
| Install prerequisites and run a first SR calculation | [Getting started](doc/guides/getting-started.md) |
| Use the scientific and project-management MCP tools | [MCP server guide](doc/guides/mcp-servers.md) |
| Prepare layered TRIM inputs | [TRIM input guide](doc/guides/trim-inputs.md) |
| Run or continue TRIM and inspect saved results | [TRIM execution guide](doc/guides/trim-execution.md) |
| Understand resources, workers and saved records | [Calculation workflow](doc/guides/workflow.md) |
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

## Results and limitations

SR provides stopping values and engine-generated range grids. TRIM supports
layered input preparation, fresh execution, checkpoint continuation, prepared
batches and summary/profile parsing. Input search and case/analysis records
preserve explicit source identities. See the [workflow](doc/guides/workflow.md)
for the full calculation and storage lifecycle.

Continued TRIM results use preserved checkpoint recovery under the
[current continuation decision](doc/decisions/trim-continuation.md). Fresh native
text, recovered values and unavailable quantities retain their distinct meanings.
Read the [validation coverage](doc/reference/validation.md) before relying on a
particular native mode or result.

Agents should start with [AGENTS.md](AGENTS.md); documentation maintenance rules
are in [instructions/documentation.md](instructions/documentation.md).
