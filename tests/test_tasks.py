import threading

import pytest
from pytestqt.qtbot import QtBot

from cold_steel.core.jobs import Cancelled, JobContext
from cold_steel.ui.tasks import TaskRunner


@pytest.fixture
def runner(qtbot: QtBot) -> TaskRunner:
    return TaskRunner()


def test_result_and_progress_arrive_on_main_thread(qtbot: QtBot, runner: TaskRunner) -> None:
    main = threading.get_ident()
    seen: list[tuple[int, int, str, bool]] = []

    def job(ctx: JobContext) -> str:
        assert threading.get_ident() != main
        for i in range(3):
            ctx.progress(i, 3, f"step {i}")
        return "done"

    task = runner.start(job)
    task.progress.connect(lambda d, t, m: seen.append((d, t, m, threading.get_ident() == main)))
    with qtbot.waitSignal(task.succeeded, timeout=5000) as blocker:
        pass

    assert blocker.args == ["done"]
    qtbot.waitUntil(lambda: runner.running == 0)
    assert [s[:3] for s in seen] == [(0, 3, "step 0"), (1, 3, "step 1"), (2, 3, "step 2")]
    assert all(s[3] for s in seen)


def test_errors_are_reported(qtbot: QtBot, runner: TaskRunner) -> None:
    def job(ctx: JobContext) -> None:
        raise ValueError("bad mod")

    task = runner.start(job)
    with qtbot.waitSignal(task.failed, timeout=5000) as blocker:
        pass
    assert isinstance(blocker.args[0], ValueError)


def test_cancel_stops_the_job(qtbot: QtBot, runner: TaskRunner) -> None:
    started = threading.Event()

    def job(ctx: JobContext) -> None:
        started.set()
        while True:
            ctx.progress(0, 0)

    task = runner.start(job)
    assert started.wait(5)
    with qtbot.waitSignal(task.cancelled, timeout=5000):
        task.cancel()
    qtbot.waitUntil(lambda: runner.running == 0)


def test_job_context_works_without_qt() -> None:
    reports: list[int] = []
    ctx = JobContext(lambda d, t, m: reports.append(d))
    ctx.progress(1, 2)
    ctx.cancel()
    with pytest.raises(Cancelled):
        ctx.progress(2, 2)
    assert reports == [1]
