# Documentation

Start with [your first SR calculation](guides/getting-started.md), or use the
[MCP server guide](guides/mcp-servers.md) to work through agent tools. The
[project README](../README.md) lists prerequisites.

## Choose a task

| I want to… | Read |
| --- | --- |
| Configure SRIM and calculate stopping or range | [Getting started](guides/getting-started.md) |
| Understand calculation inputs, workers and saved evidence | [How calculations and records work](guides/workflow.md) |
| Launch MCP servers and discover tools | [MCP servers](guides/mcp-servers.md) |
| Manage cases and record analysis provenance | [Project management](guides/project-management.md) |
| Search inputs, calculated outputs, cases and analyses | [Evidence search](guides/input-search.md) |
| Register, inspect, import, index and search an external database | [External database onboarding](guides/input-search.md#external-database-registration-and-onboarding) |
| Select materials, create resource variants or recover CAB data | [Materials, resources and CAB](guides/resources-and-cab.md) |
| Prepare a layered TRIM calculation | [TRIM inputs](guides/trim-inputs.md) |
| Execute, continue or batch TRIM and inspect outputs | [TRIM execution](guides/trim-execution.md) |
| Recover profiles from a complete frozen checkpoint | [Checkpoint recovery](guides/trim-checkpoint-recovery.md) |

## Look up a contract

| Topic | Reference |
| --- | --- |
| SR/resources Python signatures and side effects | [Python API](reference/api.md) |
| SR requests, units, resource and record layouts | [Data formats](reference/data-formats.md) |
| TRIM inputs, native mode codes and bundle format | [TRIM input reference](reference/trim-input-format.md) |
| TRIM runtime, continuation and parsed-report schemas | [TRIM execution reference](reference/trim-execution-format.md) |
| Elemental damage defaults, measured table and limitations | [TRIM defaults](reference/trim-input-defaults.md) |
| Input/output search, external imports and reference contracts | [Input-search reference](reference/input-search-format.md) |
| MCP discovery, jobs, ownership and record formats | [MCP interface reference](reference/mcp-format.md) |

## Check support and known limitations

- [Verification commands and recorded native coverage](reference/validation.md)
- [Current TRIM continuation decision](decisions/trim-continuation.md)

Guides describe how to perform a task; references define exact interfaces and
formats; decisions state active constraints. Only current material is kept here.
Agents maintaining these pages must follow the shared
[documentation instructions](../instructions/documentation.md).
