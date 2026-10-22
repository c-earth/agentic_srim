# Current TRIM continuation direction

Decision date: 2026-10-22. The user confirmed the `.sav` recovery route and explicitly requested cleanup of the mistaken return to GUI export automation.

## Active direction

Use the complete preserved checkpoint set for continued results. Keep completion/checkpoint-save handling, then integrate the existing generalized recovery API with result parsing, provenance/precision reporting and lifecycle verification. Recovery v2 is committed as `9a51222`; it currently produces labeled recovered data and does not make missing native text files satisfy execution completion checks. Integration needs its own complete approved design before implementation. Do not silently treat recovered data as byte-identical native exports or as independently validated physics.

## Abandoned branch

F-button result exports, directory-chooser automation, directory-item path readers and 2008 export-button mapping are abandoned. Old proposals and diagnostic successes are historical evidence, not authorization to return to this route. The user must explicitly reopen this branch before further experiments or implementation. Do not ask for approval to revive it as the default next step.

The chooser detour changed no production source. Its standalone diagnostic, mock artifacts and four generated native case records were removed during cleanup; both installations and parent records had been verified unchanged, and all diagnostic processes had stopped. The later production path-reader and 2008 mapping amendment was never implemented.

## Export code removed

The user approved [scoped source cleanup](TRIM_EXPORT_CLEANUP_PROPOSAL.md). The export-only selectors, policy ordering field, completed-export state, file polling and export-gated menu Exit have been removed from production code. Tests naming these behaviors only check their rejection or absence. Do not restore them from Git history or old cache scripts.

Fresh cascade responses, pure completion/default-save selectors, unrelated parser/availability work, complete checkpoint preservation, the startup filter (`57b3d74`) and optional execution deadlines (`5ccfedd`) remain. Pure save-selector completion is not execution success. Automatic continuation with a non-None cascade_output now raises ValueError before staging/native launch until `.sav` result/lifecycle integration is implemented. Report-only continuation with None remains. New fresh policies use nested schema version 2 without export fields; legacy policy version 1 is rejected, without migration or historical record edits. This temporary guard is approved; checkpoint-result integration remains unimplemented.

Cleanup verification passed all 127 TRIM tests with warnings as errors, including 43 targeted dialog/native/runtime tests. Production searches confirm the abandoned export implementation is absent; negative tests verify rejection. No native experiment was performed. The [cleanup handoff](TRIM_CLEANUP_HANDOFF.md) records the standalone commit boundary, staged verification and remaining uncommitted parser work.

## Next task

Prepare the checkpoint-result integration proposal, including typed APIs, result schemas, provenance/precision qualifications, post-save process cleanup, compatibility and meaningful regression checks. No native export/chooser experiments are required or authorized for that proposal.
