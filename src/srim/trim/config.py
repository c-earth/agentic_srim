"""Assess native presentation-only config rewrites against sealed TRIM inputs."""

import hashlib
import re
from src.srim.common.types import JSONObject
from src.srim.trim.dialogs import ENGINES


def _parse_trim_config(contents: bytes) -> JSONObject:
    """Parse complete config contents into colours and protected settings.

    Return colours, label_size, maximum_secondary_recoils and help_text without
    I/O or mutation. Accept only the observed 31-line ASCII layout, spaces and
    LF/CRLF serialization. Invalid types, layout, values or text raise ValueError.
    """
    if not isinstance(contents, bytes):
        raise ValueError("TRIM config must contain bytes")
    try:
        text = contents.decode("ascii").replace("\r\n", "\n")
    except UnicodeDecodeError as error:
        raise ValueError("TRIM config must contain ASCII") from error
    if any(ord(c) < 32 and c != "\n" or ord(c) == 127 for c in text):
        raise ValueError("TRIM config contains unsupported control characters")
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    if len(lines) != 31:
        raise ValueError("TRIM config requires exactly 31 lines")
    headings = {0: "----TRIM.cfg : TRIM Color Configuration----", 2: "[Ion Colors - MOVE, STOP]", 5: "[12 Recoil Colors - MOVE, STOP]", 19: "[Distribution Plot Labels - SIZE, COLOR]", 22: "[4 Distribution Plot Colors]", 25: "[Plot Background, Outer Box, Axes, Layer Lines, Layer Names]", 28: "Maximum number of secondary recoils (default=20000)"}
    for index, expected in headings.items():
        if lines[index] != expected:
            raise ValueError(f"Unknown TRIM config heading at line {index + 1}")
    if any(lines[index] != "" for index in (1, 4, 18, 21, 24, 27)):
        raise ValueError("TRIM config section separators changed")
    numbers: dict[int, list[int]] = {}
    for index, width in ((3, 2), *((i, 3) for i in range(6, 18)), (20, 2), (23, 4), (26, 5), (29, 1)):
        if re.fullmatch(r" *[0-9]+(?: +[0-9]+)* *", lines[index]) is None:
            raise ValueError(f"Invalid TRIM config integer at line {index + 1}")
        values = [int(token) for token in lines[index].split()]
        if len(values) != width:
            raise ValueError(f"Invalid TRIM config field count at line {index + 1}")
        numbers[index] = values
    colours: JSONObject = {"ion.move": numbers[3][0], "ion.stop": numbers[3][1], "label.colour": numbers[20][1]}
    for index in range(6, 18):
        entry, move, stop = numbers[index]
        if entry != index - 5:
            raise ValueError("TRIM config recoil indices must be sequential 1 through 12")
        colours[f"recoil.{entry:02d}.move"] = move
        colours[f"recoil.{entry:02d}.stop"] = stop
    colours.update({f"distribution.{i}": colour for i, colour in enumerate(numbers[23], 1)})
    colours.update({"plot." + field: value for field, value in zip(("background", "outer_box", "axes", "layer_lines", "layer_names"), numbers[26], strict=True)})
    if any(not 0 <= value <= 16777215 for value in colours.values()):
        raise ValueError("TRIM config colour is outside the supported 24-bit range")
    if lines[30] not in ("Note: 100k recoils requires extra 2MB of memory, 1M recoils requires 35MB.", "Note: 100k recoils requires 2MB of memory, 1M recoils requires 35MB."):
        raise ValueError("Unknown TRIM config help text")
    return {"colours": colours, "label_size": numbers[20][0], "maximum_secondary_recoils": numbers[29][0], "help_text": lines[30]}


def assess_trim_config_change(before: bytes, after: bytes, *, engine_sha256: str) -> JSONObject:
    """Assess sealed before and preserved after bytes for pinned engine_sha256.

    Return hashes, status, protected values, field changes and rejection reason.
    Byte-equal contents require no interpretation. Qualify changed known layouts
    only for allowlisted engines with unchanged label size and recoil limit.
    Malformed output becomes a different report; invalid argument types/digest
    raise ValueError. Perform no I/O, mutation or lifecycle changes.
    """
    if not isinstance(before, bytes) or not isinstance(after, bytes) or not isinstance(engine_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", engine_sha256) is None:
        raise ValueError("Expected config bytes and lowercase SHA-256 engine digest")
    result: JSONObject = {"schema_version": 1, "engine": "trim", "path": "Data/TRIM.cfg", "engine_sha256": engine_sha256, "status": "equal" if before == after else "different", "before": {"sha256": hashlib.sha256(before).hexdigest(), "size_bytes": len(before)}, "after": {"sha256": hashlib.sha256(after).hexdigest(), "size_bytes": len(after)}, "protected_values": None, "changes": [], "reason": None}
    if before == after:
        return result
    if engine_sha256 not in ENGINES.values():
        result["reason"] = "Presentation qualification requires an allowlisted executable"
        return result
    try:
        original = _parse_trim_config(before)
        observed = _parse_trim_config(after)
    except ValueError as error:
        result["reason"] = str(error)
        return result
    protected = ("label_size", "maximum_secondary_recoils")
    result["protected_values"] = {key: {"before": original[key], "after": observed[key]} for key in protected}
    fields = {**original["colours"], **{key: original[key] for key in (*protected, "help_text")}}
    changed = {**observed["colours"], **{key: observed[key] for key in (*protected, "help_text")}}
    result["changes"] = [{"field": key, "before": value, "after": changed[key], "classification": "protected" if key in protected else "presentation"} for key, value in sorted(fields.items()) if value != changed[key]]
    if any(original[key] != observed[key] for key in protected):
        result["reason"] = "Protected TRIM config settings changed"
        return result
    if not result["changes"]:
        result["changes"].append({"field": "serialization", "before": result["before"]["sha256"], "after": result["after"]["sha256"], "classification": "formatting"})
    result["status"] = "qualified"
    return result
