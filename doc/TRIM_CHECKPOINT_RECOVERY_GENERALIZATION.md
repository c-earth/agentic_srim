# Generalized checkpoint recovery - approved implementation

Status: explicitly approved by the user with "approve" and implemented. This extends the implemented offline recovery design; it does not change native execution, lifecycle checks or native-format compatibility.

## Evidence and judgment

Existing binary evidence is broader than the first recovery implementation used. It includes both engine families, compounds, repeated atomic numbers in distinct target-entry slots, one shared element over ten layers, ten distinct entries over ten layers, three entries over two layers, and grids with saved widths from 3 through 303 Angstrom. The inventory also includes up to 49 target entries and 101 layers; the existence of such checkpoints is not a certification of those native parameter limits or successful calculation.

Eight selected archived cases were decoded and compared diagnostically. Full-cascade profile widths match one projectile plus each saved target entry. Quick-mode native text aggregates the target entries, while the saved arrays retain all entry slots. IONIZ and PHONON stay two-column accumulators; NOVAC remains one column. The native depth offset remains 0.01 Angstrom in sampled zero-start cases with different widths. Profile normalization is consistent with saved accumulators / completed count, and RANGE with 1e8/completed count. Array values are already normalized per depth length; do not divide by bin width a second time.

Some archive text files have fractional progress counts that differ from their saved checkpoints, such as 9.71 versus 10 and 0.22 versus 1. These are not paired-state native-equivalence fixtures. Preserve and report this discrepancy; do not tune reconstruction to match stale/partial text.

Two newly isolated native checks using plot interval 125–1375 Angstrom stopped on unidentified startup popups before any checkpoint/output existed. They do not validate nonzero-start or fractional-width behavior. Preserve the failed process reports; no responses were guessed.

Read-only diagnostic evidence: `.cache/popup-review/sav-coverage/generalization-inventory.json`, `generalization-shapes.json`, `generalization-comparison.json`, and `generalization-native-checks/{2008,2013}/process.json`.

## Approved-design changes requested

Remove the Si-only, single-layer and fixed-depth restrictions. Infer shape and grid from complete checkpoint metadata and actual file lengths, cross-check all dependent arrays, and expose inferred geometry explicitly. Do not claim that arbitrary geometry is scientifically validated or that all native output digits are reproducible.

The public API signatures stay unchanged:

- `recover_trim_checkpoint(files: Mapping[str, bytes]) -> JSONObject`
- `export_trim_checkpoint_recovery(source: Path, destination: Path) -> JSONObject`

The four existing private signatures also remain unchanged except that the following new helper is added. No classes, settings, dependencies, native launch behavior, concurrency model or lifecycle changes.

### New `_recover_layout(checkpoint: JSONObject, files: Mapping[str, bytes]) -> JSONObject`

Pure function. checkpoint is the already parsed saved target/count/mode metadata and saved plot tokens supplied by _recover_metadata. files is a case-unique flat checkpoint byte mapping. Return row count, target-entry descriptors, layer descriptors, saved plot tokens, derived start/end/width, depth vector, profile column descriptors, storage widths and evidence classifications. No mutation or I/O. Raise ValueError on malformed, ambiguous or inconsistent layout.

Derive row count from the common byte lengths of YZRANGE#.sav and YZRADIAL#.sav, each 16 bytes per row. Require both equal, nonempty and exactly divisible by 16. This derives the actual array length rather than assuming that every plot has a caller-configurable bin count. Native evidence so far uses 100 rows; other coherent lengths are classified inferred.

Require positive target-entry/layer counts, finite positive masses/densities/layer widths, nonnegative finite stoichiometry, correct stoichiometry vector lengths and positive stoichiometry total in every layer. Validate strictly increasing cumulative depths and their agreement with summed saved layer thicknesses using the decimal precision of those stored tokens. Gas/solid is retained from the existing checkpoint parser rather than enforcing solid. Keep each target entry indexed separately; repeated Z values are never merged.

Require finite saved plot start/end/resolution, start >= 0, end > start and saved resolution > 0. Compute effective width as (end-start)/row_count under the private 80-digit Decimal context. Compare that width to the saved resolution with allowance of half the last stored resolution digit plus half the stored start/end digit uncertainties divided by row_count. Reject disagreement beyond the declared stored precision. Keep both saved resolution and computed width in metadata.

Compute depth[r] = start + (r+1)*effective_width + 0.01 Angstrom. The constant offset is observed for sampled zero-start layouts; its extension to nonzero starts and fractional widths is inferred and explicitly flagged. Never invent a different per-material offset.

Storage widths per row:

| File | Stored values per row |
|---|---:|
| XRANGE.sav | 1 + number of target entries |
| VACANCY.sav | 1 + number of target entries |
| E2RECOIL.sav | 1 + number of target entries |
| IONIZ.SAV | 2 |
| PHONON.sav | 2 |
| NOVAC.sav | 1 |
| YZRANGE#.sav / YZRADIAL#.sav | 2 raw binary64 values each |

Require exact decoded record counts and EOF for every file. Unknown record encodings, mismatched lengths and nonfinite values still fail; do not truncate arrays to make a guessed dimension fit.

For full/monolayer calculations, RANGE/VACANCY/E2RECOIL columns are projectile then target entries in saved order. Use unique names such as target_001_Z14, target_002_Z8, target_003_Z14 with role suffixes, and retain the atom index/Z/mass and per-layer stoichiometry association in the layout metadata. For quick modes, emit projectile plus summed target component to reproduce the observed aggregate quantity, while retaining individual saved-entry values in recovery.json under stored_target_components. Sum before normalization using Decimal; do not fabricate separate physical KP histories. IONIZ/PHONON remain projectile/recoil aggregates; NOVAC remains unavailable in quick modes. LATERAL remains four columns.

Classify non-Si species, multilayers and multi-entry shapes as metadata-driven with supporting layout evidence, not per-composition scientific verification. Add inferred_geometry warning for nonzero plot starts, fractional effective widths, row counts other than the observed 100, or gas layers. No new opt-in argument: structurally consistent inferred layouts are accepted because the user explicitly requested best-judgment generalization; the evidence classification and warning are mandatory. Contradictory or unsupported structure still fails.

### Changes to existing functions (signatures unchanged)

`_recover_metadata(files: Mapping[str, bytes]) -> JSONObject`: preserve complete-file/version/mode/count checks; remove geometry-specific Z=14, one-layer, width=1000, fixed 0:1000:10 restrictions. Extract raw plot tokens and call _recover_layout after parsing. Retain summary tokens and seed-state semantics, including distinct initial seed/saved state. Layer/grid consistency errors remain ValueError; no effects.

`_decode_numeric_variants(contents: bytes, *, source: str, expected_count: int) -> tuple[Decimal, ...]`: unchanged behavior. It receives metadata-derived exact counts, not hard-coded 100×width values.

`_decode_double_pairs(contents: bytes, *, source: str, expected_rows: int) -> tuple[tuple[Decimal, Decimal], ...]`: unchanged behavior; expected_rows is metadata-derived.

`recover_trim_checkpoint(files: Mapping[str, bytes]) -> JSONObject`: use dynamic rows/widths/columns/depth from the new helper; normalize cumulatively by actual positive completed count. Recover per-bin ion counts using saved ion distribution × effective bin width, retaining existing integer residual and variance safeguards. Derive summary/profile association from entry indices, not element-symbol merging. Native-only precision-limited statistics stay unavailable. Preserve stored_target_components for quick-mode target-entry data without interpreting those as tracked histories. Geometry and validation status appear explicitly in result. No I/O; ValueError behavior and private Decimal precision remain.

`_render_recovered_files(result: JSONObject) -> dict[str, bytes]`: validate schema v2 and metadata-derived column/row/depth counts rather than fixed columns/100 rows. Render eight named recovered texts and three sidecars as before. Use result grid depths for consistency checks. Include target-entry identifiers and units in headers, inferred-geometry warnings in README, and preserve unavailable quantities. TDATA.txt remains labeled saved summary. Do not imitate native headers. ValueError rejects malformed result; no I/O.

`export_trim_checkpoint_recovery(source: Path, destination: Path) -> JSONObject`: unchanged path safety, complete-source checks, exclusive publication, output/source verification and partial-output error behavior; export v2 artifacts and return the layout/evidence classification in addition to prior identity/hash data. No source mutation or native actions.

## Schema/versioning

New calls return schema_version 2 and format trim-checkpoint-recovery-v2, identifying expanded/dynamic layout behavior. Previously exported v1 artifacts are not modified or migrated. There is no v1 import API to change.

Keep existing top-level keys. Extend grid with row_count, saved_resolution, derived bin_width, evidence classification and dynamic depth array. Extend checkpoint with a layout object containing indexed target entries, layer association, profile storage/output column descriptors and inferred-rule identifiers. Profiles retain availability/reason/columns/units/rows; quick RANGE/VACANCY/E2RECOIL additionally contain stored_target_components (unscaled exact saved numeric strings, with saved entry identifiers and units described as saved accumulators). Warnings include inferred_geometry where applicable and continue to disclose native-format/precision limits. Manifest scope reflects the actual layout and its evidence level; remove the old unconditional Si restriction text.

Default filenames, UTF-8/LF encoding, finite JSON with decimal strings, 15 fractional scientific-notation digits, source/output hashes, no overwrite and cumulative count semantics remain unchanged.

## Files to change

- `src/srim/trim_recovery.py`: implement the helper and changes above. PROFILE_LAYOUTS becomes role/unit metadata rather than fixed target-column lists. Preserve dependency on existing textual checkpoint parser; add no parser/lifecycle changes.
- `test/srim/test_trim_recovery.py`: retain every existing decoding, 23-mode numerical discrepancy and continuation regression. Revise the unsupported-geometry test to distinguish structural contradictions from allowed geometry. New methods, all `(self: TestTRIMRecovery) -> None`, with docstrings and temporary files only under .cache: test_generalized_native_fixtures; test_repeated_target_entries; test_metadata_derived_grid; test_dynamic_shape_rejections; test_inferred_geometry_warnings; test_generalized_export_readback. Tests use independent native observations and synthetic structural cases, labeling the latter as unverified physical evidence.
- `test/srim/fixtures/trim/recovery/generalized/<case>/`: preserve selected existing compound/multilayer/variable-width checkpoints, their eight available native outputs, and manifests recording original hashes, saved/native counts, exact comparison eligibility and observed discrepancies. Include shared-vs-distinct entry layouts from both families, full compound examples, different bin widths and a large-layer case. No executables. Unequal native/saved counts are explicitly ineligible for native-equivalence assertions.
- `doc/guides/trim-checkpoint-recovery.md`: explain dynamic metadata, target-entry identity, aggregate/full columns, gas/nonzero/fractional/other-row inference, schema v2 and exact remaining uncertainty.
- `README.md`: replace the Si-only restriction with metadata-driven generalized support and explicit inferred-layout warnings; retain native-format/lifecycle limitations.
- `doc/TRIM_CHECKPOINT_RECOVERY_PROPOSAL.md`: preserve v1 history and add link to this approved amendment/results.
- `doc/TRIM_CHECKPOINT_RECOVERY_GENERALIZATION.md`: this amendment, final results and unresolved native-check evidence. No changes to runner code or .gitattributes needed.

## Verification and acceptance

Run the exact project interpreter and unittest discovery. Every existing v1 fixture must preserve recovered numerical values, quick unavailability, 48,800-cell/619-discrepancy regression and 1,498/1,500 continued/uninterrupted matches, with only explicit schema/layout additions.

Validate dynamic dimensions and recorded precision against eligible native compound/layered profiles. Reject stale/fractional-count pairs as equivalence tests while retaining them as structural evidence. Confirm target-entry order and role without merging repeated Z. Validate IONIZ/PHONON aggregate widths independently of target count and quick aggregate sums against native text. Re-export and re-read all generalized cases, verify source and output hashes, and retain existing path/concurrency/change-detection protections.

Synthetic altered metadata/arrays test nonzero start, fractional width and non-100 row counts for structural correctness and mandatory inferred classification. They do not count as native numerical validation. The two failed native launch checks remain failures in the report. Do not erase or silently bypass unknown popups to obtain apparent acceptance.

Acceptance means a generic metadata-driven recovery implementation with explicit evidence boundaries, not certification of every native version/mode/geometry combination, exact native formatting, or automatic lifecycle fallback. Further native experiments may strengthen evidence later without retroactively calling these inferred rules verified.

## Implementation and verification results

- Schema v2 is implemented with unchanged public signatures, dynamic dimensions/columns, indexed target-entry/layer metadata, quick-mode sums plus preserved raw target components, and mandatory inferred-layout warnings.
- Recovery tests: 19 passed. Broader TRIM suite: 113 passed.
- Original 23-mode precision regression remains 48,800 cells with 619 outside half a final native digit; original 200-ion continuation regression remains 1,498/1,500 exact nondepth matches.
- Ten generalized native fixtures were added. Eight have eligible native counts, adding 17,600 compared cells with 2,129 outside half a final printed digit. These discrepancies are recorded rather than relabeled exact. The other two fixture sets have progress-count mismatches and supply structural evidence only.
- Synthetic tests cover nonzero starts, fractional widths, gas layers and coherent non-100-row arrays. These pass structural recovery/export tests and carry inferred_geometry warnings; they are not native-validated numerical evidence.
- Public exporter produced 35 verified v2 recovery sets with source bytes unchanged. Local evidence: `.cache/popup-review/sav-coverage/generalized-recovery-v2/acceptance.json`. Existing v1 artifacts remain unchanged.
- The two earlier cropped-grid native runs remain failed startup-popup checks; no checkpoint was produced and no inference was promoted to native verification.
- Native-format compatibility, native execution and lifecycle checks remain unchanged.
