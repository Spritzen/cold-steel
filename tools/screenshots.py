"""Takes the README's screenshots from your real install, light and dark.

    make screenshots
    make screenshots PLAYSET="Cold Steel Mix"

It shows the playset you last played, or the one PLAYSET names. Run it on
the host for the desktop's own style; in the container it draws off screen
with Qt's plain style. Your playsets are read from a copy, so nothing of
yours is changed. Pictures go in screenshots/.
"""

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QWidget

from cold_steel.core.library import Scanner
from cold_steel.store import paths
from cold_steel.store.settings import Theme, load_settings
from cold_steel.ui.help import WelcomeDialog
from cold_steel.ui.main_window import MainWindow
from cold_steel.ui.settings_dialog import SettingsDialog
from cold_steel.ui.theme import apply_theme

OUT = Path(__file__).parent.parent / "screenshots"


def main() -> int:
    parser = argparse.ArgumentParser(description="Take the README's screenshots.")
    parser.add_argument("--playset", default="", help="the playset to show, by name")
    wanted = parser.parse_args().playset
    OUT.mkdir(exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="cold-steel-shots-"))
    if (mine := paths.data_dir() / "playsets.json").exists():
        shutil.copy(mine, scratch / "playsets.json")

    app = QApplication(sys.argv[:1])
    app.setApplicationName("Cold Steel")
    settings = load_settings()
    window = MainWindow(
        Scanner.from_settings(settings),
        playsets_path=scratch / "playsets.json",
        backup_dir=scratch / "backups",
        settings=settings,
    )

    def save(widget: QWidget, name: str) -> None:
        app.processEvents()
        widget.grab().save(str(OUT / f"{name}.png"))
        print(OUT / f"{name}.png")

    def select_playset() -> None:
        book = window.book
        named = [p.id for p in book.playsets if p.name == wanted] if book and wanted else []
        if wanted and not named:
            print(f"No playset is called {wanted!r}. Showing the one you last played.")
        choice = named[0] if named else book.active if book else None
        for row in range(window.playset_list.count()):
            if window.playset_list.item(row).data(Qt.ItemDataRole.UserRole) == choice:
                window.playset_list.setCurrentRow(row)
                return
        window.playset_list.setCurrentRow(min(1, window.playset_list.count() - 1))

    def take(theme: Theme) -> None:
        apply_theme(app, theme)
        save(window, f"main-{theme}")
        library = window.library
        found = library.game.data_dir if library else None
        for name, dialog in (
            ("settings", SettingsDialog(settings, found, window)),
            ("welcome", WelcomeDialog(window)),
        ):
            dialog.show()
            save(dialog, f"{name}-{theme}")
            dialog.close()
        conflicts = window._conflicts_window
        if conflicts is not None:
            save(conflicts, f"conflicts-{theme}")

    def conflicts_ready() -> None:
        conflicts = window._conflicts_window
        group = conflicts.tree.topLevelItem(0) if conflicts else None
        first = group.child(0) if group else None
        if conflicts is not None and group is not None and first is not None:
            # Open the first group and show its first clash side by side.
            group.setExpanded(True)
            conflicts.tree.setCurrentItem(first)
        QTimer.singleShot(1000, take_all)

    def take_all() -> None:
        take("light")
        take("dark")
        app.quit()

    def library_ready() -> None:
        select_playset()
        window.conflicts_shown.connect(lambda _: conflicts_ready())
        window.show_conflicts()

    # Thumbnails and health load after the list; give them a moment.
    window.health_shown.connect(lambda _: QTimer.singleShot(1500, library_ready))
    window.show()
    code = app.exec()
    shutil.rmtree(scratch, ignore_errors=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
