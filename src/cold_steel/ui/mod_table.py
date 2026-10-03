"""The mod list: a table model over a `Library`, and the filter in front of it."""

import json
from collections.abc import Sequence
from enum import IntEnum
from typing import Any, override

from PySide6.QtCore import (
    QAbstractTableModel,
    QMimeData,
    QModelIndex,
    QPersistentModelIndex,
    QSortFilterProxyModel,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QImage, QPixmap

from cold_steel.core.health import Health, Status, status
from cold_steel.core.library import Library
from cold_steel.core.mods import Mod
from cold_steel.store.playsets import Playset

type Index = QModelIndex | QPersistentModelIndex

MOD_ROLE = Qt.ItemDataRole.UserRole  # the Mod itself
SORT_ROLE = Qt.ItemDataRole.UserRole + 1

WARNING = QColor("#d9534f")
CAUTION = QColor("#c08a00")
HEALTHY = QColor("#3c9a3c")
MIME_TYPE = "application/x-cold-steel-mod-keys"


class Column(IntEnum):
    POSITION = 0
    NAME = 1
    HEALTH = 2
    VERSION = 3
    SUPPORTED = 4
    TAGS = 5
    SOURCE = 6


HEADERS = {
    Column.POSITION: "#",
    Column.NAME: "Name",
    Column.HEALTH: "Health",
    Column.VERSION: "Version",
    Column.SUPPORTED: "Made for",
    Column.TAGS: "Tags",
    Column.SOURCE: "Source",
}


class ModTableModel(QAbstractTableModel):
    # A mod's checkbox was clicked in a playset: key, turned on.
    enabled_toggled = Signal(str, bool)

    def __init__(self) -> None:
        super().__init__()
        self._library: Library | None = None
        self._mods: list[Mod] = []
        self._thumbnails: dict[str, QPixmap] = {}
        self._blank = QPixmap()  # shown until, or instead of, a thumbnail
        self._positions: dict[str, tuple[int, bool]] = {}  # key -> (position, enabled)
        self._health: dict[str, Health] | None = None  # None while being checked

    def set_library(self, library: Library, missing: Sequence[Mod] = ()) -> None:
        """`missing`: stand-ins for playset mods that aren't installed."""
        self.beginResetModel()
        self._library = library
        self._mods = [*library.mods, *missing]
        self._positions = {}
        self.endResetModel()

    def set_missing(self, missing: Sequence[Mod]) -> None:
        if self._library is not None:
            positions = self._positions
            self.set_library(self._library, missing)
            self._positions = positions

    def set_playset(self, playset: Playset | None) -> None:
        """Show load positions for this playset, or none for the full list."""
        self._positions = (
            {e.key: (i, e.enabled) for i, e in enumerate(playset.entries)} if playset else {}
        )
        if self._mods:
            self.dataChanged.emit(
                self.index(0, 0), self.index(len(self._mods) - 1, len(Column) - 1)
            )

    def set_thumbnails(self, images: dict[str, QImage], blank: QImage) -> None:
        self._blank = QPixmap.fromImage(blank)
        self._thumbnails = {key: QPixmap.fromImage(img) for key, img in images.items()}
        if self._mods:
            col = Column.NAME
            self.dataChanged.emit(
                self.index(0, col),
                self.index(len(self._mods) - 1, col),
                [Qt.ItemDataRole.DecorationRole],
            )

    def set_health(self, health: dict[str, Health] | None) -> None:
        """Each mod's problems, or None while they're being checked."""
        self._health = health
        if self._mods:
            col = Column.HEALTH
            self.dataChanged.emit(self.index(0, col), self.index(len(self._mods) - 1, col))

    def health(self, mod: Mod) -> Health:
        return self._health.get(mod.key, ()) if self._health else ()

    def health_status(self, mod: Mod) -> Status | None:
        """None while checking, or for a mod that isn't installed."""
        if self._health is None or mod.key not in self._health:
            return None
        return status(self._health[mod.key])

    def mod_at(self, row: int) -> Mod:
        return self._mods[row]

    @override
    def flags(self, index: Index) -> Qt.ItemFlag:
        flags = super().flags(index) | Qt.ItemFlag.ItemIsDropEnabled
        if index.isValid() and self._mods[index.row()].key in self._positions:
            flags |= Qt.ItemFlag.ItemIsDragEnabled
            if index.column() == Column.NAME:
                flags |= Qt.ItemFlag.ItemIsUserCheckable
        return flags

    @override
    def setData(self, index: Index, value: Any, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if role != Qt.ItemDataRole.CheckStateRole or not index.isValid():
            return False
        key = self._mods[index.row()].key
        if key not in self._positions:
            return False
        # The window changes the playset, then calls set_playset with the result.
        self.enabled_toggled.emit(key, Qt.CheckState(value) == Qt.CheckState.Checked)
        return True

    @override
    def supportedDropActions(self) -> Qt.DropAction:
        return Qt.DropAction.MoveAction

    @override
    def mimeTypes(self) -> list[str]:
        return [MIME_TYPE]

    @override
    def mimeData(self, indexes: Sequence[QModelIndex]) -> QMimeData:
        keys = list(dict.fromkeys(self._mods[i.row()].key for i in indexes if i.isValid()))
        data = QMimeData()
        data.setData(MIME_TYPE, json.dumps(keys).encode())
        return data

    def is_outdated(self, mod: Mod) -> bool:
        return self._library is not None and self._library.is_outdated(mod)

    def position(self, mod: Mod) -> tuple[int, bool] | None:
        return self._positions.get(mod.key)

    @override
    def rowCount(self, parent: Index = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._mods)

    @override
    def columnCount(self, parent: Index = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(Column)

    @override
    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return HEADERS[Column(section)]
        return None

    @override
    def data(self, index: Index, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        mod = self._mods[index.row()]
        col = Column(index.column())
        position = self._positions.get(mod.key)

        if role == MOD_ROLE:
            return mod
        if role == Qt.ItemDataRole.CheckStateRole and col == Column.NAME and position:
            return Qt.CheckState.Checked if position[1] else Qt.CheckState.Unchecked
        if role in (Qt.ItemDataRole.DisplayRole, SORT_ROLE):
            return self._text(mod, col, position, sort=role == SORT_ROLE)
        if role == Qt.ItemDataRole.DecorationRole and col == Column.NAME:
            return self._thumbnails.get(mod.key, self._blank)
        if role == Qt.ItemDataRole.ForegroundRole:
            if col == Column.SUPPORTED and self.is_outdated(mod):
                return WARNING
            if col == Column.NAME and mod.problem:
                return WARNING
            if col == Column.HEALTH:
                return {"error": WARNING, "warning": CAUTION, "ok": HEALTHY, None: None}[
                    self.health_status(mod)
                ]
            if not mod.installed or (position and not position[1]):
                return QColor(Qt.GlobalColor.gray)
        if role == Qt.ItemDataRole.FontRole and not mod.installed:
            font = QFont()
            font.setItalic(True)
            return font
        if role == Qt.ItemDataRole.ToolTipRole:
            return self._tooltip(mod, col, position)
        if role == Qt.ItemDataRole.TextAlignmentRole and col == Column.POSITION:
            return Qt.AlignmentFlag.AlignCenter
        return None

    def _text(self, mod: Mod, col: Column, position: tuple[int, bool] | None, *, sort: bool) -> Any:
        match col:
            case Column.POSITION:
                if position is None:
                    return 1 << 30 if sort else ""
                return position[0] if sort else str(position[0] + 1)
            case Column.NAME:
                return mod.name.casefold() if sort else mod.name
            case Column.HEALTH:
                return self._health_text(mod, sort=sort)
            case Column.VERSION:
                return mod.version
            case Column.SUPPORTED:
                return mod.supported_version
            case Column.TAGS:
                return ", ".join(mod.tags)
            case Column.SOURCE:
                if not mod.installed:
                    return "Not installed"
                return "Workshop" if mod.source == "workshop" else "Local"

    def _health_text(self, mod: Mod, *, sort: bool) -> Any:
        if self._health is None:
            return 1 << 30 if sort else ("…" if mod.installed else "")
        if mod.key not in self._health:
            return 1 << 30 if sort else ""
        errors, warnings = _counts(self._health[mod.key])
        if sort:  # errors first, then warnings, then OK
            return -(errors * 100_000 + warnings)
        if errors:
            return f"{errors} error{'s' if errors > 1 else ''}"
        if warnings:
            return f"{warnings} warning{'s' if warnings > 1 else ''}"
        return "OK"

    def _tooltip(self, mod: Mod, col: Column, position: tuple[int, bool] | None) -> str | None:
        if col == Column.HEALTH:
            if self._health is None:
                return "Checking this mod's files…" if mod.installed else None
            if mod.key not in self._health:
                return None
            errors, warnings = _counts(self._health[mod.key])
            if not errors and not warnings:
                return "No problems found."
            return f"{errors} error(s), {warnings} warning(s). Click to see them."
        if col == Column.SUPPORTED and self.is_outdated(mod) and self._library:
            return f"Made for {mod.supported_version}. The game is {self._library.game.version}."
        if col == Column.NAME:
            lines = [mod.name]
            if mod.problem:
                lines.append(mod.problem)
            if not mod.installed:
                lines.append("In this playset, but not installed. Unsubscribed, or deleted.")
            if position and not position[1]:
                lines.append("Turned off in this playset.")
            lines.append(mod.root or mod.archive or mod.key)
            return "\n".join(lines)
        return None


def _counts(issues: Health) -> tuple[int, int]:
    errors = sum(i.severity == "error" for i in issues)
    return errors, len(issues) - errors


# Stand-ins for a playset id in the playset filter.
ALL = ""  # no filter
NO_PLAYSET = "<none>"  # the mods in no playset; never a real id


class ModFilter(QSortFilterProxyModel):
    """Search and filters. Every setter re-filters at once.

    Dropping dragged mods here asks for a move: rows are shown in load order,
    so the drop point is "just before this mod", or the end.
    """

    # keys being moved, and the key they go before ("" for the end)
    move_requested = Signal(list, str)

    def __init__(self, model: ModTableModel) -> None:
        super().__init__()
        self._model = model
        self.setSourceModel(model)
        self.setSortRole(SORT_ROLE)
        self.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._text = ""
        self._tag = ""
        self._outdated_only = False
        self._problems_only = False
        self._member_of = ALL  # a playset id, ALL or NO_PLAYSET
        self._exclude = False  # hide those mods instead of showing only them
        self._playset: frozenset[str] | None = None  # None: the full list
        self._in_any_playset: frozenset[str] = frozenset()
        self._by_playset: dict[str, frozenset[str]] = {}

    def set_playsets(self, playsets: Sequence[Playset]) -> None:
        """Every playset, for the "in a playset" filters."""
        self.beginFilterChange()
        self._in_any_playset = frozenset(e.key for p in playsets for e in p.entries)
        self._by_playset = {p.id: frozenset(e.key for e in p.entries) for p in playsets}
        self.endFilterChange()

    @override
    def canDropMimeData(
        self, data: QMimeData, action: Qt.DropAction, row: int, column: int, parent: Index
    ) -> bool:
        return self._playset is not None and data.hasFormat(MIME_TYPE)

    @override
    def dropMimeData(
        self, data: QMimeData, action: Qt.DropAction, row: int, column: int, parent: Index
    ) -> bool:
        if not self.canDropMimeData(data, action, row, column, parent):
            return False
        keys = json.loads(bytes(data.data(MIME_TYPE).data()).decode())
        target = parent.row() if parent.isValid() else row  # onto a row, or between rows
        before = ""
        if 0 <= target < self.rowCount():
            before = self.index(target, 0).data(MOD_ROLE).key
        self.move_requested.emit(keys, before)
        # False: the move is done by changing the playset, so the view must not
        # remove the dragged rows itself.
        return False

    def set_text(self, text: str) -> None:
        self.beginFilterChange()
        self._text = text.strip().casefold()
        self.endFilterChange()

    def set_tag(self, tag: str) -> None:
        self.beginFilterChange()
        self._tag = tag
        self.endFilterChange()

    def set_outdated_only(self, on: bool) -> None:
        self.beginFilterChange()
        self._outdated_only = on
        self.endFilterChange()

    def set_problems_only(self, on: bool) -> None:
        self.beginFilterChange()
        self._problems_only = on
        self.endFilterChange()

    def set_membership(self, playset_id: str, exclude: bool) -> None:
        """Show only, or hide, the mods in a playset. `playset_id` can be ALL or NO_PLAYSET."""
        self.beginFilterChange()
        self._member_of = playset_id
        self._exclude = exclude
        self.endFilterChange()

    def set_playset(self, playset: Playset | None) -> None:
        self.beginFilterChange()
        self._playset = frozenset(e.key for e in playset.entries) if playset else None
        self.endFilterChange()

    @override
    def filterAcceptsRow(self, source_row: int, source_parent: Index) -> bool:
        mod = self._model.mod_at(source_row)
        if self._playset is None:
            if not mod.installed:
                return False
        elif mod.key not in self._playset:
            return False
        if self._text and self._text not in mod.name.casefold():
            return False
        if self._tag and self._tag not in mod.tags:
            return False
        if self._outdated_only and not self._model.is_outdated(mod):
            return False
        if self._problems_only and self._model.health_status(mod) in ("ok", None):
            return False
        if self._member_of == ALL:
            return True
        if self._member_of == NO_PLAYSET:
            inside = mod.key not in self._in_any_playset
        else:
            inside = mod.key in self._by_playset.get(self._member_of, frozenset())
        return inside != self._exclude
