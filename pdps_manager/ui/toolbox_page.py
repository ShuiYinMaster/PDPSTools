# -*- coding: utf-8 -*-
"""页面 2：CATIA 焊点处理工具箱（焊点导出 + 几何体重心提取）。"""

import os
import subprocess
from contextlib import contextmanager

import pandas as pd
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox,
    QTreeWidget, QTreeWidgetItem, QHeaderView, QScrollArea, QGroupBox,
    QMessageBox, QFileDialog, QApplication, QDialog, QFormLayout,
    QLineEdit, QSpinBox, QDialogButtonBox, QProgressBar,
)

from ..config import PYCATIA_AVAILABLE
from ..catia_utils import active_3dxml_path, connect_active_catia
from ..core import CATIAWeldPointExtractor, CATIASphereScanner
from .style import make_btn, status_style
from .widgets import DropFileLabel


class RenameDialog(QDialog):
    """Configure a predictable weld-point naming pattern."""

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.setWindowTitle("自定义改名")
        self.setMinimumWidth(360)

        form = QFormLayout(self)
        self.main_name_edit = QLineEdit(str(config.get("main_name", "WeldPoint")))
        self.prefix_edit = QLineEdit(str(config.get("prefix", "")))
        self.suffix_edit = QLineEdit(str(config.get("suffix", "")))
        self.start_index_spin = QSpinBox()
        self.start_index_spin.setRange(0, 999999999)
        self.start_index_spin.setValue(int(config.get("start_index", 1)))
        self.width_spin = QSpinBox()
        self.width_spin.setRange(0, 12)
        self.width_spin.setValue(int(config.get("width", 4)))
        self.separator_edit = QLineEdit(str(config.get("separator", "_")))
        self.separator_edit.setMaxLength(4)

        form.addRow("主名称：", self.main_name_edit)
        form.addRow("前缀：", self.prefix_edit)
        form.addRow("后缀：", self.suffix_edit)
        form.addRow("起始序号：", self.start_index_spin)
        form.addRow("序号位数：", self.width_spin)
        form.addRow("分隔符：", self.separator_edit)

        self.preview_label = QLabel()
        self.preview_label.setStyleSheet("color:#666;padding:4px 0;")
        form.addRow("示例：", self.preview_label)

        for widget in (
            self.main_name_edit,
            self.prefix_edit,
            self.suffix_edit,
            self.separator_edit,
        ):
            widget.textChanged.connect(self._update_preview)
        self.start_index_spin.valueChanged.connect(self._update_preview)
        self.width_spin.valueChanged.connect(self._update_preview)
        self._update_preview()

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _update_preview(self):
        number = str(self.start_index_spin.value())
        width = self.width_spin.value()
        if width:
            number = number.zfill(width)
        name = (
            f"{self.prefix_edit.text()}"
            f"{self.main_name_edit.text() or 'WeldPoint'}"
            f"{self.separator_edit.text()}{number}"
            f"{self.suffix_edit.text()}"
        )
        self.preview_label.setText(name)

    def values(self):
        return {
            "main_name": self.main_name_edit.text().strip() or "WeldPoint",
            "prefix": self.prefix_edit.text(),
            "suffix": self.suffix_edit.text(),
            "start_index": self.start_index_spin.value(),
            "width": self.width_spin.value(),
            "separator": self.separator_edit.text(),
        }


class ToolBoxPage(QWidget):
    def __init__(self, conn_manager=None, parent=None):
        super().__init__(parent)
        self.conn = conn_manager           # 共享连接管理器（可为 None 时回退自连）
        self.catia_app = None
        self.weld_extractor = CATIAWeldPointExtractor()
        self.sphere_scanner = CATIASphereScanner()
        self._setup_ui()
        if self.conn is not None:
            self.conn.connection_changed.connect(self._on_conn_changed)
            self._apply_connection(self.conn.app, self.conn.conn_type)
        else:
            self.try_auto_connect_catia()

    # ── UI ────────────────────────────────────────────────────────────────
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        title_bar = QFrame()
        title_bar.setStyleSheet("background:#34495e;")
        title_bar.setFixedHeight(52)
        tb_layout = QHBoxLayout(title_bar)
        title_text = "🛠️ CATIA 焊点处理工具"
        if not PYCATIA_AVAILABLE:
            title_text += "  ⚠️ pycatia 未安装"
        lbl = QLabel(title_text)
        lbl.setStyleSheet("color:white;font-size:15px;font-weight:bold;")
        tb_layout.addWidget(lbl)
        layout.addWidget(title_bar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        container.setStyleSheet("background:#ecf0f1;")
        main_layout = QVBoxLayout(container)
        main_layout.setContentsMargins(16, 12, 16, 12)
        main_layout.setSpacing(10)

        # 工具1：焊点导出
        weld_group = QGroupBox("📍 焊点数据工作流")
        weld_group.setStyleSheet("QGroupBox{font-size:13px;font-weight:bold;}")
        weld_v = QVBoxLayout(weld_group)
        weld_v.setContentsMargins(10, 8, 10, 10)
        weld_v.setSpacing(8)

        # 按功能横向分成三组，中间用纵向分割线区分。
        workflow_row = QHBoxLayout()
        workflow_row.setSpacing(10)

        source_group = QHBoxLayout()
        source_group.setSpacing(5)
        source_group.addWidget(make_btn("🔗 连接 CATIA", "#3498db", self.connect_catia, height=32))
        source_group.addWidget(make_btn("📊 提取焊点", "#e67e22", self.extract_weld_points, height=32))
        source_group.addWidget(make_btn("🔩 提取BiW点", "#d35400", self.extract_biw_points, height=32))
        self.biw_append_chk = QCheckBox("追加")
        self.biw_append_chk.setToolTip("追加 BiW 点，并与现有焊点合并")
        source_group.addWidget(self.biw_append_chk)
        source_group.addWidget(make_btn("📥 导入数据", "#9b59b6", self.import_weld_data, height=32))
        workflow_row.addLayout(source_group, 3)

        source_rename_line = QFrame()
        source_rename_line.setFrameShape(QFrame.VLine)
        source_rename_line.setFrameShadow(QFrame.Sunken)
        workflow_row.addWidget(source_rename_line)

        rename_group = QHBoxLayout()
        rename_group.setSpacing(5)
        rename_group.addWidget(QLabel("命名："))
        self.dynamic_rename_chk = QCheckBox("动态重命名")
        self.dynamic_rename_chk.setToolTip("勾选后按标准规则命名；取消后恢复原始名称")
        self.dynamic_rename_chk.stateChanged.connect(self._on_dynamic_rename_changed)
        rename_group.addWidget(self.dynamic_rename_chk)
        self.custom_rename_btn = make_btn(
            "✏ 自定义改名", "#8e44ad", self.custom_rename, height=32)
        rename_group.addWidget(self.custom_rename_btn)
        rename_group.addWidget(QLabel("取消可恢复原名"))
        workflow_row.addLayout(rename_group, 2)

        rename_action_line = QFrame()
        rename_action_line.setFrameShape(QFrame.VLine)
        rename_action_line.setFrameShadow(QFrame.Sunken)
        workflow_row.addWidget(rename_action_line)

        action_group = QHBoxLayout()
        action_group.setSpacing(5)
        action_group.addWidget(make_btn("💾 生成点球", "#27ae60", self.newball, height=32))
        action_group.addWidget(make_btn("💾 导出 Excel", "#27ae60", self.export_welds_to_excel, height=32))
        workflow_row.addLayout(action_group, 1)
        weld_v.addLayout(workflow_row)

        self.weld_table = QTreeWidget()
        self.weld_table.setColumnCount(8)
        self.weld_table.setHeaderLabels(
            ["Class", "ExternalId", "Name", "Location", "X", "Y", "Z", "Type"])
        self.weld_table.setFixedHeight(200)
        self.weld_table.header().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.weld_table.header().setSectionResizeMode(3, QHeaderView.Stretch)
        weld_v.addWidget(self.weld_table)
        main_layout.addWidget(weld_group)

        # 工具2：几何体重心
        sphere_group = QGroupBox("🔵 几何体重心提取")
        sphere_group.setStyleSheet("QGroupBox{font-size:13px;font-weight:bold;}")
        sphere_v = QVBoxLayout(sphere_group)
        sphere_v.setContentsMargins(10, 8, 10, 10)
        sphere_v.setSpacing(8)

        # 几何体操作同样按“扫描 / 生成 / 输出”三组横向排列。
        geometry_workflow_row = QHBoxLayout()
        geometry_workflow_row.setSpacing(10)

        geometry_scan_group = QHBoxLayout()
        geometry_scan_group.setSpacing(5)
        geometry_scan_group.addWidget(make_btn("📦 扫描3DXML", "#16a085", self.scan_3dxml, height=32))
        geometry_scan_group.addWidget(make_btn("🔍 扫描几何体", "#9b59b6", self.scan_geometry, height=32))
        geometry_scan_group.addWidget(make_btn("🔵 扫描图形集", "#3498db",
                                               self.scan_geometry_set, height=32))
        geometry_workflow_row.addLayout(geometry_scan_group, 3)

        geometry_scan_line = QFrame()
        geometry_scan_line.setFrameShape(QFrame.VLine)
        geometry_scan_line.setFrameShadow(QFrame.Sunken)
        geometry_workflow_row.addWidget(geometry_scan_line)

        geometry_generate_group = QHBoxLayout()
        geometry_generate_group.setSpacing(5)
        geometry_generate_group.addWidget(make_btn("📝 生成重心点", "#e74c3c", self.generate_points, height=32))
        geometry_workflow_row.addLayout(geometry_generate_group, 1)

        geometry_generate_line = QFrame()
        geometry_generate_line.setFrameShape(QFrame.VLine)
        geometry_generate_line.setFrameShadow(QFrame.Sunken)
        geometry_workflow_row.addWidget(geometry_generate_line)

        geometry_output_group = QHBoxLayout()
        geometry_output_group.setSpacing(5)
        geometry_output_group.addWidget(make_btn("📤 传入焊点表", "#8e44ad",
                                                self.transfer_sphere_data_to_weld_table, height=32))
        geometry_output_group.addWidget(make_btn("💾 导出坐标", "#27ae60", self.export_sphere_coords, height=32))
        geometry_workflow_row.addLayout(geometry_output_group, 2)
        sphere_v.addLayout(geometry_workflow_row)

        self.threedxml_drop = DropFileLabel(
            "拖放 3DXML 文件到此处\n或点击自动读取当前 CATIA")
        self.threedxml_drop.ACCEPTED_EXTS = {".3dxml"}
        self.threedxml_drop.setStyleSheet(DropFileLabel._STYLE_IDLE)
        self.threedxml_drop.setAlignment(Qt.AlignCenter)
        self.threedxml_drop.setFixedHeight(42)
        self.threedxml_drop.file_dropped.connect(self._scan_3dxml_path)
        self.threedxml_drop.clicked.connect(self.scan_3dxml)
        sphere_v.addWidget(self.threedxml_drop)

        self.sphere_table = QTreeWidget()
        self.sphere_table.setColumnCount(7)
        self.sphere_table.setHeaderLabels(
            ["Name", "X", "Y", "Z", "Radius", "几何类型", "焊点层数"])
        self.sphere_table.setFixedHeight(200)
        self.sphere_table.header().setSectionResizeMode(0, QHeaderView.Stretch)
        sphere_v.addWidget(self.sphere_table)
        main_layout.addWidget(sphere_group)

        main_layout.addStretch()
        scroll.setWidget(container)
        layout.addWidget(scroll)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # CATIA 同步接口通常无法提供可靠百分比
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("处理中，请稍候...")
        self.progress_bar.setFixedHeight(18)
        self.progress_bar.setStyleSheet(
            "QProgressBar{border:1px solid #bdc3c7;border-radius:3px;"
            "background:#ecf0f1;color:#34495e;text-align:center;}"
            "QProgressBar::chunk{background:#3498db;}"
        )
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel("就绪 | 使用 pycatia")
        self.status_label.setStyleSheet(status_style("#34495e", pad="4px 10px", font_size=11))
        layout.addWidget(self.status_label)

    def _set_status(self, text, bg):
        self.status_label.setText(text)
        self.status_label.setStyleSheet(status_style(bg, pad="4px 10px", font_size=11))

    def _begin_progress(self, text):
        self.progress_bar.setFormat(text)
        self.progress_bar.show()
        self.progress_bar.raise_()
        QApplication.processEvents()

    def _end_progress(self):
        self.progress_bar.hide()
        QApplication.processEvents()

    @contextmanager
    def _waiting(self, text):
        """Show a busy progress bar around synchronous CATIA/file operations."""
        self._begin_progress(text)
        try:
            yield
        finally:
            self._end_progress()

    # ── CATIA 连接 ────────────────────────────────────────────────────────
    def _apply_connection(self, app, conn_type):
        """根据共享连接状态刷新本页：重新绑定工具对象 + 更新状态条。"""
        self.catia_app = app
        if app:
            self.weld_extractor.set_catia_app(app, conn_type)
            self.sphere_scanner.set_catia_app(app)
            self._set_status(f"✅ CATIA 已连接 ({conn_type})", "#27ae60")
        else:
            self._set_status("⚠️ CATIA 未连接 (请开启 CATIA 后点击连接)", "#e74c3c")

    def _on_conn_changed(self, app, conn_type):
        self._apply_connection(app, conn_type)

    def try_auto_connect_catia(self):
        """启动时静默尝试连接已运行的 CATIA（仅在无共享管理器时使用）。"""
        self._set_status("🔄 正在自动检测 CATIA 实例...", "#f39c12")
        with self._waiting("正在连接 CATIA，请稍候..."):
            try:
                catia_app, conn_type = connect_active_catia()
            except Exception as e:
                self._set_status(f"❌ 连接异常: {str(e)[:20]}", "#c0392b")
                return
        self._apply_connection(catia_app, conn_type or "")

    def connect_catia(self):
        """手动点击“连接 CATIA”：走共享管理器，所有页签同步刷新。"""
        if self.conn is None:
            self.try_auto_connect_catia()
        else:
            with self._waiting("正在重新连接 CATIA，请稍候..."):
                try:
                    self.conn.reconnect()   # 成功后经 connection_changed 自动刷新本页
                except Exception as e:
                    self._set_status(f"❌ 连接异常: {str(e)[:20]}", "#c0392b")
                    QMessageBox.critical(self, "连接异常", str(e))
                    return
        if self.catia_app:
            QMessageBox.information(self, "连接成功", "已成功建立与 CATIA 的通讯链路。")
        else:
            QMessageBox.warning(self, "连接失败",
                                "未检测到运行中的 CATIA 实例。\n\n"
                                "请检查：\n"
                                "1. CATIA 是否已经完全启动？\n"
                                "2. 是否有多个 CATIA 进程冲突？")

    # ── 焊点 ─────────────────────────────────────────────────────────────
    def _refresh_weld_table(self, data):
        """用统一逻辑把焊点数据(普通焊点 / BiW焊点 / 导入)刷新到表格。"""
        self.weld_table.clear()
        for d in data:
            self.weld_table.addTopLevelItem(QTreeWidgetItem([
                d["Class"], d["ExternalId"], d["Name"],
                d["Location"], str(d["X"]), str(d["Y"]), str(d["Z"]), d["Type"]
            ]))

    def extract_weld_points(self):
        if not self.catia_app:
            QMessageBox.warning(self, "警告", "请先连接 CATIA")
            return
        with self._waiting("正在提取焊点，请稍候..."):
            self.weld_extractor.extract_weld_points(auto_rename=False)
        self._apply_current_rename()
        self.status_label.setText(self.weld_extractor.get_status())

    def extract_biw_points(self):
        """提取 BiW(Assembly Joints 下的 Joint Element)焊点，填入同一张表。"""
        if not self.catia_app:
            QMessageBox.warning(self, "警告", "请先连接 CATIA")
            return
        append = self.biw_append_chk.isChecked()
        with self._waiting("正在提取 BiW 点，请稍候..."):
            self.weld_extractor.extract_biw_fasteners(append=append)
        self._apply_current_rename()
        self.status_label.setText(self.weld_extractor.get_status())

    def export_welds_to_excel(self):
        data = [
            {key: value for key, value in item.items() if not key.startswith("_")}
            for item in self.weld_extractor.weld_points_data
        ]
        if not data:
            QMessageBox.warning(self, "警告", "没有数据可导出")
            return
        # 文档名作为默认文件名（兼容 pycatia / win32com，取不到则用通用名）
        product_name = "weld_points"
        try:
            catia = self.weld_extractor.catia_app
            if self.weld_extractor.connection_type == 'pycatia':
                product_name = catia.active_document.product.name
            else:
                product_name = catia.ActiveDocument.Product.Name
        except Exception:
            pass
        self._export_data(data, f"{product_name}.csv")

    def import_weld_data(self):
        fp, _ = QFileDialog.getOpenFileName(
            self, "选择数据文件", "",
            "Excel文件 (*.xlsx *.xls);;CSV文件 (*.csv);;所有文件 (*.*)")
        if not fp:
            return
        self._begin_progress("正在导入数据，请稍候...")
        try:
            df = pd.read_csv(fp) if fp.endswith('.csv') else pd.read_excel(fp)
            cols = df.columns.tolist()
            x_col = next((c for c in cols if c.upper() == 'X'), None)
            y_col = next((c for c in cols if c.upper() == 'Y'), None)
            z_col = next((c for c in cols if c.upper() == 'Z'), None)
            if not all([x_col, y_col, z_col]):
                QMessageBox.critical(self, "错误", "文件缺少 X/Y/Z 列")
                return
            name_col = next((c for c in cols if c.lower() in ('name', '名称')), None)
            self.weld_extractor.weld_points_data = []
            ok, fail = 0, 0
            for i, row in df.iterrows():
                try:
                    x, y, z = float(row[x_col]), float(row[y_col]), float(row[z_col])
                    name = str(row[name_col]) if name_col else f"Point_{i+1}"
                    d = {"Class": "PmWeldPoint", "ExternalId": name, "Name": name,
                         "Location": f"{x:.3f},{y:.3f},{z:.3f}",
                         "X": round(x, 3), "Y": round(y, 3), "Z": round(z, 3), "Type": "RSW"}
                    self.weld_extractor.weld_points_data.append(d)
                    ok += 1
                except Exception:
                    fail += 1
            self._apply_current_rename()
            QMessageBox.information(self, "导入成功", f"成功: {ok}，失败: {fail}")
            self.status_label.setText(f"已导入 {ok} 个焊点")
        except Exception as e:
            QMessageBox.critical(self, "导入失败", str(e))
        finally:
            self._end_progress()

    def newball(self):
        self.weld_extractor.generate_spheres_from_points(self)

    def _apply_current_rename(self):
        if self.dynamic_rename_chk.isChecked():
            mode = "standard"
        elif self.weld_extractor.rename_mode == "custom":
            mode = "custom"
        else:
            mode = "original"
        self.weld_extractor.apply_rename(mode)
        self._refresh_weld_table(self.weld_extractor.weld_points_data)

    def _on_dynamic_rename_changed(self, state):
        mode = "standard" if state else "original"
        self.weld_extractor.apply_rename(mode)
        self._refresh_weld_table(self.weld_extractor.weld_points_data)
        self.status_label.setText(
            "已启用动态重命名" if state else "已恢复原始名称")

    def custom_rename(self):
        dialog = RenameDialog(self.weld_extractor.custom_rename_config, self)
        if dialog.exec_() != QDialog.Accepted:
            return
        config = dialog.values()
        self.dynamic_rename_chk.blockSignals(True)
        self.dynamic_rename_chk.setChecked(False)
        self.dynamic_rename_chk.blockSignals(False)
        self.weld_extractor.apply_rename("custom", config)
        self._refresh_weld_table(self.weld_extractor.weld_points_data)
        self.status_label.setText("已应用自定义命名")

    # ── 几何体重心 ────────────────────────────────────────────────────────
    def scan_geometry(self):
        if not self.catia_app:
            QMessageBox.warning(self, "警告", "请先连接 CATIA")
            return
        try:
            with self._waiting("正在扫描几何体，请稍候..."):
                data = self.sphere_scanner.scan_all_bodies()
            self._refresh_sphere_table(data)
            self.status_label.setText(f"已扫描 {len(data)} 个几何体")
        except Exception as e:
            QMessageBox.critical(self, "错误", str(e))

    def scan_geometry_set(self):
        """扫描当前 CATIA 几何图形集中的球体，仅更新下方表格。"""
        if not self.catia_app:
            QMessageBox.warning(self, "警告", "请先连接 CATIA")
            return
        try:
            with self._waiting("正在扫描图形集，请稍候..."):
                data = self.sphere_scanner.scan_geometrical_set_spheres()
            self._refresh_sphere_table(data)
            self.status_label.setText(
                f"已扫描 {len(data)} 个几何图形集球体；请确认表格后点击“生成重心点”")
        except Exception as e:
            QMessageBox.critical(self, "扫描图形集失败", str(e))

    def scan_3dxml(self):
        """Read the active CATIA 3DXML, then fall back to a file chooser."""
        fp = active_3dxml_path(self.catia_app)
        if not fp:
            fp, _ = QFileDialog.getOpenFileName(
                self, "选择3DXML文件", "", "3DXML文件 (*.3dxml);;所有文件 (*.*)")
        if not fp:
            return
        self._scan_3dxml_path(fp)

    def _scan_3dxml_path(self, fp):
        try:
            with self._waiting("正在解析 3DXML，请稍候..."):
                data = self.sphere_scanner.scan_3dxml(fp)
            self._refresh_sphere_table(data)
            recognized = sum(
                bool(item.get("GeometryType") and item.get("WeldLayers"))
                for item in data
            )
            self.status_label.setText(
                f"3DXML已读取 {len(data)} 个几何中心（按形体阈值；已识别 {recognized} 个形体）")
        except Exception as e:
            QMessageBox.critical(self, "3DXML读取失败", str(e))

    def _refresh_sphere_table(self, data):
        self.sphere_table.clear()
        for d in data:
            self.sphere_table.addTopLevelItem(QTreeWidgetItem([
                d["Name"], d["X"], d["Y"], d["Z"], d["Radius"],
                d.get("GeometryType", "未知"), d.get("WeldLayers", "")]))

    def _read_sphere_table(self):
        """Convert the visible geometry table into point-generation rows."""
        rows = []
        invalid_rows = []
        for row in range(self.sphere_table.topLevelItemCount()):
            item = self.sphere_table.topLevelItem(row)
            if item is None:
                invalid_rows.append(row + 1)
                continue
            try:
                coords = tuple(float(item.text(column).strip()) for column in (1, 2, 3))
            except (TypeError, ValueError):
                invalid_rows.append(row + 1)
                continue
            rows.append({
                "Name": item.text(0).strip() or f"TableRow_{row + 1}",
                "X": item.text(1).strip(),
                "Y": item.text(2).strip(),
                "Z": item.text(3).strip(),
                "Radius": item.text(4).strip(),
                "GeometryType": item.text(5).strip(),
                "WeldLayers": item.text(6).strip(),
                "_coords": coords,
            })
        return rows, invalid_rows

    def generate_points(self):
        table_data, invalid_rows = self._read_sphere_table()
        if invalid_rows:
            row_text = ", ".join(str(row) for row in invalid_rows)
            QMessageBox.warning(self, "坐标无效", f"表格第 {row_text} 行的 X/Y/Z 不是有效数字")
            return
        if not table_data:
            QMessageBox.warning(self, "警告", "表格中没有可生成的几何中心")
            return
        if not self.catia_app:
            QMessageBox.warning(self, "警告", "请先连接 CATIA")
            return
        try:
            with self._waiting("正在生成重心点，请稍候..."):
                count = self.sphere_scanner.generate_points_at_centers(points_data=table_data)
            QMessageBox.information(self, "完成", f"已根据表格生成 {count} 个重心点")
        except Exception as e:
            QMessageBox.critical(self, "生成重心点失败", str(e))

    def transfer_sphere_data_to_weld_table(self):
        table_data, invalid_rows = self._read_sphere_table()
        if invalid_rows:
            row_text = ", ".join(str(row) for row in invalid_rows)
            QMessageBox.warning(self, "坐标无效", f"表格第 {row_text} 行的 X/Y/Z 不是有效数字")
            return
        if not table_data:
            QMessageBox.warning(self, "警告", "几何体表格中没有可传输的点数据")
            return

        with self._waiting("正在传入焊点表，请稍候..."):
            transferred = []
            for row in table_data:
                name = row["Name"]
                layers = row.get("WeldLayers") or "未知层数"
                transferred.append({
                    "Class": "PmWeldPoint",
                    "ExternalId": name,
                    "Name": name,
                    "Location": f"{row['X']},{row['Y']},{row['Z']}",
                    "X": row["X"],
                    "Y": row["Y"],
                    "Z": row["Z"],
                    "Type": layers,
                    # 用内部标记识别重复传入，避免污染焊点表的导出字段。
                    "_GeometryCenterTransfer": True,
                })

            # Replace the previous geometry-center transfer as a group.  This
            # prevents repeated clicks from duplicating rows while preserving
            # weld points that came from the upper-table extraction workflows.
            merged = [
                item for item in (self.weld_extractor.weld_points_data or [])
                if not item.get("_GeometryCenterTransfer")
                and item.get("Class") != "GeometryCenter"
                and item.get("Type") != "3DXML几何中心"
            ]
            merged.extend(transferred)
            self.weld_extractor.weld_points_data = merged
            self._apply_current_rename()
        self.status_label.setText(
            f"已将 {len(transferred)} 个几何中心传入上方焊点表（上方共 {len(merged)} 行）")

    def export_sphere_coords(self):
        sphere_data = self.sphere_scanner.get_data()
        if not sphere_data:
            QMessageBox.warning(self, "警告", "没有数据可导出")
            return
        export_data = [{k: v for k, v in s.items() if k != "_coords"} for s in sphere_data]
        self._export_data(export_data, "几何体重心数据.csv")

    def _export_data(self, data, default_name):
        fp, _ = QFileDialog.getSaveFileName(
            self, "导出数据", default_name,
            "CSV文件 (*.csv);;Excel文件 (*.xlsx);;所有文件 (*.*)")
        if not fp:
            return
        self._begin_progress("正在导出数据，请稍候...")
        try:
            if fp.endswith('.csv'):
                pd.DataFrame(data).to_csv(fp, index=False, encoding='utf-8-sig')
            else:
                pd.DataFrame(data).to_excel(fp, index=False)
            box = QMessageBox(self)
            box.setWindowTitle("成功")
            box.setIcon(QMessageBox.Information)
            box.setText(f"导出完成\n文件: {fp}")
            locate_btn = box.addButton("📂 在资源管理器中定位", QMessageBox.ActionRole)
            box.addButton("关闭", QMessageBox.RejectRole)
            box.exec_()
            if box.clickedButton() is locate_btn:
                try:
                    subprocess.run(['explorer', '/select,', os.path.normpath(fp)])
                except Exception:
                    pass
        except PermissionError:
            QMessageBox.critical(self, "错误", "文件被占用，请关闭后重试")
        except Exception as e:
            QMessageBox.critical(self, "错误", str(e))
        finally:
            self._end_progress()
