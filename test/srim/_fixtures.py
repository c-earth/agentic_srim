"""Build deterministic synthetic installations and controlled engine results for CAB tests."""

from collections.abc import Sequence
from pathlib import Path
import subprocess
from typing import BinaryIO

from src.backend.config import Config
from src.backend.paths import ProjectPaths, create_base_directories, get_project_paths
from src.backend.records import CreateRequest, CreatedDirectory, create_directories
from src.srim.cab import _SYMBOLS
from src.srim.metadata import JSONObject


def make_test_installation(root: Path) -> Config:
    """Create synthetic native installation files under root and return its Config.

    Generate 92 atomic/electronic rows, 4,278 nuclear rows and a small dictionary.
    Write only root; propagate filesystem errors. Never read an installed engine.
    """
    config = Config("UTC", root)
    data = root / "Data"
    module = root / "SR Module"
    data.mkdir(parents=True)
    module.mkdir()
    (root / "SR.exe").write_bytes(b"synthetic GUI executable")
    (module / "SRModule.exe").write_bytes(b"synthetic module executable")
    (module / "VERSION").write_bytes(b"SRIM-synthetic\r\n")
    (data / "VERSION").write_bytes(b"SRIM-synthetic-data\r\n")
    rows = ["Synthetic atomic data", "Synthetic fixed fields"]
    for z, symbol in enumerate(_SYMBOLS[:92], 1):
        mass = "1.008" if z == 1 else "12.011" if z == 6 else str(z * 2)
        rows.append(f"{z} {symbol} Element{z} {z * 2} {mass} {mass} 1.0 1E22 1.0 0.0 0.001 1E19")
    (data / "ATOMDATA").write_bytes(("\r\n".join(rows) + "\r\nSynthetic footer\r\n").encode("ascii"))
    electronic = [f'"Element{z}" "{symbol}" {z} ' + " ".join(["1"] * 92) for z, symbol in enumerate(_SYMBOLS[:92], 1)]
    (data / "SCOEF03.dat").write_bytes(("\r\n".join(electronic) + "\r\n").encode("ascii"))
    nuclear = [f"{left} {right} 1 2 3 4" for left in range(1, 93) for right in range(left, 93)]
    (data / "SNUC03.dat").write_bytes(("\r\n".join(nuclear) + "\r\n").encode("ascii"))
    compound = b'!Synthetic materials\r\n*Water\r\n"Water", 1.0, 2, 1, 2, 8, 1\r\n0 0 0 2 0 0 0 0 0 0 0 0 0 0 0 0 0 0\r\n$Synthetic\r\n*Water\r\n"%MassWater", 1.1, 2, 1, 2.016, 8, 16\r\n0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0\r\n'
    (data / "Compound.dat").write_bytes(compound)
    return config


def make_test_project(root: Path) -> tuple[ProjectPaths, Config, CreatedDirectory]:
    """Create root's project, synthetic installation, defaults and worker; return fixtures.

    Write only root, register no cleanup automatically and propagate setup errors.
    The calling test owns its temporary root and cleanup lifetime.
    """
    from src.srim.defaults import switch_sr_defaults
    paths = get_project_paths(root)
    create_base_directories(paths)
    config = make_test_installation(root / "installation")
    switch_sr_defaults(paths, config)
    worker = create_directories(paths, config, [CreateRequest("worker")]).completed[0]
    return paths, config, worker


def make_test_request() -> JSONObject:
    """Return independent derived H/126-keV intent with complete direct atomic target and bonds; perform no I/O."""
    return {"schema_version": 2, "parameters": {"projectile": {"atomic_number": 1, "mass_amu": "1.008"}, "target": {"source": "direct", "phase": "solid", "density_g_cm3": "1.0", "elements": [{"atomic_number": 1, "stoichiometry": "2"}, {"atomic_number": 8, "stoichiometry": "1"}], "bonds": {"H-O": "2"}}, "correction": {"mode": "derived"}, "stopping_units": 7, "energy": {"mode": "list", "values_keV": ["126"]}}}


def make_test_selections() -> JSONObject:
    """Return independent default role selections with no edits/publication and compound null; perform no I/O."""
    return {**{role: {"version": "default", "overrides": [], "create_version": False} for role in ("atomdata", "electronic", "nuclear", "core", "bond")}, "compound": None}


def fake_bragg_run(args: Sequence[str], *, cwd: Path, stdin: int, stdout: BinaryIO, stderr: BinaryIO, timeout: float, check: bool, creationflags: int) -> subprocess.CompletedProcess[bytes]:
    """Validate controlled args/cwd/handles/timeout and write a fixed successful one-row output.

    stdin/check/creationflags describe the expected owned subprocess invocation.
    Write only stopping.txt and diagnostic handles in the temporary runtime;
    return a successful CompletedProcess or raise AssertionError on wrong inputs.
    """
    assert len(args) == 1 and Path(args[0]).name == "SRModule.exe"
    assert stdin == subprocess.DEVNULL and timeout > 0 and check is False
    assert creationflags == subprocess.CREATE_NO_WINDOW
    assert (cwd / "SR.IN").is_file()
    (cwd / "stopping.txt").write_bytes(b"Ion = Hydrogen, Mass = 1.008\r\nTgt = C (Gas), Density = .001 g/cm3\r\nStopping Units = eV/(1E15 atoms/cm2)\r\nEnergy(keV)  S(Elec)    S(Nuc)\r\n1.260E+02   2.500E+01  1.000E-03\r\n")
    stdout.write(b"synthetic stdout")
    stderr.write(b"")
    return subprocess.CompletedProcess(args, 0)
