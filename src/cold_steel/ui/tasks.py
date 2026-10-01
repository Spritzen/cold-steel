"""Runs core jobs off the main thread, so the window never freezes (decision 9).

    task = runner.start(scan_mods)
    task.progress.connect(status_bar.show_progress)
    task.succeeded.connect(mod_list.set_mods)
    task.failed.connect(show_error)

Every signal is emitted on the main thread, from the event loop. So signals
connected straight after `start()` never miss a result, however fast the job.
"""

from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from cold_steel.core.jobs import Cancelled, Job, JobContext


class Task(QObject):
    """One running job. Connect to its signals; call `cancel()` to stop it."""

    progress = Signal(int, int, str)  # done, total, message
    succeeded = Signal(object)  # the job's return value
    failed = Signal(object)  # the exception it raised
    cancelled = Signal()
    finished = Signal()  # always last, whatever the outcome

    # Emitted on the worker thread. Connected here, at creation, so the queued
    # call to the main thread always has a receiver.
    _worker_progress = Signal(int, int, str)
    _worker_done = Signal(str, object)  # outcome, value

    def __init__(self, job: Job[Any], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._job = job
        self._ctx = JobContext(self._worker_progress.emit)
        self._worker_progress.connect(self.progress)
        self._worker_done.connect(self._deliver)

    def cancel(self) -> None:
        self._ctx.cancel()

    def run(self) -> None:
        """Runs on a worker thread."""
        try:
            result = self._job(self._ctx)
        except Cancelled:
            self._worker_done.emit("cancelled", None)
        except Exception as error:
            self._worker_done.emit("failed", error)
        else:
            self._worker_done.emit("cancelled" if self._ctx.cancelled else "succeeded", result)

    def _deliver(self, outcome: str, value: object) -> None:
        """Runs on the main thread."""
        if outcome == "succeeded":
            self.succeeded.emit(value)
        elif outcome == "failed":
            self.failed.emit(value)
        else:
            self.cancelled.emit()
        self.finished.emit()


class _Runnable(QRunnable):
    def __init__(self, task: Task) -> None:
        super().__init__()
        self._task = task

    def run(self) -> None:
        self._task.run()


class TaskRunner(QObject):
    """Starts tasks on a thread pool and keeps them alive until they finish."""

    def __init__(self, parent: QObject | None = None, pool: QThreadPool | None = None) -> None:
        super().__init__(parent)
        self._pool = pool or QThreadPool.globalInstance()
        self._running: set[Task] = set()

    def start(self, job: Job[Any]) -> Task:
        task = Task(job)
        self._running.add(task)
        task.finished.connect(lambda: self._running.discard(task))
        self._pool.start(_Runnable(task))
        return task

    def cancel_all(self) -> None:
        for task in list(self._running):
            task.cancel()

    def wait(self, msecs: int = -1) -> bool:
        """Block until every task has finished. For shutdown and tests."""
        return self._pool.waitForDone(msecs)

    @property
    def running(self) -> int:
        return len(self._running)
