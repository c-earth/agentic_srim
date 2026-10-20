# CAB research evidence before integration

Historical investigation, not a current API guide. See the [documentation index](../README.md), [CAB status/evidence](../CAB_HANDOFF.md) and [later review](../reviews/non-trim-review.md). Installation-specific observations are not universal numerical guarantees. Source/cache paths below are relative to the repository root.

Historical observations and local artifact inventory; current implementation status is in CAB_HANDOFF.md. The approved proposal/API catalog supersede historical algorithm suggestions.

## Empirical model and qualifications

For nonzero bonds, experiments support correction = (sum atom_count*C_E + sum bond_count*B_b) / sum atom_count*S_E. With no bonds the engine returns one rather than exposing the extrapolated intercept. Contributions have a common arbitrary scale until S is anchored to elemental stopping.

Seven explicitly supported elements: H, C, N, O, F, S, Cl. Bond-vector order is H-H, H-C, H-N, H-O, C-C, C=C, C-triple-C, C-N, C-O, C=O, C-F, C-Cl, N-triple-N, N-O, O=O, S-H, S-C, S-F.

One-atom mocks with H-C counts 5/10 inferred all 92 S values. C_C=1 calibration: B_H-C=1.185011995934239477176188616355710380061 and S_C=2.425871433665758582126664451037423918607. These historical experiments did not implement the new C_H=0 default-fitting procedure. Hydrogen's historical fitted intercept was slightly negative from display rounding; do not confuse those results with the newly agreed constrained procedure.

Of 85 unsupported elements, 81 had C/S consistent with one within capture rounding. Four exceptions remained reproducible using H-C counts 5,10,15,20 and the other 17 bond types at 5/10: He approximately .999536, Li 1.007903, Be .996885, B 1.007722 (four-point H-C intercepts). Do not enforce equality in fitted defaults. Separate fresh zero-bond tests for all four returned exactly one. Neither original 2008 nor 2013 Standard dictionary has any nonzero-bond entry containing those four; each has nine distinct entries containing them, all zero-bond and correction one.

All-element comparison uses a separate common scale per phase/energy: sum(reference S)/sum(mock S), i.e. ratio of arithmetic means. At four significant digits:

| Reference | Matches / 92 | Mean absolute relative error | Maximum absolute relative error |
| --- | --- | --- | --- |
| Gas 125 | 7 | .0871693% | .317046% |
| Gas 126 | 89 | .00696684% | .0290572% |
| Solid 125 | 0 | .921074% | 7.77951% |
| Solid 126 | 0 | .919436% | 7.57589% |

Gas-126 mismatches Rb, Sb, Lu have absolute relative errors .01171%, .01121%, .01142%; corresponding independently mean-scaled solid-126 errors are .4510%, .4637%, .4514%. Noble gases break the gas/solid scaling degeneracy observed earlier with only seven supported elements. This supports the gas 125-keV/amu reference experimentally; it is not internal-code proof.

2013 Standard ATOMDATA stores hydrogen MAI weight and natural weight both as 1.008, hence 125*M_H is exactly 126 keV at stored precision. The earlier conversation briefly claimed this projectile mass was wrong; that claim was corrected by inspecting ATOMDATA. Fractional-keV input is supported: existing live output accepted 1.02 and 3.98 keV.

This historical investigation tested detailed parameter recovery with 2013 Standard. Later representative 2008 comparison is recorded in CAB_HANDOFF.md; general cross-version equivalence remains unestablished. Existing `COMPOUND_COMPARISON.txt` describes the narrower ten-compound SR/TIN comparison across all three versions.

## Evidence inventory (local, ignored by Git)

All paths below are relative to the repository. The user cleared these historical ignored artifacts on 2026-10-20; they are no longer present locally or portable contents of the repository. The recorded observations remain historical summaries. Later fresh verification artifacts are listed separately in the CAB status page and were not part of this investigation. No consolidated experiment dataset was created.

- `.cache/compound-all-20261019/`: `capture_all_compounds.py`, `fit_cab.py`, `compare_elemental_stopping.py`, `results.json`/CSV (345 original 2008 + 357 original 2013 Standard captures), per-version captures, fit variants and website parameters. Original dictionary hashes still match every captured record's source hash.
- `.cache/compound-all-20261019/website-elemental-comparison/`: elemental-stopping.json/CSV (92 elements x two phases x 125/126 = 368 values), 184 isolated runs, source provenance, raw inputs/outputs. Projectile H mass 1.008, correction one, units eV/(1E15 atoms/cm2), target natural masses and phase-specific densities from ATOMDATA.
- `.cache/mock-zero-h-20261019/`: carbon-only H-C calibration, counts 1â€“14 and 15 checks, `hc-linearity-derivation.json`.
- `.cache/mock-supported-hc-5-10-20261019/`: seven supported-element parameters and `linearity-15/summary.json`.
- `.cache/mock-all-elements-hc-5-10-20261019/`: all-92 `parameters.json`/CSV, 170 new unsupported-element captures, and `light-exceptions-linearity/summary.json` (15/20 checks). Seven supported values reused earlier evidence. Historical all-92 agreement summaries were calculated read-only from these inputs and elemental-stopping.json, not saved as a separate experiment report.
- `.cache/mock-light-other-bonds-20261019/`: 136 captures (four elements x 17 other bonds x 5/10), `summary.json`, `ratios.csv`, restoration record.
- `.cache/mock-light-zero-bonds-20261019/`: four fresh zero-bond captures, exact mock dictionaries, and `summary.json`; isolated Compound.dat and SR.IN restored.

An original 2013 glycerol entry (record 321, source line 2084) contains a blank line before bonds and gives 1.107174; duplicate record 157 gives .9307967. Excluding only 321 yielded 217 nontrivial records and fit RMSE .0001810675. Preserve source bytes; do not silently repair this anomaly. Website tables are rounded and the printed C-O bond value is 15.7; sensitivity with 1.57 was separately tested, not established as a correction.
