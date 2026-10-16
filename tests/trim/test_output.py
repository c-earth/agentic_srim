"""Accept captured native outputs; verify normalization, completeness, and corruption rejection."""

import json

from agentic_srim.backends.trim.output import DETAILS, PROFILES, detail_rows, parse, profile
from agentic_srim.capabilities.trim.inputs import Request
from agentic_srim.storage.artifacts import checksum, seal, verify
from trim.support import CASES, FIXTURES, IsolatedTest, fixture_request


class OutputTests(IsolatedTest):
    """Construct isolated parser cases; verify all documented native text file families."""

    def test_fixture_checksums(self) -> None:
        """Accept no inputs; verify all retained native fixtures against capture provenance."""
        provenance = json.loads((FIXTURES / "provenance.json").read_text())
        for name, expected in provenance["sha256"].items():
            self.assertEqual(checksum(FIXTURES / name), expected, name)

    def test_all_modes_parse_without_scientific_acceptance(self) -> None:
        """Accept no inputs; parse all eight modes/both KP choices and empty optional tables."""
        for case in CASES:
            request = fixture_request(case)
            with self.subTest(case=case):
                result = parse(self.copy_fixture(case), request)
                self.assertEqual(result["summary"]["actual_ion_count"], 3)
                self.assertEqual(len(result["profiles"]), 7)
                self.assertEqual(result["validation"]["scientific_accuracy"], "not_validated")
                self.assertIn("EXYZ.txt", result["details"])
                self.assertEqual(result["profiles"]["NOVAC.txt"]["status"], "not_applicable" if request.calculation_mode in (1, 4, 8) else "parsed")

    def test_profile_units_and_native_normalization_are_preserved(self) -> None:
        """Accept no inputs; verify eV/Angstrom and cm inverse conversions without redivision."""
        text = (FIXTURES / "mode_2/native/IONIZ.txt").read_text(encoding="cp1252")
        table = profile(text, "IONIZ.txt", self.request)
        self.assertNotEqual(table.native_ion_normalization, 3)
        self.assertAlmostEqual(table.rows[0][0], 3.001)
        self.assertEqual(table.units[1], "eV/(nm event)")
        values = text.splitlines()[-100].split()
        self.assertAlmostEqual(table.rows[0][1], float(values[1]) * 10)
        text = (FIXTURES / "mode_3/native/RANGE.txt").read_text(encoding="cp1252")
        table = profile(text, "RANGE.txt", fixture_request(3))
        self.assertEqual(table.units[1], "1/nm")
        self.assertAlmostEqual(table.rows[-5][1], float(text.splitlines()[-5].split()[1]) * 1e-7)

    def test_every_requested_output_file_is_required(self) -> None:
        """Accept no inputs; reject each missing profile/summary or requested detail family."""
        for name in ("TDATA.txt", *PROFILES, *("SRIM Outputs/" + v for v in DETAILS)):
            archive = self.copy_fixture()
            (archive / "native" / name).unlink()
            with self.subTest(name=name), self.assertRaises((ValueError, FileNotFoundError)):
                parse(archive, self.request)

    def test_mode_eight_collision_choices_preserve_kp_records_and_source_lines(self) -> None:
        """Accept no inputs; parse both native KP variants without inventing recoil records."""
        for case in ("summary_mode_8", "every_mode_8"):
            request = fixture_request(case)
            path = FIXTURES / case / "native/SRIM Outputs/COLLISON.txt"
            rows = list(detail_rows(path, request))
            collisions = [v for v in rows if v["kind"] == "collision"]
            self.assertEqual(len(collisions), 3)
            self.assertEqual(sum(v["kind"] == "ion_summary" for v in rows), 18)
            self.assertFalse(any(v["kind"] in ("recoil", "cascade_summary") for v in rows))
            for row in collisions:
                raw = path.read_text(encoding="cp1252").splitlines()[row["source_line"] - 1]
                fields = raw.strip().strip("\xb3").split("\xb3")
                self.assertEqual(row["displacements"], float(fields[8]))
                self.assertEqual(row["energy_keV"], 0.5)
                self.assertEqual(row["position_nm"], [30, 0, 0])
                self.assertIsNone(row["replacements"])

    def test_details_cover_all_collision_variants_and_native_sign_format(self) -> None:
        """Accept no inputs; parse KP, ion-only full damage, cascades, escapes, and EXYZ null."""
        variants = (("mode_1", 1), ("ion_only_mode_2", 2), ("mode_3", 3), ("mode_7", 7))
        for folder, mode in variants:
            request = Request.from_dict(json.loads((FIXTURES / folder / "request.json").read_text()))
            rows = list(detail_rows(FIXTURES / folder / "native/SRIM Outputs/COLLISON.txt", request))
            self.assertTrue(any(r["kind"] == "collision" for r in rows))
            self.assertEqual(sum(r["kind"] == "ion_summary" for r in rows), 18)
        rows = list(detail_rows(FIXTURES / "mode_3/native/SRIM Outputs/BACKSCAT.txt", fixture_request(3)))
        self.assertEqual(len(rows), 1)
        self.assertLess(rows[0]["position_nm"][0], 0)
        rows = list(detail_rows(FIXTURES / "mode_7/native/SRIM Outputs/EXYZ.txt", fixture_request(7)))
        self.assertIsNone(rows[0]["last_recoil_energy_eV"])

    def test_corruption_in_identity_profiles_and_details_is_rejected(self) -> None:
        """Accept no inputs; reject wrong beam/count/geometry, missing rows, units, and truncated details."""
        cases = (("TDATA.txt", "Ion Mass= 011.0000", "Ion Mass= 012.0000"),
                 ("TDATA.txt", "Total Ions calculated =  000003", "Total Ions calculated =  000002"),
                 ("TDATA.txt", "19.3 g/cm3", "20.3 g/cm3"),
                 ("IONIZ.txt", "eV /(Angstrom-Ion)", "bad units"))
        for name, old, new in cases:
            archive = self.copy_fixture()
            path = archive / "native" / name
            text = path.read_text(encoding="cp1252")
            self.assertIn(old, text)
            path.write_text(text.replace(old, new), encoding="cp1252")
            with self.subTest(name=name, old=old), self.assertRaises(ValueError):
                parse(archive, self.request)

        for name in ("RANGE.txt", "SRIM Outputs/TRANSMIT.txt", "SRIM Outputs/COLLISON.txt"):
            archive = self.copy_fixture()
            path = archive / "native" / name
            path.write_bytes(path.read_bytes()[:path.stat().st_size // 2])
            with self.subTest(name=name), self.assertRaises(ValueError):
                parse(archive, self.request)

    def test_saved_native_input_binds_composition_and_mode(self) -> None:
        """Accept no inputs; reject outputs for altered composition despite matching geometry."""
        data = self.request.to_dict()
        data["layers"][1]["atomic_amounts"] = (0, 1, 1, 0)
        with self.assertRaisesRegex(ValueError, "complete request"):
            parse(self.copy_fixture(), Request.from_dict(data))

    def test_recursive_manifests_detect_nested_tampering_and_traversal(self) -> None:
        """Accept no inputs; verify nested native/derived artifacts and reject unsafe paths."""
        archive = self.copy_fixture()
        parse(archive, self.request)
        seal(archive)
        verify(archive)
        (archive / "native/IONIZ.txt").write_bytes(b"tampered")
        with self.assertRaises(ValueError):
            verify(archive)
        manifest = archive / "manifest.json"
        manifest.write_text(json.dumps({"schema_version": 1, "artifacts": [{"path": "../outside", "size_bytes": 0, "sha256": ""}]}))
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            verify(archive)
