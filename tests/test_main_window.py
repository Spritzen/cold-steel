from collections.abc import Callable
from pathlib import Path

import pytest
from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtGui import QAction
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMenu, QTreeWidget, QTreeWidgetItem
from pytestqt.qtbot import QtBot

from cold_steel.core import playsets as ops
from cold_steel.core.build import BuildError, BuildRecord
from cold_steel.core.jobs import JobContext
from cold_steel.core.library import Scanner
from cold_steel.core.play import PlayPlan
from cold_steel.core.saves import Save
from cold_steel.paradox.continue_game import ContinueGame
from cold_steel.store.playsets import Playset
from cold_steel.store.settings import Settings
from cold_steel.ui import main_window as main_window_module
from cold_steel.ui.conflicts_window import ConflictsWindow
from cold_steel.ui.help import shortcut_rows
from cold_steel.ui.main_window import MainWindow
from cold_steel.ui.mod_table import ALL, MOD_ROLE, NO_PLAYSET, Column
from cold_steel.ui.rebuild_dialog import RebuildChoice
from cold_steel.ui.saves_dialog import FOLDER_ROLE, SavesDialog
from conftest import SampleInstall, make_save


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
    win = MainWindow(
        Scanner(*sample_install.scanner_args()),
        tmp_path / "thumbnails",
        playsets_path=tmp_path / "data/playsets.json",
        backup_dir=tmp_path / "data/backups",
    )
    qtbot.addWidget(win)
    # The health check starts once the mods are on screen.
    with qtbot.waitSignals([win.library_shown, win.health_shown], timeout=10_000):
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
    assert window.statusBar().currentMessage() == "5 mods, 2 outdated, 1 with broken files"


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

    window.membership_box.setCurrentIndex(window.membership_box.findData(NO_PLAYSET))
    assert shown(window) == ["Beta Ships", "broken"]


def test_include_or_exclude_a_playset(window: MainWindow) -> None:
    include, playsets = window.include_box, window.membership_box
    assert not include.isEnabled()  # "All" filters nothing
    playsets.setCurrentIndex(playsets.findText("Main Playset"))
    assert include.isEnabled()
    assert include.currentText() == "Exclude"  # the usual choice
    assert shown(window) == ["Beta Ships", "broken"]
    include.setCurrentIndex(include.findText("Include"))
    assert shown(window) == ["Alpha Interface", "Gamma Soundtrack", "My Local Tweaks"]
    # Moving to another playset keeps Include.
    playsets.setCurrentIndex(playsets.findText("Second Playset"))
    assert include.currentText() == "Include"

    # No playset can only be included: choosing it resets Exclude.
    playsets.setCurrentIndex(playsets.findData(NO_PLAYSET))
    assert not include.isEnabled()
    assert include.currentText() == "Include"
    assert shown(window) == ["Beta Ships", "broken"]

    # Renaming a playset keeps it chosen, under its new name.
    window.playset_list.setCurrentRow(1)
    playsets.setCurrentIndex(playsets.findText("Main Playset"))
    assert include.currentText() == "Exclude"
    assert window.book is not None
    window._playset_changed(window.book.rename(playsets.currentData(), "Renamed"))
    assert playsets.currentText() == "Renamed"
    assert include.currentText() == "Exclude"

    # Picking another entry in the sidebar resets the filter to Include All.
    window.playset_list.setCurrentRow(0)
    assert playsets.currentData() == ALL
    assert include.currentText() == "Include"
    assert not include.isEnabled()
    playsets.setCurrentIndex(playsets.findData(NO_PLAYSET))
    window.playset_list.setCurrentRow(1)
    assert playsets.currentData() == ALL


def test_a_dev_copy_and_its_release_are_marked(
    window: MainWindow, sample_install: SampleInstall, qtbot: QtBot
) -> None:
    outer = sample_install.root / "home/.local/share/Paradox Interactive/Stellaris/mod/my_local.mod"
    with outer.open("a") as file:
        file.write('remote_file_id="2000000002"\n')
    with qtbot.waitSignal(window.library_shown, timeout=10_000):
        window.rescan()

    rows = {window.filter.index(r, Column.NAME).data(): r for r in range(window.filter.rowCount())}
    local = window.filter.index(rows["My Local Tweaks"], Column.NAME)
    release = window.filter.index(rows["Beta Ships"], Column.NAME)
    alpha = window.filter.index(rows["Alpha Interface"], Column.NAME)
    assert local.siblingAtColumn(Column.SOURCE).data() == "Local (dev copy)"
    assert release.siblingAtColumn(Column.SOURCE).data() == "Workshop (your release)"
    assert alpha.siblingAtColumn(Column.SOURCE).data() == "Workshop"
    assert "Its Workshop release is \u201cBeta Ships\u201d." in local.data(
        Qt.ItemDataRole.ToolTipRole
    )
    assert "Its dev copy is the local mod \u201cMy Local Tweaks\u201d." in release.data(
        Qt.ItemDataRole.ToolTipRole
    )


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


# Playsets


def sidebar(window: MainWindow) -> list[str]:
    return [window.playset_list.item(i).text() for i in range(window.playset_list.count())]


def name_index(window: MainWindow, name: str) -> QModelIndex:
    for row in range(window.filter.rowCount()):
        index = window.filter.index(row, Column.NAME)
        if index.data(MOD_ROLE).name == name:
            return index
    raise AssertionError(f"{name} isn't shown")


def test_new_rename_copy_delete(window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    answers = iter(["Fresh", "Renamed", "Renamed copy"])
    monkeypatch.setattr(window, "ask_text", lambda *args: next(answers))
    monkeypatch.setattr(window, "confirm", lambda *args: True)

    window.new_action.trigger()
    assert sidebar(window)[-1] == "Fresh (0)"
    assert window.selected_playset() is not None
    window.rename_action.trigger()
    window.copy_action.trigger()
    assert sidebar(window)[-2:] == ["Renamed (0)", "Renamed copy (0)"]

    window.delete_action.trigger()
    assert sidebar(window) == [
        "All mods (5)",
        "Main Playset (4)",
        "Second Playset (1)",
        "Renamed (0)",
    ]
    assert window.playset_list.currentRow() == 0


def test_add_mods_from_the_full_list(window: MainWindow) -> None:
    assert window.book is not None
    second = window.book.playsets[1]
    window.add_to_playset(second.id, ["workshop:2000000002"])
    assert sidebar(window)[2] == "Second Playset (2)"

    window.playset_list.setCurrentRow(2)
    assert shown(window) == ["Gamma Soundtrack", "Beta Ships"]


def test_checkbox_turns_a_mod_on_and_off(window: MainWindow) -> None:
    from PySide6.QtCore import Qt

    window.playset_list.setCurrentRow(1)
    gamma = name_index(window, "Gamma Soundtrack")
    assert gamma.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Unchecked
    window.filter.setData(gamma, Qt.CheckState.Checked.value, Qt.ItemDataRole.CheckStateRole)

    assert window.book is not None
    assert window.book.playsets[0].entries[1].enabled
    gamma = name_index(window, "Gamma Soundtrack")
    assert gamma.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked


def test_dragging_sets_the_load_order(window: MainWindow) -> None:
    from PySide6.QtCore import Qt

    window.playset_list.setCurrentRow(1)
    local = name_index(window, "My Local Tweaks")
    data = window.filter.mimeData([local])
    # Dropped on the first row: it goes before Alpha Interface.
    window.filter.dropMimeData(data, Qt.DropAction.MoveAction, 0, 0, QModelIndex())
    assert shown(window) == [
        "My Local Tweaks",
        "Alpha Interface",
        "Gamma Soundtrack",
        "Unsubscribed Mod",
    ]
    # Dropped below the last row: the end.
    data = window.filter.mimeData([name_index(window, "My Local Tweaks")])
    window.filter.dropMimeData(data, Qt.DropAction.MoveAction, -1, -1, QModelIndex())
    assert shown(window)[-1] == "My Local Tweaks"


def test_missing_mods_are_shown_clearly(window: MainWindow) -> None:
    from PySide6.QtCore import Qt

    window.playset_list.setCurrentRow(1)
    assert window.missing_label.isVisibleTo(window)
    assert "Unsubscribed Mod" in window.missing_label.text()
    gone = name_index(window, "Unsubscribed Mod")
    assert gone.siblingAtColumn(Column.SOURCE).data() == "Not installed"
    assert gone.data(Qt.ItemDataRole.FontRole).italic()

    window.playset_list.setCurrentRow(2)
    assert not window.missing_label.isVisibleTo(window)


def test_sort_and_dlc(window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    window.playset_list.setCurrentRow(1)
    monkeypatch.setattr(window, "ask_dlc", lambda playset: ("dlc032_machine_age",))
    window.dlc_action.trigger()
    selected = window.selected_playset()
    assert selected is not None and selected.disabled_dlcs == ("dlc032_machine_age",)

    window.sort_action.trigger()  # no rule matches the sample mods: the order stays
    assert shown(window)[0] == "Alpha Interface"


def test_play_asks_about_missing_mods_then_launches(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    launched: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        window, "launch", lambda plan, game, backups: launched.append(plan.load.enabled_mods)
    )
    window.playset_list.setCurrentRow(2)  # Second Playset: not the active one

    monkeypatch.setattr(window, "confirm", lambda *args: False)
    window.playset_list.setCurrentRow(1)
    window.play_action.trigger()
    assert launched == []  # Main Playset has a missing mod, and we said no

    window.playset_list.setCurrentRow(2)
    window.play_action.trigger()
    assert launched == [("mod/ugc_2000000003.mod",)]
    assert window.playset_list.item(2).font().bold()
    assert not window.playset_list.item(1).font().bold()
    assert "starting with Second Playset" in window.statusBar().currentMessage()


def test_a_refused_play_is_explained(window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    from cold_steel.core.play import PlayError

    def refuse(*args: object) -> None:
        raise PlayError("Steam isn't running.")

    told: list[str] = []
    monkeypatch.setattr(window, "launch", refuse)
    monkeypatch.setattr(window, "tell", lambda title, text: told.append(text))
    window.playset_list.setCurrentRow(2)
    window.play_action.trigger()
    assert told == ["Steam isn't running."]
    assert window.playset_list.item(1).font().bold()  # still the launcher's active one


def test_open_in_launcher_exports_as_active_then_starts_it(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cold_steel.paradox import processes
    from cold_steel.paradox.launcher_db import read_launcher

    assert window.library is not None
    game = window.library.game
    started: list[object] = []
    told: list[str] = []
    monkeypatch.setattr(window, "start_launcher", started.append)
    monkeypatch.setattr(window, "tell", lambda title, text: told.append(text))

    monkeypatch.setattr(processes, "running", lambda names: names == processes.LAUNCHER)
    window.playset_list.setCurrentRow(2)
    window.open_launcher_action.trigger()
    assert started == [] and "already open" in told[0]

    monkeypatch.setattr(processes, "running", lambda names: names == processes.STEAM)
    window.open_launcher_action.trigger()
    assert started == [game]
    active = [p.name for p in read_launcher(game.launcher_db).playsets if p.active]
    assert active == ["Second Playset"]


def test_sync_launcher_asks_first(window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    from cold_steel.paradox.launcher_db import read_launcher

    assert window.library is not None
    db = window.library.game.launcher_db
    asked: list[str] = []
    told: list[str] = []
    monkeypatch.setattr(window, "tell", lambda title, text: told.append(text))
    assert window.book is not None
    window.book.delete(window.book.playsets[1].id)  # Second Playset

    def refuse(title: str, text: str) -> bool:
        asked.append(text)
        return False

    monkeypatch.setattr(window, "confirm", refuse)
    window.sync_action.trigger()
    assert "replaces all playsets in the Paradox launcher" in asked[0]
    assert told == []
    assert len(read_launcher(db).playsets) == 2  # said no: nothing changed

    monkeypatch.setattr(window, "confirm", lambda title, text: True)
    window.sync_action.trigger()
    assert [p.name for p in read_launcher(db).playsets] == ["Main Playset"]
    assert "1 other playset was removed" in told[0]


def test_a_drifted_launcher_copy_is_shown_until_exported(
    qtbot: QtBot, window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(window, "tell", lambda title, text: None)
    window.playset_list.setCurrentRow(1)  # Main Playset, as imported
    assert window.launcher_label.isHidden()

    window._edit(lambda p: ops.remove_mods(p, ["workshop:2000000003"]))
    assert "also has Gamma Soundtrack" in window.launcher_label.text()
    assert not window.launcher_label.isHidden()

    # Export rescans, which reads the launcher's copy again.
    with qtbot.waitSignal(window.library_shown, timeout=10_000):
        window.export_action.trigger()
    assert window.launcher_label.isHidden()


def test_workshop_ids_for_the_steam_page(window: MainWindow) -> None:
    assert window._workshop_id("workshop:2000000001") == "2000000001"
    assert window._workshop_id("workshop:2000000001@abc") == "2000000001"
    assert window._workshop_id("local:my_local") == ""


def test_save_and_load_a_file(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "shared.json"
    monkeypatch.setattr(window, "ask_save_path", lambda name: path)
    monkeypatch.setattr(window, "ask_open_path", lambda: path)
    monkeypatch.setattr(window, "tell", lambda *args: None)

    window.playset_list.setCurrentRow(1)
    window.save_file_action.trigger()
    window.load_file_action.trigger()
    # The local mod can't be shared; the rest comes back, under a new name.
    assert sidebar(window)[-1] == "Main Playset (2) (3)"


def test_import_from_launcher(window: MainWindow) -> None:
    assert window.library is not None
    window.import_from_launcher(window.library.launcher_playsets[1])
    assert sidebar(window)[-1] == "Second Playset (2) (1)"


# Health and errors


def health_badges(window: MainWindow) -> dict[str, str]:
    table = window.filter
    return {
        table.index(r, Column.NAME).data(): table.index(r, Column.HEALTH).data()
        for r in range(table.rowCount())
    }


def test_each_mod_shows_a_health_badge(window: MainWindow) -> None:
    assert health_badges(window) == {
        "Alpha Interface": "OK",
        "Beta Ships": "2 warnings",  # "3.*": unreadable, and outdated
        "broken": "1 error",  # its descriptor is broken
        "Gamma Soundtrack": "1 warning",  # "v4.*.*"
        "My Local Tweaks": "1 warning",  # outdated
    }

    window.problems_box.setChecked(True)
    assert "Alpha Interface" not in shown(window)
    assert len(shown(window)) == 4


def test_clicking_a_badge_lists_the_problems(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        window,
        "show_health",
        lambda mod, issues: opened.append((mod.name, [i.text for i in issues])),
    )
    beta = name_index(window, "Beta Ships").siblingAtColumn(Column.HEALTH)
    window.table.clicked.emit(beta)
    [(name, texts)] = opened
    assert name == "Beta Ships"
    assert 'supported_version "3.*"' in texts[0]

    # An OK mod has nothing to show.
    window.table.clicked.emit(name_index(window, "Alpha Interface").siblingAtColumn(Column.HEALTH))
    assert len(opened) == 1


def test_the_health_dialog_groups_problems_by_file(window: MainWindow) -> None:
    from cold_steel.core.health import Issue
    from cold_steel.ui.health_dialog import HealthDialog

    mod = name_index(window, "Alpha Interface").data(MOD_ROLE)
    issues = (
        Issue("error", "No BOM", "localisation/a_l_english.yml"),
        Issue("error", "Bad line", "localisation/a_l_english.yml", 3, "X Y"),
        Issue("warning", "Outdated", "ugc_2000000001.mod"),
    )
    dialog = HealthDialog(mod, issues, window)
    tree = dialog.tree
    assert tree.topLevelItemCount() == 2
    loc = tree.topLevelItem(0)
    assert loc is not None and loc.childCount() == 2
    bad_line = loc.child(1)
    assert bad_line is not None and bad_line.text(1) == "3"


def test_errors_view_groups_the_log_by_mod(
    qtbot: QtBot, window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cold_steel.core.errors import GAME

    assert window.library is not None
    logs = window.library.game.data_dir / "logs"
    logs.mkdir()
    (logs / "error.log").write_text(
        "[10:00:00][a.cpp:1]: Bad  file: common/alpha.txt line: 1\n"
        "[10:00:01][b.cpp:2]: Something else\n",
        "utf-8",
    )
    (window.library.game.data_dir / "dlc_load.json").unlink(missing_ok=True)
    shown_dialogs: list[object] = []
    monkeypatch.setattr(window, "show_dialog", shown_dialogs.append)

    with qtbot.waitSignal(window.errors_read, timeout=5000) as read:
        window.errors_action.trigger()
    # With no dlc_load.json, no mod was loaded: both are the game's, or unknown.
    assert [g.key for g in read.args[0].groups] == [GAME]
    assert shown_dialogs and shown_dialogs[0] is window._errors_dialog
    assert window._errors_dialog is not None
    assert window._errors_dialog.tree.topLevelItemCount() == 1
    assert window.errors_button.text() == "Errors (2)"


def test_overrides_are_folded_away_until_asked_for(
    qtbot: QtBot, window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert window.library is not None
    logs = window.library.game.data_dir / "logs"
    logs.mkdir()
    (logs / "error.log").write_text(
        "[10:00:00][a.cpp:1]: Something broke\n"
        "[10:00:01][b.cpp:2]: Object with key: tech_x already exists, using the one at  "
        "file: common/technology/x.txt line: 1\n"
        "[10:00:02][c.cpp:3]: Duplicate of x_entity added to entity system\n",
        "utf-8",
    )
    monkeypatch.setattr(window, "show_dialog", lambda dialog: None)
    with qtbot.waitSignal(window.errors_read, timeout=5000):
        window.errors_action.trigger()
    assert window.errors_button.text() == "Errors (1)"

    dialog = window._errors_dialog
    assert dialog is not None
    assert dialog.overrides_box.isVisibleTo(dialog)
    assert "also has 2 overrides" in dialog.summary.text()
    unknown = dialog.tree.topLevelItem(0)
    assert unknown is not None and (unknown.text(1), unknown.childCount()) == ("1", 1)

    dialog.overrides_box.setChecked(True)
    unknown = dialog.tree.topLevelItem(0)
    assert unknown is not None and (unknown.text(1), unknown.childCount()) == ("3", 3)


def test_errors_are_read_when_the_game_closes(
    qtbot: QtBot, window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Game:
        exit_code: int | None = None

        def poll(self) -> int | None:
            return self.exit_code

    game = Game()
    monkeypatch.setattr(window, "launch", lambda *args: game)
    window.playset_list.setCurrentRow(2)
    window.play_action.trigger()
    assert window._game_timer.isActive()

    window._check_game()  # still running
    assert window._game_timer.isActive()

    game.exit_code = 0
    with qtbot.waitSignal(window.errors_read, timeout=5000):
        window._check_game()
    assert not window._game_timer.isActive()
    assert window.statusBar().currentMessage() == "Stellaris closed. Its error log is empty."


# Conflicts


@pytest.fixture
def clashing(qtbot: QtBot, window: MainWindow, sample_install: SampleInstall) -> MainWindow:
    """Alpha and My Local clash on a technology, a whole file and one identical object.

    The Main Playset loads Alpha, then My Local.
    """
    alpha = sample_install.workshop_dir / "2000000001"
    local = sample_install.data_dir / "mod/my_local"
    files = {
        alpha: {
            "common/technology/b_alpha.txt": b"tech_x = {\n\tcost = 1\n}\nonly_alpha = { }\n",
            "common/static_modifiers/a.txt": b"mod_same = { x = 1 }\n",
            "interface/same.gui": b"guiTypes = { }\n",
        },
        local: {
            "common/technology/a_local.txt": b"tech_x = {\n\tcost = 2\n}\n",
            "common/static_modifiers/b.txt": b"mod_same = {\n\tx = 1\n}\n",
            "interface/same.gui": b"guiTypes = { } # mine\n",
        },
    }
    for root, contents in files.items():
        for name, data in contents.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_bytes(data)
    with qtbot.waitSignal(window.library_shown, timeout=10_000):
        window.rescan()
    window.playset_list.setCurrentRow(1)
    assert window.selected_playset() is not None
    assert window.selected_playset().name == "Main Playset"  # type: ignore[union-attr]
    return window


def test_old_copies_are_named_above_the_mod_list_and_in_conflicts(
    qtbot: QtBot, window: MainWindow, sample_install: SampleInstall
) -> None:
    # My Local is made for v4.4.6 and loads after Alpha (v4.5.*), replacing
    # Alpha's file with one that lacks alpha_b.
    name = "common/scripted_triggers/alpha.txt"
    alpha = sample_install.workshop_dir / "2000000001" / name
    local = sample_install.data_dir / "mod/my_local" / name
    for path, data in (
        (alpha, b"alpha_a = { always = yes }\nalpha_b = { always = yes }\n"),
        (local, b"alpha_a = { always = no }\n"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    with qtbot.waitSignal(window.library_shown, timeout=20_000):
        window.rescan()
    with qtbot.waitSignal(window.old_copies_shown, timeout=20_000) as checked:
        window.playset_list.setCurrentRow(1)  # Main Playset

    [copy] = checked.args[0]
    assert copy.missing == (("common/scripted_triggers", "alpha_b"),)
    assert window.old_label.isVisibleTo(window)
    assert window.old_label.text().startswith(
        "My Local Tweaks, made for v4.4.6, replaces 1 file(s) of Alpha Interface (v4.5.*)"
    )

    conflicts = open_conflicts(qtbot, window)
    assert conflicts.old_row.isVisibleTo(conflicts)
    from cold_steel.ui.conflicts_window import old_copies_details

    details = old_copies_details(conflicts.old_copies, conflicts.names)
    assert name in details
    assert "Scripted triggers: alpha_b" in details

    # A playset without them hides the line.
    with qtbot.waitSignal(window.old_copies_shown, timeout=20_000):
        window.playset_list.setCurrentRow(2)
    assert not window.old_label.isVisibleTo(window)


def open_conflicts(qtbot: QtBot, window: MainWindow, mod: str | None = None) -> ConflictsWindow:
    with qtbot.waitSignal(window.conflicts_shown, timeout=20_000):
        window.show_conflicts(mod)
    assert window._conflicts_window is not None
    return window._conflicts_window


def groups(conflicts: ConflictsWindow) -> dict[str, list[tuple[str, str]]]:
    """Each group's label, with each row's name and winner."""
    tree = conflicts.tree
    result: dict[str, list[tuple[str, str]]] = {}
    for n in range(tree.topLevelItemCount()):
        group = tree.topLevelItem(n)
        assert group is not None
        rows = [group.child(r) for r in range(group.childCount())]
        result[group.text(0)] = [(r.text(0), r.text(1)) for r in rows if r is not None]
    return result


def test_conflicts_are_listed_by_type_with_the_winner(qtbot: QtBot, clashing: MainWindow) -> None:
    conflicts = open_conflicts(qtbot, clashing)

    # a_local.txt sorts before b_alpha.txt, so Alpha wins though it loads first.
    # The identical static modifier is hidden until asked for.
    assert groups(conflicts) == {
        "Technology (1)": [("tech_x", "Alpha Interface")],
        "Whole files (1)": [("interface/same.gui", "My Local Tweaks")],
    }
    assert conflicts.windowTitle() == "Conflicts in Main Playset"
    assert "3 conflicts between mods" in conflicts.summary.text()  # one is identical

    conflicts.identical_box.setChecked(True)
    assert "Static modifiers (1)" in groups(conflicts)

    conflicts.type_box.setCurrentIndex(conflicts.type_box.findText("Technology"))
    assert list(groups(conflicts)) == ["Technology (1)"]

    conflicts.type_box.setCurrentIndex(0)
    conflicts.group_box.setCurrentIndex(1)  # by mod
    assert list(groups(conflicts)) == ["Alpha Interface (3)", "My Local Tweaks (3)"]


def test_a_conflict_is_shown_side_by_side(qtbot: QtBot, clashing: MainWindow) -> None:
    conflicts = open_conflicts(qtbot, clashing)
    conflicts.tree.expandAll()
    tech = conflicts.tree.findItems("tech_x", Qt.MatchFlag.MatchRecursive)[0]

    with qtbot.waitSignal(conflicts.compared, timeout=5000) as compared:
        conflicts.tree.setCurrentItem(tech)
    pair = compared.args[0]
    # The winner on the right, the version it beat on the left.
    assert "cost = 2" in pair.left.text
    assert "cost = 1" in pair.right.text
    assert pair.left.changed == pair.right.changed == frozenset({1})
    assert "Alpha Interface</b> wins" in conflicts.reason.text()
    assert "b_alpha.txt sorts last" in conflicts.reason.text()
    assert "checked in game on 2026-10-01" in conflicts.rule.text()
    assert conflicts.right_box.currentText().startswith("★ Alpha Interface")
    assert conflicts.viewer.right.toPlainText() == pair.right.text


def test_search_finds_any_object_in_the_playset(qtbot: QtBot, clashing: MainWindow) -> None:
    conflicts = open_conflicts(qtbot, clashing)
    with qtbot.waitSignal(conflicts.searched, timeout=5000):
        conflicts.search.setText("ONLY_")

    assert groups(conflicts) == {"Technology (1)": [("only_alpha", "Alpha Interface")]}
    group = conflicts.tree.topLevelItem(0)
    item = group.child(0) if group else None
    assert item is not None
    conflicts.tree.setCurrentItem(item)
    assert conflicts.reason.text() == "Only one version."


def test_conflicts_for_one_mod_and_after_a_change(qtbot: QtBot, clashing: MainWindow) -> None:
    conflicts = open_conflicts(qtbot, clashing, "local:my_local")
    assert conflicts.mod_box.currentData() == "local:my_local"
    assert "Whole files (1)" in groups(conflicts)

    # Moving My Local first changes who wins the whole file, but not the
    # technology: file names decide that.
    with qtbot.waitSignal(clashing.conflicts_shown, timeout=20_000):
        clashing._edit(lambda p: ops.move_mods(p, ["local:my_local"], "workshop:2000000001"))
    assert groups(conflicts) == {
        "Technology (1)": [("tech_x", "Alpha Interface")],
        "Whole files (1)": [("interface/same.gui", "Alpha Interface")],
    }


# Resolving conflicts


def select(conflicts: ConflictsWindow, key: str) -> None:
    conflicts.tree.expandAll()
    found = conflicts.tree.findItems(key, Qt.MatchFlag.MatchRecursive)
    assert found, groups(conflicts)
    conflicts.tree.setCurrentItem(found[0])


def test_choose_a_winner_and_generate_the_patch(
    qtbot: QtBot, clashing: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    told: list[str] = []
    monkeypatch.setattr(clashing, "tell", lambda title, text: told.append(text))
    conflicts = open_conflicts(qtbot, clashing)
    select(conflicts, "tech_x")
    # The winner is on the right: My Local's cost = 2 is on the left.
    conflicts.use_left.click()
    assert "Your choice:</b> My Local Tweaks" in conflicts.choice.text()
    assert groups(conflicts)["Technology (1)"] == [("tech_x", "✓ My Local Tweaks")]
    assert "not in the game yet" in conflicts.patch_label.text()

    # Generating rescans, which finds the conflicts again.
    with (
        qtbot.waitSignal(clashing.conflicts_shown, timeout=20_000),
        qtbot.waitSignal(clashing.patch_generated, timeout=20_000) as generated,
    ):
        conflicts.generate_button.click()
    plan = generated.args[0]
    assert len(plan.written) == 1 and plan.left_out == ()
    assert "holds 1 choice" in told[0]

    # The patch is last in the playset, and the scan found it.
    playset = clashing.selected_playset()
    assert playset is not None
    assert playset.entries[-1].name == "Cold Steel patch: Main Playset"
    assert clashing.library is not None
    assert playset.entries[-1].key in {m.key for m in clashing.library.mods}
    # The window still shows the clash underneath, with the choice.
    assert groups(conflicts)["Technology (1)"] == [("tech_x", "✓ My Local Tweaks")]
    assert "up to date with your 1 choice" in conflicts.patch_label.text()


def test_delete_the_patch_mod(
    qtbot: QtBot, clashing: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(clashing, "tell", lambda title, text: None)
    conflicts = open_conflicts(qtbot, clashing)
    select(conflicts, "tech_x")
    conflicts.use_left.click()
    with (
        qtbot.waitSignal(clashing.conflicts_shown, timeout=20_000),
        qtbot.waitSignal(clashing.patch_generated, timeout=20_000),
    ):
        conflicts.generate_button.click()
    playset = clashing.selected_playset()
    assert playset is not None
    folder = clashing._patch_dir / playset.id
    assert folder.is_dir() and clashing.delete_patch_action.isEnabled()

    asked: list[str] = []
    answer = False

    def confirm(title: str, text: str) -> bool:
        asked.append(text)
        return answer

    monkeypatch.setattr(clashing, "confirm", confirm)
    # Saying no deletes nothing.
    clashing.delete_patch_action.trigger()
    assert "Main Playset" in asked[0] and folder.is_dir()

    answer = True
    with (
        qtbot.waitSignal(clashing.conflicts_shown, timeout=20_000),
        qtbot.waitSignal(clashing.library_shown, timeout=20_000),  # the rescan after
    ):
        clashing.delete_patch_action.trigger()

    # The patch, its link and its place in the playset are gone. The choice stays.
    assert clashing.book is not None and clashing.library is not None
    assert not folder.exists()
    assert not (clashing.library.game.mod_dir / f"cold_steel_patch_{playset.id}").exists()
    playset = clashing.selected_playset()
    assert playset is not None
    assert not any(e.key.startswith("local:cold_steel_patch_") for e in playset.entries)
    assert not any(m.key.startswith("local:cold_steel_patch_") for m in clashing.library.mods)
    assert groups(conflicts)["Technology (1)"] == [("tech_x", "✓ My Local Tweaks")]
    assert "not in the game yet" in conflicts.patch_label.text()
    assert not clashing.delete_patch_action.isEnabled()


def test_ignore_a_conflict_a_type_or_a_mod(qtbot: QtBot, clashing: MainWindow) -> None:
    conflicts = open_conflicts(qtbot, clashing)
    select(conflicts, "tech_x")
    menu = [a.text() for a in conflicts.ignore_button.menu().actions()]
    assert menu == [
        "This conflict",
        "Every conflict in Technology",
        "Every conflict with Alpha Interface",  # in load order
        "Every conflict with My Local Tweaks",
    ]
    conflicts.ignore_button.menu().actions()[0].trigger()
    assert list(groups(conflicts)) == ["Whole files (1)"]

    conflicts.ignored_box.setChecked(True)
    select(conflicts, "tech_x")
    assert "You chose to ignore this conflict." in conflicts.choice.text()
    conflicts.stop_ignoring_button.click()
    conflicts.ignored_box.setChecked(False)
    assert "Technology (1)" in groups(conflicts)

    select(conflicts, "tech_x")
    conflicts.ignore_button.menu().actions()[2].trigger()  # every conflict with Alpha
    assert groups(conflicts) == {}


def test_write_your_own_version(qtbot: QtBot, clashing: MainWindow) -> None:
    conflicts = open_conflicts(qtbot, clashing)
    select(conflicts, "tech_x")
    conflicts.own_button.click()
    assert conflicts.editor_panel.isVisibleTo(conflicts)
    # It starts from the winner, on the right.
    qtbot.waitUntil(lambda: "cost = 1" in conflicts.editor.toPlainText(), timeout=5000)

    conflicts.editor.setPlainText("tech_x = {\n\tcost = 3\n}\n}")
    with qtbot.waitSignal(conflicts.own_checked, timeout=5000) as checked:
        conflicts.save_own()
    assert checked.args[0][0].startswith("Line 4:")
    assert "Not saved" in conflicts.editor_problems.text()
    assert conflicts.choices is not None and not conflicts.choices.resolutions

    conflicts.editor.setPlainText("tech_x = {\n\tcost = 3\n}\n")
    with qtbot.waitSignal(conflicts.own_checked, timeout=5000) as checked:
        conflicts.save_own()
    assert checked.args[0] == []
    assert not conflicts.editor_panel.isVisibleTo(conflicts)
    assert groups(conflicts)["Technology (1)"] == [("tech_x", "✓ Your own version")]


def test_a_changed_mod_needs_another_look(
    qtbot: QtBot, clashing: MainWindow, sample_install: SampleInstall
) -> None:
    conflicts = open_conflicts(qtbot, clashing)
    select(conflicts, "tech_x")
    conflicts.keep.click()
    assert groups(conflicts)["Technology (1)"] == [("tech_x", "✓ Alpha Interface")]

    tech = sample_install.workshop_dir / "2000000001/common/technology/b_alpha.txt"
    tech.write_bytes(b"tech_x = {\n\tcost = 5\n}\n")
    with qtbot.waitSignal(clashing.library_shown, timeout=10_000):
        clashing.rescan()
    with qtbot.waitSignal(clashing.conflicts_shown, timeout=20_000):
        conflicts.refresh_requested.emit()
    assert groups(conflicts)["Technology (1)"] == [("tech_x", "⚠ Needs another look")]
    assert "1 need another look" in conflicts.patch_label.text()

    conflicts.state_box.setCurrentIndex(conflicts.state_box.findText("Needs another look"))
    assert list(groups(conflicts)) == ["Technology (1)"]


def test_clear_one_choice_or_all(
    qtbot: QtBot, clashing: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    conflicts = open_conflicts(qtbot, clashing)
    select(conflicts, "tech_x")
    conflicts.keep.click()
    conflicts.clear_button.click()
    assert groups(conflicts)["Technology (1)"] == [("tech_x", "Alpha Interface")]

    conflicts.keep.click()
    select(conflicts, "interface/same.gui")
    conflicts.use_left.click()
    monkeypatch.setattr(conflicts, "confirm", lambda title, text: "all 2 choices" in text)
    conflicts.clear_all_button.click()
    assert conflicts.choices is not None and conflicts.choices.resolutions == ()
    assert groups(conflicts) == {
        "Technology (1)": [("tech_x", "Alpha Interface")],
        "Whole files (1)": [("interface/same.gui", "My Local Tweaks")],
    }


def test_copying_a_playset_copies_its_choices(
    qtbot: QtBot, clashing: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    conflicts = open_conflicts(qtbot, clashing)
    select(conflicts, "tech_x")
    conflicts.keep.click()
    monkeypatch.setattr(clashing, "ask_text", lambda *args: "Copied")
    clashing.copy_playset()
    copied = clashing.selected_playset()
    assert copied is not None and copied.name == "Copied"
    assert len(clashing.choices_for(copied).resolutions) == 1


def test_rows_use_the_themes_text_colour(qtbot: QtBot, clashing: MainWindow) -> None:
    """Only identical or ignored rows get a colour of their own (grey)."""
    conflicts = open_conflicts(qtbot, clashing)
    select(conflicts, "tech_x")
    conflicts.keep.click()  # relabels the row
    conflicts.clear_button.click()
    item = conflicts.tree.currentItem()
    assert item is not None
    for col in range(3):
        assert item.data(col, Qt.ItemDataRole.ForegroundRole) is None


# Pinning and building


def test_pin_a_playset_then_accept_an_update(
    qtbot: QtBot, window: MainWindow, sample_install: SampleInstall, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(window, "confirm", lambda title, text: True)
    window.playset_list.setCurrentRow(1)  # Main Playset: Alpha, Gamma, My Local, Unsubscribed
    window.show_pins()
    dialog = window._pins_dialog
    assert dialog is not None and dialog.pin_button.isEnabled()
    assert not window.pin_label.isVisible()

    with qtbot.waitSignals([window.pins_changed, window.pins_checked], timeout=20_000):
        dialog.pin_button.click()
    playset = window.selected_playset()
    assert playset is not None
    # The uninstalled mod can't be copied, and local mods aren't pinned.
    assert {p.key for p in playset.pins} == {"workshop:2000000001", "workshop:2000000003"}
    assert window.pin_label.text() == "Pinned: 2 Workshop mod(s) play from saved copies."
    assert not dialog.pin_button.isEnabled()
    # Pinned copies are never listed as mods of their own.
    assert len(shown(window)) == 4

    # Steam updates Alpha: the window says so, and shows which files changed.
    (sample_install.workshop_dir / "2000000001/common/alpha.txt").write_text("changed = yes\n")
    with qtbot.waitSignal(window.pins_checked, timeout=20_000):
        window.rescan()
    assert "1 have an update on Steam: Alpha Interface" in window.pin_label.text()
    alpha = dialog.tree.findItems("Alpha Interface", Qt.MatchFlag.MatchExactly)[0]
    assert alpha.text(2) == "Updated (now 1.2): 1 changed"
    dialog.tree.setCurrentItem(alpha)
    assert [dialog.files.item(n).text() for n in range(dialog.files.count())] == [
        "~ common/alpha.txt"
    ]

    with qtbot.waitSignals([window.pins_changed, window.pins_checked], timeout=20_000):
        dialog.accept_button.click()
    assert "update" not in window.pin_label.text()

    with qtbot.waitSignal(window.library_shown, timeout=20_000):
        dialog.unpin_button.click()
    playset = window.selected_playset()
    assert playset is not None and playset.pins == ()
    assert not window.pin_label.isVisible()
    assert window.snapshots.ids() == []  # no playset uses the copies, so they're gone


def test_build_a_playset_into_one_mod(qtbot: QtBot, clashing: MainWindow) -> None:
    with (
        qtbot.waitSignal(clashing.library_shown, timeout=20_000),
        qtbot.waitSignal(clashing.build_finished, timeout=20_000) as finished,
    ):
        clashing.build_action.trigger()
    record = finished.args[0]
    assert record.mismatches == []
    assert record.names["local:my_local"] == "My Local Tweaks"

    # The report shows where every file came from.
    report = clashing._build_dialog
    assert report is not None and report.isVisible()
    assert "Every clash has the same winner" in report.summary.text()
    assert report.tree.topLevelItemCount() == len(record.files)
    report.search.setText("same.gui")
    item = report.tree.topLevelItem(0)
    assert item is not None
    assert (item.text(0), item.text(1), item.text(2)) == (
        "interface/same.gui",
        "My Local Tweaks",
        "Alpha Interface",
    )

    # A new playset plays just the built mod, which the scan found.
    assert clashing.book is not None and clashing.library is not None
    built = next(p for p in clashing.book.playsets if p.name == "Main Playset (built)")
    assert [e.name for e in built.entries] == ["Cold Steel build: Main Playset"]
    assert built.entries[0].key in {m.key for m in clashing.library.mods}
    assert clashing.report_action.isEnabled()
    assert clashing.delete_build_action.isEnabled()


def test_delete_a_built_mod(
    qtbot: QtBot, clashing: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    with (
        qtbot.waitSignal(clashing.library_shown, timeout=20_000),
        qtbot.waitSignal(clashing.build_finished, timeout=20_000) as finished,
    ):
        clashing.build_action.trigger()
    playset_id = finished.args[0].playset
    folder = clashing._build_dir / playset_id
    assert folder.is_dir()

    asked: list[str] = []

    def confirm(title: str, text: str) -> bool:
        asked.append(text)
        return True

    assert clashing.book is not None and clashing.bindings is not None
    built = next(p for p in clashing.book.playsets if p.name == "Main Playset (built)")
    clashing.bind_saves([UNE], built.id)

    monkeypatch.setattr(clashing, "confirm", confirm)
    with qtbot.waitSignal(clashing.library_shown, timeout=20_000):
        clashing.delete_build_action.trigger()
    assert "Main Playset (built)" in asked[0]
    assert "Its save is unbound" in asked[0]
    assert clashing.bindings.get(UNE) is None

    # The build, its link and the playset that played it are gone; the playset stays.
    assert clashing.book is not None and clashing.library is not None
    assert not folder.exists()
    assert not (clashing._build_dir / f"{playset_id}.json").exists()
    names = [p.name for p in clashing.book.playsets]
    assert "Main Playset" in names and "Main Playset (built)" not in names
    assert not any(m.key.startswith("local:cold_steel_build_") for m in clashing.library.mods)
    assert not clashing.delete_build_action.isEnabled()


def test_delete_a_local_mod(
    qtbot: QtBot,
    window: MainWindow,
    sample_install: SampleInstall,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod_dir = sample_install.data_dir / "mod"
    asked: list[str] = []
    answer = False

    def confirm(title: str, text: str) -> bool:
        asked.append(text)
        return answer

    trashed: list[Path] = []
    bin_ = tmp_path / "trash"
    bin_.mkdir()

    def trash(path: Path) -> bool:
        trashed.append(path)
        path.rename(bin_ / path.name)
        return True

    monkeypatch.setattr(window, "confirm", confirm)
    monkeypatch.setattr(window, "trash", trash)

    # Saying no deletes nothing.
    window.delete_local_mod("local:my_local")
    assert "My Local Tweaks" in asked[0] and str(mod_dir / "my_local") in asked[0]
    assert trashed == []

    answer = True
    with qtbot.waitSignal(window.library_shown, timeout=10_000):
        window.delete_local_mod("local:my_local")
    assert trashed == [mod_dir / "my_local.mod", mod_dir / "my_local"]
    assert window.library is not None
    assert "local:my_local" not in {m.key for m in window.library.mods}

    # It's taken out of its playset too, not left there as missing.
    assert "Main Playset" in asked[1]
    assert window.playset_list.item(1).text() == "Main Playset (3)"
    window.playset_list.setCurrentRow(1)
    assert shown(window) == ["Alpha Interface", "Gamma Soundtrack", "Unsubscribed Mod"]

    # Workshop mods can't be deleted this way.
    window.delete_local_mod("workshop:2000000001")
    assert len(asked) == 2


def test_a_deleted_local_mod_lands_in_the_trash(
    qtbot: QtBot, window: MainWindow, sample_install: SampleInstall, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The real trash: $HOME is the sample install's, so it's the one in there.
    monkeypatch.setattr(window, "confirm", lambda title, text: True)
    with qtbot.waitSignal(window.library_shown, timeout=10_000):
        window.delete_local_mod("local:my_local")
    trash = sample_install.home / ".local/share/Trash/files"
    assert sorted(p.name for p in trash.iterdir()) == ["my_local", "my_local.mod"]
    assert "My Local Tweaks" not in shown(window)


# Settings, shortcuts, help


def test_settings_are_sent_only_when_changed(
    window: MainWindow, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[Settings] = []
    window.settings_chosen.connect(sent.append)

    monkeypatch.setattr(window, "ask_settings", lambda: Settings())
    window.edit_settings()
    assert sent == []

    monkeypatch.setattr(window, "ask_settings", lambda: Settings(theme="dark"))
    window.edit_settings()
    assert sent == [Settings(theme="dark")]
    assert window.settings == Settings(theme="dark")


def test_every_shortcut_is_listed_once(window: MainWindow) -> None:
    rows = shortcut_rows(window.menuBar())
    keys = [key for _, _, key in rows]
    assert len(keys) == len(set(keys)), "two actions share a shortcut"
    assert ("Playset", "Play", "Ctrl+Return") in rows
    # Every action with a shortcut is in a menu, so the list is complete.
    with_shortcut = [a for a in window.findChildren(QAction) if not a.shortcut().isEmpty()]
    assert len(with_shortcut) == len(rows)


def test_ctrl_f_goes_to_the_search_box(window: MainWindow, qtbot: QtBot) -> None:
    window.activateWindow()
    window.table.setFocus()
    QTest.keyClick(window, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
    assert window.search.hasFocus()


def test_tags_is_three_quarters_of_name(window: MainWindow) -> None:
    header = window.table.horizontalHeader()
    for width in (900, 1400):
        window.resize(width, window.height())
        QTest.qWait(10)
        name, tags = header.sectionSize(Column.NAME), header.sectionSize(Column.TAGS)
        assert abs(tags - 0.75 * name) <= 1
    window.playset_list.setCurrentRow(1)  # the # column appears: the two still share
    name, tags = header.sectionSize(Column.NAME), header.sectionSize(Column.TAGS)
    assert abs(tags - 0.75 * name) <= 1


def test_cut_off_text_is_found(window: MainWindow) -> None:
    from PySide6.QtWidgets import QStyleOptionViewItem

    from cold_steel.ui.full_text import fits

    index = name_index(window, "Alpha Interface")
    option = QStyleOptionViewItem()
    window.table.initViewItemOption(option)
    option.rect = window.table.visualRect(index)
    assert fits(window.table, option, index)
    option.rect.setWidth(20)
    assert not fits(window.table, option, index)


def test_hover_text_wraps_at_a_sensible_width(window: MainWindow) -> None:
    from PySide6.QtGui import QFontMetrics

    from cold_steel.ui.full_text import wrap

    font = window.table.font()
    width = 70 * QFontMetrics(font).averageCharWidth()
    text = "word " * 60 + "\n/a/very/long/path/" + "x" * 200
    lines = wrap(font, text, width).split("\n")
    assert len(lines) > 4
    assert all(QFontMetrics(font).horizontalAdvance(line) <= width for line in lines)
    assert "".join(lines).replace(" ", "") == text.replace(" ", "").replace("\n", "")


# Saves

UNE = "unitednationsofearth_-15512622"
ELVES = "divineelvenorder_-1997250795"


def open_saves(qtbot: QtBot, window: MainWindow) -> SavesDialog:
    """Open the Saves window for the selected playset, once the saves are read."""
    with qtbot.waitSignal(window.saves_found, timeout=10_000):
        window.saves_action.trigger()
    dialog = window._saves_dialog
    assert dialog is not None and dialog.isVisible()
    return dialog


def folders(tree: QTreeWidget) -> list[str]:
    items = (tree.topLevelItem(row) for row in range(tree.topLevelItemCount()))
    return [item.data(0, FOLDER_ROLE) for item in items if item is not None]


def row_text(item: QTreeWidgetItem | None) -> list[str]:
    assert item is not None
    return [item.text(col) for col in range(item.columnCount())]


def test_bind_a_save_in_the_saves_window(qtbot: QtBot, window: MainWindow) -> None:
    window.playset_list.setCurrentRow(1)
    dialog = open_saves(qtbot, window)
    assert dialog.windowTitle() == "Saves of Main Playset"
    assert folders(dialog.bound) == []
    assert "No saves belong to this playset" in dialog.summary.text()

    # Every save is unbound to start with, newest first, with its files inside.
    assert folders(dialog.unbound) == [UNE, ELVES]
    une = dialog.unbound.topLevelItem(0)
    assert une is not None
    text = row_text(une)
    assert text[0:2] == ["United Nations of Earth", "2201.01.26"]
    assert text[3:] == ["v4.5.2", "Local and Steam Cloud", ""]  # suggested for none
    assert [row_text(une.child(i))[0::4] for i in range(une.childCount())] == [
        ["2201.01.26", "Local"],
        ["autosave_2201.01.01", "Steam Cloud"],
    ]

    assert not dialog.bind_button.isEnabled()
    une.setSelected(True)
    dialog.bind_button.click()
    assert folders(dialog.bound) == [UNE]
    assert folders(dialog.unbound) == [ELVES]
    assert window.saves_button.text() == "Saves (1)"

    # The window follows the playset chosen in the sidebar.
    window.playset_list.setCurrentRow(2)
    assert dialog.windowTitle() == "Saves of Second Playset"
    assert folders(dialog.bound) == []
    assert window.saves_button.text() == "Saves"


def test_move_and_unbind_a_save(qtbot: QtBot, window: MainWindow) -> None:
    assert window.book is not None and window.bindings is not None
    main, second = window.book.playsets[0], window.book.playsets[1]
    window.playset_list.setCurrentRow(1)
    dialog = open_saves(qtbot, window)
    window.bind_saves([UNE, ELVES], main.id)
    assert folders(dialog.bound) == [UNE, ELVES]

    # Move offers every other playset.
    menu = dialog.menu_for(dialog.bound, (ELVES,))
    move = menu.findChild(QMenu)
    assert move is not None and move.title() == "Move to playset"
    assert [a.text() for a in move.actions()] == ["Second Playset"]
    move.actions()[0].trigger()
    assert folders(dialog.bound) == [UNE]
    assert window.bindings.playset_of(ELVES) == second.id

    unbind = next(
        a for a in dialog.menu_for(dialog.bound, (UNE,)).actions() if a.text() == "Unbind"
    )
    unbind.trigger()
    assert folders(dialog.bound) == []
    assert folders(dialog.unbound) == [UNE]  # ELVES is Second Playset's
    assert window.bindings.playset_of(UNE) == ""

    # An unbound save can be bound to any playset from its menu.
    bind = dialog.menu_for(dialog.unbound, (UNE,)).findChild(QMenu)
    assert bind is not None and bind.title() == "Bind to playset"
    assert [a.text() for a in bind.actions()] == ["Main Playset", "Second Playset"]


def test_deleting_a_playset_unbinds_its_saves(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert window.book is not None and window.bindings is not None
    main = window.book.playsets[0]
    window.bind_saves([UNE, ELVES], main.id)
    asked: list[str] = []

    def confirm(title: str, text: str) -> bool:
        asked.append(text)
        return True

    monkeypatch.setattr(window, "confirm", confirm)

    window.playset_list.setCurrentRow(1)
    window.delete_action.trigger()
    assert "Its 2 saves are unbound" in asked[0]
    assert window.bindings.saves_of(main.id) == set()
    assert window.bindings.get(UNE) is None


def test_a_new_save_after_play_is_bound_on_its_own(
    qtbot: QtBot,
    window: MainWindow,
    sample_install: SampleInstall,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Game:
        exit_code: int | None = None

        def poll(self) -> int | None:
            return self.exit_code

    game = Game()
    monkeypatch.setattr(window, "launch", lambda *args: game)
    monkeypatch.setattr(window, "confirm", lambda *args: True)  # Unsubscribed Mod is missing
    window.playset_list.setCurrentRow(1)
    window.play_action.trigger()

    # The player starts a new campaign and saves once.
    new = "newempire_42"
    make_save(
        sample_install.data_dir / f"save games/{new}/2200.02.01.sav",
        'name="New Empire"\ndate="2200.02.01"\nmods={ "Alpha Interface" "My Local Tweaks" }\n',
    )
    game.exit_code = 0
    with qtbot.waitSignal(window.saves_found, timeout=10_000):
        window._check_game()

    main = window.selected_playset()
    assert main is not None and window.bindings is not None
    assert window.bindings.playset_of(new) == main.id
    # Older saves aren't bound by Play.
    assert window.bindings.get(UNE) is None and window.bindings.get(ELVES) is None
    assert window.saves_button.text() == "Saves (1)"
    assert "New Empire" in window.statusBar().currentMessage()


def test_suggestions_and_marks_in_the_saves_window(qtbot: QtBot, window: MainWindow) -> None:
    assert window.book is not None and window.bindings is not None
    main, second = window.book.playsets[0], window.book.playsets[1]
    window.playset_list.setCurrentRow(1)
    dialog = open_saves(qtbot, window)

    # ELVES lists only Gamma Soundtrack, which is what Second Playset plays.
    assert folders(dialog.unbound) == [UNE, ELVES]
    elves = dialog.unbound.topLevelItem(1)
    assert elves is not None and elves.text(5) == "Second Playset"
    assert dialog.suggested_button.isEnabled()
    dialog.suggested_button.click()
    assert window.bindings.playset_of(ELVES) == second.id
    assert not dialog.suggested_button.isEnabled()

    # UNE was made with Alpha and Beta; Main Playset plays Alpha and My Local.
    window.bind_saves([UNE], main.id)
    une = dialog.bound.topLevelItem(0)
    assert une is not None
    assert une.text(5) == "⚠ Mods differ"
    assert une.toolTip(5) == "Added since: My Local Tweaks\nRemoved since: Beta Ships"
    assert "1 is marked ⚠" in dialog.summary.text()
    assert window.saves_label.isVisible()
    assert "United Nations of Earth" in window.saves_label.text()

    window.playset_list.setCurrentRow(2)  # Second Playset: ELVES matches it
    elves = dialog.bound.topLevelItem(0)
    assert elves is not None and elves.text(5) == "✓"
    assert not window.saves_label.isVisible()


def build(qtbot: QtBot, window: MainWindow) -> BuildRecord:
    with (
        qtbot.waitSignal(window.library_shown, timeout=20_000),
        qtbot.waitSignal(window.build_finished, timeout=20_000) as finished,
    ):
        window.build_action.trigger()
    record: BuildRecord = finished.args[0]
    return record


def built_playset(window: MainWindow) -> Playset:
    assert window.book is not None
    return next(p for p in window.book.playsets if p.name == "Main Playset (built)")


def test_a_rebuild_asks_about_the_built_playsets_saves(
    qtbot: QtBot,
    clashing: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
    sample_install: SampleInstall,
) -> None:
    def no_question(*args: object) -> RebuildChoice | None:
        raise AssertionError("asked with no saves to ask about")

    monkeypatch.setattr(clashing, "ask_rebuild", no_question)
    build(qtbot, clashing)  # a first build asks nothing
    built = built_playset(clashing)
    assert clashing.bindings is not None
    clashing.bind_saves([UNE, ELVES], built.id)
    clashing.playset_list.setCurrentRow(1)  # back to Main Playset

    asked: list[tuple[str, list[str], str]] = []

    def ask(playset: Playset, plays_it: Playset, saves: list[Save], changes: str) -> RebuildChoice:
        asked.append((plays_it.name, [s.folder for s in saves], changes))
        return RebuildChoice(frozenset({UNE, ELVES}), frozenset({UNE}), trash=True)

    trashed: list[Path] = []

    def trash(path: Path) -> bool:
        trashed.append(path)
        return True

    monkeypatch.setattr(clashing, "ask_rebuild", ask)
    monkeypatch.setattr(clashing, "trash", trash)
    record = build(qtbot, clashing)

    assert asked == [("Main Playset (built)", [UNE, ELVES], asked[0][2])]
    assert asked[0][2].startswith("The same mods.")
    une = clashing.bindings.get(UNE)
    assert une is not None and une.playset == built.id and une.build == record.built
    assert clashing.bindings.playset_of(ELVES) == ""  # not kept: unbound for good
    assert trashed == [sample_install.data_dir / f"save games/{ELVES}"]


def test_cancelling_the_rebuild_question_builds_nothing(
    qtbot: QtBot, clashing: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    build(qtbot, clashing)
    assert clashing.bindings is not None
    clashing.bind_saves([UNE], built_playset(clashing).id)
    clashing.playset_list.setCurrentRow(1)
    before = clashing.bindings.get(UNE)

    monkeypatch.setattr(clashing, "ask_rebuild", lambda *args: None)
    clashing.build_action.trigger()
    assert clashing._build_task is None
    assert clashing.bindings.get(UNE) == before


def test_a_failed_rebuild_changes_no_binding(
    qtbot: QtBot, clashing: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    build(qtbot, clashing)
    assert clashing.bindings is not None
    clashing.bind_saves([UNE], built_playset(clashing).id)
    clashing.playset_list.setCurrentRow(1)
    before = clashing.bindings.get(UNE)

    def failing_builder(*args: object) -> Callable[[JobContext], object]:
        def job(ctx: JobContext) -> object:
            raise BuildError("something is in the way")

        return job

    monkeypatch.setattr(main_window_module, "Builder", failing_builder)
    monkeypatch.setattr(
        clashing, "ask_rebuild", lambda *args: RebuildChoice(frozenset({UNE}), frozenset(), True)
    )
    told: list[str] = []
    monkeypatch.setattr(clashing, "tell", lambda title, text: told.append(text))
    with qtbot.waitSignal(clashing.build_finished, timeout=5_000, raising=False):
        clashing.build_action.trigger()
    qtbot.waitUntil(lambda: clashing._build_task is None, timeout=5_000)
    assert "something is in the way" in told[0]
    assert clashing.bindings.get(UNE) == before


def played_with(window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> list[PlayPlan]:
    """Record each plan Play or Continue hands to the game, instead of starting it."""
    plans: list[PlayPlan] = []

    def launch(plan: PlayPlan, *args: object) -> None:
        plans.append(plan)

    monkeypatch.setattr(window, "launch", launch)
    return plans


def test_continue_opens_the_newest_save(
    qtbot: QtBot, window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert window.book is not None
    second = window.book.playsets[1]
    with qtbot.waitSignal(window.saves_found, timeout=10_000):
        window._scan_saves()
    window.playset_list.setCurrentRow(2)
    assert not window.continue_action.isEnabled()  # no saves yet

    window.bind_saves([ELVES], second.id)
    assert window.continue_action.isEnabled()
    plans = played_with(window, monkeypatch)
    window.continue_action.trigger()
    (plan,) = plans
    assert plan.continue_from == ContinueGame(
        ELVES, "2200.01.01", "Divine Elven Order", "2200.01.01"
    )
    assert "at Divine Elven Order 2200.01.01" in window.statusBar().currentMessage()


def test_continuing_a_save_whose_mods_differ_asks_first(
    qtbot: QtBot, window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert window.book is not None
    second = window.book.playsets[1]
    window.playset_list.setCurrentRow(2)
    open_saves(qtbot, window)  # reads the saves
    window.bind_saves([UNE], second.id)  # made with Alpha and Beta; Second plays Gamma
    plans = played_with(window, monkeypatch)
    asked: list[str] = []
    answer = False

    def confirm(title: str, text: str) -> bool:
        asked.append(text)
        return answer

    monkeypatch.setattr(window, "confirm", confirm)
    window.continue_action.trigger()
    assert "is marked: Mods differ" in asked[0] and "Removed since: Alpha Interface" in asked[0]
    assert plans == []

    answer = True
    window.continue_action.trigger()
    (plan,) = plans
    assert plan.continue_from is not None and plan.skip_menu
    assert plan.continue_from.title == f"save games/{UNE}/2201.01.26"


# Hiding other playsets' saves. The sample install keeps autosaves local.


def test_play_hides_other_playsets_saves_until_the_game_closes(
    qtbot: QtBot,
    window: MainWindow,
    sample_install: SampleInstall,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess

    from cold_steel.paradox import processes

    assert window.book is not None
    main, second = window.book.playsets[0], window.book.playsets[1]
    saves = sample_install.data_dir / "save games"
    make_save(saves / "loose_1/2200.01.01.sav", 'name="Loose"\ndate="2200.01.01"\n')
    with qtbot.waitSignal(window.saves_found, timeout=10_000):
        window._scan_saves()
    window.bind_saves([UNE], main.id)
    window.bind_saves([ELVES], second.id)

    class Game:
        exit_code: int | None = None

        def __init__(self, args: list[str], **kwargs: object) -> None:
            self.args = args

        def poll(self) -> int | None:
            return self.exit_code

    started: list[Game] = []

    def popen(args: list[str], **kwargs: object) -> Game:
        assert not (saves / UNE).exists()  # hidden before the game starts
        started.append(Game(args))
        return started[-1]

    monkeypatch.setattr(processes, "running", lambda names: names == processes.STEAM)
    monkeypatch.setattr(subprocess, "Popen", popen)
    window.library.game.exe.write_text("#!/bin/sh\n")  # type: ignore[union-attr]
    window.playset_list.setCurrentRow(2)  # Second Playset
    window.play_action.trigger()
    (game,) = started
    assert "1 save(s) of other playsets are hidden" in window.statusBar().currentMessage()
    # Its own save, and the one bound to no playset, stay in the Load menu.
    assert (saves / ELVES).is_dir() and (saves / "loose_1").is_dir()
    # The game's own Continue points at this playset's newest save, but Play shows the menu.
    continue_game = (sample_install.data_dir / "continue_game.json").read_text()
    assert f"save games/{ELVES}/2200.01.01" in continue_game
    assert "--continuelastsave" not in game.args

    game.exit_code = 0
    with qtbot.waitSignal(window.saves_found, timeout=10_000):
        window._check_game()
    assert (saves / UNE / "2201.01.26.sav").is_file()
    assert not (sample_install.data_dir / "cold_steel_hidden_saves").exists()


def test_the_saves_features_are_off_while_autosaves_go_to_steam_cloud(
    qtbot: QtBot,
    window: MainWindow,
    sample_install: SampleInstall,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert window.book is not None
    main = window.book.playsets[0]
    window.bind_saves([UNE], main.id)
    window.playset_list.setCurrentRow(1)

    def on() -> bool:  # read fresh each time, so mypy doesn't narrow it
        return window.saves_on

    assert on() and window.saves_button.isVisibleTo(window)

    settings = sample_install.data_dir / "settings.txt"
    settings.write_text("autosave_tocloud=yes\n")
    plans = played_with(window, monkeypatch)
    monkeypatch.setattr(window, "confirm", lambda *args: True)  # Main has a missing mod
    window.play_action.trigger()  # read each time Play is pressed
    (plan,) = plans
    assert plan.hide is None and plan.continue_from is None and not plan.skip_menu
    assert not on()
    for widget in (window.saves_button, window.continue_button, window.saves_label):
        assert not widget.isVisibleTo(window)
    assert not window.saves_action.isVisible() and not window.continue_action.isVisible()
    assert "save" not in window._saves_unbound_text(main)  # Delete doesn't mention them
    window._scan_saves()
    assert window._saves_task is None  # not even read

    settings.unlink()  # can't tell: treated as cloud, so off
    window._read_saves_setting()
    assert not on()

    # Turned off in the game: back as soon as Cold Steel notices, and read again.
    settings.write_text("autosave=4\n")  # as the game writes it with the option off
    with qtbot.waitSignal(window.saves_found, timeout=10_000):
        monkeypatch.setattr(window, "ask_settings", lambda: None)
        window.edit_settings()
    assert on() and window.saves_button.text() == "Saves (1)"
    assert window.continue_button.isVisibleTo(window)


def test_saves_left_hidden_come_back_when_cold_steel_starts(
    qtbot: QtBot,
    sample_install: SampleInstall,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cold_steel.core.hide import hidden_dir, hidden_file, hide
    from cold_steel.paradox import processes

    data = sample_install.data_dir
    hide([UNE], data / "save games", hidden_dir(data), hidden_file())  # then Cold Steel crashed
    game_running = True
    monkeypatch.setattr(
        processes, "running", lambda names: game_running and names == processes.GAME
    )
    win = MainWindow(
        Scanner(*sample_install.scanner_args()),
        tmp_path / "thumbnails",
        playsets_path=tmp_path / "data/playsets.json",
        backup_dir=tmp_path / "data/backups",
    )
    qtbot.addWidget(win)
    with qtbot.waitSignal(win.library_shown, timeout=10_000):
        win.show()
    assert not (data / "save games" / UNE).exists()  # the game still runs: they wait
    assert win._game_timer.isActive()

    game_running = False
    with qtbot.waitSignal(win.saves_found, timeout=10_000):
        win._check_game()
    assert (data / "save games" / UNE / "2201.01.26.sav").is_file()
    assert not win._game_timer.isActive()


def test_settings_show_whether_autosaves_go_to_steam_cloud(qtbot: QtBot) -> None:
    from cold_steel.ui.settings_dialog import SettingsDialog

    def shown(cloud: bool | None) -> tuple[str, bool]:
        dialog = SettingsDialog(Settings(), None, game_found=True, cloud_autosaves=cloud)
        qtbot.addWidget(dialog)
        return dialog.cloud.text(), dialog.cloud_hint.isVisibleTo(dialog)

    assert shown(False) == ("Off", False)
    assert shown(True) == ("On", True)  # with a short hint on turning them off
    text, hint = shown(None)
    assert "settings.txt couldn't be read" in text and hint
    dialog = SettingsDialog(Settings(), None)  # no game found: nothing to say
    qtbot.addWidget(dialog)
    assert not dialog.cloud.isVisibleTo(dialog)
    assert "saves features" in dialog.cloud_hint.text()


def saves_not_read_yet(qtbot: QtBot, window: MainWindow) -> None:
    """As if Build were pressed before the first save scan finished."""
    qtbot.waitUntil(lambda: window._saves_task is None, timeout=10_000)
    window.saves = None


def test_a_rebuild_waits_for_the_saves_instead_of_blocking(
    qtbot: QtBot, clashing: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    build(qtbot, clashing)
    clashing.bind_saves([UNE], built_playset(clashing).id)
    clashing.playset_list.setCurrentRow(1)
    saves_not_read_yet(qtbot, clashing)

    asked: list[list[str]] = []

    def ask(playset: Playset, plays_it: Playset, saves: list[Save], changes: str) -> None:
        asked.append([s.folder for s in saves])

    monkeypatch.setattr(clashing, "ask_rebuild", ask)
    with qtbot.waitSignal(clashing.saves_found, timeout=10_000):
        clashing.build_action.trigger()
        # Nothing read on the main thread: the question waits for the scan.
        assert asked == [] and clashing._build_task is None
        assert "Reading your saves" in clashing.statusBar().currentMessage()
    assert asked == [[UNE]]  # then asked; cancelling it built nothing
    assert clashing._build_task is None


def test_a_waiting_rebuild_is_dropped_when_another_playset_is_chosen(
    qtbot: QtBot, clashing: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    build(qtbot, clashing)
    clashing.bind_saves([UNE], built_playset(clashing).id)
    clashing.playset_list.setCurrentRow(1)
    saves_not_read_yet(qtbot, clashing)

    def no_question(*args: object) -> None:
        raise AssertionError("asked about a playset no longer chosen")

    monkeypatch.setattr(clashing, "ask_rebuild", no_question)
    with qtbot.waitSignal(clashing.saves_found, timeout=10_000):
        clashing.build_action.trigger()
        clashing.playset_list.setCurrentRow(2)
    assert clashing._build_task is None
    assert "Build cancelled" in clashing.statusBar().currentMessage()
