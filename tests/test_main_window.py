from pathlib import Path

import pytest
from PySide6.QtCore import QModelIndex, Qt
from pytestqt.qtbot import QtBot

from cold_steel.core import playsets as ops
from cold_steel.core.library import Scanner
from cold_steel.ui.conflicts_window import ConflictsWindow
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


# Phase 2: playsets


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


# Phase 3: health and errors


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


# Phase 4: conflicts


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


# Phase 5: resolving conflicts


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
