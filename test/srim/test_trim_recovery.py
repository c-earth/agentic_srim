"""Verify checkpoint recovery against preserved native mode/version evidence."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import struct
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.srim.trim_recovery import _decode_double_pairs, _decode_numeric_variants, _render_recovered_files, export_trim_checkpoint_recovery, recover_trim_checkpoint


FIXTURES = Path(__file__).parent / "fixtures/trim/recovery"
FULL = FIXTURES / "srim_2013_std/mode-02"
CACHE = Path(".cache/trim-recovery/tests").resolve()
CACHE.mkdir(parents=True, exist_ok=True)


class TestTRIMRecovery(unittest.TestCase):
    """Exercise pure decoding and exclusive export with frozen native fixtures.

    unittest constructs instances by test method name. Methods expose no public
    mutable state; filesystem tests allocate temporary children beneath CACHE.
    """

    def test_numeric_variant_types(self: TestTRIMRecovery) -> None:
        """Verify all four stored numeric types and exact signed values; return None."""
        data = struct.pack("<H", 0) + struct.pack("<Hf", 4, -1.25) + struct.pack("<Hd", 5, 2.5) + struct.pack("<HHBBIQ", 14, 14, 2, 128, 0, 12345)
        with localcontext() as context:
            context.prec = 2
            decoded = _decode_numeric_variants(data, source="sample", expected_count=4)
        self.assertEqual(decoded, (Decimal(0), Decimal("-1.25"), Decimal("2.5"), Decimal("-123.45")))
        large = struct.pack("<HHBBIQ", 14, 14, 28, 0, 0xffffffff, 0xffffffffffffffff)
        self.assertEqual(str(_decode_numeric_variants(large, source="large", expected_count=1)[0]), "7.9228162514264337593543950335")

    def test_rejects_invalid_numeric_records(self: TestTRIMRecovery) -> None:
        """Reject malformed tags, boundaries and nonfinite numbers without effects; return None."""
        samples = [b"\x04", struct.pack("<H", 3), struct.pack("<Hf", 4, float("inf")), struct.pack("<Hd", 5, float("nan")), struct.pack("<HHBBIQ", 14, 0, 0, 0, 0, 1), struct.pack("<HHBBIQ", 14, 14, 29, 0, 0, 1), struct.pack("<HHBBIQ", 14, 14, 0, 1, 0, 1), struct.pack("<H", 0) * 2, b""]
        for sample in samples:
            with self.subTest(sample=sample), self.assertRaisesRegex(ValueError, "sample.*byte"):
                _decode_numeric_variants(sample, source="sample", expected_count=1)
        for count in (-1, True, "1"):
            with self.assertRaises(ValueError):
                _decode_numeric_variants(b"", source="sample", expected_count=count)

    def test_double_pairs(self: TestTRIMRecovery) -> None:
        """Verify paired binary64 order/values and rejection paths; return None."""
        self.assertEqual(_decode_double_pairs(struct.pack("<dd", 1.5, -2.25), source="pairs", expected_rows=1), ((Decimal("1.5"), Decimal("-2.25")),))
        for sample in (b"", struct.pack("<dd", float("nan"), 0), struct.pack("<ddd", 1, 2, 3)):
            with self.assertRaises(ValueError):
                _decode_double_pairs(sample, source="pairs", expected_rows=1)

    def test_all_version_mode_fixtures(self: TestTRIMRecovery) -> None:
        """Check hashes, semantic mode, array shapes and export for all 23 cases; return None."""
        cases = sorted(FIXTURES.glob("*/mode-*"))
        self.assertEqual(len(cases), 23)
        modes = {"2008": {1: "ion-quick", 2: "ion-full", 3: "ion-monolayer", 4: "specified-ion-quick", 5: "specified-ion-full", 6: "recoil-quick", 7: "recoil-full"}, "2013": {1: "ion-quick", 2: "ion-full", 3: "ion-monolayer", 4: "specified-ion-quick", 5: "specified-ion-full", 6: "recoil-full", 7: "recoil-monolayer", 8: "recoil-quick"}}
        for case in cases:
            with self.subTest(case=str(case)):
                manifest = json.loads((case / "manifest.json").read_bytes())
                for entry in manifest["files"]:
                    data = (case / entry["path"]).read_bytes()
                    self.assertEqual(len(data), entry["size_bytes"])
                    self.assertEqual(hashlib.sha256(data).hexdigest(), entry["sha256"])
                files = {p.name: p.read_bytes() for p in case.iterdir() if p.suffix.lower() == ".sav"}
                before = dict(files)
                result = recover_trim_checkpoint(files)
                family = "2008" if "2008" in case.parent.name else "2013"
                self.assertEqual(result["checkpoint"]["semantic_mode"], modes[family][manifest["input_mode"]])
                self.assertEqual(result["checkpoint"]["counts"]["current"], 100)
                self.assertEqual(len(result["provenance"]["sources"]), 12 if family == "2008" else 18)
                for name, profile in result["profiles"].items():
                    self.assertEqual(len(profile["rows"]), 0 if profile["availability"] == "not_calculated" else 100)
                    self.assertTrue(all(len(row) == len(profile["columns"]) for row in profile["rows"]))
                self.assertEqual(len(_render_recovered_files(result)), 11)
                self.assertEqual(files, before)
                with localcontext() as context:
                    context.prec = 3
                    self.assertEqual(recover_trim_checkpoint(files), result)
                    self.assertEqual(len(_render_recovered_files(result)), 11)

    def test_quick_novac_is_unavailable(self: TestTRIMRecovery) -> None:
        """Preserve absent replacement measurement for every quick mode; return None."""
        for case in FIXTURES.glob("*/mode-*"):
            result = recover_trim_checkpoint({p.name: p.read_bytes() for p in case.iterdir() if p.suffix.lower() == ".sav"})
            if result["checkpoint"]["semantic_mode"].endswith("quick"):
                self.assertEqual(result["profiles"]["NOVAC.txt"]["availability"], "not_calculated")
                self.assertEqual(result["profiles"]["NOVAC.txt"]["rows"], [])
                rendered = _render_recovered_files(result)["NOVAC.txt"].decode()
                self.assertIn("NOT CALCULATED:", rendered)
                self.assertEqual(len(rendered.splitlines()), 4)

    def test_signed_energy_is_preserved(self: TestTRIMRecovery) -> None:
        """Verify observed signed recoil transfer survives recovery and rendering; return None."""
        negatives = 0
        for case in FIXTURES.glob("*/mode-*"):
            result = recover_trim_checkpoint({p.name: p.read_bytes() for p in case.iterdir() if p.suffix.lower() == ".sav"})
            rows = result["profiles"]["E2RECOIL.txt"]["rows"]
            negatives += sum(Decimal(row[1]) < 0 for row in rows)
            if any(Decimal(row[1]) < 0 for row in rows):
                self.assertIn("-", _render_recovered_files(result)["E2RECOIL.txt"].decode())
        self.assertGreater(negatives, 0)

    def test_rejects_unsupported_checkpoint(self: TestTRIMRecovery) -> None:
        """Reject incomplete, inconsistent or unsupported metadata without effects; return None."""
        files = {p.name: p.read_bytes() for p in FULL.iterdir() if p.suffix.lower() == ".sav"}
        for changes in ({"TDATA.sav": None}, {"Ioniz3D.sav": None}, {"tdata.sav": files["TDATA.sav"]}, {"../bad": b""}, {"NUL": b""}):
            candidate = dict(files)
            for name, data in changes.items():
                if data is None:
                    candidate.pop(name)
                else:
                    candidate[name] = data
            with self.subTest(changes=list(changes)), self.assertRaises(ValueError):
                recover_trim_checkpoint(candidate)
        text = files["TDATA.sav"]
        for original, replacement in ((b"SRIM-2013.00", b"SRIM-2013.99"), (b"100 100", b"0 0"), (b"0             1000          10", b"0             2000          10"), (b"  2 0 0 0 0 0 0", b"  1 0 0 0 0 0 0"), (b"5.3286E+02", b"NaN")):
            self.assertIn(original, text)
            candidate = dict(files)
            candidate["TDATA.sav"] = text.replace(original, replacement, 1)
            with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                recover_trim_checkpoint(candidate)
        candidate = dict(files)
        candidate["TDATA.sav"] = text.replace(b"100 100", b"99 100", 1)
        with self.assertRaises(ValueError):
            recover_trim_checkpoint(candidate)

    def test_lateral_validation(self: TestTRIMRecovery) -> None:
        """Exercise bin count, empty-bin and variance protections; return None."""
        files = {p.name: p.read_bytes() for p in FULL.iterdir() if p.suffix.lower() == ".sav"}
        variants = struct.pack("<Hf", 4, .1) + struct.pack("<H", 0) * 199
        zeros = bytes(1600)
        base = dict(files) | {"XRANGE.sav": variants, "YZRANGE#.sav": zeros, "YZRADIAL#.sav": zeros}
        for first in (.1002, -.1):
            candidate = dict(base) | {"XRANGE.sav": struct.pack("<Hf", 4, first) + struct.pack("<H", 0) * 199}
            with self.assertRaisesRegex(ValueError, "bin count"):
                recover_trim_checkpoint(candidate)
        candidate = dict(base) | {"YZRANGE#.sav": bytes(16) + struct.pack("<dd", 1, 1) + bytes(1568)}
        with self.assertRaisesRegex(ValueError, "empty row"):
            recover_trim_checkpoint(candidate)
        candidate = dict(base) | {"YZRADIAL#.sav": struct.pack("<dd", 1, 1 - 1e-13) + bytes(1584)}
        result = recover_trim_checkpoint(candidate)
        self.assertIn("radial_variance_roundoff", [warning["code"] for warning in result["warnings"]])
        self.assertEqual(Decimal(result["profiles"]["LATERAL.txt"]["rows"][0][4]), 0)
        for pair in ((1, .5), (-1, 1)):
            candidate = dict(base) | {"YZRADIAL#.sav": struct.pack("<dd", *pair) + bytes(1584)}
            with self.assertRaises(ValueError):
                recover_trim_checkpoint(candidate)

    def test_continued_vs_uninterrupted(self: TestTRIMRecovery) -> None:
        """Reproduce measured cumulative 200-ion differences and seed states; return None."""
        results = []
        for label in ("continued-100-to-200", "uninterrupted-1-to-200"):
            case = FIXTURES / "comparison-200" / label
            manifest = json.loads((case / "manifest.json").read_bytes())
            for entry in manifest["files"]:
                self.assertEqual(hashlib.sha256((case / entry["path"]).read_bytes()).hexdigest(), entry["sha256"])
            results.append(recover_trim_checkpoint({p.name: p.read_bytes() for p in case.iterdir() if p.suffix.lower() == ".sav"}))
        continued, fresh = results
        self.assertEqual(continued["checkpoint"]["counts"], fresh["checkpoint"]["counts"])
        self.assertEqual(continued["checkpoint"]["saved_generator_state"], fresh["checkpoint"]["saved_generator_state"])
        self.assertEqual(fresh["checkpoint"]["native_input"]["random_seed"], 12345)
        equal = 0
        differences = []
        for name, profile in continued["profiles"].items():
            for index, (left, right) in enumerate(zip(profile["rows"], fresh["profiles"][name]["rows"], strict=True)):
                self.assertEqual(left[0], right[0])
                for column, (a, b) in enumerate(zip(left[1:], right[1:], strict=True)):
                    if Decimal(a) == Decimal(b):
                        equal += 1
                    else:
                        differences.append((name, index, column, abs(Decimal(a) - Decimal(b))))
        self.assertEqual(equal, 1498)
        self.assertEqual([(name, row, col) for name, row, col, value in differences], [("IONIZ.txt", 1, 1), ("PHONON.txt", 1, 1)])
        self.assertAlmostEqual(float(differences[0][3]), .0002903675564437317, places=17)
        self.assertAlmostEqual(float(differences[1][3]), .0009221788, places=17)
        self.assertEqual(continued["summary"]["stored_averages"], ["5.3596E+02", "2.0901E+02", "9.9341E+02"])

    def test_native_precision_evidence(self: TestTRIMRecovery) -> None:
        """Measure every paired native cell against its own printed precision; return None."""
        total = 0
        outside = 0
        for case in sorted(FIXTURES.glob("*/mode-*")):
            manifest = json.loads((case / "manifest.json").read_bytes())
            result = recover_trim_checkpoint({p.name: p.read_bytes() for p in case.iterdir() if p.suffix.lower() == ".sav"})
            for name, profile in result["profiles"].items():
                text = (case / "native" / name).read_text(encoding="cp1252")
                native = [line.split() for line in text.splitlines() if re.match(r"^\s*\d+\.?\d*E[+-]\d+", line)]
                if profile["availability"] == "not_calculated":
                    self.assertFalse(native)
                    self.assertIn("NO Replacement Collisions", text)
                    continue
                self.assertEqual(len(native), 100)
                failures = 0
                for original, recovered in zip(native, profile["rows"], strict=True):
                    for token, value in zip(original, recovered, strict=True):
                        printed = Decimal(token)
                        failures += abs(printed - Decimal(value)) > Decimal(10) ** printed.as_tuple().exponent / 2
                        total += 1
                self.assertEqual(failures, manifest["comparison"][name.removesuffix(".txt")]["outside_half_printed_digit"], (case, name))
                outside += failures
        self.assertEqual((total, outside), (48800, 619))

    def test_export_and_readback(self: TestTRIMRecovery) -> None:
        """Export to temporary storage and verify labels, precision and hashes; return None."""
        with TemporaryDirectory(dir=CACHE) as temporary:
            source = Path(temporary) / "source"
            source.mkdir()
            for entry in FULL.iterdir():
                if entry.suffix.lower() == ".sav":
                    shutil.copyfile(entry, source / entry.name)
            original = {p.name: p.read_bytes() for p in source.iterdir()}
            destination = Path(temporary) / "output"
            report = export_trim_checkpoint_recovery(source, destination)
            self.assertEqual(len(report["files"]), 11)
            result = json.loads((destination / "recovery.json").read_bytes())
            for name, profile in result["profiles"].items():
                text = (destination / name).read_text()
                self.assertIn("RECOVERED FROM CHECKPOINT", text)
                self.assertNotIn(b"\r", (destination / name).read_bytes())
                rows = [line.split() for line in text.splitlines()[3:]]
                self.assertEqual(len(rows), 100)
                for rendered, recovered in zip(rows, profile["rows"], strict=True):
                    self.assertEqual(len(rendered), len(recovered))
                    for token, exact in zip(rendered, recovered, strict=True):
                        self.assertRegex(token, r"^-?\d\.\d{15}E[+-]\d+$")
                        printed = Decimal(token)
                        self.assertLessEqual(abs(printed - Decimal(exact)), Decimal(10) ** printed.as_tuple().exponent / 2)
            manifest = json.loads((destination / "manifest.json").read_bytes())
            for entry in manifest["outputs"]:
                self.assertEqual(hashlib.sha256((destination / entry["path"]).read_bytes()).hexdigest(), entry["sha256"])
            self.assertEqual({p.name: p.read_bytes() for p in source.iterdir()}, original)
            for mutation in ("nan", "missing", "columns", "unavailable", "schema"):
                candidate = deepcopy(result)
                if mutation == "nan":
                    candidate["profiles"]["RANGE.txt"]["rows"][0][1] = "NaN"
                elif mutation == "missing":
                    del candidate["checkpoint"]
                elif mutation == "columns":
                    candidate["profiles"]["RANGE.txt"]["columns"] = []
                elif mutation == "unavailable":
                    candidate["profiles"]["RANGE.txt"]["availability"] = "not_calculated"
                else:
                    candidate["schema_version"] = True
                with self.assertRaises(ValueError):
                    _render_recovered_files(candidate)

    def test_export_rejects_unsafe_or_existing_destination(self: TestTRIMRecovery) -> None:
        """Reject unsafe sources/destinations without overwriting temporary data; return None."""
        with TemporaryDirectory(dir=CACHE) as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            for entry in FULL.iterdir():
                if entry.suffix.lower() == ".sav":
                    shutil.copyfile(entry, source / entry.name)
            destination = root / "output"
            destination.mkdir()
            (destination / "keep").write_bytes(b"keep")
            with self.assertRaises(FileExistsError):
                export_trim_checkpoint_recovery(source, destination)
            self.assertEqual((destination / "keep").read_bytes(), b"keep")
            for bad in (source, source / "child", Path("relative")):
                with self.assertRaises(ValueError):
                    export_trim_checkpoint_recovery(source, bad)
            with patch.object(Path, "is_symlink", return_value=True):
                with self.assertRaisesRegex(ValueError, "regular frozen directory"):
                    export_trim_checkpoint_recovery(source, root / "symlink-output")
            with patch.object(Path, "lstat", return_value=SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)):
                with self.assertRaisesRegex(ValueError, "regular frozen directory"):
                    export_trim_checkpoint_recovery(source, root / "junction-output")
            (source / "nested").mkdir()
            with self.assertRaisesRegex(ValueError, "Unsafe source entry"):
                export_trim_checkpoint_recovery(source, root / "other")
            self.assertFalse((root / "other").exists())

    def test_export_detects_changed_source_and_io_failure(self: TestTRIMRecovery) -> None:
        """Inject concurrent changes and failed writes; retain partial output and report it; return None."""
        with TemporaryDirectory(dir=CACHE) as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            for entry in FULL.iterdir():
                if entry.suffix.lower() == ".sav":
                    shutil.copyfile(entry, source / entry.name)
            original = _render_recovered_files
            destination = root / "changed"
            with patch("src.srim.trim_recovery._render_recovered_files", side_effect=lambda result: ((source / "extra").write_bytes(b"new"), original(result))[1]):
                with self.assertRaisesRegex(ValueError, "Source inventory changed"):
                    export_trim_checkpoint_recovery(source, destination)
            self.assertFalse(destination.exists())
            (source / "extra").unlink()
            before = {p.name: p.read_bytes() for p in source.iterdir()}
            with patch.object(Path, "open", autospec=True, side_effect=OSError("injected failure")):
                with self.assertRaises(OSError):
                    export_trim_checkpoint_recovery(source, root / "read-failure")
            with patch("src.srim.trim_recovery._render_recovered_files", return_value={"bad/subfile.txt": b"failure"}):
                with self.assertRaises(OSError) as caught:
                    export_trim_checkpoint_recovery(source, root / "partial")
            self.assertTrue((root / "partial").is_dir())
            self.assertTrue(any("partial output retained" in note for note in caught.exception.__notes__))
            self.assertEqual({p.name: p.read_bytes() for p in source.iterdir()}, before)
            destination = root / "changed-after-write"
            with patch("src.srim.trim_recovery._render_recovered_files", return_value={"one.txt": b"one"}), patch.object(Path, "read_bytes", autospec=True, side_effect=lambda path: ((source / "added-after-write").write_bytes(b"changed"), b"one")[1] if path == destination / "one.txt" else before[path.name]):
                with self.assertRaisesRegex(ValueError, "after publication"):
                    export_trim_checkpoint_recovery(source, destination)
            self.assertTrue((destination / "one.txt").exists())

    def test_generalized_native_fixtures(self: TestTRIMRecovery) -> None:
        """Validate generalized layouts/hashes and eligible native cells; return None."""
        cases = sorted((FIXTURES / "generalized").iterdir())
        self.assertEqual(len(cases), 10)
        eligible_cases = 0
        excluded_cases = 0
        for case in cases:
            with self.subTest(case=case.name):
                manifest = json.loads((case / "manifest.json").read_bytes())
                for item in manifest["files"]:
                    data = (case / item["path"]).read_bytes()
                    self.assertEqual(len(data), item["size_bytes"])
                    self.assertEqual(hashlib.sha256(data).hexdigest(), item["sha256"])
                files = {p.name: p.read_bytes() for p in case.iterdir() if p.suffix.lower() == ".sav"}
                result = recover_trim_checkpoint(files)
                self.assertEqual(result["schema_version"], 2)
                self.assertEqual(result["format"], "trim-checkpoint-recovery-v2")
                geometry = manifest["geometry"]
                self.assertEqual(len(result["checkpoint"]["atoms"]), geometry["target_entries"])
                self.assertEqual(len(result["checkpoint"]["layers"]), geometry["layers"])
                self.assertEqual(result["grid"]["row_count"], geometry["row_count"])
                self.assertEqual(Decimal(result["grid"]["bin_width"]), Decimal(geometry["width"]))
                self.assertEqual(result["checkpoint"]["layout"]["evidence"], "metadata_driven_observed_layout")
                eligible = False
                for name, profile in result["profiles"].items():
                    comparison = manifest["comparison"][name]
                    original = (case / "native" / name).read_text(encoding="cp1252")
                    match = re.search(r"Total Ions calculated\s*=\s*([0-9.Ee+-]+)", original)
                    if profile["availability"] == "not_calculated":
                        self.assertIn("NO Replacement Collisions", original)
                        continue
                    if not comparison["numeric_comparison_eligible"]:
                        self.assertTrue(match is None or Decimal(match.group(1)) != result["checkpoint"]["counts"]["current"])
                        continue
                    self.assertIsNotNone(match)
                    self.assertEqual(Decimal(match.group(1)), result["checkpoint"]["counts"]["current"])
                    eligible = True
                    native_rows = [line.split() for line in original.splitlines() if re.match(r"^\s*\d+\.?\d*E[+-]\d+", line)]
                    self.assertEqual(len(native_rows), result["grid"]["row_count"])
                    outside = 0
                    cells = 0
                    maximum = Decimal(0)
                    with localcontext() as context:
                        context.prec = 80
                        for native_row, recovered_row in zip(native_rows, profile["rows"], strict=True):
                            self.assertEqual(len(native_row), len(recovered_row))
                            for native_token, recovered_token in zip(native_row, recovered_row, strict=True):
                                native = Decimal(native_token)
                                quantum = Decimal(10) ** native.as_tuple().exponent
                                difference = abs(native - Decimal(recovered_token))
                                outside += difference > quantum / 2
                                maximum = max(maximum, difference / quantum)
                                cells += 1
                    self.assertEqual(outside, comparison["outside_half_printed_digit"])
                    self.assertEqual(cells, comparison["cells"])
                    self.assertLess(abs(maximum - Decimal(comparison["max_printed_quanta"])), Decimal("1e-20"))
                eligible_cases += eligible
                excluded_cases += not eligible
                self.assertEqual(len(_render_recovered_files(result)), 11)
        self.assertEqual((eligible_cases, excluded_cases), (8, 2))

    def test_repeated_target_entries(self: TestTRIMRecovery) -> None:
        """Keep repeated atomic numbers distinct and quick sums auditable; return None."""
        case = FIXTURES / "generalized/case-03"
        result = recover_trim_checkpoint({p.name: p.read_bytes() for p in case.iterdir() if p.suffix.lower() == ".sav"})
        layout = result["checkpoint"]["layout"]
        self.assertEqual([entry["atomic_number"] for entry in layout["target_entries"]], [14, 8, 14])
        self.assertEqual([entry["id"] for entry in layout["target_entries"]], ["target_001_Z14", "target_002_Z8", "target_003_Z14"])
        self.assertEqual(len(layout["layers"]), 2)
        self.assertEqual(len(result["profiles"]["RANGE.txt"]["columns"]), 5)
        self.assertEqual(len(result["profiles"]["IONIZ.txt"]["columns"]), 3)
        for layer in layout["layers"]:
            self.assertEqual([entry["id"] for entry in layer["target_entries"]], [entry["id"] for entry in layout["target_entries"]])
        case = FIXTURES / "generalized/case-01"
        result = recover_trim_checkpoint({p.name: p.read_bytes() for p in case.iterdir() if p.suffix.lower() == ".sav"})
        count = Decimal(result["checkpoint"]["counts"]["current"])
        self.assertEqual(len(set(entry["id"] for entry in result["checkpoint"]["layout"]["target_entries"])), 10)
        for name in ("RANGE.txt", "VACANCY.txt", "E2RECOIL.txt"):
            profile = result["profiles"][name]
            components = profile["stored_target_components"]
            self.assertEqual(len(profile["columns"]), 3)
            self.assertEqual(len(components["entry_ids"]), 10)
            self.assertEqual(components["units"], "unscaled_saved_accumulator")
            with localcontext() as context:
                context.prec = 80
                for row, stored in zip(profile["rows"], components["rows"], strict=True):
                    self.assertEqual(len(stored), 10)
                    factor = Decimal("1e8") / count if name == "RANGE.txt" else Decimal(1) / count
                    self.assertEqual(Decimal(row[2]), sum(map(Decimal, stored)) * factor)
        shared = FIXTURES / "generalized/2013_std-ten-shared"
        result = recover_trim_checkpoint({p.name: p.read_bytes() for p in shared.iterdir() if p.suffix.lower() == ".sav"})
        self.assertEqual((len(result["checkpoint"]["atoms"]), len(result["checkpoint"]["layers"])), (1, 10))
        self.assertEqual(result["checkpoint"]["layout"]["profiles"]["RANGE.txt"]["storage_width"], 2)

    def test_metadata_derived_grid(self: TestTRIMRecovery) -> None:
        """Check archived widths and a synthetic dynamic row/grid case; return None."""
        widths = set()
        for case in (FIXTURES / "generalized").iterdir():
            result = recover_trim_checkpoint({p.name: p.read_bytes() for p in case.iterdir() if p.suffix.lower() == ".sav"})
            width = Decimal(result["grid"]["bin_width"])
            widths.add(width)
            self.assertEqual(Decimal(result["grid"]["depth"][0]), width + Decimal(".01"))
            self.assertEqual(Decimal(result["grid"]["depth"][-1]), Decimal(result["grid"]["end"]) + Decimal(".01"))
        self.assertEqual(widths, set(map(Decimal, (3, 10, 15, 30, 70, 150))))
        files = {p.name: p.read_bytes() for p in FULL.iterdir() if p.suffix.lower() == ".sav"}
        files["TDATA.sav"] = files["TDATA.sav"].replace(b"0             1000          10", b"125 1375 312.5")
        for name in ("XRANGE.sav", "VACANCY.sav", "IONIZ.SAV", "PHONON.sav", "E2RECOIL.sav"):
            files[name] = struct.pack("<H", 0) * 8
        files["NOVAC.sav"] = struct.pack("<H", 0) * 4
        files["YZRANGE#.sav"] = bytes(64)
        files["YZRADIAL#.sav"] = bytes(64)
        with localcontext() as context:
            context.prec = 3
            result = recover_trim_checkpoint(files)
            output = _render_recovered_files(result)
        self.assertEqual(result["grid"]["row_count"], 4)
        self.assertEqual(result["grid"]["depth"], ["437.51", "750.01", "1062.51", "1375.01"])
        self.assertEqual(len(output["RANGE.txt"].decode().splitlines()[3:]), 4)
        self.assertEqual(set(result["checkpoint"]["layout"]["inferred_rules"]), {"nonzero_plot_start", "fractional_bin_width", "unobserved_row_count"})

    def test_dynamic_shape_rejections(self: TestTRIMRecovery) -> None:
        """Reject inconsistent arrays, grids and target metadata; return None."""
        files = {p.name: p.read_bytes() for p in FULL.iterdir() if p.suffix.lower() == ".sav"}
        for name, replacement in (("YZRANGE#.sav", b""), ("YZRANGE#.sav", files["YZRANGE#.sav"][:-1]), ("YZRADIAL#.sav", files["YZRADIAL#.sav"][:-16]), ("XRANGE.sav", files["XRANGE.sav"] + struct.pack("<H", 0))):
            with self.subTest(name=name), self.assertRaises(ValueError):
                recover_trim_checkpoint(files | {name: replacement})
        for original, replacement in ((b"0             1000          10", b"0 2000 10"), (b"0             1000          10", b"100 0 10"), (b"0             1000          10", b"-1 1000 10"), (b'"Silicon" 1.0000E+03    1.0000E+03', b'"Silicon" 0.0000E+03    1.0000E+03'), (b'"Silicon" 1.0000E+03    1.0000E+03', b'"Silicon" 1.0000E+03    2.0000E+03'), (b"2.3212E+00 1.00000E+00", b"0.0000E+00 1.00000E+00"), (b"2.3212E+00 1.00000E+00", b"2.3212E+00 0.00000E+00")):
            self.assertIn(original, files["TDATA.sav"])
            with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                recover_trim_checkpoint(files | {"TDATA.sav": files["TDATA.sav"].replace(original, replacement, 1)})
        result = recover_trim_checkpoint(files)
        for mutation in ("row_count", "depth", "width", "components"):
            candidate = deepcopy(result)
            if mutation == "row_count":
                candidate["checkpoint"]["layout"]["row_count"] = 99
            elif mutation == "depth":
                candidate["grid"]["depth"][0] = "99"
            elif mutation == "width":
                candidate["grid"]["bin_width"] = "99"
            else:
                candidate["profiles"]["RANGE.txt"]["rows"] = []
            with self.assertRaises(ValueError):
                _render_recovered_files(candidate)

    def test_inferred_geometry_warnings(self: TestTRIMRecovery) -> None:
        """Label synthetic nonzero/fractional/gas inference without claiming native evidence; return None."""
        files = {p.name: p.read_bytes() for p in FULL.iterdir() if p.suffix.lower() == ".sav"}
        for grid, rules in ((b"125 1125 10", {"nonzero_plot_start"}), (b"0 1250 12.5", {"fractional_bin_width"})):
            candidate = files | {"TDATA.sav": files["TDATA.sav"].replace(b"0             1000          10", grid)}
            candidate["XRANGE.sav"] = struct.pack("<H", 0) * 200
            candidate["YZRANGE#.sav"] = bytes(1600)
            candidate["YZRADIAL#.sav"] = bytes(1600)
            result = recover_trim_checkpoint(candidate)
            self.assertEqual(set(result["checkpoint"]["layout"]["inferred_rules"]), rules)
            self.assertEqual(result["grid"]["evidence"], "inferred")
            self.assertIn("inferred_geometry", [warning["code"] for warning in result["warnings"]])
            self.assertIn(b"WARNING inferred_geometry", _render_recovered_files(result)["README.txt"])
        native = files["TRIMIN.sav"].decode("cp1252").splitlines()
        phase = next(i + 1 for i, line in enumerate(native) if "Target layer phases" in line)
        native[phase] = "1"
        result = recover_trim_checkpoint(files | {"TRIMIN.sav": ("\r\n".join(native) + "\r\n").encode("cp1252")})
        self.assertIn("gas_layer", result["checkpoint"]["layout"]["inferred_rules"])

    def test_generalized_export_readback(self: TestTRIMRecovery) -> None:
        """Export every generalized fixture, verify dimensions/provenance and preserve sources; return None."""
        with TemporaryDirectory(dir=CACHE) as temporary:
            root = Path(temporary)
            for case in (FIXTURES / "generalized").iterdir():
                source = root / (case.name + "-source")
                source.mkdir()
                for entry in case.iterdir():
                    if entry.suffix.lower() == ".sav":
                        shutil.copyfile(entry, source / entry.name)
                before = {p.name: p.read_bytes() for p in source.iterdir()}
                destination = root / case.name
                report = export_trim_checkpoint_recovery(source, destination)
                self.assertEqual(report["evidence"], "metadata_driven_observed_layout")
                self.assertEqual(len(report["files"]), 11)
                recovered = json.loads((destination / "recovery.json").read_bytes())
                self.assertEqual(recovered["schema_version"], 2)
                manifest = json.loads((destination / "manifest.json").read_bytes())
                self.assertEqual(manifest["scope"]["target_entries"], len(recovered["checkpoint"]["atoms"]))
                for entry in manifest["outputs"]:
                    self.assertEqual(hashlib.sha256((destination / entry["path"]).read_bytes()).hexdigest(), entry["sha256"])
                for name, profile in recovered["profiles"].items():
                    lines = (destination / name).read_text().splitlines()[3:]
                    if profile["availability"] == "not_calculated":
                        self.assertEqual(len(lines), 1)
                    else:
                        self.assertEqual(len(lines), report["layout"]["row_count"])
                        for rendered, values in zip(lines, profile["rows"], strict=True):
                            tokens = rendered.split()
                            self.assertEqual(len(tokens), len(profile["columns"]))
                            for token, value in zip(tokens, values, strict=True):
                                printed = Decimal(token)
                                self.assertLessEqual(abs(printed - Decimal(value)), Decimal(10) ** printed.as_tuple().exponent / 2)
                self.assertEqual({p.name: p.read_bytes() for p in source.iterdir()}, before)


if __name__ == "__main__":
    unittest.main()
