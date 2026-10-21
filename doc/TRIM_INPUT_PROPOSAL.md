# TRIM input implementation proposal

Status: explicitly approved by the user on 2026-10-21. The input-first implementation and verification are complete. Main TRIM runtime staging and execution remain deferred as approved.

## Scope and established decisions

Implement input validation, versioned damage defaults, resource/material/CAB resolution, deterministic TRIM.IN and TRIM.DAT generation, offline verification and export of a self-contained input bundle. Use SR's separation of original intent, selections, effective parameters, pinned bytes and provenance. Do not add a TRIM runner, worker staging, queue, output parser, resume mechanism, GUI editor or simulation lifecycle in this change.

All supported calculation modes are included: 2008 input codes 1–7 and 2013 input codes 1–8. Standard and Pro use the 2013 mapping. Original installations are read only. Missing CAB dependencies may use the existing SRModule and SR GUI machinery during resolution, as already agreed; the main TRIM calculation never starts from the production input APIs.

Every layer keeps its own name, thickness, phase, density, composition and correction. Every layer component gets a separate native atom entry. Entries are never merged between layers. Preserve layer/component ordering. The ten-layer experiment established that separate and shared Si entries gave equal aggregate profiles in that tested geometry; it did not establish exact equality between one and ten layers.

## Validation policy

Distinguish malformed input, confirmed native behavior, resource coverage and unknown executable behavior.

- Reject malformed schemas, unknown keys, bool in integer fields, nonfinite numbers, inconsistent references, unsafe paths and record-breaking text.
- Positive quantities are mass, thickness, density, projectile/event energy and nonzero component stoichiometry. Damage energies and EXYZ increment are nonnegative; in particular, explicit lattice binding of zero is supported. Counts and indices are positive integers. Seeds and autosave values are integers without speculative lower/upper caps; negative values are preserved for native evaluation rather than rejected on an unverified bound.
- Reject incidence angle exactly 90 degrees: all three tested executables silently reset it to zero. Do not extrapolate that observation into an untested decimal upper bound of 89.9. Other finite angles are preserved for native evaluation.
- Do not cap layer count, global atom-entry count, name length, ion/event count, seed, autosave or energy based on old manual statements or isolated timeout observations. In particular, the 49-entry success / 50-entry timeout does not establish a universal maximum.
- Resource lookup can fail because a selected dataset lacks an entry. Describe that as missing input data, not an executable limit. The complete explicit engine-request API remains available without material/default lookups.
- No clamping, normalization of requested direction vectors, automatic event relocation, layer merging, parameter substitution or automatic retry.
- The input bundle records native values requiring execution verification. The future runner must check native errors, dialogs, timeouts and saved effective values, retain evidence, report discrepancies and investigate. Corrective parameter suggestions are offered only when requested. This input change cannot claim to implement that runner behavior.

Existing SR request limits remain unchanged and are not applied to TRIM requests. Existing CAB/resource libraries retain their actual supported data contracts; failures in an optional derived correction are reported as dependency failures, never as evidence of a TRIM limit.

## File plan and dependencies

Paths below are relative to D:/projects/agentic_srim.

| File | Purpose and complete responsibility |
| --- | --- |
| `src/srim/trim_inputs.py` (new) | Validate high-level and explicit engine requests; flatten layer/component data; render TRIM.IN/TRIM.DAT; read only the supplied profile and request objects. Depends on Decimal, JSON types and no filesystem or processes. |
| `src/srim/trim_defaults.py` (new) | Parse/edit/register damage resources; manage the TRIM damage-default pointer; read and pin installed TRIM identity and fixed fields. Reuse backend resource registration, safe file reading, JSON publication and locks. |
| `src/srim/data/trim-damage-defaults.json` (new) | Packaged 92-row Disp/Latt seed dataset transcribed from the verified CSV. No fixed Surf table. This is a seed used to create a normal immutable vN resource, not the live default resource. |
| `src/srim/trim_resolution.py` (new) | ResolvedTRIMInputs bundle, shared-selection snapshotting, layer material construction, component overrides, surface rule, CAB orchestration and independent replay. Depends on trim_inputs, trim_defaults and existing material/resource/CAB code. |
| `src/srim/trim_storage.py` (new) | Verify and export an input bundle into a new caller-selected directory. No worker allocation, simulation record, status transition or native execution. |
| `src/srim/resolution.py` (modified) | Extract the existing correction-resolution block into `resolve_material_correction`; existing `resolve_sr_inputs` calls that helper. Reuse source selection/publication helpers for the six existing roles. Preserve SR schemas, mathematical behavior, resource publication policy, caching and error behavior. |
| `test/srim/test_trim_inputs.py` (new) | Contract tests for request validation, mode mapping, flattening and exact native bytes. |
| `test/srim/test_trim_defaults.py` (new) | Contract tests for the seed table, resource versions/overrides, default pointer, installed profile and integrity. |
| `test/srim/test_trim_resolution.py` (new) | Contract tests for layer resolution, source precedence, replay, dependency failure and preservation of uncertain native values. |
| `test/srim/test_trim_storage.py` (new) | Export integrity, collision, path safety and retained-artifact tests. |
| `test/srim/test_resolution.py` (modified) | Add direct shared-correction regression coverage while retaining the current SR tests. |
| `doc/guides/trim-inputs.md` (new) | End-to-end input-only usage and bundle inspection; explain that generating inputs does not run TRIM. |
| `doc/reference/trim-input-format.md` (new) | Final approved schemas, signatures, units, native layout, limits policy and provenance. |
| `doc/reference/api.md`, `doc/README.md`, `README.md` (modified) | Link the input-only API/guide and describe its implemented boundary after verification. Preserve existing SR documentation. |
| `doc/TRIM_INPUT_PROPOSAL.md` (this file) | Reviewable proposal; update approval/implementation status only to reflect actual user decisions and work. |

No changes to settings.toml, Config, backend schemas, SR metadata schemas, execution, queue, storage cleanup or installation files are proposed. Auxiliary verification artifacts stay under `.cache/`.

## Resource and defaults contracts

Reuse existing SR default selections for atomdata, compound, electronic, nuclear, core and bond. TRIM does not automatically switch the SR default generation. Require an existing coherent SR generation when resolving those aliases. Pin that generation once per TRIM resolution. The TRIM-specific pointer adds only the damage resource and does not mutate SR preferences.

Storage uses the existing layout:

```text
memory/resources/
  material/trim-damage/vN/                 existing backend payload/manifest layout
  trim-damage-defaults.json                TRIM-specific default reference
```

Damage payload is UTF-8 JSON with exactly:

```json
{
  "schema_version": 1,
  "units": "eV",
  "rows": [
    {"atomic_number": 1, "displacement_eV": "10", "lattice_binding_eV": "3"}
  ]
}
```

The illustrated row is abbreviated: the seed and registered table require exactly one ordered row for each Z=1–92. Those are the measured dataset identities, not a claim that the native executable rejects every other Z. Each row requires the two fields shown and may additionally contain `surface_binding_eV` as a direct resource-level override. Missing Surf means use the selected ATOMDATA rule. A present zero is an explicit override, not a missing value. No other fields are accepted.

Damage operations follow existing set/remove vocabulary:

```json
{"operation": "set", "key": [14, "lattice_binding_eV"], "value": "0"}
```

`set` accepts any of the three energy fields; `remove` accepts only the optional `surface_binding_eV` field and restores algorithmic Surf. Duplicate operations on the same field are rejected instead of relying on ordering. Required Disp/Latt rows cannot be removed. Resource versions are allocated by the existing locked vN mechanism; variants never edit a previous version or switch defaults implicitly.

TRIM damage-default pointer is exactly `{"schema_version": 1, "damage": <ResourceReference>}`. `switch_trim_defaults(..., damage_version="builtin")` registers/reuses the packaged seed then atomically selects it. A concrete vN selects an existing verified damage resource. Resolution never implicitly creates or switches this pointer.

Damage precedence, independently for each field:

1. Component's own request override.
2. Effective selected damage resource, including selection overrides and optional direct Surf.
3. For Surf only, effective selected ATOMDATA sublimation energy: positive → same value; zero → `2` eV.

Thus Surf can be overridden directly both in damage resources and on individual components; component overrides always win. Negative/malformed source values are invalid data. Preserve each value's origin, selected reference, override operation and surface-rule version `1`. Disp/Latt are looked up, not physically calculated. Never average damage energies across a layer.

## High-level request schema

Envelope is `{"schema_version": 1, "engine": "trim", "parameters": {...}}`. All parameter keys below are required; null is allowed only where explicitly stated. No run-time defaults are silently inserted for seed, count, autosave, plot or output controls.

```text
parameters:
  name: str
  mode: one of the eight descriptive names listed below
  projectile:
    atomic_number: positive int
    mass_amu: positive decimal string | null
    energy_keV: positive decimal string
    angle_degrees: finite decimal string
  total_ions: positive int
  random_seed: int
  autosave_ions: int
  outputs:
    ranges: bool
    backscatter: bool
    transmission: bool
    sputtering: bool
    collisions: "none" | "ions" | "ions-and-recoils"
    exyz_increment_eV: nonnegative decimal string
  plot:
    type: "xy-ions-recoils" | "xz-ions-recoils" | "xy-ions"
          | "yz-ions-recoils" | "all" | "none"
    minimum_depth_angstrom: nonnegative decimal string
    maximum_depth_angstrom: nonnegative decimal string
  layers: nonempty ordered list of layer objects
  events: null | nonempty ordered list of event objects
```

Decimal strings accept signed scientific decimal syntax, with positive/nonnegative semantics applied per field. Reject NaN, infinity and surrounding whitespace. Rendering converts exponent spellings to an exact plain decimal representation using Decimal without rounding; original spelling remains in request.json. No binary floats for physical quantities. Integers exclude bool. No arbitrary digit-count or magnitude cap.

Null projectile mass means use selected ATOMDATA's most abundant isotope mass. Explicit projectile mass takes priority. No automatic energy/unit conversion: the caller supplies units named in each field. Plot depths `0,0` mean full target. Otherwise require maximum greater than minimum; do not reject windows beyond target depth on an unverified engine-bound assumption.

Each layer has exactly:

```text
name: str
thickness_angstrom: positive decimal string
material: direct or compound selector
correction: existing SR none/supplied/derived form
overrides:
  density_g_cm3: positive decimal string | null
  components: ordered list of component override objects
```

Direct material is SR-style `{"source":"direct","phase":"solid"|"gas","density_g_cm3":...,"elements":[{"atomic_number":...,"stoichiometry":...}],"bonds":...}`. Bonds may be omitted for none/supplied correction and must be supplied for derived correction. Compound material is `{"source":"compound","record_index":positive int,"phase":"solid"|"gas"}`. Selected compound records supply composition, density and bonds; phase is always explicit. Atomic/mass composition conversion follows the existing material implementation and its Decimal precision/rounding policy. Do not infer density from elemental mixtures.

Component overrides contain `component_index` (one-based within the effective layer composition) and any nonempty subset of `mass_amu`, `displacement_eV`, `lattice_binding_eV`, `surface_binding_eV`. At most one override object per component. Missing field means inherit. Component masses otherwise use selected ATOMDATA natural mass. Density override applies after material selection. Corrected mass/density/energies and their origins are all recorded. No automatic recalculation of supplied density after composition changes.

Direct component order is preserved, including repeated atomic numbers. The constructor handles TRIM composition directly instead of inheriting SR's uniqueness or row-count rules. If a repeated species cannot be processed by an explicitly requested CAB dependency, report that dependency limitation without blocking none/supplied TRIM input generation. Missing lookup data can be supplied through component mass/energy overrides; native atom labels are generated from selected symbols where available, otherwise `Z<atomic_number>`.

None correction uses the existing exact `{"mode":"none","applied_factor":"1.0"}`. Supplied uses the existing positive applied_factor plus nonblank source/reason. Derived is `{"mode":"derived"}` with no user factor. Reuse existing CAB reference conditions, omissions/fallbacks, precision and evidence; do not infer bonds, invent an alternate formula or equate target phase with the CAB reference phase. Corrections are evaluated for each layer. Component mass overrides apply to the composition passed to CAB.

## Selections

One shared selections object has exactly seven keys: `atomdata`, `compound`, `electronic`, `nuclear`, `core`, `bond`, `damage`. Compound is null exactly when no layer selects a compound. The other six are selection objects, matching SR's explicit selection philosophy even when CAB is unused.

Each selection has exactly one of `version` or `source`, plus required `overrides` and `create_version`. `version` is `default` or concrete vN. Source is an absolute file path or inline data using the existing role's format; damage uses the JSON schema above. `create_version=false` records effective bytes in the bundle; true publishes one new version for that role, once per overall resolution, without switching defaults. All layers share this pinned selection set. Per-layer resource-version selection is not introduced; per-layer physical/component overrides remain available.

All sources are snapshotted before dependency runs and publications. Preserve original/effective bytes and source transformations. Existing normalization of known ATOMDATA/compound defects is reused when importing defaults. Do not silently normalize arbitrary user sources. Concurrent default changes do not redirect an in-progress resolution.

## Modes and events

| Descriptive mode | 2008 code | 2013 code |
| --- | ---: | ---: |
| `ion-quick` | 1 | 1 |
| `ion-full` | 2 | 2 |
| `ion-monolayer` | 3 | 3 |
| `specified-ion-quick` | 4 | 4 |
| `specified-ion-full` | 5 | 5 |
| `recoil-quick` | 6 | 8 |
| `recoil-full` | 7 | 6 |
| `recoil-monolayer` | unsupported | 7 |

This is the already agreed mode scope, not speculative numeric validation. Unknown installation families cannot be assigned an inferred mode map. Explicit engine requests use a supplied supported family and descriptive mode; no raw unsupported calculation-code escape is added.

Events are null for ion modes 1–3 and nonempty for specified-ion/recoil modes. Each event has exactly `id`, `atomic_number`, `energy_eV`, `position_angstrom` and `direction_cosines`. The latter two are lists of exactly three decimal strings in X,Y,Z order. Position/direction values may be signed; energies are positive. Event IDs are nonblank printable ASCII tokens without whitespace, commas or quotes; five characters are the tested form, but other lengths are preserved for executable evaluation rather than rejected by an unverified limit. Preserve order and duplicate IDs.

No unit-vector tolerance, normalization, maximum event count, species-matching rule, layer-boundary convention or input-exhaustion policy is invented. Record these as execution checks. If event energy exceeds the setup energy, preserve the request and record the native precomputation-coverage concern rather than raising an unverified numeric-limit error. Do not silently replace total_ions with the event count.

TRIM.DAT has ten header lines, line 8 containing the calculation name, then one identifier and eight numeric fields per event. Use ASCII CRLF and terminal 0x1A, matching successful probes. The calculation name must therefore be ASCII when events are requested; ordinary TRIM.IN text uses strict CP1252. Reject embedded CR/LF, NUL and other record-control characters. Render embedded double quotes in quoted TRIM.IN names by doubling them, preserving the original value and recording that native quote handling is unverified until tested. No name-length cap or truncation. Encoding failures are representation errors, not engine-limit claims.

## Installed profile and explicit engine schema

Read `TRIM.exe`, `TRIM.IN`, `Data/TRIM.cfg` and `Data/VERSION` from config.srim_root, with hash/size checks before and after stable reads. Read only the fixed ion-line Bragg field, Reminders and final stopping selector from the installed TRIM.IN; do not inherit the installed projectile, target, count, outputs or plot. Keep the entire source file as provenance. Current values are `0`, `0`, `0` in all three installations. Copy future parseable values exactly; do not infer a stopping-model year from headings. Identify engine family from Data/VERSION, not the TRIM.IN title. Pin TRIM.exe hash to the profile.

The explicit engine envelope is also `schema_version=1, engine=trim`, with parameters containing the same name/mode/projectile/count/seed/autosave/output/plot/events fields, plus:

```text
engine_family: "2008" | "2013"
native_fields:
  legacy_bragg: finite decimal string
  reminders: int
  stopping_version: int
atoms: nonempty ordered list of
  atomic_number: positive int
  mass_amu: positive decimal string
  displacement_eV: nonnegative decimal string
  lattice_binding_eV: nonnegative decimal string
  surface_binding_eV: nonnegative decimal string
layers: nonempty ordered list of
  name: str
  thickness_angstrom: positive decimal string
  density_g_cm3: positive decimal string
  phase: "solid" | "gas"
  correction_factor: positive decimal string
  stoichiometry: list of nonnegative decimal strings, one per atom
```

Each layer must have at least one positive stoichiometric entry; each atom must occur positively in at least one layer. Projectile mass is explicit here. This API performs no material/default lookup, so complete explicit inputs are not capped by the packaged 92-row table. Source-derived installed native_fields are fixed during high-level resolution; low-level explicit calls are responsible for their supplied profile values.

Flattening appends one atom per layer component; each native layer gets zeroes outside its own component columns. Input quantities are not averaged. Render explanatory headings in a fixed layout; atom descriptive prefix has exactly 16 ASCII characters, using a generated ordinal label, followed by numeric Z/mass. Preserve the leading `0` in the tested phase-heading line. Plot names map to codes 0–5 in the order listed above. Output booleans map to 0/1; collisions maps to 0/1/2. Use CP1252 CRLF and a final CRLF for TRIM.IN. Use selected installed stopping selector rather than guessing a model.

## API catalogue

All new code follows AGENTS.md: module/class/function docstrings, typed arguments including self/cls where present, explicit return types, and no implementation comments. No custom exception classes or hand-written class methods are proposed. Dataclass-generated construction stores values only; producers/consumers validate and copy nested data.

### trim_inputs.py

```python
def _decimal(value: JSONValue, field: str, *, minimum: Decimal | None = None, strict: bool = False) -> Decimal: ...
def _text(value: JSONValue, field: str, *, ascii_only: bool = False) -> str: ...
def validate_trim_request(request: JSONObject) -> None: ...
def validate_trim_engine_request(request: JSONObject) -> None: ...
def flatten_trim_target(layers: Sequence[JSONObject]) -> tuple[list[JSONObject], list[JSONObject], list[JSONObject]]: ...
def render_trim_input(request: JSONObject) -> bytes: ...
def render_trim_events(request: JSONObject) -> bytes | None: ...
```

- `_decimal`: validate one named decimal string and optional inclusive/strict minimum, return exact Decimal; ValueError names the field. No mutation, I/O, rounding or maximum.
- `_text`: validate a named single-record text value in CP1252 or ASCII; return unchanged string. ValueError for encoding/record-control failures. No length cap or I/O.
- `validate_trim_request`: validate the exact high-level schema and internal relationships above; return None; no lookup/publication/mutation/processes. Invalid schema/known unsafe semantics raise ValueError with layer/component/event path.
- `validate_trim_engine_request`: validate the complete explicit schema, family/mode mapping and matrix consistency; return None; same pure error contract. Does not require resources or a live installation.
- `flatten_trim_target`: accept resolved ordered layers with numeric components; return ordered atoms, native layers and index-map provenance. Pure; raise ValueError on incomplete component data/matrix inconsistency. Index map rows are exactly `{layer_index, component_index, atom_index}` with one-based indices.
- `render_trim_input`: validate explicit request, produce exact TRIM.IN bytes per layout; pure and immutable. ValueError for representation/schema errors.
- `render_trim_events`: validate explicit request, return TRIM.DAT bytes or None for ordinary ion modes. Same pure error/immutability contract; no files opened.

### trim_defaults.py

```python
def parse_trim_damage(contents: bytes) -> JSONObject: ...
def apply_trim_damage_overrides(data: JSONObject, overrides: Sequence[JSONObject]) -> JSONObject: ...
def _encode_trim_damage(data: JSONObject) -> bytes: ...
def create_trim_damage_variant(paths: ProjectPaths, config: Config, *, source: Path | str | bytes | JSONObject | None = None, base_version: str | None = None, overrides: Sequence[JSONObject] = ()) -> ResourceReference: ...
def read_trim_defaults(paths: ProjectPaths) -> JSONObject: ...
def switch_trim_defaults(paths: ProjectPaths, config: Config, *, damage_version: str = "builtin") -> JSONObject: ...
def read_trim_installation(config: Config) -> tuple[JSONObject, dict[str, bytes]]: ...
```

- `parse_trim_damage`: validate exact bytes/JSON, reject duplicate keys/rows, return fresh validated document preserving numeric strings; ValueError for data failures; no I/O.
- `apply_trim_damage_overrides`: validate data/operations, return independent edited document; ValueError for missing rows, duplicate operations, invalid removal or quantities; no writes/mutation.
- `_encode_trim_damage`: validate and emit sorted-key, two-space UTF-8 JSON, ordered rows, terminal LF; no I/O; ValueError for invalid data.
- `create_trim_damage_variant`: require exactly one source or base_version. Path is an absolute safe source, str is JSON content, bytes exact JSON, object is serialized JSON. Snapshot/verify base or source, apply operations, register one immutable material/trim-damage version and provenance using existing backend. Return ResourceReference. ValueError for ambiguity/data/integrity; OSError for files/locks; retained artifacts reported. No default switch.
- `read_trim_defaults`: verify pointer schema and referenced damage bytes; return independent pointer. File/resource reads and existing verification locks only; ValueError/OSError on corruption/missing initialization.
- `switch_trim_defaults`: register/reuse seed or resolve vN, then atomically publish pointer under existing resource-root lock with optimistic prior-pointer comparison. Return verified pointer. Never alter SR default generation. Failed/conflicting publication retains registered versions and raises ValueError/OSError with paths.
- `read_trim_installation`: return profile plus pinned source bytes. Profile fields are exactly schema_version, engine_family, version_text, native_fields and files (relative path/hash/size records). Source keys are `TRIM.exe`, `TRIM.IN`, `Data/TRIM.cfg`, `Data/VERSION`. Reject unsafe/missing/changing files or unparseable required fields with OSError/ValueError. No execution/writes. Unknown family raises an explicit unavailable-mode-map error, not a guess.

### Shared CAB extraction in resolution.py

```python
def resolve_material_correction(
    paths: ProjectPaths,
    config: Config,
    worker: CreatedDirectory,
    correction: JSONObject,
    composition: Sequence[JSONObject],
    bonds: Sequence[JSONObject],
    atomdata: JSONObject,
    defaults: JSONObject,
    resources: JSONObject,
    snapshots: Mapping[str, bytes],
    dependency_inputs: SRDependencyInputs | None,
    completed_entries: list[JSONObject],
    *,
    capture_timeout_seconds: float = 30.0,
    dependency_timeout_seconds: float = 60.0,
    progress: Callable[[JSONObject], None] | None = None,
) -> tuple[JSONObject, list[JSONObject]]: ...

def resolve_sr_inputs(paths: ProjectPaths, config: Config, worker: CreatedDirectory, request: JSONObject, selections: JSONObject, *, capture_timeout_seconds: float = 30.0, dependency_timeout_seconds: float = 60.0, progress: Callable[[JSONObject], None] | None = None) -> ResolvedSRInputs: ...
```

Extract current correction block without changing formulas, fallback rules or reference conditions. Inputs are already pinned selected material/CAB evidence; composition rows have atomic_number, atomic_mass and atom_count; bonds have identifier/count; defaults is the existing recorded SR generation. Return effective correction with full contribution evidence and dependency references. None/supplied returns a copied correction and empty dependencies. Derived may run/publish existing Bragg/core/bond dependencies, append to caller's completed_entries accumulator and invoke progress. Do not mutate other inputs. Preserve current ValueError/OSError/process/interruption behavior and evidence notes. Finite positive timeout/callable checks remain. Existing resolve_sr_inputs signature is unchanged and delegates only its correction block to this helper; its original validation and final resolved schemas remain unchanged.

dependency_inputs may be None for none/supplied correction and must be a verified SRDependencyInputs for derived correction. TRIM reads/pins SRModule and nuclear dependency files only when derivation needs them. The unchanged SR wrapper still returns ResolvedSRInputs and retains its current four-file SR engine bundle, publication/dependency side effects, callbacks, validation, error notes and timeout behavior.

### trim_resolution.py

```python
@dataclass(frozen=True)
class ResolvedTRIMInputs:
    request: JSONObject
    selections: JSONObject
    effective_request: JSONObject
    resolved: JSONObject
    snapshots: tuple[tuple[str, bytes], ...]
    engine_files: tuple[tuple[str, bytes], ...]

def _resolve_damage_selection(paths: ProjectPaths, config: Config, selection: JSONObject, defaults: JSONObject) -> tuple[JSONObject, dict[str, bytes]]: ...
def _construct_trim_layer(layer: JSONObject, atomdata: JSONObject, compounds: JSONObject | None, damage: JSONObject) -> tuple[JSONObject, JSONObject]: ...
def _resolve_damage(atomic_number: int, atomdata: JSONObject, damage: JSONObject, overrides: JSONObject) -> tuple[JSONObject, JSONObject]: ...
def resolve_trim_inputs(paths: ProjectPaths, config: Config, worker: CreatedDirectory, request: JSONObject, selections: JSONObject, *, capture_timeout_seconds: float = 30.0, dependency_timeout_seconds: float = 60.0, progress: Callable[[JSONObject], None] | None = None) -> ResolvedTRIMInputs: ...
def verify_trim_resolution(inputs: ResolvedTRIMInputs) -> JSONObject: ...
```

- `ResolvedTRIMInputs`: generated constructor requires all six fields; stores data only. snapshots is a sorted tuple of safe relative path/byte pairs. engine_files pins `TRIM.exe`, `Data/TRIM.cfg`, `Data/VERSION`, `Data/SCOEF03.dat`; selected effective electronic bytes supply SCOEF03. Nested dictionaries are defensively copied by production/verification APIs; frozen does not imply deep immutability.
- `_resolve_damage_selection`: same version/file/inline and publication contract as existing selection helpers, using TRIM damage parser/operations and pointer. Return resolved-role evidence plus source/effective snapshots. One optional version publication; ValueError/OSError; never changes aliases.
- `_construct_trim_layer`: construct direct/compound composition using selected data; convert mass-based compound proportions with existing rules, apply density/mass/component overrides, resolve damage, return numeric layer plus provenance. Pure; ValueError identifies invalid/missing data and effective component index. Does not derive correction or run native code.
- `_resolve_damage`: independently select three energies with the stated precedence; return values and per-field origins including rule version/input for calculated Surf. Pure. Missing required data raises ValueError only when not supplied by a higher-priority value.
- `resolve_trim_inputs`: validate request/selection/worker, snapshot installed profile, SR generation, damage pointer and all selected sources once, validate all layers before publications/dependency work, publish requested variants once, resolve correction for each layer, flatten, validate and independently replay. Return bundle. Per-layer correction progress adds layer_index to existing dependency events; callback exceptions propagate. Same worker/CAB locks and operation timeouts as SR dependency machinery; main TRIM never starts. Failures retain published versions/dependency evidence and add notes. No main worker runtime/status/export is created.
- `verify_trim_resolution`: validate bundle and all snapshot/engine hashes, replay material edits, precedence, compound conversion, index map, mode mapping and derived CAB arithmetic from recorded contributions; rebuild effective request and both native byte files. Return independent effective request. Reads no installation/resource pointer, starts no process and writes nothing. ValueError for missing, duplicate, unsafe or inconsistent snapshots. Verify recorded dependency identities/hashes and arithmetic without claiming native physics validation.

### trim_storage.py

```python
def write_trim_input_bundle(destination: Path, inputs: ResolvedTRIMInputs) -> Path: ...
```

Require an absolute new destination whose existing parent is safe/link free. Independently verify the bundle before writing. Write into a unique sibling staging directory, flush files, construct manifest, verify hashes and atomically rename to destination without overwriting an existing entry. Return destination. ValueError for invalid bundle/path, FileExistsError for collision, OSError for I/O/unsafe ancestors; preserve and identify any partial staging artifacts. No cleanup of unrelated files, worker locks, main lifecycle or native execution. Distinct destinations can be exported concurrently; same-destination competition yields one success and a collision, not merged output.

## Export format and provenance

```text
<destination>/
  manifest.json
  input/
    request.json
    selections.json
    effective-request.json
    resolved.json
    engine/TRIM.IN
    engine/TRIM.DAT                       event modes only
    resources/<role>/source
    resources/<role>/effective
    installation/TRIM.IN
    installation/TRIM.exe
    installation/Data/TRIM.cfg
    installation/Data/VERSION
    installation/Data/SCOEF03.dat          selected effective electronic bytes
    dependencies/<sha256>.json             required CAB entry snapshots
```

This is a preserved input bundle, not a staged runnable working directory. The future runner will build its own Data/output/restore layout from verified bytes. No TRIMAUTO is exported in this phase because automatic fresh/resume control belongs to execution.

JSON uses UTF-8, sorted keys, two-space indentation and terminal LF. Every exported file except manifest.json is inventoried with relative forward-slash path, SHA-256 and size_bytes. Manifest is exactly `{schema_version:1, engine:"trim", files:[...]}`, sorted by path. It does not recursively hash itself.

ResolvedTRIMInputs.snapshots names are relative to input/; exported resource paths therefore retain the existing selection helpers' input/resources/... manifest convention. resolved.files inventories source/effective/dependency/native-input bytes only, not resolved.json itself, avoiding a recursive hash. The outer manifest additionally inventories all four JSON request/provenance documents. SRModule dependency file identities are recorded with the dependency evidence and do not replace the main TRIM engine_files inventory.

resolved.json is exactly `{schema_version, engine, installation, defaults, resources, layers, atom_index_map, dependencies, native_checks, files}`. schema_version is 1; engine is trim. installation is the pinned profile; defaults records both generation pointers and canonical hashes; resources uses existing resolved-role evidence plus damage; layers contains resolved material provenance, correction evidence and each component's mass/damage origin. dependencies lists snapshotted immutable entry references and native simulation references. files inventories source/effective snapshots and generated input bytes. Native checks are stable objects `{code, path, requested, detail}` reporting unresolved engine behavior, not parameter suggestions or rejection reasons. They cover event-vector/boundary/species/energy coverage, event-count consumption, unverified identifier/quote handling, and observed autosave substitution where applicable. No unverified parameter is silently changed.

Request objects supplied by callers remain unchanged. Once resolution succeeds, exporting/verifying uses only bundle snapshots; changing defaults, sources or installations cannot silently change that bundle. No automatic export, publication of variants without create_version, default switching, retry or deletion occurs.

## Verification and acceptance

Unit tests are meaningful behavioral contracts, not implementation mirrors. All test methods return None, annotate self with the containing TestCase class, take no other arguments, document the fixture/input and expected outcome, and use inherited unittest construction. Planned classes/methods:

- `TestTRIMInputs`: `test_exact_input_bytes`, `test_event_bytes_and_eof`, `test_version_mode_mapping`, `test_layer_component_identity`, `test_zero_binding_override`, `test_confirmed_angle_substitution_rejected`, `test_unknown_limits_preserved`, `test_record_and_matrix_validation`, `test_no_mutation`. Each constructs its fixture within the method; no new setup/helper methods.
- `TestTRIMDefaults`: `test_seed_matches_measured_table`, `test_versioned_variants_and_pointer`, `test_surface_override_remove`, `test_duplicate_and_missing_rows`, `test_installed_native_fields`, `test_integrity_and_concurrent_switch`. Fixtures use temporary directories under .cache; no installed files modified.
- `TestTRIMResolution`: `test_multilayer_direct_and_compound`, `test_surface_precedence`, `test_density_and_mass_overrides`, `test_shared_selections_published_once`, `test_cab_layer_evidence`, `test_replay_without_installation`, `test_tampered_bundle_rejected`, `test_dependency_failure_retains_evidence`. Mock native dependencies; do not start a GUI in unit tests.
- `TestTRIMStorage`: `test_complete_bundle_export`, `test_event_file_presence`, `test_destination_collision`, `test_unsafe_path_rejected`, `test_partial_write_retains_artifacts`.
- Existing `TestSRResolution` class: add `test_shared_correction_preserves_sr_result` with the same annotated no-argument/None contract; no signature changes to existing tests or fixtures.

Run the existing backend/SR suite with warnings as errors, plus new tests, using only `D:\projects\agentic_srim\.venv\Scripts\python.exe`. Check syntax, required docstrings/annotations, absence of implementation comments, documentation links, signature/schema consistency and git diff whitespace.

Then run production-generated inputs in isolated .cache copies for all three installations. Use completed short ordinary-ion runs and bounded event-mode probes where native dialogs apply. Verify actual saved Z/masses/counts/layers/depths/damage values/corrections and requested controls, not exit code alone. Validate both complete explicit and high-level generated inputs. Repeat the 1×100 nm / 10×10 nm Si comparison using generated inputs, fixed seed/conditions and repeated baseline; require agreement with the recorded native behavior, not fabricated exact one-layer/ten-layer equality. Check separate component identities and per-entry overrides. Keep full-cascade timeout and event-mode completion limitations explicit. Hash all source installation inputs before/after and clean only owned processes.

Any implementation change to these contracts, additional helper/function/file or discovered need for a different format is presented for approval before code is changed. Native unsupported values are recorded/reported according to the agreed policy, not converted into speculative public caps.

## Approval record

The user approved the complete proposal, including shared CAB extraction without SR API changes; one shared seven-role selection set; a separate TRIM damage-default pointer alongside existing SR defaults; optional resource-level Surf override plus component precedence; explicit component mass/density overrides; permissive unknown-limit handling; CP1252 input names/ASCII events; and input-bundle export separated from runtime staging.

Implementation began after explicit approval and an announcement. Main TRIM execution remains outside this approved phase.

## Implementation verification, 2026-10-21

All 175 tests passed with warnings treated as errors: 134 SRIM tests, including 29 added TRIM/shared-correction tests, and 41 backend tests. Syntax, module/class/function docstrings, complete argument/return annotations and absence of implementation comments passed for all ten changed Python files. Documentation links, code fences and diff whitespace passed. All three guide examples ran in an isolated project, including replay/export and component Surf 7 taking priority over a selected resource Surf 6.

Production APIs generated and exported inputs for SRIM 2008, 2013 Standard and 2013 Pro. Isolated native runs verified saved atom Z/mass/Disp/Latt/Surf and every layer's name, width, cumulative depth, density and stoichiometry for the ordinary comparison and override cases. The override cases saved mass 29, Disp 17, Latt 0, Surf 7 and density 2.5; the supplied correction 1.05 was preserved in TRIMIN.sav. This checks serialization and native saved values, not independent physical validity of the correction.

For each installation, four 1,000-ion runs covered one 100 nm Si layer, its exact repeat, ten 10 nm layers with distinct atom entries, and a complete explicit request with shared Si entries. Each completed successfully. Seven initial 20-second timeouts were repeated with identical input hashes and a 90-second allowance, without changing parameters. The repeated baseline and the six 100-row numeric profile tables agreed across all three installations. Shared/distinct entries agreed after aggregating recoil columns. One layer versus ten layers retained the earlier native difference:

| Geometry | Mean range, Å | Straggling, Å | Vacancies/ion | Transmitted/1,000 |
| --- | --- | --- | --- | --- |
| 1 × 100 nm | 573.04 | 206.11 | 741.22 | 29 |
| 10 × 10 nm | 575.42 | 203.45 | 739.33 | 35 |

Short full-cascade and monolayer ordinary-ion runs completed in all three installations. Additional five-ion full-cascade runs enabled ranges, backscatter, transmission, sputtering, ions-and-recoils collisions and 1,000 eV EXYZ increments; all three exited normally with five ions saved and the six requested optional output files present. These runs establish control acceptance/file creation for the tested inputs, not nonzero backscatter/transmission yields.

Specified-ion quick runs completed in all three installations; the 2008 specified-ion full run also completed. The bounded 2013 specified-ion full and recoil probes did not establish completion. A separate 2013 Standard recoil probe displayed the native “Special for Neutron/Electron Damage” cascade-output question before initialization. Event formats/mappings pass unit verification, but unattended native completion for every event mode remains unverified.

A separate 2013 Standard probe with embedded quotes in both the calculation and layer names displayed native runtime error 9, “Subscript out of range.” The probes did not isolate which name caused it or establish a universal name limit. Requests remain unchanged and quoted-name checks remain explicit; no speculative rejection or silent sanitization was introduced. Original installation-file hashes remained unchanged.

Supporting evidence is retained under `.cache/trim-implementation/`: `verification-summary.json`, `native-20261021T135249Z/results.json`, `retry-20261021T141237Z/results.json` and `outputs-20261021T142017Z/results.json`, with isolated inputs, bundles, native outputs and saved state. These auxiliary files may be removed by cache cleanup; this section records the durable conclusions.
