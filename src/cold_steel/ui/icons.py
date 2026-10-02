"""Small toolbar icons: the desktop's icon theme when it has them, or simple
drawn ones in the window's text colour when it doesn't."""

from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QApplication

type Draw = Callable[[QPainter], None]

SIZES = (16, 22, 32, 48)


def theme_icon(name: str, draw: Draw) -> QIcon:
    """The theme's icon called `name`, or `draw` on a 16 by 16 grid."""
    if QIcon.hasThemeIcon(name):
        return QIcon.fromTheme(name)
    color = QApplication.palette().color(QPalette.ColorRole.WindowText)
    icon = QIcon()
    for size in SIZES:
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(size / 16, size / 16)
        pen = QPen(color, 1.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        draw(painter)
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def draw_new(p: QPainter) -> None:
    p.drawLine(QPointF(8, 3), QPointF(8, 13))
    p.drawLine(QPointF(3, 8), QPointF(13, 8))


def draw_copy(p: QPainter) -> None:
    p.drawRoundedRect(QRectF(5.5, 5.5, 8, 8), 1, 1)
    p.drawPolyline([QPointF(10.5, 2.5), QPointF(2.5, 2.5), QPointF(2.5, 10.5)])


def draw_rename(p: QPainter) -> None:
    pencil = QPainterPath(QPointF(11, 2.5))
    pencil.lineTo(13.5, 5)
    pencil.lineTo(5.5, 13)
    pencil.lineTo(2.5, 13.5)
    pencil.lineTo(3, 10.5)
    pencil.closeSubpath()
    p.drawPath(pencil)
    p.drawLine(QPointF(9.5, 4), QPointF(12, 6.5))


def draw_delete(p: QPainter) -> None:
    p.drawLine(QPointF(2.5, 4), QPointF(13.5, 4))
    p.drawPolyline([QPointF(6, 4), QPointF(6.5, 2.5), QPointF(9.5, 2.5), QPointF(10, 4)])
    p.drawPolyline([QPointF(4, 4), QPointF(4.8, 13.5), QPointF(11.2, 13.5), QPointF(12, 4)])
    p.drawLine(QPointF(6.7, 6.5), QPointF(6.9, 11))
    p.drawLine(QPointF(9.3, 6.5), QPointF(9.1, 11))
