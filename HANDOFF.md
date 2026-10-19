# Handoff

## CAB resource APIs

- Added `src/srim/cab.py`: `register_cab_default(paths, config, kind, source)`, `create_cab_variant(paths, config, kind, *, source=None, base_version=None, updates=None)`, and `load_cab(paths, kind, version)`.
- Kind is core or bond. Explicit baselines become cab/<kind>/default; variants use the existing independent max-vN-plus-one allocation and immutable resource publication.
- Sources accept local JSON Paths, UTF-8 JSON str/bytes, or Python mappings. Imports normalize rather than preserve raw JSON bytes. Update mode requires an existing version and nonempty mapping, adds/replaces keys, and leaves other entries unchanged; no deletion is included.
- Python core keys accept atomic numbers 1 through 118 or case-sensitive standard symbols, normalized to integers. Bond keys accept symbol strings such as Cl#C or tuples (element, element, bond); endpoints sort by atomic number and bond types are -, =, #.
- Values normalize to finite nonnegative floats, excluding bool. Zero and sparse/empty datasets are valid; no H entry is inserted automatically.
- Stored UTF-8 JSON has schema_version=1 and entries. Core entries are {element: int, value: float}; bond entries are {elements: [int, int], bond: str, value: float}. Exact fields are enforced; arrays sort by canonical key, formatting uses two spaces and a final newline.
- Equivalent duplicate keys in imports/updates and duplicate JSON object fields are rejected. Reversed JSON bond endpoints normalize on import. Updates may replace keys in the base. No checks require endpoints to exist in any core dataset.
- Loading returns fresh Python dictionaries and checks resource integrity plus read bytes. File inputs reject links and verify read bytes against their hash.
- No experimental baseline was invented or registered. Existing engine changes and the untracked electronic/dafault test resource remain intact and uncommitted.

## Verification

- All 67 tests pass using the project interpreter with -B -W error: 38 backend and 29 SRIM tests.
- Seven new CAB tests cover source forms, canonicalization, equivalent/JSON duplicates, immutable baselines, sparse/empty data, zero/nonfinite/negative values, updates, independent sequences, integrity, links, contention, and failed publication/retry.
- Module/class/function docstring, argument/return annotation, and no-comment checks passed for both new CAB Python files.

## Remaining work

Actual CAB strength baselines must be derived from later SR.exe experiments. CAB calculation, worker runtime copying, and input preparation remain outside this change.
