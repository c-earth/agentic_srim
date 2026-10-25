"""Exercise explicit external preview, immutable publication and verified refresh."""
from __future__ import annotations

from copy import deepcopy
import csv
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import shutil
import tarfile
from tempfile import TemporaryDirectory
import unittest
import time
from uuid import uuid4
from src.knowledge import external_index
from src.mcp.common.contracts import JOB_SCHEMA, load_context
from src.mcp.common.jobs import JobManager
from src.mcp.project_mcp.operations import write_operation
from unittest.mock import patch

from src.backend.paths import get_project_paths
from src.backend.records import _lock_parent
from src.knowledge.database_sources import list_database_sources
from src.knowledge.external_index import preview_external_import, import_external_preview, reindex_external_database, search_external_database, get_external_record, read_external_status
from src.knowledge.external_schema import EMPTY_EXTERNAL_QUERY
from src.knowledge.external_sources import ARCHIVES, DATASET_ROOT, SOURCE_DEADLINE
from test_external_sources import ROOT_MEMBERS
from test_snumat_adapter import CASE_ID, MEMBERS, MANIFEST, MAPPING


class TestExternalIndex(unittest.TestCase):
    """Verify catalog publication and source boundaries using local synthetic data."""

    def test_preview_has_no_search_publication(self: TestExternalIndex) -> None:
        """Preview writes reviewable plans without creating searchable catalog rows."""
        cache = Path(__file__).resolve().parents[2] / ".cache/external-index-tests"
        cache.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=cache) as temporary:
            root = Path(temporary)
            source = root / "dataset"
            source.mkdir()
            for name, payload in ROOT_MEMBERS.items():
                (source / name).write_bytes(payload)
            (root / "settings.toml").write_text('[srim]\nroot = ' + json.dumps(str(root / "unused")) + '\n[knowledge]\ndatabase_paths = ' + json.dumps([str(source)]) + '\n', encoding="utf-8")
            paths = get_project_paths(root)
            source_id = list_database_sources(paths)["sources"][0]["source_id"]
            before = {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in ROOT_MEMBERS}
            for invalid in (True, 0, 3601, 1.5):
                with self.assertRaises(ValueError):
                    preview_external_import(paths, source_id, cancel=lambda: False, progress=lambda value: None, timeout_seconds=invalid)
                with self.assertRaises(ValueError):
                    import_external_preview(paths, source_id, "0" * 64, cancel=lambda: False, progress=lambda value: None, timeout_seconds=invalid)
            self.assertEqual(read_external_status(paths, source_id)["state"], "not_loaded")
            self.assertFalse((root / "memory").exists())
            events = []
            preview = preview_external_import(paths, source_id, cancel=lambda: False, progress=events.append)
            self.assertEqual(len([value for value in events if value["data"].get("stage") == "archive-completed"]), 11)
            self.assertTrue(all(value["source"] == "adapter" for value in events))
            self.assertTrue(all(value["phase"] in JOB_SCHEMA["properties"]["phase"]["enum"] for value in events))
            self.assertEqual(preview["state"], "ready", preview["diagnostics"])
            self.assertEqual((preview["discovered"], preview["accepted"], preview["rejected"], preview["added"]), (1, 1, 0, 1))
            self.assertGreater(preview["storage_bytes"]["staged_objects"], 0)
            self.assertGreater(preview["storage_bytes"]["disposable_catalog_build"], 0)
            self.assertFalse((root / "memory/knowledge/external/index.sqlite").exists())
            self.assertEqual(search_external_database(paths, source_id, deepcopy(EMPTY_EXTERNAL_QUERY))["matches"], [])
            status = read_external_status(paths, source_id)
            self.assertEqual(len(status["plans"]), 1)
            self.assertEqual(status["plans"][0]["plan_sha256"], preview["plan_sha256"])
            self.assertEqual(before, {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in ROOT_MEMBERS})
            self.assertFalse((root / "memory/knowledge/retrieval").exists())
            self.assertFalse((root / "memory/knowledge/index.sqlite").exists())
            (root / "mcp.toml").write_bytes((Path(__file__).resolve().parents[2] / "mcp.toml").read_bytes())
            manager = JobManager(load_context(root, "project_mcp"), write_operation)
            manager.start()
            try:
                accepted = manager.submit("database_preview", {"source_id": source_id, "timeout_seconds": 3600}, str(uuid4()))
                identity = accepted["job_id"]
                deadline = time.monotonic() + 10
                while manager.get(identity)["state"] not in ("succeeded", "failed", "cancelled") and time.monotonic() < deadline:
                    time.sleep(0.01)
                state = manager.get(identity)
                self.assertEqual(state["state"], "succeeded", state)
                result = json.loads((root / state["result"]["path"]).read_text(encoding="utf-8"))
                self.assertEqual(result["data"]["state"], "ready")
                self.assertEqual(result["data"]["accepted"], 1)
                self.assertFalse((root / "memory/knowledge/external/index.sqlite").exists())
            finally:
                manager.shutdown()

    def test_commit_refresh_history_and_missing(self: TestExternalIndex) -> None:
        """Refresh skips unchanged values, versions changes and preserves missing history."""
        cache = Path(__file__).resolve().parents[2] / ".cache/external-index-tests"
        cache.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=cache) as temporary:
            root = Path(temporary)
            source = root / "dataset"
            source.mkdir()
            for name, payload in ROOT_MEMBERS.items():
                (source / name).write_bytes(payload)
            (root / "settings.toml").write_text('[srim]\nroot = ' + json.dumps(str(root / "unused")) + '\n[knowledge]\ndatabase_paths = ' + json.dumps([str(source)]) + '\n', encoding="utf-8")
            paths = get_project_paths(root)
            source_id = list_database_sources(paths)["sources"][0]["source_id"]
            preview = preview_external_import(paths, source_id, cancel=lambda: False, progress=lambda value: None)
            report = import_external_preview(paths, source_id, preview["plan_sha256"], cancel=lambda: False, progress=lambda value: None)
            if report["state"] == "committed":
                rebuilt = reindex_external_database(paths, source_id, expected_generation=report["generation"], cancel=lambda: False, progress=lambda value: None)
                self.assertEqual(rebuilt["state"], "committed", rebuilt)
            self.assertEqual((report["state"], report["added"]), ("committed", 1))
            initial = search_external_database(paths, source_id, deepcopy(EMPTY_EXTERNAL_QUERY))["matches"][0]
            (source / "README.md").write_bytes(b"Changed packaging description only.\n")
            unchanged = preview_external_import(paths, source_id, cancel=lambda: False, progress=lambda value: None)
            self.assertEqual((unchanged["unchanged"], unchanged["changed"]), (1, 0))
            report = import_external_preview(paths, source_id, unchanged["plan_sha256"], cancel=lambda: False, progress=lambda value: None)
            if report["state"] == "committed":
                rebuilt = reindex_external_database(paths, source_id, expected_generation=report["generation"], cancel=lambda: False, progress=lambda value: None)
                self.assertEqual(rebuilt["state"], "committed", rebuilt)
            refreshed = search_external_database(paths, source_id, deepcopy(EMPTY_EXTERNAL_QUERY))["matches"][0]
            self.assertEqual(report["skipped"], 1)
            self.assertEqual(initial["reference"], refreshed["reference"])
            self.assertNotEqual(initial["snapshot_sha256"], refreshed["snapshot_sha256"])
            for present in (True, False):
                selected = deepcopy(MEMBERS) if present else {}
                if present:
                    selected["TDATA.txt"] = selected["TDATA.txt"].replace(b"50000.E-03", b"60000.E-03")
                    case = json.loads(selected["case.json"])
                    case["provenance"]["sha256"]["TDATA.txt"] = hashlib.sha256(selected["TDATA.txt"]).hexdigest()
                    selected["case.json"] = json.dumps(case, sort_keys=True).encode("utf-8")
                metadata = {}
                for name, row in (("manifest.csv", MANIFEST), ("case_mapping.csv", MAPPING)):
                    stream = io.StringIO(newline="")
                    writer = csv.DictWriter(stream, fieldnames=list(row))
                    writer.writeheader()
                    if present:
                        writer.writerow(row)
                    metadata[name] = stream.getvalue().encode("utf-8")
                all_members = {**metadata, **{"cases/SM-1/" + CASE_ID + "/" + name: payload for name, payload in selected.items()}}
                metadata["SHA256SUMS"] = "".join(hashlib.sha256(payload).hexdigest() + "  " + name + "\n" for name, payload in sorted(all_members.items())).encode("ascii")
                archives = {}
                for ordinal, name in enumerate(ARCHIVES):
                    members = metadata if ordinal == 0 else {"cases/SM-1/" + CASE_ID + "/" + key: value for key, value in selected.items()} if ordinal == 1 else {}
                    stream = io.BytesIO()
                    with tarfile.open(fileobj=stream, mode="w:gz", format=tarfile.GNU_FORMAT) as archive:
                        for member, payload in members.items():
                            entry = tarfile.TarInfo(DATASET_ROOT + "/" + member)
                            entry.size = len(payload)
                            archive.addfile(entry, io.BytesIO(payload))
                    archives[name] = stream.getvalue()
                for name, payload in archives.items():
                    (source / name).write_bytes(payload)
                (source / "SHA256SUMS.txt").write_text("".join(hashlib.sha256(payload).hexdigest() + "  " + name + "\n" for name, payload in sorted(archives.items())), encoding="ascii")
                preview = preview_external_import(paths, source_id, cancel=lambda: False, progress=lambda value: None)
                self.assertEqual(preview["state"], "ready", preview["diagnostics"])
                self.assertEqual((preview["changed"], preview["missing"]), (1, 0) if present else (0, 1))
                report = import_external_preview(paths, source_id, preview["plan_sha256"], cancel=lambda: False, progress=lambda value: None)
                if report["state"] == "committed":
                    rebuilt = reindex_external_database(paths, source_id, expected_generation=report["generation"], cancel=lambda: False, progress=lambda value: None)
                    self.assertEqual(rebuilt["state"], "committed", rebuilt)
                self.assertEqual(report["state"], "committed")
                if present:
                    with patch("src.knowledge.external_index.normalize_snumat_case", side_effect=ValueError("rejected existing fixture")):
                        rejected = preview_external_import(paths, source_id, cancel=lambda: False, progress=lambda value: None)
                    self.assertEqual((rejected["rejected"], rejected["missing"]), (1, 0))
                    rejection_report = import_external_preview(paths, source_id, rejected["plan_sha256"], cancel=lambda: False, progress=lambda value: None)
                    if rejection_report["state"] == "committed":
                        rebuilt = reindex_external_database(paths, source_id, expected_generation=rejection_report["generation"], cancel=lambda: False, progress=lambda value: None)
                        self.assertEqual(rebuilt["state"], "committed", rebuilt)
                    self.assertEqual((rejection_report["rejected"], rejection_report["missing"]), (1, 0))
                    self.assertEqual(read_external_status(paths, source_id)["current"], 1)
            status = read_external_status(paths, source_id)
            self.assertEqual((status["records"], status["current"], status["missing"]), (2, 0, 1))
            self.assertEqual(search_external_database(paths, source_id, deepcopy(EMPTY_EXTERNAL_QUERY))["total"], 0)
            history_query = {**deepcopy(EMPTY_EXTERNAL_QUERY), "revision_scope": "history"}
            history = search_external_database(paths, source_id, history_query)
            self.assertEqual(history["total"], 2)
            self.assertEqual(history["excluded_on_page"], 0)
            self.assertEqual(history["verification"]["original_evidence"], "not_checked")
            saved = get_external_record(paths, initial["reference"], verify_evidence=False)
            self.assertEqual(saved["verification"]["record"], "verified")
            self.assertEqual(saved["record"]["outputs"]["summary"]["mean_range"]["value"], "50000.E-03")

    def test_stale_plan_cancel_and_atomicity(self: TestExternalIndex) -> None:
        """Stale inputs, cancellation, storage failure and locks never partly publish."""
        cache = Path(__file__).resolve().parents[2] / ".cache/external-index-tests"
        cache.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=cache) as temporary:
            root = Path(temporary)
            source = root / "dataset"
            source.mkdir()
            for name, payload in ROOT_MEMBERS.items():
                (source / name).write_bytes(payload)
            (root / "settings.toml").write_text('[srim]\nroot = ' + json.dumps(str(root / "unused")) + '\n[knowledge]\ndatabase_paths = ' + json.dumps([str(source)]) + '\n', encoding="utf-8")
            paths = get_project_paths(root)
            source_id = list_database_sources(paths)["sources"][0]["source_id"]
            original_deadline = SOURCE_DEADLINE.get()
            with patch("src.knowledge.external_index.monotonic", side_effect=[0.0, 601.0]):
                with self.assertRaisesRegex(ValueError, "timeout"):
                    preview_external_import(paths, source_id, cancel=lambda: False, progress=lambda value: None)
            self.assertEqual(SOURCE_DEADLINE.get(), original_deadline)
            self.assertFalse((root / "memory/knowledge/external/index.sqlite").exists())
            cancelled = preview_external_import(paths, source_id, cancel=lambda: True, progress=lambda value: None)
            self.assertEqual(cancelled["state"], "cancelled")
            with patch("src.knowledge.external_index._write_input_file", side_effect=OSError("disk-write fixture")):
                failed = preview_external_import(paths, source_id, cancel=lambda: False, progress=lambda value: None)
            self.assertEqual(failed["state"], "failed")
            self.assertIsNone(failed["plan_sha256"])
            preview = preview_external_import(paths, source_id, cancel=lambda: False, progress=lambda value: None)
            self.assertEqual(preview["state"], "ready")
            with patch("src.knowledge.external_index.monotonic", side_effect=[0.0, 601.0]):
                with self.assertRaisesRegex(ValueError, "timeout"):
                    import_external_preview(paths, source_id, preview["plan_sha256"], cancel=lambda: False, progress=lambda value: None)
            self.assertEqual(SOURCE_DEADLINE.get(), original_deadline)
            self.assertFalse((root / "memory/knowledge/external/index.sqlite").exists())
            with self.assertRaises(ValueError):
                import_external_preview(paths, source_id, "0" * 64, cancel=lambda: False, progress=lambda value: None)
            (source / "README.md").write_bytes(b"changed after preview")
            with self.assertRaises(ValueError):
                import_external_preview(paths, source_id, preview["plan_sha256"], cancel=lambda: False, progress=lambda value: None)
            (source / "README.md").write_bytes(ROOT_MEMBERS["README.md"])
            directory = root / "memory/knowledge/external"
            with _lock_parent(directory, timeout_seconds=0):
                with self.assertRaises(OSError):
                    import_external_preview(paths, source_id, preview["plan_sha256"], cancel=lambda: False, progress=lambda value: None)
            self.assertFalse((directory / "index.sqlite").exists())
            with patch("src.knowledge.external_index.fingerprint_external_source", return_value=preview["source_fingerprints"]):
                with patch("src.knowledge.external_index.resolve_external_source", return_value=source):
                    cancel = unittest.mock.Mock(side_effect=[False, False, True])
                    report = import_external_preview(paths, source_id, preview["plan_sha256"], cancel=cancel, progress=lambda value: None)
                    if report["state"] == "committed":
                        rebuilt = reindex_external_database(paths, source_id, expected_generation=report["generation"], cancel=lambda: False, progress=lambda value: None)
                        self.assertEqual(rebuilt["state"], "committed", rebuilt)
            self.assertEqual(report["state"], "cancelled")
            self.assertEqual(report["added"], 0)
            self.assertFalse((directory / "index.sqlite").exists())
            with patch("src.knowledge.external_index._write_json", side_effect=OSError("report disk fixture")):
                report = import_external_preview(paths, source_id, preview["plan_sha256"], cancel=lambda: False, progress=lambda value: None)
                if report["state"] == "committed":
                    rebuilt = reindex_external_database(paths, source_id, expected_generation=report["generation"], cancel=lambda: False, progress=lambda value: None)
                    self.assertEqual(rebuilt["state"], "committed", rebuilt)
            self.assertEqual(report["state"], "committed")
            self.assertEqual(report["issues"][0]["code"], "reporting_error")
            self.assertEqual(read_external_status(paths, source_id)["records"], 1)
            with self.assertRaises(ValueError):
                import_external_preview(paths, source_id, preview["plan_sha256"], cancel=lambda: False, progress=lambda value: None)
            query = deepcopy(EMPTY_EXTERNAL_QUERY)
            result = search_external_database(paths, source_id, query)
            self.assertEqual(result["total"], 1)
            connection = sqlite3.connect(directory / "index.sqlite")
            try:
                connection.execute("PRAGMA user_version=99")
                connection.commit()
            finally:
                connection.close()
            with self.assertRaises(ValueError):
                read_external_status(paths, source_id)

    def test_search_get_generation_and_witnesses(self: TestExternalIndex) -> None:
        """Exact predicates expose witnesses; stale originals and revocation fail reads."""
        cache = Path(__file__).resolve().parents[2] / ".cache/external-index-tests"
        cache.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=cache) as temporary:
            root = Path(temporary)
            source = root / "dataset"
            source.mkdir()
            for name, payload in ROOT_MEMBERS.items():
                (source / name).write_bytes(payload)
            settings = '[srim]\nroot = ' + json.dumps(str(root / "unused")) + '\n[knowledge]\ndatabase_paths = ' + json.dumps([str(source)]) + '\n'
            (root / "settings.toml").write_text(settings, encoding="utf-8")
            paths = get_project_paths(root)
            source_id = list_database_sources(paths)["sources"][0]["source_id"]
            preview = preview_external_import(paths, source_id, cancel=lambda: False, progress=lambda value: None)
            import_external_preview(paths, source_id, preview["plan_sha256"], cancel=lambda: False, progress=lambda value: None)
            rebuilt = reindex_external_database(paths, source_id, expected_generation=read_external_status(paths, source_id)["generation"], cancel=lambda: False, progress=lambda value: None)
            self.assertEqual(rebuilt["state"], "committed", rebuilt)
            query = {**deepcopy(EMPTY_EXTERNAL_QUERY), "text": "B Si", "clauses": [{"field": "outputs.mean_range", "operator": "eq", "value": "5", "unit": "nm"}, {"field": "inputs.energy_keV", "operator": "eq", "value": "100", "unit": "keV"}]}
            result = search_external_database(paths, source_id, query)
            self.assertEqual(result["total"], 1)
            match = result["matches"][0]
            self.assertNotIn("record", match)
            self.assertEqual(match["summary"]["mean_range"], {"value": "50000.E-03", "unit": "A"})
            verified = get_external_record(paths, match["reference"])
            self.assertEqual(verified["verification"]["original_evidence"], "verified", verified["issues"])
            self.assertEqual(get_external_record(paths, match["reference"], verify_evidence=False)["verification"]["original_evidence"], "not_checked")
            self.assertEqual(search_external_database(paths, source_id, query, offset=1)["matches"], [])
            with self.assertRaises(ValueError):
                search_external_database(paths, source_id, query, expected_generation="00000000-0000-0000-0000-000000000000")
            with self.assertRaises(ValueError):
                search_external_database(paths, source_id, query, limit=True)
            invalid = deepcopy(query)
            invalid["clauses"][0]["field"] = "outputs.VACANCY.txt"
            with self.assertRaises(ValueError):
                search_external_database(paths, source_id, invalid)
            with patch("src.knowledge.external_index.verify_external_evidence", side_effect=OSError("missing original")):
                self.assertEqual(get_external_record(paths, match["reference"])["verification"]["original_evidence"], "unavailable")
            (source / ARCHIVES[1]).write_bytes(b"source modified")
            stale = search_external_database(paths, source_id, query)
            self.assertEqual((stale["total"], stale["excluded_on_page"]), (1, 0))
            self.assertEqual(stale["matches"][0]["verification"]["original_evidence"], "not_checked")
            self.assertEqual(get_external_record(paths, match["reference"])["verification"]["original_evidence"], "unavailable")
            self.assertEqual(get_external_record(paths, match["reference"], verify_evidence=False)["verification"]["record"], "verified")
            (root / "settings.toml").write_text(settings.replace(json.dumps([str(source)]), "[]"), encoding="utf-8")
            self.assertEqual(read_external_status(paths, source_id)["records"], 1)
            self.assertEqual(read_external_status(paths, source_id)["registration"], "revoked")
            self.assertIsNone(get_external_record(paths, match["reference"], verify_evidence=False)["record"])
            with self.assertRaises(ValueError):
                search_external_database(paths, source_id, query)

    def test_nan_estimate_preview_import_search(self: TestExternalIndex) -> None:
        """Roundtrip a synthetic NaN estimate through temporary source and catalog.

        self is the unittest instance. Return None. Write only a temporary
        cache fixture and remove it on exit; assertion failures propagate.
        """
        cache = Path(__file__).resolve().parents[2] / ".cache/external-index-tests"
        cache.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=cache) as temporary:
            root = Path(temporary)
            source = root / "dataset"
            source.mkdir()
            selected = deepcopy(MEMBERS)
            manifest = deepcopy(MANIFEST)
            manifest["srmodule_interpolated_rp_nm"] = "nan"
            case = json.loads(selected["case.json"])
            case["manifest"] = manifest
            selected["case.json"] = json.dumps(case, sort_keys=True).encode("utf-8")
            metadata = {}
            for name, row in (("manifest.csv", manifest), ("case_mapping.csv", MAPPING)):
                stream = io.StringIO(newline="")
                writer = csv.DictWriter(stream, fieldnames=list(row))
                writer.writeheader()
                writer.writerow(row)
                metadata[name] = stream.getvalue().encode("utf-8")
            all_members = {**metadata, **{"cases/SM-1/" + CASE_ID + "/" + name: payload for name, payload in selected.items()}}
            metadata["SHA256SUMS"] = "".join(hashlib.sha256(payload).hexdigest() + "  " + name + "\n" for name, payload in sorted(all_members.items())).encode("ascii")
            archives = {}
            for ordinal, name in enumerate(ARCHIVES):
                members = metadata if ordinal == 0 else {"cases/SM-1/" + CASE_ID + "/" + key: value for key, value in selected.items()} if ordinal == 1 else {}
                stream = io.BytesIO()
                with tarfile.open(fileobj=stream, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
                    for member, payload in members.items():
                        entry = tarfile.TarInfo(DATASET_ROOT + "/" + member)
                        entry.size = len(payload)
                        archive.addfile(entry, io.BytesIO(payload))
                archives[name] = stream.getvalue()
            for name, payload in {**ROOT_MEMBERS, **archives}.items():
                (source / name).write_bytes(payload)
            (source / "SHA256SUMS.txt").write_text("".join(hashlib.sha256(payload).hexdigest() + "  " + name + "\n" for name, payload in sorted(archives.items())), encoding="ascii")
            before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in source.iterdir()}
            (root / "settings.toml").write_text('[srim]\nroot = ' + json.dumps(str(root / "unused")) + '\n[knowledge]\ndatabase_paths = ' + json.dumps([str(source)]) + '\n', encoding="utf-8")
            paths = get_project_paths(root)
            source_id = list_database_sources(paths)["sources"][0]["source_id"]
            preview = preview_external_import(paths, source_id, cancel=lambda: False, progress=lambda value: None)
            self.assertEqual(preview["state"], "ready", preview["diagnostics"])
            self.assertEqual((preview["accepted"], preview["rejected"]), (1, 0))
            self.assertFalse((root / "memory/knowledge/external/index.sqlite").exists())
            report = import_external_preview(paths, source_id, preview["plan_sha256"], cancel=lambda: False, progress=lambda value: None)
            if report["state"] == "committed":
                rebuilt = reindex_external_database(paths, source_id, expected_generation=report["generation"], cancel=lambda: False, progress=lambda value: None)
                self.assertEqual(rebuilt["state"], "committed", rebuilt)
            self.assertEqual(report["state"], "committed")
            query = deepcopy(EMPTY_EXTERNAL_QUERY)
            query["clauses"] = [{"field": "estimates.srmodule_interpolated_rp_nm", "operator": "unknown", "value": None, "unit": None}]
            result = search_external_database(paths, source_id, query)
            self.assertEqual(result["total"], 1)
            match = result["matches"][0]
            saved = get_external_record(paths, match["reference"])
            self.assertEqual(saved["verification"]["original_evidence"], "verified")
            record = saved["record"]
            self.assertEqual(record["case_id"], match["reference"]["case_id"])
            estimate = record["estimates"]["srmodule_interpolated_rp_nm"]
            self.assertIsNone(estimate["value"])
            self.assertEqual(estimate["raw"], "nan")
            self.assertEqual(estimate["availability"], "unavailable")
            self.assertEqual(estimate["locator"]["json_pointer"], "/manifest/srmodule_interpolated_rp_nm")
            self.assertEqual(record["inputs"]["number_of_ions"], 10)
            self.assertEqual(record["outputs"]["state"], "available")
            self.assertEqual(len(record["outputs"]["summary"]), 6)
            self.assertEqual(before, {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in source.iterdir()})


    def test_resumable_finalization(self: TestExternalIndex) -> None:
        """Resume complete failed synthetic staging and fail stale/corrupt boundaries.

        self is the unittest instance. Return None. Only temporary cache files
        and a real managed test job are created, with teardown on exit. Native
        engines never run. Failed assertions and unexpected I/O errors propagate.
        """
        cache = Path(__file__).resolve().parents[2] / ".cache/external-index-tests"
        cache.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=cache) as temporary:
            root = Path(temporary)
            source = root / "dataset"
            source.mkdir()
            for name, payload in ROOT_MEMBERS.items():
                (source / name).write_bytes(payload)
            (root / "settings.toml").write_text('[srim]\nroot = ' + json.dumps(str(root / "unused")) + '\n[knowledge]\ndatabase_paths = ' + json.dumps([str(source)]) + '\n', encoding="utf-8")
            paths = get_project_paths(root)
            source_id = list_database_sources(paths)["sources"][0]["source_id"]
            before_source = {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in ROOT_MEMBERS}
            with patch.object(external_index, "TABLES", external_index.TABLES + ("INVALID SQL",)):
                failed = preview_external_import(paths, source_id, cancel=lambda: False, progress=lambda value: None)
            self.assertEqual(failed["state"], "failed")
            self.assertEqual((failed["accepted"], failed["rejected"]), (1, 0))
            origin = root / "memory/knowledge/external/staging" / failed["operation_id"]
            old_preview = (origin / "preview.json").read_bytes()
            old_object = next((origin / "records").iterdir())
            old_payload = old_object.read_bytes()
            for invalid in ("../outside", "NOT-A-UUID", True, 1):
                with self.assertRaises(ValueError):
                    preview_external_import(paths, source_id, cancel=lambda: False, progress=lambda value: None, resume_operation_id=invalid)
            for updates in ({"state": "ready"}, {"base_generation": str(uuid4())}, {"source_id": "db_" + "1" * 64}, {"adapter_version": 2}, {"discovered": 2}, {"accepted": 2}, {"source_fingerprints": {**failed["source_fingerprints"], "files": []}}):
                with self.subTest(updates=updates):
                    modified = {**failed, **updates}
                    (origin / "preview.json").write_text(json.dumps(modified), encoding="utf-8")
                    result = preview_external_import(paths, source_id, cancel=lambda: False, progress=lambda value: None, resume_operation_id=failed["operation_id"])
                    self.assertEqual(result["state"], "failed")
                    self.assertIsNone(result["plan_sha256"])
            (origin / "preview.json").write_bytes(old_preview)
            original_readme = (source / "README.md").read_bytes()
            (source / "README.md").write_bytes(original_readme + b"changed")
            result = preview_external_import(paths, source_id, cancel=lambda: False, progress=lambda value: None, resume_operation_id=failed["operation_id"])
            self.assertEqual(result["state"], "failed")
            (source / "README.md").write_bytes(original_readme)
            duplicate_rejection = {"case_id": "SM-2_B_p_d0005nm", "code": "rejected", "message": "synthetic"}
            modified = {**failed, "discovered": 3, "rejected": 2, "diagnostics": [duplicate_rejection, duplicate_rejection]}
            (origin / "preview.json").write_text(json.dumps(modified), encoding="utf-8")
            result = preview_external_import(paths, source_id, cancel=lambda: False, progress=lambda value: None, resume_operation_id=failed["operation_id"])
            self.assertEqual(result["state"], "failed")
            (origin / "preview.json").write_bytes(old_preview)
            old_object.write_bytes(b"corrupt")
            result = preview_external_import(paths, source_id, cancel=lambda: False, progress=lambda value: None, resume_operation_id=failed["operation_id"])
            self.assertEqual(result["state"], "failed")
            old_object.write_bytes(old_payload)
            old_object.unlink()
            result = preview_external_import(paths, source_id, cancel=lambda: False, progress=lambda value: None, resume_operation_id=failed["operation_id"])
            self.assertEqual(result["state"], "failed")
            old_object.write_bytes(old_payload)
            for bad in (b"{}", json.dumps({**json.loads(old_payload), "planned": {**json.loads(old_payload)["planned"], "status_note": "duplicate"}}, sort_keys=True, separators=(",", ":")).encode("utf-8")):
                extra = origin / "records" / (hashlib.sha256(bad).hexdigest() + ".json")
                extra.write_bytes(bad)
                result = preview_external_import(paths, source_id, cancel=lambda: False, progress=lambda value: None, resume_operation_id=failed["operation_id"])
                self.assertEqual(result["state"], "failed")
                extra.unlink()
            incompatible = {**json.loads(old_payload), "source_id": "db_" + "1" * 64}
            extra_bytes = json.dumps(incompatible).encode("utf-8")
            extra = origin / "records" / (hashlib.sha256(extra_bytes).hexdigest() + ".json")
            extra.write_bytes(extra_bytes)
            result = preview_external_import(paths, source_id, cancel=lambda: False, progress=lambda value: None, resume_operation_id=failed["operation_id"])
            self.assertEqual(result["state"], "failed")
            extra.unlink()
            partial = {**failed, "discovered": 0, "accepted": 0, "added": 0, "diagnostics": []}
            old_object.unlink()
            (origin / "preview.json").write_text(json.dumps(partial), encoding="utf-8")
            result = preview_external_import(paths, source_id, cancel=lambda: False, progress=lambda value: None, resume_operation_id=failed["operation_id"])
            self.assertEqual(result["state"], "failed")
            old_object.write_bytes(old_payload)
            (origin / "preview.json").write_bytes(old_preview)
            with patch("src.knowledge.external_index.os.link", side_effect=OSError("hardlinks unsupported")):
                result = preview_external_import(paths, source_id, cancel=lambda: False, progress=lambda value: None, resume_operation_id=failed["operation_id"])
            self.assertEqual(result["state"], "failed")
            self.assertIn("hardlinks unsupported", result["diagnostics"][-1]["message"])
            result = preview_external_import(paths, source_id, cancel=lambda: True, progress=lambda value: None, resume_operation_id=failed["operation_id"])
            self.assertEqual(result["state"], "cancelled")
            with patch("src.knowledge.external_index.monotonic", side_effect=[0.0, 0.0, 0.0, 2.0]):
                result = preview_external_import(paths, source_id, cancel=lambda: False, progress=lambda value: None, timeout_seconds=1, resume_operation_id=failed["operation_id"])
            self.assertEqual(result["state"], "failed")
            self.assertRegex(result["diagnostics"][-1]["message"], "timeout|deadline")
            with patch.object(external_index, "TABLES", external_index.TABLES + ("INVALID SQL",)), patch("src.knowledge.external_index.normalize_snumat_case", side_effect=ValueError("synthetic rejected case")):
                rejected = preview_external_import(paths, source_id, cancel=lambda: False, progress=lambda value: None)
            self.assertEqual((rejected["state"], rejected["accepted"], rejected["rejected"]), ("failed", 0, 1))
            with patch("src.knowledge.external_index.normalize_snumat_case", side_effect=AssertionError("must not normalize")):
                resumed_rejected = preview_external_import(paths, source_id, cancel=lambda: False, progress=lambda value: None, resume_operation_id=rejected["operation_id"])
            self.assertEqual((resumed_rejected["state"], resumed_rejected["accepted"], resumed_rejected["rejected"]), ("ready", 0, 1))
            self.assertTrue(any(issue.get("message") == "synthetic rejected case" for issue in resumed_rejected["diagnostics"]))
            (root / "mcp.toml").write_bytes((Path(__file__).resolve().parents[2] / "mcp.toml").read_bytes())
            manager = JobManager(load_context(root, "project_mcp"), write_operation)
            manager.start()
            try:
                with patch("src.knowledge.external_index.normalize_snumat_case", side_effect=AssertionError("must not normalize")):
                    job = manager.submit("database_preview", {"source_id": source_id, "resume_operation_id": failed["operation_id"]}, str(uuid4()))
                    deadline = time.monotonic() + 10
                    while manager.get(job["job_id"])["state"] not in ("succeeded", "failed", "cancelled") and time.monotonic() < deadline:
                        time.sleep(0.01)
                    state = manager.get(job["job_id"])
                self.assertEqual(state["state"], "succeeded", state)
                ready = json.loads((root / state["result"]["path"]).read_text())["data"]
                self.assertEqual((ready["state"], ready["accepted"], ready["rejected"]), ("ready", 1, 0))
            finally:
                manager.shutdown()
            linked = root / "memory/knowledge/external/staging" / ready["operation_id"] / "records" / old_object.name
            self.assertTrue(linked.samefile(old_object))
            self.assertEqual(linked.read_bytes(), old_payload)
            self.assertEqual((origin / "preview.json").read_bytes(), old_preview)
            self.assertFalse((root / "memory/knowledge/external/index.sqlite").exists())
            self.assertEqual(search_external_database(paths, source_id, deepcopy(EMPTY_EXTERNAL_QUERY))["total"], 0)
            report = import_external_preview(paths, source_id, ready["plan_sha256"], cancel=lambda: False, progress=lambda value: None)
            if report["state"] == "committed":
                rebuilt = reindex_external_database(paths, source_id, expected_generation=report["generation"], cancel=lambda: False, progress=lambda value: None)
                self.assertEqual(rebuilt["state"], "committed", rebuilt)
            self.assertEqual(report["state"], "committed")
            match = search_external_database(paths, source_id, deepcopy(EMPTY_EXTERNAL_QUERY))["matches"][0]
            self.assertEqual(get_external_record(paths, match["reference"], verify_evidence=False)["record"], json.loads(old_payload))
            stale = preview_external_import(paths, source_id, cancel=lambda: False, progress=lambda value: None, resume_operation_id=failed["operation_id"])
            self.assertEqual(stale["state"], "failed")
            self.assertEqual(before_source, {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in ROOT_MEMBERS})

    def test_projection_lifecycle_and_exact_queries(self: TestExternalIndex) -> None:
        """Verify all indexed fields, managed rebuild, deadlines and atomic failure boundaries.

        self owns temporary synthetic records only. Return None; assertion errors
        propagate. No real source archives or native simulations are accessed.
        """
        from fractions import Fraction
        from src.knowledge.external_schema import FIELDS, compile_external_query, project_external_record, match_external_record
        from src.mcp.project_mcp.operations import read_operation
        cache = Path(__file__).resolve().parents[2] / ".cache/external-index-tests"
        cache.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=cache) as temporary:
            root = Path(temporary)
            source = root / "dataset"
            source.mkdir()
            for name, payload in ROOT_MEMBERS.items():
                (source / name).write_bytes(payload)
            (root / "settings.toml").write_text('[srim]\nroot = ' + json.dumps(str(root / "unused")) + '\n[knowledge]\ndatabase_paths = ' + json.dumps([str(source)]) + '\n', encoding="utf-8")
            (root / "mcp.toml").write_bytes((Path(__file__).resolve().parents[2] / "mcp.toml").read_bytes())
            paths = get_project_paths(root)
            source_id = list_database_sources(paths)["sources"][0]["source_id"]
            preview = preview_external_import(paths, source_id, cancel=lambda: False, progress=lambda value: None)
            imported = import_external_preview(paths, source_id, preview["plan_sha256"], cancel=lambda: False, progress=lambda value: None)
            generation = imported["generation"]
            self.assertEqual(read_external_status(paths, source_id)["search_index"]["state"], "missing")
            with self.assertRaisesRegex(ValueError, "reindex_required"):
                search_external_database(paths, source_id, deepcopy(EMPTY_EXTERNAL_QUERY))
            for invalid in (True, 0, 3601):
                with self.assertRaises(ValueError):
                    reindex_external_database(paths, source_id, expected_generation=generation, cancel=lambda: False, progress=lambda value: None, timeout_seconds=invalid)
            with self.assertRaisesRegex(ValueError, "stale"):
                reindex_external_database(paths, source_id, expected_generation=str(uuid4()), cancel=lambda: False, progress=lambda value: None)
            context = load_context(root, "project_mcp")
            manager = JobManager(context, write_operation)
            manager.start()
            try:
                with patch("src.knowledge.external_index.fingerprint_external_source", side_effect=AssertionError("no archive reads")), patch("src.knowledge.external_sources._read_external_members", side_effect=AssertionError("no member reads")):
                    job = manager.submit("database_reindex", {"source_id": source_id, "expected_generation": generation}, str(uuid4()))
                    deadline = time.monotonic() + 10
                    while manager.get(job["job_id"])["state"] not in ("succeeded", "failed", "cancelled") and time.monotonic() < deadline:
                        time.sleep(0.01)
                    state = manager.get(job["job_id"])
                    self.assertEqual(state["state"], "succeeded", state)
                    result = read_operation("database_search", {"source_id": source_id, "query": deepcopy(EMPTY_EXTERNAL_QUERY)}, context)
                    self.assertEqual(result["total"], 1)
                    self.assertEqual(result["schema_version"], 2)
                    self.assertEqual(result["matches"][0]["verification"]["original_evidence"], "not_checked")
            finally:
                manager.shutdown()
            reference = result["matches"][0]["reference"]
            record = get_external_record(paths, reference, verify_evidence=False)["record"]
            projection = project_external_record(record)
            queries = []
            for field, value in projection["fields"].items():
                kind, actual = value["kind"], value["value"]
                unit = FIELDS[field][1]
                if kind == "number":
                    number = Fraction(actual)
                    value_string = str(number.numerator) if number.denominator == 1 else str(float(number))
                    queries.extend(({"field": field, "operator": "eq", "value": value_string, "unit": unit}, {"field": field, "operator": "between", "value": [value_string, None], "unit": unit}, {"field": field, "operator": "between", "value": [None, value_string], "unit": unit}))
                elif kind == "text":
                    queries.append({"field": field, "operator": "eq", "value": actual, "unit": None})
                elif kind == "elements":
                    for operator in ("contains_all", "contains_any"):
                        queries.append({"field": field, "operator": operator, "value": actual, "unit": None})
                queries.append({"field": field, "operator": "unknown", "value": None, "unit": None})
            queries.extend(({"field": "outputs.mean_range", "operator": "eq", "value": "5", "unit": "nm"}, {"field": "outputs.mean_range", "operator": "eq", "value": "5.00000000000000000001", "unit": "nm"}, {"field": "inputs.energy_keV", "operator": "between", "value": [None, "1e1024"], "unit": "MeV"}, {"field": "inputs.target_elements", "operator": "contains_any", "value": ["Au"], "unit": None}))
            for clause in queries:
                with self.subTest(clause=clause):
                    query = {**deepcopy(EMPTY_EXTERNAL_QUERY), "clauses": [clause]}
                    expected = int(match_external_record(record, query)["matched"])
                    actual = search_external_database(paths, source_id, query)
                    self.assertEqual(actual["total"], expected)
            for text in ("b si", "not-present", CASE_ID, "TDATA.txt"):
                query = {**deepcopy(EMPTY_EXTERNAL_QUERY), "text": text}
                self.assertEqual(search_external_database(paths, source_id, query)["total"], int(match_external_record(record, query)["matched"]))
            projected_file = root / "memory/knowledge/external/search" / (source_id + ".sqlite")
            before = projected_file.read_bytes()
            with patch("src.knowledge.external_index.os.replace", side_effect=OSError("publication fixture")):
                failed = reindex_external_database(paths, source_id, expected_generation=generation, cancel=lambda: False, progress=lambda value: None)
            self.assertEqual(failed["state"], "failed")
            self.assertEqual(projected_file.read_bytes(), before)
            original_status = read_external_status(paths, source_id)
            with patch("src.knowledge.external_index.read_external_status", side_effect=[original_status, {**original_status, "generation": str(uuid4())}]):
                failed = reindex_external_database(paths, source_id, expected_generation=generation, cancel=lambda: False, progress=lambda value: None)
            self.assertEqual(failed["state"], "failed")
            self.assertEqual(projected_file.read_bytes(), before)
            with patch("src.knowledge.external_index.resolve_external_source", side_effect=[source, source, OSError("source revoked before publish")]):
                failed = reindex_external_database(paths, source_id, expected_generation=generation, cancel=lambda: False, progress=lambda value: None)
            self.assertEqual(failed["state"], "failed")
            self.assertEqual(projected_file.read_bytes(), before)
            for cancel in (lambda: True, lambda: False):
                with patch("src.knowledge.external_index.shutil.disk_usage", return_value=shutil.disk_usage(root)._replace(free=0)) if not cancel() else unittest.mock.patch("src.knowledge.external_index.shutil.disk_usage", wraps=shutil.disk_usage):
                    failed = reindex_external_database(paths, source_id, expected_generation=generation, cancel=cancel, progress=lambda value: None)
                self.assertIn(failed["state"], ("failed", "cancelled"))
                self.assertEqual(projected_file.read_bytes(), before)
            with patch("src.knowledge.external_index.monotonic", side_effect=[0.0] + [2.0] * 100):
                failed = reindex_external_database(paths, source_id, expected_generation=generation, cancel=lambda: False, progress=lambda value: None, timeout_seconds=1)
            self.assertEqual(failed["state"], "failed")
            self.assertEqual(projected_file.read_bytes(), before)
            with patch("src.knowledge.external_index.monotonic", side_effect=[0.0] + [2.0] * 100):
                with self.assertRaisesRegex(ValueError, "deadline"):
                    search_external_database(paths, source_id, deepcopy(EMPTY_EXTERNAL_QUERY), timeout_seconds=1)
            for invalid in (True, 0, 241):
                with self.assertRaises(ValueError):
                    search_external_database(paths, source_id, deepcopy(EMPTY_EXTERNAL_QUERY), timeout_seconds=invalid)
                with self.assertRaises(ValueError):
                    get_external_record(paths, reference, timeout_seconds=invalid)
            connection = external_index._open_external_projection(projected_file, readonly=True)
            try:
                compiled = compile_external_query({**deepcopy(EMPTY_EXTERNAL_QUERY), "clauses": [{"field": "inputs.energy_keV", "operator": "eq", "value": "100", "unit": "keV"}]})
                predicate, values = external_index._external_projection_predicate(compiled)
                plan = connection.execute("EXPLAIN QUERY PLAN SELECT d.case_id FROM documents d WHERE " + predicate, values).fetchall()
                self.assertTrue(any("INDEX" in row[-1] and "v" in row[-1] for row in plan), plan)
            finally:
                connection.close()
            connection = external_index._open_external_projection(projected_file, readonly=False)
            try:
                connection.execute("UPDATE documents SET projection_sha256=?", ("0" * 64,))
                connection.commit()
            finally:
                connection.close()
            with self.assertRaisesRegex(ValueError, "projection_integrity_error"):
                search_external_database(paths, source_id, deepcopy(EMPTY_EXTERNAL_QUERY))
            projected_file.write_bytes(before)
            authoritative = root / "memory/knowledge/external/index.sqlite"
            with sqlite3.connect(authoritative) as connection:
                connection.execute("UPDATE meta SET value=? WHERE key='generation'", (str(uuid4()),))
            connection.close()
            self.assertEqual(read_external_status(paths, source_id)["search_index"]["state"], "stale")
            with self.assertRaisesRegex(ValueError, "reindex_required"):
                search_external_database(paths, source_id, deepcopy(EMPTY_EXTERNAL_QUERY))
            projected_file.write_bytes(b"broken SQLite")
            self.assertEqual(read_external_status(paths, source_id)["search_index"]["state"], "corrupt")

    def test_compact_page_budget_and_exclusion_offsets(self: TestExternalIndex) -> None:
        """Verify real serialization limits, Unicode and lossless byte-limited pagination.

        self owns a temporary catalog of cloned synthetic records. Return None;
        failures propagate. Source archives are unchanged and no native jobs run.
        """
        from src.mcp.common.contracts import canonical_bytes, make_response
        cache = Path(__file__).resolve().parents[2] / ".cache/external-index-tests"
        cache.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=cache) as temporary:
            root = Path(temporary)
            source = root / "dataset"
            source.mkdir()
            for name, payload in ROOT_MEMBERS.items():
                (source / name).write_bytes(payload)
            (root / "settings.toml").write_text('[srim]\nroot = ' + json.dumps(str(root / "unused")) + '\n[knowledge]\ndatabase_paths = ' + json.dumps([str(source)]) + '\n', encoding="utf-8")
            paths = get_project_paths(root)
            source_id = list_database_sources(paths)["sources"][0]["source_id"]
            preview = preview_external_import(paths, source_id, cancel=lambda: False, progress=lambda value: None)
            imported = import_external_preview(paths, source_id, preview["plan_sha256"], cancel=lambda: False, progress=lambda value: None)
            directory = root / "memory/knowledge/external"
            original_bytes = next((directory / "objects").glob("*.json")).read_bytes()
            original = json.loads(original_bytes)
            with sqlite3.connect(directory / "index.sqlite") as connection:
                snapshot = connection.execute("SELECT snapshot_sha256 FROM bindings LIMIT 1").fetchone()[0]
                for ordinal in range(2, 102):
                    record = deepcopy(original)
                    record["case_id"] = "SM-" + str(ordinal) + "_B_p_d0005nm"
                    record["target_id"] = "SM-" + str(ordinal)
                    record["inputs"]["target_label"] = "Stra\u00dfe " + "\u5bc6" * 300
                    payload = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
                    digest = hashlib.sha256(payload).hexdigest()
                    (directory / "objects" / (digest + ".json")).write_bytes(payload)
                    connection.execute("INSERT INTO records VALUES (?,?,?,?)", (source_id, record["case_id"], digest, payload.decode("utf-8")))
                    connection.execute("INSERT INTO heads VALUES (?,?,?,?)", (source_id, record["case_id"], digest, "present"))
                    connection.execute("INSERT INTO bindings VALUES (?,?,?,?)", (source_id, record["case_id"], digest, snapshot))
            connection.close()
            rebuilt = reindex_external_database(paths, source_id, expected_generation=imported["generation"], cancel=lambda: False, progress=lambda value: None)
            self.assertEqual(rebuilt["state"], "committed", rebuilt)
            first = search_external_database(paths, source_id, deepcopy(EMPTY_EXTERNAL_QUERY), limit=100)
            self.assertTrue(first["has_more"])
            self.assertLess(len(first["matches"]), 100)
            found, offset = [], 0
            while True:
                page = search_external_database(paths, source_id, deepcopy(EMPTY_EXTERNAL_QUERY), offset=offset, limit=100)
                self.assertLessEqual(len(canonical_bytes(page)), 131072)
                self.assertLess(len(canonical_bytes(make_response(page))), 1048576)
                found.extend(hit["reference"]["case_id"] for hit in page["matches"])
                if not page["has_more"]:
                    break
                self.assertGreater(page["next_offset"], offset)
                offset = page["next_offset"]
            self.assertEqual(len(found), 101)
            self.assertEqual(found, sorted(set(found)))
            unicode_query = {**deepcopy(EMPTY_EXTERNAL_QUERY), "text": "STRASSE \u5bc6"}
            self.assertEqual(search_external_database(paths, source_id, unicode_query)["total"], 100)
            bad = first["matches"][0]["reference"]
            (directory / "objects" / (bad["record_sha256"] + ".json")).unlink()
            excluded = search_external_database(paths, source_id, deepcopy(EMPTY_EXTERNAL_QUERY), limit=1)
            self.assertEqual(excluded["excluded_on_page"], 1)
            self.assertEqual(excluded["next_offset"], 1)
            self.assertEqual(excluded["matches"], [])
            second = search_external_database(paths, source_id, deepcopy(EMPTY_EXTERNAL_QUERY), offset=1, limit=1)
            self.assertEqual(second["matches"][0]["reference"]["case_id"], found[1])