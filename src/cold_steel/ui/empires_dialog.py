"""A playset's empires: the ones bound to it, and the ones bound to no playset."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

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

from cold_steel.core.empires import KINDS, EmpireCheck, Need
from cold_steel.paradox.empires import Empire
from cold_steel.store.playsets import Playset
from cold_steel.ui.full_text import show_full_text

NAME_ROLE = Qt.ItemDataRole.UserRole
COLUMNS = ["Empire", "Species", "Origin", "Ethics", "Civics", "Authority", "Mods"]
MODS = COLUMNS.index("Mods")
SUGGESTED = "Suggested for"  # the unbound list's last column
OK, WARNING = "✓", "⚠"
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
    check: EmpireCheck | None = None  # None until the mods are checked
    bound_here: bool = False  # bound to this playset itself, not only to its source
    suggested: tuple[Playset, ...] = ()  # for an unbound empire: playsets with its mods


def _need_mods(need: Need, names: Mapping[str, str]) -> str:
    if not need.mods:
        return f"{need.use[1]} (in no installed mod)"
    return " or ".join(dict.fromkeys(names.get(m, m) for m in need.mods))


def _mark(check: EmpireCheck | None, names: Mapping[str, str]) -> tuple[str, str]:
    """The Mods column for one empire, and its tooltip."""
    if check is None:
        return "", "Checking which mods it needs…"
    if check.ok:
        return OK, (
            "The playset has every mod it needs" if check.needs_mods else "It needs no mods"
        )
    lacking = ", ".join(dict.fromkeys(_need_mods(n, names) for n in check.missing))
    details = "\n".join(
        f"{KINDS.get(n.use[0], n.use[0])} {n.use[1]}: {_need_mods(n, names)}" for n in check.missing
    )
    return f"{WARNING} {lacking}", f"The playset lacks what it uses:\n{details}"


class EmpiresDialog(QDialog):
    # Bind these empires (names) to a playset (id), as well as any they have.
    bind_requested = Signal(tuple, str)
    # Unbind these empires (names) from a playset (id).
    unbind_requested = Signal(tuple, str)
    # Delete these empires (names) from the game's empire file.
    delete_requested = Signal(tuple)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.resize(1120, 580)
        self.playset: Playset | None = None
        self.playsets: tuple[Playset, ...] = ()
        self.rows: dict[str, EmpireRow] = {}  # by name, both lists

        self.summary = QLabel(wordWrap=True)
        self.stuck_label = QLabel(wordWrap=True)
        self.stuck_label.hide()
        self.bound = self._tree(COLUMNS)
        self.unbound_label = QLabel(wordWrap=True)
        self.unbound = self._tree([*COLUMNS, SUGGESTED])
        self.unbound.itemSelectionChanged.connect(self._unbound_selected)

        lower = QWidget()
        lower_layout = QVBoxLayout(lower)
        lower_layout.setContentsMargins(0, 0, 0, 0)
        lower_layout.addWidget(self.unbound_label)
        lower_layout.addWidget(self.unbound)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.bound)
        split.addWidget(lower)
        split.setSizes([300, 220])

        self.bind_button = QPushButton("Bind to this playset")
        self.bind_button.setToolTip("Bind the selected unbound empires to this playset")
        self.bind_button.clicked.connect(self._bind_selected)
        self.suggested_button = QPushButton("Bind all suggested")
        self.suggested_button.setToolTip(
            "Bind each unbound empire to every playset that has all the mods it needs"
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
        layout.addWidget(self.stuck_label)
        layout.addWidget(split, 1)
        layout.addLayout(buttons)
        self._unbound_selected()

    def _tree(self, columns: list[str]) -> QTreeWidget:
        tree = QTreeWidget()
        show_full_text(tree)
        tree.setHeaderLabels(columns)
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
        playset: Playset,
        playsets: Sequence[Playset],
        bound: Sequence[EmpireRow],
        unbound: Sequence[EmpireRow],
        mod_names: Mapping[str, str],
        *,
        checking: bool = False,
        stuck: Sequence[str] = (),
        source: Playset | None = None,
    ) -> None:
        """Show a playset's empires. `checking` while the mods each needs are still
        being found. `stuck` names empires still hidden, as one of the same name
        is in the game's file. `source` is the playset a built playset plays."""
        self.playset, self.playsets = playset, tuple(playsets)
        self.rows = {r.empire.name: r for r in (*bound, *unbound)}
        self.setWindowTitle(f"Empires of {playset.name}")
        self._fill(self.bound, bound, mod_names)
        self._fill(self.unbound, unbound, mod_names, suggested=True)
        self.suggested_button.setEnabled(any(r.suggested for r in unbound))

        sentences = [
            f"{len(bound)} empire(s) belong to this playset."
            if bound
            else "No empires belong to this playset yet."
        ]
        if source is not None:
            sentences.append(f"Those of {source.name}, which it plays built, show here too.")
        marked = sum(bool(r.check and not r.check.ok) for r in bound)
        if checking:
            sentences.append("Checking which mods each empire needs…")
        elif marked:
            verb = "is" if marked == 1 else "are"
            sentences.append(f"{marked} of them {verb} marked {WARNING}: it lacks mods they use.")
        sentences.append("In the game, it shows these and the unbound ones.")
        sentences.append("Right-click to bind, unbind or delete an empire.")
        self.summary.setText(" ".join(sentences))
        self.stuck_label.setText(
            f"{WARNING} Still hidden from the game, because an empire of the same name is in "
            f"its file now: {', '.join(stuck)}. They're kept in "
            "~/.local/share/cold-steel/hidden_empires.txt."
        )
        self.stuck_label.setVisible(bool(stuck))
        self.unbound_label.setText(
            f"Unbound empires ({len(unbound)}): they show in every playset."
            if unbound
            else "Unbound empires: none."
        )
        self._unbound_selected()

    def _fill(
        self,
        tree: QTreeWidget,
        rows: Sequence[EmpireRow],
        names: Mapping[str, str],
        *,
        suggested: bool = False,
    ) -> None:
        selected = {i.data(0, NAME_ROLE) for i in tree.selectedItems()}
        tree.clear()
        for row in rows:
            empire, info = row.empire, row.empire.info
            mark, tip = _mark(row.check, names) if info else (WARNING, empire.problem)
            texts = [
                empire.name,
                species_name(info.species) if info else "",
                pretty(info.origin) if info else "",
                ", ".join(pretty(e) for e in info.ethics) if info else "",
                ", ".join(pretty(c) for c in info.civics) if info else "",
                pretty(info.authority) if info else "",
                mark,
            ]
            if suggested:
                texts.append(", ".join(p.name for p in row.suggested))
            item = QTreeWidgetItem(tree, texts)
            item.setData(0, NAME_ROLE, empire.name)
            item.setToolTip(MODS, tip)
            if info:
                keys = (info.origin, ", ".join(info.ethics), ", ".join(info.civics))
                for col, key in zip((2, 3, 4), keys, strict=True):
                    item.setToolTip(col, key)
                item.setToolTip(1, info.species)
                item.setToolTip(5, info.authority)
            item.setSelected(empire.name in selected)

    def _names(self, tree: QTreeWidget) -> tuple[str, ...]:
        return tuple(i.data(0, NAME_ROLE) for i in tree.selectedItems())

    def _unbound_selected(self) -> None:
        self.bind_button.setEnabled(self.playset is not None and bool(self._names(self.unbound)))

    def _bind_selected(self) -> None:
        names = self._names(self.unbound)
        if self.playset is not None and names:
            self.bind_requested.emit(names, self.playset.id)

    def _bind_suggested(self) -> None:
        by_playset: dict[str, list[str]] = {}
        for name, row in self.rows.items():
            for playset in row.suggested:
                by_playset.setdefault(playset.id, []).append(name)
        for playset_id, names in by_playset.items():
            self.bind_requested.emit(tuple(names), playset_id)

    def _menu(self, tree: QTreeWidget, pos: QPoint) -> None:
        item = tree.itemAt(pos)
        if item is not None and not item.isSelected():
            tree.clearSelection()
            item.setSelected(True)
        names = self._names(tree)
        if not names or self.playset is None:
            return
        self.menu_for(tree, names).exec(tree.viewport().mapToGlobal(pos))

    def menu_for(self, tree: QTreeWidget, names: tuple[str, ...]) -> QMenu:
        """The right-click menu for these empires. Kept apart so tests can use it."""
        menu = QMenu(self)
        target = menu.addMenu("Bind to playset")
        for playset in self.playsets:
            act = target.addAction(playset.name)
            act.triggered.connect(
                lambda _=False, pid=playset.id: self.bind_requested.emit(names, pid)
            )
        target.setEnabled(not target.isEmpty())
        if tree is self.bound and self.playset is not None:
            here = tuple(n for n in names if self.rows[n].bound_here)
            unbind = menu.addAction("Unbind from this playset")
            pid = self.playset.id
            unbind.triggered.connect(lambda: self.unbind_requested.emit(here, pid))
            unbind.setEnabled(bool(here))
            unbind.setToolTip(
                "It stays in the game's file, and shows in every playset once bound to none"
                if here
                else "It shows here because it's bound to the playset this one was built from"
            )
        menu.addSeparator()
        delete = menu.addAction("Delete…")
        delete.setToolTip("Delete it from the game's empire file, after a backup")
        delete.triggered.connect(lambda: self.delete_requested.emit(names))
        return menu
