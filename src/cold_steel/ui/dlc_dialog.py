"""Choose which DLC a playset loads."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from cold_steel.paradox.dlc import Dlc


class DlcDialog(QDialog):
    def __init__(
        self, dlcs: tuple[Dlc, ...], disabled: tuple[str, ...], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("DLC in this playset")
        self.resize(420, 560)

        self.list = QListWidget()
        off = set(disabled)
        for dlc in dlcs:
            item = QListWidgetItem(dlc.name)
            item.setData(Qt.ItemDataRole.UserRole, dlc.folder)
            item.setToolTip(dlc.file)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            on = dlc.folder not in off
            item.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
            self.list.addItem(item)

        all_on = QPushButton("All on")
        all_on.clicked.connect(lambda: self._set_all(Qt.CheckState.Checked))
        all_off = QPushButton("All off")
        all_off.clicked.connect(lambda: self._set_all(Qt.CheckState.Unchecked))
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        row = QHBoxLayout()
        row.addWidget(all_on)
        row.addWidget(all_off)
        row.addStretch()
        row.addWidget(buttons)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Unticked DLC are turned off when you play this playset."))
        layout.addWidget(self.list)
        layout.addLayout(row)

    def disabled(self) -> tuple[str, ...]:
        """The folders of the unticked DLC."""
        items = (self.list.item(i) for i in range(self.list.count()))
        return tuple(
            sorted(
                item.data(Qt.ItemDataRole.UserRole)
                for item in items
                if item.checkState() != Qt.CheckState.Checked
            )
        )

    def _set_all(self, state: Qt.CheckState) -> None:
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(state)
