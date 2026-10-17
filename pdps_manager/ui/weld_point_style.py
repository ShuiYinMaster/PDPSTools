"""Shared semantic symbols and high-contrast table selection."""

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import (
    QColor, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap,
    QPolygonF, QRadialGradient,
)
from PyQt5.QtWidgets import QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem


LAYER_COLORS = {2: "#2563eb", 3: "#ea8a23", 4: "#11956f"}


def draw_weld_marker(painter, center, layer, size=5., three_d=True, outline=None):
    """Projected 2T sphere, 3T cube, 4T cone; fallback is a 2T sphere."""
    painter.save()
    layer = layer if layer in LAYER_COLORS else 2
    color = QColor(LAYER_COLORS[layer])
    painter.setPen(QPen(QColor(outline) if outline else color.darker(125), 1.2))
    painter.setBrush(color)
    x, y = center.x(), center.y()
    if layer == 3:
        if three_d:
            top = QPointF(x, y-size)
            left, right = QPointF(x-size, y-size*.45), QPointF(x+size, y-size*.45)
            middle = QPointF(x, y+size*.1)
            bottom = QPointF(x, y+size)
            for polygon, shade in (
                ([top, right, middle, left], color.lighter(135)),
                ([left, middle, bottom, QPointF(x-size, y+size*.45)], color),
                ([middle, right, QPointF(x+size, y+size*.45), bottom], color.darker(125)),
            ):
                painter.setBrush(shade)
                painter.drawPolygon(QPolygonF(polygon))
        else:
            painter.drawRect(QRectF(x-size, y-size, size*2, size*2))
    elif layer == 4:
        body = QPainterPath(QPointF(x, y-size*1.2))
        body.lineTo(x-size, y+size*.65)
        body.quadTo(x, y+size*1.15, x+size, y+size*.65)
        body.closeSubpath()
        gradient = QRadialGradient(QPointF(x-size*.4, y-size*.1), size*2)
        gradient.setColorAt(0, color.lighter(160))
        gradient.setColorAt(1, color.darker(120))
        painter.setBrush(gradient)
        painter.drawPath(body)
        painter.setBrush(color.darker(110))
        painter.drawEllipse(QRectF(x-size, y+size*.4, size*2, size*.5))
    elif layer == 2:
        if three_d:
            gradient = QRadialGradient(QPointF(x-size*.35, y-size*.35), size*1.5)
            gradient.setColorAt(0, color.lighter(180))
            gradient.setColorAt(1, color.darker(130))
            painter.setBrush(gradient)
        painter.drawEllipse(center, size, size)
    painter.restore()


_ICONS = {}


def layer_icon(layer):
    if layer not in _ICONS:
        icon = QIcon()
        for mode, outline in ((QIcon.Normal, None), (QIcon.Selected, "#ffffff")):
            pixmap = QPixmap(22, 22)
            pixmap.fill(Qt.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.Antialiasing)
            draw_weld_marker(painter, QPointF(11, 11), layer, 6., outline=outline)
            painter.end()
            icon.addPixmap(pixmap, mode)
        _ICONS[layer] = icon
    return _ICONS[layer]


class StrongPointSelectionDelegate(QStyledItemDelegate):
    """Keep graph-selected rows conspicuous even while the plot owns focus."""

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        size.setHeight(max(size.height(), 24))
        return size

    def paint(self, painter, option, index):
        if not option.state & QStyle.State_Selected:
            super().paint(painter, option, index)
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.font.setBold(True)
        for group in (QPalette.Active, QPalette.Inactive, QPalette.Disabled):
            opt.palette.setColor(group, QPalette.Highlight, QColor("#123f78"))
            opt.palette.setColor(group, QPalette.HighlightedText, QColor("#ffffff"))
        painter.save()
        widget = opt.widget
        (widget.style() if widget else QApplication.style()).drawControl(
            QStyle.CE_ItemViewItem, opt, painter, widget)
        painter.setPen(QPen(QColor("#ffce3a"), 1.5))
        painter.drawLine(opt.rect.topLeft(), opt.rect.topRight())
        painter.drawLine(opt.rect.bottomLeft(), opt.rect.bottomRight())
        if widget and index.column() == widget.header().logicalIndex(0):
            painter.fillRect(opt.rect.left(), opt.rect.top(), 4, opt.rect.height(), QColor("#ffce3a"))
        painter.restore()
