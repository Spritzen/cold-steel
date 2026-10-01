"""The errors from the last game run, grouped by the mod that caused them."""

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
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

from cold_steel.core.errors import GAME, ErrorReport, GameError

ERROR_ROLE = Qt.ItemDataRole.UserRole


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

        self.tree = QTreeWidget()
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
        layout.addWidget(splitter)
        layout.addWidget(buttons)

    def set_report(self, report: ErrorReport) -> None:
        self.report = report
        self.tree.clear()
        self.detail.clear()
        self.stale.setVisible(report.stale)
        if not report.written:
            self.summary.setText(
                f"There's no error log yet. The game writes one each time it runs, "
                f"to {report.log_file}"
            )
            return
        when = datetime.fromtimestamp(report.written).strftime("%H:%M on %d %B")
        mods = sum(g.key != GAME for g in report.groups)
        self.summary.setText(
            f"{report.total} errors from the game run at {when}. "
            f"{mods} mod(s) caused some of them. Errors that name no mod file are under "
            "“Game / unknown”."
            if report.total
            else f"No errors from the game run at {when}."
        )
        bold = QFont()
        bold.setBold(True)
        for group in report.groups:
            top = QTreeWidgetItem(self.tree, [group.name, str(group.total), ""])
            top.setFont(0, bold)
            for error in group.errors:
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
