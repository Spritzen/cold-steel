"""The errors from the last game run, grouped by the mod that caused them."""

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cold_steel.core.errors import GAME, ErrorGroup, ErrorReport, GameError
from cold_steel.ui.full_text import show_full_text

ERROR_ROLE = Qt.ItemDataRole.UserRole
NOT_LOADED = (
    "This mod wasn't loaded in this game. The game still reads every .mod file as it "
    "starts, and logs problems in them."
)
OVERRIDES_TIP = (
    "Lines like \u201cObject with key: x already exists\u201d. They say a mod replaced "
    "something the game or another mod defines, which is what mods do."
)


class ErrorsDialog(QDialog):
    # The user pressed Refresh: read error.log again.
    refresh_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Errors from the last game")
        self.resize(980, 640)
        self.report: ErrorReport | None = None

        self.summary = QLabel(wordWrap=True)
        self.stale = QLabel(
            "Your mod list changed after this game run, so some errors may be matched to "
            "the wrong mod. Play again for an exact list.",
            wordWrap=True,
        )
        self.stale.setStyleSheet("color: #d9534f;")
        self.stale.hide()
        self.overrides_box = QCheckBox("Show overrides")
        self.overrides_box.setToolTip(OVERRIDES_TIP)
        self.overrides_box.toggled.connect(self._fill_tree)

        self.tree = QTreeWidget()
        show_full_text(self.tree)
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Error", "Times", "File"])
        self.tree.setAlternatingRowColors(True)
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.resizeSection(0, 560)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.currentItemChanged.connect(self._show_detail)

        self.detail = QPlainTextEdit(readOnly=True)
        self.detail.setPlaceholderText("Choose an error to see all of it.")
        mono = QFont("monospace")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.detail.setFont(mono)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.tree)
        splitter.addWidget(self.detail)
        splitter.setSizes([440, 160])

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        refresh = buttons.addButton("Refresh", QDialogButtonBox.ButtonRole.ActionRole)
        refresh.clicked.connect(self.refresh_requested)

        layout = QVBoxLayout(self)
        layout.addWidget(self.summary)
        layout.addWidget(self.stale)
        layout.addWidget(self.overrides_box)
        layout.addWidget(splitter)
        layout.addWidget(buttons)

    def set_report(self, report: ErrorReport) -> None:
        self.report = report
        self.stale.setVisible(report.stale)
        self.overrides_box.setVisible(bool(report.overrides))
        self._fill_tree()

    def _fill_tree(self) -> None:
        report = self.report
        self.tree.clear()
        self.detail.clear()
        if report is None:
            return
        if not report.written:
            self.summary.setText(
                f"There's no error log yet. The game writes one each time it runs, "
                f"to {report.log_file}"
            )
            return
        when = datetime.fromtimestamp(report.written).strftime("%H:%M on %d %B")
        overrides = self.overrides_box.isChecked()
        groups = [g for g in report.groups if overrides or _shown_total(g, overrides)]
        mods = sum(g.key != GAME and g.loaded for g in groups)
        text = (
            f"{report.problems} errors from the game run at {when}. "
            f"{mods} loaded mod(s) caused some of them. Errors that name no mod file are "
            "under \u201cGame / unknown\u201d."
            if report.problems
            else f"No errors from the game run at {when}."
        )
        if report.overrides:
            text += (
                f" The log also has {report.overrides} overrides: a mod replacing something "
                "the game or another mod defines, which is normal."
                + ("" if overrides else " Tick Show overrides to list them.")
            )
        self.summary.setText(text)
        bold = QFont()
        bold.setBold(True)
        for group in groups:
            name = group.name if group.loaded else f"{group.name} (not loaded)"
            top = QTreeWidgetItem(self.tree, [name, str(_shown_total(group, overrides)), ""])
            top.setFont(0, bold)
            if not group.loaded:
                top.setToolTip(0, NOT_LOADED)
                for col in range(3):
                    top.setForeground(col, QColor(Qt.GlobalColor.gray))
            for error in group.errors:
                if error.override and not overrides:
                    continue
                first = error.text.splitlines()[0] if error.text else ""
                where = f"{error.file}:{error.line}" if error.line else error.file
                item = QTreeWidgetItem(top, [first, str(error.count), where])
                item.setData(0, ERROR_ROLE, error)
                item.setToolTip(0, error.text)
        if self.tree.topLevelItemCount() == 1:
            self.tree.expandAll()

    def _show_detail(self, item: QTreeWidgetItem | None) -> None:
        error: GameError | None = item.data(0, ERROR_ROLE) if item else None
        if error is None:
            self.detail.clear()
            return
        lines = [error.text, ""]
        if error.file:
            lines.append(f"File: {error.file}" + (f", line {error.line}" if error.line else ""))
        if error.code:
            lines.append(f"That line reads: {error.code}")
        times = f", {error.count} times" if error.count > 1 else ""
        lines.append(f"Logged at {error.time}{times}, by {error.source} in the game.")
        self.detail.setPlainText("\n".join(lines))


def _shown_total(group: ErrorGroup, overrides: bool) -> int:
    return group.total if overrides else group.total - group.overrides
