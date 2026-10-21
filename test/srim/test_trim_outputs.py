"""Verify native TRIM text fixtures, precision, completeness and ambiguity contracts."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.srim.trim_outputs import PROFILES, compare_trim_saved_values, parse_trim_checkpoint, parse_trim_outputs, parse_trim_profile, parse_trim_summary
from test_trim_inputs import EXPLICIT_REQUEST, TRIM_INSTALLATION_FILES, TRIM_REQUEST, TRIM_SELECTIONS
from copy import deepcopy


FIXTURES = Path(__file__).parent / "fixtures/trim"


class TestTRIMOutputs(unittest.TestCase):
    """Read committed 2008/2013 native evidence using inherited unittest construction."""

    def test_native_family_fixtures(self: TestTRIMOutputs) -> None:
        """Parse both complete native families and require five-ion summary/checkpoints; return None."""
        for family in ("2008", "2013"):
            root = FIXTURES / family
            report = parse_trim_outputs({p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()})
            self.assertEqual(report["issues"], [])
            self.assertTrue(report["complete"])
            self.assertEqual(report["summary"]["counts"]["calculated"], 5)
            self.assertEqual(report["checkpoint"]["counts"]["current"], 5)
            self.assertEqual(set(report["profiles"]), set(PROFILES))

    def test_printed_precision_and_units(self: TestTRIMOutputs) -> None:
        """Retain exponent tokens and explicit native units without float conversion; return None."""
        result = parse_trim_profile("RANGE.txt", (FIXTURES / "2013/RANGE.txt").read_bytes())
        self.assertEqual(result["rows"][0][0], "100100.E-04")
        self.assertEqual(result["columns"][0]["unit"], "Angstrom")
        self.assertIn("Atoms/cm3", result["columns"][1]["unit"])
        self.assertTrue(all(isinstance(value, str) for row in result["rows"] for value in row))

    def test_multielement_identity(self: TestTRIMOutputs) -> None:
        """Keep repeated species columns positionally distinct; return None."""
        data = (FIXTURES / "2013/RANGE.txt").read_bytes()
        lines = data.decode("cp1252").splitlines()
        for index, line in enumerate(lines):
            if line.startswith("   DEPTH"):
                lines[index] += " Si"
            elif line.startswith("   (Ang.)"):
                lines[index] += " Tgt. Atoms"
            elif line.startswith("-----------"):
                lines[index] += "  ----------"
            elif index >= 30 and line.split() and line.split()[0][0].isdigit():
                lines[index] += " " + line.split()[-1]
        parsed = parse_trim_profile("RANGE.txt", "\r\n".join(lines).encode("cp1252"))
        self.assertEqual([c["atom_entry_index"] for c in parsed["columns"]], [None, None, 1, 2])
        self.assertEqual(len(parsed["rows"][0]), 4)
        quick = data.replace(b"SRIM-2013.00", b"SRIM-2013.00\r\nRecoil/Damage Calculations made with Kinchin-Pease Estimates", 1)
        aggregate = parse_trim_profile("RANGE.txt", quick)
        self.assertTrue(all(column["atom_entry_index"] is None for column in aggregate["columns"]))
        self.assertIsNone(aggregate["columns"][2]["symbol"])
        self.assertIn("aggregate", aggregate["columns"][2]["label"])

    def test_missing_and_truncated_profiles(self: TestTRIMOutputs) -> None:
        """Report absent profiles and reject truncation rather than invent zero rows; return None."""
        self.assertFalse(parse_trim_outputs({})["complete"])
        data = (FIXTURES / "2013/RANGE.txt").read_bytes()
        with self.assertRaises(ValueError):
            parse_trim_profile("RANGE.txt", b"\n".join(data.splitlines()[:-1]))

    def test_ambiguous_duplicates(self: TestTRIMOutputs) -> None:
        """Reject different root/output copies and tolerate byte-identical copies; return None."""
        data = (FIXTURES / "2013/TDATA.txt").read_bytes()
        result = parse_trim_outputs({"TDATA.txt": data, "SRIM Outputs/TDATA.txt": data + b"\r\n"})
        self.assertIn("ambiguous", [i["code"] for i in result["issues"]])
        result = parse_trim_outputs({"TDATA.txt": data, "SRIM Outputs/TDATA.txt": data})
        self.assertIsNotNone(result["summary"])

    def test_incomplete_summary(self: TestTRIMOutputs) -> None:
        """Reject a summary missing its mandatory projectile/count evidence; return None."""
        with self.assertRaises(ValueError):
            parse_trim_summary(b"SRIM-2013.00\r\nTotal Ions calculated = 5\r\n")

    def test_checkpoint_comparison(self: TestTRIMOutputs) -> None:
        """Parse native checkpoint controls separately from evolving RNG state; return None."""
        root = FIXTURES / "2013/SRIM Restore"
        parsed = parse_trim_checkpoint((root / "TDATA.sav").read_bytes(), (root / "TRIMIN.sav").read_bytes())
        self.assertEqual(parsed["native_input"]["random_seed"], 12345)
        self.assertNotEqual(parsed["flags"]["values"][-1], 12345)
        self.assertEqual(parsed["layers"][0]["phase"], "solid")
        from _fixtures import make_test_project
        from test_trim_runtime import CACHE
        from src.srim.trim_defaults import switch_trim_defaults
        from src.srim.trim_resolution import resolve_trim_inputs
        with TemporaryDirectory(dir=CACHE) as td:
            paths, config, worker = make_test_project(Path(td))
            for name, data in TRIM_INSTALLATION_FILES.items():
                (config.srim_root / name).write_bytes(data)
            switch_trim_defaults(paths, config)
            request = deepcopy(TRIM_REQUEST)
            request["parameters"].update({"total_ions": 5, "mode": "ion-full", "outputs": {"ranges": True, "backscatter": True, "transmission": True, "sputtering": True, "collisions": "ions-and-recoils", "exyz_increment_eV": "1000"}})
            request["parameters"]["layers"][0]["material"]["elements"].append({"atomic_number": 8, "stoichiometry": "2"})
            inputs = resolve_trim_inputs(paths, config, worker, request, TRIM_SELECTIONS)
            saved = deepcopy(parsed)
            saved["atoms"] = deepcopy(inputs.effective_request["parameters"]["atoms"])
            saved["layers"] = deepcopy(inputs.effective_request["parameters"]["layers"])
            saved["layers"][0].update({"bottom_depth_angstrom": "1000", "stoichiometry": ["3.33333E-01", "6.66667E-01"]})
            report = compare_trim_saved_values(saved, inputs)
            self.assertEqual(report["differences"], [])
            self.assertTrue(any("stoichiometry_fraction" in item["field"] for item in report["checks"]))
            self.assertEqual([component["stoichiometry"] for component in inputs.request["parameters"]["layers"][0]["material"]["elements"]], ["1", "2"])
            saved["layers"][0]["stoichiometry"] = ["0.5", "0.5"]
            self.assertTrue(compare_trim_saved_values(saved, inputs)["differences"])

    def test_no_request_does_not_claim_success(self: TestTRIMOutputs) -> None:
        """Standalone complete parsing retains absent execution/request provenance; return None."""
        root = FIXTURES / "2013"
        report = parse_trim_outputs({p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()})
        self.assertTrue(report["complete"])
        self.assertFalse(report["consistency"]["request_checked"])
        self.assertNotIn("status", report)

    def test_native_accuracy_notes(self: TestTRIMOutputs) -> None:
        """Retain native low-ion ionization warnings as notes; return None."""
        report = parse_trim_profile("IONIZ.txt", (FIXTURES / "2013/IONIZ.txt").read_bytes())
        self.assertTrue(any("not accurate" in note for note in report["notes"]))
        quick = b"SRIM-2013.00\r\nReplacement Collisions\r\nRecoil/Damage Calculations made with Kinchin-Pease Estimates\r\nThere are NO Replacement Collisions calculated.\r\nYou must execute a full TRIM cascade calculation for these.\r\n"
        unavailable = parse_trim_profile("NOVAC.txt", quick)
        self.assertIsNone(unavailable["calculated_ions"])
        self.assertEqual(unavailable["rows"], [])
        self.assertEqual(unavailable["columns"], [])
        self.assertIn("There are NO Replacement Collisions calculated.", unavailable["notes"])
        root = FIXTURES / "2013"
        files = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
        files["NOVAC.txt"] = quick
        files = {name: data for name, data in files.items() if not name.endswith(".sav")}
        self.assertTrue(parse_trim_outputs(files)["complete"])
        request = deepcopy(EXPLICIT_REQUEST)
        request["parameters"].update({"total_ions": 5, "mode": "ion-full"})
        full = parse_trim_outputs(files, request)
        self.assertTrue(any(i["path"] == "NOVAC.txt" and "requires" in i["message"] for i in full["issues"]))
