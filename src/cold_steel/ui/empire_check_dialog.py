"""Check all lists: every empire list, and which of its empires won't work, and why."""

from collections.abc import Mapping

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cold_steel.core.empire_check import CheckReport, ListReport
from cold_steel.ui.full_text import show_full_text

OK, WARNING, NOTE = "✓", "⚠", "ℹ"  # noqa: RUF001 (the information sign is meant)
COLUMNS = ["List or empire", "What's wrong"]


def list_summary(found: ListReport) -> str:
    """The list's own row: a word on the whole list. Each problem has a row below."""
    if not found.count:
        return "No empires"
    if found.problem:
        return f"{WARNING} Play won't give the game this list"
    if found.empires:
        return f"{WARNING} {len(found.empires)} of {found.count} empire(s) marked"
    return f"{OK} All {found.count} empire(s) work here"


class CheckDialog(QDialog):
    # Check every list again.
    check_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Check all empire lists")
        self.resize(1200, 560)
        self.summary = QLabel(wordWrap=True)
        self.tree = QTreeWidget()
        show_full_text(self.tree)
        self.tree.setHeaderLabels(COLUMNS)
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)

        self.again = QPushButton("Check again")
        self.again.clicked.connect(self.check_requested)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        buttons = QHBoxLayout()
        buttons.addWidget(self.again)
        buttons.addStretch()
        buttons.addWidget(close)

        layout = QVBoxLayout(self)
        layout.addWidget(self.summary)
        layout.addWidget(self.tree, 1)
        layout.addLayout(buttons)

    def set_state(
        self, report: CheckReport | None, names: Mapping[str, str], *, checking: bool = False
    ) -> None:
        """Show a check's report. `names` gives each list's name by owner."""
        self.again.setEnabled(not checking)
        if report is None:
            self.summary.setText("Checking every list against the game and your mods…")
            self.tree.clear()
            return
        lists = sorted(report.lists, key=lambda r: names.get(r.owner, r.owner).casefold())
        marked = report.marked
        sentences = [
            f"{marked} empire(s) won't work as they are."
            if marked
            else "Every empire works in the playsets that use its list."
        ]
        sentences.append(
            "Each list is checked against the game, every installed mod, and the mods of "
            "each playset that plays it. The game's own rules, such as ethics points or "
            "civics that rule each other out, are checked by the game in its empire creator."
        )
        if checking:
            sentences.append("Checking again…")
        self.summary.setText(" ".join(sentences))

        self.tree.clear()
        for found in lists:
            top = QTreeWidgetItem(
                self.tree, [names.get(found.owner, found.owner), list_summary(found)]
            )
            # One row per problem, so each can be read whole (or on hover).
            if found.problem:
                QTreeWidgetItem(top, ["", f"{WARNING} {found.problem}"])
            if found.played:
                QTreeWidgetItem(top, ["", f"{NOTE} {found.played}"])
            for empire in found.empires:
                for n, problem in enumerate(empire.problems):
                    name = f"{WARNING} {empire.name}" if n == 0 else ""
                    QTreeWidgetItem(top, [name, problem.text])
            top.setExpanded(top.childCount() > 0)
