"""The main Cold Steel window. Empty for now."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QLabel, QMainWindow, QWidget

from cold_steel.ui.tasks import TaskRunner


class MainWindow(QMainWindow):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Cold Steel")
        self.resize(1100, 700)

        self.tasks = TaskRunner(self)

        placeholder = QLabel("No mods loaded yet.")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCentralWidget(placeholder)
        self.statusBar().showMessage("Ready")

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt override)
        self.tasks.cancel_all()
        self.tasks.wait(5000)
        super().closeEvent(event)
