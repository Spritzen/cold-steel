"""Where a playset's mods clash: a list of conflicts, and each one side by side."""

from collections.abc import Callable, Iterable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPalette, QTextCursor, QTextFormat
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cold_steel.core.compare import Pair, Version, compare
from cold_steel.core.conflicts import FILE, Claim, Conflict, Found
from cold_steel.core.index import GAME, Index
from cold_steel.ui.tasks import Task, TaskRunner

ITEM_ROLE = Qt.ItemDataRole.UserRole
# Search shows at most this many objects; more would only slow the list down.
MAX_RESULTS = 500
GAME_NAME = "Stellaris (the game)"

# Kinds named by graphics and interface blocks, shown with a plainer name.
_GRAPHICS_KINDS = {
    "spritetype": "Sprites",
    "corneredtilespritetype": "Sprites (cornered tile)",
    "frameanimatedspritetype": "Sprites (animated)",
    "progressbartype": "Progress bars",
    "containerwindowtype": "Interface windows",
    "entity": "3D entities",
    "pdxmesh": "3D meshes",
    "animation": "Animations",
    "pdxparticle": "Particles",
}


def kind_label(kind: str) -> str:
    """ "common/ship_sizes" -> "Ship sizes", "spritetype" -> "Sprites"."""
    if kind == FILE:
        return "Whole files"
    if kind in _GRAPHICS_KINDS:
        return _GRAPHICS_KINDS[kind]
    if "/" not in kind and kind not in ("events", "localisation"):
        return f"Graphics: {kind}"
    last = kind.rpartition("/")[2].replace("_", " ")
    return last[:1].upper() + last[1:]


class ConflictsWindow(QDialog):
    # The user pressed Refresh: find the conflicts again.
    refresh_requested = Signal()
    # A comparison is on screen. For tests.
    compared = Signal(object)
    # Search results are on screen. For tests.
    searched = Signal(object)

    def __init__(self, tasks: TaskRunner, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Conflicts")
        self.resize(1400, 860)
        self._tasks = tasks
        self._compare_task: Task | None = None
        self._search_task: Task | None = None
        self.found: Found | None = None
        self.index: Index | None = None
        self.names: dict[str, str] = {}

        self.summary = QLabel(wordWrap=True)
        self.progress = QProgressBar(textVisible=False)
        self.progress.setMaximumWidth(260)
        self.progress.hide()
        refresh = QPushButton("Refresh")
        refresh.setToolTip("Find the conflicts again, after changing mods or the load order")
        refresh.clicked.connect(self.refresh_requested)
        top = QHBoxLayout()
        top.addWidget(self.summary, 1)
        top.addWidget(self.progress)
        top.addWidget(refresh)

        self.search = QLineEdit(
            placeholderText="Find any object or file in this playset by name",
            clearButtonEnabled=True,
        )
        # Wait for a pause in typing before searching.
        self._search_timer = QTimer(self, singleShot=True, interval=200)
        self._search_timer.timeout.connect(self._fill_tree)
        self.search.textChanged.connect(lambda: self._search_timer.start())
        self.group_box = QComboBox()
        self.group_box.addItem("Group by type", "type")
        self.group_box.addItem("Group by mod", "mod")
        self.type_box = QComboBox()
        self.mod_box = QComboBox()
        self.mod_box.setToolTip("Only conflicts involving this mod")
        self.identical_box = QCheckBox("Show identical")
        self.identical_box.setToolTip(
            "Conflicts where every mod's version means the same thing. "
            "Whichever wins, the game is the same."
        )
        self.game_box = QCheckBox("Show changes to the game")
        self.game_box.setToolTip(
            "Objects and files only one mod changes. That's what mods are for, so these "
            "are hidden unless you ask."
        )
        for box in (self.group_box, self.type_box, self.mod_box):
            box.currentIndexChanged.connect(self._fill_tree)
        for check in (self.identical_box, self.game_box):
            check.toggled.connect(self._fill_tree)
        filters = QHBoxLayout()
        filters.addWidget(self.search, 1)
        filters.addWidget(self.group_box)
        filters.addWidget(self.type_box)
        filters.addWidget(self.mod_box)
        filters.addWidget(self.identical_box)
        filters.addWidget(self.game_box)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Conflict", "Wins", "Mods"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.resizeSection(0, 300)
        header.resizeSection(1, 160)
        self.tree.currentItemChanged.connect(self._item_chosen)

        self.title = QLabel()
        bold = QFont()
        bold.setBold(True)
        bold.setPointSizeF(bold.pointSizeF() * 1.15)
        self.title.setFont(bold)
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.reason = QLabel(wordWrap=True)
        self.rule = QLabel(wordWrap=True)
        self.rule.setStyleSheet("color: palette(placeholder-text);")
        self.left_box = QComboBox()
        self.right_box = QComboBox()
        self.left_box.currentIndexChanged.connect(self._compare)
        self.right_box.currentIndexChanged.connect(self._compare)
        self.viewer = SideBySide()
        self.same = QLabel("These two versions mean the same thing. Only spacing differs.")
        self.same.hide()

        choose = QHBoxLayout()
        choose.addWidget(QLabel("Left:"))
        choose.addWidget(self.left_box, 1)
        choose.addWidget(QLabel("Right:"))
        choose.addWidget(self.right_box, 1)

        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.addWidget(self.title)
        detail_layout.addWidget(self.reason)
        detail_layout.addWidget(self.rule)
        detail_layout.addLayout(choose)
        detail_layout.addWidget(self.same)
        detail_layout.addWidget(self.viewer, 1)

        splitter = QSplitter()
        splitter.addWidget(self.tree)
        splitter.addWidget(detail)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([480, 920])

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addLayout(filters)
        layout.addWidget(splitter, 1)
        self._show_claims(None, (), "")

    # Filling in

    def show_progress(self, done: int, total: int, message: str) -> None:
        self.progress.setRange(0, total)
        self.progress.setValue(done)
        self.progress.show()
        self.summary.setText(message + "…")

    def show_failure(self, text: str) -> None:
        self.progress.hide()
        self.summary.setText(text)

    def set_found(
        self, found: Found, index: Index, names: dict[str, str], mod: str | None = None
    ) -> None:
        """Show a playset's conflicts. `mod` picks that mod in the mod filter."""
        self.progress.hide()
        self.found, self.index = found, index
        self.names = {GAME: GAME_NAME, **names}
        between = [c for c in found.conflicts if len(c.mods) > 1]
        files = sum(c.kind == FILE for c in between)
        same = sum(c.identical for c in between)
        self.summary.setText(
            f"{len(between)} conflicts between mods: {files} whole files and "
            f"{len(between) - files} objects. {same} of them are identical, so it doesn't "
            "matter which one wins. Load order runs from the game, at the top of each "
            "list, to the last mod."
        )
        current_mod = mod if mod is not None else self.mod_box.currentData()
        current_type = self.type_box.currentData()
        self._fill_combo(
            self.type_box,
            "All types",
            sorted({c.kind for c in found.conflicts}, key=lambda k: kind_label(k).casefold()),
            kind_label,
            current_type,
        )
        self._fill_combo(
            self.mod_box,
            "All mods",
            [k for k in found.order if k != GAME],
            lambda k: self.names.get(k, k),
            current_mod,
        )
        self._fill_tree()

    @staticmethod
    def _fill_combo(
        box: QComboBox,
        everything: str,
        values: Iterable[str],
        label: Callable[[str], str],
        current: str | None,
    ) -> None:
        box.blockSignals(True)
        box.clear()
        box.addItem(everything, None)
        for value in values:
            box.addItem(label(value), value)
        box.setCurrentIndex(max(0, box.findData(current)) if current else 0)
        box.blockSignals(False)

    def visible(self) -> list[Conflict]:
        """The conflicts the filters let through."""
        if self.found is None:
            return []
        kind = self.type_box.currentData()
        mod = self.mod_box.currentData()
        identical = self.identical_box.isChecked()
        game = self.game_box.isChecked()
        return [
            c
            for c in self.found.conflicts
            if (identical or not c.identical)
            and (game or len(c.mods) > 1)
            and (kind is None or c.kind == kind)
            and (mod is None or mod in c.mods)
        ]

    def _fill_tree(self) -> None:
        text = self.search.text().strip()
        if text:
            self._start_search(text)
            return
        self.tree.blockSignals(True)
        self.tree.clear()
        if self.group_box.currentData() == "mod":
            self._fill_by_mod()
        else:
            self._fill_by_type()
        self.tree.blockSignals(False)
        self._item_chosen(self.tree.currentItem())

    def _group(self, label: str, count: int) -> QTreeWidgetItem:
        item = QTreeWidgetItem(self.tree, [label, "", str(count)])
        font = item.font(0)
        font.setBold(True)
        item.setFont(0, font)
        item.setFirstColumnSpanned(True)
        item.setText(0, f"{label} ({count})")
        return item

    def _conflict_item(self, parent: QTreeWidgetItem, conflict: Conflict) -> None:
        winner = self.names.get(conflict.winning.layer, conflict.winning.layer)
        item = QTreeWidgetItem(parent, [conflict.key, winner, str(len(conflict.mods))])
        item.setData(0, ITEM_ROLE, conflict)
        item.setToolTip(0, conflict.key)
        item.setToolTip(1, conflict.reason)
        if conflict.identical:
            for col in range(3):
                item.setForeground(col, QColor(Qt.GlobalColor.gray))
            item.setToolTip(0, f"{conflict.key}\nIdentical: it doesn't matter which one wins.")

    def _fill_by_type(self) -> None:
        groups: dict[str, list[Conflict]] = {}
        for conflict in self.visible():
            groups.setdefault(conflict.kind, []).append(conflict)
        for kind in sorted(groups, key=lambda k: kind_label(k).casefold()):
            group = self._group(kind_label(kind), len(groups[kind]))
            for conflict in groups[kind]:
                self._conflict_item(group, conflict)
        if len(groups) == 1:
            self.tree.expandAll()

    def _fill_by_mod(self) -> None:
        if self.found is None:
            return
        visible = self.visible()
        chosen = self.mod_box.currentData()
        for mod in self.found.order:
            if mod == GAME or (chosen is not None and mod != chosen):
                continue
            mine = [c for c in visible if mod in c.mods]
            if mine:
                group = self._group(self.names.get(mod, mod), len(mine))
                for conflict in mine:
                    self._conflict_item(group, conflict)
        if chosen is not None:
            self.tree.expandAll()

    def _start_search(self, text: str) -> None:
        """Every object and file whose name holds the text, conflict or not.

        Searching half a million names takes a moment, so it runs off the main thread.
        """
        found = self.found
        if found is None:
            return
        if self._search_task is not None:
            self._search_task.cancel()
        task = self._tasks.start(lambda ctx: found.search(text, MAX_RESULTS))
        self._search_task = task
        task.succeeded.connect(lambda results: self._show_results(text, results, task))

    def _show_results(
        self, text: str, results: list[tuple[str, str, tuple[Claim, ...]]], task: Task
    ) -> None:
        if task is not self._search_task or text != self.search.text().strip():
            return  # the user has typed more since
        self._search_task = None
        self.tree.blockSignals(True)
        self.tree.clear()
        groups: dict[str, list[tuple[str, tuple[Claim, ...]]]] = {}
        for kind, key, claims in results:
            groups.setdefault(kind, []).append((key, claims))
        for kind in sorted(groups, key=lambda k: kind_label(k).casefold()):
            group = self._group(kind_label(kind), len(groups[kind]))
            for key, claims in groups[kind]:
                layers = tuple(dict.fromkeys(c.layer for c in claims if c.layer != GAME))
                where = ", ".join(self.names.get(c, c) for c in layers) or GAME_NAME
                item = QTreeWidgetItem(group, [key, where, str(len(layers))])
                item.setData(0, ITEM_ROLE, (kind, key, claims))
        self.tree.expandAll()
        self.tree.blockSignals(False)
        if len(results) >= MAX_RESULTS:
            self.summary.setText(f"Showing the first {MAX_RESULTS} matches. Type more to narrow.")
        self.searched.emit(results)

    # The chosen conflict

    def _item_chosen(self, item: QTreeWidgetItem | None) -> None:
        data = item.data(0, ITEM_ROLE) if item else None
        if isinstance(data, Conflict):
            self._show_conflict(data)
        elif isinstance(data, tuple):
            kind, key, claims = data
            winner = self._found_conflict(kind, key)
            if winner is not None:
                self._show_conflict(winner)
            else:
                self._show_claims(f"{key}  ·  {kind_label(kind)}", claims, "Only one version.")
        else:
            self._show_claims(None, (), "")

    def _found_conflict(self, kind: str, key: str) -> Conflict | None:
        if self.found is None:
            return None
        for conflict in self.found.conflicts:
            if conflict.kind == kind and conflict.key == key:
                return conflict
        return None

    def _show_conflict(self, conflict: Conflict) -> None:
        winner_name = self.names.get(conflict.winning.layer, conflict.winning.layer)
        reason = f"<b>{winner_name}</b> wins. {conflict.reason}"
        if conflict.identical:
            reason += " Every mod's version means the same thing."
        self._show_claims(
            f"{conflict.key}  ·  {kind_label(conflict.kind)}",
            conflict.claims,
            reason,
            conflict.winner,
        )
        rule = conflict.rule
        if rule is None:
            self.rule.setText("Whole files: the mod loaded last replaces the others.")
            return
        checked = (
            f"checked in game on {rule.checked}" if rule.checked else "not yet checked in game"
        )
        if rule.folder:
            where = f"Rule for {rule.folder}/"
        else:
            folder = conflict.winning.path.rpartition("/")[0]
            where = f"{folder}/ has no rule of its own, so the default applies"
        self.rule.setText(f"{where}: {rule.winner} wins. From {rule.source}, {checked}.")

    def _show_claims(
        self, title: str | None, claims: tuple[Claim, ...], reason: str, winner: int = -1
    ) -> None:
        self.title.setText(title or "Choose a conflict to see each version")
        self.reason.setText(reason)
        self.rule.clear()
        for box in (self.left_box, self.right_box):
            box.blockSignals(True)
            box.clear()
            for n, claim in enumerate(claims):
                name = self.names.get(claim.layer, claim.layer)
                line = f":{claim.definition.line}" if claim.definition else ""
                star = "★ " if n == winner else ""
                box.addItem(f"{star}{name}  —  {claim.path}{line}", claim)
            box.blockSignals(False)
        if winner < 0:
            winner = len(claims) - 1
        rival = _rival(claims, winner)
        self.left_box.setCurrentIndex(rival)
        self.right_box.setCurrentIndex(winner)
        self._compare()

    def _compare(self) -> None:
        left, right = self.left_box.currentData(), self.right_box.currentData()
        index = self.index
        if self._compare_task is not None:
            self._compare_task.cancel()
            self._compare_task = None
        if left is None or right is None or index is None:
            self.viewer.clear()
            self.same.hide()
            return
        task = self._tasks.start(lambda ctx: compare(index, left, right))
        self._compare_task = task
        task.succeeded.connect(lambda pair: self._compared(pair, task))
        task.failed.connect(lambda error: self.viewer.show_error(str(error)))

    def _compared(self, pair: Pair, task: Task) -> None:
        if task is not self._compare_task:
            return  # the user has moved on
        self._compare_task = None
        self.viewer.show_pair(pair)
        self.same.setVisible(
            pair.same and self.left_box.currentIndex() != self.right_box.currentIndex()
        )
        self.compared.emit(pair)


def _rival(claims: tuple[Claim, ...], winner: int) -> int:
    """The version to show beside the winner: the nearest from another mod."""
    if not claims:
        return -1
    layer = claims[winner].layer
    order = sorted(range(len(claims)), key=lambda n: (abs(n - winner), claims[n].layer == GAME))
    for n in order:
        if claims[n].layer != layer:
            return n
    return winner


class SideBySide(QWidget):
    """Two versions next to each other. Lines that differ are highlighted."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        mono = QFont("monospace")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.left = QPlainTextEdit(readOnly=True)
        self.right = QPlainTextEdit(readOnly=True)
        for pane in (self.left, self.right):
            pane.setFont(mono)
            pane.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            # Paradox script nests deeply; 8-space tabs push it off the side.
            pane.setTabStopDistance(pane.fontMetrics().horizontalAdvance(" ") * 4)
        self.left_label = QLabel()
        self.right_label = QLabel()
        # Scroll together.
        left_bar, right_bar = self.left.verticalScrollBar(), self.right.verticalScrollBar()
        left_bar.valueChanged.connect(right_bar.setValue)
        right_bar.valueChanged.connect(left_bar.setValue)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        for label, pane in ((self.left_label, self.left), (self.right_label, self.right)):
            column = QVBoxLayout()
            column.addWidget(label)
            column.addWidget(pane, 1)
            layout.addLayout(column, 1)

    def clear(self) -> None:
        for pane in (self.left, self.right):
            pane.clear()
            pane.setExtraSelections([])
        self.left_label.clear()
        self.right_label.clear()

    def show_error(self, text: str) -> None:
        self.clear()
        self.left.setPlainText(f"Couldn't compare these versions: {text}")

    def show_pair(self, pair: Pair) -> None:
        dark = self.palette().color(QPalette.ColorRole.Base).lightness() < 128
        removed = QColor("#5c2b2b" if dark else "#fbe3e3")
        added = QColor("#2b4d2f" if dark else "#e1f5e1")
        self._show(self.left, self.left_label, pair.left, removed)
        self._show(self.right, self.right_label, pair.right, added)

    @staticmethod
    def _show(pane: QPlainTextEdit, label: QLabel, version: Version, colour: QColor) -> None:
        pane.setPlainText(version.text)
        changed = len(version.changed)
        label.setText(
            f"From line {version.first_line}. "
            + (f"{changed} line(s) differ." if changed else "No lines differ.")
        )
        selections: list[QTextEdit.ExtraSelection] = []
        document = pane.document()
        for line in sorted(version.changed):
            block = document.findBlockByNumber(line)
            if not block.isValid():
                continue
            selection = QTextEdit.ExtraSelection()
            selection.format.setBackground(colour)
            selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
            cursor = QTextCursor(block)
            selection.cursor = cursor
            selections.append(selection)
        pane.setExtraSelections(selections)
