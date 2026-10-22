# TRIM checkpoint recovery v1 - implemented baseline

## Outcome and scope

Add an explicit, offline Python recovery API for complete frozen checkpoints, covering all seven supported 2008 modes and all eight supported 2013 modes in Standard and Pro. Produce the same eight named recovery artifacts previously requested plus structured results and provenance. Recovery does not launch TRIM, operate its UI, modify a checkpoint, or change execution/lifecycle success. Native text output remains a separate evidence source.

Initial validated geometry is exactly the available evidence: one Si target element, one 1000-Angstrom layer, plot interval 0–1000 Angstrom with 10-Angstrom bins. Counts are read from the checkpoint, including verified 100/200-ion cases, rather than hard-coded. Other geometries are explicitly rejected until additional binary-layout/normalization evidence is reviewed. All-mode support must not be described as arbitrary-target support. Native-output-format compatibility is not part of this proposal.

## Files and dependencies

1. `src/srim/trim_recovery.py`: new standard-library-only module; numeric decoding, saved-metadata validation, profile recovery and explicit directory export. Imports Decimal, struct, pathlib, collections.abc, hashlib, json, existing JSONObject, parse_trim_checkpoint and saved_trim_mode_name. No changes to those existing APIs. No classes, third-party dependencies, settings, background activity or global mutable state.
2. `test/srim/test_trim_recovery.py`: new unittest module. Tests the new API and rejection/provenance behavior using committed byte-preserved native fixtures. Does not run native binaries during ordinary unit tests.
3. `test/srim/fixtures/trim/recovery/<installation>/mode-<two-digit-native-input-code>/`: committed native TDATA.sav, TRIMIN.sav, XRANGE.sav, VACANCY.sav, IONIZ.SAV, PHONON.sav, E2RECOIL.sav, NOVAC.sav, YZRANGE#.sav, YZRADIAL#.sav; paired original eight native text files under native/; manifest.json with SHA-256, byte lengths, mode identity, source provenance and measured comparison discrepancies. Include all 23 existing cases and the paired continued/uninterrupted 200-ion cases. Do not commit executables. Recovery requires the full version-specific saved-state inventory even though only the listed files provide recovered profile values; include remaining saved files as binary fixtures if needed to test directory completeness. Preserve native bytes using the existing .gitattributes native-fixture rules; extend those rules only if they do not already cover these paths.
4. `doc/guides/trim-checkpoint-recovery.md`: API example, recovered layout/units, complete-checkpoint copying, input count/random-state preservation, quick-mode unavailability, mode numbering, exact scope and precision limitations; link to evidence and explain cumulative normalization.
5. `README.md`: add explicit recovery capability and supported geometry/version/mode scope; explain that the format is recovered and does not replace native export/parser or lifecycle checks. Link the guide.
6. `doc/TRIM_CHECKPOINT_RECOVERY_PROPOSAL.md`: this approved design and final validation evidence. Update status after implementation; retain unresolved precision limitations.

No edits to trim_runtime.py, trim_execution.py, trim_native.py, trim_outputs.py or the input-search catalog are proposed. This proposal does not replace the earlier resume-export design or relax its completion criteria.

## Complete function contract

All functions have typed parameters/returns and docstrings; module begins with a docstring. No implementation comments. No new classes.

### `_decode_numeric_variants(contents: bytes, *, source: str, expected_count: int) -> tuple[Decimal, ...]`

Pure function. Decode observed little-endian VB numeric Variant records: Empty tag 0, Single tag 4, Double tag 5, Decimal tag 14. Empty is zero only in these reviewed accumulator arrays. Single/Double use Decimal.from_float; Decimal uses the observed 96-bit magnitude, scale and sign layout. Return exact stored finite values, including legitimate signs. Validate tags, complete record boundaries, Decimal discriminator/scale/sign, exact count and EOF. Raise ValueError naming source and byte offset for malformed/unsupported input; no I/O or mutation.

### `_decode_double_pairs(contents: bytes, *, source: str, expected_rows: int) -> tuple[tuple[Decimal, Decimal], ...]`

Pure function. Decode exactly expected_rows pairs of little-endian binary64 values from lateral/radial accumulator files. Convert using Decimal.from_float. Reject wrong byte length and nonfinite values with ValueError identifying source; return pairs without effects.

### `_recover_metadata(files: Mapping[str, bytes]) -> JSONObject`

Pure function. Validate safe flat filenames, bytes, case-insensitive uniqueness and version-specific complete inventory (12 files for 2008, 18 for 2013, retaining all supplied files in provenance). Reuse parse_trim_checkpoint on TDATA.sav/TRIMIN.sav. Resolve semantic mode with saved_trim_mode_name and check initial input mode consistency. Accept only evidenced SRIM-2008.04/SRIM-2013.00 identities. Require current count > 0, a completed count current == total for this release, valid transmitted/backscattered counts and supported geometry. Extract saved averages/moments/lateral summary/grid using unique labeled records, retaining exact text tokens and raw summary. Store initial seed from TRIMIN and saved generator tokens separately; never reseed. Return metadata plus source inventories/hashes. ValueError rejects malformed, incomplete, ambiguous, unsupported or inconsistent inputs. No mutation or I/O.

### `recover_trim_checkpoint(files: Mapping[str, bytes]) -> JSONObject`

Public pure API. Takes an immutable-view mapping of checkpoint filenames to bytes; validates through _recover_metadata; decodes all standard profile accumulators and returns schema version 1 below. Uses local Decimal context precision 80, independent of caller context. Normalizes energy/damage/replacement arrays by current completed count; RANGE by 1e8/current count for the verified 10-Angstrom-bin layout. Produces the verified depth grid 10.01–1000.01 Angstrom. Never extrapolates that convention outside the supported grid.

Lateral per-bin counts come from XRANGE ion accumulator times 10, rounded to nearest integer only when residual is <= 0.001 ion. Reject larger residuals. For positive n: projected mean = sum_abs_y/n; projected straggling = sqrt(sum_y_squared/n); radial mean = sum_r/n; radial straggling = sqrt(sum_r_squared/n - mean_squared). A negative radial variance within 1e-12 times max(1, abs(second moment), mean_squared) is recorded as roundoff and clamped to zero; larger negatives raise ValueError. For a zero-count bin require zero lateral/radial moments, then output zeros. Record clamping as a warning; never silently hide a nontrivial inconsistency.

Quick/KP NOVAC is unavailable with reason not_calculated, columns present but rows empty; never report the empty accumulator as physical zero. Identify quick modes semantically, including the saved/input-code remapping in 2013. Preserve signed energy-transfer values. Do not derive fictitious exact summary statistics from rounded saved moments: retain stored averages/moments, and explicitly leave unavailable native-only quantities null with a reason. Raise ValueError for invalid arrays/metadata/geometry; do not fall back to old text outputs. Return fresh JSON-compatible data; no filesystem effects.

### `_render_recovered_files(result: JSONObject) -> dict[str, bytes]`

Pure function accepting only the validated recovery result schema; rejects invalid schema or nonfinite/invalid numeric fields with ValueError. Return eight .txt artifacts, recovery.json, manifest.json and README.txt as UTF-8 bytes with LF endings. Each text starts with RECOVERED FROM CHECKPOINT and identifies version, semantic mode and count. Seven profile tables have explicit labels and units, no claim of native parser compatibility; numerical strings render as scientific notation with 15 fractional digits. Quick NOVAC prints its not-calculated reason and no numeric table. TDATA.txt contains a recovered-summary label followed by the saved summary text, retaining its stored numeric precision. JSON uses sorted keys, indentation 2, allow_nan=False and terminal newline. manifest.json contains source hashes, output hashes excluding its own hash, format version, supported scope and warnings. README documents units, cumulative normalization, formatting distinction and validation limitations. No I/O.

### `export_trim_checkpoint_recovery(source: Path, destination: Path) -> JSONObject`

Public filesystem API. Both paths must be absolute; source must be an existing frozen regular directory, destination must not exist and must not be source or its descendant. Reject symlink/junction/reparse-point source entries, nested directories, nonregular files, duplicate/colliding names and unsafe paths. Read all source files once, recover/render fully before creating destination. Recheck source inventory/hashes before publication and again afterward; reject changes with ValueError, retaining any partial destination with explicit exception context. This is change detection, not a guarantee of atomic checkpoint capture. Caller must stop/freeze TRIM first. No locks on source or native process, no parallel writes.

Create destination exclusively, never overwrite or delete. Write the rendered file set; re-read and verify hashes. Return JSON object with destination absolute path, filenames, hashes, version/mode/count and warnings. Propagate OSError/FileExistsError for filesystem failures, retain partial output for inspection, and annotate the failure with destination. Never alter source or native output records. Concurrent callers cannot share a destination; exclusive creation determines the winner.

## JSON schema and output layout

`recovery.json` top-level exact keys:
- schema_version: integer 1
- format: trim-checkpoint-recovery-v1
- provenance: source filenames, SHA-256/size, method, recovered=true; no claim of native emission
- checkpoint: existing parsed identity/counts/target fields, semantic input/saved modes and stored seed state
- grid: start/end/bin_width strings, 100 depth strings, convention label and evidence restriction
- summary: raw_saved_text, stored_averages, stored_moments, stored_lateral_values, unavailable fields with reasons
- profiles: keyed by the seven standard filenames; each has availability (available/not_calculated), reason (string/null), units, ordered column names, and rows as ordered arrays of decimal strings including depth
- warnings: ordered list of code/message objects, including native_format_not_reproduced, saved_precision_limits, and scope_restriction; roundoff warning when applicable

TDATA.txt and the seven profile filenames match the earlier recovery artifacts. They are not native-file schema substitutes. Unavailable means absent measurement, not zero. All data are cumulative through current completed count. Do not subtract the 100-ion results to obtain second-half values in this change.

## Verification and acceptance

Use the exact project Python interpreter and unittest discovery, not pytest. No dependency installation.

Test module contains class `TestTRIMRecovery(unittest.TestCase)` with no custom constructor. Each method signature is `(self: TestTRIMRecovery) -> None`, has a docstring describing no returned value and temporary filesystem effects where applicable:
- test_numeric_variant_types: exact positive/negative/zero values and all four observed tags.
- test_rejects_invalid_numeric_records: unknown tags, truncation, bad Decimal fields, nonfinite values, wrong count/trailing bytes.
- test_double_pairs: exact lengths/order, finite values and rejection cases.
- test_all_version_mode_fixtures: iterate all 23 cases, assert version/mode/shape/count, decoded numerical evidence and quick-mode availability.
- test_quick_novac_is_unavailable: never export invented numeric zeros for KP modes.
- test_signed_energy_is_preserved: observed signed E2RECOIL values survive decoding/rendering.
- test_rejects_unsupported_checkpoint: missing/duplicate files, invalid identity/mode/count/geometry and inconsistent states.
- test_lateral_validation: count residuals, zero-count consistency, variance clamping warning and substantial-negative rejection.
- test_continued_vs_uninterrupted: preserved 200-ion fixtures reproduce 1498/1500 exact nondepth matches and the two measured differences; verify cumulative normalization and matching final seed states without asserting physical equivalence.
- test_native_precision_evidence: independently parse native numeric tokens, compare half-final-printed-digit errors and preserve measured discrepancy counts. Current 23-case evidence is 48800 compared cells with 619 outside threshold. These differences remain explicit; do not loosen tolerance or claim native equality. Detailed discrepancies must be byte-derived, not generated from expected recovery values.
- test_export_and_readback: eight recovered text files plus JSON/manifest/README, row/column counts, declared precision, hashes, labels and source unchanged.
- test_export_rejects_unsafe_or_existing_destination: absolute/path constraints, unsafe source entries, collisions; no overwrites.
- test_export_detects_changed_source_and_io_failure: injected source-change/write failure preserves source and reports partial destination.

Fixture tests are mode coverage for the evidenced geometry, not full parameter-space certification. Before claiming support at other counts/geometries or exact native format, gather and approve further evidence. Native resume itself is unchanged and has a separate acceptance scope.

## Approval

Approved by the user with “implement” on 2026-10-22 and implemented within this offline recovery scope. Arbitrary target composition/grid support and automatic lifecycle integration remain outside this design and require further evidence and an approved amendment.

## Completed validation

- New recovery suite: 13 tests passed, including all 23 native version/mode fixtures, unavailable quick NOVAC, exact binary decoding, malformed/nonfinite inputs, signed energy, lateral consistency, low caller Decimal precision, unsafe/existing destinations, source changes before/after publication, partial writes and output hash verification.
- Broader TRIM suite: 107 tests passed. Subsequent test-only additions for reparse-point rejection and post-publication changes passed in the 13-test recovery rerun.
- Native fixture manifests: 626 files, 6,488,191 bytes, including 23 paired native cases and two 200-ion checkpoints. Native bytes and hashes verified. No executables included.
- Actual public API exports: 25 completed and verified, with source bytes unchanged. Local evidence: `.cache/popup-review/sav-coverage/implemented-recovery/acceptance.json`.
- Numerical evidence remains 48,800 native cells with 619 outside half a final native printed digit; continued/uninterrupted 200-ion recovery remains 1,498/1,500 exact nondepth matches. These discrepancies are explicitly retained, not waived as exact native equivalence.
- Module/function/class documentation and explicit function argument/return annotations checked; no implementation comments added.
- Native resume execution and lifecycle behavior were not modified.

## Superseding geometry amendment

The Si-only v1 geometry restriction above describes the original implementation. The user-approved [generalization amendment](TRIM_CHECKPOINT_RECOVERY_GENERALIZATION.md) supersedes it for new calls, which now produce metadata-driven schema-v2 recovery. Existing v1 artifacts are not migrated. See the [current guide](guides/trim-checkpoint-recovery.md) for supported structural checks and explicit inferred-layout warnings.
