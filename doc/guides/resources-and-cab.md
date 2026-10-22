# Materials, resources and CAB

[Documentation index](../README.md) · [First calculation](getting-started.md) · [API reference](../reference/api.md) · [Formats](../reference/data-formats.md)

Examples below use `paths`, `config` and `worker` initialized as in the first-calculation guide. They demonstrate separate operations; run only the operations needed for your workflow.

## Defaults and concrete versions

`switch_sr_defaults(paths, config)` validates the configured installation, imports resources and atomically publishes one coordinated default generation. `read_sr_defaults(paths)` returns it without importing or recovering anything. A generation selects ATOMDATA, compounds, electronic coefficients, nuclear coefficients, cores, bonds and Bragg data together.

Each resource has an independently allocated `vN`; version numbers for different roles need not match. A concrete `ResourceReference` identifies category, name, version, payload SHA-256 and byte count. A `CABEntryReference` separately identifies an immutable calculated entry. Adding an entry does not change its descriptor payload hash.

`default` is a selector resolved to concrete references. New versions and per-simulation overrides do not change that selector. Compound-only installation changes replace the compound default while retaining the numerical recovery baseline; GUI-only changes can change the core/bond baseline while retaining a matching Bragg recipe. Identical numerical bytes can be reused across installation paths.

Compatibility normalization during installation import prefers `VERSION`, otherwise `_VERSION`; fixes only row 37 `Ru Rubidium` to `Rb Rubidium`; inserts a missing name/density comma; and removes one trailing composition comma. Parsers themselves remain strict. Existing malformed immutable imports require reimporting defaults; there is no migration that rewrites them. User material imports are strictly parsed and do not automatically apply these installation fixes.

## Search the effective compound database

```python
"""Search the current compound defaults without changing resources or running engines."""

from src.backend.resources import ResourceReference, verify_resource
from src.srim.defaults import read_sr_defaults
from src.srim.material import parse_atomdata, parse_compounds, search_compounds

generation = read_sr_defaults(paths)
compound_ref = ResourceReference(**generation["resources"]["compound"])
atom_ref = ResourceReference(**generation["resources"]["atomdata"])
compounds = parse_compounds(verify_resource(paths, compound_ref).read_bytes())
atoms = parse_atomdata(verify_resource(paths, atom_ref).read_bytes())

matches = search_compounds(compounds, name="water", name_mode="contains")
ratio_matches = search_compounds(
    compounds, composition={"H": "2", "O": "1"},
    composition_mode="ratio", atomdata=atoms
)
for record in matches:
    print(record["record_index"], record["name"], record["composition_basis"])
```

Exact name matching is case sensitive; `contains` uses Unicode casefold and checks both display and internal names. Name and composition filters combine with AND. `contains` composition requires a subset of elements; `elements` requires the same element set; `ratio` additionally compares exact atomic proportions. Results retain effective order and duplicate names. A no-match result is an empty list.

Native `%` internal names mark mass proportions. Their source values remain mass values. Target construction divides each proportion by the effective natural atomic mass under the recorded Decimal policy. Ratio searches use exact rational arithmetic instead. A mass-based ratio candidate requires ATOMDATA; missing masses raise an error. Any nonzero bond count on a mass-based record is rejected because an atomic bond-count scale has not been supplied.

Apply compound edits before searching if your run uses overrides. Compound set/remove keys refer to the input database's original one-based indices; append operations follow patch order. Search results and target selectors use the resulting effective indices. The returned `index_map` records removed, retained and appended identities.

## Local changes and immutable variants

Every selection has exactly one of `version` or `source`, plus explicit `overrides` and `create_version`. A local override with `create_version=False` is snapshotted for that simulation. `True` additionally registers the effective bytes as a new immutable version. Successful publications remain if a later dependency fails.

```python
"""Change carbon mass locally in a fresh set of direct-target default selections."""

selections = {
    role: {"version": "default", "overrides": [], "create_version": False}
    for role in ("atomdata", "electronic", "nuclear", "core", "bond")
}
selections["compound"] = None
selections["atomdata"]["overrides"] = [
    {"operation": "set", "key": "C", "value": {"natural_mass_amu": "12.0"}}
]
```

ATOMDATA allows named field edits on its 92 existing rows. Atomic number and symbol cannot change; removal is forbidden. Updating one quantity does not recalculate densities or other fields. Direct/compound SR targets obtain natural masses from this effective table; explicit projectile mass is unaffected.

To create reusable sparse CAB strengths independently of a simulation:

```python
"""Publish user strengths without changing defaults or recovering missing entries."""

from src.srim.cab import create_cab_variant, load_cab

core_ref = create_cab_variant(paths, config, "core", source={"C": "6.2"})
bond_ref = create_cab_variant(paths, config, "bond", source={"H-C": "1.5"})
values = load_cab(paths, "core", core_ref.version)
print(values)
```

These illustrative strengths demonstrate API syntax, not calibrated physical values. Core keys accept atomic numbers or canonical case-sensitive symbols; bond keys accept strings such as `H-C`, `C=C`, `N#N`, or endpoint/type tuples in Python mapping APIs. Endpoints normalize into ascending order; equivalent duplicate keys are rejected. In JSON selection overrides use string bond keys. Values are finite nonnegative decimal strings in stored version-two payloads. Legacy numeric version-one payloads remain readable.

`create_cab_variant(..., base_version="default")` freezes only currently available entries and their provenance; it does not trigger recovery. `load_cab` also reads only available strengths. A frozen user dataset is interpreted as user data even if based on defaults. Missing user cores use the effective elemental Bragg stopping; missing user bonds use zero. Removing a default core explicitly uses that Bragg fallback; removing a default bond uses zero without recovery.

Use `create_material_variant` for atomic/compound imports and edits. Use `create_electronic_variant` and `create_nuclear_variant` for coefficient imports or complete-row replacements. Electronic replacement keys identify an element; nuclear keys are `(lower_Z, higher_Z)` and reversed pairs are rejected. Neither supports row removal. Raw coefficient source APIs store opaque supplied bytes; selection resolution validates table shape/order before running SR. These checks do not validate the physical interpretation of every coefficient.

Strings supplied to resource APIs are content, not filesystem paths. Use `Path(...)` for a file source. JSON selections use an absolute local file path or inline kind-specific data; URLs are not supported. Original sources and registered bases are preserved.

## Derived CAB correction

For a direct target, provide positive atomic proportions and an explicit bond-count mapping. No chemistry or valence inference is performed. `correction={"mode": "derived"}` requests the calculation; a caller-supplied derived factor is rejected.

With at least one positive-count bond of nonzero selected strength, the factor is:

```text
f = (sum(atom_count * core_strength) + sum(bond_count * bond_strength))
    / sum(atom_count * elemental_electronic_stopping)
```

The calculation uses precision 28 and `ROUND_HALF_EVEN` in a local Decimal context. Only common rescaling of both atom and bond counts preserves the result. The final factor must be positive and finite. If no bond has both positive count and nonzero strength, the result is exactly `1.0`; unused core/Bragg recovery is skipped. Zero counts do not trigger recovery. Valid stored bond types outside the default engine's 18 contribute zero when no selected value exists.

The denominator uses effective material/coefficient inputs in a hydrogen gas reference simulation: projectile isotope mass from ATOMDATA, energy `125 * hydrogen_mass` keV, target natural mass/gas density, units 7, correction one, electronic stopping only. A matching Bragg recipe is selected automatically; users do not supply Bragg values or choose its version.

Missing active default bonds use the original all-default baseline, fix the H core convention at zero, estimate the smallest positive integer threshold count from the first reading, and verify it without searching/retrying. Non-H cores use 18 separate one-bond estimates and retain their mean and spread. Default recovery is distinct from the effective main denominator when users override material/coefficient inputs.

## Targeted recovery and progress

```python
"""Recover selected default entries, preserving successful work for later reuse."""

from src.srim.cab_recovery import populate_default_cab

references = populate_default_cab(
    paths, config, worker, "bond", entries=["H-C"], progress=print
)
```

This operation can launch SRModule dependencies and visible SR GUI windows. It uses Windows UI Automation through PowerShell and closes only its owned processes. `entries=None` means all 18 bonds or all 92 cores, including the fixed H convention; choose a subset for targeted work. Full non-H core recovery is much larger and has not been comprehensively validated in the recorded native checks.

Bulk APIs report `{kind, entry_id, state, completed, total}` after each selected entry, with state `cached` or `calculated`, and stop on the first error. A callback exception propagates after completed work is retained. A later call reuses successful matching entries; this is resume through cache reuse, not automatic retry.

For an explicitly selected inactive default version, preparation permits existing entries but rejects missing recovery. The direct `resolve_default_core`/`resolve_default_bond` APIs also permit verified inactive cache entries but require the supplied generation to equal the verified active defaults before missing dependency work and each capture. A concurrent default switch stops the next checked operation; completed evidence remains reusable. Checks do not exclude a switch after admission or prevent publication of an already admitted pinned result.

Derived-correction progress reports every bond and Bragg/core contribution with states `calculated`, `cached`, `resolved`, `fallback` or `skipped`. Its fixed total is `len(bonds) + 2 * len(composition)`; successful contribution resolution reaches that total, including skipped unused Bragg/core work. `none`/`supplied` modes emit no dependency events. Later evidence verification and preparation may still fail; callback failures propagate with evidence retained. [The review](../reviews/non-trim-review.md) preserves the historical reproductions.

## Evidence and retention

New calculated entries depend on exact evidence in `memory/srim/` and resource versions in `memory/resources/`. CAB GUI captures receive ordinary allocated simulation records with engine identity `sr-capture`; exact native inputs and baseline snapshots live under `input/cab/`, and the final capture runtime is preserved under `output/raw/runtime/`. CAB entries reference these permanent records. Reuse verifies linked files and hashes; missing/corrupt evidence is an error, not a cache miss that triggers regeneration.

Keep permanent records and resources together when copying or archiving a workspace. Historical entries can still reference `worker/worker_N/sr/.capture-*/`; they remain readable and are not automatically rewritten. Batch cleanup detects these references and retains the affected worker. The generic deletion API protects busy workers but does not check CAB references; deleting permanent dependency records can still break entries. There is no general reference-aware garbage collector or historical installation archive.

Capture and engine failures retain diagnostics, input hashes and available outputs. Published versions and completed entries survive later failures. Workers are execution space: `run_sr_batch` cleans verified runtime folders, keeping empty workers for reuse, only after all queued/running task calls finish. It retains workers containing unpreserved, unfinished, unsafe, unrelated or historically referenced files, and reports cleanup separately from task outcomes. Direct runs/recovery preserve evidence without automatic deletion; `cleanup_sr_batch(paths, worker_paths)` can check and clear their verified runtime folders explicitly. Permanent records/resources are not evicted. Failed/publication staging directories can remain for inspection; normal calculated-entry publication also leaves empty staging directories.

Different workers can execute SRModule concurrently. One worker has one active lease, while metadata/resource locks cover only shared filesystem operations. GUI captures remain serialized by a dedicated capture lock.
