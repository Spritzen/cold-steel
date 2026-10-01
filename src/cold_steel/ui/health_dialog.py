"""One mod's problems, grouped by file."""

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QStyle,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cold_steel.core.health import Health
from cold_steel.core.mods import Mod


class HealthDialog(QDialog):
    def __init__(self, mod: Mod, issues: Health, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Problems in {mod.name}")
        self.resize(820, 480)

        errors = sum(i.severity == "error" for i in issues)
        summary = QLabel(
            f"{errors} error(s) and {len(issues) - errors} warning(s). "
            "Errors make the game ignore part of this mod. Warnings may not matter.",
            wordWrap=True,
        )

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Problem", "Line", "The line reads"])
        self.tree.setWordWrap(True)
        self.tree.setUniformRowHeights(False)
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.resizeSection(0, 460)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        style = self.style()
        icons = {
            "error": style.standardIcon(QStyle.StandardPixmap.SP_MessageBoxCritical),
            "warning": style.standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning),
        }
        bold = QFont()
        bold.setBold(True)

        files: dict[str, QTreeWidgetItem] = {}
        for issue in issues:
            parent_item = files.get(issue.file)
            if parent_item is None:
                parent_item = QTreeWidgetItem(self.tree, [issue.file or "This mod"])
                parent_item.setFont(0, bold)
                parent_item.setFirstColumnSpanned(True)
                parent_item.setExpanded(True)
                files[issue.file] = parent_item
            item = QTreeWidgetItem(
                parent_item, [issue.text, str(issue.line) if issue.line else "", issue.detail]
            )
            item.setIcon(0, icons[issue.severity])
            item.setToolTip(0, issue.text)
            item.setToolTip(2, issue.detail)
        self.tree.expandAll()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        folder = mod.root or (str(Path(mod.archive).parent) if mod.archive else "")
        if folder:
            open_folder = buttons.addButton(
                "Open mod folder", QDialogButtonBox.ButtonRole.ActionRole
            )
            open_folder.clicked.connect(
                lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
            )

        layout = QVBoxLayout(self)
        layout.addWidget(summary)
        layout.addWidget(self.tree)
        layout.addWidget(buttons)
