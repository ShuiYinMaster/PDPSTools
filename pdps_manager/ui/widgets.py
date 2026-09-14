# -*- coding: utf-8 -*-
"""通用控件：支持 Excel 粘贴的表格、拖放文件标签、可滚动文本弹窗。"""

import os
from typing import List, Dict

import pandas as pd
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QPlainTextEdit,
    QTableWidget, QTableWidgetItem, QLabel, QMenu, QFileDialog,
    QMessageBox, QApplication,
)


class ScrollableMessageBox(QDialog):
    """可滚动的文本弹窗：长内容放进只读文本框自动出滚动条，用小号等宽字体。

    用于替代会把超长文本直接塞进 QMessageBox 的弹窗（如删除断裂对象清单、
    使用说明），避免内容过多时超过屏幕显示高度；对话框可自由缩放。

    参数：
        title       窗口标题
        intro       顶部说明文字（可空，自动换行）
        content     可滚动内容（长清单 / 多行说明，可空）
        font_size   内容字号（默认 9，小号等宽字体）
        yes_text    确认按钮文字；为空则只显示 no_text 一个按钮
        no_text     取消/关闭按钮文字
        yes_color   确认按钮底色（默认红色系）
    """

    def __init__(self, title, intro="", content="", font_size=9, parent=None,
                 yes_text="", no_text="关闭", yes_color="#e74c3c"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(520, 320)
        self.setSizeGripEnabled(True)
        screen = QApplication.desktop().availableGeometry(self)

        lay = QVBoxLayout(self)
        lay.setSpacing(8)

        if intro:
            lbl = QLabel(intro)
            lbl.setWordWrap(True)
            lay.addWidget(lbl)

        if content:
            self.editor = QPlainTextEdit()
            self.editor.setReadOnly(True)
            self.editor.setPlainText(content)
            self.editor.setFont(QFont("Consolas", font_size))
            # 限制初始高度，防止内容过多时弹窗超过屏幕
            self.editor.setMaximumHeight(int(screen.height() * 0.55))
            self.editor.setMinimumHeight(120)
            lay.addWidget(self.editor, 1)

        row = QHBoxLayout()
        row.addStretch()
        self.btn_no = QPushButton(no_text)
        self.btn_no.setMinimumWidth(84)
        row.addWidget(self.btn_no)
        self.btn_no.clicked.connect(self.reject)
        if yes_text:
            self.btn_yes = QPushButton(yes_text)
            self.btn_yes.setMinimumWidth(84)
            self.btn_yes.setStyleSheet(
                f"QPushButton {{ background: {yes_color}; color: white; font-weight: bold;"
                f" border: none; border-radius: 4px; padding: 6px 16px; }}"
                f"QPushButton:hover {{ background: {yes_color}; opacity: 0.85; }}")
            row.addWidget(self.btn_yes)
            self.btn_yes.clicked.connect(self.accept)
        lay.addLayout(row)

        # 初始尺寸同样受屏幕约束
        self.resize(min(720, int(screen.width() * 0.6)),
                    min(480, int(screen.height() * 0.7)))


class EditableTableWidget(QTableWidget):
    """支持 Excel 粘贴的可编辑表格。

    优化点：用 `_updating` 标志包裹所有批量写入操作，批量过程中不逐格触发
    data_changed，写完再统一发一次，避免粘贴大量行时反复重算统计造成卡顿。
    """

    data_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._updating = False
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        # 改为走 _on_item_changed，使 _updating 真正生效（原代码的 lambda 会无视它）
        self.itemChanged.connect(self._on_item_changed)

    def _on_item_changed(self, *_):
        if not self._updating:
            self.data_changed.emit()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_V and event.modifiers() == Qt.ControlModifier:
            self._paste_from_clipboard()
        else:
            super().keyPressEvent(event)

    def _show_context_menu(self, pos):
        menu = QMenu(self)
        menu.addAction("📋 粘贴 (Ctrl+V)", self._paste_from_clipboard)
        menu.addAction("➕ 添加行", self.add_row)
        menu.addAction("🗑️ 删除行", self.delete_selected_rows)
        menu.addAction("🗑️ 清空数据", self.delete_all_rows)
        menu.addSeparator()
        menu.addAction("📊 从Excel文件导入", self._paste_from_excel_file)
        menu.exec_(self.mapToGlobal(pos))

    def add_row(self):
        defaults = ["New_Gun", "PmWeldLocationOperation", "0", "0", "0", "0", "0", "0", "待插入"]
        self._updating = True
        try:
            row = self.rowCount()
            self.insertRow(row)
            for col, val in enumerate(defaults):
                self.setItem(row, col, QTableWidgetItem(val))
        finally:
            self._updating = False
        self.data_changed.emit()

    def delete_selected_rows(self):
        rows = sorted(set(i.row() for i in self.selectedItems()), reverse=True)
        self._updating = True
        try:
            for r in rows:
                self.removeRow(r)
        finally:
            self._updating = False
        self.data_changed.emit()

    def delete_all_rows(self):
        self._updating = True
        try:
            self.setRowCount(0)
        finally:
            self._updating = False
        self.data_changed.emit()

    def _paste_from_clipboard(self):
        text = QApplication.clipboard().text()
        if not text:
            return
        self._updating = True
        try:
            lines = text.strip().split('\n')
            start_row = self.currentRow() if self.currentRow() >= 0 else self.rowCount()
            for line in lines:
                vals = line.split('\t') if '\t' in line else line.split()
                if len(vals) >= 8:
                    final = vals[:8] + ["待插入"]
                elif len(vals) >= 7:
                    final = [vals[0], "PmWeldLocationOperation"] + vals[1:7] + ["待插入"]
                else:
                    while len(vals) < 7:
                        vals.append("0")
                    final = [vals[0], "PmWeldLocationOperation"] + vals[1:7] + ["待插入"]
                if start_row >= self.rowCount():
                    self.insertRow(self.rowCount())
                for col, v in enumerate(final[:9]):
                    self.setItem(start_row, col, QTableWidgetItem(v))
                start_row += 1
        finally:
            self._updating = False
        self.data_changed.emit()

    def _paste_from_excel_file(self):
        fp, _ = QFileDialog.getOpenFileName(
            self, "选择 Excel 文件", "",
            "Excel 文件 (*.xlsx *.xls);;所有文件 (*.*)")
        if not fp:
            return
        self._updating = True
        try:
            df = pd.read_excel(fp)
            required = ['Name', 'X', 'Y', 'Z']
            missing = [c for c in required if c not in df.columns]
            if missing:
                QMessageBox.critical(self, "错误", f"Excel 缺少必需列: {', '.join(missing)}")
                return
            self.setRowCount(0)
            for _, row in df.iterrows():
                if pd.isna(row['Name']):
                    continue
                r = self.rowCount()
                self.insertRow(r)
                vals = [
                    str(row['Name']).strip(),
                    str(row.get('Type', 'PmWeldLocationOperation')).strip(),
                    str(row.get('X', 0)), str(row.get('Y', 0)), str(row.get('Z', 0)),
                    str(row.get('Rx', 0)), str(row.get('Ry', 0)), str(row.get('Rz', 0)),
                    "待插入"
                ]
                for col, v in enumerate(vals):
                    self.setItem(r, col, QTableWidgetItem(v))
            QMessageBox.information(self, "成功", f"已导入 {self.rowCount()} 行")
        except Exception as e:
            QMessageBox.critical(self, "错误", str(e))
        finally:
            self._updating = False
        self.data_changed.emit()

    def get_all_data(self) -> List[Dict]:
        data = []
        for r in range(self.rowCount()):
            try:
                row = {
                    'Name':   (self.item(r, 0) or QTableWidgetItem()).text(),
                    'Type':   (self.item(r, 1) or QTableWidgetItem()).text(),
                    'X':      float((self.item(r, 2) or QTableWidgetItem("0")).text()),
                    'Y':      float((self.item(r, 3) or QTableWidgetItem("0")).text()),
                    'Z':      float((self.item(r, 4) or QTableWidgetItem("0")).text()),
                    'Rx':     float((self.item(r, 5) or QTableWidgetItem("0")).text()),
                    'Ry':     float((self.item(r, 6) or QTableWidgetItem("0")).text()),
                    'Rz':     float((self.item(r, 7) or QTableWidgetItem("0")).text()),
                    'Status': (self.item(r, 8) or QTableWidgetItem()).text(),
                }
                data.append(row)
            except ValueError:
                continue
        return data

    def update_status(self, row_index: int, status: str):
        self._updating = True  # 更新状态列不触发统计刷新
        try:
            if row_index < self.rowCount():
                self.setItem(row_index, 8, QTableWidgetItem(status))
        finally:
            self._updating = False


class DropFileLabel(QLabel):
    """支持拖放和点击的文件选择标签。"""

    file_dropped = pyqtSignal(str)
    clicked = pyqtSignal()

    ACCEPTED_EXTS = {'.catpart', '.catproduct', '.cgr'}

    _STYLE_IDLE = ("border: 2px dashed #aaa; border-radius: 6px; background: white;"
                   "color: #666; font-size: 11px; padding: 6px;")
    _STYLE_HOVER = ("border: 2px dashed #27ae60; border-radius: 6px; background: #eafaf1;"
                    "color: #27ae60; font-size: 11px; padding: 6px;")

    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls and os.path.splitext(urls[0].toLocalFile())[1].lower() in self.ACCEPTED_EXTS:
                event.acceptProposedAction()
                self.setStyleSheet(self._STYLE_HOVER)
                return
        event.ignore()

    def dragLeaveEvent(self, event):
        self.setStyleSheet(self._STYLE_IDLE)

    def dropEvent(self, event):
        self.setStyleSheet(self._STYLE_IDLE)
        urls = event.mimeData().urls()
        if urls:
            fp = urls[0].toLocalFile()
            if os.path.splitext(fp)[1].lower() in self.ACCEPTED_EXTS:
                self.file_dropped.emit(fp)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
