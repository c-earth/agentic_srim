# CAB integration proposal

Current status: this design was explicitly approved and has been implemented and verified. See CAB_INTEGRATION_TRACKER.md for completion evidence. Approval-pending language below records the reviewed proposal, not the current implementation status.

Technical proposal for the agreed SRIM CAB integration, paired with [the complete API and test catalog](CAB_API_CATALOG.md). Implementation requires explicit approval of these documents together. Behavioral agreements are recorded in CAB_HANDOFF.md; implementation contracts not previously discussed are proposals for this review. No implementation has started.

## Calculation and selection flow

The caller allocates a simulation and worker using the existing backend APIs, then calls prepare_sr_simulation with a request and resource selections. Preparation validates caller data, resolves the current defaults generation once, reads and snapshots source contents, applies edits, constructs the target, resolves calculated dependencies, records the correction, and prepares the main runtime. It does not execute the main simulation. run_sr_simulation retains that responsibility.

Default recovery uses only the active default installation and its original default resources. Bragg used by the main correction uses the effective simulation dependencies. Explicitly selected inactive default core/bond versions can reuse existing entries but cannot recover absent ones; absent required entries are errors. User-created sparse versions instead use the established core S_E and bond zero fallbacks. Local removal of a default entry is explicit suppression, not permission to recover it again.

## Resource layout

All paths below are relative to memory/resources. There is no separate detached CAB cache.

```text
defaults.json
baselines/<baseline-id>.json
material/atomdata/vN/{payload,manifest.json}
material/compound/vN/{payload,manifest.json}
engine/electronic/vN/{payload,manifest.json}
engine/nuclear/vN/{payload,manifest.json}
cab/core/vN/{payload,manifest.json,entries/}
cab/bond/vN/{payload,manifest.json,entries/}
cab/bragg/vN/{payload,manifest.json,entries/}
```

N is a positive integer, allocated independently per category/name beyond the greatest existing vN, never filling gaps. Frozen user core/bond payloads contain their supplied entries; their entries directory is not used for recovery. Calculated CAB payloads contain a fixed descriptor; adding individually immutable entries changes neither payload nor manifest. Explicit creation of a user version snapshots available entries, without forcing bulk recovery. Source files and immutable base versions are never edited.

defaults.json is the single atomically replaced selection document. It contains schema_version, baseline_id, and references for atomdata, compound, electronic, nuclear, core, bond and bragg. It is a coordinated pointer, not an immutable dataset. Only installation switching changes it. No individual alias reassignment API is exposed. The original configured installation path is provenance, not its identity.

Baseline identity is SHA-256 of canonical UTF-8 JSON listing role, file hash and size for SRModule.exe, its VERSION, SR.exe, Data/VERSION, default ATOMDATA and default electronic/nuclear files, plus procedure/generated-manifest identities. The compound default reference is retained in the coordinated defaults document but its contents do not invalidate recovered CAB values. If only installed Compound.dat changes, register a new compound version and atomically update the coordinated defaults document while retaining existing core, bond, Bragg and other unchanged references. The numerical recovery baseline stays the same.

Each immutable calculated descriptor has schema_version, kind, origin, baseline_id, procedure, units, base and dependency. kind is core, bond or bragg. origin is installation for default core/bond and calculated for Bragg. base is a concrete reference or null. units is eV/(1E15 atoms/cm2). procedure identifies the implemented procedure and version, including fixed reference conditions. dependency contains the fixed recipe; specific lower-level entry references belong to individual entries after recovery. Core/bond baseline_id binds the full default recovery baseline. Bragg baseline_id is null and its dependency binds only the actual Bragg inputs; unrelated GUI/compound changes do not create a different Bragg recipe. User-created frozen strength versions instead use the schema-version-two entries payload and keep base/provenance in the registration manifest. The descriptor is hashed as the ordinary resource payload. No directory-wide hash is calculated.

Calculated entries live at entries/<dependency-key>/<entry-id>.json. Bragg dependency keys cover SRModule/VERSION bytes, effective ATOMDATA and coefficient bytes, and procedure identity. Bond/core entries cover their fixed default dependencies and their referenced calculated entries. Stable element IDs are z001 through z092; bond IDs use ordered atomic numbers plus s, d or t, such as z001-z006-s. Reversed bond endpoints normalize to one ID. Nuclear pair keys do not normalize reversed inputs; they reject them.

An entry contains schema_version, kind, entry_id, value, units, dependency, simulations, captures and calculation. Decimal values are strings. Hash the exact immutable entry-file bytes. An entry reference contains dataset (ordinary ResourceReference), dependency_key, entry_id, sha256 and size_bytes. The entry stores no hash of itself and no reference to its own future hash. Bragg versions bind one effective recipe, so their entry dependency_key must match that descriptor's recipe; the directory level is integrity organization, not permission to mix recipes in one Bragg version.

The ordinary resource reference continues to hash only payload. Entry references provide entry integrity separately. Baseline/descriptor and entry identities are not alternative user modes. Simulations retain concrete version references and exact entry references; default aliases are never used as persistent identities. The operation-only CABEntryResult wrapper carries reference and calculated state for accurate progress; it is not another persistent resource or mode.

## Request and selection formats

New unresolved requests use schema_version 2 with the existing parameters envelope. Projectile, stopping_units and energy retain their existing field meanings. Target and correction admit these new forms. Existing version-one explicit none/supplied requests remain supported at the low-level rendering/parsing boundary.

```json
{
  "schema_version": 2,
  "parameters": {
    "projectile": {"atomic_number": 1, "mass_amu": "1.008"},
    "target": {"source": "compound", "record_index": 39, "phase": "solid"},
    "correction": {"mode": "derived"},
    "stopping_units": 7,
    "energy": {"mode": "list", "values_keV": ["126"]}
  }
}
```

Direct targets have exactly source, phase, density_g_cm3, elements and bonds. Each element has atomic_number and stoichiometry. Compound targets have exactly source, record_index and phase. Their selected effective record supplies density, composition and bonds. Target masses use effective ATOMDATA natural_mass_amu. Hydrogen reference-projectile mass uses ATOMDATA's most-abundant-isotope mass field, not its integer mass number. Ordinary requested projectile mass remains explicit.

Correction mode derived has no caller-supplied applied_factor. Modes none and supplied preserve their existing factor/source/reason validation. There is one derived workflow: lookup and reuse first, calculate missing values as needed. The resolved snapshot uses the same mode name and adds the computed factor and provenance.

The selection object has exactly atomdata, electronic, nuclear, core, bond and compound. The first five are selection objects; compound is null for direct targets or an object for compound targets. A selection has exactly one of version or source, plus overrides and create_version. Version is default or vN. Defaults are resolved together once. Sources are either {kind: file, path: absolute-local-path} or {kind: inline, data: kind-specific-content}. No URLs. Native inline files are text, extended material and strength data use JSON objects. Native bytes are available through Python resource APIs, not encoded into a JSON request. Bragg has no selection/source/publication flag: choose its concrete version automatically from effective dependencies, creating a version only when no matching version exists. Local overrides participate in that dependency identity. Additional elements populate the same matching version. Custom dependency versions never reassign the installation default alias.

create_version false preserves the effective data locally; true publishes the effective data as a new immutable vN used by this request. It does not modify default. Original source contents, origins, hashes and patch operations are retained. Versions published before a later dependency failure remain registered; the failure must identify them rather than silently delete them.

Version/source objects and all nested schemas reject unknown fields, wrong types, bool-as-number, malformed decimals, duplicate JSON object fields and duplicate normalized keys. Invalid references and missing files fail before dependent engine execution. An absent core/bond entry and an explicitly removed default entry are represented separately in resolution evidence.

Core/bond patch objects are {operation: set, key: element-or-bond, value: decimal-string} or {operation: remove, key: element-or-bond}. One operation per normalized key; removing absent keys is an error. Native electronic rows use an element key and complete row string. Nuclear rows use [lower-Z,higher-Z] and complete row string; removal is forbidden for both coefficient kinds. Supplied row identity must match its key.

ATOMDATA patches use set only, with an existing element key and a nonempty object of named field updates. Editable fields are name, isotope_mass_number, isotope_mass_amu, natural_mass_amu, solid_density_g_cm3, solid_atomic_density_cm3, fermi_velocity, sublimation_energy_eV, gas_density_g_cm3 and gas_atomic_density_cm3. Atomic number and canonical symbol are fixed identity fields; removal is forbidden. The detailed field rules below and API catalog specify positive/nonnegative types and printable native names.

Extended compound JSON is {schema_version: 1, records: [...]}. Each record has exactly name, composition_basis, density_g_cm3, elements and bonds. composition_basis is atomic or mass. Atomic element entries have exactly atomic_number and stoichiometry; mass-based entries have exactly atomic_number and mass_proportion. Bonds maps normalized valid bond identifiers to count strings. Mass-based records reject any nonzero bond count. Preserve source quantities in their declared basis; convert mass proportions only when constructing a target or comparing atomic ratios using effective ATOMDATA. Native parsing separately retains display/internal names, comments, original record bytes, file/record hashes and source line positions. Native source bytes are not silently repaired, including the known blank-line anomaly. Edited native records are represented through a canonical effective database while unchanged original record bytes remain available as provenance.

Compound set/remove keys address original one-based indices. Set replaces a complete record. Appends require their own explicit append operation without a key; their order follows patch order. Remove/replace operations cannot target the same original index. Apply original-index edits before appends, then build effective indices and a base-to-effective mapping. Search and target record_index refer to that effective order. Unknown operations fail. Native and extended record count/bond syntax validation must preserve the agreed fractional values and duplicate names.

## Calculation rules

Use Decimal local contexts with precision 28 and ROUND_HALF_EVEN. Do not mutate the process-wide Decimal context. Preserve original numeric strings separately from computed normalized strings. Record precision, rounding and procedure version. Existing numeric strength JSON remains readable without adding a binary-float conversion step to its decimal tokens. New payloads write strings. Legacy Python float arguments are interpreted through their decimal string representation; bool/nonfinite values are rejected.

For nonzero effective bonds, f = (sum(n_E*C_E) + sum(m_b*B_b)) / sum(n_E*S_E). Counts retain their supplied scale. Require positive stoichiometry, nonnegative bonds/strengths, positive Bragg values and a positive finite final factor. Missing composition/bond information is an error for derivation. No valence or scale-consistency validation is performed. Document that only common rescaling of stoichiometry and bonds preserves the result.

First resolve positive-count bond contributions. If none has nonzero strength, return exactly 1.0 without resolving unnecessary core/Bragg dependencies. Zero counts do not trigger recovery. Valid non-SR bond types are allowed in stored/user data; missing selected strengths and default types outside SR's 18 contribute zero. Invalid bond syntax/kinds fail even when counts are zero.

Bragg simulates a single gas element with effective natural target mass, effective gas density, H projectile mass from effective ATOMDATA, energy 125 times that H mass, correction 1.0 and units 7. Use only electronic stopping. Create an ordinary dependency simulation with raw/parsed output, lifecycle and resource evidence. Cache publication requires a successful parse and positive value.

Default bond recovery fixes C_H = 0. Capture one H atom with one chosen bond. Zero/nonfinite initial correction is an error. Estimate n = max(1, ceiling(1/correction_one)). Reuse any already captured count. Verify f(n) >= 1 and, for n > 1, f(n-1) < 1 at displayed precision. Zero bonds cannot test this boundary because SR reports one; n=1 is already the smallest positive integer. Failed threshold verification raises an error and retains captures; do not adjust the count, search or retry. At most three distinct counts are captured: one, n and n-1 when positive. Derive B_b = f(n)*S_H/n.

For non-H cores, capture one atom of E plus one bond separately for all 18 SR types. Compute each estimate f_b*S_E - B_b, reject negative/nonfinite estimates, and average all 18. Retain each estimate, captures, min, max and spread. No arbitrary spread rejection threshold. H core is the documented exact convention zero and requires no H-core captures.

Bulk recovery processes stable atomic-number/bond order, uses the same cache and stops on first failure. Completed entries survive. Retry skips completed matching entries. Progress callback receives {kind, entry_id, state, completed, total}; state is cached or calculated. Dependency operations do not increment the outer bulk counter. Invoke callbacks after successful entry publication/reuse and release publication/execution locks first. Pass a fresh JSON object. A callback exception stops the operation and propagates unchanged, with a note identifying published/completed entries; do not retry the callback or roll back successful entries. Cancellation terminates only owned active processes, retains evidence and propagates interruption. This callback contract is a proposed implementation choice for the final approval, not a new preapproved behavior claim.

## Main simulation evidence and lifecycle

Preserve input/request.json as original intent and input/selections.json as original selection intent. Preserve input/effective-request.json as the fully numeric engine request. input/resolved.json records pinned defaults generation, effective source/override snapshots, compound identity mapping, concrete resource references, calculated entry references, dependency simulation IDs, calculation totals and applied factor. Input snapshots live under input/resources/<role>/ and are inventoried before main execution. The five-file SRModule runtime remains unchanged in shape.

Original request fields are not required to equal effective fields. Instead verification validates each layer and their linkage, reconstructs effective data from recorded sources/patches, verifies referenced immutable data, recomputes the arithmetic from recorded values and checks deterministic SR.IN bytes. It never reruns a GUI capture or dependency simulation merely to verify a prepared run. Output parsing uses the effective engine request. A mismatch is an execution preflight failure, not permission to silently resolve again.

Dependency records are ordinary SR simulation records. GUI capture evidence lives in owned worker capture directories with a capture manifest, generated SR.IN and Compound.dat, exact raw correction JSON, stdout/stderr, executable/input hashes, timestamps, completion/error and cleanup outcome. Published entries reference exact evidence paths plus hashes. GUI captures are not falsely labeled as SRModule executions. Main preparation failures leave dependency records/evidence; the main executable is not launched.

Use new version-two resolved/metadata schemas for expanded resource/entry provenance. Keep legacy version-one validators/readers for existing fixtures and APIs. Preserve exact validation rather than accepting arbitrary maps. The new reference collector enumerates all ordinary and entry dependencies deterministically and deduplicates identical references. Existing start/finish transitions remain created, running, succeeded or failed. No new main status or background job scheduler is introduced.

## Publication and concurrency

Installation switching stages verified versions/baseline documents, then atomically replaces defaults.json under the resource-root lock. Failures before that replacement leave the previous defaults active. Successfully staged immutable versions may survive a failed switch and are identified in the error. Source hash checks before/after reading guard changes during switching. Do not nest register_resource under its own resource-root lock; registration completes before the final pointer transaction, which rechecks the expected generation.

Snapshot the defaults generation for each request. Validate the live executable/default recovery inputs against that pinned baseline before/after any required calculation. If switching makes required default recovery impossible, fail without changing the request's versions. Core/bond defaults never recover against another baseline. Main runtime executables are snapshotted into the prepared runtime as today.

Engine/capture execution uses the existing worker-parent nonblocking lock; no nested engine calls while holding it. Resolve dependencies before main preparation takes this lock. Resource and calculated-entry publication locks are short and never span process execution. Duplicate publication rechecks existing entry under the publication lock. Equal semantic results reuse it while retaining alternative evidence; unequal values fail and preserve both evidence sets. Canonical comparison excludes timestamps and newly allocated evidence paths, which naturally differ between successful duplicate computations.

Filesystem publications use exclusive writes, flush/fsync, staging and rename. Reject links/junctions and traversal paths. Preserve staging/evidence on failures with exception notes. There is no automatic garbage collection, cache eviction, manual Bragg import or historical installation-runtime retention feature.

## Proposed module contracts

All code modules begin with module docstrings. All classes/functions/methods have first-statement docstrings, complete argument/return annotations including self/cls, side effects and raised errors. No implementation comments. Runtime dependencies remain Python stdlib, Windows PowerShell 5.1 and installed SRIM runtime requirements; no pip packages are proposed.

The signatures below summarize public/core boundaries. The companion API catalog specifies private helpers, shared type/argument/error contracts, exact changed signatures and test methods. Existing unlisted functions remain unchanged. Read both documents together; the catalog supplies implementation mechanics rather than a separate alternative design.

### Backend resources

File src/backend/resources.py retains immutable file registration/verification. Extend registration provenance to record origin/base/edits without mutating payload bytes. Alias resolution belongs to defaults.py, not a per-resource alias setter. Ordinary references keep category, name, version, sha256 and size_bytes. New writes use vN; old explicit labels remain readable.

```python
register_resource(paths: ProjectPaths, config: Config, category: str, name: str, version: str | None, source: Path | str | bytes, *, provenance: Mapping[str, object] | None = None, reuse_key: str | None = None) -> ResourceReference
resolve_resource(paths: ProjectPaths, category: str, name: str, version: str) -> ResourceReference
verify_resource(paths: ProjectPaths, reference: ResourceReference) -> Path
```

register_resource validates input and optional provenance, allocates/publishes a new immutable version, returns its concrete reference and raises ValueError for invalid/conflicting data or OSError for filesystem/locking failure. Optional reuse_key performs atomic find-or-register under its existing lock so simultaneous matching Bragg recipes do not create different versions. Explicit user version creation omits reuse_key. resolve_resource only accepts concrete versions on the new path; default lookup is a coordinated operation. verify_resource checks manifest/payload bytes and returns the payload path. Existing same-reference integrity guarantees remain. Backend code does not import SRIM metadata or execute engines.

### Default installation coordinator

File src/srim/defaults.py depends on config, resources, material parsing and descriptor construction. It publishes original native material/coefficient bytes, empty derived descriptors and the coordinated alias generation. It does not recover entries.

```python
switch_sr_defaults(paths: ProjectPaths, config: Config) -> JSONObject
read_sr_defaults(paths: ProjectPaths) -> JSONObject
verify_sr_baseline(paths: ProjectPaths, config: Config, baseline: JSONObject) -> None
```

switch_sr_defaults reads/validates configured installation files, stages resources and atomically switches aliases; returns the concrete generation document. Reusing an identical installed generation is idempotent. read_sr_defaults validates/returns a fresh coordinated snapshot; it does not register missing defaults. verify_sr_baseline compares live required files with the supplied pinned manifest and writes nothing. Invalid/mismatched baseline raises ValueError, filesystem/lock errors propagate. Registration and alias-change failure artifacts remain.

### Materials

File src/srim/material.py owns native/extended parsing, effective edits, search and target construction; no subprocess execution. Use JSON-compatible fresh values and exact strings rather than exposing mutable shared objects.

```python
parse_atomdata(contents: bytes) -> JSONObject
parse_compounds(contents: bytes) -> JSONObject
apply_material_overrides(kind: Literal['atomdata', 'compound'], data: JSONObject, overrides: Sequence[JSONObject]) -> JSONObject
search_compounds(data: JSONObject, *, name: str | None = None, name_mode: Literal['exact', 'contains'] = 'exact', composition: Mapping[ElementKey, str] | Sequence[ElementKey] | None = None, composition_mode: Literal['contains', 'elements', 'ratio'] = 'contains', atomdata: JSONObject | None = None) -> list[JSONObject]
construct_sr_target(target: JSONObject, atomdata: JSONObject, compounds: JSONObject | None) -> JSONObject
create_material_variant(paths: ProjectPaths, config: Config, kind: Literal['atomdata', 'compound'], *, source: Path | str | bytes | JSONObject | None = None, base_version: str | None = None, overrides: Sequence[JSONObject] = ()) -> ResourceReference
```

Parsers validate schema/native structure and return canonical records plus byte/source provenance, without writes. apply_material_overrides copies before editing and returns data plus identity mapping; invalid/duplicate/disallowed edits raise ValueError. Search validates filters, applies name/composition AND, returns every matching record in effective order, empty when absent, and uses exact integer cross-products for ratio tests. construct_sr_target validates direct/record selection, phase and SR-supported elements, resolves effective masses/names/density and returns numeric SR target plus bond/composition evidence. Missing required record/field raises ValueError. create_material_variant reads/validates exactly one source/base path, applies edits and registers immutable bytes plus provenance, returning its reference; filesystem errors propagate. No function changes caller data.

### Strength resources

File src/srim/cab.py retains element/bond normalization and immutable user core/bond import/edit APIs. Extend accepted decimal values and allow explicit removals. No Bragg source import. Remove register_cab_default's supplied-default publication behavior in favor of the coordinated default/recovery APIs. It must not remain a backdoor for user-supplied recovered defaults.

```python
create_cab_variant(paths: ProjectPaths, config: Config, kind: CABKind, *, source: CABSource | None = None, base_version: str | None = None, overrides: Sequence[JSONObject] = ()) -> ResourceReference
load_cab(paths: ProjectPaths, kind: CABKind, version: str) -> CABData
```

CABKind remains core/bond. CABDictionary/CABSource extend to decimal-string/Decimal values; CABData maps canonical element/bond keys to decimal strings. load_cab verifies and returns a fresh snapshot of available values without recovering anything. Creating from a lazy default snapshots only published entries before patches. Creation validates key/unit/value/operation constraints and publishes a new immutable user payload. Core zero values remain meaningful; effective zero bonds need not be listed in the nonzero contribution set, but override evidence is retained. The API catalog specifies each existing normalizer/encoder/decoder signature and the new override helper.

### Calculated entries

File src/srim/cab_cache.py owns descriptor/entry schema validation, deterministic dependency IDs and safe publication. It depends on backend resources and filesystem helpers; never launches processes. Proposed frozen class CABEntryReference has fields dataset: ResourceReference, dependency_key: str, entry_id: str, sha256: str, size_bytes: int. Its generated constructor stores inputs; APIs validate before using them. Entry paths are derived from these validated components, never accepted as arbitrary paths.

```python
find_cab_entry(paths: ProjectPaths, dataset: ResourceReference, dependency: JSONObject, entry_id: str) -> CABEntryReference | None
load_cab_entry(paths: ProjectPaths, reference: CABEntryReference) -> JSONObject
publish_cab_entry(paths: ProjectPaths, dataset: ResourceReference, entry: JSONObject) -> CABEntryReference
```

find verifies the descriptor and returns a verified reference or None for absence; corruption is an error, not a miss. load checks exact bytes/hash and dependency schema, returning a fresh document. publish requires successful validated provenance and atomically publishes or compares existing content; no numeric-only manual Bragg path is exposed. It returns the published reference, writes only resource entry artifacts, and raises ValueError on invalid/conflicting calculations or OSError on file/lock failures. Duplicate comparison is semantic as described above.

### Bragg simulation

File src/srim/bragg.py constructs ordinary elemental dependency simulations and uses low-level preparation/execution. It depends on cache/material/config and existing backend allocation; imports must avoid the public preparation/resolution cycle.

```python
resolve_bragg(paths: ProjectPaths, config: Config, worker: CreatedDirectory, inputs: SRDependencyInputs, atomic_number: int, *, timeout_seconds: float = 60.0) -> CABEntryResult
populate_bragg(paths: ProjectPaths, config: Config, worker: CreatedDirectory, inputs: SRDependencyInputs, *, atomic_numbers: Sequence[int] | None = None, timeout_seconds: float = 60.0, progress: Callable[[JSONObject], None] | None = None) -> list[CABEntryReference]
```

inputs is an already pinned, validated effective-dependency bundle, never arbitrary supplied Bragg values. Automatically reuse a descriptor/version with matching effective dependencies or publish a new concrete version when absent, including for local overrides. resolve verifies/cache-checks before allocating a missing dependency run, validates successful positive output, publishes and returns its reference. populate uses None for 1..92 in stable order; explicit nonempty unique subsets allow targeted precomputation. No caller-selected Bragg version, manual strength import or separate version-creation flag exists. Side effects are dependency records, owned runtimes and descriptor/entry publication. Failures preserve successes/evidence and propagate with stage/element identity. No background execution.

### Core and bond recovery

File src/srim/cab_recovery.py owns the integrated GUI capture function, mock inputs, core/bond formulas and bulk recovery. Move capture_correction's existing Windows automation into this module, retain its process ownership/cleanup and remove src/srim/capture_correction.py after validation. Refine the earlier draft's extra Python-worker approach: pass an explicit runtime Path to the integrated capture function and use cwd=runtime when it launches its owned SR.exe. The function already owns its PowerShell subprocesses and performs cleanup in finally, so no additional Python wrapper or copied standalone utility is needed. Parent code never calls os.chdir. Direct ownership also avoids terminating a Python wrapper before it can clean up its GUI child on cancellation.

Generate the already tested Microsoft.Windows.Common-Controls v6 XML manifest in the isolated worker. The installed SR directory has no sidecar manifest. Record generated bytes/hash and procedure identity; never modify the installation. The verified sufficient capture runtime is SR.exe, SR.exe.manifest, SR.IN, Data/ATOMDATA, Data/SCOEF03.dat, Data/SNUC03.dat, Data/VERSION and generated Data/Compound.dat. No installed compound dictionary, pictures, tutorials, TIN/TRIM executable or full-installation clone is needed for the tested capture path. An ATOMDATA-only probe failed, so do not omit the other numerical files. Individual necessity of each numerical file has not been established and is not needed to use this small verified inventory. This was verified only with the configured 2013 Standard installation; other installations must pass the same owned capture preflight or fail with evidence.

The worker-generated manifest uses UTF-8 XML with manifestVersion 1.0, x86 assembly identity SRIM.Compound.Check version 1.0.0.0 and a Microsoft.Windows.Common-Controls 6.0.0.0 x86 dependency, publicKeyToken 6595b64144ccf1df, language *. Preserve the exact already verified fixture bytes as a module constant; record their hash. The evidence fixture hash is a186aeb2d87c53bdda0f0c550e21b583a748a9feeeae97280daf6bf7a4afed15. Isolated inventory verification is recorded in .cache/cab-capture-inventory-n3c3_vv1/probe.json: the complete numerical-file case returned expected correction 2.854669 in 2.52 seconds; ATOMDATA-only failed; all installed source hashes remained unchanged.

```python
resolve_default_bond(paths: ProjectPaths, config: Config, worker: CreatedDirectory, defaults: JSONObject, bond: BondKey, *, capture_timeout_seconds: float = 30.0, dependency_timeout_seconds: float = 60.0) -> CABEntryResult
resolve_default_core(paths: ProjectPaths, config: Config, worker: CreatedDirectory, defaults: JSONObject, atomic_number: int, *, capture_timeout_seconds: float = 30.0, dependency_timeout_seconds: float = 60.0) -> CABEntryResult
populate_default_cab(paths: ProjectPaths, config: Config, worker: CreatedDirectory, kind: CABKind, *, entries: Sequence[ElementKey | BondKey] | None = None, capture_timeout_seconds: float = 30.0, dependency_timeout_seconds: float = 60.0, progress: Callable[[JSONObject], None] | None = None) -> list[CABEntryReference]
```

Resolve functions validate kinds/identifiers/default baseline, reuse entries, obtain default Bragg/bond dependencies, perform capture formulas and publish verified results. Unsupported default bond types are resolved as zero by the correction resolver, without invoking an SR capture. Captured zero/negative/nonfinite recovery results fail as specified. populate uses None for all 92 cores or all 18 SR bonds, freezes baseline once, and reports ordered completion. No new installation version per recovered entry. Failures retain records/process diagnostics and stop bulk work.

### Effective input resolution

File src/srim/resolution.py depends on defaults/material/CAB/cache/recovery/Bragg. It is the orchestrator; it does not itself launch the main runtime. Publication decisions and effective input reconstruction are centralized here rather than duplicated in execution verification.

```python
resolve_sr_inputs(paths: ProjectPaths, config: Config, worker: CreatedDirectory, request: JSONObject, selections: JSONObject, *, capture_timeout_seconds: float = 30.0, dependency_timeout_seconds: float = 60.0, progress: Callable[[JSONObject], None] | None = None) -> ResolvedSRInputs
verify_sr_resolution(paths: ProjectPaths, request: JSONObject, selections: JSONObject, resolved: JSONObject, snapshots: Mapping[str, bytes]) -> JSONObject
```

resolve validates schemas, snapshots selections and source bytes, applies edits/publication flags, builds target, resolves effective bonds before unnecessary cores/Bragg, calculates Decimal totals and returns a ResolvedSRInputs value containing effective engine request, complete resolved JSON evidence and a separate snapshot byte inventory. It may publish explicit versions and dependency results but never writes to the allocated main record before resolution is complete. verify reconstructs and checks without process execution, validates references/evidence and returns the effective engine request. Both reject inconsistent data with ValueError; filesystem errors propagate, dependency failures preserve paths, interruptions propagate after cleanup. ResolvedSRInputs is defined alongside JSON types in metadata.py to avoid a runtime/resolution/Bragg import cycle; see the detailed contracts below.

### Existing runtime and execution

File src/srim/runtime.py supplies public orchestration and low-level effective preparation. The public function imports resolution lazily; bragg.py imports only the low-level helper. This avoids runtime/resolution/bragg/execution import cycles. Dependency simulations use correction none, never recursively invoke CAB resolution.

```python
prepare_sr_simulation(paths: ProjectPaths, config: Config, created: CreatedDirectory, worker: CreatedDirectory, request: JSONObject, selections: JSONObject, *, capture_timeout_seconds: float = 30.0, dependency_timeout_seconds: float = 60.0, progress: Callable[[JSONObject], None] | None = None) -> Path
```

Public preparation resolves before locking/staging the main runtime and saves all original/effective layers. Its return value/error contract remains a prepared runtime Path with main status created. The low-level helper retains five-file byte preparation, manifest, staging and initialization, consumes the fully specified ResolvedSRInputs class and never recursively resolves CAB. Its complete signature is listed below and in the API catalog.

File src/srim/execution.py keeps verify_sr_runtime(paths: ProjectPaths, simulation_id: str, runtime: Path) -> JSONObject and run_sr_simulation(paths: ProjectPaths, config: Config, simulation_id: str, runtime: Path, *, timeout_seconds: float = 60.0) -> JSONObject. Verification branches on record schema version, validates effective resolution/resources and returns effective engine request. Remove the exactly-two-resources and original-equals-resolved assumptions only on the new schema path. Main execution lifecycle, output evidence and error ownership semantics remain unchanged. Parsing uses the verified effective request.

File src/srim/inputs.py separates unresolved request validation from numeric engine request validation. render_sr_input(request: JSONObject) -> bytes remains a pure deterministic ASCII renderer of effective numeric inputs, never performs resource I/O or derivation. Version-two intent must not be accepted as already effective input. All strings sent to SR.IN use non-exponent decimal syntax after exact conversion where needed.

File src/srim/metadata.py adds strict new schemas, deterministic collection of ordinary/entry references and snapshot inventory support. initialize_simulation retains its lifecycle role, but accepts the resolved effective snapshots through an explicit typed boundary. read/start/finish APIs retain their signatures and terminal guards. File src/srim/outputs.py keeps parsing helpers and parse_sr_output(contents: bytes, request: JSONObject) -> JSONObject; only validation dispatch changes to accept the new effective request form.

Files src/srim/electronic.py and nuclear.py keep existing complete-row replacement rules, add selection patch integration and prohibit independent default alias changes. Remove register_electronic_default and register_nuclear_default. Coordinated switch_sr_defaults initializes/switches defaults and read_sr_defaults retrieves concrete references. Variant creation writes numbered immutable versions with origin/base/patch provenance. Config/paths/records layout and allocation APIs need no changes. No TRIM or TIN implementation is added.

## Verification and delivery

Update existing backend/SR tests for numbered resources, strict alias handling, Decimal strings and changed preparation API. Retain version-one none/supplied rendering/parsing/metadata tests. Add focused files test/srim/test_material.py, test_defaults.py, test_cab_cache.py, test_bragg.py, test_cab_recovery.py and test_resolution.py. Test publication/interruption/lock failures, duplicate/conflicting computations, precise patch/record identity, all search modes, original/effective linkage, local source changes, old-default missing-entry errors and no-effective-bond short circuit. Mock bulk capture execution so all 92/18 cases can be checked without a 46-minute live recovery. Complete test classes/method signatures remain an approval item.

Live verification uses the configured installation in isolated workers: representative gas Bragg elements, all 18 bond recoveries, one non-H core, selected compound correction/end-to-end SR run, a cached repeat proving no process launch, and controlled timeout/interruption evidence. Compare exact formulas and resource integrity; do not invent numerical-agreement tolerances for dictionary captures. Record differences and input precision. Do not run full 91-core recovery unless requested. GUI access may require the platform execution approval when the live check is actually run.

Run the full existing and added suites through D:/projects/agentic_srim/.venv/Scripts/python.exe with warnings as errors. Check Python module/class/function docstrings, all annotations including self/cls, absence of implementation comments, ASCII/CRLF engine fixtures and git diff --check. Confirm no project imports/commands refer to removed capture_correction.py. Preserve independent historical experiment directories and source installation hashes. Do not commit or publish without user authorization.

## Detailed data and helper contracts

This section proposes the remaining implementation mechanics for final approval. It does not mark them as previously approved behavioral decisions.

### Canonical documents and validation

Canonical generated JSON uses UTF-8, two-space indentation, sorted object keys, ensure_ascii false, allow_nan false and one final LF. Arrays retain meaningful source/operation order. Hash exact encoded bytes with SHA-256. Registered native input payloads preserve their original bytes; canonical JSON applies to generated descriptors, entries, snapshots of JSON input and metadata, not to native sources. Preserve original file-source bytes separately when decoding JSON and canonicalizing effective contents.

Numeric strings reject whitespace, NaN and infinities. Accept decimal/scientific syntax for stored strengths and native decoded values; validate finite and the field's positive/nonnegative constraint. Render engine-bound numbers in fixed-point notation. Direct engine request limits remain those already validated by inputs.py. Compound names may be nonempty Unicode with no control characters; element row names sent to SR remain 1..13 printable ASCII characters without quotes. Element identifiers use the existing canonical periodic table. Generic bond storage supports 1..118; SR target calculation rejects target atomic numbers outside 1..92 before launching dependencies.

ATOMDATA native rows retain exactly their 12 columns: atomic number, symbol, name, most-abundant isotope mass number, isotope mass, natural mass, solid density, solid atomic density, Fermi velocity, sublimation energy, gas density and gas atomic density. Require one row for each atomic number 1..92 in ascending order and preserve headers/footer bytes. Atomic number and canonical symbol are identity fields and cannot be edited. Name is a nonempty printable ASCII token of at most 13 characters. isotope_mass_number is a positive integer excluding bool. Both masses, densities, atomic densities and Fermi velocity are positive finite decimal strings. Sublimation energy is finite and nonnegative. Unspecified fields retain their original values/spelling. Changes do not silently recalculate other fields. Set rejects empty/unknown field objects and attempts to alter identities; remove is rejected. Supplied files must pass the same checks.

Each new user strength payload has exactly schema_version: 2, kind, units and entries. Core entries have element and value; bond entries have elements, bond and value. Values are decimal strings. Legacy schema_version 1 numeric entries decode through json numeric-token Decimal parsing and are normalized without binary-float conversion. Duplicate object fields or equivalent entries are errors. Preserve meaningful zero cores; zero bonds may remain recorded as explicit override/source data even though effective nonzero contribution sets exclude them.

### Typed preparation result

Define a frozen dataclass ResolvedSRInputs in src/srim/metadata.py with generated constructor inputs request: JSONObject, selections: JSONObject, effective_request: JSONObject, resolved: JSONObject, snapshots: tuple[tuple[str, bytes], ...] and engine_files: tuple[tuple[str, bytes], ...]. Its purpose is to carry independently copied original intent, numeric engine input, resolution evidence, snapshot bytes and pinned executable/version/coefficient bytes without putting bytes inside JSON. Construction stores fields without I/O; producer and consuming APIs validate them. Each snapshot name is a normalized relative path beneath input, with no case-insensitive duplicates. Callers cannot use it to write arbitrary paths. Frozen construction does not pretend nested JSON maps are immutable; prepare/verify defensively copy and validate before publication. SRDependencyInputs carries the pinned defaults/resource/atomdata/engine-file subset for Bragg as specified in the API catalog.

The low-level runtime boundary is:

```python
_prepare_sr_runtime(paths: ProjectPaths, config: Config, created: CreatedDirectory, worker: CreatedDirectory, inputs: ResolvedSRInputs) -> Path
```

It validates the typed bundle/allocation and pinned executable/VERSION bytes, rechecks required live installation identity, stages the five-file runtime under the worker lock, initializes original/effective evidence and returns its Path. It never replaces the pinned bytes with newly chosen executable bytes. It does not resolve aliases, calculate missing dependencies or recursively call public preparation. All input bytes are already frozen. Invalid bundles raise ValueError; collision/lock/filesystem errors propagate with retained-artifact notes. Dependency Bragg runs construct a correction-none bundle and call this helper directly.

initialize_simulation retains its existing arguments and adds keyword-only input_files: Mapping[str, bytes] | None = None alongside engine_inputs. input_files accepts validated nested evidence names under resources and cab plus effective-request.json/selections.json; engine_inputs retains its flat filenames. Reject overlap with reserved request.json/resolved.json and with each other. Create only validated parent directories, write exclusively and inventory them before publishing metadata. The exact complete signature is:

```python
initialize_simulation(paths: ProjectPaths, created: CreatedDirectory, engine: JSONObject, request: JSONObject, resolved: JSONObject, continuation: JSONObject | None = None, *, engine_inputs: Mapping[str, bytes] | None = None, input_files: Mapping[str, bytes] | None = None) -> JSONObject
```

### Resolved record schema

Version-two resolved records have exactly schema_version, parameters, sources, resources, cab, defaults, target_provenance and dependencies. parameters is the complete effective numeric engine parameters object. sources contains executable/VERSION manifests. defaults is the pinned coordinated generation document plus its exact file hash. resources maps resource roles to resolved selection evidence. dependencies is an ordered list of dependency simulation IDs and capture manifest references. target_provenance has source plus either a selected record identity/map or direct-input identity. All optional fields use explicit null, not arbitrary omitted extras.

Each resolved role has exactly reference, base, source, overrides, effective and published. reference is the concrete originally selected resource or null for file/inline inputs. base is the referenced parent version for version creation or null. source contains kind, origin and snapshot; snapshot and effective are record-relative path/hash/size manifests. For recovered defaults the source snapshot is a pinned available-entry base view, not a claim that the descriptor payload contains all numeric entries; effective local state is an explicit overlay. Frozen copies retain their copied entry references in registration provenance. origin is the concrete resource identity, original absolute source path or inline marker as appropriate, not a later input to reread. published is the newly registered effective version or null. Metadata collects all concrete references used, including bases, published outputs and calculated-entry datasets, deterministically deduplicated.

The derived cab object has exactly mode, applied_factor, core_entries, bond_entries, bragg_entries, composition, bonds, omissions, reference and calculation. Each contribution includes identifier, count where applicable, strength, origin and optional verified entry reference. No recovered contributions are copied without linking their immutable entry evidence. Local user strengths instead reference their effective snapshot/hash and override identity. reference contains the fixed procedure's H mass, energy, gas phase and units. calculation contains method/version, precision, rounding, path, intermediate_totals and calculated_factor. path records weighted-sum or no-effective-bonds; these are evidence labels, not user modes. For the short circuit record zero totals and factor 1.0 without inventing simulated Bragg entries. supplied/none retain their existing validated cab shapes.

### Existing default and variant APIs

User approved removing register_electronic_default and register_nuclear_default, rather than retaining misleading compatibility wrappers. Use switch_sr_defaults for coordinated initialization/switching and read_sr_defaults for concrete references. Update all tests and callers. If defaults have not been initialized, read_sr_defaults raises ValueError directing the caller to switch_sr_defaults. Existing create_electronic_variant/create_nuclear_variant source/replacement arguments stay typed as today; add keyword-only removals nowhere, since row deletion is prohibited. Their manifests gain origin/base/replacement provenance through extended registration.

User approved removing register_cab_default(paths, config, kind, source), whose supplied-source contract conflicts with recovered default provenance. User supplied data goes through create_cab_variant. Use read_sr_defaults for default core/bond references rather than adding separate get-default wrappers. load_cab accepts default as a lookup convenience but returns available entries only and never recovers them. Simulation resolution still pins all aliases together and never performs separate default lookups halfway through a request.

### Integrated capture functions

```python
_capture_correction(runtime: Path, compound_name: str, output_path: Path, *, timeout_seconds: float = 30.0) -> str
_prepare_capture_runtime(paths: ProjectPaths, config: Config, worker: CreatedDirectory, defaults: JSONObject, compound: bytes) -> Path
_capture_mock(paths: ProjectPaths, config: Config, worker: CreatedDirectory, defaults: JSONObject, atomic_number: int, bond: BondKey, count: int, *, timeout_seconds: float) -> JSONObject
_render_mock_compound(atomic_number: int, bond: BondKey, count: int) -> bytes
_estimate_bond_count(correction: str) -> int
_recover_bond_value(correction: str, hydrogen_stopping: str, count: int) -> str
_recover_core_value(captures: Sequence[JSONObject], elemental_stopping: str, bond_values: Mapping[str, str]) -> JSONObject
```

_capture_correction takes the explicit owned runtime rather than process cwd, validates name/output/timeout, runs the existing PowerShell/window automation and always closes/terminates its owned processes. It returns exact captured text; raw capture evidence records zero too, allowing the recovery layer to raise the agreed zero-initial-capture error with the actual reading retained. Negative/nonfinite/malformed text is retained in failure diagnostics and never published as usable data. Cleanup has the existing separate bounded allowance. No global directory changes, CLI dispatcher or extra Python worker file.

_prepare_capture_runtime validates worker/baseline, stages exactly the verified inventory, generates its manifest/mock dictionary, checks input hashes and returns a new owned runtime directory. It writes no installed resource and raises filesystem/identity errors with paths. _capture_mock owns the worker execution lock, calls preparation/capture without nested engine calls, checks input integrity and writes the completed/failed capture manifest including exact diagnostics. Obtain required Bragg dependencies before entering that lock. Its return is capture JSON with exact correction and evidence references, not an unlinked number.

_render_mock_compound is pure: one atom of the requested SR element, exactly one chosen positive integer bond count, fixed diagnostic density/name/header and the 18 native columns. Reject unsupported identifiers/counts; no valence check. _estimate_bond_count is pure Decimal ceiling and rejects zero/nonpositive/nonfinite input. Integer count serialization uses its full decimal digits and input length/range errors fail before launch where detectable; do not silently clamp. Threshold verification never adjusts/searches. _recover_bond_value validates positive stopping/count/correction, applies the agreed formula in the local Decimal context and returns a decimal string. _recover_core_value requires all 18 distinct supported captures/strengths, validates each estimate, returns mean/estimates/min/max/spread and raises on missing/negative/nonfinite data. These pure functions do no filesystem/process work.

### Capture probe and native mass basis findings

The sufficient inventory probe left no owned SR.exe running. Its successful correction exactly matched the prior capture. Raw probe outputs and installation hashes are retained separately from all earlier experiments.

Native Compound.dat explicitly permits mass-based composition through a leading % on the internal name. Read-only inspection found 70 such records in both current and archived 2013 Standard, and 68 in 2008. All inspected mass-based records have zero bond counts. Their raw numeric values are not stoichiometric atom counts; the earlier dry-air example was a mass-based record. User approved preserving the original mass values/basis and converting to atomic proportions using effective selected ATOMDATA: atomic proportion = mass proportion / natural atomic mass. Do not normalize the original source or mislabel its values as atom counts. Reject mass-based records with any nonzero bond count: a bond-compatible atomic scale has not been supplied, even if a selected strength would be zero. This is format/scale validation, not chemical-plausibility checking. Construct the engine target from converted atomic proportions and record both original values and conversion masses/results. User also approved composition-ratio search using atomic proportions and requiring selected ATOMDATA for mass-based records. Use exact rational arithmetic on finite decimal inputs, not precision-28 divided values; compare w_i*M_j*r_j with w_j*M_i*r_i exactly for query atomic proportions r and source mass proportions w. Source evidence is the native header and records, not a new consolidated experimental dataset.

The pure search API accepts atomdata as an optional keyword because atomic records and name/element-only filtering need no mass conversion. If ratio matching would evaluate a mass-based candidate and atomdata is absent or lacks a required mass, raise ValueError; never silently skip that candidate or consult a global installation. The higher-level caller supplies its effective ATOMDATA selection. Preserve native values in returned records and identify their basis; exact comparison never rewrites the database.

## Implementation approval scope

Approval covers this proposal and CAB_API_CATALOG.md together: the seven new SRIM modules material/defaults/cab_constants/cab_cache/bragg/cab_recovery/resolution; the cataloged backend resource and eight existing SRIM-module changes; strict request/selection/source/override/entry/metadata formats; test fixtures/helpers/methods; removal of independent default registration and the copied capture utility; and the stated validation/live-check plan. Existing config/path/record layout, TRIM/TIN and unrelated experiments remain outside implementation scope.

Important interface changes are the complete selections argument to prepare_sr_simulation, coordinated switch_sr_defaults/read_sr_defaults, decimal-string strengths, explicit composition basis and the original-versus-effective input split. Bragg version creation is automatic by its actual dependencies, with no supplied values or publication flag. A shared constants-only module avoids circular imports and pins generated native assets. Per-call calculation state is separate from immutable entry identity so progress remains accurate under concurrent publication.

The final design review checked the documented signatures, shared schemas, import direction, publication lock ordering, old-default missing-entry errors, source snapshots, user fallbacks and no-effective-bond short circuit against the recorded agreements. Generated assets are pinned to verified local fixture hashes. The sufficient isolated capture file set and unchanged-installation behavior were live-probed on configured 2013 Standard; other installations and the integrated implementation still require the planned checks, not assumptions of identical numerical tables.

No implementation code has changed. Begin only after explicit approval of this complete proposal, announce implementation start, then perform the described work and checks. If implementation exposes a required change to the approved design, present it for approval before proceeding with that change.

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
