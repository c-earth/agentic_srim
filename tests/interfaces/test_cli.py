"""Accept unittest discovery; verify CLI SQLite failures in disposable runtimes.

Expose regression results for locked and invalid databases without native execution.
"""

from contextlib import closing
import io
import sqlite3
import subprocess
import sys
from unittest.mock import patch

from agentic_srim.interfaces.cli import main
from agentic_srim.storage.database import connect
from sr_module.support import IsolatedTest, REQUEST
from trim.support import FIXTURES as TRIM_FIXTURES


class CLITests(IsolatedTest):
    """Construct unittest cases; expose isolated CLI error-handling regressions."""

    def test_locked_database_returns_concise_error_without_dispatch(self) -> None:
        """Accept no inputs; verify a real SQLite lock yields exit 2; return nothing.

        A zero-timeout reader keeps the regression bounded while exercising the
        application's storage path against another connection's exclusive lock.
        """
        with connect(self.runtime):
            pass
        database = self.runtime / "metadata.sqlite"
        output, diagnostic = io.StringIO(), io.StringIO()
        with closing(sqlite3.connect(database)) as blocker:
            blocker.execute("BEGIN EXCLUSIVE")
            with closing(sqlite3.connect(database, timeout=0)) as reader:
                with patch("sys.argv", ["agentic-srim", "show", "a" * 32]), \
                        patch("sys.stdout", output), patch("sys.stderr", diagnostic), \
                        patch("agentic_srim.interfaces.cli.Settings.load", return_value=self.settings), \
                        patch("agentic_srim.storage.database.sqlite3.connect", return_value=reader), \
                        patch("agentic_srim.interfaces.cli.dispatch") as dispatch:
                    self.assertEqual(main(), 2)
                    dispatch.assert_not_called()
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(diagnostic.getvalue(), "Error: database is locked\n")

    def test_invalid_database_returns_exit_two_from_fresh_cli_processes(self) -> None:
        """Accept no inputs; verify database errors across CLI commands; return nothing.

        Fresh processes use an isolated invalid database and explicit configuration.
        Both run commands must fail during submission, before worker dispatch.
        """
        database = self.runtime / "metadata.sqlite"
        original = b"Invalid SQLite database for CLI regression.\n"
        database.write_bytes(original)
        config = self.runtime / "app.toml"
        config.write_text(
            f"srim_directory = '{self.settings.sr_module_directory.parent.as_posix()}'\n"
            f"runtime_directory = '{self.runtime.as_posix()}'\n", encoding="utf-8")
        run_id = "a" * 32
        commands = (
            ["show", run_id], ["output", run_id],
            ["output", run_id, "RANGE.txt", "--capability", "trim"],
            ["cancel", run_id], ["recover"],
            ["run", str(REQUEST)],
            ["run", str(TRIM_FIXTURES / "mode_2" / "request.json"), "--capability", "trim"],
        )
        for command in commands:
            with self.subTest(command=command):
                completed = subprocess.run(
                    [sys.executable, "-B", "-m", "agentic_srim.interfaces.cli", "--config", str(config), *command],
                    capture_output=True, text=True, timeout=10)
                self.assertEqual(completed.returncode, 2, completed.stderr)
                self.assertEqual(completed.stdout, "")
                self.assertEqual(completed.stderr, "Error: file is not a database\n")
                self.assertEqual(database.read_bytes(), original)
        self.assertFalse((self.runtime / "runs").exists())
        self.assertFalse(any(self.runtime.rglob("*.exe")))
