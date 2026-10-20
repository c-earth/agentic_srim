# Non-TRIM package and documentation review

Reviewed 2026-10-20. [Documentation index](../README.md) · [Current API](../reference/api.md) · [Formats](../reference/data-formats.md) · [Native evidence](../CAB_HANDOFF.md)

The review covered all 20 implementation modules in `src/backend` and `src/srim`, their embedded contracts, existing tests and non-TRIM documentation. Four behavior/validation findings remain open. The approved work changed documentation only; it did not fix implementation, tests or settings. Passing existing tests does not contradict the additional targeted reproductions below.

## Review boundary and method

Reviewed configuration, path/record allocation, resource storage, material parsing/editing/search, stopping coefficients, defaults, CAB arithmetic/recovery/cache, resolution, SR input/output, runtime preparation, execution and shared SR metadata/lifecycle. Read the CAB proposal, API catalog, tracker, handoffs, research notes and the SR-relevant compound comparison. TIN/TRIM transport findings, formats, GUI modes and proposed transport execution remain outside scope. One existing shared metadata continuation test runs incidentally in the unchanged SRIM suite; that is not a TRIM implementation review.

Compared documented contracts and cross-file data flow with implementation, ran both existing test directories with warnings as errors, and performed targeted synthetic-fixture probes of suspected discrepancies. No installed native executable was launched during this review. Existing native evidence availability and embedded generated-asset hashes were checked separately. Four pre-existing documentation modifications were preserved in the reorganization: root handoff, CAB handoff, integration tracker and documentation index.

## Open implementation findings

### R1: Core publication checks element identity after publication

**Priority: P2. Confirmed by reproduction; source unchanged.**

In [cab_cache.py](../../src/srim/cab_cache.py), `publish_cab_entry` line 489 filters lower Bragg entries with `entry["entry_id"] == entry["entry_id"]`. The comprehension variable shadows the outer core document, so that identity condition is always true. The corresponding loader check at line 365 correctly compares the lower entry with the outer document.

As a result, a structurally and arithmetically consistent carbon core entry can reference an oxygen Bragg entry with the same stopping value and pass the publication-side dependency check. The file is renamed into the immutable entry location before `load_cab_entry` rejects it. The call raises, but the invalid entry remains published. A valid earlier carbon entry under its original dependency key remains too, causing default core lookup's multiple-entry guard to fail for that descriptor.

Reproduction used `TestCABRecovery("test_core_estimates")` setup and its controlled 18-bond/carbon evidence, then resolved oxygen Bragg through `fake_bragg_run`, which gives both elements stopping `25`. A copy of the valid carbon entry replaced only its elemental Bragg reference with the oxygen reference and was passed to `publish_cab_entry` under the existing core descriptor. Observed:

```text
ValueError Core lower-level dependencies mismatch
invalid immutable entry published: True
carbon entry count in descriptor: 2
```

This probe used actual synthetic evidence files and the normal publisher/loader; their validation was not mocked. It does not show that ordinary `resolve_default_core` generates the wrong dependency. It shows an advanced publication API can leave unusable immutable state after rejecting bad input. A future fix needs prepublication element linkage and a regression assertion that rejection creates no entry; no fix is included here.

### R2: Direct recovery APIs do not enforce the inactive-generation restriction

**Priority: P2. Confirmed for direct bond recovery; analogous core path observed in code.**

The historical approved contract permits existing inactive default entries but prohibits recovery of absent entries in inactive versions. [resolution.py](../../src/srim/resolution.py) enforces this on version selections around lines 343–344 and 388–389. The public [cab_recovery.py](../../src/srim/cab_recovery.py) resolvers validate the supplied generation/descriptor and matching configured numerical baseline but do not establish that the generation is currently active before missing recovery.

Reproduction created generation A from a synthetic installation, changed `SR.exe` bytes and switched to generation B, then restored the original bytes without switching the pointer back. With generation B still active, `resolve_default_bond(..., defaults=A, bond="H-H")` passed baseline checks and reached the capture boundary. Only that boundary was replaced with a sentinel exception, so no GUI was launched:

```text
inactive default direct API: RuntimeError REACHED INACTIVE CAPTURE
capture_calls=1
baseline_changed=True
```

The active pointer check cannot be inferred from live baseline equality. Direct core recovery follows the same missing-entry path pattern; it was inspected but not separately driven through 18 captures in this probe. Current guides distinguish the stricter preparation path and advise using the current generation with direct APIs. The intended behavior under a defaults switch during an already pinned in-flight operation should be resolved in any future implementation proposal.

### R3: Preparation progress omits completions and can never reach its total

**Priority: P2 for callback-driven callers. Confirmed by reproduction.**

[resolution.py](../../src/srim/resolution.py) sets total to bond count plus twice the composition count at line 323. It increments completion counters for every contribution, but emits bond/core callbacks only when resolving previously absent default values. Previously available default entries, supplied values, fallback cores and zero-count bonds do not all emit corresponding events. A no-effective-bonds short circuit also leaves unused core/Bragg work in the advertised total.

Reproduction used one H atom, one `H-H` bond with inline strength `10`, the existing fixed H core and controlled Bragg output. Resolution succeeded. First call:

```json
[{"kind":"bragg","entry_id":"z001","state":"calculated","completed":2,"total":3}]
```

Second call, with the Bragg entry already available:

```json
[{"kind":"bragg","entry_id":"z001","state":"cached","completed":2,"total":3}]
```

Neither call reported completion 3/3. A caller waiting for callback totals can incorrectly remain incomplete despite a successfully returned bundle. Dedicated `populate_bragg`/`populate_default_cab` loops do report each selected outer entry; this finding is about `resolve_sr_inputs` and `prepare_sr_simulation`. Current documentation labels preparation progress advisory rather than promising complete progress coverage.

### R4: Range parsing accepts output outside the requested range

**Priority: P2 validation gap. Confirmed; current documentation now states the boundary.**

[outputs.py](../../src/srim/outputs.py) passes `request` into `_parse_range`, but the range parser does not use it to compare requested bounds. It validates table headings, numerical rows, increasing energies and the footer. Main parser validation checks the selected stopping-unit label, but projectile/target header values are checked for shape rather than full equality to the effective request.

A valid numeric request with range `1`–`2` keV and stopping-unit code 5 accepted the existing `sr-range.txt` fixture:

```text
range request 1..2 keV accepted output: 79 rows
first energy: 10.00 keV
last energy: 10.00 MeV
```

No process or filesystem mutation was needed. List mode has stronger requested-energy/row-count validation. Runtime integrity checks remain valuable, but they do not make the pure range parser a full request/output identity validator. This finding is a validation gap rather than proof that normal native execution emits unrelated output. Any future bounds validation needs an explicit policy for the engine's printed precision and range grid; no tolerance was invented or code changed in this review.

## Documentation discrepancies and resolution

| Discrepancy | Evidence and documentation treatment |
| --- | --- |
| No project README or practical entry point; index led users into proposal/handoff documents | Added a root utility/prerequisites README, sequential first-calculation guide, resource guide, current API reference and formats page; made the index task based. |
| Completed-state notices coexist with "No implementation has started", proposed-only wording and baseline 90-test claims | Moved proposal/catalog/tracker to `history/` with explicit historical framing. Preserved approved text rather than rewriting decision history to hide later discrepancies. Current review records 124-test verification separately. |
| Research notes say old ignored experiments remain locally present | The old experiment cache was deleted on 2026-10-20. Corrected the inventory availability statement and compound-comparison notice. Kept the handoff's distinction between deleted evidence and later fresh artifacts. |
| Python 3.12 type-alias syntax can be mistaken for runtime compatibility | Current code calls Windows `os.path.isreserved`, requiring Python 3.13+ APIs. Current docs state 3.13+ and identify the tested interpreter as 3.13.13; no Python 3.12 compatibility claim is made. |
| Historical generic target-limit descriptions obscure separate boundaries | Native compounds allow 1–8 elements. Extended material storage allows 1–118 unique elements. Unresolved SR construction rejects Z above 92 and uses unique Z. Low-level numeric engine requests permit up to 100 distinct `(Z,mass)` rows. A probe validated 100 separate carbon-mass rows. Current reference distinguishes these boundaries. |
| Earlier optional-comma native parsing appears alongside the later strict amendment | The final amendment supersedes flexibility: parsers require the name separator and reject trailing composition commas; installation import normalizes known defects first. Current docs state this directly and preserve anomaly/source qualifications. |
| Broad canonical-JSON language does not describe every writer | Default pointers/descriptors/calculated entries use sorted canonical JSON, but runtime manifest and GUI correction JSON writers do not universally sort keys. Current formats describe the distinction and hashing of actual bytes. |
| Coefficient import can be mistaken for complete semantic validation | Source-only variant APIs preserve opaque bytes. Effective selection resolution checks row structure/order, and replacement mode checks complete tables; numerical meanings of all coefficient fields are not interpreted. Current guide/reference state these limits. |
| Worker directories look disposable despite being referenced evidence | CAB entry verification hashes linked worker capture files and dependency simulations; generic deletion performs no reference checks. Added retention guidance and documented the absence of reference-aware garbage collection. |
| Several contracts describe behavior more strongly than observed | R1–R4 remain explicitly open; current guides/reference describe actual limitations without silently changing approved implementation design. |

## Module coverage

| Module | Reviewed responsibilities and result |
| --- | --- |
| `backend/config.py` | Strict TOML schema, timezone normalization, immutable settings, path syntax versus installation validation; documented boundaries. |
| `backend/paths.py` | Derived layout, link checks, creation reports/partial failure and no rollback/locking; reflected in API/layout docs. |
| `backend/records.py` | Batch prevalidation, names, timezone conversion, parent locks, worker layout, pending/deleted tombstones, retries and reserved identities; retention consequence documented. |
| `backend/resources.py` | Immutable payload registration, version/reuse allocation, schema-one/two manifests, provenance, hash verification and failure staging; generic versus domain validation documented. |
| `srim/compatibility.py` | Version preference/fallback and conservative import-only repairs; checked against final approved amendment. |
| `srim/defaults.py` | Installation validation, coordinated pointer, baseline identity, content reuse, H convention and switch failure/concurrency handling; matching baseline versus active-generation distinction recorded. |
| `srim/material.py` | ATOMDATA/native/extended parsing, original-index edits, source spans, names, exact ratios, mass conversion, target construction and variants; storage/engine limits and anomalous native parsing qualified. |
| `srim/electronic.py` | Raw import, complete-row edits, field count/Z/order, provenance and unchanged bytes; no unclaimed coefficient physics validation. |
| `srim/nuclear.py` | Triangular pair identity/order, complete-row replacement, reversed-key rejection, provenance and raw import; documented separately from normalized bond endpoints. |
| `srim/cab.py` | Element/bond normalization, Decimal strings, legacy numeric import, sparse datasets, removal, lazy snapshots and frozen-copy provenance. |
| `srim/cab_constants.py` | Fixed precision/rounding/procedure versions, 18-bond order and embedded generated assets; fixture hashes verified. |
| `srim/cab_cache.py` | Recipe/entry identity, DAG/evidence/arithmetic checks, semantic duplicate/conflict handling, staging/locks and postpublication validation; R1. |
| `srim/bragg.py` | Pinned effective recipe, H/125-mass gas reference, ordinary dependency records, automatic recipe versions, positive stopping and bulk callbacks. |
| `srim/cab_recovery.py` | Owned GUI/process lifecycle, generated mock inventory, bounded cleanup, threshold counts, 18 core estimates, cache reuse and bulk recovery; R2. Native GUI was not rerun. |
| `srim/resolution.py` | All-role selections, source/effective snapshots, version publication, active/inactive defaults, user/removal fallbacks, arithmetic reconstruction and evidence dependencies; R3. |
| `srim/inputs.py` | Intent/engine separation, exact correction modes, decimal syntax, target identities, range/list bounds/order and ASCII/CRLF output. |
| `srim/outputs.py` | Strict output shape/units, printed precision, list energy linkage, range rows/footer, raw identity and preserved strings; R4. |
| `srim/metadata.py` | Shared SR schemas, inventories, allocation identity, initialization publication order, reference collection, start/seal/finish and terminal guards. TRIM continuation behavior excluded from substantive review. |
| `srim/runtime.py` | Allocation checks, dependency resolution before locking, pinned five-file staging, source identity and failure artifacts; first-run example checked against this boundary. |
| `srim/execution.py` | Exact preflight/runtime hashes, project-wide execution lock, subprocess timeout/interruption, raw/diagnostic preservation, parsing and failed-metadata versus exception behavior. |

This is a code/documentation review, not a proof of all numeric algorithms, race-free filesystem operations or exhaustive validation of hostile caller objects. Native numerical compatibility remains limited to the recorded checks below.

## Verification evidence

Executed from the repository root with Python **3.13.13**, using the required absolute interpreter:

```powershell
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' -W error -m unittest discover -s test/backend -p 'test_*.py' -v
& 'D:\projects\agentic_srim\.venv\Scripts\python.exe' -W error -m unittest discover -s test/srim -p 'test_*.py' -v
```

Results: **38 backend tests passed** in 1.320 seconds; **86 SRIM tests passed** in 68.978 seconds. No source changes followed these runs. Targeted probes used temporary synthetic workspaces with normal cleanup, existing fixture helpers and controlled subprocess/capture boundaries. The range/row-limit probes were pure. These probes were run for review and are recorded above; no new test files or persistent reproduction scripts were added.

Generated asset checks matched the archived design: capture manifest SHA-256 `a186aeb2d87c53bdda0f0c550e21b583a748a9feeeae97280daf6bf7a4afed15`; mock header 2,089 bytes, SHA-256 `d40af9963517683c78ad2772e7160ddc54b2e6af11a055505138962408447b97`.

Previously recorded `.cache/fresh-recovery-both-9p7q5ne2/verification.json` and `.cache/gui-cleanup-check-_r8hkuej/verification.json` exist and were readable during review. The earlier deleted experiment paths were not available. This review did not recreate the historical native runs or independently extend their numerical claims: representative 2008/2013 Standard bond/carbon/methane agreement is not validation of all 91 non-H cores, all compounds, Pro or universal cross-version equivalence.

Documentation verification passed: all six guide Python blocks parsed; the complete first calculation ran through the public APIs against controlled synthetic output; compound search, local selections and CAB variant examples ran; all 45 public function signatures and 14 public classes were covered; all 97 local links/anchors resolved; JSON/TOML examples, Markdown fences and whitespace passed. Approved archive moves, preserved pre-existing edits, untouched TIN/TRIM findings and unchanged source/tests/settings were checked. Examples demonstrate API behavior; controlled synthetic output does not establish physical values. No commit or publication was performed.
