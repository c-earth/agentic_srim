"""Launch the independent local project_mcp management adapter over stdio."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import os
from pathlib import Path
import sys

from src.mcp.common.contracts import load_context
from src.mcp.common.protocol import ProtocolAdapter
from src.mcp.project_mcp.catalog import build_catalog
from src.mcp.project_mcp.operations import read_operation, write_operation


def main(argv: Sequence[str] | None = None) -> int:
    """Parse argv's absolute --root and return project_mcp's exit status.

    Use the trusted workspace without implicit cases/native work; argparse rejects
    unknown options and fatal errors are written to stderr after safe cleanup.
    """
    parser = argparse.ArgumentParser(description="Local project management MCP server")
    parser.add_argument("--root", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        context = load_context(arguments.root, "project_mcp")
        os.chdir(context.paths.root)
        ProtocolAdapter(context, build_catalog(context), read_operation, write_operation).run()
        return 0
    except (Exception, KeyboardInterrupt) as error:
        print(f"project_mcp: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
