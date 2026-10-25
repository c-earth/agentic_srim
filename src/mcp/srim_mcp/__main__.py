"""Launch the independent local srim_mcp stdio adapter from the repository root."""

from __future__ import annotations

import argparse
from functools import partial
from collections.abc import Sequence
import os
from pathlib import Path
import sys

from src.mcp.common.contracts import load_context
from src.mcp.common.protocol import ProtocolAdapter
from src.mcp.srim_mcp.catalog import build_catalog
from src.mcp.srim_mcp.operations import read_operation, reconcile_job_runs, write_operation


def main(argv: Sequence[str] | None = None) -> int:
    """Parse argv's absolute --root, run srim_mcp, and return success/failure code.

    Unknown arguments fail through argparse. Startup/fatal errors are reported to
    stderr; no host configuration or native defaults are changed implicitly.
    """
    parser = argparse.ArgumentParser(description="Local scientific SRIM/TRIM MCP server")
    parser.add_argument("--root", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        context = load_context(arguments.root, "srim_mcp")
        os.chdir(context.paths.root)
        ProtocolAdapter(context, build_catalog(context), read_operation, write_operation, reconcile=partial(reconcile_job_runs, context)).run()
        return 0
    except (Exception, KeyboardInterrupt) as error:
        print(f"srim_mcp: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
