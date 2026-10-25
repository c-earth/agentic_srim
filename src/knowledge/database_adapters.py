"""Recognize scientific database schemas through explicit code-owned adapters."""

from __future__ import annotations

from copy import deepcopy
import json
import re
from typing import Protocol

from src.srim.common.types import JSONObject
from src.knowledge.snumat_adapter import SnumatTRIMAdapter


class DatabaseAdapter(Protocol):
    """Describe a pure recognizer with a stable adapter_id and positive adapter_version.

    Implementations accept bounded inspection profiles and return recognition evidence;
    construction, registration and scientific import are owned by project code.
    """

    adapter_id: str
    adapter_version: int

    def probe(self: DatabaseAdapter, profile: JSONObject) -> JSONObject:
        """Inspect profile without I/O or mutation and return a closed candidate decision."""
        ...


DATABASE_ADAPTERS: tuple[DatabaseAdapter, ...] = (SnumatTRIMAdapter(),)


def probe_database_adapters(profile: JSONObject) -> JSONObject:
    """Return known/unknown/ambiguous/insufficient_evidence for bounded profile.

    Invalid nonfinite or non-object input raises ValueError. Each installed pure
    probe receives its own copy. Ordinary failures or malformed decisions become
    insufficient-evidence candidates; no I/O, import or publication is performed.
    """
    if not isinstance(profile, dict):
        raise ValueError("Inspection profile must be an object")
    try:
        serialized = json.dumps(profile, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError("Inspection profile must be finite JSON") from error
    if len(serialized.encode("utf-8")) > 2 * 1024 * 1024:
        raise ValueError("too-large: inspection profile")
    candidates = []
    identities = set()
    for adapter in DATABASE_ADAPTERS:
        identity, version = adapter.adapter_id, adapter.adapter_version
        if not isinstance(identity, str) or re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", identity) is None or type(version) is not int or version < 1 or identity in identities:
            raise ValueError("Invalid or duplicate code-owned adapter registration")
        identities.add(identity)
        try:
            candidate = adapter.probe(deepcopy(profile))
            if not isinstance(candidate, dict) or set(candidate) != {"adapter_id", "adapter_version", "result", "evidence"} or candidate["adapter_id"] != identity or type(candidate["adapter_version"]) is not int or candidate["adapter_version"] != version or candidate["result"] not in ("match", "no_match", "insufficient_evidence") or not isinstance(candidate["evidence"], list) or len(candidate["evidence"]) > 32 or any(not isinstance(item, str) or len(item) > 2048 for item in candidate["evidence"]):
                raise ValueError("Malformed recognition decision")
            json.dumps(candidate, allow_nan=False)
        except Exception as error:
            candidate = {"adapter_id": identity, "adapter_version": version, "result": "insufficient_evidence", "evidence": [f"Probe issue: {type(error).__name__}"]}
        candidates.append(deepcopy(candidate))
    matches = sum(candidate["result"] == "match" for candidate in candidates)
    state = "ambiguous" if matches > 1 else "known" if matches == 1 else "insufficient_evidence" if any(candidate["result"] == "insufficient_evidence" for candidate in candidates) else "unknown"
    return {"state": state, "candidates": candidates}
