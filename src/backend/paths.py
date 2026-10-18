"""Derive project directories and create them with success or failure reports."""

from dataclasses import dataclass
import errno
from pathlib import Path


@dataclass(frozen=True)
class ProjectPaths:
    """Store immutable project directory paths without constructor validation.

    Construction requires root, simulation_memory, postprocessing_memory, cases,
    workers, and resources Path arguments. These public fields describe the project root,
    simulation and postprocessing storage, case storage, worker storage, and shared resources.
    Use get_project_paths to obtain the agreed layout from an absolute root.
    """

    root: Path
    simulation_memory: Path
    postprocessing_memory: Path
    cases: Path
    workers: Path
    resources: Path


@dataclass(frozen=True)
class DirectoryReport:
    """Store immutable directory creation outcomes without constructor validation.

    Construction requires created and already_existed tuples of Path objects.
    These public fields contain unique paths in encounter order, recording
    successful creations and accepted existing directories respectively.
    """

    created: tuple[Path, ...]
    already_existed: tuple[Path, ...]


def get_project_paths(root: Path) -> ProjectPaths:
    """Derive the agreed layout from root without accessing the filesystem.

    Args:
        root: Absolute project root, which need not exist. No resolution or
            environment-variable expansion is performed.

    Returns:
        Immutable paths for root, memory/srim, memory/post, case, worker, and memory/resources.
        This function has no filesystem side effects.

    Raises:
        ValueError: root is not absolute.
    """
    if not root.is_absolute():
        raise ValueError("Project root must be an absolute path")
    return ProjectPaths(
        root=root,
        simulation_memory=root / "memory" / "srim",
        postprocessing_memory=root / "memory" / "post",
        cases=root / "case",
        workers=root / "worker",
        resources=root / "memory" / "resources",
    )


def create_base_directories(paths: ProjectPaths) -> DirectoryReport:
    """Create project directories and report created or accepted existing paths.

    Args:
        paths: Project paths obtained from get_project_paths. Fields are used
            directly without validating a manually constructed layout.

    Returns:
        An immutable report in encounter order. Required existing directories
        are accepted. Missing ancestors are included when
        created, but existing ancestors above the project root are excluded.

    Side effects:
        Creates directories individually, processing root, memory, simulation
        memory, postprocessing memory, cases, workers, and resources in that order. Creates
        no files and prints nothing. Calls have independent state and no locking;
        directories created first by another call are reported as existing.
        Paths and all ancestors, including those above the project root, are
        checked for symlinks and junctions before use. Another process can still
        replace a checked directory before an operation uses it.

    Raises:
        OSError: A filesystem operation failed, or a symlink or junction was
            detected. Detected links raise OSError with errno.ELOOP and their
            path and type. Original filesystem exceptions and tracebacks are
            preserved with an added note listing the failing path, partial
            outcomes, and an observable blocker. Earlier creations remain;
            nothing is rolled back. Reports reflect observations during the call.
    """
    created: list[Path] = []
    already_existed: list[Path] = []
    recorded: set[Path] = set()
    current = paths.root
    try:
        for target in (
            paths.root,
            paths.simulation_memory.parent,
            paths.simulation_memory,
            paths.postprocessing_memory,
            paths.cases,
            paths.workers,
            paths.resources,
        ):
            if target in recorded:
                continue
            for current in (*reversed(target.parents), target):
                if current.is_symlink():
                    raise OSError(errno.ELOOP, "Symlink is not allowed", str(current))
                if current.is_junction():
                    raise OSError(errno.ELOOP, "Junction is not allowed", str(current))
            pending: list[Path] = []
            current = target
            while not current.exists():
                pending.append(current)
                if current.parent == current:
                    break
                current = current.parent
            if not current.is_dir():
                current.mkdir()
            if not pending:
                already_existed.append(target)
                recorded.add(target)
            for current in reversed(pending):
                for ancestor in (*reversed(current.parents), current):
                    if ancestor.is_symlink():
                        current = ancestor
                        raise OSError(errno.ELOOP, "Symlink is not allowed", str(current))
                    if ancestor.is_junction():
                        current = ancestor
                        raise OSError(errno.ELOOP, "Junction is not allowed", str(current))
                try:
                    current.mkdir()
                except FileExistsError:
                    if current.is_symlink():
                        raise OSError(errno.ELOOP, "Symlink is not allowed", str(current))
                    if current.is_junction():
                        raise OSError(errno.ELOOP, "Junction is not allowed", str(current))
                    if not current.is_dir():
                        raise
                    if current not in recorded:
                        already_existed.append(current)
                else:
                    if current not in recorded:
                        created.append(current)
                recorded.add(current)
    except OSError as error:
        blocker = ""
        try:
            if current.is_symlink():
                blocker = "\nBlocked by a symlink."
            elif current.is_junction():
                blocker = "\nBlocked by a junction."
            elif current.is_file():
                blocker = "\nBlocked by an existing file."
        except OSError:
            pass
        created_text = "\n".join(f"  {path}" for path in created) or "  None"
        existing_text = "\n".join(f"  {path}" for path in already_existed) or "  None"
        error.add_note(
            f"Directory setup failed at: {current}{blocker}\n"
            f"Created directories:\n{created_text}\n"
            f"Already existed:\n{existing_text}"
        )
        raise
    return DirectoryReport(tuple(created), tuple(already_existed))
