"""Accept explicit TRIM examples; verify scientific boundaries and native rendering."""

import copy
import io
import json
import math
import sys
from unittest.mock import patch

from agentic_srim.backends.trim.input import render, render_particles
from agentic_srim.backends.trim.output import native_input_data
from agentic_srim.capabilities.trim.inputs import Request
from agentic_srim.config import Settings
from agentic_srim.domain.specifications import digest
from agentic_srim.interfaces.cli import main
from agentic_srim.interfaces.mcp import call_tool
from trim.support import FIXTURES, IsolatedTest, fixture_request


class InputTests(IsolatedTest):
    """Construct isolated cases; verify required inputs, compatibility, and identity."""

    def test_kp_rejects_full_cascades_before_cli_or_mcp_execution(self) -> None:
        """Accept no inputs; reject incompatible output through both public interfaces."""
        for mode in (1, 4, 8):
            data = fixture_request(mode).to_dict()
            data["outputs"]["collisions"] = 2
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, "KP"):
                Request.from_dict(data)
            with patch("agentic_srim.interfaces.mcp.dispatch") as dispatch:
                result = call_tool("run_trim", {"request": data}, self.settings)
                self.assertTrue(result["isError"])
                self.assertIn("KP", result["content"][0]["text"])
                dispatch.assert_not_called()
            path = self.runtime / f"invalid_{mode}.json"
            path.write_text(json.dumps(data))
            with patch("sys.argv", ["agentic-srim", "run", str(path), "--capability", "trim"]), patch("sys.stderr", io.StringIO()), patch("agentic_srim.interfaces.cli.dispatch") as dispatch:
                self.assertEqual(main(), 2)
                dispatch.assert_not_called()
        self.assertFalse((self.runtime / "metadata.sqlite").exists())

    def test_mode_six_rejects_native_incompatible_collision_file(self) -> None:
        """Accept no inputs; reject the observed native full-recoil collision warning."""
        data = fixture_request(6).to_dict()
        for level in (1, 2):
            data["outputs"]["collisions"] = level
            with self.subTest(level=level), self.assertRaisesRegex(ValueError, "mode 6"):
                Request.from_dict(data)

    def test_invalid_scientific_inputs(self) -> None:
        """Accept no inputs; reject nonfinite, unsafe, missing, and dimensionally invalid data."""
        base = json.loads(json.dumps(self.request.to_dict()))
        changes = ({"schema_version": True}, {"calculation_mode": 9}, {"random_seed": -1},
                   {"ion_count": True}, {"ion_count": 10000000}, {"energy_keV": float("nan")},
                   {"ion_mass_u": 10 ** 400}, {"ion_mass_u": -(10 ** 400)},
                   {"angle_degrees": 90}, {"depth_min_nm": 5, "depth_max_nm": 3},
                   {"title": 'unsafe"\n'}, {"layers": []}, {"elements": []}, {"sources": {}},
                   {"particles": [{}]}, {"unknown": 1})
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                Request.from_dict(base | change)
        data = copy.deepcopy(base)
        data["layers"][0]["atomic_amounts"] = [1]
        with self.assertRaises(ValueError):
            Request.from_dict(data)
        data = copy.deepcopy(base)
        data["outputs"]["ranges"] = 1
        with self.assertRaises(ValueError):
            Request.from_dict(data)

    def test_mode_eight_collision_records_are_explicit_and_inapplicable_elsewhere(self) -> None:
        """Accept no inputs; require either extra-prompt choice without relaxing KP guards."""
        base = fixture_request(8).to_dict()
        base["outputs"]["collisions"] = 1
        with self.assertRaisesRegex(ValueError, "requires collision_records"):
            Request.from_dict(base)
        for choice in ("summary", "every"):
            data = copy.deepcopy(base)
            data["outputs"]["collision_records"] = choice
            request = Request.from_dict(data)
            self.assertEqual(request.outputs.collision_records, choice)
            data["outputs"]["collisions"] = 2
            with self.assertRaisesRegex(ValueError, "KP"):
                Request.from_dict(data)
        for choice in (True, 1, "all", [], {}):
            data = copy.deepcopy(base)
            data["outputs"]["collision_records"] = choice
            with self.subTest(choice=choice), self.assertRaises(ValueError):
                Request.from_dict(data)
        data = self.request.to_dict()
        data["outputs"]["collision_records"] = "every"
        with self.assertRaisesRegex(ValueError, "applies only"):
            Request.from_dict(data)

    def test_mode_and_collision_compatibility_matrix(self) -> None:
        """Accept no inputs; verify every mode/output-level combination before native launch."""
        for mode in range(1, 9):
            for level in range(3):
                choices = ("summary", "every") if mode == 8 and level == 1 else (None,)
                for choice in choices:
                    data = fixture_request(mode).to_dict()
                    data["outputs"].update(collisions=level, collision_records=choice)
                    rejected = (mode in (1, 4, 8) and level == 2) or (mode == 6 and level != 0)
                    with self.subTest(mode=mode, level=level, choice=choice):
                        if rejected:
                            with self.assertRaises(ValueError):
                                Request.from_dict(data)
                        else:
                            self.assertEqual(Request.from_dict(data).outputs.collisions, level)

    def test_external_event_constraints(self) -> None:
        """Accept no inputs; enforce event count, energy coverage, direction, and local species."""
        base = json.loads(json.dumps(fixture_request(7).to_dict()))
        for key, value in (("energy_eV", 99999999), ("position_nm", [1000, 0, 0]),
                           ("direction", [1, 1, 0]), ("atomic_number", 8), ("event", "../bad")):
            data = copy.deepcopy(base)
            data["particles"][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                Request.from_dict(data)
        with self.assertRaises(ValueError):
            Request.from_dict(base | {"ion_count": 5})

    def test_native_length_overflow_is_rejected_before_execution(self) -> None:
        """Accept no inputs; reject overflowing native lengths through requests and MCP."""
        boundary = sys.float_info.max / 10
        cases = [("width_nm", ("layers", 0, "width_nm"), 1),
                 ("depth_min_nm", ("depth_min_nm",), 1),
                 ("depth_max_nm", ("depth_max_nm",), 1)]
        cases.extend((f"position_nm[{axis}]", ("particles", 0, "position_nm", axis), sign)
                     for axis in range(3) for sign in ((1,) if axis == 0 else (1, -1)))
        for field, path, sign in cases:
            for magnitude in (math.nextafter(boundary, math.inf), 1e308):
                data = json.loads(json.dumps(fixture_request(7).to_dict()))
                target = data
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = sign * magnitude
                with self.subTest(field=field, value=sign * magnitude):
                    with self.assertRaises(ValueError) as raised:
                        Request.from_dict(data)
                    self.assertIn(field, str(raised.exception))
                    self.assertIn("converted from nm to Angstroms", str(raised.exception))
                    with patch("agentic_srim.interfaces.mcp.dispatch") as dispatch:
                        for tool in ("validate_trim", "run_trim"):
                            response = call_tool(tool, {"request": data}, self.settings)
                            self.assertTrue(response["isError"])
                            self.assertIn(field, response["content"][0]["text"])
                        dispatch.assert_not_called()
        self.assertFalse((self.runtime / "metadata.sqlite").exists())

    def test_finite_native_length_boundaries_remain_renderable(self) -> None:
        """Accept no inputs; preserve finite boundary lengths and signed lateral coordinates."""
        boundary = sys.float_info.max / 10
        data = json.loads(json.dumps(fixture_request(7).to_dict()))
        data["layers"][0]["width_nm"] = boundary
        data["depth_min_nm"] = boundary / 2
        data["depth_max_nm"] = boundary
        data["particles"][0]["position_nm"] = [math.nextafter(boundary, 0), boundary, -boundary]
        request = Request.from_dict(data)
        self.assertEqual(request.layers[0].width_nm, boundary)
        self.assertEqual(request.particles[0].position_nm[1:], (boundary, -boundary))
        lines = render(request).splitlines()
        native_lengths = lines[10].split()[1:]
        native_lengths.append(next(line for line in lines if line.startswith('1 "Tungsten" ')).split()[2])
        particle_fields = render_particles(request).decode("ascii").splitlines()[10].split()
        native_lengths.extend(particle_fields[3:6])
        self.assertTrue(all(math.isfinite(float(value)) for value in native_lengths))
        self.assertLess(float(particle_fields[5]), 0)
        response = call_tool("validate_trim", {"request": data}, self.settings)
        self.assertFalse(response["isError"])
        self.assertFalse((self.runtime / "metadata.sqlite").exists())

    def test_native_rendering_units_and_event_lines(self) -> None:
        """Accept no inputs; compare captured native rendering and explicit nm/eV conversions."""
        for path in sorted(FIXTURES.glob("*/request.json")):
            request = fixture_request(path.parent.name)
            mode = request.calculation_mode
            payload = render(request)
            self.assertEqual(native_input_data(payload.encode("ascii"), request),
                             native_input_data((path.parent / "TRIM.IN").read_bytes(), request))
            self.assertIn("\r\n", payload)
            self.assertEqual(payload.splitlines()[4].split()[0], str(mode))
            self.assertEqual(payload.splitlines()[10], "5 0 3000")
            self.assertIn('1 "Tungsten" 1000 19.3', payload)
            particles = render_particles(request)
            if mode >= 4:
                self.assertEqual(particles, (path.parent / "TRIM.DAT").read_bytes())
                self.assertEqual(len(particles.splitlines()), 10 + request.ion_count)
                self.assertTrue(particles.endswith(b"\r\n"))
                if mode >= 6:
                    self.assertEqual(particles.splitlines()[10].split()[3], b"300")

    def test_hash_normalizes_composition_but_separates_physics_and_execution(self) -> None:
        """Accept no inputs; verify physical identity normalization and sampling exclusion."""
        changed = json.loads(json.dumps(self.request.to_dict()))
        changed["title"] = "Other label"
        changed["ion_count"] = 9
        changed["random_seed"] = 99
        changed["outputs"]["collisions"] = 1
        for layer in changed["layers"]:
            layer["atomic_amounts"] = [v * 2 for v in layer["atomic_amounts"]]
        self.assertEqual(digest(self.request.physical_spec()), digest(Request.from_dict(changed).physical_spec()))
        changed["layers"][0]["width_nm"] = 101
        self.assertNotEqual(digest(self.request.physical_spec()), digest(Request.from_dict(changed).physical_spec()))

    def test_one_root_configuration_resolves_both_backends(self) -> None:
        """Accept no inputs; verify relative/absolute installation roots and protected paths."""
        path = self.runtime / "app.toml"
        path.write_text(f'srim_directory = "{self.settings.trim_directory.as_posix()}"\nruntime_directory = "runs"\n')
        settings = Settings.load(path)
        self.assertEqual(settings.trim_directory, self.settings.trim_directory)
        self.assertEqual(settings.sr_module_directory, self.settings.sr_module_directory)
        self.assertEqual(settings.runtime_directory, self.runtime / "runs")
        path.write_text('srim_directory = "."\nruntime_directory = "runs"\n')
        with self.assertRaises(ValueError):
            Settings.load(path)
