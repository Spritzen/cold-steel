"""A playset's pinned mods: which copy each plays, and what Steam changed since."""

from collections.abc import Mapping

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cold_steel.core.library import Library
from cold_steel.core.snapshots import Drift, Snapshot
from cold_steel.store.playsets import Playset

KEY_ROLE = Qt.ItemDataRole.UserRole
# Shown in the file list before each path.
ADDED, REMOVED, CHANGED = "+ ", "- ", "~ "

type Checked = Mapping[str, tuple[Snapshot, Drift]]  # by snapshot id


def describe(drift: Drift | None) -> str:
    """What Steam did to a pinned mod, in a few words."""
    if drift is None:
        return "Checking…"
    if drift.gone:
        return "Gone from Steam. The copy still plays"
    if not drift.updated:
        return "Same as the copy"
    parts = [
        f"{len(files)} {word}"
        for files, word in (
            (drift.changed, "changed"),
            (drift.added, "added"),
            (drift.removed, "removed"),
        )
        if files
    ]
    version = f" (now {drift.version})" if drift.version else ""
    return f"Updated{version}: {', '.join(parts)}"


class PinsDialog(QDialog):
    # Copy these mods (Mod.keys) as they are on Steam now, and play the copies.
    pin_requested = Signal(tuple)
    # Play every mod from Steam's folder again.
    unpin_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.resize(820, 520)
        self.playset: Playset | None = None
        self.checked: Checked = {}

        self.summary = QLabel(wordWrap=True)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Mod", "Pinned copy", "On Steam now"])
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.itemSelectionChanged.connect(self._show_files)
        self.files = QListWidget()
        self.files_label = QLabel(wordWrap=True)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self.files_label)
        right_layout.addWidget(self.files)
        split = QSplitter()
        split.addWidget(self.tree)
        split.addWidget(right)
        split.setSizes([520, 300])

        self.pin_button = QPushButton("Pin all mods")
        self.pin_button.setToolTip("Save a copy of every Workshop mod that isn't pinned yet")
        self.pin_button.clicked.connect(self._pin_all)
        self.accept_button = QPushButton("Accept update")
        self.accept_button.setToolTip("Replace the selected mods' copies with Steam's version")
        self.accept_button.clicked.connect(self._accept)
        self.unpin_button = QPushButton("Unpin all")
        self.unpin_button.setToolTip("Play every mod from Steam's folder again")
        self.unpin_button.clicked.connect(self.unpin_requested)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        buttons = QHBoxLayout()
        buttons.addWidget(self.pin_button)
        buttons.addWidget(self.accept_button)
        buttons.addWidget(self.unpin_button)
        buttons.addStretch()
        buttons.addWidget(close)

        layout = QVBoxLayout(self)
        layout.addWidget(self.summary)
        layout.addWidget(split, 1)
        layout.addLayout(buttons)

    def set_state(self, playset: Playset, library: Library, checked: Checked) -> None:
        self.playset, self.checked = playset, checked
        self.setWindowTitle(f"Pinned versions of {playset.name}")
        names = {m.key: m.name for m in library.every_mod}
        pins = {p.key: p.snapshot for p in playset.pins}
        workshop = [e for e in playset.entries if e.key.startswith("workshop:")]
        installed = {m.key for m in library.mods}

        selected = {i.data(0, KEY_ROLE) for i in self.tree.selectedItems()}
        self.tree.clear()
        updates = 0
        for entry in workshop:
            snapshot_id = pins.get(entry.key)
            found = checked.get(snapshot_id) if snapshot_id else None
            name = names.get(entry.key) or entry.name or entry.key
            if snapshot_id is None:
                copy = "Not pinned: plays Steam's folder"
                now = "" if entry.key in installed else "Not installed"
            elif found is None:
                copy, now = f"Copy {snapshot_id[:8]}", describe(None)
            else:
                snapshot, drift = found
                version = f"{snapshot.version}, " if snapshot.version else ""
                copy = f"{version}saved {snapshot.taken}"
                now = describe(drift)
                updates += drift.updated
            item = QTreeWidgetItem(self.tree, [name, copy, now])
            item.setData(0, KEY_ROLE, entry.key)
            item.setSelected(entry.key in selected)

        unpinned = sum(e.key not in pins and e.key in installed for e in workshop)
        if not pins:
            text = (
                "Not pinned. Every mod plays from Steam's folder, which changes whenever an "
                "author updates. Pin the playset to keep playing the versions you have now."
            )
        else:
            text = (
                f"Pinned: {len(pins)} Workshop mod(s) play from copies saved by Cold Steel, "
                "even after Steam updates them or you unsubscribe."
            )
            if updates:
                text += f" {updates} have an update on Steam: select one to see what changed."
            if unpinned:
                text += f" {unpinned} aren't pinned yet: Pin all mods adds them."
        self.summary.setText(text)
        self.pin_button.setEnabled(unpinned > 0)
        self.pin_button.setText("Pin all mods" if not pins else "Pin the rest")
        self.unpin_button.setEnabled(bool(pins))
        self._show_files()

    def _drift(self, key: str) -> Drift | None:
        if self.playset is None:
            return None
        snapshot_id = next((p.snapshot for p in self.playset.pins if p.key == key), None)
        found = self.checked.get(snapshot_id) if snapshot_id else None
        return found[1] if found else None

    def _selected(self) -> list[str]:
        return [i.data(0, KEY_ROLE) for i in self.tree.selectedItems()]

    def _show_files(self) -> None:
        self.files.clear()
        keys = self._selected()
        updated = [k for k in keys if (d := self._drift(k)) is not None and d.updated]
        self.accept_button.setEnabled(bool(updated))
        if len(keys) != 1:
            self.files_label.setText("Select a pinned mod to see what Steam changed.")
            return
        drift = self._drift(keys[0])
        if drift is None or not drift.updated:
            self.files_label.setText(
                "Nothing to accept." if drift is not None else "This mod isn't pinned."
            )
            return
        self.files_label.setText(
            "Files Steam changed since the copy. Accepting replaces the copy with Steam's version."
        )
        for prefix, paths in (
            (CHANGED, drift.changed),
            (ADDED, drift.added),
            (REMOVED, drift.removed),
        ):
            self.files.addItems([prefix + p for p in paths])

    def _pin_all(self) -> None:
        if self.playset is None:
            return
        pinned = {p.key for p in self.playset.pins}
        keys = [
            e.key
            for e in self.playset.entries
            if e.key.startswith("workshop:") and e.key not in pinned
        ]
        self.pin_requested.emit(tuple(keys))

    def _accept(self) -> None:
        keys = [k for k in self._selected() if (d := self._drift(k)) is not None and d.updated]
        if keys:
            self.pin_requested.emit(tuple(keys))
