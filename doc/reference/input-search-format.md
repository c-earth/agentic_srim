# Native input-search APIs and formats

[Usage](../guides/input-search.md) · [Schema validators](../../src/knowledge/schema.py)

Only native SR/TRIM input retrieval is implemented. Existing IDs, metadata, runner signatures and worker ownership remain unchanged. No external importer, CLI or output analytics. The catalog starts empty; new terminal records index automatically without historical backfill.

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
