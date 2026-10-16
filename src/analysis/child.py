"""Accept an attempt job file/token; wait for ownership then execute trusted code.

Only stdlib startup occurs before the parent's acknowledgement. The lifetime pipe
protects POSIX descendants if the supervisor disappears. User code has the same
filesystem and service permissions as the configured Python interpreter.
"""

import os
from pathlib import Path
import signal
import sys
import threading


def main() -> int:
    """Accept job JSON path/token through argv and stdin; write result/report; return exit status."""
    path, token = Path(sys.argv[1]), sys.argv[2]
    if sys.stdin.buffer.readline().decode().strip() != token:
        return 3
    def parent_lifetime() -> None:
        """Accept no inputs; await parent-pipe closure and terminate the POSIX group; return nothing."""
        while os.read(sys.stdin.fileno(), 4096):
            pass
        if os.name != "nt":
            os.killpg(os.getpgrp(), signal.SIGKILL)
    if os.name != "nt":
        threading.Thread(target=parent_lifetime, daemon=True).start()
    import importlib.util
    import json
    import unittest
    from analysis.records import loads, write_json
    job = loads(path.read_text(encoding="utf-8"))
    bundle = Path(job["bundle"])
    sys.path.insert(0, str(bundle))
    if job["kind"] == "verification":
        suite = unittest.defaultTestLoader.discover(str(bundle / "tests"), pattern="test*.py")
        result = unittest.TextTestRunner(stream=sys.stderr, verbosity=2).run(suite)
        summary = {"tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped)}
        write_json(Path(job["context"]["output_directory"]) / "result.json",
                   {"schema_version": 1, "summary": summary, "tables": [], "figures": [], "limitations": ["Bundled tests only; no scientific acceptance."]})
        return 0 if result.wasSuccessful() and result.testsRun > len(result.skipped) else 1
    module_spec = importlib.util.spec_from_file_location("method", bundle / "method.py")
    module = importlib.util.module_from_spec(module_spec)
    sys.modules["method"] = module
    module_spec.loader.exec_module(module)
    try:
        result = module.run(job["inputs"], job["parameters"], job["context"])
    except Exception as exc:
        write_json(path.parent / "method_error.json", {"message": str(exc)[:16000], "type": type(exc).__name__})
        raise
    write_json(Path(job["context"]["output_directory"]) / "result.json", result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
