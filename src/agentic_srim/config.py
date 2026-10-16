"""Read operational TOML inputs and expose validated local execution settings."""

from dataclasses import dataclass
from pathlib import Path
import math
import tomllib


@dataclass(frozen=True)
class Settings:
    """Construct from installation/runtime paths and execution bounds.

    Exposes resolved paths, per-attempt timeout in seconds, and attempt limit.
    Paths must be disjoint so execution cannot change the source installation.
    Optional trim_directory is the native suite root. TOML loading derives both
    executable-specific paths from the required srim_directory root.
    """

    sr_module_directory: Path
    runtime_directory: Path
    timeout_seconds: float = 30
    max_attempts: int = 1
    trim_directory: Path | None = None

    def __post_init__(self) -> None:
        """Validate construction inputs, resolve paths, and return nothing."""
        source = Path(self.sr_module_directory).resolve()
        runtime = Path(self.runtime_directory).resolve()
        if source == runtime or source in runtime.parents or runtime in source.parents:
            raise ValueError("Simulator and runtime directories must be disjoint")
        if type(self.timeout_seconds) not in (int, float) or not math.isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 3600:
            raise ValueError("timeout_seconds must be finite and in (0, 3600]")
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 3:
            raise ValueError("max_attempts must be an integer from 1 to 3")
        object.__setattr__(self, "sr_module_directory", source)
        object.__setattr__(self, "runtime_directory", runtime)
        if self.trim_directory is not None:
            trim = Path(self.trim_directory).resolve()
            if trim == runtime or trim in runtime.parents or runtime in trim.parents:
                raise ValueError("TRIM and runtime directories must be disjoint")
            object.__setattr__(self, "trim_directory", trim)

    @classmethod
    def load(cls, path: Path) -> "Settings":
        """Read a TOML path; return settings with paths relative to that file."""
        path = path.resolve()
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        if set(data) - {"srim_directory", "runtime_directory", "timeout_seconds", "max_attempts"}:
            raise ValueError("Unknown configuration keys; use srim_directory for the installation root")
        if any(not isinstance(data.get(key), str) or not data[key].strip() for key in ("srim_directory", "runtime_directory")):
            raise ValueError("srim_directory and runtime_directory must be nonempty path strings")
        source = path.parent / data["srim_directory"]
        return cls(
            source / "SR Module",
            path.parent / data["runtime_directory"],
            data.get("timeout_seconds", 30),
            data.get("max_attempts", 1),
            source,
        )
