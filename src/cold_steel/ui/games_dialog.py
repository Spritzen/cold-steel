"""A playset's games: the ones bound to it, and the ones bound to no playset."""

from collections.abc import Sequence
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

from cold_steel.core.saves import Save, SavedGame
from cold_steel.store.playsets import Playset
from cold_steel.ui.full_text import show_full_text

FOLDER_ROLE = Qt.ItemDataRole.UserRole
COLUMNS = ["Game", "In game", "Saved", "Version", "Where"]
LOCAL, CLOUD = "Local", "Steam Cloud"


def saved_text(saved: int, now: datetime | None = None) -> str:
    """When a save was written: "7 Oct 17:35", with the year if it isn't this one."""
    when = datetime.fromtimestamp(saved / 1e9)
    now = now or datetime.now()
    day = f"{when.day} {when:%b}" + (f" {when.year}" if when.year != now.year else "")
    return f"{day} {when:%H:%M}"


def _version(save: Save) -> str:
    # "Cygnus v4.5.2" -> "v4.5.2"
    return save.info.version.rsplit(" ", 1)[-1] if save.info else ""


def _where(saves: Sequence[Save]) -> str:
    places = [
        p for p, cloud in ((LOCAL, False), (CLOUD, True)) if any(s.cloud == cloud for s in saves)
    ]
    return " and ".join(places)


class GamesDialog(QDialog):
    # Bind these games (folders) to a playset (id), or move them to it.
    bind_requested = Signal(tuple, str)
    # Unbind these games (folders). They're not suggested for a playset again.
    unbind_requested = Signal(tuple)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.resize(820, 560)
        self.playset: Playset | None = None
        self.playsets: tuple[Playset, ...] = ()

        self.summary = QLabel(wordWrap=True)
        self.bound = self._tree()
        self.unbound_label = QLabel(wordWrap=True)
        self.unbound = self._tree()
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
        self.bind_button.setToolTip("Bind the selected unbound games to this playset")
        self.bind_button.clicked.connect(self._bind_selected)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        buttons = QHBoxLayout()
        buttons.addWidget(self.bind_button)
        buttons.addStretch()
        buttons.addWidget(close)

        layout = QVBoxLayout(self)
        layout.addWidget(self.summary)
        layout.addWidget(split, 1)
        layout.addLayout(buttons)
        self._unbound_selected()

    def _tree(self) -> QTreeWidget:
        tree = QTreeWidget()
        show_full_text(tree)
        tree.setHeaderLabels(COLUMNS)
        tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, len(COLUMNS)):
            tree.header().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tree.customContextMenuRequested.connect(lambda pos: self._menu(tree, pos))
        return tree

    def set_state(
        self,
        playset: Playset,
        playsets: Sequence[Playset],
        bound: Sequence[SavedGame] | None,
        unbound: Sequence[SavedGame] = (),
    ) -> None:
        """Show a playset's games. `bound` is None while the saves are still being read."""
        self.playset, self.playsets = playset, tuple(playsets)
        self.setWindowTitle(f"Games of {playset.name}")
        self._fill(self.bound, bound or ())
        self._fill(self.unbound, unbound)
        if bound is None:
            self.summary.setText("Looking for saves…")
        elif bound:
            self.summary.setText(
                f"{len(bound)} game(s) belong to this playset, newest first. "
                "Open a game to see its saves. Right-click to move or unbind it."
            )
        else:
            self.summary.setText(
                "No games belong to this playset yet. Bind one from the unbound games below."
            )
        self.unbound_label.setText(
            f"Unbound games ({len(unbound)}): played with no playset, or unbound by you."
            if unbound
            else "Unbound games: none."
        )
        self._unbound_selected()

    def _fill(self, tree: QTreeWidget, games: Sequence[SavedGame]) -> None:
        selected = {i.data(0, FOLDER_ROLE) for i in tree.selectedItems()}
        items = [tree.topLevelItem(row) for row in range(tree.topLevelItemCount())]
        expanded = {i.data(0, FOLDER_ROLE) for i in items if i is not None and i.isExpanded()}
        tree.clear()
        for game in games:
            newest = game.newest
            info = game.info
            item = QTreeWidgetItem(
                tree,
                [
                    game.empire,
                    newest.info.date if newest.info else "",
                    saved_text(game.saved),
                    _version(newest),
                    _where(game.saves),
                ],
            )
            item.setData(0, FOLDER_ROLE, game.folder)
            tip = f"Folder: {game.folder}" if info else "No save of this game could be read"
            item.setToolTip(0, f"{game.empire}\n{tip}")
            for save in game.saves:
                child = QTreeWidgetItem(
                    item,
                    [
                        save.name,
                        save.info.date if save.info else "",
                        saved_text(save.saved),
                        _version(save),
                        CLOUD if save.cloud else LOCAL,
                    ],
                )
                child.setToolTip(0, f"{save.path}\n{save.problem}".strip())
                child.setFlags(child.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            item.setSelected(game.folder in selected)
            item.setExpanded(game.folder in expanded)

    def _folders(self, tree: QTreeWidget) -> tuple[str, ...]:
        return tuple(i.data(0, FOLDER_ROLE) for i in tree.selectedItems())

    def _unbound_selected(self) -> None:
        self.bind_button.setEnabled(self.playset is not None and bool(self._folders(self.unbound)))

    def _bind_selected(self) -> None:
        folders = self._folders(self.unbound)
        if self.playset is not None and folders:
            self.bind_requested.emit(folders, self.playset.id)

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
        """The right-click menu for these games. Kept apart so tests can use it."""
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
            unbind.setToolTip("The game stays on disk. It isn't suggested for a playset again")
            unbind.triggered.connect(lambda: self.unbind_requested.emit(folders))
        return menu
