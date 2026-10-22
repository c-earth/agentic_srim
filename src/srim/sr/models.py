"""Carry pinned SR inputs and dependency evidence without validation or execution."""

from __future__ import annotations

from dataclasses import dataclass
from src.srim.common.types import JSONObject


@dataclass(frozen=True)
class ResolvedSRInputs:
    """Carry request/selections intent, effective_request, resolved evidence and pinned bytes.

    Construction stores supplied JSON objects and snapshots/engine_files tuples
    without validation or I/O. snapshots contains safe input-relative name/bytes
    pairs; engine_files contains executable/version/coefficient name/bytes pairs.
    Producers and consumers validate and defensively copy these public attributes.
    """

    request: JSONObject
    selections: JSONObject
    effective_request: JSONObject
    resolved: JSONObject
    snapshots: tuple[tuple[str, bytes], ...]
    engine_files: tuple[tuple[str, bytes], ...]


@dataclass(frozen=True)
class SRDependencyInputs:
    """Carry pinned defaults/resources/atomdata JSON and engine_files name/bytes pairs.

    Construction stores explicitly supplied inputs without validation or I/O.
    Public attributes identify effective Bragg dependencies, never supplied stopping
    values. Producers and consumers validate and independently snapshot nested data.
    """

    defaults: JSONObject
    resources: JSONObject
    atomdata: JSONObject
    engine_files: tuple[tuple[str, bytes], ...]
