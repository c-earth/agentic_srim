"""Carry pinned layered TRIM intent and native byte inventories without execution."""

from __future__ import annotations

from dataclasses import dataclass
from src.srim.common.types import JSONObject


@dataclass(frozen=True)
class ResolvedTRIMInputs:
    """Store supplied TRIM intent, resolution and exact source/native byte inventories.

    Construction requires request, selections, effective_request, resolved,
    snapshots and engine_files and performs no validation/I/O. The tuple fields
    contain relative name/bytes pairs. Producers/consumers validate and copy
    nested JSON; frozen construction does not make nested maps immutable.
    """

    request: JSONObject
    selections: JSONObject
    effective_request: JSONObject
    resolved: JSONObject
    snapshots: tuple[tuple[str, bytes], ...]
    engine_files: tuple[tuple[str, bytes], ...]
