# CAB handoff - 2026-10-20

[Documentation index](README.md) · [Current API reference](reference/api.md) · [Non-TRIM review](reviews/non-trim-review.md)

This page records implementation/native-validation status and evidence. It is not the user guide. Historical design, API approval and delivery checklist documents now live in `history/`; their proposal-stage language is historical. The later review records open code discrepancies without modifying implementation.

## Current state

The approved CAB integration and import compatibility amendment are implemented. The standalone `src/srim/capture_correction.py` has been replaced by integrated recovery. Approved design contracts are in [the historical proposal](history/CAB_INTEGRATION_PROPOSAL.md) and [API catalog](history/CAB_API_CATALOG.md), including their final compatibility amendment. Historical research/evidence inventory is in [CAB research notes](history/CAB_RESEARCH_NOTES.md). Later documentation and SRModule visibility work is complete and authorized for local commit; its latest verification and remaining qualifications are in the [root handoff](../HANDOFF.md#srmodule-window-settings). Publication was not requested.

Installation imports now normalize `_VERSION` to the logical `VERSION`, correct only row 37 `Ru Rubidium` to `Rb Rubidium`, insert a missing quoted-name/composition separator, and remove one trailing composition comma. Corrections happen in memory before resource or worker saves. Source hashes/changes are recorded; installed files stay unchanged. The native compound parser is strict. Previously registered malformed immutable compound resources require reimport; no migration rewrites them.

## Verification and retained evidence

- CAB integration suite: 124 tests passed with warnings as errors (38 backend, 86 SRIM). Docstrings, signature annotations, absence of implementation comments and Git whitespace checks passed at that stage. The later visibility feature brings the passing suite to 127 tests; see the root handoff for that separate scope.
- 2013 Standard representative live recovery: `.cache/cab-integration-live-rcj2h13z/verification.json` (18 bonds, carbon across 18 estimates, methane record 92, launch-free cache reuse, timeout and interruption).
- 2008 representative live recovery in the authorized mapped/corrected copy: `.cache/cab-integration-2008-live-krrx1o3r/verification.json`. All 18 bond calculations and carbon estimates match 2013 exactly. Methane raw stopping output is byte-identical. Carbon mean is `6.268410214222222222222222222`, spread `0.005650190`; CAB factor is `1.004815621499447362865268308`. These cases do not establish general cross-version equivalence or full 91-core validation.
- Native installation normalized-import checks: `.cache/cab-compatibility-import-verification.json` (2008: 345 compounds; 2013 Standard: 357).
- Native 2008 after compatibility implementation: `.cache/cab-integration-2008-live-krrx1o3r/native-compatibility-verification.json`. Reused all 18 bonds and carbon from the verified matching baseline; fresh methane simulation, launch-free cache repeat and forced Bragg timeout passed. Interruption propagated with retained evidence and a GUI cleanup-timeout note. Installed input hashes remained unchanged.

The user cleared all project `.cache/` contents on 2026-10-20 after closing its six SRIM processes, retaining the directory for later use. Evidence paths above are historical and no longer exist locally; the numerical results describe completed earlier checks. Raw artifacts and experiment scripts are not part of Git. Later fresh artifacts are listed below.

## Latest fresh native validation

Fresh native recovery on both 2008 and 2013 Standard passed on 2026-10-20 without implementation changes or an additional execution approval prompt. Evidence: `.cache/fresh-recovery-both-9p7q5ne2/verification.json`; harness: `.cache/fresh-recovery-both.py`. These are new artifacts generated after the cache clear noted above.

Each version freshly recovered all 18 bonds and carbon across 18 estimates, completed 44 captures with successful cleanup, and ran methane record 92 at 126 keV. All bond calculations, carbon estimates and parsed methane outputs match exactly between versions. Methane electronic/nuclear stopping is `7.226E+00` / `9.171E-03` eV/(1E15 atoms/cm2). All eight installation input hashes remain unchanged.

Before the full runs, three individual fresh captures per version also passed; GUI close phases took approximately 0.50-0.53 seconds. Evidence: `.cache/gui-cleanup-check-_r8hkuej/verification.json`.

## Remaining qualification

The historical five-second GUI cleanup timeout did not reproduce in either full fresh run. Its cause remains unconfirmed; no cleanup code or timeout was changed. This supersedes the previous limitation that fresh recovery after the compatibility amendment had not passed. No full 91-core or general cross-version equivalence is claimed. The next larger work area is TRIM input/runtime planning; no implementation for it has been approved.
