# Search native simulation inputs

[Documentation index](../README.md) · [API and formats](../reference/input-search-format.md)

Input search locates project-generated SR/TRIM runs by their verified saved inputs. It provides structured retrieval for agents through Python; no model, embeddings, internet service or CLI is needed. Output values, external imports and NERSC migration are deferred.

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
