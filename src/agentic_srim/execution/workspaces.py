"""Accept a runtime root; exclusively lock and expose its reusable worker directory."""

from contextlib import contextmanager
import os
from pathlib import Path


@contextmanager
def locked(runtime: Path):
    """Acquire runtime's single worker lock; yield its path, or fail if busy.

    Input runtime is a generated-data root. No waiting queue is hidden here;
    OS locking releases automatically when the owner exits. Returns a context.
    """
    workspace = runtime / "workers" / "worker-1"
    workspace.mkdir(parents=True, exist_ok=True)
    with (workspace / ".lock").open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("Single simulation worker is busy") from exc
        try:
            yield workspace
        finally:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_UN)
