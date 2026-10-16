"""Accept unittest construction; provide disposable analysis stores and synthetic sealed SR runs."""

import json
from pathlib import Path
import tempfile
import time
import unittest
import uuid

from agentic_srim.storage.artifacts import checksum, seal, write_json
from agentic_srim.storage.database import connect, now
from agentic_srim.storage.exports import export_run_data
from analysis.builtins import install
from analysis.config import Settings
from analysis.runner import Dispatcher
from analysis.storage import Store


ROOT = Path(__file__).resolve().parents[2]


class AnalysisTest(unittest.TestCase):
    """Construct regression tests; expose isolated stores, workers and request helpers."""

    def setUp(self) -> None:
        """Accept no inputs; allocate disposable runtime and cleanup callbacks; return nothing."""
        parent = ROOT / "runtime" / "test_workspaces"
        parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(temporary.cleanup)
        self.runtime = Path(temporary.name).resolve()
        self.settings = Settings(self.runtime, stores={"local": self.runtime, "alias": self.runtime})
        self.store = Store(self.settings)
        self.worker = None

    def audit(self, **values) -> dict:
        """Accept request overrides; return unique audit fields plus supplied values."""
        return {"actor": "test coordinator", "reason": "Non-native regression", "idempotency_key": uuid.uuid4().hex, **values}

    def start(self) -> None:
        """Accept no inputs; start one supervised worker with cleanup; return nothing."""
        if self.worker is None:
            self.worker = Dispatcher(self.store)
            self.worker.start()
            self.addCleanup(self.worker.stop)

    def methods(self) -> dict:
        """Accept no inputs; explicitly register built-ins and return name/reference map."""
        return {reference["method_id"]: reference for reference in install(self.store, "test", "Register regression bundles")}

    def wait(self, job_id: str, timeout: float = 15) -> dict:
        """Accept job ID/test deadline seconds; await terminal state and return status."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            record = self.store.perform("get_analysis", {"analysis_id": job_id})
            if record["state"] not in ("QUEUED", "PREPARING", "RUNNING", "VERIFYING"):
                return record
            time.sleep(0.03)
        self.fail(f"Job did not finish: {job_id}")

    def verify(self, method: dict, **limits) -> dict:
        """Accept exact method/optional lower limits; verify and assert passing bundled tests; return status."""
        self.start()
        job = self.store.perform("verify_method", self.audit(schema_version=1, method=method, limits=limits))
        result = self.wait(job["analysis_id"])
        if result["state"] != "CHECKED":
            attempt = result["attempts"][-1]
            path = self.runtime / "analyses" / job["analysis_id"] / "attempts" / attempt["id"]
            logs = (path / "stderr.log").read_text() if (path / "stderr.log").exists() else ""
            self.fail(f"Verification failed: {result['error']} {logs}")
        return result

    def source(self, values: list = None, coordinates: list = None, **overrides) -> dict:
        """Accept sampled stopping values/keV grid/result overrides; seal synthetic run/export; return reference."""
        values = [0, 2, 2] if values is None else values
        coordinates = [1, 3, 9] if coordinates is None else coordinates
        run_id, attempt_id = uuid.uuid4().hex, uuid.uuid4().hex
        archive = self.runtime / "runs" / run_id / "attempts" / attempt_id
        archive.mkdir(parents=True)
        result = {"schema_version": 1, "analysis_version": "sr-table-v1", "units": {"energy": "keV", "stopping": "eV/nm", "length": "nm"},
                  "rows": [{"energy_keV": x, "electronic_stopping": y} for x, y in zip(coordinates, values)],
                  "provenance": {"run_id": run_id, "attempt_id": attempt_id},
                  "limitations": ["Synthetic software fixture; not native/scientific evidence."], **overrides}
        write_json(archive / "result.json", result)
        write_json(archive / "outcome.json", {"state": "CHECKED", "error": None})
        seal(archive)
        with connect(self.runtime) as connection:
            connection.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?)", (run_id, now(), now(), "CHECKED", json.dumps({"fixture": True}), "a" * 64, "b" * 64, json.dumps({"capability": "stopping_range"}), None))
            connection.execute("INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?)", (attempt_id, run_id, 1, "CHECKED", now(), now(), archive.relative_to(self.runtime).as_posix(), None, checksum(archive / "manifest.json")))
            connection.execute("INSERT INTO results VALUES(?,?,?,?)", (run_id, attempt_id, "sr-table-v1", json.dumps(result)))
        request = self.audit(schema_version=1, run_id=run_id, attempt_id=attempt_id, store_id="local", datasets=[{"name": "stopping_range"}])
        return export_run_data(self.runtime, request)["reference"]

    def summary_request(self, method: dict, source: dict, **values) -> dict:
        """Accept method/source/overrides; return explicit sampled-summary submission."""
        return self.audit(schema_version=1, method=method, inputs={"data": source}, parameters={"source": {"input": "data", "dataset": "stopping_range"},
                          "coordinate": "energy_keV", "value_columns": ["electronic_stopping"]}, limits={}, **values)

    def output(self, status: dict, name: str = "summary", **values) -> dict:
        """Accept terminal status/table name/page overrides; return pinned verified output."""
        reference = status["output"]
        return self.store.perform("get_analysis_output", {"analysis_id": status["analysis_id"], "attempt_id": reference["attempt_id"],
                                  "output_sha256": reference["manifest_sha256"], "name": name, **values})
