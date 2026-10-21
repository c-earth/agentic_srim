"""Load and validate immutable project configuration from a TOML file."""

from dataclasses import dataclass
from pathlib import Path
import tomllib
from typing import Any


@dataclass(frozen=True)
class Config:
    """Store immutable timezone, installation root and independent window preferences.

    timezone is a timezone setting string, and srim_root is the SRIM installation
    path. hide_sr_window and hide_trim_window are boolean display preferences,
    both defaulting to True. TRIM's preference controls initial display; native
    dialogs remain reportable and are never automatically answered. Direct construction
    stores inputs without validation or I/O; load_config validates file inputs.
    """

    timezone: str
    srim_root: Path
    hide_sr_window: bool = True
    hide_trim_window: bool = True


def load_config(path: Path) -> Config:
    """Read and validate the TOML configuration at path.

    Args:
        path: Configuration file path, relative to the current working directory
            or absolute. No automatic discovery or path expansion is performed.

    Returns:
        Immutable configuration with a validated timezone and absolute Windows
        SRIM root. A missing time table or timezone defaults to UTC. Signed zero
        timezone offsets are normalized to UTC; system is retained unresolved.
        Omitted srim.hide_sr_window and srim.hide_trim_window default to True;
        explicitly supplied values must be actual TOML booleans.

    Side effects:
        Opens and reads path afresh without caching, locking, or writing files.

    Raises:
        OSError: File access failed; the original exception propagates unchanged.
        tomllib.TOMLDecodeError: The file contains malformed TOML.
        ValueError: Sections, keys, types, required values, timezone syntax, or
            the absolute Windows SRIM path do not satisfy the configuration schema.
    """
    with path.open("rb") as stream:
        data: dict[str, Any] = tomllib.load(stream)

    for section in data:
        if section not in {"time", "srim"}:
            raise ValueError(f"Unknown configuration section: {section!r}")

    for section, allowed_keys in (("time", {"timezone"}), ("srim", {"root", "hide_sr_window", "hide_trim_window"})):
        if section in data:
            if not isinstance(data[section], dict):
                raise ValueError(f"Configuration section {section!r} must be a table")
            for key in data[section]:
                if key not in allowed_keys:
                    raise ValueError(f"Unknown configuration setting: {section}.{key}")

    timezone = data.get("time", {}).get("timezone", "UTC")
    if not isinstance(timezone, str):
        raise ValueError("time.timezone must be a string")
    if timezone not in {"system", "UTC"}:
        if not (
            len(timezone) == 9
            and timezone[:3] == "UTC"
            and timezone[3] in "+-"
            and timezone[6] == ":"
            and all(character in "0123456789" for character in timezone[4:6] + timezone[7:9])
            and int(timezone[4:6]) <= 23
            and int(timezone[7:9]) <= 59
        ):
            raise ValueError("time.timezone must be 'system', 'UTC', or 'UTC±HH:MM' with hours 00–23 and minutes 00–59")
        if timezone[4:6] == "00" and timezone[7:9] == "00":
            timezone = "UTC"

    if "srim" not in data or "root" not in data["srim"]:
        raise ValueError("Required configuration setting is missing: srim.root")
    root = data["srim"]["root"]
    if not isinstance(root, str):
        raise ValueError("srim.root must be a string")
    srim_root = Path(root)
    if not srim_root.is_absolute():
        raise ValueError("srim.root must be an absolute Windows path")

    hide_sr_window = data["srim"].get("hide_sr_window", True)
    hide_trim_window = data["srim"].get("hide_trim_window", True)
    for key, value in (("hide_sr_window", hide_sr_window), ("hide_trim_window", hide_trim_window)):
        if type(value) is not bool:
            raise ValueError(f"srim.{key} must be a boolean")

    return Config(timezone=timezone, srim_root=srim_root, hide_sr_window=hide_sr_window, hide_trim_window=hide_trim_window)
