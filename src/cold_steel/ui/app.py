"""Creates the Qt application and shows the main window."""

from PySide6.QtWidgets import QApplication

from cold_steel import __version__
from cold_steel.core.library import Scanner
from cold_steel.store.settings import Settings, load_settings, save_settings
from cold_steel.ui.help import WelcomeDialog
from cold_steel.ui.main_window import MainWindow
from cold_steel.ui.theme import apply_theme


def run(argv: list[str]) -> int:
    app = QApplication(argv)
    app.setApplicationName("Cold Steel")
    app.setApplicationVersion(__version__)
    app.setDesktopFileName("cold-steel")

    settings = load_settings()
    apply_theme(app, settings.theme)
    window = MainWindow(scan=Scanner.from_settings(settings), settings=settings)

    def use_settings(changed: Settings) -> None:
        nonlocal settings
        before, settings = settings, changed
        save_settings(settings)
        apply_theme(app, settings.theme)
        if (before.steam_dir, before.game_data_dir) != (settings.steam_dir, settings.game_data_dir):
            window.set_scan(Scanner.from_settings(settings))

    window.settings_chosen.connect(use_settings)
    window.show()
    if not settings.welcomed:
        WelcomeDialog(window).exec()
        settings.welcomed = True
        save_settings(settings)
    return app.exec()
