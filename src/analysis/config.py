"""Accept analysis TOML settings; expose immutable paths and execution ceilings."""

from dataclasses import dataclass, field
from pathlib import Path
import sys
import tomllib

from analysis.records import AnalysisError, NAME, validate


DEFAULT_LIMITS = {"wall_seconds": 60, "total_seconds": 180, "max_attempts": 1,
                  "input_bytes": 64 * 1024 * 1024, "input_rows": 1000000, "columns": 64,
                  "candidates": 32, "output_bytes": 256 * 1024 * 1024,
                  "log_bytes": 2 * 1024 * 1024, "figure_files": 32, "figure_pixels": 4096}
MAX_LIMITS = {**DEFAULT_LIMITS, "max_attempts": 3}
LIMIT_SCHEMA = {"type": "object", "properties": {
    name: {"type": "number" if name.endswith("seconds") else "integer", "minimum": 0.001 if name.endswith("seconds") else 1,
           "maximum": maximum} for name, maximum in MAX_LIMITS.items()}, "additionalProperties": False}
LIMIT_SCHEMA["properties"]["log_bytes"]["minimum"] = 96


@dataclass(frozen=True)
class Settings:
    """Construct from runtime/interpreter/store aliases/ceilings; expose resolved settings."""

    runtime_directory: Path
    python: Path = field(default_factory=lambda: Path(sys.executable))
    stores: dict = field(default_factory=dict)
    limits: dict = field(default_factory=lambda: dict(MAX_LIMITS))

    def __post_init__(self) -> None:
        """Accept construction fields; resolve paths and validate ceilings; return nothing."""
        validate(self.limits, LIMIT_SCHEMA, "limits")
        if set(self.limits) != set(MAX_LIMITS):
            raise AnalysisError("invalid_config", "All service ceilings are required")
        if self.limits["total_seconds"] < self.limits["wall_seconds"]:
            raise AnalysisError("invalid_config", "Cumulative ceiling must allow one full attempt")
        interpreter = Path(self.python).resolve()
        if not interpreter.is_file():
            raise AnalysisError("invalid_config", "Configured Python interpreter is missing")
        for name in self.stores:
            validate(name, NAME, "store alias")
        object.__setattr__(self, "runtime_directory", Path(self.runtime_directory).resolve())
        object.__setattr__(self, "python", interpreter)
        object.__setattr__(self, "stores", {key: Path(path).resolve() for key, path in self.stores.items()})
        object.__setattr__(self, "limits", dict(self.limits))

    @classmethod
    def load(cls, path: Path) -> "Settings":
        """Accept TOML path; return strict settings with file-relative configured paths."""
        path = path.resolve()
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        if set(data) - {"runtime_directory", "python", "stores", "limits"} or "runtime_directory" not in data:
            raise AnalysisError("invalid_config", "Unknown/missing analysis settings")
        for value in (data["runtime_directory"], data.get("python", sys.executable), *data.get("stores", {}).values()):
            if not isinstance(value, str) or not value.strip():
                raise AnalysisError("invalid_config", "Configured paths must be nonempty strings")
        return cls(path.parent / data["runtime_directory"], path.parent / data.get("python", sys.executable),
                   {key: path.parent / value for key, value in data.get("stores", {}).items()},
                   {**MAX_LIMITS, **data.get("limits", {})})

    def effective(self, requested: dict) -> dict:
        """Accept lower requested bounds; return accepted settings or reject escalation."""
        validate(requested, LIMIT_SCHEMA, "limits")
        result = {**DEFAULT_LIMITS, **self.limits, "max_attempts": 1, **requested}
        for key, value in result.items():
            if value > self.limits[key]:
                if key in requested:
                    raise AnalysisError("limit_exceeded", "Requested bound exceeds service ceiling", {"limit": key})
                result[key] = self.limits[key]
        if result["total_seconds"] < result["wall_seconds"]:
            raise AnalysisError("invalid_input", "Cumulative time must allow one attempt")
        return result
