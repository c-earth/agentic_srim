# Prepare TRIM inputs

[Documentation index](../README.md) · [TRIM format and API](../reference/trim-input-format.md)

Preparation resolves layered materials, masses, corrections and damage energies, then produces a reproducible input bundle. It does not stage a worker runtime or start the main TRIM calculation. Missing dependencies for an explicitly requested derived CAB correction can run through the existing SR machinery.

Use the existing settings.toml installation path. Common resource defaults are shared with SR. For a new project, initialize them explicitly with switch_sr_defaults; reuse that generation if already configured. The independent switch_trim_defaults selects the damage table. Resolution/export never switches either pointer implicitly.

```python
"""Prepare and export a 100 nm silicon input without running TRIM."""

from pathlib import Path
from src.backend.config import load_config
from src.backend.paths import create_base_directories, get_project_paths
from src.backend.records import CreateRequest, create_directories
from src.srim.defaults import switch_sr_defaults
from src.srim.trim_defaults import switch_trim_defaults
from src.srim.trim_resolution import resolve_trim_inputs, verify_trim_resolution
from src.srim.trim_storage import write_trim_input_bundle

paths = get_project_paths(Path.cwd())
create_base_directories(paths)
config = load_config(paths.root / "settings.toml")
switch_sr_defaults(paths, config)
switch_trim_defaults(paths, config)
worker = create_directories(paths, config, [CreateRequest("worker")]).completed[0]
request = {
    "schema_version": 1, "engine": "trim", "parameters": {
        "name": "Ar into 100 nm Si", "mode": "ion-quick",
        "projectile": {"atomic_number": 18, "mass_amu": "39.962", "energy_keV": "50", "angle_degrees": "0"},
        "total_ions": 1000, "random_seed": 12345, "autosave_ions": 10000,
        "outputs": {
            "ranges": False, "backscatter": False, "transmission": False,
            "sputtering": False, "collisions": "none", "exyz_increment_eV": "0",
        },
        "plot": {"type": "none", "minimum_depth_angstrom": "0", "maximum_depth_angstrom": "1000"},
        "layers": [{
            "name": "Silicon", "thickness_angstrom": "1000",
            "material": {
                "source": "direct", "phase": "solid", "density_g_cm3": "2.3212",
                "elements": [{"atomic_number": 14, "stoichiometry": "1"}],
            },
            "correction": {"mode": "none", "applied_factor": "1.0"},
            "overrides": {"density_g_cm3": None, "components": []},
        }],
        "events": None,
    },
}
selections = {
    role: {"version": "default", "overrides": [], "create_version": False}
    for role in ("atomdata", "electronic", "nuclear", "core", "bond", "damage")
}
selections["compound"] = None
bundle = resolve_trim_inputs(paths, config, worker, request, selections)
assert verify_trim_resolution(bundle) == bundle.effective_request
destination = paths.root / ".cache" / "trim-example-input"
destination.parent.mkdir(parents=True, exist_ok=True)
write_trim_input_bundle(destination, bundle)
```

The destination must be new. Choose a persistent location instead of .cache when the bundle must survive cleanup. After resolution, replay/export uses pinned bytes and needs no installation or current default aliases. No main simulation allocation/status is created.

Each layer can select a compound with `{"source":"compound","record_index":N,"phase":"solid"}` and a nonnull compound selection. Search the selected effective database first; indices differ across sources. Compound density/composition/bonds come from that record; target masses normally use effective ATOMDATA natural masses. Null projectile mass selects its most abundant isotope mass. Direct density is explicit. Derived correction is `{"mode":"derived"}` and requires direct-target bonds; none/supplied follows SR's existing forms.

```python
"""Set a component isotope, zero lattice binding and direct Surf override."""

request["parameters"]["layers"][0]["overrides"] = {
    "density_g_cm3": None,
    "components": [{"component_index": 1, "mass_amu": "29", "lattice_binding_eV": "0", "surface_binding_eV": "7"}],
}
```

Indices are one-based within each effective layer. Equal elements in different layers remain separate native atom entries. Density changes independently of composition; explicit zero energies remain zero.

Disp/Latt use material/trim-damage/vN. Its optional Surf field overrides the algorithm; otherwise Surf uses selected effective ATOMDATA: positive sublimation unchanged, zero → 2 eV. Component overrides take priority over both. These are input defaults, not material-specific physical validation; see the [measured evidence](../reference/trim-input-defaults.md).

```python
"""Publish/select a damage variant without changing default pointers."""

from src.srim.trim_defaults import create_trim_damage_variant

variant = create_trim_damage_variant(
    paths, config, base_version="default",
    overrides=[{"operation": "set", "key": [14, "surface_binding_eV"], "value": "6"}],
)
selections["damage"] = {"version": variant.version, "overrides": [], "create_version": False}
```

For specified-ion/recoil modes, events supply id, atomic_number, energy_eV, position_angstrom and direction_cosines; vectors contain three decimal strings. Five-character IDs such as T0001 are the tested form. The serializer adds ten headers, ASCII CRLF and terminal EOF, preserving vectors, positions and requested count.

Malformed records, missing selected data and the confirmed exact 90° → 0° substitution raise ValueError. Uncertain size/count/energy/seed/autosave limits are not validation caps. Inspect bundle.resolved["native_checks"] for executable checks, without automatic corrective suggestions or retries. Main-run errors/dialogs/saved substitutions belong to the future TRIM runner. hide_trim_window remains reserved for that runner.

Native checks completed ordinary-ion examples in all three versions. Embedded quotes in both names produced runtime error 9 in a 2013 Standard probe, and some event-mode probes stopped at native dialogs/timeouts. The input APIs preserve these requests and record checks; unattended execution of every event mode has not been established. See the [verification record](../TRIM_INPUT_PROPOSAL.md#implementation-verification-2026-10-21).
