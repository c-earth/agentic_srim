"""Accept Analysis CLI arguments; serve MCP, inspect jobs or explicitly install built-ins."""

import argparse
import json
from pathlib import Path
import sqlite3
import sys

from analysis.config import Settings
from analysis.mcp import serve
from analysis.runner import Dispatcher
from analysis.storage import Store


def main() -> int:
    """Accept argv; run selected operation with configured Python; return status 0 or 2."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Trusted local scientific postprocessing")
    parser.add_argument("--config", type=Path, default=Path("configs/analysis.toml"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("mcp")
    install = commands.add_parser("install-builtins")
    install.add_argument("--actor", required=True)
    install.add_argument("--reason", required=True)
    show = commands.add_parser("show")
    show.add_argument("analysis_id")
    try:
        args = parser.parse_args()
        store = Store(Settings.load(args.config))
        if args.command == "mcp":
            worker = Dispatcher(store)
            worker.start()
            try:
                serve(store)
            finally:
                worker.stop()
        elif args.command == "install-builtins":
            from analysis.builtins import install
            print(json.dumps({"methods": install(store, args.actor, args.reason)}, allow_nan=False))
        else:
            print(json.dumps(store.perform("get_analysis", {"analysis_id": args.analysis_id}), allow_nan=False, indent=2))
        return 0
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
