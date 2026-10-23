# TRIM continuation

Decision recorded 2026-10-22; checkpoint-result and lifecycle integration is
implemented. This is the current policy for resumed results.

## Use the preserved checkpoint

Continued calculations recover their numerical results from the complete,
preserved `.sav` checkpoint set. The runner stops its owned native job before
publishing recovered results and verifies the live/preserved checkpoint hashes,
requested counts, saved inputs and applicable configuration integrity checks.
Fresh calculations retain their native-text parsing requirements. Recovered
values retain their own provenance, precision, availability and inferred-layout
warnings; they are not byte-identical native exports.

GUI F-button result exports, directory-chooser automation, directory-item path
readers and 2008 export-button mapping are abandoned. Do not restore or recommend
that branch from historical proposals or scripts unless the user explicitly
reopens it. Preserve fresh cascade handling, checkpoint saving/recovery, timeout
support and the committed startup filter.

## Complete an automatic continuation

The default `cascade_output=None` remains report-only. An explicitly selected
automatic policy inspects the exact completion prompt before responding:

1. Two matching valid final checkpoints at least one second apart permit the
   owned job to stop without completion acknowledgment or a Save command.
2. If a final checkpoint is unavailable after ten seconds, ordinary continuation
   may use one verified Yes/default-save pair and a separate readiness deadline.
3. Event continuation never acknowledges completion or opens a save form. Without
   a final checkpoint it fails `checkpoint-required`; the caller must explicitly
   select an autosave schedule covering the requested final count.

Invalid or unstable checkpoints do not authorize saving as repair. Timeout,
cancellation, unknown popups and failed integrity checks retain their failure
behavior. Natural clean exit remains supported. Requested autosave settings are
never silently changed. The 2008 specified-ion-full `End of calculation` Yes/No
prompt is recognized for inspection only.

New automatic policies use nested schema 4. Legacy nested schemas 1/2/3 require
newly prepared runtimes; historical records are not rewritten. Resumed config
assessment qualifies only strictly parsed known colour/help/serialization
changes while retaining original bytes and protecting label size and the
secondary-recoil limit. Fresh and other input checks remain byte-exact.

## Read the operational details

- [Execute and continue TRIM](../guides/trim-execution.md)
- [Execution APIs and report schemas](../reference/trim-execution-format.md)
- [Offline checkpoint recovery](../guides/trim-checkpoint-recovery.md)
- [Measured native coverage and its limits](../reference/validation.md#trim-native-checks)

The continuation matrix covers two modes on three installations. Stable hashes
do not prove atomic native saving, every-mode unattended support or independent
physical accuracy. The [validation reference](../reference/validation.md) records
review closeouts and coverage qualifications.
