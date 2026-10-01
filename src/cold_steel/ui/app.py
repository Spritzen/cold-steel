"""Creates the Qt application and shows the main window."""

from PySide6.QtWidgets import QApplication

from cold_steel import __version__
from cold_steel.ui.main_window import MainWindow


def run(argv: list[str]) -> int:
    app = QApplication(argv)
    app.setApplicationName("Cold Steel")
    app.setApplicationVersion(__version__)
    app.setDesktopFileName("cold-steel")

    window = MainWindow()
    window.show()
    return app.exec()
