"""What a long-running core job sees: a way to report progress and to notice
it has been cancelled. No Qt here, so jobs run the same in tests and benchmarks
as they do behind the window.

A job is any callable that takes a `JobContext` and returns a result:

    def scan_mods(ctx: JobContext) -> list[Mod]:
        for i, path in enumerate(paths):
            ctx.progress(i, len(paths), f"Reading {path.name}")
            ...
"""

import threading
from collections.abc import Callable

type ProgressReporter = Callable[[int, int, str], None]
type Job[T] = Callable[[JobContext], T]


class Cancelled(Exception):  # noqa: N818 (a signal to stop, not an error)
    """Raised inside a job when it has been asked to stop."""


def _ignore(done: int, total: int, message: str) -> None:
    pass


class JobContext:
    def __init__(self, report: ProgressReporter = _ignore) -> None:
        self._report = report
        self._cancel = threading.Event()

    def progress(self, done: int, total: int, message: str = "") -> None:
        """Report progress. Raises `Cancelled` if the job should stop."""
        self.check_cancelled()
        self._report(done, total, message)

    def check_cancelled(self) -> None:
        if self._cancel.is_set():
            raise Cancelled

    def cancel(self) -> None:
        """Ask the job to stop. Safe to call from any thread."""
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()
