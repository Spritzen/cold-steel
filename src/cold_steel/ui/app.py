"""Creates the Qt application and shows the main window."""

from pathlib import Path

from PySide6.QtWidgets import QApplication

from cold_steel import __version__
from cold_steel.core.library import Scanner
from cold_steel.store.settings import load_settings, save_settings
from cold_steel.ui.main_window import MainWindow


def run(argv: list[str]) -> int:
    app = QApplication(argv)
    app.setApplicationName("Cold Steel")
    app.setApplicationVersion(__version__)
    app.setDesktopFileName("cold-steel")

    settings = load_settings()
    window = MainWindow(scan=Scanner.from_settings(settings))

    def use_steam_dir(folder: Path) -> None:
        settings.steam_dir = str(folder)
        save_settings(settings)
        window.set_scan(Scanner.from_settings(settings))

    window.steam_dir_chosen.connect(use_steam_dir)
    window.show()
    return app.exec()
