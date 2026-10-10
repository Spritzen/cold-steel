"""A playset's empire list, and importing empires from another list or exporting to one."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
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

from cold_steel.core.empires import KINDS, EmpireCheck, Need
from cold_steel.paradox.empires import Empire
from cold_steel.store.empires import LOOSE
from cold_steel.ui.full_text import show_full_text

NAME_ROLE = Qt.ItemDataRole.UserRole
COLUMNS = ["Empire", "Species", "Origin", "Ethics", "Civics", "Authority", "Mods"]
MODS = COLUMNS.index("Mods")
OK, WARNING = "✓", "⚠"
LOOSE_NAME = "Not in any playset"
# Prefixes the game puts on its keys, left out when showing them.
_PREFIXES = ("origin_", "civic_", "ethic_", "auth_", "gov_")


def species_name(key: str) -> str:
    """A species' name. One you picked from the game's names is stored as its
    text key: "SPEC_Korinth" -> "Korinth". One you typed is kept as typed."""
    if not key.startswith("SPEC_"):
        return key
    return " ".join(key.removeprefix("SPEC_").split("_"))


def pretty(key: str) -> str:
    """A key as a name: "ethic_fanatic_spiritualist" -> "Fanatic Spiritualist"."""
    for prefix in _PREFIXES:
        if key.startswith(prefix):
            key = key[len(prefix) :]
            break
    return " ".join(word.capitalize() for word in key.split("_") if word)


@dataclass(frozen=True)
class EmpireRow:
    empire: Empire
    check: EmpireCheck | None = None  # against the playset shown; None until checked


@dataclass(frozen=True)
class OtherList:
    owner: str  # a playset id, or LOOSE
    name: str  # the playset's name, or LOOSE_NAME
    count: int  # how many empires it has


def need_mods(need: Need, names: Mapping[str, str]) -> str:
    if not need.mods:
        return f"{need.use[1]} (in no installed mod)"
    return " or ".join(dict.fromkeys(names.get(m, m) for m in need.mods))


def mark(check: EmpireCheck | None, names: Mapping[str, str]) -> tuple[str, str]:
    """The Mods column for one empire, and its tooltip."""
    if check is None:
        return "", "Checking which mods it needs…"
    if check.ok:
        return OK, (
            "The playset has every mod it needs" if check.needs_mods else "It needs no mods"
        )
    lacking = ", ".join(dict.fromkeys(need_mods(n, names) for n in check.missing))
    details = "\n".join(
        f"{KINDS.get(n.use[0], n.use[0])} {n.use[1]}: {need_mods(n, names)}" for n in check.missing
    )
    return f"{WARNING} {lacking}", f"The playset lacks what it uses:\n{details}"


class EmpiresDialog(QDialog):
    # Copy these empires (names) from this list to another playset's (owner id).
    export_requested = Signal(tuple, str)
    # Copy these empires (names) from another list (owner id, or LOOSE) into this one.
    import_requested = Signal(tuple, str)
    # Take these empires (names) out of this list.
    remove_requested = Signal(tuple)
    # Delete these empires (names) from the loose list.
    delete_requested = Signal(tuple)
    # Show another list (owner id, or LOOSE) to import from.
    source_chosen = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.resize(1120, 620)
        self.others: tuple[OtherList, ...] = ()
        self.source = LOOSE
        self.locked = False

        self.summary = QLabel(wordWrap=True)
        self.listed = self._tree()
        self.listed.itemSelectionChanged.connect(self._selection_changed)
        self.export_button = QPushButton("Export to playset")
        self.export_button.setToolTip("Copy the selected empires to another playset's list")
        self.export_menu = QMenu(self)
        self.export_button.setMenu(self.export_menu)
        self.remove_button = QPushButton("Remove…")
        self.remove_button.setToolTip("Take the selected empires out of this playset's list")
        self.remove_button.clicked.connect(
            lambda: self.remove_requested.emit(self._names(self.listed))
        )
        upper_buttons = QHBoxLayout()
        upper_buttons.addWidget(self.export_button)
        upper_buttons.addWidget(self.remove_button)
        upper_buttons.addStretch()

        self.source_box = QComboBox()
        self.source_box.currentIndexChanged.connect(self._source_picked)
        self.other = self._tree()
        self.other.itemSelectionChanged.connect(self._selection_changed)
        self.import_button = QPushButton("Import to this playset")
        self.import_button.clicked.connect(
            lambda: self.import_requested.emit(self._names(self.other), self.source)
        )
        pick = QHBoxLayout()
        pick.addWidget(QLabel("Import from:"))
        pick.addWidget(self.source_box, 1)
        pick.addWidget(self.import_button)

        upper, lower = QWidget(), QWidget()
        upper_layout = QVBoxLayout(upper)
        upper_layout.setContentsMargins(0, 0, 0, 0)
        upper_layout.addWidget(self.listed)
        upper_layout.addLayout(upper_buttons)
        lower_layout = QVBoxLayout(lower)
        lower_layout.setContentsMargins(0, 0, 0, 0)
        lower_layout.addLayout(pick)
        lower_layout.addWidget(self.other)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(upper)
        split.addWidget(lower)
        split.setSizes([320, 240])

        close = QPushButton("Close")
        close.clicked.connect(self.close)
        bottom = QHBoxLayout()
        bottom.addStretch()
        bottom.addWidget(close)

        layout = QVBoxLayout(self)
        layout.addWidget(self.summary)
        layout.addWidget(split, 1)
        layout.addLayout(bottom)
        self._selection_changed()

    def _tree(self) -> QTreeWidget:
        tree = QTreeWidget()
        show_full_text(tree)
        tree.setHeaderLabels(COLUMNS)
        tree.setRootIsDecorated(False)
        tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        header = tree.header()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tree.customContextMenuRequested.connect(lambda pos: self._menu(tree, pos))
        return tree

    def set_state(
        self,
        title: str,
        listed: Sequence[EmpireRow],
        others: Sequence[OtherList],
        source: str,
        source_rows: Sequence[EmpireRow],
        mod_names: Mapping[str, str],
        *,
        checking: bool = False,
        built_from: str = "",
        locked: bool = False,
    ) -> None:
        """Show a playset's list, and the list `source` (one of `others`) to
        import from. `built_from` names the playset whose list a built playset
        uses. `locked` while the game runs with this list: it can't change."""
        self.others, self.source, self.locked = tuple(others), source, locked
        self.setWindowTitle(f"Empires of {title}")
        self._fill(self.listed, listed, mod_names)
        self._fill(self.other, source_rows, mod_names)

        self.source_box.blockSignals(True)
        self.source_box.clear()
        for other in self.others:
            self.source_box.addItem(f"{other.name} ({other.count})", other.owner)
        self.source_box.setCurrentIndex(max(0, self.source_box.findData(source)))
        self.source_box.blockSignals(False)

        self.export_menu.clear()
        self._add_exports(self.export_menu, lambda: self._names(self.listed))

        sentences = [
            f"{len(listed)} empire(s) in this playset's list."
            if listed
            else "This playset's list has no empires yet."
        ]
        if built_from:
            sentences.append(f"It's the list of {built_from}, which this playset plays built.")
        marked = sum(bool(r.check and not r.check.ok) for r in listed)
        if checking:
            sentences.append("Checking which mods each empire needs…")
        elif marked:
            verb = "is" if marked == 1 else "are"
            sentences.append(f"{marked} {verb} marked {WARNING}: the playset lacks mods they use.")
        if locked:
            sentences.append("The game is running with this list: it can change once it closes.")
        else:
            sentences.append("Play gives the game exactly these empires.")
        self.summary.setText(" ".join(sentences))
        self._selection_changed()

    def _add_exports(self, menu: QMenu, names: Callable[[], tuple[str, ...]]) -> None:
        """One action per other playset's list, exporting the names `names()` gives."""
        for other in self.others:
            if other.owner == LOOSE:
                continue
            act = menu.addAction(other.name)
            act.triggered.connect(
                lambda _=False, owner=other.owner: self.export_requested.emit(names(), owner)
            )

    def _fill(self, tree: QTreeWidget, rows: Sequence[EmpireRow], names: Mapping[str, str]) -> None:
        selected = {i.data(0, NAME_ROLE) for i in tree.selectedItems()}
        tree.clear()
        for row in rows:
            empire, info = row.empire, row.empire.info
            text, tip = mark(row.check, names) if info else (WARNING, empire.problem)
            item = QTreeWidgetItem(
                tree,
                [
                    empire.name,
                    species_name(info.species) if info else "",
                    pretty(info.origin) if info else "",
                    ", ".join(pretty(e) for e in info.ethics) if info else "",
                    ", ".join(pretty(c) for c in info.civics) if info else "",
                    pretty(info.authority) if info else "",
                    text,
                ],
            )
            item.setData(0, NAME_ROLE, empire.name)
            item.setToolTip(MODS, tip)
            if info:
                keys = (info.species, info.origin, ", ".join(info.ethics), ", ".join(info.civics))
                for col, key in zip((1, 2, 3, 4), keys, strict=True):
                    item.setToolTip(col, key)
                item.setToolTip(5, info.authority)
            item.setSelected(empire.name in selected)

    def _names(self, tree: QTreeWidget) -> tuple[str, ...]:
        return tuple(i.data(0, NAME_ROLE) for i in tree.selectedItems())

    def _selection_changed(self) -> None:
        mine = bool(self._names(self.listed))
        self.export_button.setEnabled(mine and not self.export_menu.isEmpty())
        self.remove_button.setEnabled(mine and not self.locked)
        self.import_button.setEnabled(bool(self._names(self.other)) and not self.locked)
        self.import_button.setToolTip(
            "Move the selected empires into this playset's list"
            if self.source == LOOSE
            else "Copy the selected empires into this playset's list"
        )

    def _source_picked(self) -> None:
        owner = self.source_box.currentData()
        if owner is not None and owner != self.source:
            self.source = owner
            self.source_chosen.emit(owner)

    def _menu(self, tree: QTreeWidget, pos: QPoint) -> None:
        item = tree.itemAt(pos)
        if item is not None and not item.isSelected():
            tree.clearSelection()
            item.setSelected(True)
        names = self._names(tree)
        if names:
            self.menu_for(tree, names).exec(tree.viewport().mapToGlobal(pos))

    def menu_for(self, tree: QTreeWidget, names: tuple[str, ...]) -> QMenu:
        """The right-click menu for these empires. Kept apart so tests can use it."""
        menu = QMenu(self)
        if tree is self.listed:
            target = menu.addMenu("Export to playset")
            self._add_exports(target, lambda: names)
            target.setEnabled(not target.isEmpty())
            remove = menu.addAction("Remove from this playset…")
            remove.setEnabled(not self.locked)
            remove.triggered.connect(lambda: self.remove_requested.emit(names))
            return menu
        imp = menu.addAction("Import to this playset")
        imp.setEnabled(not self.locked)
        imp.triggered.connect(lambda: self.import_requested.emit(names, self.source))
        if self.source == LOOSE:
            menu.addSeparator()
            delete = menu.addAction("Delete…")
            delete.setToolTip("Delete it for good. A backup of the list is kept")
            delete.triggered.connect(lambda: self.delete_requested.emit(names))
        return menu
