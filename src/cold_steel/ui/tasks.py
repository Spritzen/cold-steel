"""Runs core jobs off the main thread, so the window never freezes (decision 9).

    task = runner.start(scan_mods)
    task.progress.connect(status_bar.show_progress)
    task.succeeded.connect(mod_list.set_mods)
    task.failed.connect(show_error)

Signals are emitted from the worker thread and delivered on the main thread,
because the `Task` object lives there.
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

    def __init__(self, job: Job[Any], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._job = job
        self._ctx = JobContext(self.progress.emit)

    def cancel(self) -> None:
        self._ctx.cancel()

    def run(self) -> None:
        """Runs on a worker thread."""
        try:
            result = self._job(self._ctx)
        except Cancelled:
            self.cancelled.emit()
        except Exception as error:
            self.failed.emit(error)
        else:
            if self._ctx.cancelled:
                self.cancelled.emit()
            else:
                self.succeeded.emit(result)
        finally:
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
