# Handoff

## Newly implemented

- `src/backend/resources.py` implements `ResourceReference`, `register_resource`, `resolve_resource`, and `verify_resource`. Storage is `memory/resources/<category>/<subcategory>/<name>/objects/<sha256>/{manifest.json,payload}`, with a sibling `versions.json` mapping immutable readable version labels to hashes. Category and subcategory are independently validated strings, not fixed enumerations. Initial uses are engine/electronic-stopping, engine/nuclear-stopping, cab/core, and cab/bond.
- The shared store preserves complete opaque payloads. Repeated bytes within one resource reuse the object; different resource names have independent storage. Runtime filenames and source/transformation provenance belong to simulation selections, not object manifests. There are no aliases, deletion APIs, or garbage collection.
- Registration copies/hashes bytes into staging, synchronizes payload and manifest, publishes the object by rename, then atomically publishes the version index. Failures retain/report staging or unreferenced complete objects. Retry can reuse a complete object. Version resolution reads index/manifest; verification additionally hashes the complete payload. Shared immutability is enforced through these APIs, not filesystem permissions.
- `ProjectPaths.resources` and base-directory setup now include only `memory/resources`. Registration creates deeper hierarchy on demand. Existing record-layout validation and path tests were updated for the required resources root. Older projects must rerun base setup before record operations.
- `src/srim/metadata.py` implements initialization, reading, starting, and finishing simulation records. The layout is `metadata.json`, `input/{request.json,resolved.json,engine/,continuation/}`, and `output/{raw/,parsed/}`. No shared resource payloads are copied into simulation folders.
- Request documents contain schema_version/parameters. Resolved documents contain schema_version/parameters/sources/resources/cab. The metadata module docstring and fixtures in `test/srim/test_metadata.py` document the exact schemas. Engine selections preserve complete references, runtime filenames, source descriptions/releases/citations, and optional transformation provenance referencing the original shared object.
- CAB derived snapshots reference separate core/bond datasets and selected entry identifiers. They preserve decimal-string composition, bond counts, contribution overrides with reasons/units, reference conditions, calculation provenance, and calculated/applied factors. Original dataset contributions are not duplicated. No-correction and directly supplied-factor modes are also supported.
- Initialization requires an empty allocated simulation and verifies resources before writes. Metadata is written last; partial initialization remains with exception notes. Request/resolved files are hashed at initialization and must remain unchanged. Engine and continuation files may be populated before start; start verifies resources and seals all local inputs. Success requires unchanged sealed inputs. Failure can be recorded before or after start and preserves available outputs even after input damage. Terminal states cannot restart.
- Only TRIM may declare continuation, with a different parent ID and exact imported-file manifests. It uses a new simulation record. Parent deletion does not invalidate the relationship. Exact TRIM restore compatibility is not established here.
- Resource operations use the resources-root lock; metadata operations use the simulation-parent lock and acquire resource locks only afterward. Lock contention fails immediately. JSON replacement is atomic per file, not transactional across initialization or registration. Link/junction checks retain the existing non-atomic filesystem-replacement limitation.
- `AGENTS.md` includes the user-requested requirement to announce the start of long coding tasks after approval.

## Verification

All 48 tests pass: 38 backend tests and 10 metadata tests. The combined run used the project virtual environment, `-B`, and `-W error`. Tests cover immutable labels, hierarchy extension, deduplication, custom resources, source changes, corrupt payloads/manifests/indexes, failed publication/retry, partial initialization, CAB references/precise overrides, continuation integrity, lifecycle transitions, modified inputs, lock contention, and the existing directory/tombstone behaviors.

Run the suites separately from the project root:

```powershell
D:\projects\agentic_srim\.venv\Scripts\python.exe -B -W error -m unittest discover -s test/backend -p "test_*.py" -v
D:\projects\agentic_srim\.venv\Scripts\python.exe -B -W error -m unittest discover -s test/srim -p "test_*.py" -v
```

AST/token checks passed for required module/class/function docstrings, function argument/return annotations, and absence of implementation comments.

## Remaining work

Engine-specific parameter schemas, input builders, CAB dataset interpretation/compatibility and calculation, resource transformations, worker preparation/execution, output parsers, and postprocessing/case metadata remain outside this implementation. No real simulation has been run and no production resource datasets have been registered. No next component has been selected.
