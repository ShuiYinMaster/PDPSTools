import sys
import os

# ── 所有 import 必须在 setAttribute 之前完成 ──────────────────────
from PyQt5.QtCore import Qt, pyqtSignal, QPoint, QRect, QSize
from PyQt5.QtGui import QColor, QFont, QPainter, QLinearGradient, QConicalGradient, QRadialGradient
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QCheckBox, QDoubleSpinBox, QGroupBox,
    QFrame, QScrollArea, QColorDialog, QStatusBar, QLineEdit,
    QGridLayout, QDialog, QSizePolicy, QSlider,
)

# ── 高 DPI 支持 ───────────────────────────────────────────────────
# setAttribute 必须在 QApplication 实例化之前调用。
# 用 instance() 检测：若主项目已创建 QApplication，则跳过，
# 由主项目自行负责高 DPI 设置，避免重复调用报错。
os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"
if QApplication.instance() is None:
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)


# ─────────────────────────────────────────────
#  色相-饱和度-亮度 渐变画布
# ─────────────────────────────────────────────
class SatValCanvas(QWidget):
    """右侧正方形：X=饱和度 Y=明度，背景随色相变化。"""
    picked = pyqtSignal(int, int)   # sat(0-255), val(0-255)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hue = 0
        self._sat = 255
        self._val = 255
        self.setFixedSize(200, 200)
        self.setCursor(Qt.CrossCursor)

    def set_hue(self, h):
        self._hue = h
        self.update()

    def set_sv(self, s, v):
        self._sat, self._val = s, v
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        w, h = self.width(), self.height()
        # 水平：白→纯色
        hg = QLinearGradient(0, 0, w, 0)
        hg.setColorAt(0, QColor(255, 255, 255))
        hg.setColorAt(1, QColor.fromHsv(self._hue, 255, 255))
        p.fillRect(0, 0, w, h, hg)
        # 垂直：透明→黑
        vg = QLinearGradient(0, 0, 0, h)
        vg.setColorAt(0, QColor(0, 0, 0, 0))
        vg.setColorAt(1, QColor(0, 0, 0, 255))
        p.fillRect(0, 0, w, h, vg)
        # 十字准星
        cx = int(self._sat / 255 * w)
        cy = int((255 - self._val) / 255 * h)
        p.setPen(Qt.white if self._val < 128 else Qt.black)
        p.drawLine(cx - 8, cy, cx + 8, cy)
        p.drawLine(cx, cy - 8, cx, cy + 8)

    def _pos_to_sv(self, pos):
        s = max(0, min(255, int(pos.x() / self.width() * 255)))
        v = max(0, min(255, int((1 - pos.y() / self.height()) * 255)))
        return s, v

    def mousePressEvent(self, e):
        s, v = self._pos_to_sv(e.pos())
        self._sat, self._val = s, v
        self.update()
        self.picked.emit(s, v)

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton:
            s, v = self._pos_to_sv(e.pos())
            self._sat, self._val = s, v
            self.update()
            self.picked.emit(s, v)


# ─────────────────────────────────────────────
#  色相条
# ─────────────────────────────────────────────
class HueBar(QWidget):
    """竖向彩虹条，拖动选色相。"""
    hueChanged = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hue = 0
        self.setFixedSize(22, 200)
        self.setCursor(Qt.SizeVerCursor)

    def paintEvent(self, _):
        p = QPainter(self)
        g = QLinearGradient(0, 0, 0, self.height())
        for i in range(7):
            g.setColorAt(i / 6, QColor.fromHsv(int(i / 6 * 359), 255, 255))
        p.fillRect(self.rect(), g)
        # 指示线
        y = int(self._hue / 359 * self.height())
        p.setPen(Qt.white)
        p.drawLine(0, y, self.width(), y)
        p.setPen(Qt.black)
        p.drawLine(0, y + 1, self.width(), y + 1)

    def _y_to_hue(self, y):
        return max(0, min(359, int(y / self.height() * 359)))

    def mousePressEvent(self, e):
        self._hue = self._y_to_hue(e.pos().y())
        self.update(); self.hueChanged.emit(self._hue)

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton:
            self._hue = self._y_to_hue(e.pos().y())
            self.update(); self.hueChanged.emit(self._hue)

    def set_hue(self, h):
        self._hue = h; self.update()


# ─────────────────────────────────────────────
#  预设色块网格（支持双击确认）
# ─────────────────────────────────────────────
_PRESETS = [
    "#000000","#444444","#888888","#bbbbbb","#ffffff",
    "#ff0000","#ff6600","#ffcc00","#00cc00","#0066ff",
    "#9900cc","#ff66aa","#00cccc","#ff4444","#ffaa00",
    "#aaff00","#00ffaa","#00aaff","#aa00ff","#ff00aa",
    "#330000","#333300","#003300","#003333","#000033",
    "#663300","#336600","#006633","#003366","#330066",
    "#cc6600","#66cc00","#00cc66","#0066cc","#6600cc",
    "#ffcccc","#ffffcc","#ccffcc","#ccffff","#ccccff",
]

class PresetGrid(QWidget):
    clicked   = pyqtSignal(QColor)
    dblclicked = pyqtSignal(QColor)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cols  = 8
        self._size  = 24
        self._gap   = 3
        self._hover = -1
        cols = self._cols
        total = len(_PRESETS)
        rows = (total + cols - 1) // cols
        w = cols * (self._size + self._gap) - self._gap
        h = rows * (self._size + self._gap) - self._gap
        self.setFixedSize(w, h)
        self.setMouseTracking(True)

    def _idx(self, pos):
        s, g = self._size, self._gap
        col = pos.x() // (s + g)
        row = pos.y() // (s + g)
        idx = row * self._cols + col
        if 0 <= idx < len(_PRESETS):
            # boundary check inside cell
            cx = col * (s + g); cy = row * (s + g)
            if pos.x() - cx < s and pos.y() - cy < s:
                return idx
        return -1

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s, g = self._size, self._gap
        for i, hex_c in enumerate(_PRESETS):
            col = i % self._cols; row = i // self._cols
            x = col * (s + g); y = row * (s + g)
            color = QColor(hex_c)
            p.setBrush(color)
            if i == self._hover:
                p.setPen(Qt.black); p.drawRoundedRect(x-1,y-1,s+2,s+2,4,4)
            else:
                p.setPen(QColor("#cccccc")); p.drawRoundedRect(x,y,s,s,3,3)

    def mouseMoveEvent(self, e):
        self._hover = self._idx(e.pos()); self.update()

    def leaveEvent(self, _):
        self._hover = -1; self.update()

    def mousePressEvent(self, e):
        idx = self._idx(e.pos())
        if idx >= 0: self.clicked.emit(QColor(_PRESETS[idx]))

    def mouseDoubleClickEvent(self, e):
        idx = self._idx(e.pos())
        if idx >= 0: self.dblclicked.emit(QColor(_PRESETS[idx]))


# ─────────────────────────────────────────────
#  自定义颜色选择对话框（浅色主题）
# ─────────────────────────────────────────────
class LightColorDialog(QDialog):
    """
    浅色主题色盘对话框，替代系统 QColorDialog。
    - 双击预设色块直接确认
    - 记忆上次选择颜色（类级别共享）
    - 大号 OK / Cancel 按钮
    """
    _last_color: QColor = QColor("#FF4444")   # 类变量：跨实例记忆

    DIALOG_SS = """
        QDialog { background: #f5f6fa; }
        QLabel  { color: #2a3050; font-size: 12px; }
        QLineEdit {
            background: #ffffff; border: 1px solid #c8cfe8;
            border-radius: 6px; color: #1a2040;
            padding: 3px 8px; font-family: 'Courier New', monospace;
            font-size: 12px;
        }
        QLineEdit:focus { border: 1px solid #4a72f5; }
        QPushButton#ok_btn {
            background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                stop:0 #3d6bf5, stop:1 #5b8aff);
            color: white; border: none; border-radius: 8px;
            font-size: 13px; font-weight: 700; min-width: 90px; min-height: 36px;
        }
        QPushButton#ok_btn:hover {
            background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                stop:0 #5b8aff, stop:1 #82aaff);
        }
        QPushButton#ok_btn:pressed { background: #2a50cc; }
        QPushButton#cancel_btn {
            background: #ffffff; color: #2a3050;
            border: 1.5px solid #c8cfe8; border-radius: 8px;
            font-size: 13px; font-weight: 600; min-width: 90px; min-height: 36px;
        }
        QPushButton#cancel_btn:hover { border-color: #4a72f5; color: #3d6bf5; }
        QPushButton#cancel_btn:pressed { background: #eef0fa; }
        QSlider::groove:horizontal {
            height: 6px; border-radius: 3px; background: #dde2f0;
        }
        QSlider::handle:horizontal {
            width: 14px; height: 14px; margin: -4px 0;
            border-radius: 7px; background: #4a72f5;
            border: 2px solid white;
        }
        QSlider::sub-page:horizontal { background: #4a72f5; border-radius: 3px; }
    """

    def __init__(self, initial: QColor = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择颜色")
        self.setModal(True)
        self.setStyleSheet(self.DIALOG_SS)
        self.setFixedWidth(480)

        self._color = QColor(initial or LightColorDialog._last_color)
        self._result: QColor = None

        root = QVBoxLayout(self)
        root.setSpacing(14)
        root.setContentsMargins(20, 20, 20, 20)

        # ── 上半：画布 + 色相条 + 预览 ──────────────
        top = QHBoxLayout(); top.setSpacing(12)

        self._sv = SatValCanvas()
        self._hb = HueBar()

        # 预览列
        prev_col = QVBoxLayout(); prev_col.setSpacing(6)
        prev_lbl = QLabel("预览")
        prev_lbl.setAlignment(Qt.AlignCenter)
        self._prev_new = QLabel()
        self._prev_new.setFixedSize(56, 56)
        self._prev_new.setStyleSheet("border-radius:8px; border:1px solid #c8cfe8;")
        prev_old_lbl = QLabel("上次")
        prev_old_lbl.setAlignment(Qt.AlignCenter)
        self._prev_old = QLabel()
        self._prev_old.setFixedSize(56, 56)
        self._prev_old.setStyleSheet("border-radius:8px; border:1px solid #c8cfe8;")
        self._prev_old.setCursor(Qt.PointingHandCursor)
        self._prev_old.setToolTip("单击恢复上次颜色")
        self._prev_old.mousePressEvent = lambda _: self._apply_color(LightColorDialog._last_color)
        prev_col.addWidget(prev_lbl)
        prev_col.addWidget(self._prev_new)
        prev_col.addWidget(prev_old_lbl)
        prev_col.addWidget(self._prev_old)
        prev_col.addStretch()

        top.addWidget(self._sv)
        top.addWidget(self._hb)
        top.addLayout(prev_col)
        root.addLayout(top)

        # ── 预设色块 ─────────────────────────────────
        root.addWidget(QLabel("预设颜色  （双击直接确认）"))
        self._grid = PresetGrid()
        root.addWidget(self._grid)

        # ── RGB 滑块 ─────────────────────────────────
        sliders_grp = QWidget()
        sl = QVBoxLayout(sliders_grp); sl.setSpacing(6); sl.setContentsMargins(0,0,0,0)
        self._sl_r = self._make_slider("#ff4444"); sl.addLayout(self._slider_row("R", self._sl_r))
        self._sl_g = self._make_slider("#44bb44"); sl.addLayout(self._slider_row("G", self._sl_g))
        self._sl_b = self._make_slider("#4488ff"); sl.addLayout(self._slider_row("B", self._sl_b))
        root.addWidget(sliders_grp)

        # ── Hex 输入 ──────────────────────────────────
        hex_row = QHBoxLayout()
        hex_row.addWidget(QLabel("HEX"))
        self._hex_edit = QLineEdit()
        self._hex_edit.setFixedWidth(100)
        self._hex_edit.setPlaceholderText("#RRGGBB")
        hex_row.addWidget(self._hex_edit)
        hex_row.addStretch()
        root.addLayout(hex_row)

        # ── 按钮行 ────────────────────────────────────
        btn_row = QHBoxLayout(); btn_row.setSpacing(10)
        btn_row.addStretch()
        ok_btn = QPushButton("确 认"); ok_btn.setObjectName("ok_btn")
        cancel_btn = QPushButton("取 消"); cancel_btn.setObjectName("cancel_btn")
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        root.addLayout(btn_row)

        # ── 信号连接 ──────────────────────────────────
        self._sv.picked.connect(self._on_sv)
        self._hb.hueChanged.connect(self._on_hue)
        self._grid.clicked.connect(self._apply_color)
        self._grid.dblclicked.connect(self._confirm_color)   # 双击直接确认
        self._sl_r.valueChanged.connect(self._on_rgb_slider)
        self._sl_g.valueChanged.connect(self._on_rgb_slider)
        self._sl_b.valueChanged.connect(self._on_rgb_slider)
        self._hex_edit.editingFinished.connect(self._on_hex)
        ok_btn.clicked.connect(self._ok)
        cancel_btn.clicked.connect(self.reject)

        self._apply_color(self._color)   # 初始化界面

    # helpers ──────────────────────────────────────
    def _make_slider(self, color: str) -> QSlider:
        s = QSlider(Qt.Horizontal)
        s.setRange(0, 255)
        s.setStyleSheet(s.styleSheet() +
            f" QSlider::sub-page:horizontal {{ background: {color}; border-radius:3px; }}")
        return s

    def _slider_row(self, lbl: str, slider: QSlider):
        row = QHBoxLayout(); row.setSpacing(8)
        l = QLabel(lbl); l.setFixedWidth(14)
        val_lbl = QLabel("0"); val_lbl.setFixedWidth(28)
        val_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        slider.valueChanged.connect(lambda v, vl=val_lbl: vl.setText(str(v)))
        row.addWidget(l); row.addWidget(slider); row.addWidget(val_lbl)
        return row

    def _apply_color(self, color: QColor):
        """更新所有控件到指定颜色（不触发 accept）。"""
        self._color = QColor(color)
        self._sync_widgets_from_color()

    def _confirm_color(self, color: QColor):
        """双击色块：直接确认。"""
        self._color = QColor(color)
        self._sync_widgets_from_color()
        self._ok()

    def _sync_widgets_from_color(self):
        c = self._color
        h, s, v, _ = c.getHsv()
        if h < 0: h = 0
        # 阻断信号避免循环
        for w in (self._sl_r, self._sl_g, self._sl_b):
            w.blockSignals(True)
        self._sl_r.setValue(c.red())
        self._sl_g.setValue(c.green())
        self._sl_b.setValue(c.blue())
        for w in (self._sl_r, self._sl_g, self._sl_b):
            w.blockSignals(False)
        self._hex_edit.blockSignals(True)
        self._hex_edit.setText(c.name().upper())
        self._hex_edit.blockSignals(False)
        self._sv.set_hue(h); self._sv.set_sv(s, v)
        self._hb.set_hue(h)
        hex_c = c.name()
        self._prev_new.setStyleSheet(
            f"background:{hex_c}; border-radius:8px; border:1px solid #c8cfe8;")
        old_hex = LightColorDialog._last_color.name()
        self._prev_old.setStyleSheet(
            f"background:{old_hex}; border-radius:8px; border:1px solid #c8cfe8;")

    def _on_sv(self, s, v):
        h, _, _, _ = self._color.getHsv()
        if h < 0: h = 0
        self._apply_color(QColor.fromHsv(h, s, v))

    def _on_hue(self, h):
        _, s, v, _ = self._color.getHsv()
        self._apply_color(QColor.fromHsv(h, s if s >= 0 else 255, v if v >= 0 else 255))
        self._sv.set_hue(h)

    def _on_rgb_slider(self):
        self._apply_color(QColor(self._sl_r.value(), self._sl_g.value(), self._sl_b.value()))

    def _on_hex(self):
        text = self._hex_edit.text().strip()
        if not text.startswith("#"): text = "#" + text
        c = QColor(text)
        if c.isValid(): self._apply_color(c)

    def _ok(self):
        LightColorDialog._last_color = QColor(self._color)   # 记忆颜色
        self._result = self._color
        self.accept()

    def get_color(self) -> QColor:
        return self._result

    @classmethod
    def pick(cls, initial: QColor = None, parent=None) -> QColor:
        """便捷类方法，返回选中颜色或 None。"""
        dlg = cls(initial or cls._last_color, parent)
        if dlg.exec_() == QDialog.Accepted:
            return dlg.get_color()
        return None


# ─────────────────────────────────────────────
#  色块按钮（使用自定义对话框）
# ─────────────────────────────────────────────
class ColorButton(QPushButton):
    colorChanged = pyqtSignal(QColor)

    def __init__(self, initial_color: QColor = QColor("#FF0000"), parent=None):
        super().__init__(parent)
        self._color = initial_color
        self.setFixedSize(120, 36)
        self.setCursor(Qt.PointingHandCursor)
        self._update_style()
        self.clicked.connect(self._pick_color)

    def _update_style(self):
        r, g, b = self._color.red(), self._color.green(), self._color.blue()
        luminance = 0.299 * r + 0.587 * g + 0.114 * b
        text_color = "#1a1a1a" if luminance > 140 else "#ffffff"
        hex_val = self._color.name().upper()
        self.setText(hex_val)
        self.setStyleSheet(f"""
            QPushButton {{
                background-color: {hex_val};
                color: {text_color};
                border: 2px solid rgba(0,0,0,0.15);
                border-radius: 8px;
                font-family: 'Courier New', monospace;
                font-size: 12px;
                font-weight: bold;
                letter-spacing: 1px;
            }}
            QPushButton:hover {{ border: 2px solid rgba(0,0,0,0.35); }}
        """)

    def _pick_color(self):
        color = LightColorDialog.pick(self._color, self)
        if color is not None:
            self._color = color
            self._update_style()
            self.colorChanged.emit(color)

    def get_rgb_int(self) -> int:
        c = self._color
        return c.red() + c.green() * 256 + c.blue() * 65536

    def color(self) -> QColor:
        return self._color


# ─────────────────────────────────────────────
#  分隔线
# ─────────────────────────────────────────────
class HRule(QFrame):
    def __init__(self):
        super().__init__()
        self.setFrameShape(QFrame.HLine)
        self.setFixedHeight(1)
        self.setStyleSheet("background: #dde2f0; border: none;")


# ─────────────────────────────────────────────
#  操作行
# ─────────────────────────────────────────────
class ActionRow(QWidget):
    def __init__(self, label, control, btn_text, callback, icon_char="●", parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(12)

        icon = QLabel(icon_char)
        icon.setFixedWidth(20)
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet("color: #4a72f5; font-size: 14px;")

        lbl = QLabel(label)
        lbl.setFixedWidth(130)
        lbl.setStyleSheet("color: #2a3050; font-size: 13px;")

        btn = QPushButton(btn_text)
        btn.setFixedSize(110, 36)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #3d6bf5, stop:1 #5b8aff);
                color: white; border: none; border-radius: 8px;
                font-size: 12px; font-weight: 600;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #5b8aff, stop:1 #82aaff);
            }
            QPushButton:pressed { background: #2a50cc; }
        """)
        btn.clicked.connect(callback)

        layout.addWidget(icon)
        layout.addWidget(lbl)
        layout.addWidget(control, 1)
        layout.addWidget(btn)


# ═════════════════════════════════════════════════════════════════════
#  ExcelShapeWidget
#  ─ 纯 QWidget，可直接嵌入任何 QTabWidget / QStackedWidget / 布局
#  ─ 状态信息通过 statusMessage 信号向外广播，宿主可自由接入
#
#  用法（嵌入已有标签页）：
#      from excel_shape_tool import ExcelShapeWidget
#      tabs.addTab(ExcelShapeWidget(), "Excel 图形工具")
#
#  可选：监听状态消息
#      widget = ExcelShapeWidget()
#      widget.statusMessage.connect(self.statusBar().showMessage)
# ═════════════════════════════════════════════════════════════════════
class ExcelShapeWidget(QWidget):
    # 操作结果信号：(消息文字,)
    # 宿主可将其接入自己的状态栏，不接也无妨（内置状态标签兜底）
    statusMessage = pyqtSignal(str)

    # ── 公共样式表（供宿主 / MainWindow 复用）────────────────────────
    STYLESHEET = """
        QWidget#ExcelShapeWidget, QWidget#ExcelShapeWidgetRoot {
            background: #f5f6fa;
        }
        QGroupBox {
            border: 1px solid #d0d5e8;
            border-radius: 12px;
            margin-top: 20px;
            background: #ffffff;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top left;
            padding: 4px 12px;
            color: #2d52c4;
            font-weight: 700;
            font-size: 13px;
            letter-spacing: 1px;
        }
        QScrollArea { border: none; background: transparent; }
        QScrollBar:vertical { width: 6px; background: transparent; }
        QScrollBar::handle:vertical {
            background: #c0c8e0; border-radius: 3px;
        }
        QDoubleSpinBox, QSpinBox {
            background: #ffffff;
            border: 1px solid #c8cfe8;
            border-radius: 8px; color: #1a2040;
            padding: 4px 8px; font-size: 13px; min-height: 32px;
        }
        QDoubleSpinBox:focus, QSpinBox:focus {
            border: 1px solid #4a72f5;
        }
        QDoubleSpinBox::up-button, QSpinBox::up-button,
        QDoubleSpinBox::down-button, QSpinBox::down-button {
            border: none; background: transparent; width: 18px;
        }
        QDoubleSpinBox::up-arrow, QSpinBox::up-arrow {
            image: none;
            border-left: 4px solid transparent;
            border-right: 4px solid transparent;
            border-bottom: 5px solid #4a72f5;
            width: 0; height: 0;
        }
        QDoubleSpinBox::down-arrow, QSpinBox::down-arrow {
            image: none;
            border-left: 4px solid transparent;
            border-right: 4px solid transparent;
            border-top: 5px solid #4a72f5;
            width: 0; height: 0;
        }
        QCheckBox { color: #2a3050; font-size: 13px; spacing: 8px; }
        QCheckBox::indicator {
            width: 18px; height: 18px; border-radius: 5px;
            border: 2px solid #b0bada;
            background: #ffffff;
        }
        QCheckBox::indicator:checked {
            background: #3d6bf5; border: 2px solid #2d52c4; image: none;
        }
    """

    def __init__(self, parent=None, show_header: bool = True,
                 show_status_bar: bool = True):
        """
        Parameters
        ----------
        parent          : 父控件，嵌入标签页时传入 tab widget
        show_header     : 是否显示顶部标题栏（独立窗口模式建议 True，
                          嵌入模式可设 False 节省空间）
        show_status_bar : 是否显示底部内置状态标签（宿主有自己状态栏时
                          可设 False，改用 statusMessage 信号接入）
        """
        super().__init__(parent)
        self.setObjectName("ExcelShapeWidget")
        self.setMinimumWidth(600)
        self.setStyleSheet(self.STYLESHEET)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── 可选顶部标题栏 ───────────────────────
        if show_header:
            header = QWidget()
            header.setFixedHeight(72)
            header.setStyleSheet("""
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 #e8eeff, stop:0.5 #dde5ff, stop:1 #f5f6fa);
                border-bottom: 1px solid #c0ccf0;
            """)
            h_layout = QHBoxLayout(header)
            h_layout.setContentsMargins(28, 0, 28, 0)

            title_lbl = QLabel("⬡  Excel 图形批量处理工具")
            title_lbl.setStyleSheet(
                "color: #1a2a6c; font-size: 18px; font-weight: 700; letter-spacing: 1px;")
            subtitle = QLabel("Batch Shape Formatter")
            subtitle.setStyleSheet(
                "color: #6a85c8; font-size: 11px; letter-spacing: 2px;")
            title_col = QVBoxLayout()
            title_col.setSpacing(2)
            title_col.addWidget(title_lbl)
            title_col.addWidget(subtitle)
            h_layout.addLayout(title_col)
            h_layout.addStretch()
            outer.addWidget(header)

        # ── 滚动内容区 ───────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content.setObjectName("ExcelShapeWidgetRoot")
        content.setStyleSheet("background: transparent;")
        scroll.setWidget(content)

        main_layout = QVBoxLayout(content)
        main_layout.setContentsMargins(24, 20, 24, 24)
        main_layout.setSpacing(16)
        outer.addWidget(scroll)

        # ── 全局选项 ─────────────────────────────
        opts_col = QVBoxLayout()
        opts_col.setSpacing(6)

        self.chk_sel_range = QCheckBox("仅处理所选单元格范围内的图形（默认勾选）")
        self.chk_sel_range.setChecked(True)          # 默认勾选

        self.chk_all = QCheckBox("处理全部工作表（否则仅当前活动表）")

        # 两者互斥：勾选"仅所选范围"时禁用"全部工作表"
        def _on_sel_range_toggled(checked):
            self.chk_all.setEnabled(not checked)
            if checked:
                self.chk_all.setChecked(False)

        def _on_all_toggled(checked):
            self.chk_sel_range.setEnabled(not checked)
            if checked:
                self.chk_sel_range.setChecked(False)

        self.chk_sel_range.toggled.connect(_on_sel_range_toggled)
        self.chk_all.toggled.connect(_on_all_toggled)

        # 初始状态同步：默认勾选 sel_range → 禁用 all
        self.chk_all.setEnabled(False)

        opts_col.addWidget(self.chk_sel_range)
        opts_col.addWidget(self.chk_all)
        main_layout.addLayout(opts_col)
        main_layout.addWidget(HRule())

        # ── 颜色组 ───────────────────────────────
        color_group = QGroupBox("颜 色 设 置")
        cg_layout = QVBoxLayout(color_group)
        cg_layout.setSpacing(6)
        cg_layout.setContentsMargins(16, 16, 16, 16)

        self.btn_connector_clr = ColorButton(QColor("#FF4444"))
        self.btn_text_clr      = ColorButton(QColor("#222222"))
        self.btn_fill_clr      = ColorButton(QColor("#FFE066"))
        self.btn_border_clr    = ColorButton(QColor("#3355FF"))

        for label, ctrl, text, cb, icon in [
            ("连接线颜色", self.btn_connector_clr, "修改连接线",  self.change_connector_color, "⟿"),
            ("文字颜色",   self.btn_text_clr,      "修改文字色",  self.change_text_color,      "T"),
            ("填充颜色",   self.btn_fill_clr,      "修改填充色",  self.change_fill_color,       "◼"),
            ("边框颜色",   self.btn_border_clr,    "修改边框色",  self.change_border_color,     "□"),
        ]:
            cg_layout.addWidget(ActionRow(label, ctrl, text, cb, icon))
            cg_layout.addWidget(HRule())
        main_layout.addWidget(color_group)

        # ── 尺寸 / 数值组 ────────────────────────
        size_group = QGroupBox("尺 寸 与 数 值")
        sg_layout = QVBoxLayout(size_group)
        sg_layout.setSpacing(6)
        sg_layout.setContentsMargins(16, 16, 16, 16)

        self.spin_size = QDoubleSpinBox()
        self.spin_size.setRange(0.1, 50.0)
        self.spin_size.setValue(0.5)
        self.spin_size.setSingleStep(0.1)
        self.spin_size.setSuffix("  cm")
        self.spin_size.setFixedWidth(130)
        sg_layout.addWidget(ActionRow("节点大小", self.spin_size,
                                      "修改节点大小", self.resize_flowchart_nodes, "⬡"))
        sg_layout.addWidget(HRule())

        self.spin_font = QDoubleSpinBox()
        self.spin_font.setRange(4.0, 144.0)
        self.spin_font.setValue(12.0)
        self.spin_font.setSingleStep(0.5)
        self.spin_font.setSuffix("  pt")
        self.spin_font.setFixedWidth(130)
        sg_layout.addWidget(ActionRow("字体大小", self.spin_font,
                                      "修改字体大小", self.change_font_size, "A"))
        sg_layout.addWidget(HRule())

        self.spin_outline = QDoubleSpinBox()
        self.spin_outline.setRange(0.25, 20.0)
        self.spin_outline.setValue(1.0)
        self.spin_outline.setSingleStep(0.25)
        self.spin_outline.setSuffix("  pt")
        self.spin_outline.setFixedWidth(130)
        sg_layout.addWidget(ActionRow("轮廓粗细", self.spin_outline,
                                      "修改轮廓粗细", self.change_outline_weight, "≡"))
        main_layout.addWidget(size_group)

        # ── 一键应用全部 ──────────────────────────
        apply_all_btn = QPushButton("▶  一键应用全部设置")
        apply_all_btn.setFixedHeight(46)
        apply_all_btn.setCursor(Qt.PointingHandCursor)
        apply_all_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #1e4aaa, stop:1 #3d6bf5);
                color: white; border: none; border-radius: 10px;
                font-size: 14px; font-weight: 700; letter-spacing: 1px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #3d6bf5, stop:1 #7099ff);
            }
            QPushButton:pressed { background: #1a3a99; }
        """)
        apply_all_btn.clicked.connect(self.apply_all)
        main_layout.addWidget(apply_all_btn)
        main_layout.addStretch()

        # ── 可选内置状态标签 ──────────────────────
        if show_status_bar:
            self._status_lbl = QLabel("就绪 — 请打开 Excel 文件后再执行操作")
            self._status_lbl.setStyleSheet(
                "color: #2d52c4; font-size: 12px; padding: 5px 12px;"
                "background: #eef0fa;"
                "border-top: 1px solid #d0d5e8;")
            outer.addWidget(self._status_lbl)
        else:
            self._status_lbl = None

    # ─────────────────────────────────────────
    #  状态消息统一出口
    # ─────────────────────────────────────────
    def _set_status(self, msg: str):
        """同时更新内置标签（若存在）并发射信号供宿主接入。"""
        if self._status_lbl is not None:
            self._status_lbl.setText(msg)
        self.statusMessage.emit(msg)

    # ─────────────────────────────────────────
    #  Excel 操作核心
    # ─────────────────────────────────────────
    def _get_excel(self):
        # 【优化①: Early Binding】
        try:
            import win32com.client
            try:
                return win32com.client.gencache.EnsureDispatch("Excel.Application")
            except Exception:
                return win32com.client.Dispatch("Excel.Application")
        except Exception as e:
            self._set_status(f"❌ 无法连接 Excel：{e}")
            return None

    def _process_shape_cached(self, shp, actions: list):
        # 【优化②: COM 对象引用缓存】对单个 shape 应用多个 action
        try:
            shp_type = shp.Type
            if shp_type == 6:
                for sub in shp.GroupItems:
                    self._process_shape_cached(sub, actions)
                return

            is_connector = None
            auto_type    = None
            tf2          = None
            has_text     = None

            for action, param in actions:
                if action == "connector":
                    if is_connector is None:
                        is_connector = shp.Connector
                    if is_connector:
                        shp.Line.ForeColor.RGB = param

                elif action in ("resize", "fill", "border", "outline_weight"):
                    if auto_type is None:
                        auto_type = shp.AutoShapeType
                    # 9  = msoShapeOval（椭圆/圆形）
                    # 35 = msoShapeFlowchartTerminator（流程图终止符，胶囊形）
                    # 62-89 = msoShapeFlowchart* 流程图节点系列
                    if auto_type == 9 or auto_type == 35 or 62 <= auto_type <= 89:
                        if action == "resize":
                            shp.Width = param; shp.Height = param
                        elif action == "fill":
                            shp.Fill.ForeColor.RGB = param
                        elif action == "border":
                            shp.Line.ForeColor.RGB = param
                        elif action == "outline_weight":
                            shp.Line.Weight = param

                elif action in ("text", "fontsize"):
                    if tf2 is None:
                        tf2 = shp.TextFrame2
                    if has_text is None:
                        has_text = tf2.HasText
                    if has_text:
                        tr = tf2.TextRange
                        if action == "text":
                            tr.Font.Fill.ForeColor.RGB = param
                        elif action == "fontsize":
                            tr.Font.Size = param
        except Exception:
            pass

    @staticmethod
    def _process_sheet_in_thread(sheet_name: str, actions: list, results: dict):
        # 【优化③: 多线程】子线程独立初始化 COM，附着同一 Excel 实例
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        try:
            excel = win32com.client.GetActiveObject("Excel.Application")
            ws = excel.Worksheets(sheet_name)
            for shp in list(ws.Shapes):
                ExcelShapeWidget._process_shape_static(shp, actions)
            results[sheet_name] = "ok"
        except Exception as e:
            results[sheet_name] = f"err:{e}"
        finally:
            pythoncom.CoUninitialize()

    @staticmethod
    def _process_shape_static(shp, actions: list):
        # 静态版，供子线程调用
        try:
            shp_type = shp.Type
            if shp_type == 6:
                for sub in shp.GroupItems:
                    ExcelShapeWidget._process_shape_static(sub, actions)
                return

            is_connector = None
            auto_type    = None
            tf2          = None
            has_text     = None

            for action, param in actions:
                if action == "connector":
                    if is_connector is None:
                        is_connector = shp.Connector
                    if is_connector:
                        shp.Line.ForeColor.RGB = param
                elif action in ("resize", "fill", "border", "outline_weight"):
                    if auto_type is None:
                        auto_type = shp.AutoShapeType
                    # 9  = msoShapeOval（椭圆/圆形）
                    # 35 = msoShapeFlowchartTerminator（流程图终止符，胶囊形）
                    # 62-89 = msoShapeFlowchart* 流程图节点系列
                    if auto_type == 9 or auto_type == 35 or 62 <= auto_type <= 89:
                        if action == "resize":
                            shp.Width = param; shp.Height = param
                        elif action == "fill":
                            shp.Fill.ForeColor.RGB = param
                        elif action == "border":
                            shp.Line.ForeColor.RGB = param
                        elif action == "outline_weight":
                            shp.Line.Weight = param
                elif action in ("text", "fontsize"):
                    if tf2 is None:
                        tf2 = shp.TextFrame2
                    if has_text is None:
                        has_text = tf2.HasText
                    if has_text:
                        tr = tf2.TextRange
                        if action == "text":
                            tr.Font.Fill.ForeColor.RGB = param
                        elif action == "fontsize":
                            tr.Font.Size = param
        except Exception:
            pass

    @staticmethod
    def _shape_in_rect(shp, rect) -> bool:
        """
        判断 shape 是否与选区矩形有交叠。
        rect = (left, top, right, bottom)，单位均为 Excel 内部点（pt）。
        采用"中心点落在选区内"策略，避免跨边界的大图形被误判。
        也可改为 AABB 相交判断（任意重叠即处理），见注释。
        """
        try:
            shp_l = shp.Left
            shp_t = shp.Top
            shp_cx = shp_l + shp.Width  / 2   # 中心 X
            shp_cy = shp_t + shp.Height / 2   # 中心 Y
            left, top, right, bottom = rect

            # 策略A（默认）：中心点落在选区内
            return left <= shp_cx <= right and top <= shp_cy <= bottom

            # 策略B：任意重叠即处理（解除注释并注释策略A）
            # return (shp_l < right and shp_l + shp.Width > left and
            #         shp_t < bottom and shp_t + shp.Height > top)
        except Exception:
            return True   # 读取失败时默认处理该 shape

    def _run_action(self, action: str, param):
        return self._run_actions([(action, param)])

    def _run_actions(self, actions: list) -> int:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        excel = self._get_excel()
        if not excel:
            return 0

        all_sheets = self.chk_all.isChecked()
        try:
            excel.ScreenUpdating = False
            excel.EnableEvents   = False
        except Exception:
            pass

        # 仅所选范围模式：获取当前选区的像素边界（Left/Top/Right/Bottom）
        sel_rect = None
        if self.chk_sel_range.isChecked():
            try:
                sel = excel.Selection
                # Selection 可能是 Range 或其他对象，统一用 Left/Top/Width/Height
                sel_left   = sel.Left
                sel_top    = sel.Top
                sel_right  = sel_left + sel.Width
                sel_bottom = sel_top  + sel.Height
                sel_rect   = (sel_left, sel_top, sel_right, sel_bottom)
            except Exception:
                self._set_status("⚠️ 无法获取选区，将处理全部图形")

        count = 0
        try:
            if not all_sheets:
                ws = excel.ActiveSheet
                for shp in list(ws.Shapes):
                    if sel_rect is not None and not self._shape_in_rect(shp, sel_rect):
                        continue
                    self._process_shape_cached(shp, actions)
                count = 1
            else:
                sheet_names = [ws.Name for ws in excel.Worksheets]
                results: dict = {}
                with ThreadPoolExecutor(max_workers=min(len(sheet_names), 8)) as pool:
                    futures = {
                        pool.submit(ExcelShapeWidget._process_sheet_in_thread,
                                    name, actions, results): name
                        for name in sheet_names
                    }
                    for fut in as_completed(futures):
                        try:
                            fut.result()
                        except Exception:
                            pass
                count = sum(1 for v in results.values() if v == "ok")
                errors = [f"{k}: {v}" for k, v in results.items() if v != "ok"]
                if errors:
                    self._set_status(f"⚠️ 部分表处理失败：{'; '.join(errors)}")
        except Exception as e:
            self._set_status(f"❌ 操作失败：{e}")
        finally:
            try:
                excel.ScreenUpdating = True
                excel.EnableEvents   = True
            except Exception:
                pass
        return count

    # ─────────────────────────────────────────
    #  各功能槽函数
    # ─────────────────────────────────────────
    def change_connector_color(self):
        n = self._run_action("connector", self.btn_connector_clr.get_rgb_int())
        self._set_status(f"✅ 连接线颜色已修改，处理 {n} 张工作表")

    def resize_flowchart_nodes(self):
        pts = self.spin_size.value() * 28.3465
        n = self._run_action("resize", pts)
        self._set_status(f"✅ 节点大小设为 {self.spin_size.value()} cm，处理 {n} 张工作表")

    def change_text_color(self):
        n = self._run_action("text", self.btn_text_clr.get_rgb_int())
        self._set_status(f"✅ 文字颜色已修改，处理 {n} 张工作表")

    def change_font_size(self):
        n = self._run_action("fontsize", self.spin_font.value())
        self._set_status(f"✅ 字体大小设为 {self.spin_font.value()} pt，处理 {n} 张工作表")

    def change_fill_color(self):
        n = self._run_action("fill", self.btn_fill_clr.get_rgb_int())
        self._set_status(f"✅ 填充颜色已修改，处理 {n} 张工作表")

    def change_border_color(self):
        n = self._run_action("border", self.btn_border_clr.get_rgb_int())
        self._set_status(f"✅ 边框颜色已修改，处理 {n} 张工作表")

    def change_outline_weight(self):
        n = self._run_action("outline_weight", self.spin_outline.value())
        self._set_status(f"✅ 轮廓粗细设为 {self.spin_outline.value()} pt，处理 {n} 张工作表")

    def apply_all(self):
        actions = [
            ("connector",      self.btn_connector_clr.get_rgb_int()),
            ("text",           self.btn_text_clr.get_rgb_int()),
            ("fill",           self.btn_fill_clr.get_rgb_int()),
            ("border",         self.btn_border_clr.get_rgb_int()),
            ("resize",         self.spin_size.value() * 28.3465),
            ("fontsize",       self.spin_font.value()),
            ("outline_weight", self.spin_outline.value()),
        ]
        n = self._run_actions(actions)
        self._set_status(f"✅ 全部设置已应用，共处理 {n} 张工作表")


# ═════════════════════════════════════════════════════════════════════
#  MainWindow — 独立运行时的薄壳，直接复用 ExcelShapeWidget
# ═════════════════════════════════════════════════════════════════════
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Excel 图形批量处理工具")
        self.setMinimumWidth(640)

        # 核心 widget（显示标题栏，隐藏内置状态标签，改用 QStatusBar）
        self._widget = ExcelShapeWidget(self, show_header=True, show_status_bar=False)
        self.setCentralWidget(self._widget)

        # 状态栏
        self._status_bar = QStatusBar()
        self._status_bar.setStyleSheet(
            "background: #eef0fa; color: #2d52c4;"
            "font-size: 12px; border-top: 1px solid #d0d5e8;")
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("就绪 — 请打开 Excel 文件后再执行操作")

        # 将 widget 的信号接入窗口状态栏
        self._widget.statusMessage.connect(self._status_bar.showMessage)

        # 整体背景色
        self.setStyleSheet("QMainWindow { background: #f5f6fa; }")


# ─────────────────────────────────────────────
#  入口
# ─────────────────────────────────────────────
def main():
    app = QApplication(sys.argv)
    ratio = app.primaryScreen().devicePixelRatio()
    base_font_size = max(9, round(10 / ratio * 1.35))
    app.setFont(QFont("Microsoft YaHei UI", base_font_size))

    win = MainWindow()
    win.resize(660, 680)
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()