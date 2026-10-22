"""Define shared JSON aliases and immutable worker-cleanup result models."""

from dataclasses import dataclass
from pathlib import Path


type JSONValue = None | bool | int | float | str | list[JSONValue] | dict[str, JSONValue]


type JSONObject = dict[str, JSONValue]


@dataclass(frozen=True)
class RetainedWorker:
    """Store worker Path, retention reason and optional originating error without I/O."""

    worker: Path
    reason: str
    error: Exception | None


@dataclass(frozen=True)
class BatchCleanupReport:
    """Store cleaned_workers paths and retained_workers explanations without validation or I/O."""

    cleaned_workers: tuple[Path, ...]
    retained_workers: tuple[RetainedWorker, ...]
