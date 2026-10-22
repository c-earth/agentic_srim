"""Adapt frozen checkpoint recovery into explicitly sourced pipeline results."""

from collections.abc import Mapping
from src.srim.common.types import JSONObject
from src.srim.trim.outputs import parse_trim_outputs
from src.srim.trim.recovery import recover_trim_checkpoint


def parse_trim_checkpoint_results(files: Mapping[str, bytes]) -> JSONObject:
    """Validate frozen runtime-relative files and return checkpoint results.

    files maps safe runtime paths to immutable bytes. Return structural recovery
    completeness, recovery-v2 data and independent native diagnostics. Invalid
    caller mappings raise ValueError; invalid or nested checkpoint contents are
    retained as issues. Perform no I/O, mutation or execution certification.
    """
    native = parse_trim_outputs(files)
    result: JSONObject = {"schema_version": 2, "engine": "trim", "result_source": "checkpoint-recovery", "native": native, "recovery": None, "issues": [], "complete": False}
    checkpoint = {name.split("/", 1)[1]: data for name, data in files.items() if name.casefold().startswith("srim restore/")}
    try:
        if any("/" in name for name in checkpoint):
            raise ValueError("Nested checkpoint entries are unsupported")
        result["recovery"] = recover_trim_checkpoint(checkpoint)
        result["complete"] = True
    except ValueError as error:
        result["issues"].append({"path": "SRIM Restore", "code": "checkpoint-recovery", "message": str(error)})
    return result
