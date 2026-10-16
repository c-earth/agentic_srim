"""Accept an explicit Python interpreter; inspect its exact dependency identity."""

from pathlib import Path
import subprocess
import os

from analysis.records import AnalysisError, checksum, digest, loads


def inspect(python: Path, timeout: float = 15) -> dict:
    """Accept interpreter path/deadline seconds; return exact fingerprint/platform/dependency inventory."""
    code = "import sys,os,importlib.metadata as m,json; print(json.dumps({'python':sys.version,'platform':[sys.platform,list(sys.getwindowsversion()) if os.name=='nt' else list(os.uname())],'executable':sys.executable,'packages':sorted((d.metadata['Name'].lower(),d.version) for d in m.distributions())}))"
    try:
        result = subprocess.run([str(python), "-I", "-B", "-c", code], stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except subprocess.TimeoutExpired as exc:
        raise AnalysisError("timed_out", "Environment inspection exceeded its preparation deadline") from exc
    if result.returncode:
        raise AnalysisError("environment_error", "Configured interpreter cannot be inspected")
    data = loads(result.stdout.decode("utf-8"))
    data["interpreter_sha256"] = checksum(python)
    data["service_code"] = {path.name: checksum(path) for path in sorted(Path(__file__).parent.glob("*.py"))}
    return {**data, "fingerprint": digest(data)}


def compatible(specification: dict, environment: dict) -> None:
    """Accept method requirements/environment; reject missing/inexact dependencies; return nothing."""
    required = specification["python"]
    actual = environment["python"].split()[0]
    if actual != required and ".".join(actual.split(".")[:2]) != required:
        raise AnalysisError("environment_error", "Method requires an exact Python version/minor", {"required": required, "actual": actual})
    installed = dict(environment["packages"])
    for package, version in specification["requirements"].items():
        if installed.get(package.lower()) != version:
            raise AnalysisError("environment_error", "Missing/incompatible dependency", {"package": package, "required": version})
