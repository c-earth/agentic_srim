# CAB handoff - 2026-10-20

## Current state

The approved CAB integration and import compatibility amendment are implemented. The standalone `src/srim/capture_correction.py` has been replaced by integrated recovery. Complete contracts are in CAB_INTEGRATION_PROPOSAL.md and CAB_API_CATALOG.md, including their final compatibility amendment. Historical research/evidence inventory is in CAB_RESEARCH_NOTES.md. The user authorized handoff cleanup and a local commit; publication was not requested.

Installation imports now normalize `_VERSION` to the logical `VERSION`, correct only row 37 `Ru Rubidium` to `Rb Rubidium`, insert a missing quoted-name/composition separator, and remove one trailing composition comma. Corrections happen in memory before resource or worker saves. Source hashes/changes are recorded; installed files stay unchanged. The native compound parser is strict. Previously registered malformed immutable compound resources require reimport; no migration rewrites them.

## Verification and retained evidence

- Latest suite: 124 tests passed with warnings as errors (38 backend, 86 SRIM). Docstrings, signature annotations, absence of implementation comments and Git whitespace checks passed. No functional source changes followed these checks; final cleanup removed a trailing blank line.
- 2013 Standard representative live recovery: `.cache/cab-integration-live-rcj2h13z/verification.json` (18 bonds, carbon across 18 estimates, methane record 92, launch-free cache reuse, timeout and interruption).
- 2008 representative live recovery in the authorized mapped/corrected copy: `.cache/cab-integration-2008-live-krrx1o3r/verification.json`. All 18 bond calculations and carbon estimates match 2013 exactly. Methane raw stopping output is byte-identical. Carbon mean is `6.268410214222222222222222222`, spread `0.005650190`; CAB factor is `1.004815621499447362865268308`. These cases do not establish general cross-version equivalence or full 91-core validation.
- Native installation normalized-import checks: `.cache/cab-compatibility-import-verification.json` (2008: 345 compounds; 2013 Standard: 357).
- Native 2008 after compatibility implementation: `.cache/cab-integration-2008-live-krrx1o3r/native-compatibility-verification.json`. Reused all 18 bonds and carbon from the verified matching baseline; fresh methane simulation, launch-free cache repeat and forced Bragg timeout passed. Interruption propagated with retained evidence and a GUI cleanup-timeout note. Installed input hashes remained unchanged.

The user cleared all project `.cache/` contents on 2026-10-20 after closing its six SRIM processes. The empty directory remains. Evidence paths above are historical and no longer exist locally; the numerical results describe completed earlier checks. Raw artifacts and experiment scripts are not part of Git.

## Unresolved work

Fresh native 2008 recovery captured the first correction but failed twice when the existing GUI close helper exceeded its five-second timeout. Evidence: `.cache/cab-native-2008-compat-tkeghao8/verification.json` and `.cache/cab-native-2008-compat-27ik0rl_/verification.json`. Owned-process termination retained failure evidence. Cleanup timing was not changed within the compatibility scope. Do not claim full fresh live recovery passed after the amendment.

No further implementation work is outstanding within the approved scope. Investigating or changing GUI cleanup is a separate next task requiring a proposal before code changes.
