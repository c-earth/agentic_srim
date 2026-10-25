# Native input-search APIs and formats

[Usage](../guides/input-search.md) · [Schema validators](../../src/knowledge/schema.py)

The native APIs below retrieve SR/TRIM inputs. The unified retrieval section also covers calculated outputs, cases and analyses. Existing IDs, metadata, runner signatures and worker ownership remain unchanged. External SNUMAT inputs and summaries use the separate explicit import/search APIs below; no CLI or output analytics. The catalog starts empty; new terminal records index automatically without historical backfill.

## Public APIs

```python
initialize_index(paths: ProjectPaths) -> JSONObject
update_index(paths: ProjectPaths, simulation_ids: Sequence[str]) -> JSONObject
rebuild_index(paths: ProjectPaths, simulation_ids: Sequence[str]) -> JSONObject
search_inputs(paths: ProjectPaths, filters: JSONObject | None = None, *,
    offset: int = 0, limit: int = 100,
    expected_generation: str | None = None) -> JSONObject
project_simulation_inputs(paths: ProjectPaths, simulation_id: str) -> JSONObject
read_source_record(paths: ProjectPaths, reference: JSONObject) -> JSONObject
read_source_file(paths: ProjectPaths, reference: JSONObject,
    file: JSONObject, *, max_bytes: int = 67108864) -> bytes
validate_search_document(document: JSONObject, kind: str) -> None
convert_quantity(value: str, source_unit: str, target_unit: str) -> str
matches_filters(record: JSONObject, filters: JSONObject) -> bool
```

Modules: src.knowledge.index, search, source and schema respectively. Existing Config/ProjectPaths/JSONObject conventions apply. Reads require saved evidence, no live installation/defaults. initialize_index returns {schema_version,generation,created} and preserves an existing catalog. Empty update does not create storage. Rebuild takes explicit nonempty replacement membership and rejects concurrent changes.

Invalid API arguments raise ValueError; source/file unsafe or missing I/O raises OSError. Initialization also propagates SQLite errors. Ordinary operational update/rebuild failures return separate failed/partial reports; interruption propagates after cleanup. Search rejects corrupt/incompatible schema or changed pinned generation and lists individual stale sources in excluded. Diagnostics cannot change already published native status.

## Exact envelopes

```text
SourceReference = {kind,id,metadata_sha256,input_inventory_sha256}
FileReference = {owner,path,sha256,size_bytes,role}
Issue = {simulation_id,code,field,message}
Observation = {field,value,unit,evidence}
SearchRecord = {schema_version,reference,origin,execution_status,inputs,observations,files,issues}
SearchResult = {schema_version,generation,total,offset,limit,matches,excluded}
UpdateReport = {schema_version,operation_id,state,generation,attempted,indexed,skipped,rejected,issues,completed_at}
Filter = {schema_version,clauses}
Clause = {field,operator,value,unit,layer}
```

New envelopes schema_version=1; every key required, unavailable values null, unknown/duplicate keys rejected. Scientific quantities finite decimal strings; integer counts/indices exclude bool. Hashes lowercase SHA-256, sizes nonnegative integer bytes, paths safe owner-relative forward-slash names. Native source kind simulation, origin native, terminal status succeeded/failed. File roles input/output/other and paths must occur in saved inventories.

References hash exact terminal metadata and canonical sorted input-inventory JSON (sort_keys=True,separators=(',',':'), UTF-8, no final newline). Evidence locators {file,json_pointer} cite saved JSON or a null pointer for exact native bytes. A file listing does not imply available parsed scientific results. No output contents are read to construct search records.

Issue codes unsupported/malformed/missing/integrity/conflicting/stale/ineligible/indexing_error. Update state succeeded/partial/failed, operation update_<uuid>, sorted ID lists, UTC completion time. A report-write failure does not roll back a committed catalog. Full nested input validation is defined by [schema.py](../../src/knowledge/schema.py) and the saved-input projection in [source.py](../../src/knowledge/source.py); searchable settings must reproduce preserved SR.IN/TRIM.IN and applicable events. Resumed TRIM count/events use verified continuation control/checkpoint inputs.

## Filters

| Field | Operators | Units |
| --- | --- | --- |
| engine, mode, execution_status, target_label | eq/in/unknown | null |
| projectile.atomic_number | eq/in/unknown | null |
| projectile.mass_amu | eq/between/unknown | amu |
| energy | eq/between/unknown | eV/keV/MeV/GeV |
| target.elements | contains_all/contains_any/unknown | null |
| target.composition | eq/unknown | null |
| layer_count | eq/between/unknown | null |
| layer.thickness | eq/between/unknown | A/nm/um/mm/cm/m |
| layer.density | eq/between/unknown | g/cm3 |
| incidence_angle | eq/between/unknown | deg |
| total_ions, random_seed | eq/in/between/unknown | null |

Clauses AND, in/contains_any OR. Numeric bounds inclusive and ordered with at least one non-null bound. Exact unit conversion/equality; unknown has null value/unit. Labels casefold exact, caller supplies integer Z, no formula inference. Composition/density/thickness require explicit layer index/any/all. Multiple any clauses can refer to different layers. Proportions compare exact ratios without common-scale dependence; all query masses null for elemental matching or all specified for mass-specific, never mixed.

SR list energies match any requested value; SR ranges membership/overlap, never output sampling. TRIM event projectile and energy predicates share one event. SR density/composition use homogeneous target 1, thickness/count unknown. No planned-depth, collection or origin-choice filters yet.

### Numeric processing limits

Every interpreted decimal string in numeric filters, query composition masses
and proportions, and SearchRecord input quantities has a maximum length of
256 characters, inclusive. Signs, decimal points and e/E notation count toward
that limit. The existing ASCII decimal grammar permits scientific notation;
whitespace and nonfinite values remain invalid.

The coefficient's decimal scaling exponent, as returned by
`Decimal.as_tuple().exponent`, must be within -1024..1024 inclusive. The same
limit applies after the unit exponent shift. Both the original and converted
exponents must pass; conversion cannot rescue an already over-limit input. Zero
and trailing zeros obey these representation limits without normalization.

| Value and conversion | Result |
| --- | --- |
| `1e1024` or `1e-1024`, same units | Accepted exactly |
| `0.1e-1023`, same units | Accepted; scaling exponent is -1024 |
| `0.1e-1024`, same units | Rejected; scaling exponent is -1025 |
| `1e1021`, MeV to keV | Accepted; converted exponent is 1024 |
| `1e1022`, MeV to keV | Rejected; converted exponent is 1025 |
| `1e-1021`, eV to keV | Accepted; converted exponent is -1024 |
| `1e-1022`, eV to keV | Rejected; converted exponent is -1025 |

Length is checked before parsing; exponent limits are checked before Fraction
construction or fixed-point formatting. Limit failures raise ValueError with
`quantity: numeric string exceeds 256 characters` or
`quantity: decimal exponent outside -1024..1024`. MCP maps these to
`invalid-argument`, including when no catalog exists. No mutation job is accepted.
Exact unit conversion and comparison within the limits retain their precision;
integer-count validation and native SR/TRIM request validation are unchanged.

Historical search projections outside these limits become rejected/excluded
through the existing validation/indexing error handling. No automatic catalog
migration or native-evidence rewrite occurs. These per-number limits do not
establish a total runtime bound for large collections or many composition
components. Arbitrary observation values without numeric interpretation retain
their existing contract.

Result ordering stable native ID; offset>=0, limit 1..1000 default 100. Generation UUID commits with rows, no-op updates retain it, absent catalog returns null. Pin a non-null generation when paging. Current evidence damage excludes a source even if catalog generation matches.

## Storage and concurrency

memory/knowledge/index.sqlite uses records/issues/meta tables, user_version=1, DELETE journal and synchronous FULL. Numeric predicates use exact Python arithmetic, not SQLite REAL. Source projections run outside publication locks and selected identities are rechecked before commit. No unrelated-record scans on update; historical inclusion/rebuild membership always explicit.

Existing Windows locks bound search/publication waits to 30 seconds. Finalization releases record-parent lock before notification; indexing acquires no worker lease. Prepared batches propagate copied contexts, collect thread-safely and flush once after active calls finish, including finalized work on exceptional exit. Native status/return/cleanup semantics unchanged. Operational reports separate under updates/update_<uuid>.json, best-effort logged when storage fails.

Corrupt/incompatible catalogs need explicit rebuild and never imply damaged native records should be altered. Search integrity checks may cost more for large sealed input trees; small synthetic timing results are not large-collection performance guarantees.

## Unified retrieval APIs

The project-level unified catalog is separate from the native input catalog:
`memory/knowledge/retrieval/index.sqlite`. It uses SQLite FTS5, schema version 1,
DELETE journaling, FULL synchronization and foreign keys. Tables are meta,
documents, heads, links, issues and document_text, with FTS5 auxiliary tables.
Operational reports use `retrieval/updates/update_<uuid>.json`.

```python
initialize_retrieval_index(paths: ProjectPaths) -> JSONObject
update_retrieval_index(paths: ProjectPaths,
                       references: Sequence[JSONObject]) -> JSONObject
reconcile_retrieval_index(paths: ProjectPaths, *,
                         scope: str = "registered",
                         expected_plan_sha256: str | None = None) -> JSONObject
read_retrieval_status(paths: ProjectPaths) -> JSONObject
search_knowledge(paths: ProjectPaths, query: JSONObject, *,
                 offset: int = 0, limit: int = 100,
                 expected_generation: str | None = None) -> JSONObject
get_knowledge(paths: ProjectPaths, kind: str, reference: JSONObject, *,
              table: str | None = None, offset: int = 0, limit: int = 100,
              dependencies: bool = False) -> JSONObject
read_knowledge_links(paths: ProjectPaths, kind: str, reference: JSONObject, *,
                      direction: str = "outgoing",
                      offset: int = 0, limit: int = 100) -> JSONObject
```

Index APIs are in `src.knowledge.retrieval_index`; reads are in
`src.knowledge.retrieval_search`. Kind is run/case/analysis. Run references are
unchanged native SourceReferences. Project references are
`{kind,id,revision,sha256}`. No native or project source schema is changed.

### Query contract

Query keys are exactly
`{schema_version,kinds,text,statuses,tags,revision_scope,input_filters,output_filter,relations}`.
Use a deep copy of `src.knowledge.retrieval_schema.EMPTY_QUERY`. MCP exposes this
as the default complete query and publishes schemas `knowledge.query.v1` and
`knowledge.output-filter.v1`.

- `schema_version=1`; kinds is a nonempty unique list.
- Text is null or a nonblank string, maximum 4,096 characters and 64 FTS5 tokens.
  Tokenizer is unicode61 with remove_diacritics=0. Tokens AND; no advanced syntax.
- Statuses has exactly run/case/analysis keys, each null or a nonempty valid list.
- Tags is null or `{operator,values}`, with contains_all/contains_any and case-only
  kinds. Values are nonempty strings up to 128 characters.
- Revision scope is current/history. Default current uses observed heads and
  rechecks current pointers; history preserves exact pinned revisions.
- Direct input_filters and output_filter require run-only kinds.
- Relations is a list of direct relationship clauses; all constraints AND.

OutputFilter has exactly
`{result_sources,complete,summary,table_groups,availability}`.
Result sources is null or a unique native-text/checkpoint-recovery list;
complete is null or bool. Summary predicates are `{name,operator,value,unit}`.
Table groups are `{table,clauses}` with clauses
`{column,operator,value,unit}`. Operators eq/between/unknown follow the numeric
limits above. Unknown requires null value/unit. Bounds are inclusive, ordered
and have at least one non-null endpoint. Each table group requires one shared
row witness. Availability clauses are `{table,state}`, where state is
available/unavailable/not_calculated.

SR column names: energy, requested_energy, electronic_stopping, nuclear_stopping,
projected_range, longitudinal_straggling, lateral_straggling. Canonical energy is
keV and lengths A; original printed tokens/units are retained. List-mode range
values are unknown. Stopping comparisons require identical saved unit labels;
no density-dependent or reduced-unit conversion is implemented.

TRIM columns use zero-based indices and preserved native/recovery descriptors.
Recognized Angstrom/angstrom length labels map to A. Other compound unit labels
must match exactly. Native summary names are calculated_ions, transmitted_ions,
backscattered_ions (ions), mean_range, straggling (A), vacancies_per_ion
(vacancies/ion). Recovered summaries expose the three count fields; unqualified
stored moments/averages are not renamed into native statistics.

RelationClause is
`{relation,direction,reference,target_input_filters,target_output_filter}`.
Relations are case_member/analysis_input/analysis_artifact; direction is
outgoing/incoming. At least a reference or scientific target filter is required.
Scientific filters are allowed only on outgoing native case members or analysis
inputs. References are exact saved references; native-source member wrappers
can be supplied explicitly. Incoming matches verify their indexed origin records.

### Results, manifests and evidence

SearchResult keys are
`{schema_version,generation,query_sha256,total,offset,limit,matches,excluded,coverage}`.
Hit keys are
`{kind,id,reference,title,status,score,sections,output_matches,relation_matches,verification}`.
FTS5 bm25 weights are title=5, question=5, tags=3, notes=1, method=2, findings=5,
limitations=2, member_notes=1, output_notes=1. Lower score ranks first; ties use
kind/ID/revision/document key. Ranking uses the indexed text corpus. No text means
null score and stable identity order.

Up to three excerpts contain at most 320 Unicode code points each and retain
source locators. Output matches retain provenance, completeness, warnings,
issues and at most 20 matching row locators with an explicit truncation flag.
Use get_knowledge(table=...) for actual paged rows and original column metadata.
Without a table, it returns table manifests without row payloads. Dependency
verification is separate from primary-record integrity and scientific correctness.

Parsed-result cap is 64 MiB per file; aggregate evidence cap is 256 MiB per
projection. Oversized or unsupported results are reported as output rejection;
no silent truncation or reparsing. Inputs remain discoverable if their own
evidence verifies. Supported TRIM profile families are RANGE.txt, VACANCY.txt,
IONIZ.txt, PHONON.txt, E2RECOIL.txt, NOVAC.txt and LATERAL.txt. No attachment contents,
collision-history analytics, inferred scalar statistics or generated summaries
are indexed.

### Publication, consistency and recovery

Catalog snapshots capture generation, candidates, links and ranks under a bounded
30-second lock. SQLite closes and the lock releases before source verification.
Source publication locks and catalog locks are never held together. Documents,
heads, links, text and issues commit with generation atomically; no-op updates
retain generation. Initialization validates FTS5 and the exact table layout.

New case/analysis revisions notify after pointer publication and source unlock;
identical revision retries safely re-notify. New native finalization notifies both
catalogs independently, including prepared-batch collection/flush behavior.
Notification failures never change published scientific/project return contracts.
There is no durable outbox: explicit project-scope reconciliation detects a
publication lost entirely before notification.

Current heads track the latest observed revision even if projection fails.
Older incoming notifications cannot replace newer heads. Current-pointer mismatch
excludes stale hits; historical evidence is read by exact revision/hash. Source
damage after a captured generation can change totals, and later pages must restart
if catalog generation changes.

UpdateReport is
`{schema_version,operation_id,state,generation,indexed,skipped,rejected,issues,completed_at}`,
with explicit `{kind,reference}` identities and succeeded/partial/failed state.
Reconciliation returns
`{schema_version,scope,plan_sha256,actions,issues,report}`; null report means
preview. Apply requires the unchanged digest. registered scope inspects catalog
heads; explicit project scope inventories published identities to recover missed
notifications. Neither scope edits sources or silently runs at startup.

Catalog compatibility failures require investigation; the native index APIs
continue to support their existing explicit update/rebuild behavior independently.

## External source inspection

`src.knowledge.database_sources` provides these read-only APIs:

```text
list_database_sources(paths: ProjectPaths) -> JSONObject
inspect_database_source(paths: ProjectPaths, source_id: str) -> JSONObject
read_database_sample(paths: ProjectPaths, reference: JSONObject) -> JSONObject
validate_database_document(document: JSONObject, kind: str) -> None
```

Registrations come from optional `[knowledge] database_paths = []` in
`settings.toml`. `Config.database_paths` is an immutable tuple, defaulting to
empty; parsing does not read source contents or require existence. Entries are
unique Windows-normalized absolute local drive paths. Source identity is `db_`
plus the full lowercase SHA256 of the UTF-8 Windows-normalized configured path
(`os.path.normcase(os.path.normpath(str(path)))`). This identifies registration,
not content. Source calls reload configuration each time and use the agreed
`ProjectPaths` layout. General MCP allowed input roots are unchanged.

All envelopes are closed, finite JSON with required keys, `schema_version: 1`
where shown, and explicit nulls. Discovery publishes the exact schemas.

| Shape | Required keys |
| --- | --- |
| SourceList | schema_version, sources, issues |
| Source | source_id, path, state, issues |
| InspectionReport | schema_version, inspection_id, source, inspection_state, inventory, samples, format_detection, limits, usage, issues, recommended_skill |
| InventoryEntry | relative_path, container, member_path, kind, size_bytes |
| Sample | reference, encoding, content, structure |
| SampleReference | schema_version, source_id, relative_path, member_path, container, file_size_bytes, file_mtime_ns, sample_offset, sample_size_bytes, sample_sha256 |
| SampleRead | schema_version, reference, encoding, content, verification |
| format_detection | state, candidates |
| Candidate | adapter_id, adapter_version, result, evidence |

`Source.state` is configured/unavailable/unsafe. `inspection_id` is a UUID;
`inspection_state` is inspected/limited/unavailable/unsafe/unsupported_container.
Inspected means the declared inspection finished, not complete scientific or
whole-dataset validation. Inventory `kind` is regular/directory/unsupported;
`container` is directory/file/zip/tar/tar_gzip/gzip/sqlite/text/unknown. Issues and
candidate evidence are bounded string arrays. `recommended_skill` is
`external-database-onboarding` or null. Recognition-only always still needs an
compatible import implementation; SNUMAT v1 is the code-owned supported adapter.

Paths use safe forward-slash relative names. A registered file uses empty
`relative_path`; a registered directory requires a nonempty descendant path for
a sample. `member_path` is null outside archives or an exact safe ZIP/TAR member.
Absolute/traversal/device/stream paths, links and case-colliding members are
rejected. No archive is extracted. `sample_offset` is zero: v1 samples prefixes
only. `encoding` is utf-8 or base64; undecodable/NUL-containing evidence is binary.
`structure` is null or syntactic `json_keys`/`header_candidates` arrays, at most
64 strings of 256 characters; possible delimiter headers are not scientific
column mappings. SQLite evidence is limited to its 16-byte magic header, with
no SQL connection, table query or extension loading.

Intrinsic per-request limits are returned in `limits`:

| Limit | Maximum |
| --- | --- |
| Inventory entries, including directories/archive members | 256 |
| Directory depth below registered root | 2 |
| Samples | 8 |
| Individual prefix | 65,536 bytes |
| Total prefix bytes | 262,144 bytes |
| Raw source bytes read | 16,777,216 bytes |
| Decompressed bytes processed, including TAR skips | 16,777,216 bytes |
| ZIP central directory | 8,388,608 bytes |
| Cooperative deadline | 5 seconds |

`usage` contains raw_bytes, expanded_bytes, entries, samples and sample_bytes.
The deadline is checked around bounded work; it cannot interrupt an OS read.
Directory ordering is deterministic within the bounded scanned subset. Unscanned
children, depth limits, exhausted sampling and oversized TAR payload skips report
limited coverage. Archive-prefix rereads can scan metadata again and consume the
shared byte budget. Oversized ZIP inventories, ZIP64 and multidisk archives may
be unsupported. Unsupported, malformed and encrypted archives report issues;
unsafe members never yield selected content. Nested archives are not recursively
expanded. A source count or sampled inventory is not a full record count.

`SampleRead.verification` is `sample_only`. Reads re-resolve registration and
check ancestors, file size/mtime, before/after file identity and SHA256 of exactly
the sampled prefix. These checks do not seal unsampled bytes or an entire archive
and do not establish scientific provenance. Changed evidence raises `integrity`;
there is no automatic refresh. Missing configured paths return unavailable
inspection reports without creating paths or blocking unrelated startup. Removed
or unknown IDs raise `not-found`; malformed arguments raise ValueError, unsafe
filesystem access raises OSError, and access failures retain explicit diagnostics.
MCP additionally enforces result/chunk caps and maps errors through its existing
contract. No source call changes the retrieval index.

`src.knowledge.database_adapters.DatabaseAdapter` has `adapter_id: str`,
`adapter_version: int`, and pure
`probe(self: DatabaseAdapter, profile: JSONObject) -> JSONObject`. IDs are
lowercase hyphenated; versions are positive integers. The code-owned
`DATABASE_ADAPTERS: tuple[DatabaseAdapter, ...]` contains the SNUMAT v1 recognizer.
`probe_database_adapters(profile: JSONObject) -> JSONObject` validates finite
bounded JSON and gives each probe an isolated copy. Candidate result is
match/no_match/insufficient_evidence. One match yields known, multiple matches
ambiguous, unresolved candidates insufficient_evidence, otherwise unknown.
Ordinary probe errors/malformed results become insufficient-evidence candidates;
invalid caller input or registry metadata raises ValueError. Probes do no I/O,
code loading, installation or indexing. Packaging recognition alone never makes
an adapter match. SNUMAT v1 normalization/publication uses the explicit preview
and import contract below; other schemas need an approved adapter/import design.

## External import and search

`src.knowledge.external_index` provides:

```text
preview_external_import(paths: ProjectPaths, source_id: str,
    *, cancel: Callable[[], bool], progress: Callable[[JSONObject], None],
    timeout_seconds: int = 600, resume_operation_id: str | None = None) -> JSONObject
import_external_preview(paths: ProjectPaths, source_id: str, expected_plan_sha256: str,
    *, cancel: Callable[[], bool], progress: Callable[[JSONObject], None], timeout_seconds: int = 600) -> JSONObject
reindex_external_database(paths: ProjectPaths, source_id: str, *, expected_generation: str,
    cancel: Callable[[], bool], progress: Callable[[JSONObject], None],
    timeout_seconds: int = 600) -> JSONObject
search_external_database(paths: ProjectPaths, source_id: str, query: JSONObject,
    *, offset: int = 0, limit: int = 20, expected_generation: str | None = None,
    timeout_seconds: int = 60) -> JSONObject
get_external_record(paths: ProjectPaths, reference: JSONObject,
    *, verify_evidence: bool = True, timeout_seconds: int = 240) -> JSONObject
read_external_status(paths: ProjectPaths, source_id: str) -> JSONObject
```

External records use a separate catalog. Native source validators, native run
metadata and unified `knowledge_*` query kinds are unchanged. The existing project
server exposes the external operations; no source-specific server is installed.

Discovery exposes these closed schemas from `src.knowledge.external_schema`:
`database.external-reference.v1`, `database.external-query.v1`,
`database.external-preview.v1`, `database.external-report.v1` and
`database.external-record.v1`. Read `schema_get` for exact current nested shapes.

An external reference contains `schema_version: 1`, `kind: "external-run"`,
`source_id`, `adapter_id`, `adapter_version`, `case_id` and `record_sha256`.
The published case ID is preserved. Instance identity is source ID plus case ID;
record version is SHA256 of the normalized scientific payload. Archive snapshot
bindings are separate, so repacking unrelated bytes does not version unchanged
scientific records. Responses identify their exact snapshot binding.

Records preserve `inputs`, `planned`, `estimates`, `outputs`, `provenance` and
`issues` separately. Inputs come from labeled SRIM-2013.00 TRIM.IN sections.
SNUMAT v1 accepts one target layer. The adapter retains ion, energy, target
composition, density, thickness, incidence angle, ion count, random seed, mode,
correction and target atom energies. Planned manifest energy/depth and SR-module
range/straggling estimates are not substituted for actual input or TRIM outputs.

Output summary entries are `calculated_ions`, `transmitted_ions`,
`backscattered_ions` (ions), `mean_range`, `straggling` (A), and
`vacancies_per_ion` (vacancies/ion). Each quantity retains its original decimal
text, unit, availability and exact evidence locator. Missing or unparsable TDATA
makes summaries unavailable while independently usable inputs can remain usable.
Archive/member integrity failures reject affected evidence. Publisher validation,
launcher flags, errors and independent checks remain separate; a publisher
success claim is not scientific validation. Profiles and per-ion data are
explicitly not indexed. Coarser RANGE statistics are secondary evidence.

A query has exactly `schema_version: 1`, nullable `text`, `revision_scope`
(current/history) and `clauses`. Each clause has field/operator/value/unit.
Supported fields are:

- `inputs.ion_z`, `inputs.ion_mass_amu`, `inputs.energy_keV`,
  `inputs.incidence_angle_deg`, `inputs.number_of_ions`, `inputs.random_seed`,
  `inputs.calculation_mode`, `inputs.target_label`, `inputs.ion_symbol`,
  `inputs.target_elements`, `inputs.layer.thickness_A`,
  `inputs.layer.density_g_cm3`;
- `planned.requested_depth_nm`, `planned.selected_energy_keV`,
  `estimates.srmodule_interpolated_rp_nm`;
- `outputs.` followed by any of the six summary names above.

Numeric operators are eq/between/unknown; between is inclusive and permits one
null bound. String operators are eq/unknown; target elements use
contains_all/contains_any. Unknown requires null value/unit. Decimal predicates
use exact arithmetic and compatible unit shifts without rounding. Text is at
most 4096 characters, whitespace-separated literal AND terms, case-insensitive.
Pagination is offset >= 0, limit 1–100, default 20. An expected generation pins
catalog state, not source bytes. Search uses result `schema_version: 2`; stored
records, references and queries retain version 1. The result contains state,
generation, total, offset, limit, returned, next_offset, has_more, matches,
excluded_on_page, issues and verification. `total` is the pinned derived-index
predicate count before page exclusions; it does not assert that original archives
are unchanged or that every retained record was freshly hash-checked.

Each hit contains reference, snapshot_sha256, summary and verification. Summary
contains target_id, ion_symbol, target_label (at most 256 code points),
label_truncated, number_of_ions, energy_keV, calculated_ions, mean_range and
issue_counts by code. The three quantities retain exact `{value,unit}` pairs.
Full record/provenance/witness payloads are not embedded. Use database_get for
raw values, source locators and all scientific fields.

Search verifies the page's local objects, schema, snapshot binding and predicate
agreement. Top-level verification is `{record: "verified_page",
original_evidence: "not_checked", total: "derived_index_predicate_count"}`;
each successful hit has `{record: "verified", original_evidence: "not_checked"}`.
No query reads original archives. Source registration is still required. Missing
page objects or bindings are explicit exclusions; a predicate/projection
integrity disagreement fails explicitly. Issue messages are limited to 512 code
points with message_truncated. Byte limits can return fewer hits than requested.
`next_offset` advances past consumed candidates, including exclusions, and is null
only at the end. Unconsumed candidates remain on the next page. Never calculate
the next offset by adding the requested limit.

The UTF-8 JSON data budget is 131,072 bytes using the transport's two-space sorted
serializer; MCP's existing envelope/result caps still apply. A mandatory hit or
metadata that cannot fit fails with too-large. Search timeout_seconds is an
actual integer 1..240, default60; get uses the same range, default240. Cooperative
deadlines are checked during SQL/hash/TAR work. Expiry fails explicitly; blocked
OS I/O cannot be preempted. These deadlines do not increase the MCP client limit.

Get's verify_evidence defaults true. It verifies each required original compressed
archive hash once and scans its bounded TAR/PAX stream once per call for all owned
members; member hashes are streamed rather than buffered for full-record checks.
It rechecks file/handle metadata, registration and latest snapshot binding. No
hash result is reused across calls. False checks local records without claiming
original verification. Software/hash checks do not establish physical validity.

Locators contain source_id, relative_path, member_path, member_sha256, size_bytes,
json_pointer, line_start and line_end. JSON uses a pointer with null line bounds;
text uses inclusive 1-based lines with null pointer. Originals are read directly
from safe exact archive members, without extraction. Archive SHA256 comes from
the snapshot binding. Source registration is required on every access, including
access to derived records. Removing registration revokes reads without deleting
history. No read imports, refreshes or silently repairs a source.

Preview produces a ready/failed/cancelled plan with source fingerprints,
discovered/accepted/rejected and added/changed/unchanged/missing counts,
fields/units, unavailable outputs, diagnostics, storage_bytes, base_generation
and plan_sha256. It writes staging only. Exact staged bytes and disposable
catalog-build costs are measured; transient/journal overhead estimates are labeled.
Import requires that plan digest, fresh source verification and unchanged base
generation. A single transaction publishes committed records/heads/bindings and
a new generation. Import state is committed/failed/cancelled; cancellation before
commit rolls back, and cancellation after commit does not undo publication.
Refresh adds identities, versions changed content, skips unchanged content and
marks missing heads without removing historical versions.

Storage is `memory/knowledge/external/`: `index.sqlite`, immutable
`objects/<record_sha256>.json` and `snapshots/<snapshot_sha256>.json`,
`staging/<operation UUID>/plan.json` and `records/`, and
`reports/<operation UUID>.json`. Staged plans are retained for approval/retry.
SQLite is a local derived catalog with synchronous FULL, DELETE journaling,
parameterized queries and extension loading disabled. Writers use the external
store lock with a 30-second timeout; readers use read-only/query-only connections.
Corrupt or incompatible catalogs fail explicitly rather than being reset.

Derived scientific search indexes live at `search/<source_id>.sqlite`. Explicit
`database_reindex` jobs use `<operation_uuid>.building.sqlite` and retain failed
builds/reports. Reindex requires source_id, expected_generation and MCP request_id;
timeout_seconds is integer1..3600, default600. It reads validated immutable
imported records, not archives, and builds all retained versions and current/missing
head state in a read transaction. It needs at least2GiB free before building and
caps the published database at2GiB. Cancellation/deadline/failure never publishes
a partial index. Publication rechecks registration and catalog generation under
the existing lock, then atomically replaces only that source's derived index.
Reindex does not change the authoritative catalog or generation.

Projection schema1 stores metadata, documents (identity, presence, folded literal
text, projection digest), values_by_field (field, kind, exact rational number or
text) and elements (field, symbol). Field/type/number, field/type/text,
field/symbol and presence indexes support the existing closed predicates. Exact
numbers use canonical reduced numerator/positive-denominator strings and the
EXTERNAL_RATIONAL_V1 SQLite collation; no floats are used. Literal text preserves
the original casefolded substring-AND semantics and scans stored text when needed.
This is structured indexing, without embeddings or semantic ranking.

`database_status.search_index` contains state (missing/ready/stale/corrupt),
schema_version, generation, records, current and issues, with null metadata when
absent. Every import invalidates older projections through generation comparison;
run reindex after the explicit import. Search refuses missing/stale/corrupt
projections without an automatic slow fallback. Generation and page verification
detect consistency errors, but the replaceable local projection is not a signed
adversarial-tampering proof: altered projection contents can affect completeness
and require rebuilding. Catalog/schema corruption is reported, never reset.


Full-source verification streams SHA256 over selected source files, checks
publisher archive checksum claims and detects changing source metadata.
Publisher checksum agreement proves agreement with supplied bytes, not authenticity.
Limits are 16 archives, 1 GiB compressed and 512 MiB expanded per archive, 32,768
entries per archive, 32 MiB selected metadata members, 1 MiB selected case members,
100,000 case IDs, 8 MiB buffered case evidence, 512 MiB generated spool and a
600-second default cooperative operation timeout (explicit integer timeout_seconds in 1..3600). Reads reject traversal, absolute names,
duplicate/linked/sparse/device members and unsafe filesystem paths. Limit
exhaustion fails explicitly; it never publishes a silently truncated collection.
Full-source preview/import and selected-record verification are distinct from
the much smaller bounded discovery inspection.

Local PAX extension headers are parsed within 64 KiB and 32 records per header. Supported keys are atime, ctime, mtime, path and size; effective paths and sizes undergo the same safety and resource checks as ordinary members. Time metadata does not affect scientific identity. Global/chained/dangling PAX metadata, unknown keys, link and sparse overrides, and GNU extension records remain unsupported and fail explicitly.

External preview and import accept optional timeout_seconds (integer 1..3600,
default 600). The chosen deadline spans all source passes, normalization and
publication; the source reader honors that shared deadline. Expiration before
commit prevents publication. Native tool contracts are unchanged.


For the three SNUMAT SR estimate fields, the literal publisher token `nan`
means the numeric estimate is unavailable: `value: null`, `raw: "nan"`,
`availability: "unavailable"`, with the original JSON locator retained and a
`missing` diagnostic. Otherwise valid inputs and TRIM summaries remain usable.
Empty estimates retain their existing unavailable representation. Other malformed
numeric tokens and nonfinite planned/input values remain invalid. Local manual
overrides are not implemented; diagnostic SR reruns do not replace source values.


External `database_preview` accepts optional `resume_operation_id` (canonical
lowercase UUID or null, default null), also accepted by
`preview_external_import(..., timeout_seconds=600, resume_operation_id=None)`.
Resume selects a terminal failed preview with complete normalization in this
project. Fresh full fingerprints, exact source inventory/member hashes, cached
record schemas/digests and unchanged catalog generation are required. Partial,
corrupt, unsafe or stale caches fail explicitly; no fresh-normalization fallback
occurs. Immutable derived records are hardlinked into a new staging operation;
unsupported hardlinks fail explicitly. Original failed evidence and rejection
diagnostics remain preserved. The default path still creates a fresh preview.
Resume does not apply new mappings to prior rejections or publish searchable
records; the new ready plan still requires explicit import approval.
