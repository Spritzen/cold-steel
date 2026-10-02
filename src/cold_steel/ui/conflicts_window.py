"""Where a playset's mods clash: a list of conflicts, and each one side by side.

Each conflict can be settled here: pick the version that wins, write your own,
or ignore it. The main window turns the choices into the patch mod.
"""

from collections.abc import Callable, Iterable

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPalette, QTextCursor, QTextFormat
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTextEdit,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cold_steel.core.compare import TEXT_SUFFIXES, Pair, Version, compare, version_text
from cold_steel.core.conflicts import FILE, Claim, Conflict, Found
from cold_steel.core.index import GAME, Index
from cold_steel.core.mods import base_key
from cold_steel.core.patch import check_own
from cold_steel.core.resolve import CHOSEN, NONE, STALE, ResolutionBook, State, choices_digest
from cold_steel.store.resolutions import Ignore
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
    # The user pressed Generate patch mod.
    generate_requested = Signal()
    # The user's own version was checked: the problems found, if any. For tests.
    own_checked = Signal(object)

    def __init__(self, tasks: TaskRunner, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Conflicts")
        self.resize(1400, 860)
        self._tasks = tasks
        self._compare_task: Task | None = None
        self._search_task: Task | None = None
        self._check_task: Task | None = None
        self.found: Found | None = None
        self.index: Index | None = None
        self.names: dict[str, str] = {}
        self.choices: ResolutionBook | None = None
        self.current: Conflict | None = None  # the conflict on screen
        self.editing: Conflict | None = None  # the conflict the editor is for
        self._by_key: dict[tuple[str, str], Conflict] = {}

        self.summary = QLabel(wordWrap=True)
        self.progress = QProgressBar(textVisible=False)
        self.progress.setMaximumWidth(260)
        self.progress.hide()
        refresh = QPushButton("Refresh")
        refresh.setToolTip("Find the conflicts again, after changing mods or the load order")
        refresh.clicked.connect(self.refresh_requested)
        self.generate_button = QPushButton("Generate patch mod")
        self.generate_button.setToolTip(
            "Write your choices as a mod that loads last, so the game uses them"
        )
        self.generate_button.clicked.connect(self.generate_requested)
        self.clear_all_button = QPushButton("Clear all choices…")
        self.clear_all_button.clicked.connect(self.clear_all)
        top = QHBoxLayout()
        top.addWidget(self.summary, 1)
        top.addWidget(self.progress)
        top.addWidget(refresh)
        top.addWidget(self.clear_all_button)
        top.addWidget(self.generate_button)
        self.patch_label = QLabel(wordWrap=True)

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
        self.state_box = QComboBox()
        self.state_box.addItem("Chosen or not", None)
        self.state_box.addItem("No choice yet", NONE)
        self.state_box.addItem("Chosen", CHOSEN)
        self.state_box.addItem("Needs another look", STALE)
        self.ignored_box = QCheckBox("Show ignored")
        for box in (self.group_box, self.type_box, self.mod_box, self.state_box):
            box.currentIndexChanged.connect(self._fill_tree)
        for check in (self.identical_box, self.game_box, self.ignored_box):
            check.toggled.connect(self._fill_tree)
        filters = QHBoxLayout()
        filters.addWidget(self.search, 1)
        filters.addWidget(self.group_box)
        filters.addWidget(self.type_box)
        filters.addWidget(self.mod_box)
        filters.addWidget(self.state_box)
        filters.addWidget(self.identical_box)
        filters.addWidget(self.game_box)
        filters.addWidget(self.ignored_box)

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

        # Settling the conflict on screen.
        self.choice = QLabel(wordWrap=True)
        self.use_left = QPushButton("Use left")
        self.use_left.setToolTip("Make the version on the left win")
        self.use_left.clicked.connect(lambda: self._choose(self.left_box.currentData()))
        self.use_right = QPushButton("Use right")
        self.use_right.setToolTip("Make the version on the right win")
        self.use_right.clicked.connect(lambda: self._choose(self.right_box.currentData()))
        self.keep = QPushButton("Keep the winner")
        self.keep.setToolTip("Keep the version that wins now, even if the load order changes")
        self.keep.clicked.connect(
            lambda: self._choose(self.current.winning if self.current else None)
        )
        self.own_button = QPushButton("Write my own…")
        self.own_button.setToolTip("Write a version of your own, starting from the right one")
        self.own_button.clicked.connect(self.start_own)
        self.clear_button = QPushButton("Clear choice")
        self.clear_button.clicked.connect(self.clear_choice)
        self.ignore_button = QToolButton()
        self.ignore_button.setText("Ignore")
        self.ignore_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.ignore_button.setMenu(QMenu(self.ignore_button))
        self.stop_ignoring_button = QPushButton("Stop ignoring")
        self.stop_ignoring_button.clicked.connect(self.stop_ignoring)
        actions = QHBoxLayout()
        for button in (
            self.use_left,
            self.use_right,
            self.keep,
            self.own_button,
            self.clear_button,
            self.ignore_button,
            self.stop_ignoring_button,
        ):
            actions.addWidget(button)
        actions.addStretch(1)

        # The merge editor: the user's own version, beside the two being compared.
        self.editor_title = QLabel(wordWrap=True)
        self.editor = QPlainTextEdit()
        self.editor.setFont(self.viewer.right.font())
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.editor.setTabStopDistance(self.viewer.right.tabStopDistance())
        self.editor_problems = QLabel(wordWrap=True)
        self.editor_problems.setStyleSheet("color: #c0392b;")
        self.editor_problems.hide()
        save = QPushButton("Save my version")
        save.clicked.connect(self.save_own)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.close_editor)
        editor_buttons = QHBoxLayout()
        editor_buttons.addStretch(1)
        editor_buttons.addWidget(cancel)
        editor_buttons.addWidget(save)
        self.editor_panel = QWidget()
        editor_layout = QVBoxLayout(self.editor_panel)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.addWidget(self.editor_title)
        editor_layout.addWidget(self.editor, 1)
        editor_layout.addWidget(self.editor_problems)
        editor_layout.addLayout(editor_buttons)
        self.editor_panel.hide()
        beside = QSplitter()
        beside.addWidget(self.viewer)
        beside.addWidget(self.editor_panel)
        beside.setStretchFactor(0, 2)
        beside.setStretchFactor(1, 1)

        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.addWidget(self.title)
        detail_layout.addWidget(self.reason)
        detail_layout.addWidget(self.rule)
        detail_layout.addLayout(choose)
        detail_layout.addWidget(self.choice)
        detail_layout.addLayout(actions)
        detail_layout.addWidget(self.same)
        detail_layout.addWidget(beside, 1)

        splitter = QSplitter()
        splitter.addWidget(self.tree)
        splitter.addWidget(detail)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([480, 920])

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.patch_label)
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
        self,
        found: Found,
        index: Index,
        names: dict[str, str],
        mod: str | None = None,
        choices: ResolutionBook | None = None,
    ) -> None:
        """Show a playset's conflicts, and the choices made for them. `mod` picks
        that mod in the mod filter."""
        self.progress.hide()
        self.found, self.index, self.choices = found, index, choices
        self._by_key = {(c.kind, c.key): c for c in found.conflicts}
        if self.editing is not None:
            # The editor stays open, on the same conflict as found this time.
            self.editing = self._by_key.get((self.editing.kind, self.editing.key))
            if self.editing is None:
                self.close_editor()
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
        self._show_patch_state()
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
        state = self.state_box.currentData()
        identical = self.identical_box.isChecked()
        game = self.game_box.isChecked()
        ignored = self.ignored_box.isChecked()
        return [
            c
            for c in self.found.conflicts
            if (identical or not c.identical)
            and (game or len(c.mods) > 1)
            and (kind is None or c.kind == kind)
            and (mod is None or mod in c.mods)
            and (state is None or self.state(c) == state)
            and (ignored or self.choices is None or self.choices.ignored_by(c) is None)
        ]

    def state(self, conflict: Conflict) -> State:
        if self.choices is None or self.index is None:
            return NONE
        return self.choices.state(conflict, self.index)

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
        item = QTreeWidgetItem(parent, [conflict.key, "", str(len(conflict.mods))])
        item.setData(0, ITEM_ROLE, conflict)
        self._label_item(item, conflict)

    def _label_item(self, item: QTreeWidgetItem, conflict: Conflict) -> None:
        """Show who wins: the user's choice if there is one, else the game's rule."""
        state = self.state(conflict)
        if state == CHOSEN:
            item.setText(1, "✓ " + self._choice_name(conflict))
            item.setToolTip(1, "Your choice. The patch mod makes it win.")
        elif state == STALE:
            item.setText(1, "⚠ Needs another look")
            item.setToolTip(1, "A version changed after you chose. Look again, then choose.")
        else:
            item.setText(1, self.names.get(conflict.winning.layer, conflict.winning.layer))
            item.setToolTip(1, conflict.reason)
        item.setToolTip(0, conflict.key)
        quiet = conflict.identical or (
            self.choices is not None and self.choices.ignored_by(conflict) is not None
        )
        for col in range(3):
            if quiet:
                item.setForeground(col, QColor(Qt.GlobalColor.gray))
            else:
                # No colour of our own, so the theme's text colour shows. An empty
                # QColor would be solid black, unreadable on a dark theme.
                item.setData(col, Qt.ItemDataRole.ForegroundRole, None)
        if conflict.identical:
            item.setToolTip(0, f"{conflict.key}\nIdentical: it doesn't matter which one wins.")
        elif quiet:
            item.setToolTip(0, f"{conflict.key}\nIgnored.")

    def _choice_name(self, conflict: Conflict) -> str:
        resolution = self.choices.get(conflict.kind, conflict.key) if self.choices else None
        if resolution is None:
            return ""
        if resolution.own:
            return "Your own version"
        return self.names.get(resolution.layer, resolution.layer)

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
        return self._by_key.get((kind, key))

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
        self.current = conflict
        self._show_choice()
        self.rule.setText(_rule_text(conflict))

    def _show_claims(
        self, title: str | None, claims: tuple[Claim, ...], reason: str, winner: int = -1
    ) -> None:
        self.current = None
        self._show_choice()
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

    # Choosing

    def _show_choice(self) -> None:
        """The choice for the conflict on screen, and the buttons that change it."""
        conflict, choices = self.current, self.choices
        settled = conflict is not None and choices is not None and len(conflict.mods) > 0
        for button in (self.use_left, self.use_right, self.keep, self.ignore_button):
            button.setEnabled(settled)
        self.own_button.setEnabled(settled and _editable(conflict))
        if not settled or conflict is None or choices is None:
            self.choice.clear()
            self.clear_button.setEnabled(False)
            self.stop_ignoring_button.hide()
            self.ignore_button.show()
            return
        state = self.state(conflict)
        self.clear_button.setEnabled(state != NONE)
        if state == CHOSEN:
            text = f"<b>Your choice:</b> {self._choice_name(conflict)}. The patch mod makes it win."
        elif state == STALE:
            text = (
                f"<b>Needs another look.</b> You chose {self._choice_name(conflict)}, but a "
                "version changed after that, so the patch mod leaves this out. Look again, "
                "then choose."
            )
        else:
            text = "No choice yet. The game uses the winner above."
        ignored = choices.ignored_by(conflict)
        if ignored is not None:
            text += " " + _ignore_text(ignored, self.names)
        self.choice.setText(text)
        self.ignore_button.setVisible(ignored is None)
        self.stop_ignoring_button.setVisible(ignored is not None)
        self._fill_ignore_menu(conflict)

    def _fill_ignore_menu(self, conflict: Conflict) -> None:
        menu = self.ignore_button.menu()
        menu.clear()
        options = [
            ("This conflict", Ignore(kind=conflict.kind, key=conflict.key)),
            (f"Every conflict in {kind_label(conflict.kind)}", Ignore(kind=conflict.kind)),
        ]
        order = self.found.order if self.found else ()
        mods = sorted(conflict.mods, key=lambda m: order.index(m) if m in order else len(order))
        options += [
            (f"Every conflict with {self.names.get(m, m)}", Ignore(mod=base_key(m))) for m in mods
        ]
        for text, rule in options:
            menu.addAction(text).triggered.connect(lambda _=False, r=rule: self.ignore(r))

    def _choose(self, claim: Claim | None) -> None:
        if claim is None or self.current is None or self.choices is None or self.index is None:
            return
        self.choices.choose(self.current, claim, self.index)
        self._choice_changed()

    def clear_choice(self) -> None:
        if self.current is None or self.choices is None:
            return
        self.choices.clear(self.current.kind, self.current.key)
        self._choice_changed()

    def clear_all(self) -> None:
        choices = self.choices
        if choices is None or not choices.resolutions:
            return
        count = len(choices.resolutions)
        if self.confirm(
            "Clear all choices",
            f"Clear all {count} choices for this playset? Generate the patch mod again "
            "afterwards to take them out of the game too.",
        ):
            choices.clear_all()
            self._relabel_all()
            self._show_choice()
            self._show_patch_state()

    def ignore(self, rule: Ignore) -> None:
        if self.choices is None:
            return
        self.choices.ignore(rule)
        self._fill_tree()

    def stop_ignoring(self) -> None:
        if self.current is None or self.choices is None:
            return
        rule = self.choices.ignored_by(self.current)
        if rule is not None:
            self.choices.stop_ignoring(rule)
            self._fill_tree()

    def _choice_changed(self) -> None:
        """Update what shows the choices, without rebuilding the list."""
        item = self.tree.currentItem()
        data = item.data(0, ITEM_ROLE) if item else None
        if item is not None and isinstance(data, Conflict):
            self._label_item(item, data)
        else:
            self._relabel_all()
        self._show_choice()
        self._show_patch_state()

    def _relabel_all(self) -> None:
        for n in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(n)
            for r in range(group.childCount() if group else 0):
                child = group.child(r) if group else None
                data = child.data(0, ITEM_ROLE) if child else None
                if child is not None and isinstance(data, Conflict):
                    self._label_item(child, data)

    def _show_patch_state(self) -> None:
        choices = self.choices
        self.generate_button.setEnabled(choices is not None)
        self.clear_all_button.setEnabled(choices is not None and bool(choices.resolutions))
        if choices is None:
            self.patch_label.clear()
            return
        count = len(choices.resolutions)
        if count == 0 and not choices.built:
            self.patch_label.setText(
                "Choose a winner for a conflict, then generate the patch mod so the game uses it."
            )
            return
        if choices.built == choices_digest(choices.resolutions):
            text = f"The patch mod is up to date with your {count} choice(s)."
        elif choices.built:
            text = "Your choices changed since the patch mod was made. Generate it again."
        else:
            text = f"{count} choice(s), not in the game yet. Generate the patch mod."
        stale = sum(
            self.state(c) == STALE
            for r in choices.resolutions
            if (c := self._by_key.get((r.kind, r.key))) is not None
        )
        if stale:
            text += f" {stale} need another look, and are left out until you choose again."
        self.patch_label.setText(text)

    # Writing your own version

    def start_own(self) -> None:
        """Open the editor beside the versions, starting from the user's own
        version if there is one, else from the version on the right."""
        conflict = self.current
        if conflict is None or not _editable(conflict):
            return
        self.editing = conflict
        self.editor_title.setText(f"<b>Your version of {conflict.key}</b>")
        self.editor_problems.hide()
        self.editor_panel.show()
        resolution = self.choices.get(conflict.kind, conflict.key) if self.choices else None
        right, index = self.right_box.currentData(), self.index
        if resolution is not None and resolution.own:
            self._start_editor(resolution.text)
        elif right is not None and index is not None:
            # Reading may mean opening a zip, so it runs off the main thread.
            self.editor.setPlainText("")
            self.editor.setEnabled(False)
            task = self._tasks.start(lambda ctx: version_text(index, right)[0])
            task.succeeded.connect(lambda text: self._start_editor(text, conflict))
            task.failed.connect(lambda error: self._show_problems([f"Couldn't read it: {error}"]))

    def _start_editor(self, text: str, conflict: Conflict | None = None) -> None:
        if conflict is not None and conflict is not self.editing:
            return  # the user has moved on
        self.editor.setEnabled(True)
        self.editor.setPlainText(text)
        self.editor.setFocus()

    def close_editor(self) -> None:
        self.editing = None
        self.editor_panel.hide()

    def save_own(self) -> None:
        """Check the user's version, off the main thread, then save it if it's sound."""
        conflict, index, text = self.editing, self.index, self.editor.toPlainText()
        if conflict is None or index is None or self.choices is None:
            return
        task = self._tasks.start(lambda ctx: check_own(conflict, text, index))
        self._check_task = task
        task.succeeded.connect(lambda problems: self._own_checked(conflict, text, problems, task))
        task.failed.connect(lambda error: self._show_problems([f"Checking failed: {error}"]))

    def _own_checked(self, conflict: Conflict, text: str, problems: list[str], task: Task) -> None:
        if task is not self._check_task or self.choices is None or self.index is None:
            return
        self._check_task = None
        self.own_checked.emit(problems)
        if problems:
            self._show_problems(problems)
            return
        self.choices.write_own(conflict, text, self.index)
        self.close_editor()
        if self.current is not None and self.current.key == conflict.key:
            self._choice_changed()
        else:
            self._relabel_all()
            self._show_patch_state()

    def _show_problems(self, problems: list[str]) -> None:
        self.editor_problems.setText("Not saved:\n" + "\n".join(problems))
        self.editor_problems.show()

    def confirm(self, title: str, text: str) -> bool:
        """Ask a yes/no question. Tests replace this."""
        answer = QMessageBox.question(self, title, text)
        return answer == QMessageBox.StandardButton.Yes

    def _compared(self, pair: Pair, task: Task) -> None:
        if task is not self._compare_task:
            return  # the user has moved on
        self._compare_task = None
        self.viewer.show_pair(pair)
        self.same.setVisible(
            pair.same and self.left_box.currentIndex() != self.right_box.currentIndex()
        )
        self.compared.emit(pair)


def _rule_text(conflict: Conflict) -> str:
    rule = conflict.rule
    if rule is None:
        return "Whole files: the mod loaded last replaces the others."
    checked = f"checked in game on {rule.checked}" if rule.checked else "not yet checked in game"
    if rule.folder:
        where = f"Rule for {rule.folder}/"
    else:
        folder = conflict.winning.path.rpartition("/")[0]
        where = f"{folder}/ has no rule of its own, so the default applies"
    return f"{where}: {rule.winner} wins. From {rule.source}, {checked}."


def _ignore_text(rule: Ignore, names: dict[str, str]) -> str:
    if rule.key:
        return "You chose to ignore this conflict."
    if rule.kind:
        return f"You chose to ignore every conflict in {kind_label(rule.kind)}."
    return f"You chose to ignore every conflict with {names.get(rule.mod, rule.mod)}."


def _editable(conflict: Conflict | None) -> bool:
    """Objects and text files can be rewritten. Pictures and sounds can't."""
    if conflict is None:
        return False
    return conflict.kind != FILE or conflict.key.lower().endswith(TEXT_SUFFIXES)


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
        removed, added = self._colours()
        self._show(self.left, self.left_label, pair.left, removed)
        self._show(self.right, self.right_label, pair.right, added)

    def _colours(self) -> tuple[QColor, QColor]:
        """Backgrounds for removed and added lines, to suit a light or dark theme."""
        dark = self.palette().color(QPalette.ColorRole.Base).lightness() < 128
        return QColor("#5c2b2b" if dark else "#fbe3e3"), QColor("#2b4d2f" if dark else "#e1f5e1")

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 (Qt override)
        super().changeEvent(event)
        if event.type() != QEvent.Type.PaletteChange:
            return
        # The theme changed: recolour the highlighted lines where they are.
        for pane, colour in zip((self.left, self.right), self._colours(), strict=True):
            selections = pane.extraSelections()
            for selection in selections:
                selection.format.setBackground(colour)
            pane.setExtraSelections(selections)

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
