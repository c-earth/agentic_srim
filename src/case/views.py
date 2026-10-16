"""Accept authoritative case snapshots/checks; expose readable Markdown overviews.

Views are generated on demand, carry their revision and are never an editable
source of case state. Rendering performs no storage writes or producer calls.
"""

import json


def overview(record: dict, checks: list[dict]) -> str:
    """Accept a case snapshot/artifact checks; return its complete Markdown overview.

All case sections and the artifact index are included. JSON fences exceed any
backtick run in the payload, keeping stored user text inside its data block.
"""
    parts = [f"# Case {record['id']}",
             f"Revision: {record['revision']} | Status: {record['status']} | Archived: {record['archived']}"]
    sections = {
        "Identity": {key: record[key] for key in ("title", "schema_version", "created_at", "updated_at", "created_by", "updated_by", "parent")},
        "Original request": record["original_request"],
        "Objective": record["objective"], "Success criteria": record["success_criteria"],
        "Constraints": record["constraints"], "Assumptions": record["assumptions"],
        "Prohibitions": record["prohibitions"], "Approval evidence": record["approvals"],
        "Budget evidence (not enforced here)": record["budgets"], "Plan": record["plan"],
        "Decisions": record["decisions"], "Findings": record["findings"],
        "Unresolved issues": record["unresolved_issues"], "Next action": record["next_action"],
        "Analyses": record["analyses"], "Answers": record["answers"],
        "Artifact index": record["artifacts"], "Current artifact checks": checks,
    }
    for title, value in sections.items():
        text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
        fence = "```"
        while fence in text:
            fence += "`"
        parts.append(f"## {title}\n\n{fence}json\n{text}\n{fence}")
    return "\n\n".join(parts) + "\n"
