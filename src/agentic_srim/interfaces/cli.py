"""Expose validated stopping/range execution and audit commands to terminal users."""

import argparse
import json
from pathlib import Path
import sqlite3
import sys

from agentic_srim.backends.sr_module.input import render
from agentic_srim.capabilities.stopping_range.inputs import Request
from agentic_srim.capabilities.stopping_range.workflow import submit, read_output as read_stopping_range_output
from agentic_srim.domain.specifications import digest
from agentic_srim.execution.jobs import cancel, read_metadata, recover
from agentic_srim.config import Settings
from agentic_srim.execution.workers import dispatch
from agentic_srim.capabilities.trim.inputs import Request as TrimRequest
from agentic_srim.capabilities.trim.workflow import submit as submit_trim, read_output
from agentic_srim.backends.trim.input import render as render_trim, render_particles


def main() -> int:
    """Read CLI arguments from argv; print JSON/native input and return exit status.

    Only run starts a simulator; validation, metadata/output reads and MCP startup do not.
    run is explicit execution; errors return 2 and unsuccessful runs return 1.
    """
    parser = argparse.ArgumentParser(description="Auditable SR Module and TRIM capabilities")
    parser.add_argument("--config", type=Path, default=Path("configs/application.toml"))
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "run"):
        command = commands.add_parser(name)
        command.add_argument("request", type=Path, help="Explicit version-1 JSON scientific request")
        command.add_argument("--capability", choices=("stopping_range", "trim"), default="stopping_range")
    output = commands.add_parser("output", help="Read verified calculation output for a capability")
    output.add_argument("run_id")
    output.add_argument("--capability", choices=("stopping_range", "trim"), default="stopping_range")
    output.add_argument("name", nargs="?", help="Required for TRIM: native output filename, e.g. COLLISON.txt")
    output.add_argument("--offset", type=int)
    output.add_argument("--limit", type=int)
    for name in ("show", "cancel"):
        commands.add_parser(name).add_argument("run_id")
    commands.add_parser("recover", help="Recover abandoned attempts after previous processes exit")
    commands.add_parser("mcp", help="Serve MCP JSON-RPC over newline-delimited stdio")
    args = parser.parse_args()
    try:
        if args.command in ("validate", "run"):
            request_type = TrimRequest if args.capability == "trim" else Request
            request = request_type.from_dict(json.loads(args.request.read_text(encoding="utf-8")))
            if args.command == "validate":
                result = {"request": request.to_dict(), "physical_hash": digest(request.physical_spec())}
                if args.capability == "trim":
                    result.update({"trim_in": render_trim(request), "trim_dat": render_particles(request).decode("ascii")})
                else:
                    result["sr_in"] = render(request)
                print(json.dumps(result, indent=2, allow_nan=False))
                return 0
        settings = Settings.load(args.config)
        runtime = settings.runtime_directory
        if args.command == "run":
            run_id = submit_trim(request, settings) if args.capability == "trim" else submit(request, settings)
            print(f"Run ID: {run_id}", file=sys.stderr, flush=True)
            result = dispatch(runtime, run_id)
        elif args.command == "show":
            result = read_metadata(runtime, args.run_id)
        elif args.command == "cancel":
            result = cancel(runtime, args.run_id)
        elif args.command == "recover":
            result = {"recovered_runs": recover(runtime)}
        elif args.command == "output":
            if args.capability == "stopping_range":
                if args.name is not None or args.offset is not None or args.limit is not None:
                    raise ValueError("Stopping/range output takes only run_id and --capability stopping_range")
                result = read_stopping_range_output(runtime, args.run_id)
            else:
                if args.name is None:
                    raise ValueError("TRIM output requires a native output filename")
                result = read_output(runtime, args.run_id, args.name,
                                     0 if args.offset is None else args.offset,
                                     100 if args.limit is None else args.limit)
        else:
            from agentic_srim.interfaces.mcp import serve
            serve(settings)
            return 0
        print(json.dumps(result, indent=2, allow_nan=False))
        if args.command == "run" and (result.get("state") != "CHECKED" or result.get("error")):
            return 1
        return 0
    except (ValueError, OSError, RuntimeError, KeyError, sqlite3.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
