"""A built mod's report: where every file came from, and whether it plays like
the playset."""

from PySide6.QtWidgets import (
    QDialog,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cold_steel.core.build import BuildRecord
from cold_steel.core.index import GAME
from cold_steel.ui.conflicts_window import GAME_NAME, kind_label

# The tree shows this many files at most; the filter narrows it down.
SHOWN = 5000


class BuildDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.resize(900, 600)
        self.record: BuildRecord | None = None
        self.summary = QLabel(wordWrap=True)
        self.search = QLineEdit(placeholderText="Filter by file or mod", clearButtonEnabled=True)
        self.search.textChanged.connect(self._fill)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["File", "From", "Replaced"])
        self.tree.setRootIsDecorated(False)
        self.tree.setSortingEnabled(True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        layout = QVBoxLayout(self)
        layout.addWidget(self.summary)
        layout.addWidget(self.search)
        layout.addWidget(self.tree, 1)

    def set_record(self, record: BuildRecord) -> None:
        self.record = record
        self.setWindowTitle(f"Build of {record.name}")
        text = (
            f"<b>Cold Steel build: {record.name}</b>, built {record.built} from "
            f"{len(record.order)} mod(s): {len(record.files)} files, {record.copied} of them "
            "copied this time. "
        )
        if record.mismatches:
            shown = [
                f"{kind_label(m.kind)} {m.key}: {self._name(m.got) or 'missing'} instead of "
                f"{self._name(m.expected)}"
                for m in record.mismatches[:10]
            ]
            more = len(record.mismatches) - len(shown)
            text += (
                f"<br><span style='color:#d9534f'><b>{len(record.mismatches)} clash(es) "
                "would play differently from the playset:</b><br>"
                + "<br>".join(shown)
                + (f"<br>…and {more} more" if more > 0 else "")
                + "</span>"
            )
        else:
            text += "Every clash has the same winner as in the playset."
        text += (
            "<br><br>It holds other authors' work, so it's for your own use. Don't upload it "
            "to the Workshop."
        )
        self.summary.setText(text)
        self._fill()

    def _name(self, key: str) -> str:
        if not key:
            return ""
        if key == GAME:
            return GAME_NAME
        return self.record.names.get(key, key) if self.record else key

    def _fill(self) -> None:
        self.tree.clear()
        if self.record is None:
            return
        text = self.search.text().casefold()
        self.tree.setSortingEnabled(False)
        shown = 0
        for path, file in sorted(self.record.files.items()):
            name = self._name(file.layer)
            if text and text not in path.casefold() and text not in name.casefold():
                continue
            replaced = ", ".join(self._name(k) for k in file.replaced)
            item = QTreeWidgetItem(self.tree, [path, name, replaced])
            if file.source != path:
                item.setToolTip(0, f"{file.source} in {name}")
            shown += 1
            if shown >= SHOWN:
                break
        self.tree.setSortingEnabled(True)
