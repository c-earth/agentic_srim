# External simulation imports and input search proposal

Status: proposed for approval; no implementation authorized by this document.
Date: 2026-10-21.

The current first-version approval contract is [Native SR/TRIM input search](NATIVE_INPUT_SEARCH_PROPOSAL.md). This older document is deferred design history; its external-import APIs, schemas and migration work are not part of the native-only implementation.

## Scope change: native-only first version

The user subsequently requested only the generic SR/TRIM formats/layouts developed by this project and to start with no data. This section supersedes conflicting scope, file/API, migration and acceptance requirements below. The detailed external-import design is retained as deferred design history, not an implementation contract for the first version.

- Start with an empty search catalog and index newly finalized project-generated SR/TRIM runs automatically. Do not backfill historical records automatically.
- Support only this project's existing native simulation records and preserved input formats/layouts. No generic external-folder import, metadata mapping, import profile implementation or external collection adapter in this version.
- Defer all NERSC inspection/import/indexing/migration acceptance work. Leave memory/NERSC_TRIM_20k and its original files untouched. Do not delete existing project records; starting empty refers to the new catalog, not removal of evidence.
- Preserve approved input filter, energy/target matching, unknown/conflict, terminal-status, pagination and incremental-indexing principles where applicable to native runs. External collection/planned-depth/origin-choice filters and import ID allocation have no implementation requirement in this version.
- Keep future source-reference extensibility as a design boundary; implement only the native reference kind now.
- Rewrite the implementation file list, schemas, typed API catalogue and verification plan for this smaller scope before seeking complete implementation approval. Existing external-import functions below are deferred and must not be implemented under native-only approval.

## 1. Agreed scope

Provide Python APIs to import external simulation collections and search/filter native and imported SR/TRIM records by input parameters. Search includes both origins by default. Preserve original evidence and expose references that future post-processing can consume. Include verified migration of memory/NERSC_TRIM_20k into the agreed permanent import layout.

No CLI, embeddings, vector database, answer model, internet retrieval, output indexing, plotting, new scientific analysis, or automatic engine execution in this increment. Narrative case documents and existing post-processing conclusions are deferred; input-bearing external case collections are supported. Existing case/ records can link simulation references through the explicit profile described below. An imported case is a simulation record, not automatically a new project case/ allocation.

Previously approved decisions: flexible source layouts; reusable profiles; preservation of originals; separate collection/import IDs; native IDs unchanged; serialized input values preferred over planned metadata; unchanged re-imports skipped; new runs added; conflicting changes reported without overwrite; AND between fields and OR within a field; explicit units and inclusive numeric bounds; unknowns excluded from ordinary filters; engine/mode searchable; Python API only.

All additional details below are proposals, including defaults and precise comparison semantics. Approval of the whole document is needed before implementation. Unlisted new functions or changes to these contracts require an amendment before coding them.

Subsequent decisions approved individually: automatic incremental indexing after standalone runs, batches and imports; single/list/range energy matching as specified in section 6; distinct target label/element/composition filters with explicit layer selection; searchable partial records with unknown/conflicting fields preserved; indexing terminal succeeded and failed native runs by default, with an optional native execution-status filter. These approvals do not authorize implementation of the remaining unreviewed contracts.

Search pagination also approved: default 100 matches, maximum 1,000 per call, total valid match count, stable ID ordering and optional generation pinning; a changed pinned generation is reported rather than mixing pages.

## 2. Existing architecture and integration

Reviewed current backend paths/records/resources, SR inputs/resolution/runtime/execution, TRIM inputs/resolution/runtime/execution/outputs/storage/queue, relevant tests, and the current TRIM handoff. TRIM execution is committed as 7d4c780 according to that handoff. This proposal does not resume or revise TRIM execution work.

Follow the established separation between caller intent, serialized effective inputs, observed files, and execution status. Preserve bytes and numeric spellings. Use pure parsers, explicit validation, versioned JSON, SHA-256 plus sizes, exclusive publication, bounded locks, retained failure evidence, and independently verifiable references. Do not consult current SRIM defaults or launch SRIM to interpret historical files.

Reuse native read_simulation and sealed input inventories, existing mode tables, safe regular-file reads, JSON publication conventions and lock mechanics. Do not change ProjectPaths, Config, existing record kinds, lifecycle schemas, worker allocation or native simulation IDs. Derive new paths from ProjectPaths.root. New import allocation must not masquerade as a native lifecycle transition.

Existing parse_trim_directory already freezes and parses one external directory. Reuse its pure output parsers later; do not call its whole-directory copying workflow for every archive member in this increment. No output parser changes are required here. Imported-run usability is independent of parse_trim_outputs.complete, which expects a fuller output set than the NERSC export contains.

## 3. Storage and identity

```text
memory/
  imports/
    .records.lock
    identities.json
    .staging/<operation-id>/
    collection_YYYYMMDD_HHMMSS_N/
      collection.json
      profiles/<profile-sha256>.json
      source/<snapshot-sha256>/<original-relative-path>
      operations/<operation-id>.json
      runs/import_YYYYMMDD_HHMMSS_N/
        record.json
  knowledge/
    .records.lock
    index.sqlite
    index-manifest.json
```

Use configured record timezone and first available positive suffix, with separate collection/import prefixes. Import IDs are project-wide unique despite their nested storage. identities.json reserves allocated IDs permanently, including failed allocations; no import-deletion API is added. Existing worker/srim/post/case allocation remains unchanged. A collection label is human-readable and does not determine filesystem identity. Original external case IDs remain exact strings, unique within a collection; they never become paths supplied by the producer.

IDs are stable identities, not content hashes. Snapshot hashes identify exact sorted file inventories. Separate native executions with identical bytes retain their separate identities. Provenance timestamps are distinct from import timestamps.

The source directory preserves original archives without permanent expanded duplicates. Snapshot directories permit additive re-import without replacing earlier evidence. Unchanged source files may reference a previous snapshot in the same collection; no cross-collection deduplication. Each archive/member reference pins both archive and member hash/size. Scratch extraction and verification artifacts live under .cache/import-search/. Bounded input/member buffers are allowed; outputs are streamed only when explicitly requested.

## 4. Data formats

All new JSON is UTF-8, schema_version=1, deterministic sorted object keys, indentation two, final newline, allow_nan=False, duplicate keys rejected. Unknown schema fields are rejected, except an explicit external_metadata object containing preserved producer fields. JSON numbers used for counts/indices must be actual integers, not bool. Scientific values are finite decimal strings; preserve original lexical tokens, use Decimal for comparisons, and perform no binary-float normalization. Hashes are lowercase SHA-256 and sizes nonnegative integer bytes. Stored paths are safe relative forward-slash paths; API filesystem paths are absolute. Timestamps include an explicit UTC offset.

The following are exact object shapes; question-mark notation means nullable, not an omitted key. Lists preserve order unless explicitly defined as sorted.

### Evidence references

SourceReference = {kind, id, collection_id, record_sha256}:

- kind: simulation or import.
- id: existing native simulation ID or globally allocated import ID.
- collection_id: null for native, owning collection ID for import.
- record_sha256: exact native metadata.json or imported record.json hash. A changed native lifecycle record requires refresh; it must not silently resolve against an earlier reference.

FileReference = {owner, storage, path, member, sha256, size_bytes, container_sha256, container_size_bytes}:

- owner: SourceReference, or null for collection-level profile inspection before run publication. A run may own a reference to its collection's frozen profile as well as its source files; containment is checked against its owning collection. The profile file is explicitly listed in that run's files.
- storage: file or tar-member. path is relative to the project after import, relative to the inspection root before publication; inspection references are not accepted by read_source_file.
- member: safe exact archive-member path for tar-member, otherwise null.
- sha256/size_bytes identify returned file/member bytes; container fields identify archive bytes for tar-member, otherwise null.

Observation = {field, value, unit, basis, evidence, external_key}:

- field: normalized field path, including positional layer/atom indices where relevant.
- value: JSON string/integer/boolean/list/object or null; scientific numbers are strings. Booleans are permitted only for observed boolean controls, never numeric quantities/counts.
- unit: canonical unit or null for categorical/dimensionless fields.
- basis: serialized_input, planned_metadata, or user_mapping. Output-derived observations are excluded.
- evidence: list of {file, line_start, line_end, json_pointer, csv_row, column}; inapplicable locators are null. Lines/rows are one-based; CSV row 1 is its header.
- external_key: original field name or null. User mappings cite the preserved profile file.

Issue = {code, severity, external_id, field, message, evidence}; severity is warning or error; nullable external_id/field; evidence is an observation-locator list. Codes: missing, unsupported, malformed, ambiguous, conflicting, rounding_difference, source_changed, checksum_mismatch, duplicate, unavailable, stale.

### Search record

Record = {schema_version, reference, label, origin, execution_status, imported_at, input_status, inputs, planned, observations, issues, files, external_metadata, parser_version, input_fingerprint}.

- Stored reference.record_sha256 and self-owned FileReference.owner.record_sha256 are null to avoid self-hashing; readers fill the exact document hash in all returned references. Persisted self-owner identities must otherwise equal the enclosing record identity. Native search projections are index-derived and use metadata hashes directly. A returned SourceReference always has a non-null hash.
- origin: native or imported; imported_at is null for native projections.
- execution_status: succeeded or failed for terminal native runs, null for imports. Producer-specific imported statuses remain in external_metadata; importing does not establish project-observed execution success. Native created/running records are excluded until terminal. Default search includes both terminal native statuses and all eligible imports.
- input_status: parsed, metadata_only, partial, or unknown. These describe inputs, never execution success.
- inputs: the normalized object below, every key present with null for unknown.
- planned: {requested_depth_nm, fields}; fields retains profile-mapped planned observations. Unknown requested depth is null.
- files: sorted list of {role, reference}; role is sr_input, trim_input, events, metadata, output, or other. Output filenames remain accessible without reading numerical contents.
- external_metadata: original producer dictionaries, including external ID and historical statuses, retained without treating producer validation as local execution proof.
- parser_version: srim-input-search-v1. input_fingerprint hashes sorted run-associated file/member identities and mapped source metadata. A changed output is a changed supplied run even though it is not searchable.

inputs = {engine, engine_family, mode, mode_code, projectile, energy, target_label, layers, incidence_angle_degrees, total_ions, random_seed}.

- engine: sr, trim, or null; engine_family: 2008, 2013, or null; mode uses existing engine-specific labels. SR uses list/range with mode_code null. TRIM uses existing MODES labels and source numeric code; unknown family leaves an ambiguous label null, retaining the numeric code.
- projectile: {atomic_number, mass_amu}, or null; mixed event projectiles leave it null, retaining event inputs separately in observations.
- energy: null or {kind, values_keV, minimum_keV, maximum_keV}; kind single/list/range; single/list preserve ordered values and duplicates, bounds null; range has an empty values list and both bounds. Event modes do not treat the nominal TRIM.IN energy as actual event energies. Valid event data supplies ordered energies; unparsed/missing event data leaves event energy unknown.
- layers: null or ordered {index, label, phase, thickness_angstrom, density_g_cm3, components} objects; index one-based; components are ordered {atomic_number, mass_amu, atomic_proportion}. Retain repeated isotope/element entries. SR is one homogeneous target entry with unknown thickness, not a finite TRIM layer. Thus layer_count is applicable only to TRIM.
- incidence_angle_degrees: TRIM.IN angle convention, not a converted angle from a surface normal; event direction vectors do not imply one scalar angle.
- total_ions: requested TRIM control count, not completed count or assumed event-list length.
- random_seed: serialized initial control value, not a saved final RNG state; zero remains zero and does not establish deterministic reproducibility.

Serialized observations take precedence field by field. If unavailable, an unambiguous mapped metadata observation may supply a value, with its basis retained. Conflicting metadata candidates without a serialized value leave the field null. A disagreement with serialized input retains both values and flags the difference; no broad relative tolerance. A metadata decimal that rounds to the serialized token at its represented decimal place is rounding_difference, not an exact-equality claim. Do not replace a serialized token with the manifest's extra digits.

Collection = {schema_version, id, label, created_at, profile, snapshots, run_ids, external_id_map}; profile is the frozen import-profile object; snapshots is a list of {id, files}, files being sorted {path, sha256, size_bytes}; run_ids sorted; external_id_map maps exact external IDs to import IDs. Publication updates this manifest atomically only after run records exist. Pending operations are recoverable journals, not search-visible records.

Identities = {schema_version, collections, imports}; each list contains permanently reserved IDs. Operations = {schema_version, id, collection_id, state, source_inventory, completed, skipped, conflicts, pending, destination}; state pending/published/migrated. Journals contain no invented simulation timestamps.

### Import profile and inspection

Profile = {schema_version, name, version, layout, engine_hint, engine_family_hint, groups, metadata_tables, field_map, existing_references}.

- name: generic-srim-trim or nersc-snumat-v1 or caller-defined portable lowercase hyphenated identifier; version positive integer; layout detected, explicit, or nersc-snumat-v1.
- hints are null unless explicitly supplied/observed; they never silently override contradictory file contents.
- groups: list of {external_id, label, files}; files are exact {path, member, role} associations. No executable profile code, regular-expression capture language or producer-controlled filesystem operations.
- metadata_tables: list of {path, member, format, key_column}; format csv/json. CSV uses UTF-8 with optional BOM, standard quoting and newline-aware reading. JSON points to one object or a list of objects; key_column is a top-level key. Duplicate/absent keys are issues, not last-row-wins.
- field_map: map from known normalized field name to producer column/key name. No expressions or implicit units; profile-mapped quantities require companion unit entries using keys <field>.unit. All producer rows remain in external_metadata.
- existing_references: list of SourceReference objects for case collections that already reference project simulations. They do not create duplicate imported runs. Arbitrary narrative case parsing is not supported.
- Default generic profile has all lists/maps empty, null hints, layout detected. NERSC built-in profile pins its archive-root layout, manifest/case_mapping joins by case_id and known units. The profile is persisted with source evidence, not settings.toml.

Inspection = {schema_version, source_inventory, profile, limits, groups, existing_references, issues}; each group = {external_id, label, files, metadata, observations, inputs, planned, issues, fingerprint}. No IDs allocated or source changes during inspection. Default generic detection groups supported input files by their containing run directory; root/SRIM Outputs is an output subdirectory of that run. Directories without input files require explicit/profile metadata grouping. Multiple inputs for either engine, or both SR and TRIM inputs, in the same proposed group require explicit separation. Directory names alone do not establish physical inputs. External IDs default to the safe relative run directory, or '.' for a single source-root run; they are strings, not internal path names.

SourceInventory = {schema_version, files, members, fingerprint}; files is a sorted list of {path,sha256,size_bytes}; members is a sorted list of {path,member,sha256,size_bytes}, where path identifies the container in files. fingerprint hashes the canonical files/members arrays. Limits has exactly schema_version plus the five positive integer limit keys listed in section 8. Frozen profiles are preserved as profiles/<sha256>.json, hashed over exact JSON bytes and included in each affected run's metadata file references.

## 5. Input parsing

Add reverse parsing separately from existing renderers. Parse native serialized parameters, not high-level material-selection/CAB intent. Preserve unknowns instead of manufacturing a runnable request.

SR parser supports the current generated ASCII SR.IN format, native matching heading structure, positive numeric tokens, explicit target rows, unit code, range controls or ordered energy list terminated by zero. It validates section structure/counts and reports unsupported dialects rather than guessing field positions. No output-header fallback.

TRIM parser supports measured native 2008/2013 and current generated TRIM.IN headings, CP1252, LF/CRLF/CRCRLF splitting, quoted labels/doubled quotes, positional atom/layer rows, phases, per-layer corrections, atom energies and native controls. Use existing MODES mappings only when family is known from content/profile. Retain parsed controls not yet searchable in observations. Support project-generated TRIM.DAT syntax and observed compatible eight-numeric-field records with explicit IDs/units and preserved order; unsupported dialects leave event-specific search fields unknown. Do not claim file-to-execution provenance.

Both parsers return {schema_version, engine, inputs, observations, issues, parser_version}. Invalid API argument types raise ValueError. Malformed external bytes return partial observations plus issues; never silently skip a malformed required section and claim fully parsed status. Limits for source inspection are explicit API inputs, as below.

## 6. Search contract

Filter object shape: {schema_version:1, clauses:[...]}. Each clause = {field, operator, value, unit, layer}. Omitted optional filter argument means an empty clause list. Reject unknown keys/fields/operators, invalid units and nonsensical engine/field combinations before searching. Contradictory valid clauses return no matches.

Registered fields:

| Fields | Operators and values |
| --- | --- |
| engine, mode, origin, execution_status, collection_id, external_id, target_label | eq(string), in(nonempty string list), unknown(null) |
| projectile.atomic_number | eq(integer Z), in(integer Z list), unknown |
| projectile.mass_amu | eq(decimal string), between([lower-or-null,upper-or-null]), unknown; unit amu |
| energy | eq/between/unknown; unit eV, keV, MeV, GeV |
| target.elements | contains_all(integer Z list), contains_any(integer Z list), unknown |
| target.composition | eq ordered-independent list of {atomic_number,mass_amu,atomic_proportion}, unknown |
| layer_count | eq(integer), between(integer bounds), unknown; TRIM only |
| layer.thickness | eq/between/unknown; unit A,nm,um,mm,cm,m |
| layer.density | eq/between/unknown; unit g/cm3 |
| incidence_angle | eq/between/unknown; unit deg |
| total_ions, random_seed | eq(integer), in(integer list), between(integer bounds), unknown |
| planned.requested_depth | eq/between/unknown; unit A,nm,um,mm,cm,m |

Numeric eq is exact mathematical decimal equality after exact unit conversion, not a tolerance comparison. Scientific query values are strings; integer query values reject bool. between bounds inclusive, at least one non-null bound, lower <= upper. Units required for dimensional fields, null otherwise. Atomic numbers 1..118 are allowed for metadata search; historical engine support is not inferred from metadata acceptance.

Different clauses use AND; in/contains_any provide OR within a field. Null/unknown fields do not satisfy ordinary predicates. Unknown explicitly selects unavailable or unresolved values. No inferred isotope mass number from rounded mass_amu. Labels are Unicode casefold exact matches; IDs exact case-sensitive matches. Element symbol conversion may be performed by the agent; this API accepts Z.

Layer selectors: layer=null for non-layer fields; positive integer for an exact layer, or 'any'/'all' for layer fields and target.composition. Default is not inferred: layer must be explicit on these fields. Multiple 'any' clauses may match different layers; use the same explicit layer index to require co-location. Unknown means absent at the selected layer(s). For all, require a nonempty layer list and all layers known/matching. target.elements searches the union across known target components; if target coverage is partial, contains_all/any can confirm observed presence but cannot establish absence. SR homogeneous target uses selector 1 or any/all for density/composition; thickness and layer_count are unavailable.

Exact composition compares atomic proportions as rational ratios independent of common scaling and element order, preserving distinct mass identities and rejecting duplicate query identities. Missing masses cannot establish isotope-specific equality. No formula/name inference or cross-layer averaging. Requested compositions with null mass match elemental composition after aggregating entries by Z; isotope-specific queries require all masses. These two query forms cannot mix null and specified masses.

Energy single/list matches when any explicit value satisfies eq/between. For continuous SR ranges, eq means the requested energy belongs to the requested interval and between means intervals overlap; results retain energy.kind so membership is not presented as a sampled output point. Event-mode projectile and energy clauses must match the same event when both are supplied. Event angle cannot be reduced to the TRIM.IN nominal angle. Event data is input only and may be cached in the derived index; it is not a parsed output.

SearchResult = {schema_version, generation, total, offset, limit, matches, excluded}; matches are normalized records with sealed references, in stable (kind,id) lexicographic order. offset >=0, default 0; limit 1..1000, default 100; total counts valid matches before pagination. No relevance ranking. excluded is a list of {reference,code,message} for catalog entries rejected as stale/missing/invalid; no silent stale matches. Generation is the index-manifest hash. A paging caller can pin expected_generation; a mismatch raises ValueError rather than returning mixed pages.

## 7. Index and source access

Use standard-library sqlite3 as a rebuildable catalog, not an evidence store. No FTS or embeddings needed for the agreed exact filters. Database schema user_version=1:

- records(kind TEXT, id TEXT, collection_id TEXT NULL, record_sha256 TEXT, projection_json TEXT, PRIMARY KEY(kind,id)). Index collection_id; origin/engine/mode categorical columns may only be added by approved schema amendment.
- issues(kind TEXT, id TEXT, code TEXT, message TEXT); entries are indexing failures, not fake searchable runs.
- meta(key TEXT PRIMARY KEY, value TEXT), containing schema_version, generation, parser_version.

Structured indexed identity narrowing plus Python Decimal/rational predicates avoids SQLite REAL conversion. Load projections in bounded batches. Performance must be measured on the 20k dataset; no premature vector-service dependency. Search validates indexed document hashes and native sealed input inventories against current source bytes before matching, never trusts stale projections. Native records can be mutable while created/running: index only terminal succeeded/failed sr/trim records; omit sr-capture and unfinished records with explicit refresh issues. Expected input damage produces exclusion, not a misleading native input record.

Incremental indexing after simulation finalization, batches and imports is approved. Update only explicitly supplied new/changed terminal records; never scan existing collections or decompress unrelated archives for a single-run update. Initialize an empty catalog on the first incremental update. Existing historical runs require an explicit initial rebuild or explicit reference list. Search before any catalog publication raises FileNotFoundError; search itself has no hidden writes or auto-import. Source changes invalidate affected matches and are reported until updated. Native requested and effective input documents remain linked; searchable physical settings come from preserved serialized input, with effective JSON as a cross-check and fallback identified as metadata. No output values are parsed. Case linkage deduplicates by SourceReference and does not imply a second execution.

An incremental update validates the supplied references, builds their projections outside the knowledge lock, then applies just their upserts/removals and issues in one SQLite transaction under that lock. Unchanged supplied document/input hashes skip reparsing. A supplied invalid existing record is removed from searchable rows and given an issue entry. Bump generation only when catalog contents change; a no-op update retains generation. Database transaction commits before index-manifest publication; an interrupted mismatched pair is rejected until repaired by update/rebuild. No whole-database rewrite is required per simulation.

The shared finish_simulation boundary triggers a best-effort incremental update only after terminal metadata has been durably published and its record-parent lock released. Keep its existing typed signature and returned metadata unchanged. Exclude sr-capture. Both success and failed terminal runs may be indexed if their inputs verify. The hook uses a local import to avoid module-import cycles. It must remain outside the native execution failure-classification path: an ordinary indexing exception cannot rewrite terminal status, trigger execution cleanup/failure finalization, or replace the simulation's return value.

Prepared SR/TRIM batches defer per-run hooks using a context-local collector, propagated explicitly into each worker thread with its own copy_context(). At batch exit, index the collected finalized references together, including safely finalized runs when another task raises. Restore context in finally. Individual caller threads outside the batch keep standalone behavior. No process-global suppression flag and no background indexing service. Finish all lifecycle writes before catalog publication; never hold a record-parent lock while acquiring the knowledge lock. Index readers acquire no worker leases.

import_collection calls the same incremental update after publishing source records and releasing the imports lock, passing only added and previously unindexed unchanged references. Return its separate indexing report; re-import retries a previous indexing failure without copying or allocating duplicates. Indexing failure never rolls back preserved sources. Both automatic and explicit updates write reports under memory/knowledge/updates/<operation-id>.json where possible, including attempted/indexed/rejected references and errors. Native hooks additionally log failures through the module logger; report/log failures are contained so simulation results remain valid. If storage cannot retain a report, the explicit update/rebuild can recover from permanent records. KeyboardInterrupt/SystemExit are not silently swallowed, and already published terminal metadata remains terminal. No latency promise until measured.

refresh_index publishes a complete new database and manifest under the knowledge lock. Readers hold the same lock while opening/checking the generation and collecting a result page, so replacement is Windows-compatible and pages are consistent. Lock contention fails after 30 seconds. Atomic pair publication uses the manifest as commit marker; mismatched database/generation after interruption is rejected and rebuildable. Do not acquire worker locks or perform network access. No background thread/process lifecycle.

read_source_file verifies owner identity, current hashes and safe containment before returning selected bytes. Archive member lookup scans gzip sequentially; no promise of random access or zero scan cost. Output access may be slower than unpacked storage; extraction caching is deferred. Exact raw bytes are sufficient for downstream parsers; this increment does not implement post-processing methods. Post-processing must pin the returned reference and declare required file roles.

## 8. Import lifecycle, re-import and NERSC migration

inspect -> review Inspection -> import with incremental indexing -> search -> optional file access. Full refresh is a separate recovery/initial-catalog operation. Inspecting allocates nothing and writes nothing. Importing does not run engines. Per-run input errors do not prevent preserving its files and unknown fields; ambiguous grouping or unsafe archives block affected publication. Byte-integrity errors block their whole snapshot.

First import has collection_id=None and allocates one collection plus globally unique import IDs. Later import must explicitly supply that collection_id; identical content in a different collection is not silently conflated. Skip unchanged fingerprints, add new external IDs, report changed fingerprints under existing IDs without overwriting either source interpretation or prior outputs. Additive imports preserve old sources. Ignore removed external IDs as removal is not deletion authorization. Immutable record.json includes its initial parser interpretation; refresh may derive a newer projection but cannot rewrite the source record. Explicit interpretation revision APIs are deferred; reports retain parser version and source hashes.

Operation journals pin the inspection fingerprint and assigned IDs. Repeating the same pending operation reuses reservations and already verified staged work. Completed records are listed in collection.json only after publication. A mid-import exception retains staging and journal paths in exception notes. Existing collection manifests and records remain valid. Import holds the imports lock for publication/identity allocation; scanning and copying happen outside the lock against pinned inventories, then are rechecked. Concurrent changes to the same collection are detected by the collection-manifest hash and require re-inspection; never merge silently. A run that conflicts remains an explicit report item while unrelated additions may complete.

Archive support: .tar.gz and .tgz, plus ordinary directories. Native .zip support is deferred because the supplied dataset is tar.gz; report other containers as unsupported. Inventory all members, reject absolute/parent paths, links, special files, duplicate names, Windows case collisions, overlapping file/directory identities, and unsafe reserved names before publication. Do not execute source scripts. Read archives through tarfile without extractall; stream selected files/members. Profile/member paths refer to the archive namespace, not historical absolute producer paths.

Default explicit limits: max_files=250000, max_total_bytes=10737418240 (10 GiB decoded), max_input_bytes=16777216 (16 MiB per parsed input/metadata document), max_metadata_bytes=268435456 (256 MiB across metadata), max_archive_bytes=10737418240 per container. Exceeding a limit reports/raises before committing the affected snapshot. These are operational bounds, not scientific engine limits; callers may explicitly increase positive integer bounds. Metadata documents above the limit are preserved but not loaded for parsing; master CSV tables are streamed under the aggregate metadata bound.

NERSC built-in profile joins manifest.csv/case_mapping.csv/case.json by case_id, checks uniqueness/target membership, preserves planned columns, and resolves exported cases/<target>/<case_id> paths instead of trusting historical targets/... paths. The actual TRIM.IN member supplies serialized settings. Report rounding differences separately. Historical validation/README claims remain external assertions. Unavailable exported files do not become available because validation.json lists them. Check top-level SHA256SUMS.txt and dataset member checksums, plus per-case supplied hashes where present; agree-by-hash establishes integrity relative to supplied manifests, not independent authentication of authorship/execution.

Migration source is D:/projects/agentic_srim/memory/NERSC_TRIM_20k. First preserve and verify all 11 archives, README.md, SHA256SUMS.txt under the allocated collection snapshot, publish all run records and collection manifest, refresh and verify the catalog, then finalize migration separately. finalize_migration rechecks destination inventory, source inventory, source-to-destination coverage and catalog visibility of every published imported run. Only after these pass does it remove precisely the original 13 allowlisted regular files and the now-empty source directory. Unexpected additions/links or changed files block removal. No recursive deletion, wildcard deletion, or source removal on partial/conflicting import. Return the permanent collection path; if removal fails, leave both copies and report retained source files. A repeated successful finalization is idempotent through its migrated journal, with destination verification repeated.

## 9. Proposed files and dependency direction

Paths below are relative to D:/projects/agentic_srim. All implementation is standard-library Python and must meet AGENTS.md docstring, annotation and no-comment requirements. There are no new classes; JSON APIs follow the existing JSONObject convention. No package re-export wrappers or new settings fields.

| File | Purpose, contents, dependencies, relationships |
| --- | --- |
| src/knowledge/schema.py | Exact schemas, validation, unit/rational predicates and filter matching. Depends on Decimal/Fraction and existing JSON aliases. Pure; shared by all new modules. |
| src/knowledge/input_parsing.py | Reverse SR/TRIM/event input parsers and observations. Depends on schema and existing engine mode maps. Pure, no engine launch/resource lookup; leaves current renderers untouched. |
| src/knowledge/source_io.py | Safe inventories, tar-member access, native/import record reads, common file resolution. Depends on schema and existing safe I/O/hash/metadata readers. No import publication or output interpretation. |
| src/knowledge/importing.py | Built-in/explicit profiles, inspection, snapshot/record publication, allocation/journals, re-import and migration. Depends on schema, input_parsing, source_io, index and existing locks/time/atomic JSON tools. Index publication follows release of import locks. Migration accepts a verified incremental-update or refresh report. |
| src/knowledge/index.py | Native/import projections, incremental transactions, context-local batch collection, separate indexing reports, explicit catalog rebuild and generation publication. Depends on schema, source_io, input_parsing, contextvars, contextlib and sqlite3. Never allocates runs. |
| src/srim/metadata.py | Keep finish_simulation signature/schema; move its return outside the record-parent lock and notify indexing only after terminal publication. Local import of the notification helper avoids import cycles. Indexing failures never alter returned metadata. |
| src/srim/queue.py; src/srim/trim_queue.py | Keep batch signatures, task/result classes and scheduling semantics. Add context-local indexing collection around dispatch and explicit context propagation per submitted task; flush finalized references at batch exit without masking original execution errors. |
| src/knowledge/search.py | Read-only filtering/pagination over a valid catalog and stale-reference exclusion. Depends on schema, source_io, index formats and sqlite3. No import or index refresh side effects. |
| test/knowledge/test_schema.py | Validation, units, unknowns, composition and energy/layer/event semantics. |
| test/knowledge/test_input_parsing.py | Reverse parsing, source spans, numeric spelling, malformed/unknown dialects and family ambiguity. |
| test/knowledge/test_source_io.py | Safe paths/archives, source mutation, pinned resolution and lazy output access. |
| test/knowledge/test_importing.py | Grouping/profiles, conflicts, re-import, allocation, interruption, locking, migration allowlist and checksum failures. |
| test/knowledge/test_index_search.py | Both origins, native state filtering, refresh, generations, pagination, stale data and result-to-file access. |
| test/knowledge/fixtures/ | Small synthetic SR/TRIM collections, cross-layout metadata and archives. No engine executables or full NERSC data. Each fixture has explicit expected identities. |
| doc/guides/import-search.md | Python inspect/import/refresh/search/file-access examples and NERSC migration instructions. |
| doc/reference/import-search-format.md | Approved schemas, contracts, filter semantics and lifecycle. |
| README.md; doc/README.md; doc/reference/api.md | Link and describe implemented import/search entry points after verification. |
| doc/IMPORT_SEARCH_PROPOSAL.md | This approval contract and subsequent approved amendments/verification state. |

Tests depend on the new public contracts, use unittest and descriptive .cache/import-search/ temporary subdirectories, and do not alter live SRIM installations. Extend test/srim/test_metadata.py, test/srim/test_queue.py and test/srim/test_trim_queue.py for post-publication notification, deferral, thread isolation, failure containment and unchanged native results. Only the three existing implementation modules listed above are proposed for modification; further changes require an amendment.

## 10. Complete planned function catalogue

JSONObject and JSONValue are existing types. Path is pathlib.Path; Mapping/Sequence are collections.abc; Iterator is collections.abc. All return independent JSON data/bytes. Unless listed otherwise, invalid arguments raise ValueError, filesystem failures propagate OSError, and unsafe paths are rejected. No implementation-only extra helpers are preauthorized: amend this catalogue if decomposition requires them.

### schema.py

```python
def validate_document(document: JSONObject, kind: str) -> None: ...
def convert_quantity(value: str, source_unit: str, target_unit: str) -> str: ...
def matches_filters(record: JSONObject, filters: JSONObject) -> bool: ...
```

- validate_document: kind is reference/file/observation/issue/record/collection/identities/operation/profile/inspection/filters/result/refresh/import-report/limits. Validate exact relevant schemas, safe identities, types and invariants above; return None; no effects. Unsupported kind or invalid data raises ValueError with field path.
- convert_quantity: finite decimal string and registered dimension-compatible units; return exact fixed-point string without invented precision. No effects; invalid syntax/units or incompatible dimensions raises ValueError. Use exact decimal exponent shifts for the approved units.
- matches_filters: validated search projection and filter envelope; return exact predicate result using the rules above, retaining event correlation. No effects or input mutation; invalid records/filters raise ValueError.

### input_parsing.py

```python
def parse_sr_input(contents: bytes, *, filename: str = "SR.IN") -> JSONObject: ...
def parse_trim_input(contents: bytes, *, filename: str = "TRIM.IN",
    engine_family: str | None = None, events: bytes | None = None) -> JSONObject: ...
def parse_trim_event_input(contents: bytes, *, filename: str = "TRIM.DAT") -> JSONObject: ...
```

- parse_sr_input: contents are bounded source bytes; filename is a safe evidence name, not an opened path. Return the parsing envelope and source spans. No I/O; byte-level input defects yield issues and partial values, type/unsafe filename errors raise ValueError. Never fabricate a complete runnable request.
- parse_trim_input: same byte/name rules; optional explicit family hint only 2008/2013; events supplies associated input bytes or None. Return native observations plus normalized query fields, retaining discrepancies between header family and hint. Uses parse_trim_event_input when appropriate; no effects. Content defects are issues; invalid API arguments raise ValueError.
- parse_trim_event_input: return {schema_version, events, observations, issues, parser_version}; events are ordered {id,atomic_number,energy_eV,position_angstrom,direction_cosines}, preserving strings. No effects; unsupported/malformed content yields issues and only independently parsed records, with incomplete event coverage flagged. Partial event sets cannot establish whole-run any/all absence. Filename/type errors raise ValueError.

### source_io.py

```python
def inventory_source(source: Path, *, limits: JSONObject) -> JSONObject: ...
def read_inspection_file(source: Path, file: JSONObject, *,
    max_bytes: int) -> bytes: ...
def iter_source_records(paths: ProjectPaths) -> Iterator[JSONObject]: ...
def read_source_record(paths: ProjectPaths, reference: JSONObject) -> JSONObject: ...
def read_source_file(paths: ProjectPaths, reference: JSONObject,
    file: JSONObject, *, max_bytes: int = 67108864) -> bytes: ...
```

- inventory_source: absolute quiescent ordinary directory and validated limits. Return sorted regular file and archive-member inventories with identities; stream hashing/bound checks, no extraction/writes. Unsafe/malformed archives, limits exceeded or changing sources raise ValueError/OSError. Special files/links never followed.
- read_inspection_file: absolute inspection root, previously inventoried file/member and positive max_bytes. Verify file/container/member identity and return exact bounded bytes. No writes. Reject oversized, changed, ambiguous or unsafe sources with ValueError/OSError.
- iter_source_records: project paths; yield validated native terminal metadata projections and published import records in deterministic identity order. Each item is {reference,record,issues}; an invalid individual record yields record=null plus issues, not silent disappearance. Root I/O/invalid project layout raises; existing readers can create coordination files. Never yields staging or unfinished native records as matches.
- read_source_record: sealed native/import reference; return normalized current record, verify document hash and input identity, preserve evidence basis. Native projection uses parsers plus saved effective JSON as described. No data writes except existing lock files; stale/missing evidence raises ValueError/OSError.
- read_source_file: sealed owner reference, one of its listed FileReferences and positive max_bytes (default 64 MiB). Verify exact ownership/containment/current identities before returning bytes; never load unrequested output files. Larger files require an explicit bound increase. No extraction/writes; integrity/size/schema failures raise ValueError, I/O raises OSError. File access never invokes scientific parsing.

### importing.py

```python
def get_import_profile(name: str) -> JSONObject: ...
def inspect_import(source: Path, *, profile: JSONObject | None = None,
    limits: JSONObject | None = None) -> JSONObject: ...
def import_collection(paths: ProjectPaths, config: Config, source: Path,
    inspection: JSONObject, *, label: str,
    collection_id: str | None = None) -> JSONObject: ...
def finalize_migration(paths: ProjectPaths, source: Path,
    import_report: JSONObject, refresh_report: JSONObject) -> JSONObject: ...
def _allocate_import_ids(paths: ProjectPaths, config: Config,
    collection_id: str | None, count: int) -> JSONObject: ...
def _publish_import(paths: ProjectPaths, operation: JSONObject,
    inspection: JSONObject, source: Path) -> JSONObject: ...
```

- get_import_profile: registered built-in name; return independent generic or NERSC profile with exact rules above, no effects; unknown name raises ValueError.
- inspect_import: absolute source, optional validated profile/limits; defaults generic and listed limits. Return Inspection with detected groups, issues, source identities and frozen profile/limits. Read source only; never allocate or publish. Content issues remain in report; unsafe source/inventory or invalid API args raise. Inspect CSVs sequentially and member inputs by archive stream to avoid repeatedly decompressing one archive per run.
- import_collection: configured project/time, same source and reviewed Inspection, nonblank label, optional existing collection ID. Verify inspection/source equality, safe collection profile compatibility and pending journal. Copy streamed source evidence, allocate/reserve IDs, publish supported nonconflicting groups, then incrementally index new/previously unindexed records after releasing import locks. Return ImportReport = {schema_version,collection_id,operation_id,collection_path,added,skipped,conflicts,pending,source_inventory,migration_ready,indexing,issues}. indexing is an UpdateReport separate from import status; indexing failure makes migration_ready false but does not undo imports. Each list holds external ID/internal reference/issue entries as applicable. No output interpretation/source deletion. Existing label is not silently renamed. Malformed/colliding inspection raises; per-run conflicts reported; import I/O/interruption propagates with journal/staging notes. Index failures are returned separately.
- finalize_migration: absolute original source plus complete matching import and refresh reports; limited to the approved NERSC source and exact 13-file inventory in this version. Reverify preservation, every imported reference and current catalog generation, then remove only verified original regular files and empty source directory as section 8 specifies. Return {schema_version,collection_id,destination,removed,retained,state}; state migrated or retained. Validation failures raise before removal; deletion errors return retained details and leave durable journal. No engine/index rebuild. Reports alone are not proof: re-read pinned destination/catalog evidence.
- For finalize_migration, the parameter named refresh_report accepts either a successful RefreshReport or successful UpdateReport covering all published collection records. The migration catalog check does not require an unnecessary full rebuild after successful incremental indexing.
- _allocate_import_ids: under imports lock, validate optional collection and nonnegative count, derive configured time, reserve collection/import IDs atomically in identities.json. Return {collection_id,import_ids,created_at}; no source reads or worker use. Existing reservation entries never reused. Partial I/O raises with retained paths; zero count allowed for collection-only initialization. Imported records retain reservation time across resume.
- _publish_import: staged operation/inspection/source under explicit expected manifest generation; implement stream copy, verify, per-run records and collection-manifest/journal publication. Return ImportReport; no source deletion/indexing. Called by import_collection only. Detect contention/source mutation; preserve earlier published records and staging, raising with notes on I/O/interruption. No worker locks or recursive lock acquisition.

### index.py

```python
def refresh_index(paths: ProjectPaths) -> JSONObject: ...
def update_index(paths: ProjectPaths,
    references: Sequence[JSONObject]) -> JSONObject: ...
def notify_simulation_finished(paths: ProjectPaths,
    metadata: JSONObject) -> None: ...
@contextmanager
def collect_index_updates(paths: ProjectPaths) -> Iterator[None]: ...
def _project_record(record: JSONObject) -> JSONObject: ...
```

- refresh_index: explicit catalog rebuild from iter_source_records, validate sources, stage database/manifest and publish under knowledge lock. Return RefreshReport = {schema_version,generation,indexed,rejected,issues,completed_at}; indexed is sorted sealed references, rejected references plus issue codes. Native running/created records are not indexed. Individual record errors reported; systemic I/O/schema publication failure raises retaining staging. No simulation/default/resource/source mutations or network calls.
- update_index: project paths and explicit sealed references; validate/deduplicate identities, verify only those records and upsert in a transaction as section 7 specifies. Return UpdateReport = {schema_version,operation_id,state,generation,attempted,indexed,skipped,rejected,issues,completed_at}; state succeeded/partial/failed; generation null when no valid catalog is available. Operational indexing failures return failed reports with best-effort durable diagnostics; invalid API arguments raise ValueError. No simulation/import-source changes or unrelated collection scanning. Empty reference list is a no-op and need not create a catalog.
- notify_simulation_finished: current terminal metadata from finish_simulation and project paths; ignore capture records, collect a sealed identity when in batch context, otherwise call update_index for just this record. Return None. Do not mutate metadata; contain ordinary indexing/reporting/logging exceptions. Only index after record publication and lock release. No native process or output parsing.
- collect_index_updates: context manager for one batch; yield None while collecting finalized identities in a thread-safe per-batch collector shared through copied contexts. On exit reset context and call update_index once for deduplicated collected references. Flush on exceptional exit without masking the original exception; ordinary indexing failures are separately logged/reported. Nested collectors for the same project reuse the enclosing collector and only its owner flushes. Cross-project nested contexts remain separate. No global default mutable collector. No new public class is required.
- _project_record: validated observed input record; return canonical query projection with observations/files and exact input-only fields. No I/O/effects. Unsupported/malformed schemas raise ValueError; it does not infer missing physics or parse output summaries.

### search.py

```python
def search_inputs(paths: ProjectPaths, filters: JSONObject | None = None, *,
    offset: int = 0, limit: int = 100,
    expected_generation: str | None = None) -> JSONObject: ...
```

- search_inputs: validated project/filter/page arguments; read current catalog under bounded lock, check generation, reject stale current records, apply matches_filters and return SearchResult. Both origins default; no query expansion, hidden import/refresh, model calls or persistent query records. Missing index raises FileNotFoundError; wrong generation/schema/invalid arguments raises ValueError; record-level staleness is excluded with explicit reasons. Coordination lock files may be created. Return independent JSON, bounded page but exact valid total.

## 11. Verification and delivery

Before coding, resolve any objections to this document and obtain explicit approval. Before the long implementation task, announce implementation start. Preserve other work and unrelated caches/residue. No AGENTS rules are waived.

Verify pure parser round trips against current SR/TRIM renderers without requiring lexical byte equivalence; externally prepared fixtures must test independent field expectations so tests do not only mirror renderers. Test CRCRLF, rounded parameters, different engine-family mode codes, repeated species, event correlation, unknown fields, malformed/truncated input, and metadata fallback/conflicts.

Verify filters: AND/OR, inclusive bounds, dimensional units, exact decimals, unknowns, isotope-aware and elemental composition, layer selectors/co-location, continuous SR ranges versus lists, pagination/generation and both origins by default. Output values must never become searchable inputs.

Verify imports: renamed/moved layouts through explicit profiles, metadata-only groups, output-only unknown records, archive safety, checksum mismatch, source mutation, duplicate external IDs, unchanged re-import, additive import, changed output/input conflict, interruption/resume, concurrent IDs and collection manifest changes. Test migration rejection before any removal, success allowlist, unexpected additions, partial deletion and idempotent repetition. Verify cross-kind source references can load the exact file bytes expected by existing parsers without invoking new analysis.

Verify incremental indexing touches only supplied identities, updates both native terminal states, initializes a missing catalog without a full scan, retries failed import indexing without duplicate IDs, and remains idempotent. Test standalone finalization, batch deferral/context propagation, partial batch failure, concurrent standalone runs and batches, no-op generations, transaction interruption and separate diagnostic failures. Force index lock/permission/storage errors and prove published simulation status/return values stay unchanged. Benchmark single-record update separately from full rebuild and 20k import indexing.

Use synthetic fixtures for destructive failure-path tests. On the real NERSC source perform read-only inspection first: all archive checksums, 20,000 unique manifest IDs, 200 targets, complete member inventories, case joins, input parsing and supplied member checksums. Previously sampled checks are not acceptance of all cases. Publication and final source removal occur only after implementation approval and successful verification; retain the original folder on any incomplete migration.

Measure cold/warm inspection, import, refresh and representative searches on 20k cases, report wall time/storage/peak-memory observations, and disclose archive sequential-access costs. No unmeasured latency guarantees. If performance requires a changed schema/caching/storage contract, obtain approval for that amendment before implementation.

Run all project Python using the exact approved executable:

```powershell
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' -W error -m unittest discover -s test/knowledge -p 'test_*.py'
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' -W error -m unittest discover -s test/backend -p 'test_*.py'
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' -W error -m unittest discover -s test/srim -p 'test_*.py'
```

Review required docstrings, complete typed signatures and absence of implementation comments. Verify document links, schemas and examples agree with the implemented APIs. Update user-facing guides only after checks pass. No rerun of native physics simulations is required by this import/search change.
