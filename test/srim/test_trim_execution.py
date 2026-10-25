"""Exercise TRIM lifecycle and preservation with native text and mocked processes."""

from __future__ import annotations

from copy import deepcopy
import json
import hashlib
from pathlib import Path
from operator import attrgetter
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from _fixtures import make_test_project
from test_trim_inputs import TRIM_INSTALLATION_FILES, TRIM_REQUEST, TRIM_SELECTIONS
from test_trim_runtime import CACHE
from src.backend.records import CreateRequest, create_directories
from src.srim.common.records import read_execution_evidence, read_simulation
from src.srim.trim.defaults import switch_trim_defaults
from src.srim.trim.execution import _trim_completion, run_trim_simulation
from src.srim.trim.models import ResolvedTRIMInputs
from src.srim.trim.resolution import resolve_trim_inputs
from src.srim.trim.runtime import prepare_trim_continuation, prepare_trim_simulation, verify_trim_runtime
from src.srim.trim.storage import verify_preserved_trim_runtime
from src.srim.trim.dialogs import ENGINES
from src.srim.trim.outputs import parse_trim_checkpoint
from test_trim_config import CONFIG
from test_trim_dialogs import POLICY, WINDOW


class TestTRIMExecution(unittest.TestCase):
    """Use owned synthetic allocations and committed native results without GUI launches."""

    def test_2008_event_stop_prompt_replay(self: TestTRIMExecution) -> None:
        """Validate the 2008 event completion stop and reject prompt/save contradictions; return None."""
        policy = {**POLICY, "kind": "resume", "engine_family": "2008", "engine_sha256": ENGINES["2008"], "input_mode": 5}
        control = {"total_ions": 100, "autosave_ions": 100, "dialog_policy": policy}
        inputs = ResolvedTRIMInputs({}, {}, {"parameters": {"mode": "specified-ion-full"}}, {}, (), ())
        prompt = {**deepcopy(WINDOW), "title": "End of calculation"}
        prompt["controls"][0]["text"] = "Calculation Complete (100 Ions)\r\n\r\nYes = Save data\r\n\r\nNo  = Increase number of ions"
        parsed = {"schema_version": 2, "result_source": "checkpoint-recovery", "complete": True, "recovery": {"checkpoint": {"counts": {"current": 100, "total": 100, "autosave": 100}}, "provenance": {"sources": []}}}
        stop = {"origin": "completed-checkpoint", "completion_observed": True, "completion_prompt": prompt, "save_verified": False, "stable_seconds": 1.0, "sources": [], "job_empty": True}
        process = {"termination": "checkpoint-stop", "exit_code": 1, "popups": [], "monitor_errors": [], "checkpoint_stop": stop, "dialog_events": [], "dialog_state": {"stage": "checkpoint-check", "pending_sequence": None}}
        self.assertTrue(_trim_completion(inputs, process, parsed, {"differences": []}, control)["complete"])
        for changed in ({"completion_prompt": {**prompt, "title": "Unexpected"}}, {"origin": "explicit-save", "save_verified": True}):
            self.assertFalse(_trim_completion(inputs, {**process, "checkpoint_stop": {**stop, **changed}}, parsed, {"differences": []}, control)["complete"])

    def test_event_save_completion_order_and_natural_exit(self: TestTRIMExecution) -> None:
        """Accept event checkpoint inspection or natural exit and reject command evidence; return None."""
        inputs = ResolvedTRIMInputs({}, {}, {"parameters": {"mode": "specified-ion-full"}}, {}, (), ())
        policy = {**POLICY, "kind": "resume", "input_mode": 5, "expected_count": 200, "autosave_ions": 200}
        control = {"total_ions": 200, "autosave_ions": 200, "dialog_policy": policy}
        parsed = {"schema_version": 2, "result_source": "checkpoint-recovery", "complete": True, "recovery": {"checkpoint": {"counts": {"current": 200, "total": 200, "autosave": 200}}, "provenance": {"sources": []}}}
        prompt = {**deepcopy(WINDOW), "title": "End of TRIM.DAT calculation", "controls": [{"class": "Static", "text": "Calculation Complete (200 Ions)"}, {"handle": 12, "class": "Button", "text": "OK", "control_id": 2, "enabled": True, "visible": True, "pid": 42}]}
        complete = {"queued": True, "transition": "verified", "error": None, "stage_after": "resume-event-save", "window": prompt}
        save = {"queued": True, "transition": "verified", "error": None, "stage_before": "resume-event-save", "stage_after": "checkpoint-saved", "control_id": 3}
        process = {"termination": "checkpoint-stop", "exit_code": 1, "popups": [], "monitor_errors": [], "checkpoint_stop": {"origin": "completed-checkpoint", "completion_observed": True, "completion_prompt": prompt, "save_verified": False, "job_empty": True, "stable_seconds": 1.1, "sources": []}, "dialog_events": [], "dialog_state": {"stage": "checkpoint-check", "pending_sequence": None}}
        self.assertTrue(_trim_completion(inputs, process, parsed, {"differences": []}, control)["complete"])
        for changed in ({"dialog_events": [save, complete]}, {"dialog_events": [complete]}, {"dialog_events": [{**complete, "transition": "failed"}, save]}, {"dialog_state": {"stage": "checkpoint-saved", "pending_sequence": 2}}, {"popups": [{"title": "Unknown"}]}, {"termination": "timeout"}):
            self.assertFalse(_trim_completion(inputs, {**process, **changed}, parsed, {"differences": []}, control)["complete"])
        for stage in ("done", "resume-event-save"):
            natural = {**process, "termination": "exit", "exit_code": 0, "checkpoint_stop": None, "dialog_events": [{**complete, "stage_after": stage}], "dialog_state": {"stage": "done", "pending_sequence": None}}
            self.assertTrue(_trim_completion(inputs, natural, parsed, {"differences": []}, control)["complete"])
        for update in ({"origin": "unknown"}, {"save_verified": True}, {"completion_observed": False}, {"completion_prompt": {**prompt, "title": "Unknown"}}, {"stable_seconds": float("nan")}, {"job_empty": False}):
            changed = {**process, "checkpoint_stop": {**process["checkpoint_stop"], **update}}
            self.assertFalse(_trim_completion(inputs, changed, parsed, {"differences": []}, control)["complete"])

    def test_fresh_config_still_byte_exact(self: TestTRIMExecution) -> None:
        """Reject a fresh staged config rewrite without creating a qualification sidecar; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            paths, config, worker = make_test_project(Path(directory))
            for name, data in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(data)
            (config.srim_root / "Data/TRIM.cfg").write_bytes(CONFIG)
            switch_trim_defaults(paths, config)
            inputs = resolve_trim_inputs(paths, config, worker, TRIM_REQUEST, TRIM_SELECTIONS)
            created = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            runtime = prepare_trim_simulation(paths, config, created, worker, inputs)
            (runtime / "Data/TRIM.cfg").write_bytes(CONFIG.replace(b" 255 0 ", b" 255 255 "))
            process = {"pid": 42, "exit_code": 0, "elapsed_seconds": 0.1, "termination": "exit", "popups": [], "monitor_errors": []}
            with patch("src.srim.trim.execution.verify_trim_runtime", return_value=inputs), patch("src.srim.trim.execution.execute_trim_process", return_value=process):
                result = run_trim_simulation(paths, config, created.path.name, runtime)
            self.assertEqual(result["status"], "failed")
            report = json.loads((created.path / "output/raw/execution.json").read_bytes())
            self.assertEqual(report["failure"]["code"], "integrity")
            self.assertFalse((created.path / "output/parsed/config-integrity.json").exists())
            verify_preserved_trim_runtime(paths, created.path.name, runtime)

    def test_ordinary_fallback_evidence_is_exact(self: TestTRIMExecution) -> None:
        """Require one ordered ordinary Yes/default-save pair and exact stop evidence; return None."""
        inputs = ResolvedTRIMInputs({}, {}, {"parameters": {"mode": "ion-full"}}, {}, (), ())
        policy = {**POLICY, "kind": "resume", "input_mode": 2, "cascade_output": "none"}
        control = {"total_ions": 100, "autosave_ions": 100, "dialog_policy": policy}
        parsed = {"schema_version": 2, "result_source": "checkpoint-recovery", "complete": True, "recovery": {"checkpoint": {"counts": {"current": 100, "total": 100, "autosave": 100}}, "provenance": {"sources": []}}}
        prompt = {**deepcopy(WINDOW), "title": "End of calculation"}
        prompt["controls"][0]["text"] = "Calculation Complete (100 Ions) Yes = Save data No = Increase number of ions"
        form = {**deepcopy(WINDOW), "class": "ThunderRT5Form", "title": "Form1", "controls": [{"handle": i + 20, "pid": 42, "class": "ThunderRT5CommandButton", "text": text, "control_id": i, "visible": True, "enabled": True} for i, text in ((1, "Continue without Saving TRIM"), (2, "Store in another Directory / Disk"), (3, "Store in SRIM directory (default)"))]}
        yes = {"queued": True, "transition": "verified", "error": None, "stage_before": "checkpoint-save-required", "stage_after": "resume-save", "action": "button", "target_handle": 12, "control_id": 6, "response": "Yes", "window": prompt}
        save = {"queued": True, "transition": "verified", "error": None, "stage_before": "resume-save", "stage_after": "checkpoint-saved", "action": "button", "target_handle": 23, "control_id": 3, "response": "Store in SRIM directory (default)", "window": form}
        process = {"termination": "checkpoint-stop", "exit_code": 1, "popups": [], "monitor_errors": [], "checkpoint_stop": {"origin": "explicit-save", "completion_observed": True, "completion_prompt": prompt, "save_verified": True, "job_empty": True, "stable_seconds": 1.0, "sources": []}, "dialog_events": [yes, save], "dialog_state": {"stage": "checkpoint-saved", "pending_sequence": None}}
        self.assertTrue(_trim_completion(inputs, process, parsed, {"differences": []}, control)["complete"])
        for events in ([save, yes], [yes], [yes, yes, save], [{**yes, "target_handle": 99}, save], [yes, {**save, "control_id": 2}], [yes, {**save, "transition": "pending"}]):
            self.assertFalse(_trim_completion(inputs, {**process, "dialog_events": events}, parsed, {"differences": []}, control)["complete"])
        self.assertFalse(_trim_completion(inputs, process, parsed, {"differences": []}, {**control, "dialog_policy": {**policy, "input_mode": 5}})["complete"])

    def test_checkpoint_continuation_pipeline(self: TestTRIMExecution) -> None:
        """Recover actual 2008/2013 arrays after owned stop and reject failure evidence; return None."""
        for family, termination, damage in (("2008", "exit", None), ("2013", "exit", None), ("2013", "checkpoint-stop", None), ("2013", "timeout", None), ("2013", "checkpoint-stop", "hash"), ("2013", "exit", "array"), ("2013", "exit", "count"), ("2013", "exit", "substitution"), ("2013", "exit", "cfg"), ("2013", "exit", "limit"), ("2013", "exit", "cfgunknown"), ("2013", "exit", "otherinput"), ("2013", "exit", "baseline")):
            with self.subTest(family=family, termination=termination, damage=damage), TemporaryDirectory(dir=CACHE) as directory:
                paths, config, worker = make_test_project(Path(directory))
                for name, data in TRIM_INSTALLATION_FILES.items():
                    (config.srim_root / name).write_bytes(data if name != "Data/VERSION" else f"SRIM-{family}.{'04' if family == '2008' else '00'}\r\n".encode())
                (config.srim_root / "Data/TRIM.cfg").write_bytes(CONFIG)
                switch_trim_defaults(paths, config)
                request = deepcopy(TRIM_REQUEST)
                request["parameters"].update({"total_ions": 50, "mode": "ion-full"})
                request["parameters"]["layers"][0]["overrides"]["components"] = [{"component_index": 1, "mass_amu": "28.086", "surface_binding_eV": "4.7"}]
                inputs = resolve_trim_inputs(paths, config, worker, request, TRIM_SELECTIONS)
                parent = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
                runtime = prepare_trim_simulation(paths, config, parent, worker, inputs)
                source = Path(__file__).parent / f"fixtures/trim/recovery/srim_{family}{'_std' if family == '2013' else ''}/mode-02"
                saved = runtime / "SRIM Restore"
                saved.mkdir(exist_ok=True)
                for item in source.glob("*.sav"):
                    shutil.copyfile(item, saved / item.name)
                tdata = next(p for p in saved.iterdir() if p.name.casefold() == "tdata.sav")
                trimin = next(p for p in saved.iterdir() if p.name.casefold() == "trimin.sav")
                checkpoint = parse_trim_checkpoint(tdata.read_bytes(), trimin.read_bytes())
                lines = tdata.read_bytes().splitlines(keepends=True)
                index = checkpoint["count_line"]["line"] - 1
                tokens = lines[index].split()
                tokens[:2] = [b"50", b"50"]
                lines[index] = b" ".join(tokens) + b"\r\n"
                tdata.write_bytes(b"".join(lines))
                process = {"pid": 42, "exit_code": 1, "elapsed_seconds": 0.1, "termination": "timeout", "popups": [], "monitor_errors": []}
                with patch("src.srim.trim.execution.verify_trim_runtime", return_value=inputs), patch("src.srim.trim.execution.execute_trim_process", return_value=process):
                    self.assertEqual(run_trim_simulation(paths, config, parent.path.name, runtime)["status"], "failed")
                parent_bytes = (parent.path / "output/raw/runtime/SRIM Restore" / tdata.name).read_bytes()
                child = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
                digest = hashlib.sha256(TRIM_INSTALLATION_FILES["TRIM.exe"]).hexdigest()
                with patch.dict(ENGINES, {family: digest}):
                    resumed = prepare_trim_continuation(paths, config, child, worker, parent.path.name, total_ions=100, autosave_ions=checkpoint["counts"]["autosave"], cascade_output="none" if termination == "checkpoint-stop" else None)
                    verify_trim_runtime(paths, child.path.name, resumed)
                    target = resumed / "SRIM Restore"
                    for item in source.glob("*.sav"):
                        shutil.copyfile(item, target / item.name)
                    (resumed / "TDATA.txt").write_bytes(b"stale native output")
                    sources = [{"path": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "size_bytes": p.stat().st_size} for p in sorted(target.iterdir(), key=attrgetter("name"))]
                    process = {"pid": 42, "exit_code": 1 if termination != "exit" else 0, "elapsed_seconds": 2.0, "termination": termination, "popups": [], "monitor_errors": []}
                    if termination == "checkpoint-stop":
                        prompt = {**deepcopy(WINDOW), "title": "End of calculation"}
                        prompt["controls"][0]["text"] = "Calculation Complete (100 Ions) Yes = Save data No = Increase number of ions"
                        form = {**deepcopy(WINDOW), "class": "ThunderRT5Form", "title": "Form1", "controls": [{"handle": i + 20, "pid": 42, "class": "ThunderRT5CommandButton", "text": text, "control_id": i, "visible": True, "enabled": True} for i, text in ((1, "Continue without Saving TRIM"), (2, "Store in another Directory / Disk"), (3, "Store in SRIM directory (default)"))]}
                        process.update({"checkpoint_stop": {"origin": "explicit-save", "completion_observed": True, "completion_prompt": prompt, "save_verified": True, "stable_seconds": 1.0, "sources": sources, "job_empty": True}, "dialog_state": {"pending_sequence": None, "stage": "checkpoint-saved"}, "dialog_events": [{"queued": True, "transition": "verified", "error": None, "stage_before": "checkpoint-save-required", "stage_after": "resume-save", "action": "button", "target_handle": 12, "control_id": 6, "response": "Yes", "window": prompt}, {"queued": True, "transition": "verified", "error": None, "stage_before": "resume-save", "stage_after": "checkpoint-saved", "action": "button", "target_handle": 23, "control_id": 3, "response": "Store in SRIM directory (default)", "window": form}]})
                    if damage == "hash":
                        (target / "additional.bin").write_bytes(b"changed after live snapshot")
                    elif damage == "array":
                        next(p for p in target.iterdir() if p.name.casefold() == "ioniz.sav").write_bytes(b"broken")
                    elif damage in ("count", "substitution"):
                        data = next(p for p in target.iterdir() if p.name.casefold() == "tdata.sav")
                        payload = data.read_bytes()
                        if damage == "count":
                            content = payload.splitlines(keepends=True)
                            values = content[index].split()
                            values[:2] = [b"99", b"99"]
                            content[index] = b" ".join(values) + b"\r\n"
                            payload = b"".join(content)
                        else:
                            payload = payload.replace(b"39.9620", b"40.9620")
                        data.write_bytes(payload)
                    elif damage in ("cfg", "limit", "cfgunknown"):
                        changed = CONFIG.replace(b" 255 0 ", b" 255 255 ") if damage == "cfg" else CONFIG.replace(b" 20000 \r\n", b" 30000 \r\n") if damage == "limit" else CONFIG + b"unknown\r\n"
                        (resumed / "Data/TRIM.cfg").write_bytes(changed)
                    elif damage == "otherinput":
                        (resumed / "Data/SCOEF03.dat").write_bytes(b"changed immutable input")
                    control_document = json.loads((child.path / "input/execution/control.json").read_bytes())
                    manifest_document = json.loads((child.path / "input/execution/runtime.json").read_bytes())
                    if damage == "baseline":
                        next(entry for entry in manifest_document["files"] if entry["path"] == "Data/TRIM.cfg")["sha256"] = "0" * 64
                    with patch("src.srim.trim.execution.verify_trim_runtime", return_value=inputs), patch("src.srim.trim.execution.execute_trim_process", return_value=process), patch("src.srim.trim.execution._read_json", side_effect=[control_document, control_document, manifest_document]):
                        result = run_trim_simulation(paths, config, child.path.name, resumed)
                expected_success = damage in (None, "cfg") and termination != "timeout"
                report = json.loads((child.path / "output/raw/execution.json").read_bytes())
                self.assertEqual(result["status"], "succeeded" if expected_success else "failed", report)
                self.assertEqual(report["schema_version"], 4)
                parsed = json.loads((child.path / "output/parsed/results.json").read_bytes())
                self.assertEqual(parsed["result_source"], "checkpoint-recovery")
                self.assertFalse(parsed["native"]["complete"])
                config_report = json.loads((child.path / "output/parsed/config-integrity.json").read_bytes())
                self.assertEqual(config_report["status"], "qualified" if damage == "cfg" else "different" if damage in ("limit", "cfgunknown", "baseline") else "equal")
                self.assertEqual((child.path / "input/installation/Data/TRIM.cfg").read_bytes(), CONFIG)
                if damage in ("limit", "cfgunknown", "otherinput", "baseline"):
                    self.assertEqual(report["failure"]["code"], "integrity")
                if expected_success:
                    self.assertEqual(parsed["recovery"]["checkpoint"]["counts"]["current"], 100)
                self.assertEqual((parent.path / "output/raw/runtime/SRIM Restore" / tdata.name).read_bytes(), parent_bytes)
                verify_preserved_trim_runtime(paths, child.path.name, resumed)

    def test_timeout_forwarding_and_evidence(self: TestTRIMExecution) -> None:
        """Execute isolated synthetic runs with finite/unlimited deadlines; verify evidence and return None."""
        for timeout in (None, 120):
            with self.subTest(timeout=timeout), TemporaryDirectory(dir=CACHE) as directory:
                paths, config, worker = make_test_project(Path(directory))
                for name, data in TRIM_INSTALLATION_FILES.items():
                    (config.srim_root / name).write_bytes(data)
                switch_trim_defaults(paths, config)
                inputs = resolve_trim_inputs(paths, config, worker, TRIM_REQUEST, TRIM_SELECTIONS)
                created = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
                runtime = prepare_trim_simulation(paths, config, created, worker, inputs)
                process = {"pid": 42, "exit_code": 0, "elapsed_seconds": 120.0, "termination": "exit", "popups": [], "monitor_errors": []}
                with patch("src.srim.trim.execution.execute_trim_process", return_value=process) as execute:
                    run_trim_simulation(paths, config, created.path.name, runtime, timeout_seconds=timeout)
                self.assertEqual(execute.call_args.kwargs["timeout_seconds"], timeout)
                report = json.loads((created.path / "output/raw/execution.json").read_bytes())
                self.assertEqual(report["timeout_seconds"], timeout)

    def test_success_requires_saved_counts(self: TestTRIMExecution) -> None:
        """Require real saved counts, parsed profiles and preserved settings for success; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            paths, config, worker = make_test_project(Path(directory))
            for name, data in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(data)
            switch_trim_defaults(paths, config)
            request = deepcopy(TRIM_REQUEST)
            request["parameters"].update({"total_ions": 5, "mode": "ion-full", "outputs": {"ranges": True, "backscatter": True, "transmission": True, "sputtering": True, "collisions": "ions-and-recoils", "exyz_increment_eV": "1000"}})
            request["parameters"]["layers"][0]["overrides"]["components"] = [{"component_index": 1, "mass_amu": "28.086", "surface_binding_eV": "4.7"}]
            inputs = resolve_trim_inputs(paths, config, worker, request, TRIM_SELECTIONS)
            created = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            runtime = prepare_trim_simulation(paths, config, created, worker, inputs)
            verify_trim_runtime(paths, created.path.name, runtime)
            shutil.copytree(Path(__file__).parent / "fixtures/trim/2013", runtime, dirs_exist_ok=True)
            process = {"pid": 42, "exit_code": 0, "elapsed_seconds": 0.1, "termination": "exit", "popups": [], "monitor_errors": []}
            with patch("src.srim.trim.execution.verify_trim_runtime", return_value=inputs), patch("src.srim.trim.execution.execute_trim_process", return_value=process):
                result = run_trim_simulation(paths, config, created.path.name, runtime)
            comparison = json.loads((created.path / "output/parsed/saved-values.json").read_bytes())
            self.assertEqual(comparison["differences"], [])
            self.assertEqual(result["status"], "succeeded")
            verify_preserved_trim_runtime(paths, created.path.name, runtime)

    def test_exit_zero_incomplete(self: TestTRIMExecution) -> None:
        """Reject exit zero when no native outputs or checkpoint were produced; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            paths, config, worker = make_test_project(Path(directory))
            for name, data in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(data)
            switch_trim_defaults(paths, config)
            inputs = resolve_trim_inputs(paths, config, worker, TRIM_REQUEST, TRIM_SELECTIONS)
            created = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            runtime = prepare_trim_simulation(paths, config, created, worker, inputs)
            with patch("src.srim.trim.execution.execute_trim_process", return_value={"pid": 42, "exit_code": 0, "elapsed_seconds": 0.1, "termination": "exit", "popups": [], "monitor_errors": []}):
                result = run_trim_simulation(paths, config, created.path.name, runtime)
            self.assertEqual(result["status"], "failed")
            self.assertIn("missing", result["failure"]["message"])
            verify_preserved_trim_runtime(paths, created.path.name, runtime)

    def test_popup_preserves_runtime(self: TestTRIMExecution) -> None:
        """Record popup evidence and terminal failure without any automatic response; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            paths, config, worker = make_test_project(Path(directory))
            for name, data in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(data)
            switch_trim_defaults(paths, config)
            inputs = resolve_trim_inputs(paths, config, worker, TRIM_REQUEST, TRIM_SELECTIONS)
            created = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            runtime = prepare_trim_simulation(paths, config, created, worker, inputs)
            popup = {"title": "Unexpected native dialog", "controls": [{"text": "Native error"}]}
            with patch("src.srim.trim.execution.execute_trim_process", return_value={"pid": 42, "exit_code": 1, "elapsed_seconds": 0.1, "termination": "popup", "popups": [popup], "monitor_errors": []}):
                result = run_trim_simulation(paths, config, created.path.name, runtime)
            report = json.loads((created.path / "output/raw/execution.json").read_bytes())
            self.assertEqual(result["status"], "failed")
            self.assertEqual(report["failure"]["code"], "popup")
            self.assertEqual(report["process"]["popups"], [popup])
            verify_preserved_trim_runtime(paths, created.path.name, runtime)

    def test_interruption_finalizes(self: TestTRIMExecution) -> None:
        """Preserve interrupted runtime and re-raise KeyboardInterrupt; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            paths, config, worker = make_test_project(Path(directory))
            for name, data in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(data)
            switch_trim_defaults(paths, config)
            inputs = resolve_trim_inputs(paths, config, worker, TRIM_REQUEST, TRIM_SELECTIONS)
            created = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            runtime = prepare_trim_simulation(paths, config, created, worker, inputs)
            with patch("src.srim.trim.execution.execute_trim_process", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    run_trim_simulation(paths, config, created.path.name, runtime)
            self.assertEqual(read_simulation(paths, created.path.name)["status"], "failed")
            verify_preserved_trim_runtime(paths, created.path.name, runtime)

    def test_preservation_failure_still_finalizes(self: TestTRIMExecution) -> None:
        """Verify self's preservation failure still finalizes after a stopped native failure; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            paths, config, worker = make_test_project(Path(directory))
            for name, data in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(data)
            switch_trim_defaults(paths, config)
            inputs = resolve_trim_inputs(paths, config, worker, TRIM_REQUEST, TRIM_SELECTIONS)
            created = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            runtime = prepare_trim_simulation(paths, config, created, worker, inputs)
            original = RuntimeError("native execution failed")
            with patch("src.srim.trim.execution.execute_trim_process", side_effect=original), patch("src.srim.trim.execution.preserve_trim_runtime", side_effect=OSError("preservation unavailable")):
                with self.assertRaises(RuntimeError) as caught:
                    run_trim_simulation(paths, config, created.path.name, runtime)
            self.assertIs(caught.exception, original)
            metadata = read_simulation(paths, created.path.name)
            self.assertEqual(metadata["status"], "failed")
            self.assertTrue(any("TRIM runtime preservation failed" in note for note in original.__notes__))

    def test_diagnostic_failure_still_finalizes(self: TestTRIMExecution) -> None:
        """Verify self's diagnostic failure still finalizes after a stopped native failure; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            paths, config, worker = make_test_project(Path(directory))
            for name, data in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(data)
            switch_trim_defaults(paths, config)
            inputs = resolve_trim_inputs(paths, config, worker, TRIM_REQUEST, TRIM_SELECTIONS)
            created = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            runtime = prepare_trim_simulation(paths, config, created, worker, inputs)
            original = RuntimeError("native execution failed")
            with patch("src.srim.trim.execution.execute_trim_process", side_effect=original), patch("src.srim.trim.execution._write_json", side_effect=OSError("diagnostics unavailable")):
                with self.assertRaises(RuntimeError) as caught:
                    run_trim_simulation(paths, config, created.path.name, runtime)
            self.assertIs(caught.exception, original)
            metadata = read_simulation(paths, created.path.name)
            self.assertEqual(metadata["status"], "failed")
            self.assertTrue(any("TRIM execution diagnostics failed" in note for note in original.__notes__))

    def test_finalization_failure_is_reported(self: TestTRIMExecution) -> None:
        """Verify self's finalization failure is reported after a stopped native failure; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            paths, config, worker = make_test_project(Path(directory))
            for name, data in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(data)
            switch_trim_defaults(paths, config)
            inputs = resolve_trim_inputs(paths, config, worker, TRIM_REQUEST, TRIM_SELECTIONS)
            created = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            runtime = prepare_trim_simulation(paths, config, created, worker, inputs)
            original = RuntimeError("native execution failed")
            with patch("src.srim.trim.execution.execute_trim_process", side_effect=original), patch("src.srim.trim.execution.finish_simulation", side_effect=OSError("metadata unavailable")):
                with self.assertRaises(RuntimeError) as caught:
                    run_trim_simulation(paths, config, created.path.name, runtime)
            self.assertIs(caught.exception, original)
            metadata = read_simulation(paths, created.path.name)
            self.assertEqual(metadata["status"], "running")
            self.assertTrue(any("TRIM finalization failed" in note for note in original.__notes__))

    def test_unstopped_process_remains_unresolved(self: TestTRIMExecution) -> None:
        """Keep self's running record and live runtime unresolved after stop failure; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            paths, config, worker = make_test_project(Path(directory))
            for name, data in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(data)
            switch_trim_defaults(paths, config)
            inputs = resolve_trim_inputs(paths, config, worker, TRIM_REQUEST, TRIM_SELECTIONS)
            created = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            runtime = prepare_trim_simulation(paths, config, created, worker, inputs)
            original = RuntimeError("native process could not be stopped")
            original.trim_job_stopped = False
            with patch("src.srim.trim.execution.execute_trim_process", side_effect=original), patch("src.srim.trim.execution.preserve_trim_runtime") as preserve:
                with self.assertRaises(RuntimeError) as caught:
                    run_trim_simulation(paths, config, created.path.name, runtime)
            self.assertIs(caught.exception, original)
            preserve.assert_not_called()
            metadata = read_simulation(paths, created.path.name)
            self.assertEqual(metadata["status"], "running")
            self.assertIsNone(metadata["finished_at"])
            self.assertTrue(any("process ownership unresolved" in note for note in original.__notes__))
            report = json.loads((created.path / "output/raw/execution.json").read_bytes())
            self.assertEqual(report["failure"]["code"], "monitor")
            self.assertTrue(runtime.is_dir())

    def test_terminal_record_not_reclassified(self: TestTRIMExecution) -> None:
        """Keep self's published failure unchanged when indexing is interrupted; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            paths, config, worker = make_test_project(Path(directory))
            for name, data in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(data)
            switch_trim_defaults(paths, config)
            inputs = resolve_trim_inputs(paths, config, worker, TRIM_REQUEST, TRIM_SELECTIONS)
            created = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            runtime = prepare_trim_simulation(paths, config, created, worker, inputs)
            original = KeyboardInterrupt("notification interrupted")
            process = {"pid": 42, "exit_code": 0, "elapsed_seconds": 0.1, "termination": "exit", "popups": [], "monitor_errors": []}
            with patch("src.srim.trim.execution.execute_trim_process", return_value=process), patch("src.knowledge.index.notify_simulation_finished", side_effect=original):
                with self.assertRaises(KeyboardInterrupt) as caught:
                    run_trim_simulation(paths, config, created.path.name, runtime)
            self.assertIs(caught.exception, original)
            metadata = read_simulation(paths, created.path.name)
            self.assertEqual(metadata["status"], "failed")
            self.assertIn("missing", metadata["failure"]["message"])
            self.assertIsNotNone(metadata["finished_at"])
            self.assertFalse(any("finalization failed" in note for note in original.__notes__))

    def test_resume_preserves_parent(self: TestTRIMExecution) -> None:
        """Stage complete opaque saved state into a new record with explicit totals; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            paths, config, worker = make_test_project(Path(directory))
            for name, data in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(data)
            switch_trim_defaults(paths, config)
            request = deepcopy(TRIM_REQUEST)
            request["parameters"].update({"total_ions": 5, "mode": "ion-full", "outputs": {"ranges": True, "backscatter": True, "transmission": True, "sputtering": True, "collisions": "ions-and-recoils", "exyz_increment_eV": "1000"}})
            request["parameters"]["layers"][0]["overrides"]["components"] = [{"component_index": 1, "mass_amu": "28.086", "surface_binding_eV": "4.7"}]
            inputs = resolve_trim_inputs(paths, config, worker, request, TRIM_SELECTIONS)
            created = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            runtime = prepare_trim_simulation(paths, config, created, worker, inputs)
            shutil.copytree(Path(__file__).parent / "fixtures/trim/2013", runtime, dirs_exist_ok=True)
            for name in ("E2RECOIL", "IONIZ", "NOVAC", "PHONON", "SPUTTER", "STOPPING", "VACANCY", "XRANGE", "YZRADIAL#", "YZRANGE#", "Ioniz3D", "NoVac3D", "Phonon3D", "Range3D", "Recoil3D", "Vac3D"):
                (runtime / "SRIM Restore" / (name + ".sav")).write_bytes(b"opaque native array fixture")
            with patch("src.srim.trim.execution.verify_trim_runtime", return_value=inputs), patch("src.srim.trim.execution.execute_trim_process", return_value={"pid": 42, "exit_code": 0, "elapsed_seconds": 0.1, "termination": "exit", "popups": [], "monitor_errors": []}):
                self.assertEqual(run_trim_simulation(paths, config, created.path.name, runtime)["status"], "succeeded")
            parent_bytes = (created.path / "output/raw/runtime/SRIM Restore/TDATA.sav").read_bytes()
            child = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            resumed = prepare_trim_continuation(paths, config, child, worker, created.path.name, total_ions=10, autosave_ions=1)
            self.assertEqual((resumed / "TRIMAUTO").read_bytes(), b"2\r\ncheckpoint\\\r\n")
            self.assertEqual((created.path / "output/raw/runtime/SRIM Restore/TDATA.sav").read_bytes(), parent_bytes)
            verify_trim_runtime(paths, child.path.name, resumed)
            self.assertEqual(json.loads((child.path / "input/execution/control.json").read_bytes())["initial_count"], 5)

    def test_substitution_is_failure(self: TestTRIMExecution) -> None:
        """Preserve a native mass substitution and fail without rewriting inputs; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            paths, config, worker = make_test_project(Path(directory))
            for name, data in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(data)
            switch_trim_defaults(paths, config)
            request = deepcopy(TRIM_REQUEST)
            request["parameters"].update({"total_ions": 5, "mode": "ion-full", "outputs": {"ranges": True, "backscatter": True, "transmission": True, "sputtering": True, "collisions": "ions-and-recoils", "exyz_increment_eV": "1000"}})
            request["parameters"]["layers"][0]["overrides"]["components"] = [{"component_index": 1, "mass_amu": "28.086", "surface_binding_eV": "4.7"}]
            inputs = resolve_trim_inputs(paths, config, worker, request, TRIM_SELECTIONS)
            created = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            runtime = prepare_trim_simulation(paths, config, created, worker, inputs)
            shutil.copytree(Path(__file__).parent / "fixtures/trim/2013", runtime, dirs_exist_ok=True)
            saved = runtime / "SRIM Restore/TDATA.sav"
            saved.write_bytes(saved.read_bytes().replace(b"28.0860", b"29.0860"))
            with patch("src.srim.trim.execution.verify_trim_runtime", return_value=inputs), patch("src.srim.trim.execution.execute_trim_process", return_value={"pid": 42, "exit_code": 0, "elapsed_seconds": 0.1, "termination": "exit", "popups": [], "monitor_errors": []}):
                result = run_trim_simulation(paths, config, created.path.name, runtime)
            report = json.loads((created.path / "output/raw/execution.json").read_bytes())
            self.assertEqual(result["status"], "failed")
            self.assertEqual(report["failure"]["code"], "substitution")
            self.assertEqual(inputs.request, request)
            self.assertIn(b"29.0860", (created.path / "output/raw/runtime/SRIM Restore/TDATA.sav").read_bytes())

    def test_launch_failure(self: TestTRIMExecution) -> None:
        """Finalize expected launch failure and preserve the unexecuted runtime; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            paths, config, worker = make_test_project(Path(directory))
            for name, data in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(data)
            switch_trim_defaults(paths, config)
            inputs = resolve_trim_inputs(paths, config, worker, TRIM_REQUEST, TRIM_SELECTIONS)
            created = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            runtime = prepare_trim_simulation(paths, config, created, worker, inputs)
            with patch("src.srim.trim.execution.execute_trim_process", return_value={"pid": None, "exit_code": None, "elapsed_seconds": 0.1, "termination": "launch-error", "popups": [], "monitor_errors": ["Cannot assign owned job"]}):
                result = run_trim_simulation(paths, config, created.path.name, runtime)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(json.loads((created.path / "output/raw/execution.json").read_bytes())["failure"]["code"], "launch")
            verify_preserved_trim_runtime(paths, created.path.name, runtime)

    def test_preservation_failure_retains_worker(self: TestTRIMExecution) -> None:
        """Propagate preservation I/O failure with original runtime intact; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            paths, config, worker = make_test_project(Path(directory))
            for name, data in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(data)
            switch_trim_defaults(paths, config)
            inputs = resolve_trim_inputs(paths, config, worker, TRIM_REQUEST, TRIM_SELECTIONS)
            created = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            runtime = prepare_trim_simulation(paths, config, created, worker, inputs)
            original = (runtime / "TRIM.IN").read_bytes()
            with patch("src.srim.trim.execution.execute_trim_process", return_value={"pid": 42, "exit_code": 0, "elapsed_seconds": 0.1, "termination": "exit", "popups": [], "monitor_errors": []}), patch("src.srim.trim.execution.preserve_trim_runtime", side_effect=PermissionError("copy blocked")):
                with self.assertRaises(PermissionError) as caught:
                    run_trim_simulation(paths, config, created.path.name, runtime)
            self.assertEqual((runtime / "TRIM.IN").read_bytes(), original)
            self.assertTrue(any(str(runtime) in note for note in caught.exception.__notes__))
            self.assertNotEqual(json.loads((created.path / "metadata.json").read_bytes())["status"], "succeeded")

    def test_parse_failure_is_preserved(self: TestTRIMExecution) -> None:
        """Preserve malformed profile bytes and report their parser failure; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            paths, config, worker = make_test_project(Path(directory))
            for name, data in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(data)
            switch_trim_defaults(paths, config)
            inputs = resolve_trim_inputs(paths, config, worker, TRIM_REQUEST, TRIM_SELECTIONS)
            created = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            runtime = prepare_trim_simulation(paths, config, created, worker, inputs)
            with patch("src.srim.trim.execution.verify_trim_runtime", return_value=inputs):
                (runtime / "RANGE.txt").write_bytes(b"broken native profile")
                with patch("src.srim.trim.execution.execute_trim_process", return_value={"pid": 42, "exit_code": 0, "elapsed_seconds": 0.1, "termination": "exit", "popups": [], "monitor_errors": []}):
                    result = run_trim_simulation(paths, config, created.path.name, runtime)
            self.assertEqual(result["status"], "failed")
            self.assertEqual((created.path / "output/raw/runtime/RANGE.txt").read_bytes(), b"broken native profile")
            self.assertTrue(any(i["path"] == "RANGE.txt" and i["code"] == "format" for i in json.loads((created.path / "output/parsed/results.json").read_bytes())["issues"]))

    def test_stopped_evidence(self: TestTRIMExecution) -> None:
        """Persist self's timeout stop proof after owned cleanup without changing failure results; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            paths, config, worker = make_test_project(Path(directory))
            for name, data in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(data)
            switch_trim_defaults(paths, config)
            inputs = resolve_trim_inputs(paths, config, worker, TRIM_REQUEST, TRIM_SELECTIONS)
            created = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            runtime = prepare_trim_simulation(paths, config, created, worker, inputs)
            process = {"pid": 42, "exit_code": 1, "elapsed_seconds": 0.1, "termination": "timeout", "popups": [], "monitor_errors": []}
            with patch("src.srim.trim.execution.execute_trim_process", return_value=process):
                metadata = run_trim_simulation(paths, config, created.path.name, runtime)
            self.assertEqual(metadata["status"], "failed")
            evidence = read_execution_evidence(paths, created.path.name)
            self.assertFalse(evidence["issues"])
            self.assertEqual(evidence["stopped"]["reason"], "timeout")
            self.assertEqual(evidence["stopped"]["exit_code"], 1)
            self.assertLessEqual(evidence["launch"]["recorded_at"], metadata["started_at"])

        with TemporaryDirectory(dir=CACHE) as directory:
            paths, config, worker = make_test_project(Path(directory))
            for name, data in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(data)
            switch_trim_defaults(paths, config)
            inputs = resolve_trim_inputs(paths, config, worker, TRIM_REQUEST, TRIM_SELECTIONS)
            created = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            runtime = prepare_trim_simulation(paths, config, created, worker, inputs)
            process = {"pid": 42, "exit_code": 1, "elapsed_seconds": 0.1, "termination": "timeout", "popups": [], "monitor_errors": []}
            original = OSError("stop evidence unavailable")
            with patch("src.srim.trim.execution.execute_trim_process", return_value=process), patch("src.srim.trim.execution.record_execution_stopped", side_effect=original):
                with self.assertRaises(OSError) as caught:
                    run_trim_simulation(paths, config, created.path.name, runtime)
            self.assertIs(caught.exception, original)
            self.assertEqual(read_simulation(paths, created.path.name)["status"], "failed")
            self.assertFalse((created.path / "lifecycle/stopped.json").exists())

    def test_unstopped_evidence_absent(self: TestTRIMExecution) -> None:
        """Leave self's explicit live ownership without stop proof or terminal metadata; return None."""
        with TemporaryDirectory(dir=CACHE) as directory:
            paths, config, worker = make_test_project(Path(directory))
            for name, data in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(data)
            switch_trim_defaults(paths, config)
            inputs = resolve_trim_inputs(paths, config, worker, TRIM_REQUEST, TRIM_SELECTIONS)
            created = create_directories(paths, config, [CreateRequest("srim")]).completed[0]
            runtime = prepare_trim_simulation(paths, config, created, worker, inputs)
            original = OSError("owned job did not stop")
            original.trim_job_stopped = False
            with patch("src.srim.trim.execution.execute_trim_process", side_effect=original):
                with self.assertRaises(OSError) as caught:
                    run_trim_simulation(paths, config, created.path.name, runtime)
            self.assertIs(caught.exception, original)
            self.assertEqual(read_simulation(paths, created.path.name)["status"], "running")
            evidence = read_execution_evidence(paths, created.path.name)
            self.assertIsNotNone(evidence["launch"])
            self.assertIsNone(evidence["stopped"])
            self.assertTrue(evidence["issues"])
