"""Verify saved SR/native TRIM/recovered TRIM output projection and evidence tampering."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import test_source as fixtures
from src.backend.records import CreateRequest, create_directories
from src.knowledge.retrieval_sources import project_output_values
from src.knowledge.retrieval_search import get_knowledge, search_knowledge
from src.knowledge.retrieval_schema import EMPTY_QUERY
from src.knowledge.retrieval_index import update_retrieval_index
from src.knowledge.source import project_simulation_inputs
from src.srim.common.files import _inventory
from src.srim.common.types import JSONObject
from src.srim.trim.inputs import render_trim_input
from src.srim.trim.outputs import parse_trim_outputs
from src.srim.trim.recovery import recover_trim_checkpoint


class TestRetrievalSources(fixtures.NativeFixture):
    """Use synthetic sealed native records and preserved parser/recovery fixture bytes."""

    def test_sr_list_output_pinning_and_tamper(self: TestRetrievalSources) -> None:
        """Keep duplicate rows, requested energies, availability and raw hashes."""
        raw = self.created.path / "output/raw/stopping.txt"
        raw.write_bytes(b"Synthetic SR output fixture")
        parsed = {"schema_version": 1, "energy_mode": "list", "source": {"filename": "stopping.txt", "sha256": hashlib.sha256(raw.read_bytes()).hexdigest(), "size_bytes": raw.stat().st_size}, "stopping_units": {"code": 7, "label": "eV/(1E15 atoms/cm2)"}, "rows": [{"energy": {"value": energy, "unit": "keV"}, "electronic_stopping": "1.25", "nuclear_stopping": ".005", "requested_energy_keV": energy} for energy in ("100", "500", "100")]}
        target = self.created.path / "output/parsed/result.json"
        target.parent.mkdir(exist_ok=True)
        target.write_text(json.dumps(parsed), encoding="utf-8")
        metadata_path = self.created.path / "metadata.json"
        metadata = json.loads(metadata_path.read_bytes())
        metadata["outputs"] = _inventory(self.created.path / "output", self.created.path)
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        source = project_simulation_inputs(self.paths, self.created.path.name)
        output = project_output_values(self.paths, source)
        self.assertEqual(output["state"], "available")
        self.assertEqual(len(output["tables"][0]["rows"]), 3)
        self.assertIsNone(output["tables"][0]["rows"][0][-1])
        update_retrieval_index(self.paths, [{"kind": "run", "reference": source["reference"]}])
        query = {**deepcopy(EMPTY_QUERY), "kinds": ["run"], "output_filter": {"result_sources": None, "complete": True, "summary": [], "table_groups": [{"table": "sr.rows", "clauses": [{"column": "energy", "operator": "eq", "value": "100", "unit": "keV"}, {"column": "nuclear_stopping", "operator": "between", "value": ["0", ".01"], "unit": "eV/(1E15 atoms/cm2)"}]}], "availability": []}}
        found = search_knowledge(self.paths, query)
        self.assertEqual(found["total"], 1)
        self.assertEqual([row["row"] for row in found["matches"][0]["output_matches"]["matches"]["row_locators"]], [0, 2])
        read = get_knowledge(self.paths, "run", source["reference"], table="sr.rows", offset=1, limit=1)
        self.assertEqual(read["table"]["rows"][0][0], "500")
        self.assertEqual(read["document"]["outputs"]["tables"][0]["rows"], [])
        raw.write_bytes(b"changed")
        self.assertEqual(project_output_values(self.paths, source)["state"], "rejected")
        self.assertEqual(search_knowledge(self.paths, query)["total"], 0)
        self.assertEqual(search_knowledge(self.paths, {**query, "output_filter": None})["total"], 1)

    def test_native_and_checkpoint_profiles(self: TestRetrievalSources) -> None:
        """Retain native/recovered differences, normalized rows and explicit unavailable tables."""
        for recovered in (False, True):
            with self.subTest(recovered=recovered):
                created = create_directories(self.paths, self.config, [CreateRequest("srim")]).completed[0]
                for name in ("input/engine", "output/raw", "output/parsed"):
                    (created.path / name).mkdir(parents=True, exist_ok=True)
                request = deepcopy(fixtures.TRIM_REQUEST)
                (created.path / "input/effective-request.json").write_text(json.dumps(request), encoding="utf-8")
                (created.path / "input/engine/TRIM.IN").write_bytes(render_trim_input(request))
                native = Path("test/srim/fixtures/trim/recovery/srim_2013_std/mode-02")
                files = {entry.name: entry.read_bytes() for entry in (native.iterdir() if recovered else (native / "native").iterdir()) if entry.suffix.lower() == (".sav" if recovered else ".txt")}
                if recovered:
                    scientific = recover_trim_checkpoint(files)
                    document = {"schema_version": 2, "engine": "trim", "result_source": "checkpoint-recovery", "native": {}, "recovery": scientific, "issues": [], "complete": True}
                else:
                    document = parse_trim_outputs(files)
                for name, payload in files.items():
                    (created.path / "output/raw" / name).write_bytes(payload)
                (created.path / "output/parsed/results.json").write_text(json.dumps(document), encoding="utf-8")
                metadata = json.loads((self.created.path / "metadata.json").read_bytes())
                metadata.update(id=created.path.name, engine={"name": "trim", "version": "2013", "executable": {"name": "TRIM.exe", "sha256": "b" * 64}}, inputs=_inventory(created.path / "input", created.path), outputs=_inventory(created.path / "output", created.path))
                (created.path / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
                source = project_simulation_inputs(self.paths, created.path.name)
                output = project_output_values(self.paths, source)
                self.assertIn(output["state"], ("available", "partial"), output["issues"])
                self.assertEqual(len(output["tables"]), 7)
                table = next(table for table in output["tables"] if table["name"] == "RANGE.txt")
                self.assertEqual(len(table["rows"]), 100)
                self.assertEqual(table["columns"][0]["unit"], "A")
                if recovered:
                    self.assertEqual(output["result_source"], "checkpoint-recovery")
                    self.assertTrue(output["warnings"])
                    self.assertIn("normalization", table["precision"])
                    self.assertNotIn("mean_range", [item["name"] for item in output["summary"]])

    def test_output_caps_and_malformed_schema(self: TestRetrievalSources) -> None:
        """Report unsupported/oversized saved output without hiding valid input evidence."""
        target = self.created.path / "output/parsed/result.json"
        target.parent.mkdir(exist_ok=True)
        target.write_text('{"schema_version":99}', encoding="utf-8")
        metadata_path = self.created.path / "metadata.json"
        metadata = json.loads(metadata_path.read_bytes())
        metadata["outputs"] = _inventory(self.created.path / "output", self.created.path)
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        source = project_simulation_inputs(self.paths, self.created.path.name)
        self.assertEqual(project_output_values(self.paths, source)["state"], "rejected")
        with patch("src.knowledge.retrieval_sources.MAX_RESULT_BYTES", 1):
            output = project_output_values(self.paths, source)
        self.assertIn("too_large", output["issues"][-1]["message"])
        target.write_text('{"schema_version":1,"engine":"trim","result_source":"unknown","complete":5}', encoding="utf-8")
        metadata["outputs"] = _inventory(self.created.path / "output", self.created.path)
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        source = project_simulation_inputs(self.paths, self.created.path.name)
        output = project_output_values(self.paths, source)
        self.assertEqual(output["state"], "rejected")
        self.assertIsNone(output["result_source"])
        self.assertIsNone(output["complete"])
        from src.knowledge.retrieval_sources import project_retrieval_document
        self.assertEqual(project_retrieval_document(self.paths, "run", source["reference"])["outputs"]["state"], "rejected")


    def test_sr_range_units_and_original_precision(self: TestRetrievalSources) -> None:
        """Convert saved range coordinates exactly while preserving printed token/unit pairs."""
        request = deepcopy(self.request)
        request["parameters"]["energy"] = {"mode": "range", "minimum_keV": "100", "maximum_keV": "500"}
        from src.srim.sr.inputs import render_sr_input
        (self.created.path / "input/request.json").write_text(json.dumps(request), encoding="utf-8")
        (self.created.path / "input/engine/SR.IN").write_bytes(render_sr_input(request))
        raw = self.created.path / "output/raw/stopping.txt"
        raw.write_bytes(b"Synthetic range fixture")
        row = {"energy": {"value": ".1", "unit": "MeV"}, "electronic_stopping": "1", "nuclear_stopping": "2", "projected_range": {"value": ".25", "unit": "um"}, "longitudinal_straggling": {"value": ".001", "unit": "mm"}, "lateral_straggling": {"value": "10", "unit": "A"}}
        document = {"schema_version": 1, "energy_mode": "range", "source": {"filename": "stopping.txt", "sha256": hashlib.sha256(raw.read_bytes()).hexdigest(), "size_bytes": raw.stat().st_size}, "stopping_units": {"code": 7, "label": "eV/(1E15 atoms/cm2)"}, "rows": [row]}
        target = self.created.path / "output/parsed/result.json"
        target.parent.mkdir(exist_ok=True)
        target.write_text(json.dumps(document), encoding="utf-8")
        metadata_path = self.created.path / "metadata.json"
        metadata = json.loads(metadata_path.read_bytes())
        metadata.update(inputs=_inventory(self.created.path / "input", self.created.path), outputs=_inventory(self.created.path / "output", self.created.path))
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        source = project_simulation_inputs(self.paths, self.created.path.name)
        output = project_output_values(self.paths, source)
        self.assertEqual(output["state"], "available", output["issues"])
        values = output["tables"][0]["rows"][0]
        self.assertEqual(values[0], "100")
        self.assertEqual(values[4], "2500")
        self.assertEqual(output["tables"][0]["precision"]["original_rows"][0], row)
