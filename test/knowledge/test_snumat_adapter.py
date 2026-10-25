"""Test supported SNUMAT grammar, precision, evidence and publisher-status separation."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import unittest
from src.knowledge.snumat_adapter import SnumatTRIMAdapter, normalize_snumat_case, parse_snumat_input
from src.knowledge.external_schema import MANIFEST_FIELDS, ESTIMATE_UNITS, validate_external_document

SOURCE_ID = "db_" + "0" * 64
CASE_ID = "SM-1_B_p_d0005nm"
TRIM = b"""==> SRIM-2013.00 This file controls TRIM Calculations.
Ion: Z1 , M1, Energy (keV), Angle,Number,Bragg Corr,AutoSave Number.
5 11.0000 100.0000 0 10 1 10
Cascades(1=No;2=Full;3=Sputt;4-5=Ions;6-7=Neutrons), Random Number Seed, Reminders
1 0 0
Diskfiles (0=no,1=yes): Ranges, Backscatt, Transmit, Sputtered, Collisions(1=Ion;2=Ion+Recoils), Special EXYZ.txt file
1 0 0 0 0 0
Target material : Number of Elements & Layers
"B into Si" 1 1
PlotType (0-5); Plot Depths: Xmin, Xmax(Ang.) [=0 0 for Viewing Full Target]
0 0 100000
Target Elements: Z Mass(amu)
Atom 1 = Si = 14 28.085
Layer Layer Name / Width Density Si(14)
Numb. Description (Ang) (g/cm3) Stoich
1 "Si" 100000 2.3290 1
0  Target layer phases (0=Solid, 1=Gas)
0
Target Compound Corrections (Bragg)
1
Individual target atom displacement energies (eV)
15
Individual target atom lattice binding energies (eV)
2
Individual target atom surface binding energies (eV)
4
Stopping Power Version (1=2011, 0=2011)
0
"""
TDATA = b"""====== B into Si =======
SRIM-2013.00
Ion = B (5) Ion Mass= 11.000
Energy = 100000.E-03 keV
Ion Angle to Surface = 0 degrees
Layer # 1 - Si
Layer # 1 - Bottom Depth= 1.E+05 A
Layer # 1- Density = 5.000E22 atoms/cm3 = 2.329 g/cm3
Layer # 1- Si = 100 Atomic Percent = 100 Mass Percent
Target energies for target atom = Si
Displacement = 15 eV, Binding = 2 eV, Surface = 4 eV
Total Ions calculated = 10
Average Range = 50000.E-03 Angstroms
Average Straggling = 12500.E-03 Angstroms
Average Vacancy/Ion = 37500.E-05
Total Backscattered Ions= 1
Total Transmitted Ions = 0
"""
MANIFEST = {key: "" for key in MANIFEST_FIELDS}
MANIFEST.update({"case_id": CASE_ID, "status": "PASS", "target_id": "SM-1", "target_formula": "Si", "target_source": "SM-1", "target_qa_status": "PASS", "ion_symbol": "B", "ion_z": "5", "ion_mass_u": "11.0001", "ion_selection_type": "p_type", "ion_selection_source": "synthetic", "requested_depth_nm": "5.0", "selected_energy_keV": "100.0000001", "srmodule_interpolated_rp_nm": "5.00000001", "srmodule_longitudinal_straggling_nm": "1.0", "srmodule_lateral_straggling_nm": "2.0", "density_g_cm3": "2.32900001", "reduced_stoichiometry": "Si1", "compound_correction": "1.0", "target_thickness_nm": "10000.0", "target_thickness_angstrom": "100000.0", "planned_number_of_ions": "10", "incidence_angle_deg": "0.0", "calculation_mode": "1", "range3d_enabled": "True", "trim_in_path": "targets/SM-1/B_p/depth_0005nm/TRIM.IN", "planned_range3d_path": "targets/SM-1/B_p/depth_0005nm/RANGE_3D.txt"})
MAPPING = {"case_id": CASE_ID, "target_id": "SM-1", "formula": "Si", "ion": "B", "energy_keV": "100.0000001", "density_g_cm3": "2.32900001", "sha256_delivered": hashlib.sha256(TRIM).hexdigest(), "sha256_as_run": hashlib.sha256(TRIM).hexdigest(), "status": "pass"}
MEMBERS = {"TRIM.IN": TRIM, "TDATA.txt": TDATA, "validation.json": b'{"validation_passed":true,"pysrim_run_success":false}', "run_metadata.json": json.dumps({"case_id": CASE_ID, "trim_in_sha256": hashlib.sha256(TRIM).hexdigest(), "pysrim_run_success": False}).encode("utf-8")}
MEMBERS["case.json"] = json.dumps({"case_id": CASE_ID, "manifest": MANIFEST, "case_mapping": MAPPING, "provenance": {"sha256": {key: hashlib.sha256(value).hexdigest() for key, value in MEMBERS.items()}}}, sort_keys=True).encode("utf-8")
LOCATORS = {key: {"source_id": SOURCE_ID, "relative_path": "snumat20k_v1_part01-of-10.tar.gz", "member_path": "snumat-20k_SRIM_dataset_v1/cases/SM-1/" + CASE_ID + "/" + key, "member_sha256": hashlib.sha256(value).hexdigest(), "size_bytes": len(value), "json_pointer": "", "line_start": None, "line_end": None} for key, value in MEMBERS.items()}



class TestSnumatAdapter(unittest.TestCase):
    """Exercise external schema boundaries without native execution."""

    def test_recognition_evidence(self: TestSnumatAdapter) -> None:
        """Recognize matching case/summary samples; filenames alone are insufficient."""
        adapter = SnumatTRIMAdapter()
        assert adapter.probe({"samples": []})["result"] == "insufficient_evidence"
        profile = {"samples": [{"reference": {"member_path": LOCATORS[key]["member_path"]}, "encoding": "utf-8", "content": MEMBERS[key].decode("utf-8")} for key in ("case.json", "TDATA.txt")]}
        assert adapter.probe(profile)["result"] == "match"
        profile["samples"][0]["content"] = '{"case_id":"other"}'
        assert adapter.probe(profile)["result"] == "no_match"


    def test_input_sections(self: TestSnumatAdapter) -> None:
        """Validate labeled sections, cardinality, numeric bounds and complete input."""
        parsed = parse_snumat_input(TRIM)
        assert parsed["energy_keV"]["value"] == "100.0000"
        assert parsed["layers"][0]["composition"][0]["symbol"] == "Si"
        for broken in (TRIM.replace(b'"B into Si" 1 1', b'"B into Si" 1 2'), TRIM.replace(b"1 0 0\nDiskfiles", b"2 0 0\nDiskfiles"), TRIM + b"unknown section\n", TRIM.replace(b"100.0000", b"NaN"), TRIM.replace(b"Atom 1 = Si", b"Atom 2 = Si"), TRIM.replace(b"B into", b"C into"), TRIM.replace(b"Si", b"Al"), TRIM.replace(b"B into", b"Xx into")):
            with self.assertRaises(ValueError):
                parse_snumat_input(broken)


    def test_normalization_precision_and_claims(self: TestSnumatAdapter) -> None:
        """Keep actual/planned/estimated decimals and publisher status contradictions."""
        record = normalize_snumat_case(SOURCE_ID, CASE_ID, MEMBERS, LOCATORS)
        assert record["inputs"]["energy_keV"]["value"] == "100.0000"
        assert record["planned"]["selected_energy_keV"]["value"] == "100.0000001"
        assert record["estimates"]["srmodule_interpolated_rp_nm"]["value"] == "5.00000001"
        assert record["outputs"]["summary"]["mean_range"]["value"] == "50000.E-03"
        assert "publisher_status_conflict" in {item["code"] for item in record["issues"]}
        symbol_line = record["provenance"]["input_fields"]["/inputs/ion_symbol"]["line_start"]
        assert b'"B into Si"' in TRIM.splitlines()[symbol_line - 1]
        assert record["provenance"]["validation"]["validation_passed"] is True
        assert record["provenance"]["run_metadata"]["pysrim_run_success"] is False


    def test_missing_partial_and_conflicting_outputs(self: TestSnumatAdapter) -> None:
        """Represent missing summaries and reject duplicate JSON or hash corruption."""
        missing = {key: value for key, value in MEMBERS.items() if key != "TDATA.txt"}
        record = normalize_snumat_case(SOURCE_ID, CASE_ID, missing, LOCATORS)
        assert record["outputs"]["state"] == "unavailable"
        assert record["outputs"]["summary"]["transmitted_ions"]["value"] is None
        assert record["inputs"]["number_of_ions"] == 10
        broken = dict(MEMBERS)
        broken["case.json"] = b'{"case_id":"first","case_id":"second"}'
        with self.assertRaises(ValueError):
            normalize_snumat_case(SOURCE_ID, CASE_ID, broken, LOCATORS)
        partial_members, partial_locators = dict(MEMBERS), deepcopy(LOCATORS)
        partial_members["TDATA.txt"] = TDATA.replace(b"Average Vacancy/Ion = 37500.E-05\n", b"")
        partial_case = json.loads(partial_members["case.json"])
        partial_case["provenance"]["sha256"]["TDATA.txt"] = hashlib.sha256(partial_members["TDATA.txt"]).hexdigest()
        partial_members["case.json"] = json.dumps(partial_case, sort_keys=True).encode("utf-8")
        for name in ("case.json", "TDATA.txt"):
            partial_locators[name]["member_sha256"] = hashlib.sha256(partial_members[name]).hexdigest()
            partial_locators[name]["size_bytes"] = len(partial_members[name])
        partial = normalize_snumat_case(SOURCE_ID, CASE_ID, partial_members, partial_locators)
        self.assertEqual(partial["outputs"]["state"], "partial")
        self.assertIsNone(partial["outputs"]["summary"]["vacancies_per_ion"]["value"])
        self.assertEqual(partial["outputs"]["summary"]["transmitted_ions"]["value"], "0")
        wrong_members, wrong_locators = dict(MEMBERS), deepcopy(LOCATORS)
        wrong_members["TRIM.IN"] = TRIM.replace(b"B into", b"C into")
        wrong_case = json.loads(wrong_members["case.json"])
        wrong_case["manifest"]["ion_symbol"] = "C"
        wrong_digest = hashlib.sha256(wrong_members["TRIM.IN"]).hexdigest()
        wrong_case["case_mapping"]["sha256_delivered"] = wrong_digest
        wrong_case["case_mapping"]["sha256_as_run"] = wrong_digest
        wrong_case["provenance"]["sha256"]["TRIM.IN"] = wrong_digest
        wrong_members["run_metadata.json"] = json.dumps({"trim_in_sha256": wrong_digest}).encode("utf-8")
        wrong_case["provenance"]["sha256"]["run_metadata.json"] = hashlib.sha256(wrong_members["run_metadata.json"]).hexdigest()
        wrong_members["case.json"] = json.dumps(wrong_case).encode("utf-8")
        for name in ("TRIM.IN", "case.json", "run_metadata.json"):
            wrong_locators[name]["member_sha256"] = hashlib.sha256(wrong_members[name]).hexdigest()
            wrong_locators[name]["size_bytes"] = len(wrong_members[name])
        with self.assertRaises(ValueError):
            normalize_snumat_case(SOURCE_ID, CASE_ID, wrong_members, wrong_locators)
        bad_locators = deepcopy(LOCATORS)
        bad_locators["TRIM.IN"]["member_sha256"] = "f" * 64
        with self.assertRaises(ValueError):
            normalize_snumat_case(SOURCE_ID, CASE_ID, MEMBERS, bad_locators)


    def test_nan_estimates_are_unavailable(self: TestSnumatAdapter) -> None:
        """Check synthetic estimate availability and strict numeric rejection without I/O.

        self is the unittest instance. Return None; only cloned fixture objects
        are modified. Assertion failures identify broken preservation boundaries.
        """
        original_members, original_locators = deepcopy(MEMBERS), deepcopy(LOCATORS)
        baseline = normalize_snumat_case(SOURCE_ID, CASE_ID, MEMBERS, LOCATORS)
        for key, token in [(key, token) for key in ESTIMATE_UNITS for token in ("nan", "", "1.2500", "Infinity", "-Infinity", "NaN", "junk")] + [("selected_energy_keV", "nan")]:
            with self.subTest(field=key, token=token):
                members, locators = deepcopy(MEMBERS), deepcopy(LOCATORS)
                case = json.loads(members["case.json"])
                case["manifest"][key] = token
                members["case.json"] = json.dumps(case, sort_keys=True).encode("utf-8")
                locators["case.json"]["member_sha256"] = hashlib.sha256(members["case.json"]).hexdigest()
                locators["case.json"]["size_bytes"] = len(members["case.json"])
                if token not in ("nan", "", "1.2500") or key == "selected_energy_keV":
                    with self.assertRaises(ValueError):
                        normalize_snumat_case(SOURCE_ID, CASE_ID, members, locators)
                    continue
                before_members, before_locators = deepcopy(members), deepcopy(locators)
                record = normalize_snumat_case(SOURCE_ID, CASE_ID, members, locators)
                validate_external_document(record, "record")
                quantity = record["estimates"][key]
                self.assertEqual(quantity["value"], token if token == "1.2500" else None)
                self.assertEqual(quantity["raw"], token or None)
                self.assertEqual(quantity["unit"], "nm")
                self.assertEqual(quantity["availability"], "available" if token == "1.2500" else "unavailable")
                self.assertEqual(quantity["locator"]["json_pointer"], "/manifest/" + key)
                if token == "nan":
                    issue = next(issue for issue in record["issues"] if issue["field"] == "estimates." + key)
                    self.assertEqual(issue["code"], "missing")
                    self.assertEqual(issue["locators"], [quantity["locator"]])
                self.assertEqual(record["inputs"], baseline["inputs"])
                self.assertEqual(record["outputs"], baseline["outputs"])
                self.assertEqual(members, before_members)
                self.assertEqual(locators, before_locators)
        self.assertEqual(MEMBERS, original_members)
        self.assertEqual(LOCATORS, original_locators)
