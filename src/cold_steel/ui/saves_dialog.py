"""A playset's saves: the ones bound to it, and the ones bound to no playset."""

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cold_steel.core.saves import Save, SaveCheck, SaveFile
from cold_steel.store.playsets import Playset
from cold_steel.ui.full_text import show_full_text

FOLDER_ROLE = Qt.ItemDataRole.UserRole
COLUMNS = ["Save", "In game", "Saved", "Version", "Where"]
# The last column: how a bound save compares with its playset, or which
# playset an unbound one is suggested for.
STATE, SUGGESTED = "Mods", "Suggested for"
OK, WARNING = "✓", "⚠"
LOCAL, CLOUD = "Local", "Steam Cloud"


def saved_text(saved: int, now: datetime | None = None) -> str:
    """When a file was written: "7 Oct 17:35", with the year if it isn't this one."""
    when = datetime.fromtimestamp(saved / 1e9)
    now = now or datetime.now()
    day = f"{when.day} {when:%b}" + (f" {when.year}" if when.year != now.year else "")
    return f"{day} {when:%H:%M}"


def _version(file: SaveFile) -> str:
    # "Cygnus v4.5.2" -> "v4.5.2"
    return file.info.version.rsplit(" ", 1)[-1] if file.info else ""


def _state(check: SaveCheck | None) -> tuple[str, str]:
    """The mark for a bound save, and its tooltip."""
    if check is None:
        return "", ""
    if not check.marks:
        return OK, "Its newest file has the playset's mods"
    return f"{WARNING} " + ", ".join(check.marks), "\n".join(check.details())


def _where(files: Sequence[SaveFile]) -> str:
    places = [
        p for p, cloud in ((LOCAL, False), (CLOUD, True)) if any(f.cloud == cloud for f in files)
    ]
    return " and ".join(places)


class SavesDialog(QDialog):
    # Bind these saves (folders) to a playset (id), or move them to it.
    bind_requested = Signal(tuple, str)
    # Unbind these saves (folders). They're not suggested for a playset again.
    unbind_requested = Signal(tuple)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.resize(820, 560)
        self.playset: Playset | None = None
        self.playsets: tuple[Playset, ...] = ()
        self.suggested: dict[str, Playset] = {}  # by save folder

        self.summary = QLabel(wordWrap=True)
        self.bound = self._tree(STATE)
        self.unbound_label = QLabel(wordWrap=True)
        self.unbound = self._tree(SUGGESTED)
        self.unbound.itemSelectionChanged.connect(self._unbound_selected)

        lower = QWidget()
        lower_layout = QVBoxLayout(lower)
        lower_layout.setContentsMargins(0, 0, 0, 0)
        lower_layout.addWidget(self.unbound_label)
        lower_layout.addWidget(self.unbound)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.bound)
        split.addWidget(lower)
        split.setSizes([320, 200])

        self.bind_button = QPushButton("Bind to this playset")
        self.bind_button.setToolTip("Bind the selected unbound saves to this playset")
        self.bind_button.clicked.connect(self._bind_selected)
        self.suggested_button = QPushButton("Bind all suggested")
        self.suggested_button.setToolTip(
            "Bind each unbound save to the playset whose mods match its newest file"
        )
        self.suggested_button.clicked.connect(self._bind_suggested)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        buttons = QHBoxLayout()
        buttons.addWidget(self.bind_button)
        buttons.addWidget(self.suggested_button)
        buttons.addStretch()
        buttons.addWidget(close)

        layout = QVBoxLayout(self)
        layout.addWidget(self.summary)
        layout.addWidget(split, 1)
        layout.addLayout(buttons)
        self._unbound_selected()

    def _tree(self, last: str) -> QTreeWidget:
        tree = QTreeWidget()
        show_full_text(tree)
        tree.setHeaderLabels([*COLUMNS, last])
        tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, len(COLUMNS) + 1):
            tree.header().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tree.customContextMenuRequested.connect(lambda pos: self._menu(tree, pos))
        return tree

    def set_state(
        self,
        playset: Playset,
        playsets: Sequence[Playset],
        bound: Sequence[Save] | None,
        unbound: Sequence[Save] = (),
        checks: Mapping[str, SaveCheck] | None = None,
        suggested: Mapping[str, Playset] | None = None,
    ) -> None:
        """Show a playset's saves. `bound` is None while the saves are still being read.
        `checks` compares each bound save with the playset, and `suggested` names
        the playset an unbound save's mods match; both are by save folder."""
        self.playset, self.playsets = playset, tuple(playsets)
        checks, self.suggested = checks or {}, dict(suggested or {})
        self.setWindowTitle(f"Saves of {playset.name}")
        self._fill(self.bound, bound or (), lambda save: _state(checks.get(save.folder)))
        self._fill(
            self.unbound,
            unbound,
            lambda save: (p.name, "") if (p := self.suggested.get(save.folder)) else ("", ""),
        )
        self.suggested_button.setEnabled(bool(self.suggested))
        if bound is None:
            self.summary.setText("Looking for saves…")
        elif bound:
            shown = {s.folder for s in bound}
            differ = sum(bool(c.marks) for f, c in checks.items() if f in shown)
            self.summary.setText(
                f"{len(bound)} save(s) belong to this playset, newest first. "
                + (
                    f"{differ} {'is' if differ == 1 else 'are'} marked {WARNING}: "
                    "hover over the mark to see why. "
                    if differ
                    else ""
                )
                + "Open a save to see its files. Right-click to move or unbind it."
            )
        else:
            self.summary.setText(
                "No saves belong to this playset yet. Bind one from the unbound saves below."
            )
        self.unbound_label.setText(
            f"Unbound saves ({len(unbound)}): played with no playset, or unbound by you."
            if unbound
            else "Unbound saves: none."
        )
        self._unbound_selected()

    def _fill(
        self,
        tree: QTreeWidget,
        saves: Sequence[Save],
        last: Callable[[Save], tuple[str, str]],
    ) -> None:
        """One row per save, opening into its files. `last` gives the last column's
        text and tooltip."""
        selected = {i.data(0, FOLDER_ROLE) for i in tree.selectedItems()}
        items = [tree.topLevelItem(row) for row in range(tree.topLevelItemCount())]
        expanded = {i.data(0, FOLDER_ROLE) for i in items if i is not None and i.isExpanded()}
        tree.clear()
        for save in saves:
            newest = save.newest
            text, tip = last(save)
            item = QTreeWidgetItem(
                tree,
                [
                    save.empire,
                    newest.info.date if newest.info else "",
                    saved_text(save.saved),
                    _version(newest),
                    _where(save.files),
                    text,
                ],
            )
            item.setToolTip(len(COLUMNS), tip)
            item.setData(0, FOLDER_ROLE, save.folder)
            where = f"Folder: {save.folder}" if save.info else "None of its files could be read"
            item.setToolTip(0, f"{save.empire}\n{where}")
            for file in save.files:
                child = QTreeWidgetItem(
                    item,
                    [
                        file.name,
                        file.info.date if file.info else "",
                        saved_text(file.saved),
                        _version(file),
                        CLOUD if file.cloud else LOCAL,
                    ],
                )
                child.setToolTip(0, f"{file.path}\n{file.problem}".strip())
                child.setFlags(child.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            item.setSelected(save.folder in selected)
            item.setExpanded(save.folder in expanded)

    def _folders(self, tree: QTreeWidget) -> tuple[str, ...]:
        return tuple(i.data(0, FOLDER_ROLE) for i in tree.selectedItems())

    def _unbound_selected(self) -> None:
        self.bind_button.setEnabled(self.playset is not None and bool(self._folders(self.unbound)))

    def _bind_selected(self) -> None:
        folders = self._folders(self.unbound)
        if self.playset is not None and folders:
            self.bind_requested.emit(folders, self.playset.id)

    def _bind_suggested(self) -> None:
        by_playset: dict[str, list[str]] = {}
        for folder, playset in self.suggested.items():
            by_playset.setdefault(playset.id, []).append(folder)
        for playset_id, folders in by_playset.items():
            self.bind_requested.emit(tuple(folders), playset_id)

    def _menu(self, tree: QTreeWidget, pos: QPoint) -> None:
        item = tree.itemAt(pos)
        if item is not None and item.parent() is None and not item.isSelected():
            tree.clearSelection()
            item.setSelected(True)
        folders = self._folders(tree)
        if not folders or self.playset is None:
            return
        menu = self.menu_for(tree, folders)
        menu.exec(tree.viewport().mapToGlobal(pos))

    def menu_for(self, tree: QTreeWidget, folders: tuple[str, ...]) -> QMenu:
        """The right-click menu for these saves. Kept apart so tests can use it."""
        menu = QMenu(self)
        bound = tree is self.bound
        target = menu.addMenu("Move to playset" if bound else "Bind to playset")
        for playset in self.playsets:
            if bound and self.playset is not None and playset.id == self.playset.id:
                continue
            act = target.addAction(playset.name)
            act.triggered.connect(
                lambda _=False, pid=playset.id: self.bind_requested.emit(folders, pid)
            )
        target.setEnabled(not target.isEmpty())
        if bound:
            unbind = menu.addAction("Unbind")
            unbind.setToolTip("The save stays on disk. It isn't suggested for a playset again")
            unbind.triggered.connect(lambda: self.unbind_requested.emit(folders))
        return menu
