"""Asked before a rebuild: keep each save with the rebuilt playset, or let it go."""

from collections.abc import Sequence
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cold_steel.core.saves import Save
from cold_steel.ui.full_text import show_full_text
from cold_steel.ui.saves_dialog import saved_text

FOLDER_ROLE = Qt.ItemDataRole.UserRole


@dataclass(frozen=True)
class RebuildChoice:
    asked: frozenset[str]  # the save folders the question listed
    keep: frozenset[str]  # those kept with the rebuilt playset
    trash: bool  # move the local files of the saves not kept to the trash

    @property
    def dropped(self) -> frozenset[str]:
        return self.asked - self.keep


def describe_changes(added: Sequence[str], removed: Sequence[str], reordered: bool) -> str:
    """What changed since the last build, in a sentence."""
    if not added and not removed:
        if reordered:
            return "Since the last build: the same mods, in another load order."
        return (
            "The same mods. Files inside them may have changed "
            "(a Steam update, or a new patch mod)."
        )
    parts = [
        f"{len(mods)} mod(s) {word} ({', '.join(mods)})"
        for mods, word in ((added, "added"), (removed, "removed"))
        if mods
    ]
    return f"Since the last build: {', '.join(parts)}."


class RebuildDialog(QDialog):
    def __init__(
        self,
        name: str,
        built_name: str,
        saves: Sequence[Save],
        changes: str,
        *,
        game_running: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Rebuild “{name}”")
        self.resize(620, 420)
        self.saves = tuple(saves)

        intro = QLabel(
            f"“{built_name}” has {len(saves)} save(s). The rebuilt mod keeps its "
            "name, so the game will load them without a warning even if the mods changed.",
            wordWrap=True,
        )
        self.changes = QLabel(changes, wordWrap=True)
        self.tree = QTreeWidget()
        show_full_text(self.tree)
        self.tree.setHeaderLabels(["Keep", "Save", "In game", "Saved"])
        self.tree.setRootIsDecorated(False)
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for col in (2, 3):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        for save in saves:
            info = save.info
            item = QTreeWidgetItem(
                self.tree,
                ["", save.empire, info.date if info else "", saved_text(save.saved)],
            )
            item.setData(0, FOLDER_ROLE, save.folder)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Checked)
        self.tree.itemChanged.connect(self._update_trash_note)

        self.trash_box = QCheckBox("Move the local files of saves not kept to the trash")
        self.trash_box.toggled.connect(self._update_trash_note)
        self.trash_note = QLabel(wordWrap=True)
        if game_running:
            self.trash_box.setEnabled(False)
            self.trash_box.setToolTip("Stellaris is running")
        self._game_running = game_running

        buttons = QDialogButtonBox()
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.rebuild_button = buttons.addButton("Rebuild", QDialogButtonBox.ButtonRole.AcceptRole)
        self.rebuild_button.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(self.changes)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.trash_box)
        layout.addWidget(self.trash_note)
        layout.addWidget(buttons)
        self._update_trash_note()
        self.rebuild_button.setFocus()  # not the list, which would frame its first row

    def _items(self) -> list[QTreeWidgetItem]:
        items = (self.tree.topLevelItem(row) for row in range(self.tree.topLevelItemCount()))
        return [item for item in items if item is not None]

    def kept(self) -> frozenset[str]:
        return frozenset(
            i.data(0, FOLDER_ROLE)
            for i in self._items()
            if i.checkState(0) == Qt.CheckState.Checked
        )

    def set_kept(self, folder: str, kept: bool) -> None:
        state = Qt.CheckState.Checked if kept else Qt.CheckState.Unchecked
        for item in self._items():
            if item.data(0, FOLDER_ROLE) == folder:
                item.setCheckState(0, state)

    def choice(self) -> RebuildChoice:
        trash = self.trash_box.isChecked() and self.trash_box.isEnabled()
        asked = frozenset(s.folder for s in self.saves)
        return RebuildChoice(asked, self.kept(), trash)

    def _update_trash_note(self) -> None:
        kept = self.kept()
        dropped = [s for s in self.saves if s.folder not in kept]
        if self._game_running:
            self.trash_note.setText("Close Stellaris to move save files to the trash.")
            return
        cloud = sum(f.cloud for s in dropped for f in s.files)
        local = sum(not f.cloud for s in dropped for f in s.files)
        if not dropped:
            text = "Every save is kept, so nothing goes to the trash."
        elif not self.trash_box.isChecked():
            text = "The saves not kept are unbound. Their files stay where they are."
        else:
            text = f"{local} local file(s) go to the trash."
            if cloud:
                text += (
                    f" {cloud} cloud autosave(s) stay: Steam's folder is never touched. "
                    "Delete those from the game's Load menu."
                )
        self.trash_note.setText(text)
