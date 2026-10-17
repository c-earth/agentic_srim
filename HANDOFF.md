# Handoff

Continue filesystem design for the SRIM/TRIM MCP project in `D:\projects\agentic_srim`.

The SR investigation summary is in [`.cache/outputs/sr-simulation-summary.txt`](.cache/outputs/sr-simulation-summary.txt).

## Agreed structure

```text
src/
  srim/
  case/
  analysis/
  memory/
  plan/
memory/
  srim/
    srim_yyyymmdd_hhmmss_xx/
  post/
    post_yyyymmdd_hhmmss_xx/
case/
  case_yyyymmdd_hhmmss_xx/
worker/
  worker_xx/
    sr/
    trim/
```

## Agreed responsibilities

- `memory/srim`: raw and parsed simulation outputs.
- `memory/post`: processed results and evidence.
- `case`: goals and answers supported by evidence in postprocessing records.
- `memory` broadly: durable records, reusable knowledge, and future RAG/database storage.
- `src/plan`: workflow management and reuse of skills and workflows.
- `worker/worker_xx/{sr,trim}`: temporary execution directories with exclusive use.
- Retain the flat source layout for now.

## Current state

The base directories contain `.gitkeep` files. No worker instances or timestamped records exist. A premature filesystem implementation and its tests were removed at the user's request; no implementation remains. Project instructions were updated in `AGENTS.md`.

## Next step

Prepare the filesystem design proposal, including the specific files and their exact responsibilities. The previous proposal was not approved. Metadata schemas, timestamp timezone, ID suffix behavior, and precise API contracts remain unresolved.
