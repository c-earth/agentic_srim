# TRIM input format and API

[Documentation index](../README.md) · [Input guide](../guides/trim-inputs.md) · [Validation coverage](validation.md#trim-native-checks)

This reference describes high-level and explicit TRIM requests, version-specific mode codes, pinned resources, and input-bundle replay/export. The linked source validators define the complete nested contracts. Preparation, execution and continuation have their own [execution reference](trim-execution-format.md). Existing SR request and lifecycle schemas remain unchanged.

## Inputs and resources

Both requests use `{"schema_version":1,"engine":"trim","parameters":...}`. High-level parameters are exactly name, mode, projectile, total_ions, random_seed, autosave_ions, outputs, plot, layers and events. Each layer has name, thickness_angstrom, material, correction and overrides. Direct and compound material sources follow the [guide](../guides/trim-inputs.md); sparse component edits use one-based component_index plus mass_amu and/or any damage-energy field. Physical quantities are finite decimal strings accepting scientific notation; rendering expands exponents without rounding. Integer fields reject bool. Repeated species are preserved.

An explicit engine request adds engine_family, native_fields and atoms and supplies full numeric layers with per-atom stoichiometry. Atoms contain atomic_number, mass_amu, displacement_eV, lattice_binding_eV and surface_binding_eV. Each layer and atom must participate positively. The explicit API does no material/default lookup and imposes no packaged-dataset Z ceiling.

Selections have seven keys: atomdata, compound, electronic, nuclear, core, bond and damage. Compound is null only when unused; other roles are objects. Version/file/inline, overrides and create_version match SR philosophy. Shared sources are snapshotted once and requested variants publish once across all layers. Defaults do not switch implicitly. Common aliases come from SR's existing generation; `memory/resources/trim-damage-defaults.json` separately selects material/trim-damage/vN.

The damage table is UTF-8 JSON with schema_version=1, units=eV and 92 ordered rows. Each row requires atomic_number, displacement_eV and lattice_binding_eV and may contain surface_binding_eV. Set/remove operations use `[Z, field]`; only optional Surf can be removed. Precedence is component override → effective damage row → Surf from effective ATOMDATA. Positive sublimation is unchanged; zero gives 2 eV. Record per-field origins and surface_rule_version=1. No layer averaging.

Installed TRIM.IN supplies the legacy Bragg slot, Reminders and stopping selector. All were 0 in the inspected installations; conflicting heading-year labels are not engine identity. Data/VERSION and executable hashes identify the selected engine. Complete explicit requests carry the intended native_fields; high-level resolution always obtains them from the installation.

## Native representation and uncertain bounds

TRIM.IN uses strict CP1252, CRLF, fixed headings, 16 ignored ASCII prefix characters per atom, ordered layer/atom matrices, phases, per-layer correction and per-atom energies. Embedded quotes in names are doubled and flagged for native saved-name verification. A 2013 Standard probe with quotes in both names produced native runtime error 9, “Subscript out of range”; the individual cause and universal bounds remain unresolved. TRIM.DAT exists only for event modes: ten ASCII CRLF headers, description on line eight, ID/eight numeric fields per event and terminal 0x1A. Event descriptions must be ASCII.

The [mode map](trim-input-format.md#mode-codes) covers 2008 codes 1–7 and 2013 codes 1–8. Plot codes 0–5 and collision controls 0/1/2 are explicit descriptive choices. Damage energies and EXYZ increment accept zero. Positive semantic quantities remain positive; seeds/autosave have no speculative lower/upper integer bounds. No unknown count/name/layer/entry/energy caps, vector normalization, event relocation or automatic parameter retries are introduced. Exact 90° incidence is rejected because all tested versions substitute 0°. Native execution checks are recorded rather than presented as established limits.

## Mode codes

Requests use semantic mode names; renderers select the version-specific native
code. Specified-ion and recoil modes require explicit event records.

| Mode | 2008 | 2013 Standard / Pro |
| --- | --- | --- |
| ion-quick | 1 | 1 |
| ion-full | 2 | 2 |
| ion-monolayer | 3 | 3 |
| specified-ion-quick | 4 | 4 |
| specified-ion-full | 5 | 5 |
| recoil-quick | 6 | 8 |
| recoil-full | 7 | 6 |
| recoil-monolayer | unavailable | 7 |

Mode support in the input format does not imply unattended execution or detailed
history availability. See [native coverage](validation.md#trim-native-checks).

## APIs and effects

| Source | Public API | Effects |
| --- | --- | --- |
| [trim_inputs.py](../../src/srim/trim_inputs.py) | validate_trim_request, validate_trim_engine_request, flatten_trim_target, render_trim_input, render_trim_events | Pure validation/construction; bytes or None for absent events; ValueError on malformed input. |
| [trim_defaults.py](../../src/srim/trim_defaults.py) | parse_trim_damage, apply_trim_damage_overrides | Pure; fresh JSON; ValueError on bad tables/operations. |
| [trim_defaults.py](../../src/srim/trim_defaults.py) | create_trim_damage_variant | Exactly one source/base_version; immutable ResourceReference; never changes defaults. |
| [trim_defaults.py](../../src/srim/trim_defaults.py) | read_trim_defaults, switch_trim_defaults, read_trim_installation | Verified file reads; explicit pointer switch only in switch_trim_defaults. Partial publications are retained and identified. |
| [trim_resolution.py](../../src/srim/trim_resolution.py) | ResolvedTRIMInputs, resolve_trim_inputs | Copied intent and pinned byte tuple inventories; requested publications and optional SR/CAB dependency runs; no main TRIM run. |
| [trim_resolution.py](../../src/srim/trim_resolution.py) | verify_trim_resolution | Offline reconstruction from bundle only; no file/alias/process access. |
| [trim_storage.py](../../src/srim/trim_storage.py) | write_trim_input_bundle | New absolute destination with safe existing parent; verified staging and rename; no overwrite or cleanup. |

The source modules linked above define the full typed signatures, inputs, outputs, side effects and error contracts. Invalid data/integrity raises ValueError; filesystem/lock errors propagate OSError; collision raises FileExistsError. Dependency/callback/interruption errors propagate with evidence notes. Worker/CAB dependency leases and finite positive operation timeouts follow existing SR machinery. Layers resolve sequentially; progress events receive layer_index after each layer completes. Shared derived-correction events use `calculated`, `cached`, `resolved`, `fallback` and `skipped` states; counters reset per layer and include each bond plus a Bragg/core pair per composition entry. A successful nonempty layer contribution plan reaches its fixed total, including unused skipped work; this is not whole-runtime completion. Explicit correction modes emit no dependency events.

The shared resolve_material_correction in [resolution.py](../../src/srim/resolution.py) extracts SR's original correction block without changing its formulas, omissions/fallbacks, fixed reference conditions or SR API. A derived correction may recover missing entries; none/supplied does not execute dependencies. The ResolvedTRIMInputs dataclass stores values only and does not deeply freeze nested maps; consumers copy and independently verify them.

## Bundle layout

```text
destination/
  manifest.json
  input/
    request.json
    selections.json
    effective-request.json
    resolved.json
    engine/TRIM.IN
    engine/TRIM.DAT                         event modes only
    resources/<role>/{source,effective}
    installation/TRIM.IN
    installation/TRIM.exe
    installation/Data/{TRIM.cfg,VERSION,SCOEF03.dat}
    dependencies/<sha256>.json
```

SCOEF03.dat contains the selected effective coefficients. Other installation snapshots retain exact source bytes. JSON uses UTF-8, sorted keys, two-space indentation and terminal LF. Input-relative snapshots are inventoried in resolved.files; the outer manifest also hashes all four JSON documents, excludes itself and uses sorted path/SHA-256/size records.

resolved.json has exactly schema_version, engine, installation, defaults, resources, layers, atom_index_map, dependencies, native_checks and files. Defaults records both pinned pointers and optional SR dependency engine identities. Layer records contain numeric material, composition/density/mass/damage origins, corrections and native dependencies. CAB entry snapshots validate transitive recipes, hashes and arithmetic offline. Existing native SR/CAB simulation references are retained; their full raw outputs remain in the permanent SR records. Input reconstruction needs only this bundle; preserve those referenced records for full native evidence.

This is an exported input bundle, not a runnable working directory. No TRIMAUTO, main worker runtime, lifecycle metadata or outputs are produced. Main TRIM execution, native error monitoring, saved-value verification, queueing and resume are implemented through the separate [execution APIs](trim-execution-format.md). Internal consistency verification is not physical validation or authentication of externally supplied data.

Native verification covered completed ordinary-ion cases in all three versions, optional-output creation, overrides and repeated layer comparisons. Some event probes stopped at native dialogs/timeouts; full unattended event-mode completion is unverified. See the [verification record](validation.md#trim-native-checks) for results and limits.
