"""Accept scalar scientific values and JSON specifications; expose validation/hashes.

Shared by the SR and TRIM capabilities; no material or scientific defaults exist.
"""

import hashlib
import json
import math


def canonical(value: object) -> str:
    """Return deterministic finite JSON for a JSON-compatible input value."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: object) -> str:
    """Return the SHA-256 of a JSON-compatible input's canonical encoding."""
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def positive(value: object, name: str) -> float:
    """Validate a numeric input named name; return a finite positive float."""
    return finite(value, name, positive_only=True)


def finite(value: object, name: str, positive_only: bool = False) -> float:
    """Return a finite float from a numeric input, optionally requiring positivity.

    name identifies invalid inputs in ValueError messages. Integers outside the
    finite float range are validation errors rather than conversion overflows.
    """
    message = f"{name} must be a finite {'positive ' if positive_only else ''}number"
    if type(value) not in (int, float):
        raise ValueError(message)
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError(message) from exc
    if not math.isfinite(result) or (positive_only and result <= 0):
        raise ValueError(message)
    return result


def atomic_number(value: object) -> int:
    """Validate an atomic number input; return an integer in SRIM's 1..92 range."""
    return integer(value, "atomic_number", 1, 92)


def integer(value: object, name: str, minimum: int, maximum: int) -> int:
    """Return an integer input within inclusive bounds; reject bool and floats."""
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer from {minimum} to {maximum}")
    return value
