# Search saved evidence

[Documentation index](../README.md) · [API and formats](../reference/input-search-format.md)

Input search locates project-generated SR/TRIM runs by their verified saved inputs. It provides structured retrieval for agents through Python; no model, embeddings, internet service or CLI is needed. This native API indexes inputs only. Unified retrieval below also searches saved outputs, cases and analyses; external sources use the explicit registration, preview/import and separate search workflow below.

The catalog starts empty. Newly finalized standalone simulations index themselves; prepared batches index their finalized runs together. Historical records are not automatically backfilled. Succeeded and failed terminal records are included if their inputs verify; created/running and CAB GUI captures are excluded. Index errors are logged/reported separately and cannot change native status or return values.

## Search from Python

Import from the repository root using the approved Python environment. Existing Config/ProjectPaths and execution APIs remain unchanged.

```python
from pathlib import Path
from src.backend.paths import get_project_paths
from src.knowledge.search import search_inputs

paths = get_project_paths(Path(r"D:\projects\agentic_srim"))
filters = {
    "schema_version": 1,
    "clauses": [
        {"field": "engine", "operator": "eq", "value": "trim", "unit": None, "layer": None},
        {"field": "projectile.atomic_number", "operator": "in", "value": [2, 18], "unit": None, "layer": None},
        {"field": "energy", "operator": "between", "value": ["100", "500"], "unit": "keV", "layer": None},
    ],
}
result = search_inputs(paths, filters)
```

Different clauses AND; the projectile list accepts helium or argon. Omit filters for all eligible registered runs. `execution_status` can restrict succeeded/failed; both default. Numeric equality is exact after explicit unit conversion, bounds inclusive. SR lists match any requested value; continuous ranges match membership/overlap. Results retain `energy.kind`, so an interval is not confused with output sample points. Event projectile and energy filters must share one event.

Decimal strings are limited to 256 characters, including signs and exponent
notation. Their decimal scaling exponent must be between -1024 and +1024,
inclusive, before and after unit conversion. Fractional places count toward that
exponent: `0.1e-1023` has scaling exponent -1024. Over-limit values produce a clear
validation error before exact arithmetic or fixed-point expansion. Ordinary
values retain exact precision. These are per-number processing limits; native
SR/TRIM physical-input validation is separate. Existing search projections
outside these limits are rejected/excluded through normal validation without
rewriting the saved native inputs. See the [numeric contract](../reference/input-search-format.md#numeric-processing-limits)
for conversion boundaries.

## Targets and unknowns

`target_label` matches a supplied label without inferring composition. `target.elements` accepts `contains_all`/`contains_any` with integer Z lists. `target.composition` compares exact atomic ratios independent of scale/order. Every query component has atomic_number, mass_amu, atomic_proportion; all masses null means elemental matching, all specified means mass-specific matching.

```python
silica = {"schema_version": 1, "clauses": [{
    "field": "target.composition", "operator": "eq",
    "value": [
        {"atomic_number": 14, "mass_amu": None, "atomic_proportion": "1"},
        {"atomic_number": 8, "mass_amu": None, "atomic_proportion": "2"},
    ],
    "unit": None, "layer": "any",
}]}
matches = search_inputs(paths, silica)
```

For composition/density/thickness specify a layer number, any or all. Multiple any predicates can match different layers; use one explicit layer number for co-location. SR has one homogeneous target for density/composition and no finite thickness/layer count.

Unknown/inapplicable values do not satisfy normal filters. Use operator unknown, value/unit null to find them, retaining a layer selector where required. Unverifiable evidence excludes the source rather than supplying guessed inputs. Requested ion count and initial seed are input controls, not achieved counts/final RNG state. Resumed TRIM uses verified continuation count/events; parent immutable physics remains separately preserved.

## Pagination and file access

Default page size 100, maximum 1,000, valid total and stable simulation-ID order. Pin the returned non-null generation when paging; mismatch raises ValueError and requires restarting pagination. No catalog means empty results/generation null without creating storage. excluded reports current stale/rejected sources.

```python
first = search_inputs(paths, filters, limit=100)
if first["generation"] is not None:
    second = search_inputs(paths, filters, offset=100,
                           expected_generation=first["generation"])
```

```python
from src.knowledge.source import read_source_file, read_source_record

for match in result["matches"]:
    record = read_source_record(paths, match["reference"])
    for file in record["files"]:
        if file["path"].endswith("/TDATA.txt"):
            raw = read_source_file(paths, record["reference"], file)
```

File access verifies pinned ownership, inventory, safe paths, hash and size and returns exact bytes without output parsing. Default limit 64 MiB; increase max_bytes explicitly for larger known files. Existing parsers can consume these bytes later; search adds no scientific analysis.

## Retry and recovery

```python
from src.knowledge.index import initialize_index, update_index, rebuild_index

initial = initialize_index(paths)
```

Initialization creates an empty catalog or leaves an existing one unchanged. `update_index(paths, simulation_ids)` retries indexing or deliberately adds selected historical IDs; it reads only the supplied records and skips re-projection of unchanged verified inputs. `rebuild_index(paths, simulation_ids)` replaces catalog membership with exactly a nonempty explicit ID list, for recovery. Supply every ID you intend to retain; no automatic historical discovery occurs.

Inspect update/rebuild state, indexed/skipped/rejected and issues. Reports live under memory/knowledge/updates where storage permits. Corrupt schemas require explicit rebuild. A catalog generation does not protect files from external modifications; current evidence is rechecked at search/access. Input verification does not establish independent physical validity.

## Search runs, cases, analyses and calculated outputs

Use project MCP `knowledge_search` for unified retrieval. It indexes new terminal
SR/TRIM publications and every new case/analysis revision automatically. Analyses
retain their existing `post_*` IDs. The native `inputs_search` API above remains
available with its existing input-only semantics.

The unified catalog starts empty and does not scan historical records at startup.
An indexing failure cannot change an already published record or native outcome.
A process interruption between publication and notification can leave a missing
entry; explicit reconciliation detects that gap.

For Python, start with a private copy of the complete query template:

```python
from copy import deepcopy
from src.knowledge.retrieval_schema import EMPTY_QUERY
from src.knowledge.retrieval_search import search_knowledge, get_knowledge

query = deepcopy(EMPTY_QUERY)
query["kinds"] = ["case", "analysis"]
query["text"] = "helium range"
result = search_knowledge(paths, query)
for hit in result["matches"]:
    record = get_knowledge(paths, hit["kind"], hit["reference"])
```

Text is literal Unicode-token AND search, with weighted titles/questions/findings.
Quotes, OR and other punctuation do not enable advanced query operators. A score
orders relevance within this query; it is not scientific confidence. Excerpts
retain field locators. Statuses default to all states, including failed runs,
draft analyses and archived cases. Add explicit kind-specific status filters
when the task requires them.

Current revision search is the default. `revision_scope="history"` searches
indexed revisions separately. Hits pin exact revision/hash references. A missed
new revision is reported as stale current evidence; an older revision is not
silently presented as current. Linked analysis revisions remain exactly those
saved in the case.

### Match calculated output values

Use run-only kinds for direct scientific predicates. Input and output constraints
AND. SR outputs expose table `sr.rows`; TRIM profiles use their canonical names,
such as `RANGE.txt` and `VACANCY.txt`. Read `knowledge_get` without a table to
inspect available column descriptors, units and availability first.

```python
query = deepcopy(EMPTY_QUERY)
query["kinds"] = ["run"]
query["output_filter"] = {
    "result_sources": None,
    "complete": True,
    "summary": [],
    "table_groups": [{
        "table": "sr.rows",
        "clauses": [
            {"column": "energy", "operator": "eq",
             "value": "100", "unit": "keV"},
            {"column": "projected_range", "operator": "between",
             "value": ["10", "1000"], "unit": "nm"},
        ],
    }],
    "availability": [],
}
result = search_knowledge(paths, query)
```

Both clauses in that group must match the same actual row. Separate groups can
match separate rows. No interpolation, extrapolation or numerical aggregation
is performed. SR list-mode output has no calculated range columns; those values
remain unknown. Stopping predicates require the saved stopping-unit label.
TRIM column selectors are zero-based indices with explicit saved units.

Checkpoint-recovered profiles retain recovery provenance, cumulative
normalization, precision and inferred-layout warnings. Their diagnostic native
report is not substituted for recovered results. Missing values and quick-mode
not-calculated tables never become zeros. Use an availability clause such as
`{"table": "NOVAC.txt", "state": "not_calculated"}` to find the latter.

Search reads saved parsed results and verifies their file/dependency hashes.
It never reparses results or launches a calculation. Output rejection leaves a
valid input record discoverable, with the output problem reported.

### Follow relationships and verify dependencies

Case members, selected analysis inputs and analysis artifacts form explicit
direct links. `knowledge_links` reads outgoing links or incoming indexed origins.
Incoming completeness is limited to indexed revisions. Job members stay unsealed;
they never automatically turn into terminal native-source members.

A relation clause can constrain an outgoing case native-source member or analysis
input using native input and output filters. Both filters must match that same
target run. Independent relation clauses may match different links. No transitive
traversal or title-based inferred membership is performed.

A verified record means its pinned primary evidence is intact. It does not mean
all linked artifacts are intact. Request `knowledge_get(dependencies=True)` for
declared dependency checks. Intact analysis findings remain readable with
missing-artifact issues, rather than being silently removed.

### Maintain the derived index

`knowledge_status` reports indexed counts and recorded issues, not a complete
filesystem inventory. `knowledge_update(operation="update", references=[...])`
retries explicit `{kind,reference}` identities. It does not scan unrelated records.

For missed notifications, preview
`knowledge_update(operation="reconcile", scope="registered")`.
Use `scope="project"` explicitly to discover wholly unregistered published
records. Apply by resubmitting the returned `plan_sha256` as
`expected_plan_sha256`. A changed plan fails with conflict. Reconciliation changes
only derived storage, never source evidence.

Default pages contain 100 hits, maximum 1,000. Pin the returned non-null generation
on later pages. A generation pins catalog content, not external file bytes;
subsequent file damage can change verified totals even within one generation.
Unified exclusions are query-scoped, not an audit of nonmatching records.


## External database registration and onboarding

Register external locations without moving their contents into the project:

```toml
[knowledge]
database_paths = [
    'D:\datasets\collection',
    'E:\datasets\bundle.zip',
]
```

The shipped list is empty. Use absolute local Windows paths to directories or
individual files. A directory containing several archive parts is one registered
source. Relative paths, network/device paths, traversal, wildcards, stream/device
names and duplicate Windows-normalized paths are rejected. Missing locations are
allowed in settings and reported unavailable; they are never created for you.

After installing the new tool definitions, restart the project MCP server once.
Subsequent path edits take effect on the next source-tool call without a restart.
Use `database_sources_list`, then `database_inspect(source_id)`. Read returned
prefix references with `database_sample_read(reference)`. These tools inspect
registered sources only; they do not broaden general file-tool permissions.
They write no files, extract no archives and update no retrieval records.

Inspection recognizes packaging and bounded syntax: directories, ZIP, TAR,
gzip-compressed TAR, gzip streams, UTF-8 text, possible headers/JSON keys and
SQLite magic headers. It does not infer scientific quantities, units, record
counts or a complete dataset schema. The code-owned SNUMAT v1 adapter recognizes
sampled scientific evidence; unknown or insufficiently sampled formats recommend
the repository's
[external-database-onboarding skill](../../.agents/skills/external-database-onboarding/SKILL.md).
The agent uses fresh sampled evidence to propose the exact adapter and mappings,
including calculated outputs, units, precision and provenance. One adapter can
serve several databases that share a schema. An adapter is ordinary project
Python code behind the existing server; each database needs no separate MCP.

Ask in ordinary language to inspect or integrate the selected registration; no
special command syntax is required. Registration alone never loads it. Startup,
search and file changes never load or refresh external data automatically.
The SNUMAT v1 adapter supports simulation inputs and six output summaries.
Other schemas still require an evidence-based approved adapter design.

Native run finalizations and case/analysis publications continue indexing
automatically. External loading and subsequent updates require explicit requests.
Refresh adds new records, versions changed ones, skips unchanged ones
and reports missing records with history preserved. Evidence reads detect changed
metadata or sampled bytes and fail integrity; that check does not refresh an
index. Removing a path revokes source reads immediately. Missing paths do not
prevent unrelated native/project work.

Reports show limited coverage and exact budgets. A pinned prefix verifies only
its bytes plus file size/mtime, not the whole archive or scientific validity.
See the [inspection contract](../reference/input-search-format.md#external-source-inspection)
for limits and unavailable/unsafe/unsupported states.

### Preview, import and search SNUMAT v1

The supported source is a registered directory containing the SNUMAT v1 metadata
archive, ten case archives, README and archive checksums. Originals stay in place.
The adapter imports actual TRIM inputs and these TDATA summaries: calculated,
transmitted and backscattered ion counts, mean range, straggling and vacancies
per ion. It retains planned inputs and SR-module estimates separately. Profile
tables and per-ion results are not searchable.

1. Request integration or refresh for the registered source. The agent uses
   `database_preview(source_id, request_id)`, a managed job that verifies
   whole-source fingerprints and stages a plan without publishing search results.
2. Review its counts, rejected records, exact fields/units, unavailable outputs
   and storage costs. A partial or failed scan cannot yield an importable plan.
3. Approve the concrete plan. The agent calls `database_import(source_id,
   expected_plan_sha256, request_id)`. It rechecks source bytes and catalog
   generation before atomic publication.
4. Call `database_reindex(source_id, expected_generation, request_id)` as a
   managed job to build scientific predicate indexes from the imported local
   records. Use the generation reported by import/status. This step does not
   repeat the source preview or read original archives.
5. Use `database_search`, `database_get` and `database_status` for imported
   external records. Use `knowledge_search` and `knowledge_get` for native/project
   records; they do not silently include the external catalog.

Search uses the same decimal-string/unit, offset/limit, generation and source
reference conventions as native retrieval. Its external query has explicit
`text`, `revision_scope` and typed `clauses`; it has no invented native run status.
For example, a query for calculated mean range between 700 and 705 nm is:

```json
{
  "schema_version": 1,
  "text": "Rb Tl4O3",
  "revision_scope": "current",
  "clauses": [
    {"field": "outputs.mean_range", "operator": "between",
     "value": ["700", "705"], "unit": "nm"}
  ]
}
```

Pass that query with the selected `source_id` and `offset: 0`. Search defaults
to 20 compact hits, with a maximum requested limit of 100 and a 128 KiB result
budget. Follow `next_offset` while `has_more` is true; byte limits may make the
returned page smaller than the requested limit. Keep `expected_generation` on
later pages. Schema-version-2 search results verify returned local records and
mark original evidence `not_checked`. They contain references and concise
summaries; full records, raw values and archive-member locators are available
through `database_get`.

`database_get` accepts the immutable external reference; its default
`verify_evidence: true` verifies original archives/members. It hashes and scans
each shared archive once per call, checking all required members. Full archive
verification still has a read cost. `verify_evidence: false` reads verified local
records without original-archive checks. Search defaults to a 60-second internal
deadline and get to 240 seconds; both allow explicit integer `timeout_seconds`
from 1 through 240 and fail explicitly when expired.

Adding records using the same schema needs an explicit preview/approved import,
not a reconnect. Changed records get new versions; unchanged records are skipped.
Missing records are reported with history retained. Each import changes the
catalog generation: run `database_reindex` afterward. Until then, status shows a
stale or missing search index and search requests a rebuild. Records and indexes
persist in this project across chats; searches do not rebuild them automatically.
Installing new adapter/tool
code requires restarting the existing project MCP server and refreshing its
client tool connection once. Path registration edits reload on subsequent calls.
No additional server, watcher, startup import or search-triggered refresh is used.

See the [external import and search contract](../reference/input-search-format.md#external-import-and-search)
for identities, schema details, limits and storage.
