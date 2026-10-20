# CAB integration API catalog

Archived implementation contract and planned test inventory. For current signatures and behavior use the [API reference](../reference/api.md), [formats](../reference/data-formats.md) and [review](../reviews/non-trim-review.md). Approval-pending, baseline-test and future-tense statements below are historical. The final compatibility amendment supersedes earlier parser flexibility. Python 3.12 syntax is mentioned historically; the current runtime requires Python 3.13+ APIs. Native validation status is in the [CAB status page](../CAB_HANDOFF.md). Source/cache paths below are repository-root relative.

Current status: this design was explicitly approved and has been implemented and verified. See CAB_INTEGRATION_TRACKER.md for completion evidence. Approval-pending language below records the reviewed proposal, not the current implementation status.

This catalog supplements CAB_INTEGRATION_PROPOSAL.md. It specifies proposed implementation boundaries and test coverage for final review. It does not authorize implementation. Signatures use Python 3.12 type-alias syntax already used by the project; all declared functions/methods will have first-statement docstrings and fully annotated arguments/returns, including self. New implementation helpers are limited to this catalog; a necessary design change must be presented before implementation.

## Shared argument and error contracts

paths is the validated ProjectPaths for the workspace; config supplies the immutable configured SRIM root and time settings; created is an allocated empty main simulation; worker is an allocated worker. These objects are never modified. request/selections/documents and mappings are copied before editing. Sources are local Paths, content strings/bytes or explicitly supported Python/JSON maps; strings never implicitly mean paths. Sources and all ancestors must be regular/link-free as applicable. ResourceReference identifies immutable payload bytes; CABEntryReference additionally identifies one calculated entry.

Pure functions perform no I/O and raise ValueError for invalid types, schema, ranges, identity, duplicates or inconsistent arithmetic. Decimal arithmetic uses a local precision-28 half-even context except exact integer/rational comparisons. I/O functions additionally propagate OSError and lock contention; publication functions preserve identifiable partial artifacts. Calculation functions additionally propagate engine/capture failures with stage/entry/evidence context and propagate interruptions after owned-process cleanup. Do not retry operations unless a signature explicitly states lookup/reuse; bond threshold verification does not retry. No function sends messages, changes installation files, deletes historical versions or runs the main simulation except run_sr_simulation itself.

All generated JSON dictionaries use the shared JSONObject alias. Pure parsing returns fresh structures. JSON duplicate-field rejection precedes dictionary construction. All record-relative paths and file manifests use strict normalized path/hash/size validation. These shared rules apply to every signature below, rather than being repeated in each row.

## Backend resources

File src/backend/resources.py remains the generic immutable resource store; it has no SRIM imports. Existing ResourceReference fields and other unchanged helpers remain as currently implemented. New optional manifest fields are provenance and reuse_key; schema-version-one reads remain supported. New writes use numbered vN versions.

- `register_resource(paths: ProjectPaths, config: Config, category: str, name: str, version: str | None, source: Path | str | bytes, *, provenance: Mapping[str, object] | None = None, reuse_key: str | None = None) -> ResourceReference`

  Validate category/name, source and provenance; allocate/publish immutable bytes. None allocates vN; explicit new labels must be vN. A non-null reuse_key is a lowercase SHA-256 recipe identity: under the existing resource-root lock, reuse a matching verified registration or register exactly one if absent. Reject explicit version plus reuse_key, conflicting bytes for the same key, corrupt matches and malformed provenance. This makes concurrent automatic Bragg-version creation atomic. User explicit version creation passes no reuse_key.

- `resolve_resource(paths: ProjectPaths, category: str, name: str, version: str) -> ResourceReference`

  Resolve a concrete manifest without treating default as a mutable alias. Retain legacy concrete label reading. Validate supported manifest schema versions and return a concrete reference; unknown versions raise ValueError.

- `verify_resource(paths: ProjectPaths, reference: ResourceReference) -> Path`

  Verify manifest/reference and exact payload hash/size; return payload Path. No data mutation.

- `_validate_resource_manifest(document: dict[str, object]) -> None`

  Validate exact old/new manifest keys, timestamp, hash/size, optional provenance and reuse key; no writes.

- `_validate_registration_provenance(provenance: Mapping[str, object] | None) -> dict[str, object] | None`

  Return independent JSON-safe provenance or None; validate origin/source/base/change/baseline/procedure fields and concrete reference shape. No SRIM calculations or imports.


Keep _validate_identifier, _validate_reference, _resource_directory, _hash_file, _read_json and _write_json signatures unchanged. Add the following pure duplicate decoder in this backend module, which has no SRIM dependencies:

- `_unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]`

  Called by json.loads object_pairs_hook for every object, return its dictionary after rejecting duplicate keys with ValueError. This checks nested objects without a recursive custom parser. Strength decoding can additionally use parse_float=Decimal before normalizing numeric tokens. _read_json uses this hook. _write_json retains its synchronized atomic publication contract and now sorts object keys for canonical generated JSON; it never rewrites existing source/payload bytes merely to canonicalize them. Reuse registration's existing inline version/staging/publication logic while holding its one lock, not a nested call to register_resource.

New manifest schema is exactly schema_version=2, sha256, size_bytes, registered_at, provenance and reuse_key. provenance is null or an object with origin, source, base, changes, copied_entries, baseline_id and procedure. origin is installation/user/calculated. source has exactly kind, origin, sha256 and size_bytes; kind is file/inline/version/recipe, with origin respectively absolute path/inline marker/concrete reference/generated marker. base is a concrete reference or null, changes an operation list, copied_entries a list of pinned calculated-entry reference documents (empty for ordinary imports), baseline_id a hash or null, procedure a method/version document or null. Frozen versions copied from recovered defaults retain copied_entries so their original available subset remains identifiable after later cache growth. Fields required by generic registration may be null when not applicable; nested schemas still reject unknown keys. Backend validation checks reference/document structure without importing SRIM modules. Resource payloads remain exact native bytes or caller-generated canonical bytes.

## Default coordinator

File src/srim/defaults.py imports backend resource/config/path APIs, material validators and pure descriptor encoding. It never launches processes or performs recovery.

- `switch_sr_defaults(paths: ProjectPaths, config: Config) -> JSONObject`

  Read/hash/validate default installation, reuse/register appropriate versions, then atomically replace the coordinated pointer after rechecking its prior generation. Return verified concrete references/baseline. Same effective generation is idempotent. A compound-only change preserves numerical versions. Leave old pointer active on prepublication failure and report staged versions.

- `read_sr_defaults(paths: ProjectPaths) -> JSONObject`

  Read and validate one coordinated pointer plus its immutable descriptor references; return an independent document and its canonical pointer-file identity. Missing/uninitialized defaults raise ValueError. No registration or alias reassignment.

- `verify_sr_baseline(paths: ProjectPaths, config: Config, baseline: JSONObject) -> None`

  Validate supplied pinned recipe and compare required live SR executable/default file hashes. Mismatch raises ValueError; success returns None. No alias reread or writes.

- `_read_installation(config: Config) -> tuple[JSONObject, dict[str, bytes], JSONObject]`

  Validate absolute installation path, read required executable/version/material/coefficient bytes without links, recheck hashes; return a role manifest and independent raw contents. Include Data/VERSION as a capture input. Installed Compound.dat belongs to generation selection but not the numerical recovery fingerprint.

- `_make_baseline(files: Mapping[str, bytes]) -> JSONObject`

  Pure construction of fixed numerical input/procedure identity from required role bytes; record file hashes/sizes and recipe ID. Exclude installation path and installed compound content from the numerical hash.

- `_validate_default_generation(document: JSONObject) -> None`

  Validate exact pointer roles, baseline identity and concrete reference kinds. Pure structural validation.


Default pointer schema has schema_version=1, baseline_id and resources with exactly atomdata/compound/electronic/nuclear/core/bond/bragg concrete references. The pointer hash is calculated externally from its bytes, not embedded in itself. read_sr_defaults returns the generation document itself; the resolver records its canonical bytes/hash in a separate wrapper. Defaults pointer files are canonical generated JSON and read validation rejects noncanonical/tampered pointer bytes. baselines/<hash>.json stores the canonical numerical recipe; it cannot be overwritten. Resource imports use reuse_key to reuse identical installation data and derived recipes safely. Baseline provenance includes default Data/VERSION and generated capture-manifest/mock-header/procedure identities; raw installed compound content is excluded. Readers do not combine aliases from different reads. Before pointer publication, validate role origins and numerical dependencies against the one staged baseline, not merely independent valid references.

Initialize the known C_H=0 convention entry with each new default core descriptor, without engine execution. It is then available for lookup and user snapshots immediately; other core/bond and all Bragg entries remain lazy. Retain its fixed-convention provenance and baseline identity. This is static policy publication, not fitted recovery or an implicit hydrogen simulation.

## Shared procedure constants

New file src/srim/cab_constants.py has a module docstring and typed Final constants only, with no functions/classes/I/O. It contains CAB_UNITS, CAB_PRECISION=28, CAB_ROUNDING, BRAGG_PROCEDURE_VERSION, RECOVERY_PROCEDURE_VERSION, CAPTURE_PROCEDURE_VERSION, SR_BONDS (the exact18 canonical tuples in native column order), CAPTURE_MANIFEST and MOCK_HEADER byte constants pinned by the verified hashes. Construct procedure JSON afresh from constants; do not expose a shared mutable dictionary or Decimal context. defaults.py and cab_recovery.py import these constants, avoiding an import cycle while keeping one authoritative byte/procedure definition. Values are embedded in source during implementation, not read from .cache or an installation at runtime.

## Materials and search

File src/srim/material.py depends on JSON/Decimal/Fraction, standard-library tokenization, canonical CAB element/bond normalization and backend registration. It does not import runtime/execution/recovery.

- `parse_atomdata(contents: bytes) -> JSONObject`

  Validate the native 92-row table and return canonical fields, original lexical values and source row spans/hashes. Preserve two header lines and trailing source bytes. Reject missing/duplicate/out-of-order numbers, invalid fields and mismatched canonical symbols.

- `parse_compounds(contents: bytes) -> JSONObject`

  Detect native CP1252 versus UTF-8 extended JSON from document structure, validate once and return ordered canonical records plus source provenance. Do not infer a basis from numeric sums. Native leading % means mass; JSON composition_basis is explicit.

- `_parse_native_compounds(contents: bytes) -> JSONObject`

  Parse starred records, quoted internal names, density/count/pairs and the fixed 18 bond columns. Retain original display/internal names, comments, byte spans and line numbers. Blank lines are retained, not silently repaired. Reject malformed record structure.

- `_parse_json_compounds(contents: bytes) -> JSONObject`

  Strictly decode schema-version-one extended records, validate basis-specific element fields and bond maps and preserve array order/duplicate names. No native source assumptions.

- `apply_material_overrides(kind: Literal['atomdata', 'compound'], data: JSONObject, overrides: Sequence[JSONObject]) -> JSONObject`

  Copy data and apply permitted edits. Atomdata edits existing named fields without removals/identity changes. Compound edits target original indices, replace complete records/remove/append and return the original/effective mapping. Reject duplicate targets, unknown keys, invalid operation/basis and nonexistent removal.

- `_encode_atomdata(data: JSONObject) -> bytes`

  Pure native encoding: unchanged rows/header/footer retain bytes; replace only changed lexical fields in edited rows. Validate the complete table before encoding ASCII.

- `_encode_compounds(data: JSONObject) -> bytes`

  Pure effective extended JSON encoding with explicit composition_basis and source quantities, without converting mass values. Unmodified source bytes remain in provenance snapshots.

- `search_compounds(data: JSONObject, *, name: str | None = None, name_mode: Literal['exact', 'contains'] = 'exact', composition: Mapping[ElementKey, str] | Sequence[ElementKey] | None = None, composition_mode: Literal['contains', 'elements', 'ratio'] = 'contains', atomdata: JSONObject | None = None) -> list[JSONObject]`

  Validate filters and return independent matching effective records in order. Exact name is case-sensitive; contains uses Unicode casefold. Match against both retained display and internal names; native % is a basis marker, not part of the cleaned searchable internal name. Preserve raw names too. Composition filters AND with name. Ratio requires exact element set; mass candidates require effective atomdata and exact rational comparison. Empty results are valid.

- `_match_ratio(record: JSONObject, ratio: Mapping[int, str], atomdata: JSONObject | None) -> bool`

  Pure exact Fraction/cross-product comparison from original decimal strings and, for mass records, supplied natural masses. Return False on element-set/ratio mismatch; missing masses/invalid inputs raise ValueError. No rounded division.

- `_atomic_composition(record: JSONObject, atomdata: JSONObject) -> list[JSONObject]`

  Return atomic numbers/count strings: preserve atomic counts unchanged or divide mass proportions by effective natural masses in local Decimal context. Reject nonzero bonds on mass records and absent masses. Record original source basis/quantities separately through caller evidence.

- `construct_sr_target(target: JSONObject, atomdata: JSONObject, compounds: JSONObject | None) -> JSONObject`

  Resolve direct or effective indexed compound source, explicit phase, density and target masses/names. Return exactly target (numeric engine target), composition (atomic counts), bonds, and provenance. Reject missing source/required fields/record, unsupported target Z, duplicate target elements and incompatible mass/bond basis before process launch. No chemical plausibility or scale normalization.

- `create_material_variant(paths: ProjectPaths, config: Config, kind: Literal['atomdata', 'compound'], *, source: Path | str | bytes | JSONObject | None = None, base_version: str | None = None, overrides: Sequence[JSONObject] = ()) -> ResourceReference`

  Require exactly source or base_version, validate/import/apply and publish immutable version plus provenance. Native/JSON sources are kind-specific. Preserve native unchanged input bytes; edited compounds use effective extended JSON. Return concrete reference without changing defaults.


Common internal record form has record_index, name, composition_basis, density_g_cm3, elements, bonds and provenance. Atom element objects have atomic_number/stoichiometry; mass elements atomic_number/mass_proportion. Provenance has format, source hash/size, original index/display/internal names/line/span or null for supplied JSON, and effective-index mapping. Byte spans identify exact source snapshot ranges rather than putting Python bytes inside JSON. Validated native record count follows its encoded element count, 1..8; direct/extended SR targets follow engine limits up to 92 unique elements. Generic storage supports valid element identifiers through 118 but SR construction rejects unsupported target elements. Mass-based records reject any nonzero count regardless of selected strength. Element-only/name searches require no atomdata.

## Core and bond user resources

File src/srim/cab.py remains the canonical element/bond normalizer and user resource encoder. Remove register_cab_default. Keep _SYMBOLS as the existing identity table.

Aliases: CABKind = Literal['core','bond']; ElementKey = int | str; BondKey = str | tuple[ElementKey,ElementKey,str]; CABValue = int | float | str | Decimal; CABDictionary = Mapping[ElementKey | BondKey,CABValue]; CABSource = Path | str | bytes | CABDictionary; CABData = dict[int,str] | dict[tuple[int,int,str],str]. No custom mapping class is added.

- `create_cab_variant(paths: ProjectPaths, config: Config, kind: CABKind, *, source: CABSource | None = None, base_version: str | None = None, overrides: Sequence[JSONObject] = ()) -> ResourceReference`

  Require exactly source/base, snapshot available base entries, apply set/remove and register a new frozen user payload. No forced default recovery or alias changes. Return reference; errors retain staged evidence.

- `load_cab(paths: ProjectPaths, kind: CABKind, version: str) -> CABData`

  Pin default once if requested, verify concrete user payload or available calculated default entries and return fresh decimal-string mapping. Do not recover missing values.

- `_atomic_number(element: ElementKey) -> int`

  Existing pure symbol/int normalization, excluding bool and unsupported identifiers.

- `_normalize_entries(kind: CABKind, entries: CABDictionary) -> CABData`

  Validate finite nonnegative strengths, canonicalize keys, reject duplicates and preserve supplied decimal spelling. Floats use str(value), never implicit binary-to-Decimal expansion.

- `_decode_payload(kind: CABKind, payload: bytes) -> CABData`

  Decode strict old numeric/new decimal-string payloads, validating exact keys/units/kind and duplicates; no I/O.

- `_read_source(kind: CABKind, source: CABSource) -> CABData`

  Read/verify supplied local bytes or mapping then normalize. Reject changed files, malformed JSON and source types.

- `_encode_payload(kind: CABKind, data: CABData) -> bytes`

  Validate and encode new schema-version-two strength payload with units and canonical sorted entries, keeping numeric strings.

- `_apply_cab_overrides(kind: CABKind, data: CABData, overrides: Sequence[JSONObject]) -> tuple[CABData, list[str]]`

  Pure copy/edit, return effective mapping plus explicitly removed canonical IDs. Reject duplicate normalized operations/removal of absent entries. Removal list prevents default recovery of explicitly suppressed entries.


Electronic/nuclear variant functions retain their complete signatures and helpers from current code; remove only their independent default-registration functions. create_electronic_variant(paths: ProjectPaths, config: Config, *, source: Path | str | bytes | None = None, base_version: str | None = None, replacements: Mapping[int,str | bytes] | None = None) -> ResourceReference. create_nuclear_variant uses replacements: Mapping[tuple[int,int],str | bytes] | None. Their _replace_rows/_row_atomic_number/_row_pair signatures remain unchanged. Publish base/replacement provenance and reject reversed nuclear keys, omitted/mismatched complete rows and any removal operation at selection validation.

## Calculated descriptors and entries

File src/srim/cab_cache.py imports backend resource/path/record helpers and metadata JSON types. It does not execute engines or depend on resolution.

Frozen dataclass CABEntryReference has generated construction inputs dataset: ResourceReference, dependency_key: str, entry_id: str, sha256: str, size_bytes: int. Construction stores inputs without I/O; APIs validate them. It identifies one immutable entry under a concrete dataset. No folder hash or mutable alias is stored as entry identity.

Frozen dataclass CABEntryResult has generated construction inputs reference: CABEntryReference and calculated: bool. It reports the validated reference and whether this invocation performed the calculation, allowing correct cached/calculated progress even when another request publishes an identical result concurrently. Construction stores values without I/O; calculated is operation state, never part of the persistent entry identity. Resolve functions return this wrapper; bulk APIs return reference lists and use the wrapper for progress reporting.

- `find_cab_entry(paths: ProjectPaths, dataset: ResourceReference, dependency: JSONObject, entry_id: str) -> CABEntryReference | None`

  Validate concrete descriptor/dependency and return verified entry reference or None only for true absence. Corrupt entries/evidence raise errors.

- `load_cab_entry(paths: ProjectPaths, reference: CABEntryReference) -> JSONObject`

  Verify descriptor, entry bytes and permitted dependency/provenance structure; return fresh JSON. Missing published evidence is an error, not permission to treat corruption as a cache miss.

- `publish_cab_entry(paths: ProjectPaths, dataset: ResourceReference, entry: JSONObject) -> CABEntryReference`

  Validate successful provenance, stage/write/fsync then recheck under short version-directory lock. Reuse semantically identical result, otherwise raise conflict and retain both evidence sets. Never update existing entry bytes.

- `_dependency_key(dependency: JSONObject) -> str`

  Pure validated canonical-recipe SHA-256; dependency identity excludes timestamps, mutable paths and freshly allocated evidence IDs. Includes actual input hashes and procedure identity.

- `_entry_path(paths: ProjectPaths, dataset: ResourceReference, dependency_key: str, entry_id: str) -> Path`

  Validate category/kind/concrete version and safe identifiers, reject links/junctions and return derived path. No arbitrary caller path.

- `_validate_entry(document: JSONObject) -> None`

  Pure exact schema/range/provenance validation, including permitted DAG: Bragg references simulation evidence, bonds reference H Bragg/captures, cores reference elemental Bragg and 18 bonds/captures. No same-kind calculated cycles.

- `_entry_result(document: JSONObject) -> JSONObject`

  Pure semantic comparison projection containing identity/dependency/value/calculation inputs and results but excluding new evidence paths, timestamps and process IDs. Decimal-equal lexical forms compare equal; retain published lexical/evidence bytes.


Entry schema has exactly schema_version=1, kind, entry_id, value, units, dependency, simulations, captures and calculation. Bragg must have a positive value and verified successful simulation; core H zero has documented fixed-convention calculation with no capture; other core/bond values are nonnegative and require their expected evidence. Simulation references include ID, parsed result manifest and raw output manifest. Capture references include owned worker-relative manifest/raw-input/output hashes. Dependency contains file identities and exact lower-level entry references. No numeric-only Bragg publisher is exposed through selection/user APIs. A declared missing entry in an inactive default version is distinct from missing evidence for a published entry: both fail, without substituting a user fallback or regenerating evidence.

## Typed orchestration inputs

Add frozen dataclasses in src/srim/metadata.py so dependency direction remains acyclic. Their generated constructors store explicitly supplied fields without I/O; all producing/consuming APIs validate and defensively copy JSON.

- ResolvedSRInputs(request: JSONObject, selections: JSONObject, effective_request: JSONObject, resolved: JSONObject, snapshots: tuple[tuple[str,bytes],...], engine_files: tuple[tuple[str,bytes],...]). snapshots are safe input-record evidence paths/bytes. engine_files contains exact pinned SRModule.exe/VERSION/effective coefficient bytes for the five-file runtime excluding generated SR.IN. This avoids rereading a different engine after dependency resolution; it is not a historical installation archive.
- SRDependencyInputs(defaults: JSONObject, resources: JSONObject, atomdata: JSONObject, engine_files: tuple[tuple[str,bytes],...]). This is the pinned effective dependency bundle used by Bragg. resources holds resolved source/effective provenance, not manually supplied stopping values. defaults is the single request-generation snapshot; default core/bond recovery constructs a separate all-default bundle.

Functions do not assume frozen dataclasses make nested dictionaries immutable. A consumer validates the byte inventory/hash linkage and makes independent snapshots before publishing. Class docstrings will describe every construction field and their public attributes. Neither class introduces custom methods or hidden side effects.

## Bragg simulations

File src/srim/bragg.py depends on the typed input classes, material/cache helpers, backend allocation and low-level runtime/execution. Public runtime orchestration lazily imports resolution; dependency code does not recursively call public preparation.

- `resolve_bragg(paths: ProjectPaths, config: Config, worker: CreatedDirectory, inputs: SRDependencyInputs, atomic_number: int, *, timeout_seconds: float = 60.0) -> CABEntryResult`

  Validate pinned effective inputs/element/timeout, atomically find/create matching descriptor using registration reuse_key, reuse entry or allocate/run a correction-none dependency simulation, validate positive electronic stopping and publish entry. Return reference plus actual calculation state; retain failures/evidence.

- `populate_bragg(paths: ProjectPaths, config: Config, worker: CreatedDirectory, inputs: SRDependencyInputs, *, atomic_numbers: Sequence[int] | None = None, timeout_seconds: float = 60.0, progress: Callable[[JSONObject],None] | None = None) -> list[CABEntryReference]`

  None selects all 1..92 in numeric order; explicit subsets must be nonempty unique valid integers. Resolve each with same pinned dependencies and version, reporting after completion. Stop on first error/callback/cancellation; keep successes.

- `_make_bragg_inputs(inputs: SRDependencyInputs, atomic_number: int) -> ResolvedSRInputs`

  Pure validated construction of H/125M_H/gas/natural-mass/correction-one/units-7 numeric request and complete original/effective evidence for low-level preparation. No registration or execution.


Bragg descriptor identity binds the complete effective recipe, not a mix of recipe namespaces under one version. Reuse the matching concrete version across element requests; new dependency sets allocate a new vN. A descriptor's entry dependency_key equals its bound recipe identity. Its baseline_id is null: the actual Bragg recipe already contains its required SRModule/VERSION/ATOMDATA/coefficient/procedure hashes, and unrelated GUI recovery/compound inputs must not alter that identity. Core/bond descriptors instead bind their full default recovery baseline. Default coordinator references the matching default Bragg recipe version; custom recipes do not alter that alias. A GUI-only installation change can therefore create new default core/bond versions while retaining a Bragg version whose actual recipe is unchanged. Version numbers are independent per resource kind, not required to match across roles. Identical effective input bytes/procedure reuse values even if supplied through different file paths; each caller separately records its origin/override provenance.

## Default recovery

File src/srim/cab_recovery.py imports typed/default/cache/Bragg helpers, stdlib Windows automation support and backend locks. It owns generated native mocks and integrated GUI capture. No standalone CLI or copied script remains after verified migration.

- `resolve_default_bond(paths: ProjectPaths, config: Config, worker: CreatedDirectory, defaults: JSONObject, bond: BondKey, *, capture_timeout_seconds: float = 30.0, dependency_timeout_seconds: float = 60.0) -> CABEntryResult`

  Resolve supported SR bond against pinned all-default recipe, reuse cached entry, otherwise obtain H Bragg, capture/verify at most three positive counts, compute/publish strength. Return reference plus actual calculation state. Missing entry in inactive default version raises; unsupported valid types are handled as zero by resolver without calling this function.

- `resolve_default_core(paths: ProjectPaths, config: Config, worker: CreatedDirectory, defaults: JSONObject, atomic_number: int, *, capture_timeout_seconds: float = 30.0, dependency_timeout_seconds: float = 60.0) -> CABEntryResult`

  Reuse published value; H returns documented fixed zero entry. Otherwise obtain default elemental Bragg/all18 bonds outside capture lock, capture each one-bond estimate, validate and publish mean/spread/evidence. Return reference plus actual calculation state.

- `populate_default_cab(paths: ProjectPaths, config: Config, worker: CreatedDirectory, kind: CABKind, *, entries: Sequence[ElementKey | BondKey] | None = None, capture_timeout_seconds: float = 30.0, dependency_timeout_seconds: float = 60.0, progress: Callable[[JSONObject],None] | None = None) -> list[CABEntryReference]`

  Freeze defaults once, validate ordered requested set or choose all92/all18, call same resolve functions, report cached/calculated status and retain partial successes.

- `_capture_correction(runtime: Path, compound_name: str, output_path: Path, *, timeout_seconds: float = 30.0) -> str`

  Move existing UI behavior into integrated explicit-runtime function. Launch/clean up only owned SR/PowerShell processes; preserve raw zero readings and exact correction text. Failure/cleanup exceptions retain evidence; no global cwd change.

- `_prepare_capture_runtime(paths: ProjectPaths, config: Config, worker: CreatedDirectory, defaults: JSONObject, compound: bytes) -> Path`

  Stage the verified eight-file native inventory in a new owned worker directory; hash inputs and recheck pinned source baseline. Return Path; no installed changes or historical runtime archive.

- `_capture_mock(paths: ProjectPaths, config: Config, worker: CreatedDirectory, defaults: JSONObject, atomic_number: int, bond: BondKey, count: int, *, timeout_seconds: float) -> JSONObject`

  Hold worker execution lock for one GUI capture, generate initialized SR.IN/mock dictionary, run integrated capture, preserve diagnostics and completed/failed manifest, recheck inputs. Return exact result/evidence JSON. No nested Bragg execution.

- `_render_mock_compound(atomic_number: int, bond: BondKey, count: int) -> bytes`

  Pure fixed validated native header/category, *Custom name, one atom, density1.0, 18-column vector with chosen count, terminal separator and CRLF encoding. No bond inference/valence checks.

- `_estimate_bond_count(correction: str) -> int`

  Require positive finite captured decimal; compute max(1,ceil(1/f)) using exact integer/rational ceiling, not rounded integer approximation. No search or retry.

- `_recover_bond_value(correction: str, hydrogen_stopping: str, count: int) -> str`

  Pure validated local-Decimal f*S_H/count result. Reject invalid/negative/nonfinite values; count must be positive integer.

- `_recover_core_value(captures: Sequence[JSONObject], elemental_stopping: str, bond_values: Mapping[str,str]) -> JSONObject`

  Require all18 exact supported identities; compute/validate each f*S_E-B, return estimates/mean/min/max/spread as decimal strings. No arbitrary tolerance/clamping.


The generated manifest bytes are pinned by the verified fixture hash in the main proposal. The mock header is the exact 2089-byte prefix before *Custom in .cache/mock-zero-h-20261019/zero_h_5_hc.Compound.dat, SHA-256 d40af9963517683c78ad2772e7160ddc54b2e6af11a055505138962408447b97. Embed those verified header bytes in the recovery module and copy them to test/srim/fixtures/cab-mock-header.dat during approved implementation; do not read .cache at runtime. The generated SR.IN uses render_sr_input on a correction-none H reference request, one requested target element at default natural mass, density1.0, solid phase for GUI initialization, units7 and list energy125M_H. This initialization is not the gas Bragg reference simulation; the chosen mock dictionary entry supplies capture composition/bonds. Counts use their full positive decimal integer spelling; reject serialization failure before launch, and propagate native timeout/error rather than silently limit or adjust counts. f(n)/f(n-1) threshold failure always raises. Raw numeric zero can be captured as evidence but never accepted as the initial estimate. Cleanup remains bounded separately from the 30-second operation deadline.

## Resolution and request validation

File src/srim/resolution.py applies selections/edits and orchestrates dependencies. It does not own main runtime execution.

- `resolve_sr_inputs(paths: ProjectPaths, config: Config, worker: CreatedDirectory, request: JSONObject, selections: JSONObject, *, capture_timeout_seconds: float = 30.0, dependency_timeout_seconds: float = 60.0, progress: Callable[[JSONObject],None] | None = None) -> ResolvedSRInputs`

  Validate all intent, pin one generation and engine bytes, resolve/source/snapshot roles and validate the effective target before publishing requested explicit versions. Then determine effective bonds, resolve only needed dependencies and compute correction/evidence. Return separated JSON/bytes bundle. Dependency failures may leave registered explicit versions and evidence but never run the main process.

- `verify_sr_resolution(paths: ProjectPaths, request: JSONObject, selections: JSONObject, resolved: JSONObject, snapshots: Mapping[str,bytes]) -> JSONObject`

  Reconstruct effective sources/edits and check immutable entries/reference linkage and arithmetic without processes/live alias resolution. Return validated effective numeric request. Missing/tampered evidence raises rather than silently regenerates.

- `_resolve_selection(paths: ProjectPaths, config: Config, role: str, selection: JSONObject, defaults: JSONObject) -> tuple[JSONObject, dict[str,bytes]]`

  Validate role-specific version/source/patch modes, snapshot source/effective bytes and return pending resolved role/evidence bytes. No registration or default recovery. Unknown/disallowed source/operations fail. Recovered-default base views pin the available entry references used for editing/freezing rather than pretending an expanding folder is one immutable payload.

- `_publish_effective_selection(paths: ProjectPaths, config: Config, role: str, selection: JSONObject, resolved_role: JSONObject, snapshots: Mapping[str,bytes]) -> JSONObject`

  After complete source/target validation, use the already snapshotted effective bytes to register a requested new version and return an updated independent role document. A false create_version flag returns the unchanged effective local selection. Never reread the original file path or alter aliases. Retain any published version on a later dependency failure, with its reference reported.

- `_make_dependency_inputs(defaults: JSONObject, resources: JSONObject, atomdata: JSONObject, engine_files: Mapping[str,bytes]) -> SRDependencyInputs`

  Pure validation/copy to typed pinned input bundle. Does not reread aliases or files.

- `_calculate_cab(composition: Sequence[JSONObject], bonds: Sequence[JSONObject], cores: Mapping[int,str], strengths: Mapping[str,str], stopping: Mapping[int,str]) -> JSONObject`

  Pure finite/range-checked weighted sums and factor under recorded context. Return contribution totals/factor; explicit no-effective-bond path returns1.0 without requiring missing unused core/stopping maps.


File src/srim/inputs.py keeps rendering pure and separates unresolved intent from numeric engine input.

- `validate_sr_request(request: JSONObject) -> None`

  Validate version-two unresolved intent and legacy version-one intent; never resolve dependencies or trust derived applied_factor in unresolved version-two mode. Validate target basis/source and correction fields.

- `validate_sr_engine_request(request: JSONObject) -> None`

  Validate fully numeric engine request, per-row names/masses/stoichiometry, range/list limits and correction factor. New effective derived correction has exactly mode/applied_factor; full derived provenance is validated separately in resolved.cab. Reject unresolved target-source objects.

- `render_sr_input(request: JSONObject) -> bytes`

  Call engine validation, emit deterministic ASCII/CRLF numeric SR.IN preserving target/list order and duplicate requested energies. No I/O.

- `_positive_decimal(value: JSONValue, field: str) -> Decimal`

  Preserve existing validation/error purpose; support finite engine numeric spelling after exact fixed-point conversion where necessary. No rounding by construction.


Version-two direct derived targets require explicit bonds; none/supplied do not calculate CAB and may omit bond information. Any supplied bond information still receives syntax/range validation. Stoichiometry remains required to run SR. User supplied projectile mass remains explicit and is not silently changed by target mass overrides. Required ATOMDATA roles still supply target and reference masses as documented.

## Metadata and main runtime

File src/srim/metadata.py adds the typed classes above, new strict schema branches and nested evidence input handling while retaining existing lifecycle functions. Preserve version-one validation and immutable input inventories.

- `_validate_document(document: JSONObject, document_type: str) -> None`

  Extend exact structural validators for new metadata/resolved/role/entry/default documents; no physical calculations or engine runs. Retain current legacy branches.

- `_validate_input_files(input_files: Mapping[str,bytes] | None) -> dict[str,bytes]`

  Return copied safe nested evidence names/bytes or empty map; reject reserved/duplicate/traversal paths, unknown roots and nonbytes. No writes.

- `_collect_resource_references(resolved: JSONObject) -> list[JSONObject]`

  Pure deterministic ordinary-reference collection/dedup across role/base/published/calculated-entry dependencies, branching legacy/new schema. Validate reference shape.

- `initialize_simulation(paths: ProjectPaths, created: CreatedDirectory, engine: JSONObject, request: JSONObject, resolved: JSONObject, continuation: JSONObject | None = None, *, engine_inputs: Mapping[str,bytes] | None = None, input_files: Mapping[str,bytes] | None = None) -> JSONObject`

  Validate empty allocation, schemas and all references; publish original/effective evidence and inventory then metadata last. Safe nested snapshots only; failure retains artifacts.

- `read_simulation(paths: ProjectPaths, simulation_id: str) -> JSONObject`

  Retain current signature/status behavior; accept exact version-two schema in addition to legacy. Reading historical metadata alone does not require a live installation.

- `start_simulation(paths: ProjectPaths, config: Config, simulation_id: str) -> JSONObject`

  Existing guarded created-to-running transition; verify/input sealing semantics retained for version-two records.

- `finish_simulation(paths: ProjectPaths, config: Config, simulation_id: str, failure: JSONObject | None = None) -> JSONObject`

  Existing terminal transition and evidence inventory semantics; support new record schema without weakening state checks.


Other metadata helpers retain current signatures/behavior. Expanded metadata schema has exactly the existing metadata fields plus entry_references and defaults_generation for version2; resources remains concrete ordinary references. entry_references contains exact calculated-entry references. defaults_generation is the pinned pointer snapshot/hash or null for legacy-compatible low-level cases. Request/selection/effective snapshots are in the inventoried input tree, not injected as mutable extra metadata fields.

File src/srim/runtime.py retains its safe byte reader/five-file manifest helpers. _read_selected_resource keeps its current signature for legacy uses; the new path consumes verified effective bytes instead of pretending every local input is an unchanged registered payload.

- `prepare_sr_simulation(paths: ProjectPaths, config: Config, created: CreatedDirectory, worker: CreatedDirectory, request: JSONObject, selections: JSONObject, *, capture_timeout_seconds: float = 30.0, dependency_timeout_seconds: float = 60.0, progress: Callable[[JSONObject],None] | None = None) -> Path`

  Validate allocations before side effects, call resolution before main worker lock, then prepare effective main runtime. Return prepared Path, status created; never execute main process.

- `_validate_preparation_allocations(paths: ProjectPaths, created: CreatedDirectory, worker: CreatedDirectory) -> str`

  Validate allocation identity/empty main record/worker layout without creating artifacts; return simulation ID. No engine work.

- `_prepare_sr_runtime(paths: ProjectPaths, config: Config, created: CreatedDirectory, worker: CreatedDirectory, inputs: ResolvedSRInputs) -> Path`

  Validate typed pinned bytes, recheck required live installation identity, stage exact five-file runtime and initialize snapshots/metadata under existing lock. Use inputs.engine_files, not newly chosen executable bytes. Return Path, retain errors/artifacts as today.


File src/srim/execution.py keeps verify_sr_runtime(paths: ProjectPaths, simulation_id: str, runtime: Path) -> JSONObject and run_sr_simulation(paths: ProjectPaths, config: Config, simulation_id: str, runtime: Path, *, timeout_seconds: float = 60.0) -> JSONObject. Update preflight linkage for version2 and pass verified effective input to parsing. Keep _preserve_sr_output(runtime: Path, record: Path) -> bytes | None unchanged. Do not change existing expected-failure return versus caller/state/filesystem exception ownership contracts.

File src/srim/outputs.py keeps parse_sr_output(contents: bytes, request: JSONObject) -> JSONObject and every existing parser helper signature. Change only its validation dependency to validate_sr_engine_request; preserve raw numeric strings, precision checks, unit/range/list handling and raw hashes.

Config/paths/records implementation files do not change. Remove src/srim/capture_correction.py only after integrated capture validation and reference checks. No new package dependency, service, scheduler, UI or TRIM/TIN implementation.

## Exact nested data shapes

All objects below reject unlisted keys; nullable fields must be explicit null when not applicable. Arrays retain the stated order and reject duplicate identities. Integer fields exclude bool. Decimal fields are finite strings with their prescribed sign/range. SHA-256 fields are lowercase64-character hex. Version labels on new persistent references are concrete vN. These shapes are shared by validators, encoders and reconstruction tests.

- OrdinaryReference: category, name, version, sha256, size_bytes, as existing ResourceReference. RecordManifest: path, sha256, size_bytes, relative to the owning simulation record. EntryReference: dataset (OrdinaryReference), dependency_key, entry_id, sha256, size_bytes.
- FileEvidence: kind (simulation or worker), id (validated simulation/worker directory ID), path (normalized relative path under that owner), sha256, size_bytes. Resolve owners only through ProjectPaths; reject traversal/links. A missing published evidence file raises, never silently regenerates.
- SimulationEvidence: simulation_id, result (FileEvidence), raw (FileEvidence). Both files must belong to that simulation, with successful metadata/result linkage. CaptureEvidence: manifest, correction, inputs, stdout, stderr; values are FileEvidence except inputs is a list. Owner IDs and paths must identify the same owned capture runtime. Capture manifests have schema_version, status, inputs_before, inputs_after, correction, process_id, started_at, finished_at, cleanup, error. Successful status requires identical input hashes, a raw numeric reading and completed cleanup; failed manifests keep error/evidence and cannot publish usable entries.
- Procedure: method, version, precision, rounding, units, reference. Precision is28; rounding is ROUND_HALF_EVEN; units is eV/(1E15 atoms/cm2). reference has projectile=1, energy_per_amu_keV=125 as a string, phase=gas, stopping_units=7, projectile_mass_field=isotope_mass_amu and target_mass_field=natural_mass_amu. Method identifies Bragg/default bond/default core/fixed H convention; version constants are1 initially. This is fixed procedure identity, not a user-selectable configuration.
- FileIdentity: role, sha256, size_bytes. DependencyRecipe: schema_version=1, kind, procedure, files (FileIdentity list), entries (EntryReference list). Raw identities use effective bytes; omit source paths, aliases, timestamps and IDs of newly allocated runs. Core/bond recipes include fixed generated header/manifest identities and needed default runtime files. Their calculated entries add actual lower-level entry references to the descriptor's fixed recipe. Bragg entries use their descriptor's same recipe. Entry ID supplies the chosen element/bond; mock count/capture results are evidence, not inputs needed for pre-capture cache lookup.
- CalculatedDescriptor: schema_version=1, kind, origin, baseline_id, procedure, units, base, dependency. For default core/bond origin is installation and baseline_id is their fixed recovery fingerprint. For Bragg origin is calculated and baseline_id null, with complete actual input identity in dependency. base is null for initial computed recipes. No user numeric values appear in a calculated descriptor.
- CalculatedEntry: schema_version=1, kind, entry_id, value, units, dependency, simulations, captures, calculation. For Bragg, calculation has method, version, precision, rounding, value_field=electronic_stopping, row_index=1, energy_keV, projectile_mass_amu, target_mass_amu, phase=gas, density_g_cm3 and stopping_units=7. The value must equal that referenced parsed row's electronic output. For a bond, calculation has method, version, precision, rounding, initial_correction, estimated_count, captures_by_count, hydrogen_stopping, value and threshold_verified=true; captures_by_count elements have count, capture_index and correction. For non-H cores, calculation has method, version, precision, rounding, elemental_stopping, estimates, mean, minimum, maximum and spread; each of the18 estimates has bond, correction, bond_strength, value and capture_index. Fixed H convention calculation has method, version, precision, rounding, convention=hydrogen-core-zero and value=0 as a string. All capture indices address the entry's captures list; duplicate/reused counts reference the same capture. Typed validation enforces the expected dependency graph and arithmetic.
- BaselineRecipe: schema_version=1, files, procedures and generated. files contains exact numerical installation FileIdentity objects for SRModule.exe, module VERSION, SR.exe, Data/VERSION, ATOMDATA and electronic/nuclear data. procedures has bragg, recovery and capture procedure documents; generated has manifest and mock_header FileIdentity objects. Exclude installed compound dictionary/path. The immutable baseline file contains this object, not a hash of itself; its exact canonical byte hash is baseline_id. DefaultGeneration: schema_version=1, baseline_id and resources (exactly seven concrete references for atomdata, compound, electronic, nuclear, core, bond, bragg). Different resource kinds may have different vN numbers. RecordedGeneration: generation (DefaultGeneration), sha256, size_bytes of its canonical pointer bytes. The record contains this value and never consults a later default pointer to interpret it.
- ResolvedRole: reference, base, source, overrides, effective, published. reference/base/published are ordinary references or null as appropriate. source has kind, origin, snapshot. kind is version/file/inline; origin is the concrete original reference, original absolute file path, or literal inline marker respectively; snapshot is RecordManifest for preserved original bytes. For a recovered-default base, source.snapshot instead contains a canonical base view with exactly schema_version=1, dataset, entries and values: the immutable descriptor reference, pinned available entry references and decimal-string value payload. Verify its entries against that descriptor; do not claim this view hash equals the descriptor payload hash. effective is RecordManifest for effective bytes. For frozen user data these are actual effective payload bytes; for a locally edited recovered default it is a canonical overlay with schema_version=1, dataset and overrides, while used calculated values are pinned individually in CAB contributions. This prevents unrelated future cache additions from changing replay. Explicit new-version creation materializes only the available entries and publishes a frozen user payload.
- MaterialSource: format (native/json), encoding (ascii/cp1252/utf-8 as appropriate), text, sha256, size_bytes. Material record provenance retains original index, raw display/internal names, cleaned display/internal names, source line and source span, using explicit null for unavailable JSON-source positions. A span has offset, size_bytes and sha256 over original source bytes. Effective index map entries have original_index and effective_index; a removal uses effective_index null, an append original_index null. Store original source text/bytes in the preserved source snapshot, never silently rewrite it as a converted composition.
- Parsed ATOMDATA: schema_version=1, rows, source. Rows contain the12 native fields listed earlier plus provenance (source line/span). Preserve original lexical numeric values separately through source spans; edited fields contain supplied lexical strings. Parsed compounds: schema_version=1, records, source, index_map. Each record contains record_index, name, composition_basis, density_g_cm3, elements, bonds and provenance. Extended external serialization writes only approved external record fields, not internal spans/index_map. Store original/effective mappings in edit/publication/simulation provenance.
- TargetProvenance: source (direct/compound), record_index, original_record_index, record_name, composition_basis, original_elements, atomic_composition, masses, record_sha256, mapping. Direct-only record fields are null and mapping empty. masses lists atomic_number/mass_amu used in construction. Unchanged native record_sha256 identifies original span bytes; edited/extended records identify their canonical effective record. Original and effective identities remain separately available in snapshots/index mapping.
- Contribution: entry_id, value, origin, entry_reference, snapshot, override_index. origin is dataset/override/recovered-default/bragg-fallback/zero-fallback/convention. entry_reference pins a calculated value or, for bragg-fallback core, its Bragg entry. User/override values instead point to effective RecordManifest and optional zero-based override_index. Unused reference fields are null. Values are decimal strings, not copied unlinked numbers.
- Derived CAB: mode=derived, applied_factor, core_entries, bond_entries, bragg_entries, composition, bonds, omissions, reference and calculation. Core/bond entries are Contribution lists; Bragg entries are EntryReference lists. composition lists atomic_number/atomic_mass/atom_count. bonds lists identifier/count. omissions lists dataset, entry_id and reason (missing-user-entry/removed-entry/unsupported-default-bond/zero-count). reference records projectile, mass_amu, energy_keV, phase and contribution_units for the main denominator recipe. calculation has method, version, precision, rounding, path, intermediate_totals and calculated_factor. path is weighted-sum/no-effective-bonds, intermediate_totals has exactly core/bond/bragg decimal strings, and calculated_factor equals applied_factor. Fixed short circuit uses1.0 and zero totals without invented simulated entries. mode none/supplied retains old exact schemas.
- ResolvedRecord version2: schema_version, parameters, sources, resources, cab, defaults, target_provenance, dependencies. parameters is numeric engine input. sources is the executable/VERSION record-source list with existing id/name/version/sha256/citation fields. resources maps applicable roles to ResolvedRole; dependencies lists SimulationEvidence/CaptureEvidence. defaults is RecordedGeneration. Metadata version2 has the existing metadata fields plus entry_references and defaults_generation, with entry_references a deterministic unique EntryReference list and defaults_generation RecordedGeneration. Legacy branches retain their previous exact key sets.

The numeric engine-request envelope retains schema_version/parameters, has fully numeric projectile/target/unit/energy fields and correction none/supplied or derived with applied_factor. Unresolved version-two target source selectors and derived-without-factor are never accepted by engine rendering/parsing. Source intent and effective input are linked through reconstruction, not dictionary equality. Main execution never re-resolves aliases or reruns calculations during verification.

## Test file and method catalog

All new test classes inherit unittest.TestCase's constructor; no custom construction methods. Each setUp creates a temporary project and synthetic installation through the fixture helper and registers cleanup. Each method below has the complete signature `(self: ClassName) -> None`; it takes only its fixture-bearing instance, returns nothing, writes only its temporary project, and raises unittest assertion failures or expected-test exceptions. Every method has a docstring explaining self's relevant fixture/input and expected behavior. Existing unrelated test methods remain unchanged; existing methods referencing removed defaults/preparation APIs are updated in place with their current annotated signatures.

New file test/srim/_fixtures.py contains only these declared helpers, all with docstrings and annotations:

- `make_test_installation(root: Path) -> Config`: create synthetic executables/VERSION, all92 valid atomic/electronic rows, all4278 nuclear rows and a small native/extended test dictionary under root; return Config. No installed SR dependency.
- `make_test_project(root: Path) -> tuple[ProjectPaths,Config,CreatedDirectory]`: create base directories and synthetic installation, initialize coordinated defaults, allocate one worker and return fixtures. Propagate file/setup errors.
- `make_test_request() -> JSONObject`: return independent H/126keV version-two derived direct-target intent with explicit counts/bonds.
- `make_test_selections() -> JSONObject`: return independent five-default role selectors plus compound null, each with explicit empty overrides/create_version false.
- `fake_bragg_run(args: Sequence[str], *, cwd: Path, stdin: int, stdout: BinaryIO, stderr: BinaryIO, timeout: float, check: bool, creationflags: int) -> subprocess.CompletedProcess[bytes]`: assert owned single-element SR invocation/timeout/diagnostic handles, write a controlled one-row126keV output and return success. Do not implement a fake physical stopping model or mirror production derivation. Per-test failures use Mock/side_effect objects, not undocumented helper functions.

| New test file and class | Fully typed method names and coverage |
| --- | --- |
| test/srim/test_material.py, TestSRMaterial | `setUp(self: TestSRMaterial) -> None`; `test_atomdata_fields(self: TestSRMaterial) -> None` covers native preservation, field edits and forbidden removals; `test_compound_formats(self: TestSRMaterial) -> None` covers native/extended bases and original spans; `test_compound_patch_indices(self: TestSRMaterial) -> None` covers original-key edits/append/effective mapping; `test_name_and_element_search(self: TestSRMaterial) -> None` covers duplicates/order/AND/no-match; `test_exact_atomic_ratio_search(self: TestSRMaterial) -> None` covers exact atomic/mass ratios and selected masses; `test_target_construction(self: TestSRMaterial) -> None` covers direct/record/mass conversion, fractions, missing data and nonzero mass bonds. |
| test/srim/test_defaults.py, TestSRDefaults | `setUp(self: TestSRDefaults) -> None`; `test_coordinated_switch(self: TestSRDefaults) -> None` checks aliases/pinned references and changed install; `test_compound_only_switch(self: TestSRDefaults) -> None` checks numerical version reuse; `test_failed_switch_keeps_generation(self: TestSRDefaults) -> None` injects staging/publication failure; `test_identical_installation_reuse(self: TestSRDefaults) -> None` checks content identity rather than path alone. |
| test/srim/test_cab_cache.py, TestCABEntries | `setUp(self: TestCABEntries) -> None`; `test_immutable_entry_publication(self: TestCABEntries) -> None` checks later entries do not change references; `test_duplicate_and_conflict(self: TestCABEntries) -> None` checks semantic equality versus changed values/evidence retention; `test_tamper_and_missing_evidence(self: TestCABEntries) -> None` checks integrity errors are not cache misses; `test_publication_lock(self: TestCABEntries) -> None` checks contention/staging retention; `test_dependency_recipe_identity(self: TestCABEntries) -> None` checks exact effective-byte/procedure matching. |
| test/srim/test_bragg.py, TestBragg | `setUp(self: TestBragg) -> None`; `test_reference_simulation(self: TestBragg) -> None` verifies gas/H125M/natural mass/units7/electronic output and simulation evidence; `test_automatic_versions(self: TestBragg) -> None` checks same/changed/local effective inputs and race reuse; `test_bulk_reuse_progress(self: TestBragg) -> None` checks all92 ordered entries without real engines; `test_dependency_failure(self: TestBragg) -> None` checks failure/cancellation/invalid output with no publication. |
| test/srim/test_cab_recovery.py, TestCABRecovery | `setUp(self: TestCABRecovery) -> None`; `test_bond_threshold_recovery(self: TestCABRecovery) -> None` checks n1/count reuse/formula and no retry on failure; `test_zero_initial_capture(self: TestCABRecovery) -> None` retains zero evidence and raises; `test_core_estimates(self: TestCABRecovery) -> None` checks all18/mean/spread/negative rejection/Hzero; `test_capture_ownership_cleanup(self: TestCABRecovery) -> None` mocks owned Windows process/PowerShell cleanup including interruption; `test_bulk_resume_and_callback(self: TestCABRecovery) -> None` checks all92/all18 and retained partial success/callback errors. |
| test/srim/test_resolution.py, TestSRResolution | `setUp(self: TestSRResolution) -> None`; `test_sources_and_version_creation(self: TestSRResolution) -> None` checks local/inline/registered sources and explicit frozen variants; `test_override_fallbacks(self: TestSRResolution) -> None` distinguishes supplied missing/default removed/unchanged inherited entries; `test_no_effective_bonds(self: TestSRResolution) -> None` checks1.0 without unnecessary recovery; `test_old_default_missing_entry(self: TestSRResolution) -> None` rejects old missing recovery; `test_pinned_defaults(self: TestSRResolution) -> None` prevents mid-request source switching; `test_resolution_reconstruction(self: TestSRResolution) -> None` checks saved intent/effective numeric linkage and tamper rejection without reruns. |

Existing test/backend/test_resources.py adds/updates coverage for manifest2/reuse_key atomic dedup, preserved immutability and numbered registrations. Existing test/srim/test_cab.py covers decimal-string/legacy numeric payloads/removals/user fallbacks and removal of supplied default registration. Existing electronic/nuclear tests use coordinated defaults or explicit user variants and enforce complete rows/ordered pairs. Existing inputs/metadata/runtime tests cover new intent/effective split and preserve their legacy rendering/parsing/state/failure checks. No tests are added for documentation edits alone.

Live fixtures are not silently imported into automated tests. Representative live evidence stays under independently named ignored .cache directories, with hashes and paths in the final handoff. Engine-free tests use deterministic synthetic data and controlled subprocess mocks; live probes establish compatibility with configured installed resources separately.

### Existing affected test method identities

Existing test method signatures remain `(self: ClassName) -> None` except the existing TestSRRuntime._fake_sr_run helper below. Their constructor remains unittest.TestCase's inherited constructor. Keep existing test intentions, changing setup/expectations only where required by the accepted API/version/Decimal changes. Additional scenarios can be added to these methods without undeclared nested helper functions.

- TestResources in test/backend/test_resources.py: test_registration_versions_and_categories; test_identifier_and_reference_validation; test_corruption_and_reference_mismatch; test_publication_failure_and_retry; test_missing_setup_links_and_contention. Registration/version cases add reuse_key atomic identity and new manifest checks; corruption/publication/links cases preserve their respective failure guarantees.
- TestCAB in test/srim/test_cab.py: test_default_registration; test_source_forms; test_normalization_and_duplicates; test_variant_updates; test_invalid_inputs; test_load_integrity_and_independence; test_files_contention_and_publication. The first tests coordinated concrete default lookup rather than the removed supplied-default function; remaining tests update numeric-string/user-patch contracts while retaining their named purpose.
- TestElectronic and TestNuclear in their existing files: test_default_registration; test_file_and_byte_variants; test_row_replacements; test_invalid_inputs; test_missing_corrupt_and_linked_sources; test_contention_and_failed_publication. Setup uses coordinated defaults or explicit numbered variants. Each self annotation uses its respective class. Default-registration tests assert coordinated references; row tests retain complete-table/ordered-pair behavior.
- TestSRInputs in test/srim/test_inputs.py: setUp; test_range_serialization; test_list_serialization; test_request_validation; test_target_and_correction_rules. Retain legacy numeric fixtures and add explicit unresolved/effective validation separation.
- TestMetadata in test/srim/test_metadata.py: setUp; test_initialize_read_and_independent_snapshots; test_success_and_terminal_immutability; test_failure_before_and_after_start; test_input_integrity_and_resource_verification; test_cab_shared_datasets_and_overrides; test_transformed_resource_provenance; test_continuation_new_record_and_deleted_parent; test_schema_identity_and_path_validation; test_atomic_failure_and_partial_initialization; test_prepared_engine_inputs; test_links_and_lock_contention. Keep legacy branches while adding precise new schemas/reference collection/nested snapshot handling.
- TestSRRuntime in test/srim/test_runtime.py: setUp; test_prepared_layout_and_provenance; test_resource_integrity; test_invalid_inputs_and_collisions; test_links_and_contention; test_publication_failures; test_independent_runtime_directories; test_parse_sr_range; test_parse_sr_list; test_parse_sr_units; test_parse_sr_invalid_output; test_verify_sr_runtime; test_run_sr_success; test_run_sr_preflight_failure; test_run_sr_process_failures; test_run_sr_output_failures; test_run_sr_preservation_failures; test_run_sr_state_and_contention; test_run_sr_interruption. Use new selections/effective evidence for preparation while retaining parser fixtures and execution failure/ownership assertions.

Existing helper retains the complete signature `_fake_sr_run(self: TestSRRuntime, args: list[str], *, cwd: Path, stdin: int, stdout: BinaryIO, stderr: BinaryIO, timeout: float, check: bool, creationflags: int) -> subprocess.CompletedProcess[bytes]`. It uses self's controlled output/error/mutation fixtures, asserts owned execution/state/locks and writes diagnostic/output bytes only to its temporary runtime. It is extended only to account for effective request verification, not to implement real stopping physics. Existing fixture methods propagate setup errors and register temporary-directory cleanup.

## Completion gate

The main proposal and this catalog form the implementation approval unit. Proposed private helpers, source/entry/reference schemas, generated-byte identities and test methods are part of that approval. Any discovered need to change the approved design returns to the user before changing implementation. No implementation changes have been made by creating these documents. The documented public signatures were cross-checked between the two documents, and baseline tests remain90/90 with warnings as errors; this is baseline evidence, not a claim that the unimplemented integration has passed tests.

## Approved native parser clarification â€” 2026-10-20

The user approved accepting an optional comma between the quoted internal name and density, and one trailing comma after native composition fields. This handles the preserved rhesus-monkey and skeletal-muscle records without altering source bytes or provenance. Other malformed field/count structures remain errors.


## Approved compatibility amendment (2026-10-20)

This supersedes the earlier native comma flexibility. The strict compound parser requires a name/density comma and rejects a trailing composition comma. Existing malformed immutable resources remain untouched; reimport defaults for corrected resources.

New `compatibility.py` functions (no classes):
- `read_module_version(srim_root: Path) -> tuple[Path, bytes]`: prefer VERSION, otherwise _VERSION, returning actual path and stable bytes. Absolute root, link-free regular file and nonblank first ASCII line required. No writes; ValueError for invalid/changing data, filesystem errors propagate.
- `normalize_atomdata(contents: bytes) -> tuple[bytes, list[dict[str, object]]]`: fix only row 37 Ru Rubidium to Rb; preserve other bytes. Return set operation [37, "symbol"] = "Rb". Type errors raise ValueError; caller strictly validates output. No I/O.
- `normalize_compounds(contents: bytes) -> tuple[bytes, list[dict[str, object]]]`: insert missing quoted-name comma and remove one trailing composition comma; require full strict parsing after correction. Preserve other bytes. Return set operations [record_index, "native_composition"] = effective composition line without line ending. Invalid data raises ValueError; no I/O.

`_read_installation(config: Config) -> tuple[JSONObject, dict[str, bytes], JSONObject]` returns effective identities/bytes plus original source records (path, sha256, size_bytes, changes). `switch_sr_defaults` records original source identity and correction operations, hashes effective bytes for resources/reuse/baselines, and rechecks original hashes and version-file selection before publication. `verify_sr_baseline` normalizes live files in memory. `resolve_sr_inputs`, `resolve_default_bond`, `resolve_default_core`, `_prepare_sr_runtime` retain signatures and lifecycle, using the shared version reader. Capture workers receive corrected pinned resources. Installed files are never written. Existing locks, timeouts, schemas and directory layouts remain unchanged; no migration or configuration option.

Focused compatibility/material tests cover independent fixes, byte preservation, idempotence, strict malformed rejection, version preference/fallback, provenance, baseline verification and reuse. Run all backend/SR tests and representative native 2008 live checks with before/after installation hashes.
