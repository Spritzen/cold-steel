"""The mod list: a table model over a `Library`, and the filter in front of it."""

from enum import IntEnum
from typing import Any, override

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPersistentModelIndex,
    QSortFilterProxyModel,
    Qt,
)
from PySide6.QtGui import QColor, QFont, QImage, QPixmap

from cold_steel.core.library import Library, Playset
from cold_steel.core.mods import Mod

type Index = QModelIndex | QPersistentModelIndex

MOD_ROLE = Qt.ItemDataRole.UserRole  # the Mod itself
SORT_ROLE = Qt.ItemDataRole.UserRole + 1

WARNING = QColor("#d9534f")


class Column(IntEnum):
    POSITION = 0
    NAME = 1
    VERSION = 2
    SUPPORTED = 3
    TAGS = 4
    SOURCE = 5


HEADERS = {
    Column.POSITION: "#",
    Column.NAME: "Name",
    Column.VERSION: "Version",
    Column.SUPPORTED: "Made for",
    Column.TAGS: "Tags",
    Column.SOURCE: "Source",
}


class ModTableModel(QAbstractTableModel):
    def __init__(self) -> None:
        super().__init__()
        self._library: Library | None = None
        self._mods: list[Mod] = []
        self._thumbnails: dict[str, QPixmap] = {}
        self._blank = QPixmap()  # shown until, or instead of, a thumbnail
        self._positions: dict[str, tuple[int, bool]] = {}  # key -> (position, enabled)

    def set_library(self, library: Library) -> None:
        self.beginResetModel()
        self._library = library
        self._mods = [*library.mods, *library.missing]
        self._positions = {}
        self.endResetModel()

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

    def mod_at(self, row: int) -> Mod:
        return self._mods[row]

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
        if role in (Qt.ItemDataRole.DisplayRole, SORT_ROLE):
            return self._text(mod, col, position, sort=role == SORT_ROLE)
        if role == Qt.ItemDataRole.DecorationRole and col == Column.NAME:
            return self._thumbnails.get(mod.key, self._blank)
        if role == Qt.ItemDataRole.ForegroundRole:
            if col == Column.SUPPORTED and self.is_outdated(mod):
                return WARNING
            if col == Column.NAME and mod.problem:
                return WARNING
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

    def _tooltip(self, mod: Mod, col: Column, position: tuple[int, bool] | None) -> str | None:
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


class Membership(IntEnum):
    ANY = 0
    IN_A_PLAYSET = 1
    IN_NO_PLAYSET = 2


class ModFilter(QSortFilterProxyModel):
    """Search and filters. Every setter re-filters at once."""

    def __init__(self, model: ModTableModel) -> None:
        super().__init__()
        self._model = model
        self.setSourceModel(model)
        self.setSortRole(SORT_ROLE)
        self.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._text = ""
        self._tag = ""
        self._outdated_only = False
        self._membership = Membership.ANY
        self._playset: frozenset[str] | None = None  # None: the full list
        self._in_any_playset: frozenset[str] = frozenset()

    def set_library(self, library: Library) -> None:
        self.beginFilterChange()
        self._in_any_playset = frozenset(e.key for p in library.playsets for e in p.entries)
        self.endFilterChange()

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

    def set_membership(self, membership: Membership) -> None:
        self.beginFilterChange()
        self._membership = membership
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
        if self._membership == Membership.IN_A_PLAYSET:
            return mod.key in self._in_any_playset
        if self._membership == Membership.IN_NO_PLAYSET:
            return mod.key not in self._in_any_playset
        return True
