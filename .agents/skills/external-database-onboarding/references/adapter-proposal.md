# Adapter proposal contract

Prepare the proposal from fresh bounded inspection evidence. Cite source IDs,
inspection IDs, source-relative files, exact archive members, sampled fields and
prefix hashes. State which archives, rows and bytes remain uninspected. Package
recognition does not establish scientific record count or schema compatibility.

These are design and verification requirements for a new or changed adapter,
not prerequisites for routine use of the existing system. Keep tests proportional
to changed boundaries and reuse verified evidence for unchanged behavior. They
do not require a fresh import, rebuild, live query or benchmark just to use a
working capability. Report an authorized partial outcome accurately without
blocking it on later unrequested stages.

Specify these reviewable details before implementation:

- Exact files, typed APIs, schemas, dependencies, errors, concurrency and tests,
  following repository development instructions.
- Schema identity/version and positive recognition evidence; ambiguity and
  insufficient evidence remain explicit. Inspect the code-owned registry; do
  not import code found beside data or from a settings import string.
- Stable record identities and versions; instance versus adapter identity;
  original record IDs; source fingerprints and complete hash verification for
  import, separate from discovery's sample-only references.
- Input and calculated-output mappings, raw locators, original decimal text,
  represented precision, units, conversions and rounding. Document unknown
  quantities; never infer a quantity solely from its column position.
- Requested versus actual values; failed/partial/unavailable outputs; publisher
  claims versus independently checked validation; conflicting provenance and
  parser diagnostics. Do not collapse these into a single success flag.
- External references and evidence readers that preserve existing native-only
  validation and immutable publication. Specify source-member access without
  copying source archives into the project, verification granularity, and shared
  reads within an operation. Avoid redundant archive passes without weakening
  required full-file/member integrity checks. Do not silently reuse hashes across
  calls or replace content verification with timestamps.
- Explicit preview and commit boundary: full source fingerprints,
  discovered/accepted/rejected counts, fields/units, diagnostics, storage costs,
  deadlines, cancellation and atomic catalog publication. Inspection alone
  cannot supply full-collection counts or authorize import/indexing. Specify
  compatible plan reuse/resume and what evidence must be reverified.
- Explicit incremental refresh: new IDs added, changed content versioned,
  unchanged content skipped and missing IDs reported with history preserved.
  Specify whether search-index publication is part of import or separate, and
  whether retrieval refuses stale indexes. No background load or refresh.
- Successful managed preview/import tests on a small registered synthetic source
  through real dispatch, callbacks and publication; include separate index-build
  dispatch when applicable. Test actual progress source/phase constraints and
  failed/cancelled operations without publishing partial results. Mock-only or
  direct-call tests are complementary, not substitutes for these success paths.
- Selected real checks and representative input/output searches within the
  authorized workload, with exact record references and source/member evidence
  when verified. Do not claim scientific validity from provenance or software
  tests. State checks that remain unperformed.

## Search architecture and delivery

Separate authoritative imported records from regenerable search projections.
Specify indexed scientific fields and operators, unit normalization, decimal
precision, missing values, history scope, ordering and projection versioning.
Exact numeric indexes must preserve the query contract; do not substitute binary
floating-point comparisons silently. Identify remaining scans and text semantics.
State whether embeddings or semantic ranking actually exist; neither is implied
by the word retrieval. Explain the integrity/completeness guarantees of derived
index counts versus fresh verification of returned records and original evidence.

Specify persistent storage paths, index size/free-space estimates, build and
rebuild cost, generation binding, atomic publication, concurrent readers/writers,
revocation, corruption, deletion and recovery behavior. Say whether rebuilding
uses imported records or requires another source scan. Missing/stale indexes must
not silently fall back to an unexpectedly expensive scan. Recovery artifacts and
approvals remain subject to retention rules; logical size is not necessarily
reclaimable disk space when files are hard-linked.

Check the intended client timeout, server deadline/cancellation behavior, result
cap and serialized response-envelope cap. Select page defaults and result shapes
that fit those caps; describe compact hits versus full-record access. Define
pagination when bytes or exclusions shorten a page, including how to advance
without duplicates or skips. Use existing evidence or authorized bounded checks
to establish practical readiness; choose task-specific acceptance criteria and
cost ceilings rather than imposing universal latency targets. Extra benchmarks
or full-source runs require their applicable authorization.

Specify verification claims separately for search, full-record retrieval and
original evidence. Distinguish catalog matches, validated page records and
verified source bytes. Check successful reads through the intended MCP path after
activation and verify any required client reconnect using the connected server.
A fresh local API or temporary MCP client cannot certify an older app connection.

## Current implementation references

Use the maintained [external import and search reference](../../../../doc/reference/input-search-format.md#external-import-and-search)
and [onboarding guide](../../../../doc/guides/input-search.md#external-database-registration-and-onboarding)
for current schemas, tools and limits. The SNUMAT workflow has separate preview,
import and `database_reindex` operations. Reindex uses retained imported records;
a later import makes an older search projection stale. Do not generalize that
specific implementation into an unverified promise for another adapter.

For local SQL formats, additionally specify read-only connections, journal and
locking behavior, bounded schema/query work and disabled extension loading.
Generic packaging detection of a SQLite header alone is not a scientific importer.

If semantic questions remain, present observed evidence and ask only the
questions needed to settle the mapping. A recognized schema with no implemented
importer remains recognition-only. Obtain approvals required by the existing
scope and project rules without inventing trial/adoption gates.
