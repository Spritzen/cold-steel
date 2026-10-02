"""Hovering over text that's cut off in a table, tree or list shows all of it.

Every hover text in these views is wrapped to TIP_CHARS characters, so a long
one is a few lines, not one line across the screen.
"""

from PySide6.QtCore import QEvent, QModelIndex, QPersistentModelIndex, Qt
from PySide6.QtGui import QFont, QFontMetrics, QHelpEvent, QTextLayout, QTextOption
from PySide6.QtWidgets import (
    QAbstractItemView,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolTip,
)

# The widest a hover text gets, in average characters.
TIP_CHARS = 70


class FullTextDelegate(QStyledItemDelegate):
    """Draws cells as usual. On hover, a cell whose text doesn't fit shows it
    in full, above the cell's own tooltip if it has one."""

    def helpEvent(  # noqa: N802 (Qt's name)
        self,
        event: QHelpEvent,
        view: QAbstractItemView,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> bool:
        if event is None or event.type() != QEvent.Type.ToolTip or not index.isValid():
            return super().helpEvent(event, view, option, index)
        text = str(index.data() or "")
        own = str(index.data(Qt.ItemDataRole.ToolTipRole) or "")
        if text and not fits(view, option, index) and not own.startswith(text):
            own = "\n".join(t for t in (text, own) if t)
        if not own:
            QToolTip.hideText()
            return True
        font = QToolTip.font()
        tip = wrap(font, own, TIP_CHARS * QFontMetrics(font).averageCharWidth())
        QToolTip.showText(event.globalPos(), tip, view, option.rect)
        return True


def fits(
    view: QAbstractItemView,
    option: QStyleOptionViewItem,
    index: QModelIndex | QPersistentModelIndex,
) -> bool:
    """Whether the cell's text shows in full, without being cut off."""
    opt = QStyleOptionViewItem(option)
    delegate = view.itemDelegateForIndex(index)
    if isinstance(delegate, QStyledItemDelegate):
        delegate.initStyleOption(opt, index)
    style = view.style()
    rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, opt, view)
    # The text is drawn inset by this much on each side.
    margin = style.pixelMetric(QStyle.PixelMetric.PM_FocusFrameHMargin, None, view) + 1
    width = rect.width() - 2 * margin
    font = opt.font
    metrics = QFontMetrics(font)
    if opt.features & QStyleOptionViewItem.ViewItemFeature.WrapText:
        return line_count(font, opt.text, width) * metrics.lineSpacing() <= rect.height()
    paragraphs = opt.text.split("\n")
    widest = max(metrics.horizontalAdvance(p) for p in paragraphs)
    return widest <= width and len(paragraphs) * metrics.lineSpacing() <= rect.height()


def line_count(font: QFont, text: str, width: int) -> int:
    """How many lines `text` takes when wrapped to `width`, as a view wraps it."""
    return len(_lines(font, text, width))


def wrap(font: QFont, text: str, width: int) -> str:
    """`text` with line breaks added, so no line is wider than `width`."""
    return "\n".join(line.rstrip() for line in _lines(font, text, width))


def _lines(font: QFont, text: str, width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        layout = QTextLayout(paragraph, font)
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(option)
        layout.beginLayout()
        while (line := layout.createLine()).isValid():
            line.setLineWidth(width)
            lines.append(paragraph[line.textStart() : line.textStart() + line.textLength()])
        layout.endLayout()
    return lines


def show_full_text(*views: QAbstractItemView) -> None:
    """Give each view a delegate that shows cut-off text on hover."""
    for view in views:
        view.setItemDelegate(FullTextDelegate(view))
