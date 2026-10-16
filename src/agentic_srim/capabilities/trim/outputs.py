"""Expose typed TRIM profiles and streamed-detail descriptors from parsed data.

Input rows use normalized nm/keV/eV units. Output JSON retains native units,
normalization, raw-artifact lineage, and explicit scientific validation limits.
"""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Profile:
    """Construct a named profile from columns/units, rows, and native normalization.

    Rows are rectangular finite float arrays; the first column is depth in nm.
    native_ion_normalization is the printed native value, which can be fractional.
    """

    name: str
    columns: tuple[str, ...]
    units: tuple[str, ...]
    rows: tuple[tuple[float, ...], ...]
    native_units: str
    native_ion_normalization: float | None
    status: str = "parsed"

    def to_dict(self) -> dict:
        """Accept no inputs; return JSON-compatible normalized profile data."""
        return asdict(self)


@dataclass(frozen=True)
class Detail:
    """Construct a descriptor for an immutable JSONL detail artifact and raw source.

    Exposes row count, column units, and content hashes. Detailed records remain
    on disk and are read in bounded pages through CLI/MCP rather than embedded.
    """

    name: str
    records: int
    artifact: str
    sha256: str
    raw_artifact: str
    raw_sha256: str
    units: dict[str, str]

    def to_dict(self) -> dict:
        """Accept no inputs; return a JSON-compatible detail descriptor."""
        return asdict(self)
