"""Interactive, dependency-free Qt projection of point records in millimeters."""

import math

from PyQt5.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton,
    QFileDialog, QMessageBox, QToolTip, QSizePolicy, QPlainTextEdit, QToolButton,
    QTabWidget, QTreeWidget, QTreeWidgetItem, QHeaderView, QAbstractItemView,
)

from ..core.weld_layers import weld_layer, layer_label, layer_counts
from .weld_point_style import LAYER_COLORS, draw_weld_marker, layer_icon


PLANES = {"XZ": (0, 2), "XY": (0, 1), "YZ": (1, 2)}


class PointProjectionCanvas(QWidget):
    point_clicked = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(240, 180)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.plane = "XZ"
        self.points = []
        self.invalid_count = 0
        self.selected_ids = set()
        self._center = (0., 0.)
        self._scale = 1.
        self._signature = None
        self._press_pos = self._last_pos = None
        self._moved = False
        self._last_hit_pos = None
        self._last_hits = []
        self._hit_cycle = 0
        self._manual_view = False
        self._pivot = (0., 0., 0.)
        self._yaw = math.radians(-35)
        self._pitch = math.radians(25)
        self._drag_button = None

    def set_points(self, rows, row_ids, duplicate_ids=(), dataset=""):
        if len(rows) != len(row_ids) or len(set(row_ids)) != len(row_ids):
            raise ValueError("图表行标识必须与数据一一对应且唯一")
        points = []
        invalid = 0
        for row_id, row in zip(row_ids, rows):
            try:
                xyz = tuple(float(row[axis]) for axis in ("X", "Y", "Z"))
                if not all(math.isfinite(value) for value in xyz):
                    raise ValueError("non-finite coordinate")
            except (KeyError, TypeError, ValueError, OverflowError):
                invalid += 1
                continue
            points.append({"id": row_id, "xyz": xyz, "row": row,
                           "duplicate": row_id in duplicate_ids, "layer": weld_layer(row)})
        signature = (dataset, tuple((p["id"], p["xyz"]) for p in points))
        changed = signature != self._signature
        self._signature = signature
        self.points, self.invalid_count = points, invalid
        self.selected_ids.intersection_update(p["id"] for p in points)
        self._last_hits = []
        if changed:
            self._pivot = tuple((min(p["xyz"][a] for p in points) + max(p["xyz"][a] for p in points))/2
                                for a in range(3)) if points else (0., 0., 0.)
            self.fit_points()
        self.update()

    def set_selection(self, row_ids):
        self.selected_ids = set(row_ids) & {p["id"] for p in self.points}
        if len(self.selected_ids) == 1:
            point = next(p for p in self.points if p["id"] in self.selected_ids)
            if not self.plot_rect().contains(self._project(point["xyz"])):
                self._center = self._view_coordinates(point["xyz"])[:2]
        self.update()

    def set_plane(self, plane):
        if plane not in PLANES and plane != "3D":
            raise ValueError(plane)
        self.plane = plane
        self._last_hits = []
        self.fit_points()

    def reset_view(self):
        if self.plane == "3D":
            self._yaw, self._pitch = math.radians(-35), math.radians(25)
        self.fit_points()

    def plot_rect(self):
        return QRectF(62, 30, max(10, self.width()-86), max(10, self.height()-86))

    def fit_points(self):
        self._manual_view = False
        if self.points:
            if self.plane == "3D":
                radius = max(max(math.dist(p["xyz"], self._pivot) for p in self.points), .5)
                self._center = (0., 0.)
                self._scale = min(self.plot_rect().width(), self.plot_rect().height()) / (radius*2*1.15)
                self.update()
                return
            a, b = PLANES[self.plane]
            horizontal = [p["xyz"][a] for p in self.points]
            vertical = [p["xyz"][b] for p in self.points]
            self._center = ((min(horizontal)+max(horizontal))/2,
                            (min(vertical)+max(vertical))/2)
            # Equal physical scale preserves distances and shape proportions.
            span_x = max(max(horizontal)-min(horizontal), 1.)
            span_y = max(max(vertical)-min(vertical), 1.)
            rect = self.plot_rect()
            self._scale = min(rect.width()/span_x, rect.height()/span_y) / 1.15
        else:
            self._center, self._scale = (0., 0.), 1.
        self.update()

    def position_for_row(self, row_id):
        point = next((p for p in self.points if p["id"] == row_id), None)
        return self._project(point["xyz"]) if point else None

    def _project(self, xyz):
        u, v, _depth = self._view_coordinates(xyz)
        origin = self.plot_rect().center()
        return QPointF(origin.x()+(u-self._center[0])*self._scale,
                       origin.y()-(v-self._center[1])*self._scale)

    def _view_coordinates(self, xyz):
        if self.plane != "3D":
            a, b = PLANES[self.plane]
            return xyz[a], xyz[b], 0.
        x, y, z = (xyz[a]-self._pivot[a] for a in range(3))
        cy, sy = math.cos(self._yaw), math.sin(self._yaw)
        cp, sp = math.cos(self._pitch), math.sin(self._pitch)
        u, q = cy*x-sy*y, sy*x+cy*y
        return u, cp*z-sp*q, cp*q+sp*z

    def _world(self, pos):
        origin = self.plot_rect().center()
        return (self._center[0]+(pos.x()-origin.x())/self._scale,
                self._center[1]-(pos.y()-origin.y())/self._scale)

    def _hits(self, pos):
        if not self.plot_rect().contains(pos):
            return []
        hits = []
        for point in self.points:
            screen = self._project(point["xyz"])
            distance = math.hypot(screen.x()-pos.x(), screen.y()-pos.y())
            if distance <= 11:
                hits.append((distance, -self._view_coordinates(point["xyz"])[2], point["id"]))
        hits.sort()
        return [row_id for distance, depth, row_id in hits]

    @staticmethod
    def _step(span):
        raw = max(span / 5, 1e-12)
        base = 10**math.floor(math.log10(raw))
        return next(multiplier*base for multiplier in (1, 2, 5, 10) if multiplier*base >= raw)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#f8fafc"))
        rect = self.plot_rect()
        painter.setPen(QPen(QColor("#cbd5e1"), 1))
        painter.drawRect(rect)
        if not self.points:
            painter.setPen(QColor("#64748b"))
            painter.drawText(rect, Qt.AlignCenter, "当前列表没有可绘制的点位\n读取数据或调整筛选条件")
            return
        if self.plane == "3D":
            self._draw_3d_frame(painter, rect)
        else:
            self._draw_grid(painter, rect)
        painter.save()
        painter.setClipRect(rect.adjusted(-1, -1, 1, 1))
        ordered = sorted(self.points, key=lambda p: self._view_coordinates(p["xyz"])[2])
        for point in ordered:
            screen = self._project(point["xyz"])
            if not rect.adjusted(-12, -12, 12, 12).contains(screen):
                continue
            painter.save()
            if self.selected_ids and point["id"] not in self.selected_ids:
                painter.setOpacity(.35)
            draw_weld_marker(painter, screen, point["layer"], size=6., three_d=True)
            painter.restore()
            if point["duplicate"]:
                painter.setBrush(Qt.NoBrush)
                painter.setPen(QPen(QColor("#df3131"), 1.8, Qt.DashLine))
                painter.drawEllipse(screen, 9, 9)
        selected = [p for p in self.points if p["id"] in self.selected_ids]
        for index, point in enumerate(selected):
            screen = self._project(point["xyz"])
            # Large contrasting halo and crosshair remain clear in a dense
            # cluster and are drawn last, including for occluded 3D points.
            painter.setBrush(QColor("#ffffff"))
            painter.setPen(QPen(QColor("#ffce3a"), 6))
            painter.drawEllipse(screen, 12, 12)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor("#14263b"), 2))
            painter.drawEllipse(screen, 13, 13)
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                painter.drawLine(QPointF(screen.x()+dx*16, screen.y()+dy*16),
                                 QPointF(screen.x()+dx*22, screen.y()+dy*22))
            draw_weld_marker(painter, screen, point["layer"], 8., three_d=True)
            if index < 3:
                text = f"#{point['id']+1}  {layer_label(point['layer'])}"
                width = painter.fontMetrics().horizontalAdvance(text)+14
                x = min(max(rect.left()+2, screen.x()+18), rect.right()-width-2)
                y = min(max(rect.top()+2, screen.y()-34), rect.bottom()-24)
                label_rect = QRectF(x, y, width, 24)
                painter.setBrush(QColor("#fff5cc"))
                painter.setPen(QPen(QColor("#14263b"), 1))
                painter.drawRoundedRect(label_rect, 3, 3)
                painter.drawText(label_rect, Qt.AlignCenter, text)
        painter.restore()

    def _draw_grid(self, painter, rect):
        lower, upper = self._world(rect.bottomLeft()), self._world(rect.topRight())
        for axis in (0, 1):
            step = self._step(upper[axis]-lower[axis])
            value = math.ceil(lower[axis]/step)*step
            for _ in range(12):
                if value > upper[axis] + step*1e-6:
                    break
                origin = rect.center()
                painter.setPen(QPen(QColor("#e2e8f0"), 1))
                if axis == 0:
                    x = origin.x()+(value-self._center[0])*self._scale
                    painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
                    label_rect = QRectF(x-42, rect.bottom()+8, 84, 20)
                else:
                    y = origin.y()-(value-self._center[1])*self._scale
                    painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
                    label_rect = QRectF(0, y-10, rect.left()-7, 20)
                painter.setPen(QColor("#64748b"))
                painter.drawText(label_rect, Qt.AlignCenter if axis == 0 else Qt.AlignRight | Qt.AlignVCenter,
                                 f"{0 if abs(value) < step*1e-6 else value:.6g}")
                value += step
        painter.setPen(QColor("#334155"))
        painter.drawText(QRectF(rect.left(), self.height()-25, rect.width(), 22),
                         Qt.AlignCenter, f"{self.plane[0]} (mm)")
        painter.drawText(QRectF(7, 4, 120, 22), f"{self.plane[1]} (mm)")

    def _draw_3d_frame(self, painter, rect):
        import itertools
        lows = [min(p["xyz"][a] for p in self.points) for a in range(3)]
        highs = [max(p["xyz"][a] for p in self.points) for a in range(3)]
        corners = list(itertools.product((0, 1), repeat=3))
        painter.save()
        painter.setClipRect(rect)
        painter.setPen(QPen(QColor("#cbd5e1"), 1, Qt.DashLine))
        for i, first in enumerate(corners):
            for second in corners[i+1:]:
                if sum(a != b for a, b in zip(first, second)) == 1:
                    a = tuple(highs[k] if first[k] else lows[k] for k in range(3))
                    b = tuple(highs[k] if second[k] else lows[k] for k in range(3))
                    painter.drawLine(self._project(a), self._project(b))
        painter.restore()
        painter.setPen(QColor("#334155"))
        painter.drawText(QRectF(rect.left(), 4, rect.width(), 22), "3D · XYZ坐标 (mm) · 正交视图")
        origin = QPointF(rect.left()+48, rect.bottom()-40)
        for axis, color in enumerate(("#dc4343", "#11956f", "#2563eb")):
            vector = list(self._pivot)
            vector[axis] += 1
            u, v, _depth = self._view_coordinates(vector)
            end = QPointF(origin.x()+u*34, origin.y()-v*34)
            painter.setPen(QPen(QColor(color), 2.5))
            painter.drawLine(origin, end)
            painter.drawText(QRectF(end.x()-7, end.y()-18, 22, 22), "XYZ"[axis])
        painter.setPen(QColor("#64748b"))
        painter.drawText(QRectF(rect.left(), self.height()-25, rect.width(), 22), Qt.AlignCenter,
                         "左键拖动旋转 · Shift+拖动 / 中键平移 · 滚轮缩放")

    def resizeEvent(self, event):
        # Preserve a zoomed/panned view; initial data is fitted after layout.
        super().resizeEvent(event)
        if not self._manual_view:
            self.fit_points()

    def mousePressEvent(self, event):
        if event.button() in (Qt.LeftButton, Qt.MiddleButton, Qt.RightButton):
            self.setFocus()
            self._press_pos = self._last_pos = event.pos()
            self._moved = False
            self._drag_button = event.button()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._press_pos is not None:
            if (event.pos()-self._press_pos).manhattanLength() >= 4:
                self._moved = True
            if self._moved:
                self._manual_view = True
                delta = event.pos()-self._last_pos
                if (self.plane == "3D" and self._drag_button == Qt.LeftButton
                        and not event.modifiers() & Qt.ShiftModifier):
                    self._yaw += delta.x()*.008
                    self._pitch = max(math.radians(-85), min(math.radians(85), self._pitch-delta.y()*.008))
                else:
                    self._center = (self._center[0]-delta.x()/self._scale,
                                    self._center[1]+delta.y()/self._scale)
                self._last_hits = []
                self._last_pos = event.pos()
                self.setCursor(Qt.ClosedHandCursor)
                QToolTip.hideText()
                self.update()
            return
        hits = self._hits(event.pos())
        self.setCursor(Qt.PointingHandCursor if hits else Qt.ArrowCursor)
        if hits:
            point = next(p for p in self.points if p["id"] == hits[0])
            xyz = point["xyz"]
            text = f"{point['row'].get('Name', '')}\nX={xyz[0]:.3f}  Y={xyz[1]:.3f}  Z={xyz[2]:.3f} mm"
            text += f"\n{layer_label(point['layer'])}"
            if len(hits) > 1:
                text += f"\n附近/重叠 {len(hits)} 点，连续点击可切换"
            QToolTip.showText(event.globalPos(), text, self)
        else:
            QToolTip.hideText()

    def mouseReleaseEvent(self, event):
        if self._press_pos is None:
            return
        if event.button() == Qt.LeftButton and not self._moved:
            hits = self._hits(event.pos())
            repeated = (self._last_hit_pos is not None
                        and (event.pos()-self._last_hit_pos).manhattanLength() <= 5
                        and hits == self._last_hits)
            self._hit_cycle = (self._hit_cycle+1) % len(hits) if hits and repeated else 0
            self._last_hit_pos, self._last_hits = event.pos(), hits
            self.point_clicked.emit(hits[self._hit_cycle] if hits else -1)
        self._press_pos = self._last_pos = None
        self._drag_button = None
        self.unsetCursor()

    def mouseDoubleClickEvent(self, event):
        self.reset_view()

    def wheelEvent(self, event):
        if not self.points or not self.plot_rect().contains(event.pos()):
            event.ignore()
            return
        before = self._world(event.pos())
        self._manual_view = True
        self._scale = min(1e7, max(1e-6, self._scale * 1.2**(event.angleDelta().y()/120)))
        after = self._world(event.pos())
        self._center = (self._center[0]+before[0]-after[0], self._center[1]+before[1]-after[1])
        self._last_hits = []
        self.update()
        event.accept()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_F, Qt.Key_Home):
            self.fit_points()
        elif event.key() == Qt.Key_Escape:
            self.point_clicked.emit(-1)
        else:
            super().keyPressEvent(event)


class PointProjectionPanel(QWidget):
    point_clicked = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(270)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self.source_label = QLabel("点位投影")
        self.source_label.setStyleSheet("font-size:14px;font-weight:bold;color:#26384b;")
        self.source_label.setTextFormat(Qt.PlainText)
        self.source_label.setWordWrap(True)
        self.source_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self.source_label)
        toolbar = QHBoxLayout()
        self.plane_combo = QComboBox()
        for text, value in (("XZ 侧视", "XZ"), ("XY 俯视", "XY"), ("YZ 前视", "YZ"), ("3D 可旋转", "3D")):
            self.plane_combo.addItem(text, value)
        toolbar.addWidget(self.plane_combo)
        self.fit_button = QPushButton("适合窗口")
        toolbar.addWidget(self.fit_button)
        self.details_button = QToolButton()
        self.details_button.setText("详情")
        self.details_button.setCheckable(True)
        self.details_button.setChecked(True)
        toolbar.addWidget(self.details_button)
        self.save_button = QPushButton("保存图片")
        toolbar.addWidget(self.save_button)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        self.canvas = PointProjectionCanvas()
        layout.addWidget(self.canvas, 1)
        self.legend_label = QLabel(
            '<span style="color:#2563eb">● 2T 球体</span>　'
            '<span style="color:#ea8a23">◆ 3T 正方体</span>　'
            '<span style="color:#11956f">▲ 4T 圆锥</span>　'
            '<span style="color:#df3131">虚线环：重复</span>　'
            '<span style="color:#745600">黄环/十字：选中</span>')
        self.legend_label.setStyleSheet("color:#475569;font-size:11px;")
        self.legend_label.setWordWrap(True)
        self.legend_label.setToolTip("图中有效点的层数分类；详情的“分类统计”页提供全量、列表、有效及选中统计")
        layout.addWidget(self.legend_label)
        self.count_label = QLabel("暂无点位")
        self.count_label.setWordWrap(True)
        layout.addWidget(self.count_label)
        hint = QLabel("点击定位列表 · 重叠点连续点击切换 · 滚轮缩放 · 双击复位；3D 左键旋转，Shift+拖动平移")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#64748b;font-size:11px;")
        layout.addWidget(hint)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMinimumHeight(60)
        self.details.setPlaceholderText("选择点位后显示层数、名称、三维坐标及识别证据")
        self.detail_tabs = QTabWidget()
        self.detail_tabs.setMinimumHeight(120)
        self.detail_tabs.setMaximumHeight(160)
        self.detail_tabs.addTab(self.details, "点位详情")
        self.statistics_table = QTreeWidget()
        self.statistics_table.setColumnCount(5)
        self.statistics_table.setHeaderLabels(["类别", "总数", "列表", "有效", "选中"])
        self.statistics_table.setRootIsDecorated(False)
        self.statistics_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.statistics_table.header().setSectionResizeMode(0, QHeaderView.Stretch)
        for column in range(1, 5):
            self.statistics_table.header().setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.detail_tabs.addTab(self.statistics_table, "分类统计")
        layout.addWidget(self.detail_tabs)
        self._all_rows = self._visible_rows = []
        self._count_text = "暂无点位"
        self.canvas.point_clicked.connect(self.point_clicked)
        self.plane_combo.currentIndexChanged.connect(
            lambda _index: self.canvas.set_plane(self.plane_combo.currentData()))
        self.fit_button.clicked.connect(self.canvas.fit_points)
        self.save_button.clicked.connect(self._save_image)
        self.details_button.toggled.connect(self.detail_tabs.setVisible)
        self._compact = False
        self._update_statistics()

    def set_compact(self, compact):
        if compact != self._compact:
            self._compact = compact
            self.canvas.setMinimumHeight(160 if compact else 180)
            self.details_button.setChecked(not compact)

    def set_points(self, title, rows, row_ids, duplicate_ids=(), all_rows=None):
        self.source_label.setText(f"点位视图 · {title}")
        self._visible_rows = list(rows)
        self._all_rows = list(rows if all_rows is None else all_rows)
        self.canvas.set_points(rows, row_ids, duplicate_ids, dataset=title)
        text = f"图中 {len(self.canvas.points)} 点 / 当前列表 {len(rows)} 行"
        if self.canvas.invalid_count:
            text += f"；坐标无效 {self.canvas.invalid_count} 行"
        self._count_text = text
        self.set_selection(self.canvas.selected_ids)

    def set_selection(self, row_ids):
        self.canvas.set_selection(row_ids)
        selected = [p for p in self.canvas.points if p["id"] in self.canvas.selected_ids]
        lines = []
        for point in selected[:3]:
            row, xyz = point["row"], point["xyz"]
            lines.append(f"【{layer_label(point['layer'])}】")
            lines.append(f"{row.get('Name', '')}（源数据第 {point['id']+1} 行）")
            lines.append(f"X={xyz[0]:.6f}  Y={xyz[1]:.6f}  Z={xyz[2]:.6f} mm")
            lines.extend(str(row[key]) for key in ("ShapeHypothesis", "Evidence") if row.get(key))
        if len(selected) > 3:
            lines.append(f"共选中 {len(selected)} 点")
        self.details.setPlainText("\n".join(lines))
        self.count_label.setText(self._count_text + (f"；已选中 {len(selected)} 点" if selected else ""))
        self._update_statistics()

    def _update_statistics(self):
        columns = {
            "total": layer_counts(self._all_rows),
            "visible": layer_counts(self._visible_rows),
            "valid": layer_counts(p["row"] for p in self.canvas.points),
            "selected": layer_counts(p["row"] for p in self.canvas.points if p["id"] in self.canvas.selected_ids),
        }
        self.layer_statistics = {layer: {key: value[layer] for key, value in columns.items()}
                                 for layer in (2, 3, 4)}
        labels = {2: "2T 球体", 3: "3T 正方体", 4: "4T 圆锥"}
        self.legend_label.setText(" &nbsp;│&nbsp; ".join(
            f'<span style="color:{LAYER_COLORS[layer]}">{labels[layer]} {self.layer_statistics[layer]["valid"]}</span>'
            for layer in (2, 3, 4))
            + '<br><span style="color:#df3131">红色虚线环：重复</span>　黄环/十字：选中')
        self.statistics_table.clear()
        for layer in (2, 3, 4):
            counts = self.layer_statistics[layer]
            item = QTreeWidgetItem([layer_label(layer)] + [str(counts[key]) for key in columns])
            item.setIcon(0, layer_icon(layer))
            item.setForeground(0, QColor(LAYER_COLORS[layer]))
            item.setToolTip(0, "正方体=3T，圆锥=4T，球体=2T；无已识别几何时沿用明确层数，否则默认2T")
            self.statistics_table.addTopLevelItem(item)

    def _save_image(self):
        path, _ = QFileDialog.getSaveFileName(self, "保存当前点位投影", "点位投影.png", "PNG图片 (*.png)")
        if path:
            if not path.lower().endswith(".png"):
                path += ".png"
            if not self.canvas.grab().save(path, "PNG"):
                QMessageBox.warning(self, "保存失败", "无法写入所选图片文件")
