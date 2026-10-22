# Checkpoint recovery handoff

2026-10-22 update: the user selected `.sav` recovery as the continuation-results route and abandoned GUI result exports. [Current direction and unresolved cleanup](TRIM_DIRECTION.md) supersede any older suggestion to finish F-button or directory-chooser automation. Parser/lifecycle integration is the next design task, rather than a requirement to reproduce native text formatting.

Recovery v2 is implemented and committed independently of unfinished popup automation. See the recovery guide and approved generalization amendment for contracts; this handoff records only additional state.

- Commit includes `trim_recovery.py`, its tests/native fixtures, recovery documentation, the README recovery paragraph, uppercase `.SAV` byte-preservation rule, and the two required saved-mode mapping helpers in `trim_outputs.py`.
- Other popup/native/runtime/parser edits and their tests/proposals remain outside this commit. The working tree also contains unrelated NERSC archives, caches and test residue; these were preserved.
- Working-tree verification: 19 recovery tests and 113 TRIM tests passed. The 35 v2 public API exports and source/hash verification are recorded locally in `.cache/popup-review/sav-coverage/generalized-recovery-v2/acceptance.json` and `validation.json`. Cache evidence is not required for the committed fixture tests.
- Ten generalized fixture sets were added: eight eligible native-count comparisons (17,600 cells; 2,129 outside half a native final digit), two structural-only sets with progress-count mismatches. Original 23-mode (48,800 cells; 619 discrepancies) and 200-ion (1,498/1,500 exact profile values) regressions remain unchanged.
- Nonzero starts, fractional bin widths, gas layers and non-100-row layouts are structurally tested and labeled inferred. Two native cropped-grid probes failed at unidentified startup popups before producing checkpoints; do not count them as native validation.
- The original recovery implementation scope is complete. Newly selected next work is recovered-result parser/lifecycle integration; it is not implemented yet. Exact native text formatting is not required for that route. Further native validation of inferred geometry remains separate. Recovery currently does not make missing native outputs pass lifecycle checks.

Clean staged-snapshot verification passed all 19 recovery tests after exporting the index to an isolated directory. This establishes that the commit works without the unstaged popup/native/runtime changes. Staged source/documentation whitespace checks passed; native fixture whitespace/line endings are intentionally preserved and verified by their hashes. No commit hash is embedded here to avoid self-referential edits.
