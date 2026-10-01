from pytestqt.qtbot import QtBot

from cold_steel.ui.main_window import MainWindow


def test_window_opens_and_closes(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)

    window.show()
    qtbot.waitExposed(window)
    assert window.isVisible()
    assert window.windowTitle() == "Cold Steel"

    window.close()
    assert not window.isVisible()
