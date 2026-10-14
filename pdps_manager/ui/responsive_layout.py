"""Wrapping action rows and tables that fit the available viewport width."""

from PyQt5.QtCore import QRect, QSize, Qt, QTimer
from PyQt5.QtWidgets import QLayout, QHeaderView, QTreeWidget


class FlowLayout(QLayout):
    """Place controls from left to right, wrapping instead of widening a page."""

    def __init__(self, parent=None, spacing=6):
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Horizontal)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QRect(0, 0, width, 0), measure_only=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            if not item.isEmpty():
                size = size.expandedTo(item.minimumSize())
        left, top, right, bottom = self.getContentsMargins()
        return size + QSize(left + right, top + bottom)

    def _arrange(self, rect, measure_only=False):
        left, top, right, bottom = self.getContentsMargins()
        area = rect.adjusted(left, top, -right, -bottom)
        x, y, row_height = area.x(), area.y(), 0
        for item in self._items:
            if item.isEmpty():
                continue
            size = item.sizeHint().expandedTo(item.minimumSize())
            width = min(size.width(), max(area.width(), item.minimumSize().width()))
            if row_height and x + width > area.x() + area.width():
                x = area.x()
                y += row_height + self.spacing()
                row_height = 0
            height = (item.heightForWidth(width) if item.hasHeightForWidth()
                      else size.height())
            if not measure_only:
                item.setGeometry(QRect(x, y, width, height))
            x += width + self.spacing()
            row_height = max(row_height, height)
        return y + row_height - rect.y() + bottom


class FittedTreeWidget(QTreeWidget):
    """Keep default column widths within the viewport; show full values on hover."""

    def __init__(self, weights, parent=None):
        super().__init__(parent)
        self._weights = tuple(weights)
        self._fit_pending = False
        self._fitting = False
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.timeout.connect(self._fit_columns)
        self.setColumnCount(len(weights))
        self.setTextElideMode(Qt.ElideMiddle)
        self.header().setStretchLastSection(False)
        self.header().setMinimumSectionSize(20)
        self.header().setSectionResizeMode(QHeaderView.Interactive)
        self.model().rowsInserted.connect(self._schedule_fit)
        self.model().rowsRemoved.connect(self._schedule_fit)
        self.model().modelReset.connect(self._schedule_fit)
        self.itemChanged.connect(lambda item, col: item.setToolTip(col, item.text(col)))

    def addTopLevelItem(self, item):
        for column in range(self.columnCount()):
            if not item.toolTip(column):
                item.setToolTip(column, item.text(column))
        super().addTopLevelItem(item)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_columns()

    def showEvent(self, event):
        super().showEvent(event)
        self._schedule_fit()

    def _schedule_fit(self, *_args):
        if not self._fit_pending:
            self._fit_pending = True
            self._fit_timer.start(0)

    def _fit_columns(self):
        self._fit_pending = False
        if self._fitting:
            return
        self._fitting = True
        try:
            columns = [col for col in range(self.columnCount()) if not self.isColumnHidden(col)]
            weight_sum = sum(self._weights[col] for col in columns)
            if not weight_sum:
                return
            available = max(0, self.viewport().width() - 2)
            widths = [int(available * self._weights[col] / weight_sum) for col in columns]
            widths[-1] += available - sum(widths)
            for column, width in zip(columns, widths):
                self.header().resizeSection(column, width)
        finally:
            self._fitting = False
