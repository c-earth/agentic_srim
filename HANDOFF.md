# Handoff

## Resource layout update

- Resources now use `memory/resources/<category>/<name>/<version>/{manifest.json,payload}`. Subcategory was removed from resource APIs, references, callers, and simulation reference validation.
- Each version stores independent bytes. The manifest retains `schema_version: 1`, `sha256`, `size_bytes`, and timezone-aware `registered_at`. There is no version index or hash directory.
- CAB validation requires `cab/core` for core datasets and `cab/bond` for bond datasets.
- Registration publishes synchronized staging directories by rename under the existing resource-root lock. Identical re-registration preserves the original manifest; conflicting or corrupt existing versions fail. A corrupt version does not block registering a different version.
- Failed registration retains and reports artifacts. Retry creates fresh staging; retained staging is not automatically reclaimed.
- No migration or legacy-layout support was added; no production resources were expected to exist.

## Verification

- All 48 tests passed using the project virtual environment with `-B -W error`: 38 backend tests and 10 SRIM metadata tests.
- Updated coverage verifies exact layout, independent same-content versions, immutable versions and timestamps, manifest/payload corruption, failed publication and retry, CAB names, and references without subcategory.
- AST/token checks passed for module/class/function docstrings, argument/return annotations, and absence of comments in all four changed Python files. `git diff --check` passed.

## Unresolved work

No outstanding work remains for this layout change. No next component has been selected.
