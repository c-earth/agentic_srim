"""Verify project directory derivation, creation reports, and failure behavior."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import errno
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from src.backend.paths import DirectoryReport, ProjectPaths, create_base_directories, get_project_paths


class TestPaths(unittest.TestCase):
    """Provide path checks through unittest's inherited method-selection constructor.

    Tests create isolated fixtures beneath this module's directory and remove
    them automatically. Assertions report incorrect outcomes. Link detection is
    mocked so symlink and junction rejection checks require no privileges.
    """

    def test_path_derivation(self: TestPaths) -> None:
        """Check all paths derived for self's test; return None without filesystem changes."""
        root = Path(__file__).absolute().parent / "project"
        self.assertEqual(
            get_project_paths(root),
            ProjectPaths(root, root / "memory" / "srim", root / "memory" / "post", root / "case", root / "worker"),
        )

    def test_relative_root_rejected(self: TestPaths) -> None:
        """Check relative roots raise ValueError for self's test; return None without side effects."""
        for root in (Path("."), Path("project"), Path("D:project")):
            with self.subTest(root=root):
                with self.assertRaises(ValueError):
                    get_project_paths(root)

    def test_lookup_has_no_side_effects(self: TestPaths) -> None:
        """Check lookup leaves self's nonexistent fixture root absent; return None and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory) / "project"
            get_project_paths(root)
            self.assertFalse(root.exists())
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_creates_base_directories(self: TestPaths) -> None:
        """Check creation and report order for self's fixture; return None and clean directories."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory) / "project"
            expected = (root, root / "memory", root / "memory" / "srim", root / "memory" / "post", root / "case", root / "worker")
            self.assertEqual(create_base_directories(get_project_paths(root)), DirectoryReport(expected, ()))
            self.assertTrue(all(path.is_dir() for path in expected))
            self.assertEqual(set(root.rglob("*")), set(expected[1:]))

    def test_creates_missing_ancestors(self: TestPaths) -> None:
        """Check self's missing ancestors are reported and existing ancestors excluded; return None and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            base = Path(directory)
            root = base / "first" / "second" / "project"
            expected = (
                base / "first", base / "first" / "second", root,
                root / "memory", root / "memory" / "srim", root / "memory" / "post",
                root / "case", root / "worker",
            )
            self.assertEqual(create_base_directories(get_project_paths(root)), DirectoryReport(expected, ()))
            self.assertTrue(all(path.is_dir() for path in expected))

    def test_existing_directories_reported(self: TestPaths) -> None:
        """Check repeated setup records self's existing directories once; return None and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths = get_project_paths(Path(directory) / "project")
            first = create_base_directories(paths)
            second = create_base_directories(paths)
            self.assertEqual(second, DirectoryReport((), first.created))
            self.assertEqual(len(second.already_existed), len(set(second.already_existed)))

    def test_partial_creation_failure(self: TestPaths) -> None:
        """Check self's file blocker preserves partial work and reports it; return None and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory) / "project"
            root.mkdir()
            blocker = root / "case"
            blocker.write_text("preserve", encoding="utf-8")
            with self.assertRaises(FileExistsError) as caught:
                create_base_directories(get_project_paths(root))
            expected = (
                f"Directory setup failed at: {blocker}\nBlocked by an existing file.\n"
                f"Created directories:\n  {root / 'memory'}\n"
                f"  {root / 'memory' / 'srim'}\n  {root / 'memory' / 'post'}\n"
                f"Already existed:\n  {root}"
            )
            self.assertIn(expected, caught.exception.__notes__)
            self.assertTrue(all((root / "memory" / name).is_dir() for name in ("srim", "post")))
            self.assertFalse((root / "worker").exists())
            self.assertEqual(blocker.read_text(encoding="utf-8"), "preserve")

    def test_file_access_error_preserved(self: TestPaths) -> None:
        """Check self's mocked permission error keeps identity and empty report; return None and restore mocks."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory) / "project"
            error = PermissionError("denied")
            with patch.object(Path, "mkdir", side_effect=error):
                with self.assertRaises(PermissionError) as caught:
                    create_base_directories(get_project_paths(root))
            self.assertIs(caught.exception, error)
            self.assertIn(
                f"Directory setup failed at: {root}\nCreated directories:\n  None\nAlready existed:\n  None",
                error.__notes__,
            )
            self.assertFalse(root.exists())

    def test_concurrent_creation_reported(self: TestPaths) -> None:
        """Simulate a competing mkdir for self's test; return None, restore mocks, and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory) / "project"
            root.mkdir()
            with patch.object(Path, "mkdir", autospec=True, side_effect=FileExistsError("created concurrently")) as mocked:
                with patch.object(Path, "is_dir", autospec=True, return_value=True):
                    report = create_base_directories(get_project_paths(root))
            self.assertEqual(report.created, ())
            self.assertEqual(report.already_existed, (
                root, root / "memory", root / "memory" / "srim", root / "memory" / "post", root / "case", root / "worker",
            ))
            self.assertGreater(mocked.call_count, 0)

    def test_symlink_rejected(self: TestPaths) -> None:
        """Check simulated symlinks block self's setup with partial reports; return None and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            base = Path(directory)
            for location in ("required", "ancestor"):
                with self.subTest(location=location):
                    root = base / location / "project"
                    root.mkdir(parents=True)
                    link = root / "case" if location == "required" else root.parent

                    def is_link(path: Path) -> bool:
                        """Return whether input path is the simulated link, without side effects."""
                        return path == link

                    with patch.object(Path, "is_symlink", autospec=True, side_effect=is_link):
                        with patch.object(Path, "is_junction", autospec=True, return_value=False):
                            with self.assertRaises(OSError) as caught:
                                create_base_directories(get_project_paths(root))
                    self.assertEqual(caught.exception.errno, errno.ELOOP)
                    self.assertEqual(caught.exception.filename, str(link))
                    self.assertIn("Symlink", str(caught.exception))
                    note = caught.exception.__notes__[-1]
                    self.assertIn(f"Directory setup failed at: {link}\nBlocked by a symlink.", note)
                    if location == "required":
                        self.assertIn(
                            f"Created directories:\n  {root / 'memory'}\n"
                            f"  {root / 'memory' / 'srim'}\n  {root / 'memory' / 'post'}\n"
                            f"Already existed:\n  {root}", note,
                        )
                        self.assertTrue((root / "memory" / "post").is_dir())
                    else:
                        self.assertIn("Created directories:\n  None\nAlready existed:\n  None", note)
                        self.assertFalse((root / "memory").exists())
                    self.assertFalse((root / "case").exists())
                    self.assertFalse((root / "worker").exists())
                    self.assertTrue(root.is_dir())

    def test_junction_rejected(self: TestPaths) -> None:
        """Check simulated junctions block self's setup with partial reports; return None and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            base = Path(directory)
            for location in ("required", "ancestor"):
                with self.subTest(location=location):
                    root = base / location / "project"
                    root.mkdir(parents=True)
                    link = root / "case" if location == "required" else root.parent

                    def is_link(path: Path) -> bool:
                        """Return whether input path is the simulated link, without side effects."""
                        return path == link

                    with patch.object(Path, "is_junction", autospec=True, side_effect=is_link):
                        with patch.object(Path, "is_symlink", autospec=True, return_value=False):
                            with self.assertRaises(OSError) as caught:
                                create_base_directories(get_project_paths(root))
                    self.assertEqual(caught.exception.errno, errno.ELOOP)
                    self.assertEqual(caught.exception.filename, str(link))
                    self.assertIn("Junction", str(caught.exception))
                    note = caught.exception.__notes__[-1]
                    self.assertIn(f"Directory setup failed at: {link}\nBlocked by a junction.", note)
                    if location == "required":
                        self.assertIn(
                            f"Created directories:\n  {root / 'memory'}\n"
                            f"  {root / 'memory' / 'srim'}\n  {root / 'memory' / 'post'}\n"
                            f"Already existed:\n  {root}", note,
                        )
                        self.assertTrue((root / "memory" / "post").is_dir())
                    else:
                        self.assertIn("Created directories:\n  None\nAlready existed:\n  None", note)
                        self.assertFalse((root / "memory").exists())
                    self.assertFalse((root / "case").exists())
                    self.assertFalse((root / "worker").exists())
                    self.assertTrue(root.is_dir())

    def test_results_are_immutable(self: TestPaths) -> None:
        """Check self's result containers reject every field reassignment; return None and clean fixtures."""
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths = get_project_paths(Path(directory) / "project")
            report = create_base_directories(paths)
            for name in ("root", "simulation_memory", "postprocessing_memory", "cases", "workers"):
                with self.subTest(field=name):
                    with self.assertRaises(FrozenInstanceError):
                        setattr(paths, name, Path(directory))
            for name in ("created", "already_existed"):
                with self.subTest(field=name):
                    with self.assertRaises(FrozenInstanceError):
                        setattr(report, name, ())
