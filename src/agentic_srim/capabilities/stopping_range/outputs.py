"""Expose typed stopping/range rows from parsed native tables, with explicit units."""

from dataclasses import asdict, dataclass


STOPPING_UNITS = {
    1: "eV/Angstrom", 2: "keV/micron", 3: "MeV/mm", 4: "keV/(ug/cm2)",
    5: "MeV/(mg/cm2)", 6: "keV/(mg/cm2)", 7: "eV/(1E15 atoms/cm2)",
    8: "L.S.S. reduced units",
}


@dataclass(frozen=True)
class Row:
    """Construct a row from keV, native stopping values, and three lengths in nm.

    Exposes projected range and longitudinal/lateral straggling; stopping units
    are specified at table level. Straggling is not a numerical error estimate.
    """

    energy_keV: float
    electronic_stopping: float
    nuclear_stopping: float
    projected_range_nm: float
    longitudinal_straggling_nm: float
    lateral_straggling_nm: float


@dataclass(frozen=True)
class Table:
    """Construct from rows, stopping-unit label, and native simulator version.

    Exposes schema-versioned rows normalized to keV and nm without interpolation.
    """

    rows: tuple[Row, ...]
    stopping_unit: str
    simulator_version: str

    def to_dict(self) -> dict:
        """Accept no inputs; return JSON results with units and validation limits."""
        return {
            "schema_version": 1, "analysis_version": "sr-table-v1",
            "units": {"energy": "keV", "stopping": self.stopping_unit, "length": "nm"},
            "simulator_version": self.simulator_version,
            "rows": [asdict(row) for row in self.rows],
            "validation": {"format": "checked", "scientific_accuracy": "not_validated"},
            "limitations": ["Scientific reference validation deferred by user.",
                            "Energy grid is selected by SR Module; no interpolation.",
                            "Straggling is a physical distribution width, not uncertainty in the calculation.",
                            "SR Module tables are deterministic; ion count and random seed are not applicable."],
        }
