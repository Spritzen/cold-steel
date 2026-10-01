from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from cold_steel.core.library import Scanner
from cold_steel.ui.main_window import MainWindow
from cold_steel.ui.mod_table import MOD_ROLE, Column, Membership
from conftest import SampleInstall


def test_window_opens_and_closes(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)

    window.show()
    qtbot.waitExposed(window)
    assert window.isVisible()
    assert window.windowTitle() == "Cold Steel"

    window.close()
    assert not window.isVisible()


@pytest.fixture
def window(qtbot: QtBot, sample_install: SampleInstall, tmp_path: Path) -> MainWindow:
    win = MainWindow(Scanner(*sample_install.scanner_args()), tmp_path / "thumbnails")
    qtbot.addWidget(win)
    with qtbot.waitSignal(win.library_shown, timeout=10_000):
        win.show()
    return win


def shown(window: MainWindow) -> list[str]:
    """The names in the table, top to bottom."""
    table = window.filter
    return [table.index(row, Column.NAME).data(MOD_ROLE).name for row in range(table.rowCount())]


def test_lists_mods_and_game_version(window: MainWindow) -> None:
    assert shown(window) == [
        "Alpha Interface",
        "Beta Ships",
        "broken",
        "Gamma Soundtrack",
        "My Local Tweaks",
    ]
    assert window.game_label.text() == "Stellaris Cygnus v4.5.1 (358e)"
    assert window.statusBar().currentMessage() == "5 mods, 2 outdated"


def test_sidebar_shows_playsets_in_launcher_order(window: MainWindow) -> None:
    items = [window.playset_list.item(i).text() for i in range(window.playset_list.count())]
    assert items == ["All mods (5)", "Main Playset (4)", "Second Playset (1)"]
    assert window.playset_list.item(1).font().bold()  # the active one


def test_choosing_a_playset_shows_its_load_order(window: MainWindow) -> None:
    window.playset_list.setCurrentRow(1)
    assert shown(window) == [
        "Alpha Interface",
        "Gamma Soundtrack",
        "My Local Tweaks",
        "Unsubscribed Mod",
    ]
    assert not window.table.isColumnHidden(Column.POSITION)

    window.playset_list.setCurrentRow(0)
    assert len(shown(window)) == 5
    assert window.table.isColumnHidden(Column.POSITION)


def test_search_and_filters(window: MainWindow) -> None:
    window.search.setText("gam")
    assert shown(window) == ["Gamma Soundtrack"]
    window.search.clear()

    window.outdated_box.setChecked(True)
    assert shown(window) == ["Beta Ships", "My Local Tweaks"]
    window.outdated_box.setChecked(False)

    window.tag_box.setCurrentIndex(window.tag_box.findData("Sound"))
    assert shown(window) == ["Gamma Soundtrack"]
    window.tag_box.setCurrentIndex(0)

    window.membership_box.setCurrentIndex(window.membership_box.findData(Membership.IN_NO_PLAYSET))
    assert shown(window) == ["Beta Ships", "broken"]


def test_outdated_mods_are_shown_in_red(window: MainWindow) -> None:
    from PySide6.QtCore import Qt

    rows = {window.filter.index(r, Column.NAME).data(): r for r in range(window.filter.rowCount())}
    beta = window.filter.index(rows["Beta Ships"], Column.SUPPORTED)
    alpha = window.filter.index(rows["Alpha Interface"], Column.SUPPORTED)
    assert beta.data(Qt.ItemDataRole.ForegroundRole) is not None
    assert "The game is v4.5.1" in beta.data(Qt.ItemDataRole.ToolTipRole)
    assert alpha.data(Qt.ItemDataRole.ForegroundRole) is None


def test_thumbnails_load_in_the_background(qtbot: QtBot, window: MainWindow) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPixmap

    from cold_steel.ui.thumbnails import SHOWN_SIZE

    def icon(row: int) -> QPixmap:
        pixmap = window.model.index(row, Column.NAME).data(Qt.ItemDataRole.DecorationRole)
        assert isinstance(pixmap, QPixmap)
        return pixmap

    # Row 0 is Alpha Interface, which has a picture; row 1, Beta Ships, has none.
    qtbot.waitUntil(lambda: not icon(0).isNull(), timeout=5000)
    assert icon(0).deviceIndependentSize().toSize() == SHOWN_SIZE
    assert icon(1).deviceIndependentSize().toSize() == SHOWN_SIZE


def test_missing_game_shows_how_to_fix_it(qtbot: QtBot, tmp_path: Path) -> None:
    win = MainWindow(Scanner((tmp_path / "no-steam",), tmp_path / "cache.msgpack"))
    qtbot.addWidget(win)
    win.show()
    qtbot.waitUntil(lambda: win.pages.currentIndex() == 1, timeout=5000)
    assert "Stellaris wasn't found" in win.message.text()
