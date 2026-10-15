"""Run lock: makes sure only one Monday job runs at a time, so two runs never send the same brief.

Uses an exclusive flock on a lock file. The system frees it when the process ends, even after a crash.
"""

import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class AlreadyRunning(RuntimeError):
    pass


@contextmanager
def run_lock(path: Path) -> Iterator[None]:
    """Hold the lock on `path` for the `with` block. Raises AlreadyRunning when another process holds it."""
    with path.open("a") as file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise AlreadyRunning(f"Another Monday job is running: {path} is locked") from None
        try:
            yield
        finally:
            fcntl.flock(file, fcntl.LOCK_UN)
