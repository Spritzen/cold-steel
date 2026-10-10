"""A list's history, shown as a tab of the Empires window: its versions,
newest first, what each change did, and Restore."""

from collections.abc import Sequence
from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cold_steel.core.empires import Changes, Version
from cold_steel.ui.full_text import show_full_text

VERSION_ROLE = Qt.ItemDataRole.UserRole
COLUMNS = ["When", "Then", "Empires", "What that changed"]


@dataclass(frozen=True)
class VersionRow:
    version: Version
    count: int  # its empires
    changes: Changes | None  # from it to the next version, or to the list now; None if unread
    problem: str = ""  # why it couldn't be read
    names: tuple[str, ...] = ()  # its empires, by name


def changes_text(changes: Changes | None) -> str:
    if changes is None:
        return ""
    if not changes:
        return "Nothing: the same empires"
    parts = [
        f"{label} {', '.join(names)}"
        for label, names in (
            ("Added", changes.added),
            ("Removed", changes.removed),
            ("Changed", changes.changed),
        )
        if names
    ]
    return ". ".join(parts)


class HistoryPanel(QWidget):
    # Put this version (a Version) back as the list.
    restore_requested = Signal(object)
    # Clear every version of the list.
    clear_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.locked = False
        self.summary = QLabel(wordWrap=True)
        self.tree = QTreeWidget()
        show_full_text(self.tree)
        self.tree.setHeaderLabels(COLUMNS)
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header = self.tree.header()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        self.tree.itemDoubleClicked.connect(lambda _item, _col: self._restore())

        self.restore_button = QPushButton("Restore")
        self.restore_button.setToolTip(
            "Put the list back as it was in this version. The list as it is now is kept "
            "as a version first, at the top, so restoring can be undone here too"
        )
        self.restore_button.clicked.connect(self._restore)
        self.clear_button = QPushButton("Clear history…")
        self.clear_button.setToolTip(
            "Move every version of this list to the trash. The list itself stays as it is"
        )
        self.clear_button.clicked.connect(self.clear_requested)
        buttons = QHBoxLayout()
        buttons.addWidget(self.restore_button)
        buttons.addStretch()
        buttons.addWidget(self.clear_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.summary)
        layout.addWidget(self.tree, 1)
        layout.addLayout(buttons)
        self._selection_changed()

    def set_state(
        self, rows: Sequence[VersionRow], *, locked: bool = False, done: str = ""
    ) -> None:
        """Show a list's versions, newest first. `locked` while the game runs
        with it. `done` says what was just done, such as a restore."""
        self.locked = locked
        sentences: list[str] = []
        sentences.append(
            f"{len(rows)} version(s) of this list, newest first. Each is the list as it was "
            "just before the change in Then, and the last column says what that change did. "
            "Restore puts one back."
            if rows
            else "This list has no versions yet. One is kept before each change."
        )
        if locked:
            sentences.append("The game is running with this list: restore once it closes.")
        said = " ".join(sentences)
        self.summary.setText(f"{done}\n\n{said}" if done else said)
        self.tree.clear()
        for row in rows:
            version = row.version
            item = QTreeWidgetItem(
                self.tree,
                [
                    version.when.strftime("%Y-%m-%d %H:%M:%S"),
                    version.note or "A change (no note)",
                    str(row.count) if not row.problem else "",
                    row.problem or changes_text(row.changes),
                ],
            )
            item.setData(0, VERSION_ROLE, version)
            item.setToolTip(0, str(version.path))
            if row.names:
                item.setToolTip(2, "\n".join(row.names))
        self.clear_button.setEnabled(bool(rows))
        self._selection_changed()

    def selected(self) -> Version | None:
        items = self.tree.selectedItems()
        return items[0].data(0, VERSION_ROLE) if items else None

    def _selection_changed(self) -> None:
        self.restore_button.setEnabled(self.selected() is not None and not self.locked)

    def _restore(self) -> None:
        version = self.selected()
        if version is not None and not self.locked:
            self.restore_requested.emit(version)
