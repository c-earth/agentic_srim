"""Accept Case MCP CLI arguments; launch stdio or print authoritative case views.

Configuration is case-specific; starting the server or reading an empty store
does not initialize a database. This entry point never starts simulation work.
"""

import argparse
import json
from pathlib import Path
import sqlite3
import sys

from case.config import Settings
from case.mcp import serve
from case.storage import Store


def main() -> int:
    """Accept arguments from argv; serve MCP/print a view, returning status 0 or 2."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Independent case management")
    parser.add_argument("--config", type=Path, default=Path("configs/case.toml"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("mcp", help="Serve Case MCP over newline-delimited stdio")
    for name in ("show", "overview"):
        command = commands.add_parser(name)
        command.add_argument("case_id")
        command.add_argument("--revision", type=int)
    args = parser.parse_args()
    try:
        settings = Settings.load(args.config)
        store = Store(settings.runtime_directory, settings.stores)
        if args.command == "mcp":
            serve(store)
        else:
            tool = "get_case_overview" if args.command == "overview" else "get_case"
            result = store.perform(tool, {"case_id": args.case_id, "revision": args.revision})
            print(result["markdown"] if args.command == "overview" else json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
