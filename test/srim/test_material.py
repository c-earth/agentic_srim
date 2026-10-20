"""Verify material byte preservation, exact search, patch identity and target construction."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from _fixtures import make_test_installation
from src.srim.material import _encode_atomdata, _encode_compounds, apply_material_overrides, construct_sr_target, parse_atomdata, parse_compounds, search_compounds


class TestSRMaterial(unittest.TestCase):
    """Use inherited unittest construction and temporary synthetic material sources."""

    def setUp(self: TestSRMaterial) -> None:
        """Create self's temporary installation/material fixtures; return None and register cleanup."""
        self.temporary = TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temporary.cleanup)
        self.config = make_test_installation(Path(self.temporary.name).absolute() / "installation")
        self.native = (self.config.srim_root / "Data" / "Compound.dat").read_bytes()
        self.atom_bytes = (self.config.srim_root / "Data" / "ATOMDATA").read_bytes()
        self.atoms = parse_atomdata(self.atom_bytes)
        self.compounds = parse_compounds(self.native)

    def test_atomdata_fields(self: TestSRMaterial) -> None:
        """Check self's exact atomic bytes, lexical edits and forbidden identities/removals; return None."""
        self.assertEqual(_encode_atomdata(self.atoms), self.atom_bytes)
        changed = apply_material_overrides("atomdata", self.atoms, [{"operation": "set", "key": "H", "value": {"natural_mass_amu": "2.014", "isotope_mass_number": 2}}])
        self.assertEqual(changed["rows"][0]["natural_mass_amu"], "2.014")
        self.assertEqual(self.atoms["rows"][0]["natural_mass_amu"], "1.008")
        self.assertEqual(_encode_atomdata(changed).splitlines()[3:], self.atom_bytes.splitlines()[3:])
        for operation in ({"operation": "remove", "key": "H"}, {"operation": "set", "key": 1, "value": {"symbol": "D"}}, {"operation": "set", "key": 1, "value": {"isotope_mass_number": True}}, {"operation": "set", "key": 1, "value": {"natural_mass_amu": "NaN"}}, {"operation": "set", "key": 1, "value": {}}):
            with self.assertRaises(ValueError):
                apply_material_overrides("atomdata", self.atoms, [operation])

    def test_compound_formats(self: TestSRMaterial) -> None:
        """Check self's native/JSON bases, exact spans and strict native comma validation; return None."""
        self.assertEqual([record["composition_basis"] for record in self.compounds["records"]], ["atomic", "mass"])
        self.assertEqual(self.compounds["source"]["text"].encode("cp1252"), self.native)
        reloaded = parse_compounds(_encode_compounds(self.compounds))
        self.assertEqual(reloaded["records"][1]["elements"], self.compounds["records"][1]["elements"])
        changed = self.native.replace(b'"Water", 1.0,', b'"Water" 1.0,').replace(b'8, 1\r\n', b'8, 1,\r\n')
        with self.assertRaises(ValueError):
            parse_compounds(changed)
        malformed = changed.replace(b'8, 1,\r\n', b'8, 1,,\r\n')
        with self.assertRaises(ValueError):
            parse_compounds(malformed)
        with self.assertRaises(ValueError):
            parse_compounds(b'{"schema_version":1,"schema_version":1,"records":[]}')

    def test_compound_patch_indices(self: TestSRMaterial) -> None:
        """Check self's original-index patches, appends, effective mapping and source independence; return None."""
        before = deepcopy(self.compounds)
        replacement = json.loads(_encode_compounds(self.compounds))["records"][0]
        replacement["name"] = "Edited"
        result = apply_material_overrides("compound", self.compounds, [{"operation": "remove", "key": 1}, {"operation": "set", "key": 2, "value": replacement}, {"operation": "append", "value": replacement}])
        self.assertEqual(self.compounds, before)
        self.assertEqual(result["index_map"], [{"original_index": 1, "effective_index": None}, {"original_index": 2, "effective_index": 1}, {"original_index": None, "effective_index": 2}])
        self.assertEqual([record["record_index"] for record in result["records"]], [1, 2])
        with self.assertRaises(ValueError):
            apply_material_overrides("compound", self.compounds, [{"operation": "remove", "key": 1}, {"operation": "set", "key": 1, "value": replacement}])

    def test_name_and_element_search(self: TestSRMaterial) -> None:
        """Check self's duplicate names, internal names, ordering and AND filters; return None."""
        self.assertEqual(len(search_compounds(self.compounds, name="Water")), 2)
        self.assertEqual(len(search_compounds(self.compounds, name="masswater", name_mode="contains")), 1)
        self.assertEqual(search_compounds(self.compounds, name="water"), [])
        self.assertEqual(search_compounds(self.compounds, name="Water", composition=["C"]), [])
        self.assertEqual(len(search_compounds(self.compounds, composition=["H"], composition_mode="contains")), 2)
        self.assertEqual(search_compounds(self.compounds, composition=["H"], composition_mode="elements"), [])
        found = search_compounds(self.compounds)
        found[0]["name"] = "Mutated"
        self.assertEqual(self.compounds["records"][0]["name"], "Water")

    def test_exact_atomic_ratio_search(self: TestSRMaterial) -> None:
        """Check self's exact atomic/mass ratios and required effective masses; return None."""
        matches = search_compounds(self.compounds, composition={"H": "2", "O": "1"}, composition_mode="ratio", atomdata=self.atoms)
        self.assertEqual([record["record_index"] for record in matches], [1, 2])
        self.assertEqual(search_compounds(self.compounds, composition={"H": "2.00000000000000000000000000001", "O": "1"}, composition_mode="ratio", atomdata=self.atoms), [])
        with self.assertRaises(ValueError):
            search_compounds(self.compounds, composition={"H": "2", "O": "1"}, composition_mode="ratio")
        modified = apply_material_overrides("atomdata", self.atoms, [{"operation": "set", "key": 1, "value": {"natural_mass_amu": "2.016"}}])
        self.assertEqual([record["record_index"] for record in search_compounds(self.compounds, composition={"H": "2", "O": "1"}, composition_mode="ratio", atomdata=modified)], [1])

    def test_target_construction(self: TestSRMaterial) -> None:
        """Check self's direct/compound targets, mass conversion, fractions and invalid scales; return None."""
        result = construct_sr_target({"source": "compound", "record_index": 2, "phase": "gas"}, self.atoms, self.compounds)
        self.assertEqual([element["stoichiometry"] for element in result["target"]["elements"]], ["2", "1"])
        self.assertEqual(result["provenance"]["composition_basis"], "mass")
        direct = {"source": "direct", "phase": "solid", "density_g_cm3": "1.2", "elements": [{"atomic_number": 6, "stoichiometry": "0.25"}], "bonds": {"C-H": "0.5"}}
        self.assertEqual(construct_sr_target(direct, self.atoms, None)["composition"][0]["stoichiometry"], "0.25")
        with self.assertRaises(ValueError):
            construct_sr_target({**direct, "elements": [{"atomic_number": 118, "stoichiometry": "1"}]}, self.atoms, None)
        with self.assertRaises(ValueError):
            construct_sr_target({"source": "compound", "record_index": 3, "phase": "gas"}, self.atoms, self.compounds)
        invalid = json.loads(_encode_compounds(self.compounds))
        invalid["records"][1]["bonds"] = {"H-O": "1"}
        with self.assertRaises(ValueError):
            parse_compounds(json.dumps(invalid).encode("utf-8"))
