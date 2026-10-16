"""Accept tool-discovery imports; expose bounded version-1 Case MCP contracts.

These schemas are also enforced by the store for direct Python callers. Tool
annotations describe effects; no tool starts simulations or confers approval.
"""

from case.records import ARTIFACT, CONTENT, ID, PATCH, SHORT, STATUSES, TEXT


REVISION = {"type": "integer", "minimum": 1, "maximum": 2147483647}
AUDIT = {"actor": SHORT, "reason": TEXT, "idempotency_key": SHORT}
TARGET = {"case_id": ID, "expected_revision": REVISION, **AUDIT}
READ = {"case_id": ID, "revision": {**REVISION, "type": ["integer", "null"]}}
PAGE = {"limit": {"type": "integer", "minimum": 1, "maximum": 100},
        "cursor": {**ID, "type": ["string", "null"]},
        "status": {"type": ["string", "null"], "enum": [None, *STATUSES]},
        "include_archived": {"type": "boolean"}}
PROGRESS = {"type": "object", "properties": {
    **CONTENT, "status": PATCH["properties"]["status"]},
    "minProperties": 1, "additionalProperties": False}


def schema(properties: dict, required: list[str]) -> dict:
    """Accept named property schemas/required names; return a strict object schema."""
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


SCHEMAS = {
    "create_case": schema({"title": SHORT, "original_request": TEXT, **AUDIT,
                           "content": schema(CONTENT, [])},
                          ["title", "original_request", *AUDIT]),
    "list_cases": schema(PAGE, []),
    "search_cases": schema({"query": SHORT, **PAGE}, ["query"]),
    "get_case": schema(READ, ["case_id"]),
    "update_case": schema({**TARGET, "patch": PATCH}, [*TARGET, "patch"]),
    "record_case_progress": schema({**TARGET, "progress": PROGRESS}, [*TARGET, "progress"]),
    "link_case_artifact": schema({**TARGET, "artifact": ARTIFACT, "progress": PROGRESS, "resolve_action_id": ID}, [*TARGET, "artifact"]),
    "get_case_history": schema({"case_id": ID,
                                "after_revision": {"type": "integer", "minimum": 0, "maximum": 2147483647},
                                "limit": PAGE["limit"]}, ["case_id"]),
    "branch_case": schema({"parent_case_id": ID, "parent_revision": REVISION,
                           "title": SHORT, "original_request": TEXT, **AUDIT},
                          ["parent_case_id", "parent_revision", "title", "original_request", *AUDIT]),
    "get_case_overview": schema(READ, ["case_id"]),
}
READ_TOOLS = frozenset(("list_cases", "search_cases", "get_case", "get_case_history", "get_case_overview"))
DESCRIPTIONS = {
    "create_case": "Create an active case with an immutable original request and durable retry key.",
    "list_cases": "List case summaries in ID order; archived cases are excluded by default.",
    "search_cases": "Find literal case-insensitive text across case content with bounded ID pagination.",
    "get_case": "Read a current or exact historical snapshot and local artifact availability checks.",
    "update_case": "Atomically replace supplied fields at an expected revision; preserve all earlier revisions.",
    "record_case_progress": "Save a coherent progress checkpoint, replacing supplied fields at an expected revision.",
    "link_case_artifact": "Attach an owner-qualified reference without copying or executing the producer's data.",
    "get_case_history": "Read bounded revision audit metadata; use get_case to retrieve a historical snapshot.",
    "branch_case": "Create a new active case from a specific parent revision; approvals/answers/plans are not inherited.",
    "get_case_overview": "Generate a readable Markdown overview directly from a case snapshot and artifact checks.",
}


def tool_definitions() -> list[dict]:
    """Accept no inputs; return schemas/effect hints for the ten Case MCP tools."""
    return [{"name": name, "description": DESCRIPTIONS[name], "inputSchema": value,
             "annotations": {"readOnlyHint": name in READ_TOOLS, "destructiveHint": False,
                             "idempotentHint": True, "openWorldHint": False}}
            for name, value in SCHEMAS.items()]
