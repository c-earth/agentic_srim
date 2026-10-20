# Requests, resources and stored data

[Documentation index](../README.md) · [First calculation](../guides/getting-started.md) · [API reference](api.md)

This page describes the current non-TRIM formats and their boundaries. Source validators remain the executable definitions. The [review](../reviews/non-trim-review.md) identifies differences between the historical design and implementation.

## Numeric and identity conventions

- JSON numbers used for atomic numbers, indices, versions and counts of rows are actual integers; bool is rejected in these fields.
- Engine request quantities use positive strings matching `[0-9]+(?:\.[0-9]+)?`: no sign, exponent, whitespace or leading decimal point. Examples: `"1"`, `"0.001"`. There is no implicit unit conversion of request energies: they are keV.
- Material/strength numeric strings additionally accept signed scientific decimal syntax and leading decimal points, subject to the field's positive/nonnegative constraint. Source spellings are retained. Constructed target quantities and derived engine factors are converted to fixed-point strings before rendering. Explicit projectile, energy and supplied-factor fields still need engine syntax.
- Computed CAB values use local Decimal precision 28 and `ROUND_HALF_EVEN`. Ratios used for compound search compare exact rational quantities. Printed engine results remain strings; they are not binary floats or a claim of greater physical precision.
- Element keys use integers 1–118 or canonical case-sensitive symbols. SR calculations support Z 1–92. Bond types use `-`, `=`, `#`; endpoints normalize into ascending Z.
- New concrete resource versions are `v1`, `v2`, ... independently for each category/name. Existing gaps are not filled. Resource identifiers use lowercase alphanumeric segments separated by hyphens, at most 64 characters, excluding Windows device names.
- Hashes are lowercase SHA-256 over exact bytes, paired with byte counts. Stored paths use normalized relative forward-slash names within the owning record/worker. Source file selections require absolute local paths. Links/junctions and unsafe path components are rejected by filesystem boundaries.

## Version-two unresolved request

The exact envelope is `{"schema_version": 2, "parameters": {...}}`. Parameters have exactly `projectile`, `target`, `correction`, `stopping_units`, `energy`.

```json
{
  "schema_version": 2,
  "parameters": {
    "projectile": {"atomic_number": 1, "mass_amu": "1.008"},
    "target": {
      "source": "direct",
      "phase": "solid",
      "density_g_cm3": "1.0",
      "elements": [
        {"atomic_number": 1, "stoichiometry": "2"},
        {"atomic_number": 8, "stoichiometry": "1"}
      ],
      "bonds": {"H-O": "2"}
    },
    "correction": {"mode": "derived"},
    "stopping_units": 7,
    "energy": {"mode": "list", "values_keV": ["126", "1000", "126"]}
  }
}
```

Direct target fields are `source`, `phase`, `density_g_cm3`, `elements` and, for derivation, required `bonds`. None/supplied intent can omit `bonds`; any provided mapping is still validated. Element entries have exactly `atomic_number` and `stoichiometry`, with unique atomic numbers. Phase is explicitly `solid` or `gas`; it is not inferred from compound categories. Positive counts need not sum to one or 100. Bond counts are nonnegative strings; there is no bond/valence inference or plausibility validation.

A compound target instead has exactly:

```json
{"source": "compound", "record_index": 1, "phase": "solid"}
```

`record_index` is a positive one-based index in the selected effective compound database. It is not a stable cross-installation identifier. Density, composition and bonds come from that record; masses/names come from effective ATOMDATA.

Correction forms are:

```json
{"mode": "none", "applied_factor": "1.0"}
```

```json
{"mode": "supplied", "applied_factor": "1.05", "source": "measurement", "reason": "User calibration"}
```

```json
{"mode": "derived"}
```

None requires the exact string `"1.0"`. Supplied requires a positive engine-format factor and nonblank source/reason. Unresolved derived accepts no caller factor. All correction modes still require complete selections.

Energy is either an ordered list of 1–1,000 positive string values (duplicates allowed), or `{"mode":"range","minimum_keV":"1","maximum_keV":"1000"}` with minimum strictly less than maximum. The range has no user-specified increment.

## Selection intent

Selections have exactly `atomdata`, `electronic`, `nuclear`, `core`, `bond`, `compound`. `compound` is null for direct targets and an object for compound targets. The other five are always objects.

Each object has one source mode and explicit edit/publication options:

```json
{"version": "default", "overrides": [], "create_version": false}
```

```json
{"source": {"kind": "file", "path": "D:\\data\\strengths.json"}, "overrides": [], "create_version": false}
```

```json
{
  "source": {
    "kind": "inline",
    "data": {"schema_version": 2, "kind": "core", "units": "eV/(1E15 atoms/cm2)", "entries": []}
  },
  "overrides": [],
  "create_version": true
}
```

Versions are `default` or concrete `vN`. File selections do not accept URLs. Inline ATOMDATA/coefficient data is native ASCII text; compound/strength data can be text or an external JSON object. Native compound files with CP1252 characters are best supplied as a file source or bytes through Python APIs; inline strings are encoded as UTF-8. There is no Bragg selection key or manual Bragg import flag.

`create_version=false` saves effective changes only in the run's evidence. `true` publishes a new immutable version and records it without changing defaults. Original sources and base versions are not edited. Use string element/bond identifiers for JSON keys; Python tuple keys are available in the standalone CAB mapping APIs, not JSON selection documents.

## Override operations

| Role | Operations and key | Value |
| --- | --- | --- |
| ATOMDATA | `set`, element key | Nonempty map of editable field names to values |
| Compound | `set`/`remove`, original one-based index; `append` without key | Complete external record for set/append |
| Core | `set`/`remove`, element key | Nonnegative decimal string for set |
| Bond | `set`/`remove`, bond string | Nonnegative decimal string for set |
| Electronic | `set`, element key | Complete ASCII row without CR/LF |
| Nuclear | `set`, `[lower_Z, higher_Z]` | Complete ASCII row without CR/LF |

Set objects have exactly `operation`, `key`, `value`; remove has `operation`, `key`; append has `operation`, `value`. Equivalent duplicate keys and removing absent entries fail. Coefficients/ATOMDATA cannot remove rows. Compound edits use original indices before appends; the resulting indices and mapping determine search/target selection.

ATOMDATA fields are `atomic_number`, `symbol`, `name`, `isotope_mass_number`, `isotope_mass_amu`, `natural_mass_amu`, `solid_density_g_cm3`, `solid_atomic_density_cm3`, `fermi_velocity`, `sublimation_energy_eV`, `gas_density_g_cm3`, `gas_atomic_density_cm3`. Atomic number/symbol are fixed. Mass number is a positive integer. Other numerical fields are positive strings except sublimation energy, which may be zero. Name is a printable ASCII token of 1–13 characters excluding quotes. An edit does not recalculate related quantities.

## Material and strength payloads

Native ATOMDATA has two header lines, exactly the expected 92 ordered rows with 12 columns, and retained trailing bytes. Electronic tables have 92 ordered rows with 95 quote-aware fields. Nuclear tables have all 4,278 pairs ordered by first/second Z, six fields per row, with `1 <= first <= second <= 92`. Coefficient field meanings beyond row identity/structure are not fully interpreted.

Native compounds use starred display names, a quoted internal-name/composition line and 18 bond columns. Native records support 1–8 unique elements. A leading `%` inside the internal name marks mass proportions. The strict parser requires the name/density comma and rejects a trailing composition comma; installation import normalization handles the two known defects before parsing. Source text, blank lines, comments, spans and hashes are retained as provenance. Blank lines are skipped when locating the numerical lines; this does not establish that the native GUI interprets anomalous records identically.

Extended external compound JSON has exactly `schema_version=1` and `records`, an ordered list of these records:

```json
{
  "name": "Water",
  "composition_basis": "atomic",
  "density_g_cm3": "1.0",
  "elements": [
    {"atomic_number": 1, "stoichiometry": "2"},
    {"atomic_number": 8, "stoichiometry": "1"}
  ],
  "bonds": {"H-O": "2"}
}
```

For basis `mass`, replace `stoichiometry` by `mass_proportion` in each element; every bond count must be zero. Density/proportions must be positive; names are nonempty without control characters. Records may share names. Extended storage supports 1–118 unique elements per record, but SR target construction rejects Z above 92, yielding at most 92 unique elements for unresolved direct/compound calculations. An empty external records list parses as a database, but cannot satisfy an indexed target.

Core payload example:

```json
{"schema_version": 2, "kind": "core", "units": "eV/(1E15 atoms/cm2)", "entries": [{"element": 6, "value": "6.2"}]}
```

Bond payload example:

```json
{"schema_version": 2, "kind": "bond", "units": "eV/(1E15 atoms/cm2)", "entries": [{"elements": [1, 6], "bond": "-", "value": "1.5"}]}
```

Values here illustrate serialization only. Version-one strength payloads have exactly `schema_version=1` and numeric-valued `entries`; they decode decimal tokens without introducing binary-float rounding. New payloads use strings. Empty sparse datasets are valid. Calculated default descriptors are a separate format; their payload is not a strength-entry list.

## Effective engine request and SR.IN

Resolution returns a complete numeric target with exactly `phase`, `density_g_cm3`, `elements`. Each engine row has `atomic_number`, `name`, `stoichiometry`, `mass_amu`. Low-level engine validation permits 1–100 rows with distinct `(atomic_number, mass)` identities, including separate isotope rows; this differs from unresolved targets' unique-element restriction.

Effective derived correction has exactly `mode="derived"`, `applied_factor`; full evidence belongs in `resolved.cab`. None/supplied correction retains its fields. Legacy schema-version-one numeric none/supplied requests remain valid for rendering/parsing and low-level records; public resource resolution/preparation requires version two.

`SR.IN` is ASCII with CRLF and fixed output filename `stopping.txt`. It serializes projectile, phase (`solid=0`, `gas=1`), density, applied factor, ordered target rows, stopping-unit code and energy controls. List mode emits `0 0`, one line per energy and final `0`. Range mode emits minimum/maximum on one line. The prepared SRModule runtime contains exactly `SRModule.exe`, `VERSION`, `SR.IN`, `SCOEF03.dat`, `SNUC03.dat` before execution.

## Units and results

Stopping-unit labels are exactly the parser's native labels:

| Code | Label |
| --- | --- |
| 1 | `eV/Angstrom` |
| 2 | `keV/micron` |
| 3 | `MeV/mm` |
| 4 | `keV/(ug/cm2)` |
| 5 | `MeV/(mg/cm2)` |
| 6 | `keV/(mg/cm2)` |
| 7 | `eV/(1E15 atoms/cm2)` |
| 8 | `L.S.S. reduced units` |

Parsed output has exactly `schema_version=1`, `energy_mode`, `source`, `stopping_units`, `rows`. `source` has `filename="stopping.txt"`, `sha256`, `size_bytes`; units has `code`, `label`.

List rows have `energy={value,unit="keV"}`, `electronic_stopping`, `nuclear_stopping`, `requested_energy_keV`. They preserve requested order and duplicates. Printed-energy matching permits half the last printed place plus `abs(requested) * 1e-6` for engine rounding.

Range rows have `energy={value,unit}`, electronic/nuclear stopping strings, and `projected_range`, `longitudinal_straggling`, `lateral_straggling`, each `{value,unit}`. Accepted energy units are eV/keV/MeV/GeV; lengths are A/um/mm/cm/m. No automatic common-unit conversion is returned. The conversion footer is validated but is not emitted as a separate conversion-factor table. Parsing is a syntax/units/row check with stronger list-energy matching; it does not fully verify physical header identity or range bounds.

## Persistent layout and lifecycle

```text
<project>/
  settings.toml
  memory/
    srim/srim_YYYYMMDD_HHMMSS_N/
      metadata.json
      input/
        request.json
        selections.json
        effective-request.json
        resolved.json
        engine/{SRModule.exe,SR.IN,VERSION,SCOEF03.dat,SNUC03.dat,runtime.json}
        resources/<role>/{source,effective}
        cab/{native/,defaults.json,baseline.json}  # CAB capture records
        continuation/
      output/
        raw/{stopping.txt,stdout.bin,stderr.bin,execution.json}
        raw/runtime/              # exact final working-directory copy
        parsed/result.json
    post/
    resources/
      defaults.json
      baselines/<baseline-id>.json
      material/{atomdata,compound}/vN/{payload,manifest.json}
      engine/{electronic,nuclear}/vN/{payload,manifest.json}
      cab/{core,bond,bragg}/vN/
        payload
        manifest.json
        entries/<dependency-key>/<entry-id>.json
  case/
  worker/worker_N/
    sr/<simulation-id>/{SRModule.exe,VERSION,SR.IN,SCOEF03.dat,SNUC03.dat}
    sr/<capture-simulation-id>/
      SR.exe
      SR.exe.manifest
      SR.IN
      Data/{ATOMDATA,Compound.dat,SCOEF03.dat,SNUC03.dat,VERSION}
      correction.json
      manifest.json
      stdout.bin
      stderr.bin
    trim/
```

The empty `trim/` and continuation directories are part of shared existing layout; no TRIM workflow is described here. Persistent `.records.lock`, `.deleted/`, and retained staging artifacts may also exist. Case/post directory allocation exists without an implemented postprocessing pipeline.

Ordinary record names use configured timestamps plus the first available positive suffix; tombstones permanently reserve deleted non-worker names. Workers use `worker_N`, have no timestamp and may reuse a deleted suffix. Allocation is separate from simulation initialization.

Lifecycle is `created -> running -> succeeded` or `created/running -> failed`. Terminal updates are rejected. Preparation records the original/effective inputs before main execution. Start seals/verifies input inventory; success requires unchanged inputs; failure can still inventory evidence after input damage. Raw output may exist for failed runs, but parsed result is published only after successful execution/integrity/parsing. Expected execution failures return failed metadata; errors during preservation/finalization can raise instead.

Metadata version two has exactly `schema_version`, `id`, `engine`, `resources`, `status`, `created_at`, `started_at`, `finished_at`, `continuation`, `inputs`, `outputs`, `failure`, `entry_references`, `defaults_generation`. SR continuation is null. Timestamps have explicit offsets; absent start/finish timestamps reflect lifecycle state. Failure is null except failed records, where it is `{message,exit_code}`. Input/output inventory entries are `{path,sha256,size_bytes}`.

Worker execution locks live at worker/.locks/worker_N/.records.lock, separate from removable worker directories. GUI captures use worker/.locks/capture/.records.lock. The root worker/.records.lock coordinates allocation/deletion and lease acquisition. CAB GUI records use the same simulation basename and lifecycle with engine name sr-capture; their diagnostic request/resolution use schema version one, with exact native files under input/cab/native and defaults/baseline JSON snapshots under input/cab. Their parsed result is the captured correction document (compound, correction, process_id, recorded_at). New capture evidence uses kind=simulation and paths below output/raw/runtime; historical kind=worker evidence remains readable.

Resolved version-two documents have exactly `schema_version`, `parameters`, `sources`, `resources`, `cab`, `defaults`, `target_provenance`, `dependencies`. `parameters` is effective numeric input, `sources` identifies executable/version, and each role records `reference`, `base`, `source`, `overrides`, `effective`, `published`. Source/effective manifests point into the saved input tree. `defaults` wraps the pinned generation plus its canonical hash/size. Target provenance retains original/effective record identity, composition conversion masses and mapping. Verification reconstructs these layers without re-resolving defaults or running calculations.

## Resource and calculated-entry evidence

Ordinary references have exactly `category`, `name`, `version`, `sha256`, `size_bytes`. New resource manifests have `schema_version=2`, `sha256`, `size_bytes`, `registered_at`, `provenance`, `reuse_key`. Legacy version-one manifests retain the four fields without provenance/reuse key. Registration provenance records `origin`, `source`, `base`, `changes`, `copied_entries`, `baseline_id`, `procedure` with explicit nulls where appropriate. Source has `kind`, `origin`, `sha256`, `size_bytes`.

`defaults.json` has `schema_version=1`, `baseline_id`, `resources` with exactly seven coordinated concrete roles. Numerical baselines exclude installation path and compound bytes but include executable/default numerical inputs, procedure identities and generated capture assets. Compound-only changes therefore preserve the numerical baseline. The canonical pointer's hash/size is recorded outside the pointer in simulation evidence.

Calculated descriptor payloads have `schema_version=1`, `kind`, `origin`, `baseline_id`, `procedure`, `units`, `base`, `dependency`. Default core/bond descriptors bind the recovery baseline; Bragg binds effective input bytes/procedure and has a null baseline. Dependencies have `schema_version`, `kind`, `procedure`, `files`, `entries`. File identities contain `role`, `sha256`, `size_bytes`; entry references have `dataset`, `dependency_key`, `entry_id`, `sha256`, `size_bytes`.

Stable element entry IDs are `z001` through `z092`. Bond IDs combine ordered endpoints and `s`/`d`/`t`, such as `z001-z006-s`. A calculated entry has `schema_version=1`, `kind`, `entry_id`, `value`, `units`, `dependency`, `simulations`, `captures`, `calculation`. Values are nonnegative strings (strictly positive for Bragg); units are always `eV/(1E15 atoms/cm2)`.

Each simulation evidence object has `simulation_id`, `result`, `raw`. Capture evidence has `manifest`, `correction`, `inputs`, `stdout`, `stderr`. Individual file evidence has `kind` (`simulation` for new records; `worker` remains a legacy format), `id` (owner), `path` (relative to owner), `sha256`, `size_bytes`. Capture manifests retain before/after input hashes, correction, owned process ID, timestamps, cleanup and error state. H core is the explicit zero convention without a simulated/captured estimate.

Stored recovered-default source snapshots contain a pinned available-entry view (`schema_version`, `dataset`, `entries`, `values`); their hash is not the descriptor payload hash. Local recovered-default effective snapshots are overlays (`schema_version`, `dataset`, `overrides`). Frozen user copies materialize available values and retain copied references. Each used calculation pins its exact entry evidence, so later cache growth does not redefine the run.

Generated defaults, descriptors, strength payloads and entries use canonical UTF-8 JSON with two-space indentation, sorted keys and final LF. Native source and engine output bytes retain their own formats. Some diagnostic/runtime JSON writers do not sort keys; integrity always hashes their exact saved bytes rather than assuming universal canonicalization. Preserve linked permanent dependency/capture records with their resources. Batch cleanup verifies runtime copies before clearing working files, keeps empty workers for reuse and retains legacy worker references; there is no general garbage collection for permanent records/resources.
