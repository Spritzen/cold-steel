"""Takes the README's screenshots from your real install, light and dark.

    make screenshots
    make screenshots PLAYSET="Cold Steel Mix"

It shows the playset you last played, or the one PLAYSET names. The Saves
window shows the playset with the most saves, and the Empires window the one
with the most empires in its list; both are left out while the game's cloud
autosaves are on. Run it on the host for the desktop's own style; in the
container it draws off screen with Qt's plain style. Your playsets, save
bindings and empire lists are read from copies, so nothing of yours is changed.
Pictures go in screenshots/.
"""

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QWidget

from cold_steel.core.empires import list_owner
from cold_steel.core.library import Scanner
from cold_steel.core.saves import bound_saves
from cold_steel.paradox.save import autosaves_to_cloud
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
    for name in ("playsets.json", "saves.json", "empires.json", "empires_in_game.json"):
        if (mine := paths.data_dir() / name).exists():
            shutil.copy(mine, scratch / name)
    if (lists := paths.data_dir() / "empires").is_dir():
        shutil.copytree(lists, scratch / "empires")

    app = QApplication(sys.argv[:1])
    app.setApplicationName("Cold Steel")
    settings = load_settings()
    window = MainWindow(
        Scanner.from_settings(settings),
        playsets_path=scratch / "playsets.json",
        backup_dir=scratch / "backups",
        saves_path=scratch / "saves.json",
        hidden_path=scratch / "hidden.json",
        empire_lists_dir=scratch / "empires",
        empires_state_path=scratch / "empires_in_game.json",
        settings=settings,
    )

    def save(widget: QWidget, name: str) -> None:
        app.processEvents()
        widget.grab().save(str(OUT / f"{name}.png"))
        print(OUT / f"{name}.png")

    def select(playset_id: str | None) -> bool:
        for row in range(window.playset_list.count()):
            if window.playset_list.item(row).data(Qt.ItemDataRole.UserRole) == playset_id:
                window.playset_list.setCurrentRow(row)
                return True
        return False

    def select_playset() -> None:
        book = window.book
        named = [p.id for p in book.playsets if p.name == wanted] if book and wanted else []
        if wanted and not named:
            print(f"No playset is called {wanted!r}. Showing the one you last played.")
        if not select(named[0] if named else book.active if book else None):
            window.playset_list.setCurrentRow(min(1, window.playset_list.count() - 1))

    def take(theme: Theme) -> None:
        apply_theme(app, theme)
        save(window, f"main-{theme}")
        # Built as the app builds it, with the game's cloud autosave setting.
        library = window.library
        data_dir = library.game.data_dir if library else None
        found = None if settings.game_data_dir else data_dir
        cloud = autosaves_to_cloud(data_dir) if data_dir else None
        settings_dialog = SettingsDialog(
            settings, found, window, game_found=library is not None, cloud_autosaves=cloud
        )
        for name, dialog in (
            ("settings", settings_dialog),
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
        book, bindings, saves = window.book, window.bindings, window.saves
        if not (window.saves_on and book and bindings and saves):
            print("No saves to show (cloud autosaves on, or none found): skipped the Saves window.")
            take_empires()
            return
        most = max(book.playsets, key=lambda p: len(bound_saves(saves, bindings, p.id)))
        select(most.id)
        window.saves_found.connect(lambda _: QTimer.singleShot(500, saves_ready))
        window.show_saves()

    def saves_ready() -> None:
        dialog = window._saves_dialog
        if dialog is not None:
            # Open the newest save to show its files.
            if (first := dialog.bound.topLevelItem(0)) is not None:
                first.setExpanded(True)
            for theme in ("light", "dark"):
                apply_theme(app, theme)
                save(dialog, f"saves-{theme}")
            dialog.close()
        take_empires()

    def take_empires() -> None:
        book = window.book
        if not (window.saves_on and book):
            print("Cloud autosaves are on: skipped the Empires window.")
            app.quit()
            return
        window._sync_empires()  # the first lists, in the copies
        count = {p.id: len(window._read_list(list_owner(p))) for p in book.playsets}
        if max(count.values(), default=0):
            select(max(count, key=lambda pid: count[pid]))
        else:  # no lists yet: show the playset above, with the empires to import below
            select_playset()
        shown = window.selected_playset()
        others = {pid: n for pid, n in count.items() if shown is None or pid != list_owner(shown)}
        if not window._read_list("loose") and max(others.values(), default=0):
            # Nothing to import from Not in any playset: show another playset's list there.
            window._empire_source = max(others, key=lambda pid: others[pid])
        window.empires_checked.connect(lambda _: QTimer.singleShot(500, empires_ready))
        window.show_empires()

    def empires_ready() -> None:
        dialog = window._empires_dialog
        if dialog is not None:
            for theme in ("light", "dark"):
                apply_theme(app, theme)
                save(dialog, f"empires-{theme}")
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
