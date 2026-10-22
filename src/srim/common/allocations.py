"""Validate allocated simulation and worker identities before preparation effects."""

from datetime import datetime
import errno
from src.backend.paths import ProjectPaths
from src.backend.records import CreatedDirectory, _validate_paths
from src.srim.common.records import _simulation_path


def _validate_preparation_allocations(paths: ProjectPaths, created: CreatedDirectory, worker: CreatedDirectory) -> str:
    """Validate paths/created/worker allocations without writes and return the simulation ID.

    Require an empty correctly identified main record and allocated link-free worker
    with sr/trim directories. Raise ValueError/OSError for invalid layout or identity.
    """
    _validate_paths(paths)
    if not isinstance(created, CreatedDirectory) or created.kind != "srim" or not isinstance(created.timestamp, datetime) or created.timestamp.utcoffset() is None or type(created.suffix) is not int or created.suffix < 1:
        raise ValueError("Expected an allocated simulation with aware timestamp")
    instant = created.timestamp
    simulation_id = f"srim_{instant.year:04d}{instant.month:02d}{instant.day:02d}_{instant.hour:02d}{instant.minute:02d}{instant.second:02d}_{created.suffix}"
    if created.path != paths.simulation_memory / simulation_id:
        raise ValueError("Simulation allocation does not match its identity")
    if not isinstance(worker, CreatedDirectory) or worker.kind != "worker" or worker.timestamp is not None or type(worker.suffix) is not int or worker.suffix < 1 or worker.path != paths.workers / f"worker_{worker.suffix}":
        raise ValueError("Expected an allocated worker")
    record = _simulation_path(paths, simulation_id)
    if any(record.iterdir()):
        raise ValueError("Simulation preparation requires an empty record")
    for directory in (worker.path, worker.path / "sr", worker.path / "trim"):
        if directory.is_symlink() or directory.is_junction():
            raise OSError(errno.ELOOP, "Worker path contains a link", str(directory))
        if not directory.is_dir():
            raise FileNotFoundError(str(directory))
    return simulation_id
