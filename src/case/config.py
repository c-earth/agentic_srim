"""Accept a case-specific TOML path; expose resolved runtime settings.

Configuration requires no simulator installation and performs no writes.
"""

from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class Settings:
    """Construct from a runtime path; expose its resolved independent case store."""

    runtime_directory: Path
    stores: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Accept construction fields; resolve the runtime path and return nothing."""
        object.__setattr__(self, "runtime_directory", Path(self.runtime_directory).resolve())
        object.__setattr__(self, "stores", {name: Path(value).resolve() for name, value in self.stores.items()})

    @classmethod
    def load(cls, path: Path) -> "Settings":
        """Accept a TOML file path; return settings with file-relative runtime paths."""
        path = path.resolve()
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        if set(data) - {"runtime_directory", "stores"} or "runtime_directory" not in data:
            raise ValueError("Case configuration requires runtime_directory and optional stores")
        value = data["runtime_directory"]
        if not isinstance(value, str) or not value.strip():
            raise ValueError("runtime_directory must be a nonempty path string")
        stores = data.get("stores", {"local": value})
        if not isinstance(stores, dict) or any(not isinstance(name, str) or not isinstance(item, str) or not item.strip() for name, item in stores.items()):
            raise ValueError("Case store aliases require named path strings")
        return cls(path.parent / value, {name: path.parent / item for name, item in stores.items()})
