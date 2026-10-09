"""The question asked before a rebuild, on its own."""

from pathlib import Path

from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from cold_steel.core.saves import Save, SaveFile
from cold_steel.paradox.save import SaveInfo
from cold_steel.ui.rebuild_dialog import RebuildDialog, describe_changes


def save(folder: str, *cloud: bool) -> Save:
    info = SaveInfo(empire=folder.title(), date="2200.01.01", version="Cygnus v4.5.1")
    files = tuple(
        SaveFile(Path(f"{folder}/{i}.sav"), on_cloud, 1_791_000_000 * 10**9, info)
        for i, on_cloud in enumerate(cloud)
    )
    return Save(folder, files)


def test_what_changed_since_the_last_build() -> None:
    assert describe_changes(("Gigastructures", "ACOT"), ("Ariphaos",), False) == (
        "Since the last build: 2 mod(s) added (Gigastructures, ACOT), 1 mod(s) removed (Ariphaos)."
    )
    assert describe_changes((), (), True) == (
        "Since the last build: the same mods, in another load order."
    )
    assert describe_changes((), (), False).startswith("The same mods. Files inside them")


def test_every_save_is_kept_unless_unticked(qtbot: QtBot) -> None:
    dialog = RebuildDialog("Mix", "Mix (built)", [save("one", False), save("two", False, True)], "")
    qtbot.addWidget(dialog)
    assert (
        "\u201cMix (built)\u201d has 2 save(s)"
        in dialog.findChildren(type(dialog.changes))[0].text()
    )
    assert dialog.choice().keep == {"one", "two"}
    assert dialog.choice().dropped == set()
    assert "Every save is kept" in dialog.trash_note.text()

    dialog.set_kept("two", False)
    assert "Their files stay" in dialog.trash_note.text()
    dialog.trash_box.setChecked(True)
    choice = dialog.choice()
    assert choice.keep == {"one"} and choice.dropped == {"two"} and choice.trash
    # Only local files go. Steam Cloud's autosave stays.
    assert dialog.trash_note.text().startswith(
        "1 local file(s) go to the trash. 1 cloud autosave(s) stay"
    )


def test_files_arent_trashed_while_the_game_runs(qtbot: QtBot) -> None:
    dialog = RebuildDialog("Mix", "Mix (built)", [save("one", False)], "", game_running=True)
    qtbot.addWidget(dialog)
    assert not dialog.trash_box.isEnabled()
    assert "Close Stellaris" in dialog.trash_note.text()
    dialog.set_kept("one", False)
    dialog.trash_box.setChecked(True)
    assert not dialog.choice().trash


def test_rows_show_each_save(qtbot: QtBot) -> None:
    dialog = RebuildDialog("Mix", "Mix (built)", [save("one", False)], "")
    qtbot.addWidget(dialog)
    item = dialog.tree.topLevelItem(0)
    assert item is not None
    assert item.checkState(0) == Qt.CheckState.Checked
    assert [item.text(c) for c in (1, 2)] == ["One", "2200.01.01"]
