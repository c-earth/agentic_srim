# Recovering TRIM checkpoint profiles

`recover_trim_checkpoint` and `export_trim_checkpoint_recovery` recover cumulative numerical profiles from a complete, frozen checkpoint. Native `.txt` outputs are not inputs to recovery. These offline APIs do not launch TRIM or change lifecycle status. The [execution pipeline](trim-execution.md) now calls recovery explicitly for resumed runs after owned processes stop, with independent completion and provenance checks.

## Supported cases

The preserved native fixtures cover all seven supported SRIM-2008.04 modes and all eight SRIM-2013.00 modes in both Standard and Pro installations: **23 version/mode combinations**. Counts come from the completed checkpoint; 100-ion and 200-ion cases are tested.

Recovery v2 derives target-entry columns, layers, row counts and grids from saved metadata and array lengths. It accepts structurally coherent compounds, other elements, repeated element entries, multilayers and changed plot intervals. It is no longer restricted to Si or the 0–1000 Angstrom grid. Unsupported versions, unfinished counts, incomplete checkpoints or contradictory geometry/arrays raise `ValueError`.

Ten additional native fixture sets cover shared and distinct entries over ten layers, a three-entry/two-layer target, compounds, 50 layers, and bin widths of 3, 10, 15, 30, 70 and 150 Angstrom. Eight sets have eligible native count matches; two sets have native progress counts different from their saved counts and supply structural evidence only. Layout coverage does not establish scientific correctness for every mode/composition/geometry combination.

Structurally consistent **nonzero plot starts, fractional bin widths, gas layers and row counts other than the observed 100** are accepted with `inferred_geometry` warnings. These cases use the approved inferred rules; they are not native-validated results. The two attempted cropped-grid native checks stopped at unidentified startup popups before producing checkpoints and did not verify those rules.

## Usage

Stop TRIM and freeze the whole checkpoint before calling recovery. Source-file change detection is not a guarantee that binary arrays were saved atomically. Copy the entire checkpoint, not selected files collected at different times.

```python
from pathlib import Path
from src.srim.trim_recovery import export_trim_checkpoint_recovery

report = export_trim_checkpoint_recovery(
    Path(r"D:\frozen-run\SRIM Restore"),
    Path(r"D:\recovered-run"),
)
```

Both paths must be absolute. The source must contain regular flat files, with no symlinks, junctions or nested directories. The destination must not exist or be inside the source. Recovery reads and validates before creating the destination, writes exclusively, verifies the outputs and rechecks the source. Filesystem errors propagate; a partial destination is retained with its path in the exception notes. No source files are modified or deleted.

For in-memory recovery, call `recover_trim_checkpoint(files)` with a mapping of checkpoint filenames to bytes. It returns a JSON-compatible dictionary with identity, dynamic layout/grid, summary, seven profile families, availability, source hashes and warnings. It performs no I/O and does not mutate the supplied mapping. No installation is required for offline recovery. Both public function signatures are unchanged from v1.

## What must be preserved to resume

Use the complete checkpoint with the matching pinned engine and support files. SRIM 2008 saves 12 checkpoint files; SRIM 2013 saves 18, including six 3D arrays. The runtime copies the complete set, retaining additional safe entries rather than just copying a minimum subset.

The saved current count and random-generator state must remain intact. The continuation API updates the requested absolute total/autosave counts in `TDATA.sav`; it does not reseed. Event modes also require a preserved `TRIM.DAT` prefix and enough appended events. Initial seed and saved generator state are distinct fields in recovered metadata. Normal `.txt` outputs are not needed to continue, but preserve existing native outputs for their original formatting and precision.

## Output files and units

The destination contains:

- `TDATA.txt`: labeled saved summary, retaining stored precision.
- `RANGE.txt`, `VACANCY.txt`, `IONIZ.txt`, `PHONON.txt`, `E2RECOIL.txt`, `NOVAC.txt`, `LATERAL.txt`: labeled recovered tables or an explicit unavailable statement.
- `recovery.json`: schema version 2, format `trim-checkpoint-recovery-v2`; structured values, dynamic layout and evidence classification, stored summary tokens, checkpoint identity, initial/saved seed fields, provenance, availability and warnings.
- `manifest.json`: source hashes and output hashes, excluding the manifest's own hash.
- `README.txt`: format, units, scope and precision limitations.

Text is UTF-8 with LF endings. Numeric table output uses scientific notation with 15 fractional digits. Additional displayed digits preserve stored numbers and do not imply physical accuracy. JSON numerical table values are decimal strings; unknown/unavailable values are accompanied by explicit reasons.

| Profile | Data units | Recovery rule |
| --- | --- | --- |
| RANGE | inverse cm | Saved projectile/target-entry accumulators × `1e8 / current_count` |
| VACANCY | events/Angstrom/incident ion | Saved values / current count |
| IONIZ, PHONON, E2RECOIL | eV/Angstrom/incident ion | Saved values / current count |
| NOVAC | events/Angstrom/incident ion | Saved values / current count, when calculated |
| LATERAL | Angstrom | Saved bin moments and recovered integer bin counts |

All depth columns are Angstrom. Row count comes from the common lateral/radial binary64-pair file lengths; other saved arrays must have exactly the matching record counts. Effective bin width is `(end - start) / row_count`, checked against the saved resolution at its stored precision. Depth is `start + (row + 1) × effective_width + 0.01`. The offset is observed across the sampled zero-start grids; its extension to nonzero starts/fractional widths is explicitly inferred. Native evidence still uses 100 rows; array-driven sizing does not establish that native TRIM offers a configurable number of bins.

The saved depth-profile accumulators are already per unit depth. They are divided by the completed count, not divided by bin width a second time. Results are cumulative through that count, so recovery at 200 includes the first 100 ions. It does not report the isolated second-half contribution.

RANGE, VACANCY and E2RECOIL store a projectile column plus one column for every saved target entry. Full/monolayer recovered tables retain those entries in saved order, with unique identifiers such as `target_001_Z14`, `target_002_Z8`, `target_003_Z14` and role suffixes. The layout records mass and per-layer stoichiometry for each entry. Repeated atomic numbers are **not merged**. Quick modes emit the observed aggregate target quantity and additionally preserve the unscaled per-entry saved accumulators in `stored_target_components` in `recovery.json`; these are not separate tracked recoil histories. IONIZ and PHONON retain their two aggregate components, independent of target-entry count. NOVAC has one component when available; LATERAL has four.

Previously exported v1 artifacts are not rewritten or migrated. New exports use v2 and include row count, saved resolution, derived width, target-entry/layer associations and the layout evidence classification. Warnings are also printed in the generated README.

Quick/Kinchin–Pease modes do **not** calculate replacement collisions. Their NOVAC profile has `availability="not_calculated"`, an explanation and no numeric rows. Empty saved accumulators must not be interpreted as measured zeros. Signed energy-transfer values are retained.

Saved numeric Variant records use Empty, Single, Double and Decimal encodings in the observed modes; lateral arrays use raw binary64 pairs. Unexpected tags, truncated/nonfinite records and inconsistent shapes are rejected. Lateral counts must be within 0.001 ion of an integer. A small negative radial variance within the documented relative `1e-12` roundoff allowance is clamped with a warning; substantial negative variance and nonzero moments in an empty bin fail validation.

## Precision and validation

**These files are recovered artifacts, not native TRIM exports.** Their headers, layout and summary differ from the native format. Do not feed rendered recovery text to the native-output parser. Resumed execution explicitly selects structured checkpoint results; fresh runs retain native lifecycle output checks. There is no fallback from missing fresh native text. `parse_trim_checkpoint_results(files)` in `src.srim.trim_results` provides the pure adapter, preserving the recovery-v2 object and separate native diagnostics without certifying execution success.

The 23 native fixture cases compare **48,800 table cells**. Of these, **48,181** are within half a native final printed digit and **619** are outside that strict threshold. Regression tests measure and retain those discrepancies; they do not loosen tolerance to claim native equality. Saved summary moments are rounded, so some native statistics cannot be reconstructed at their original printed precision. Exact native-only statistics are explicitly unavailable rather than fabricated.

In the separate tested 2013 ion-full comparison, continued 100→200 and uninterrupted 1→200 recovery match exactly in **1,498 of 1,500 nondepth profile values**, and all depth values match. One recoil-ionization value differs by approximately `0.0002903675564` and one recoil-phonon value by `0.0009221788` eV/Angstrom/incident ion. Final random-generator states and saved main averages match; some saved higher moments differ in their last digits. These results establish the measured behavior for that case, not bitwise or physical equivalence of every native resume path.

Committed native fixtures and their SHA-256 manifests are under `test/srim/fixtures/trim/recovery/`. The fixtures contain no executables. Run the offline tests from the repository root:

```powershell
D:\projects\agentic_srim\.venv\Scripts\python.exe -m unittest discover -s test/srim -p test_trim_recovery.py -q
```

The NERSC dataset archived locally has no `.sav` files and cannot supply recovery inputs. Further native evidence can strengthen the inferred-layout rules. Exact native-format compatibility remains outside recovery; lifecycle verification belongs to execution. A live 2013 Standard ion-full 100-to-200 continuation passed on 2026-10-22, with direct frozen recovery equal to pipeline recovery. This adds evidence for that lifecycle, not general native equality or physics validation; see the [recorded check](../decisions/trim-continuation.md).
