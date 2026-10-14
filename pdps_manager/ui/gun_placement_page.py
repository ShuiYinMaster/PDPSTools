# -*- coding: utf-8 -*-
"""页面 3：CATIA 焊枪批量插入工具。"""

import os
import json
from datetime import datetime

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QWidget, QFrame, QSplitter, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPlainTextEdit, QCheckBox, QRadioButton, QButtonGroup,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QProgressBar, QGroupBox,
    QMessageBox, QFileDialog, QApplication,
)

from ..config import PYCATIA_AVAILABLE
from ..catia_utils import connect_active_catia
from ..core.gun_placer import CatiaGunPlacer
from .style import make_btn, status_style
from .widgets import EditableTableWidget, DropFileLabel, ScrollableMessageBox


class CatiaGunPlacementPage(QWidget):
    def __init__(self, conn_manager=None, parent=None):
        super().__init__(parent)
        self.conn = conn_manager        # 共享连接管理器（可为 None 时回退自连）
        self.gun_placer = CatiaGunPlacer()
        self.catia_app = None
        self._batch_running = False     # 批量插入运行态，防重入
        self._setup_ui()
        if self.conn is not None:
            self.conn.connection_changed.connect(self._on_conn_changed)
            self._apply_connection(self.conn.app, self.conn.conn_type, silent=True)
        else:
            self.try_auto_connect_catia()

    # ── UI ────────────────────────────────────────────────────────────────
    def _setup_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)

        ctrl_frame = QFrame()
        ctrl_frame.setStyleSheet("background:#2c3e50;")
        ctrl_layout = QVBoxLayout(ctrl_frame)
        ctrl_layout.setContentsMargins(10, 8, 10, 8)

        title = QLabel("🔧 CATIA 焊枪批量插入工具")
        title.setStyleSheet("color:white;font-size:14px;font-weight:bold;")
        title.setAlignment(Qt.AlignCenter)
        ctrl_layout.addWidget(title)

        # 旋转顺序
        rot_row = QHBoxLayout()
        rot_row.addWidget(QLabel("旋转顺序:", styleSheet="color:white;"))
        self.rotation_group = QButtonGroup(self)
        self.lbl_rotation_info = QLabel("当前: 库卡 (ZYX)")
        self.lbl_rotation_info.setStyleSheet("color:#f1c40f;")
        for text, val in [("库卡 (ZYX)", "kuka"), ("CATIA ZYX", "catia_zyx"), ("CATIA XYZ", "catia_xyz")]:
            rb = QRadioButton(text)
            rb.setStyleSheet("color:white;")
            rb.setProperty("value", val)
            self.rotation_group.addButton(rb)
            rot_row.addWidget(rb)
            if val == "kuka":
                rb.setChecked(True)  # 在连接信号之前设置默认值
        for btn in self.rotation_group.buttons():
            btn.toggled.connect(self.on_rotation_order_changed)
        rot_row.addWidget(self.lbl_rotation_info)
        rot_row.addStretch()
        ctrl_layout.addLayout(rot_row)

        # 坐标校正
        corr_row = QHBoxLayout()
        corr_row.addWidget(QLabel("坐标校正:", styleSheet="color:white;"))

        self.chk_use_tcp = QCheckBox("使用库卡 TCP 变换")
        self.chk_use_tcp.setStyleSheet("color:#f1c40f; font-weight:bold;")
        self.chk_use_tcp.toggled.connect(self.on_tcp_mode_changed)
        corr_row.addWidget(self.chk_use_tcp)

        self.corr_edits = {}
        self.corr_labels = {}
        for label in ["X", "Y", "Z", "Rx", "Ry", "Rz"]:
            lbl = QLabel(f"{label}:", styleSheet="color:white;")
            self.corr_labels[label] = lbl
            corr_row.addWidget(lbl)

            e = QLineEdit("0.0")
            e.setFixedWidth(80)
            e.setStyleSheet("color: white; background-color: rgba(255, 255, 255, 20);")
            self.corr_edits[label] = e
            corr_row.addWidget(e)

        corr_row.addWidget(make_btn("清除校正", "#7f8c8d", self.clear_correction))
        corr_row.addWidget(make_btn("保存配置", "#27ae60", self.save_correction_config))
        corr_row.addWidget(make_btn("加载配置", "#2980b9", self.load_correction_config))
        self.lbl_correction_status = QLabel("模式: 普通坐标偏移")
        self.lbl_correction_status.setStyleSheet("color:#f1c40f;")
        corr_row.addWidget(self.lbl_correction_status)
        ctrl_layout.addLayout(corr_row)

        # 主按钮行
        btn_row = QHBoxLayout()
        self._btn_connect = make_btn("🔌 连接 CATIA", "#3498db", self.connect_catia)
        self._btn_test = make_btn("🧪 测试插入", "#9b59b6", self.test_single_insert)
        self._btn_batch = make_btn("🚀 批量插入", "#e74c3c", self.start_batch_placement)
        self._btn_batch.setStyleSheet(self._btn_batch.styleSheet() + "font-weight:bold;font-size:14px;")
        btn_row.addWidget(self._btn_connect)
        btn_row.addWidget(make_btn("🔫 选择焊钳", "#e67e22", self.select_gun_template))
        btn_row.addWidget(make_btn("➕ 添加行", "#27ae60", self.add_row))
        btn_row.addWidget(make_btn("📋 导入Excel", "#16a085", self.paste_excel))
        btn_row.addWidget(self._btn_test)
        btn_row.addWidget(self._btn_batch)
        btn_row.addStretch()
        ctrl_layout.addLayout(btn_row)

        tip = QLabel("💡 提示: 双击单元格编辑 | 右键粘贴数据 | Ctrl+V 快速粘贴 | 支持从 Excel 复制粘贴")
        tip.setStyleSheet("color:#ecf0f1;font-size:10px;")
        ctrl_layout.addWidget(tip)
        root_layout.addWidget(ctrl_frame)

        # 主体：左表 + 右面板
        splitter = QSplitter(Qt.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(4, 4, 4, 4)
        left_layout.addWidget(QLabel("📋 插枪数据 (可编辑)",
                                     styleSheet="font-size:12px;font-weight:bold;"))

        self.data_table = EditableTableWidget()
        self.data_table.setColumnCount(9)
        self.data_table.setHorizontalHeaderLabels(
            ["名称", "类型", "X", "Y", "Z", "Rx (°)", "Ry (°)", "Rz (°)", "状态"])
        self.data_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.data_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.data_table.horizontalHeader().setSectionResizeMode(8, QHeaderView.ResizeToContents)
        self.data_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.data_table.setAlternatingRowColors(True)
        left_layout.addWidget(self.data_table)
        splitter.addWidget(left)

        # 右侧配置
        right = QWidget()
        right.setStyleSheet("background:#ecf0f1;")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(12, 8, 12, 8)

        config_group = QGroupBox("⚙️ 焊钳模板")
        config_v = QVBoxLayout(config_group)
        self.drop_label = DropFileLabel("📂 拖放焊钳文件到此处\n或点击选择文件\n(.CATPart / .CATProduct / .cgr)")
        self.drop_label.setFixedHeight(80)
        self.drop_label.setStyleSheet(DropFileLabel._STYLE_IDLE)
        self.drop_label.setAlignment(Qt.AlignCenter)
        self.drop_label.file_dropped.connect(self.on_gun_template_selected)
        self.drop_label.clicked.connect(self.select_gun_template)
        config_v.addWidget(self.drop_label)
        right_layout.addWidget(config_group)

        # 统计信息
        stats_group = QGroupBox("📊 统计信息")
        stats_v = QVBoxLayout(stats_group)

        row1 = QHBoxLayout()
        self.lbl_total = QLabel("总数: 0")
        self.lbl_total.setStyleSheet("font-weight:bold;")
        self.lbl_weld = QLabel("🔥 焊点: 0")
        self.lbl_weld.setStyleSheet("color:#e67e22;")
        self.lbl_via = QLabel("🔄 过渡点: 0")
        self.lbl_via.setStyleSheet("color:#8e44ad;")
        self.lbl_continuous = QLabel("➰ 连续点: 0")
        self.lbl_continuous.setStyleSheet("color:#16a085;")
        row1.addWidget(self.lbl_total)
        row1.addWidget(self._sep())
        row1.addWidget(self.lbl_weld)
        row1.addWidget(self._sep())
        row1.addWidget(self.lbl_via)
        row1.addWidget(self._sep())
        row1.addWidget(self.lbl_continuous)
        row1.addStretch()
        stats_v.addLayout(row1)

        row2 = QHBoxLayout()
        self.lbl_success = QLabel("成功: 0")
        self.lbl_success.setStyleSheet("color:green;")
        self.lbl_failed = QLabel("失败: 0")
        self.lbl_failed.setStyleSheet("color:red;")
        row2.addWidget(self.lbl_success)
        row2.addWidget(self._sep())
        row2.addWidget(self.lbl_failed)
        row2.addStretch()
        stats_v.addLayout(row2)
        right_layout.addWidget(stats_group)

        # 类型筛选
        filter_group = QGroupBox("🔽 插入筛选")
        filter_v = QVBoxLayout(filter_group)
        self.filter_all = QCheckBox("✓ 插入全部点")
        self.filter_all.setChecked(True)
        self.filter_all.toggled.connect(self.on_filter_all_changed)
        self.filter_weld = QCheckBox("🔥 仅插入焊点 (PmWeldLocationOperation)")
        self.filter_weld.toggled.connect(self.on_filter_type_changed)
        self.filter_via = QCheckBox("🔄 仅插入过渡点 (PmViaLocationOperation)")
        self.filter_via.toggled.connect(self.on_filter_type_changed)
        self.filter_continuous = QCheckBox("➰ 仅插入连续点 (PmContinuousFeatureOperation)")
        self.filter_continuous.toggled.connect(self.on_filter_type_changed)
        for w in [self.filter_all, self.filter_weld, self.filter_via, self.filter_continuous]:
            filter_v.addWidget(w)
        self.lbl_filter_info = QLabel("当前: 插入全部点")
        self.lbl_filter_info.setStyleSheet("color:#2980b9;font-size:10px;")
        filter_v.addWidget(self.lbl_filter_info)
        right_layout.addWidget(filter_group)

        # 进度
        right_layout.addWidget(QLabel("📊 执行进度", styleSheet="font-weight:bold;"))
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        right_layout.addWidget(self.progress_bar)
        self.lbl_progress = QLabel("等待开始...")
        self.lbl_progress.setStyleSheet("color:#7f8c8d;font-size:10px;")
        right_layout.addWidget(self.lbl_progress)

        # 日志
        right_layout.addWidget(QLabel("📝 操作日志", styleSheet="font-weight:bold;"))
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 9))
        self.log_text.setMaximumBlockCount(500)
        right_layout.addWidget(self.log_text)

        right_layout.addWidget(make_btn("❓ 使用说明", "#95a5a6", self.show_help))

        self.status_label = QLabel("就绪")
        self.status_label.setStyleSheet(status_style("#34495e"))
        right_layout.addWidget(self.status_label)

        splitter.addWidget(right)
        splitter.setSizes([850, 350])
        root_layout.addWidget(splitter)

        self.data_table.data_changed.connect(self._update_statistics)

    @staticmethod
    def _sep():
        s = QLabel("|")
        s.setStyleSheet("color:#ccc;")
        return s

    def _set_status(self, text, bg):
        self.status_label.setText(text)
        self.status_label.setStyleSheet(status_style(bg))

    def add_sample_data(self):
        samples = [
            ("Gun_001", "PmWeldLocationOperation", "100.0", "200.0", "300.0", "0", "0", "90", "待插入"),
            ("Via_001", "PmViaLocationOperation",  "150.0", "250.0", "350.0", "0", "45", "0", "待插入"),
            ("Gun_002", "PmWeldLocationOperation", "200.0", "300.0", "400.0", "30", "0", "0", "待插入"),
        ]
        for vals in samples:
            r = self.data_table.rowCount()
            self.data_table.insertRow(r)
            for col, v in enumerate(vals):
                self.data_table.setItem(r, col, QTableWidgetItem(v))

    def on_tcp_mode_changed(self, checked):
        if checked:
            self.corr_labels["Rx"].setText("A:")
            self.corr_labels["Ry"].setText("B:")
            self.corr_labels["Rz"].setText("C:")
            self.lbl_correction_status.setText("模式: 库卡 TCP 矩阵变换")
            self.log("🔄 切换至 [库卡 TCP 变换] 模式，此模式下变换不直接修改表格数据。")
        else:
            self.corr_labels["Rx"].setText("Rx:")
            self.corr_labels["Ry"].setText("Ry:")
            self.corr_labels["Rz"].setText("Rz:")
            self.lbl_correction_status.setText("模式: 普通坐标偏移")
            self.log("🔄 切换至 [普通坐标偏移] 模式。")

    # ── 旋转顺序 ──────────────────────────────────────────────────────────
    def on_rotation_order_changed(self):
        for btn in self.rotation_group.buttons():
            if btn.isChecked():
                order = btn.property("value")
                self.gun_placer.set_rotation_order(order)
                names = {'kuka': '库卡 (ZYX)', 'catia_zyx': 'CATIA ZYX', 'catia_xyz': 'CATIA XYZ'}
                self.lbl_rotation_info.setText(f"当前: {names.get(order, order)}")
                self.log(f"旋转顺序切换为: {names.get(order, order)}")
                break

    # ── 坐标校正 ──────────────────────────────────────────────────────────
    def get_correction_values(self):
        try:
            return {k: float(e.text() or 0) for k, e in self.corr_edits.items()}
        except Exception:
            return {k: 0 for k in self.corr_edits}

    def clear_correction(self):
        for e in self.corr_edits.values():
            e.setText("0.0")
        self.lbl_correction_status.setText("校正: 已清除")
        self.log("🔄 坐标校正已清除")

    def save_correction_config(self):
        config = {k: e.text() for k, e in self.corr_edits.items()}
        config['gun_template'] = self.gun_placer.gun_template_path

        # 优先保存到焊钳同目录，文件名与焊钳一致
        if self.gun_placer.gun_template_path:
            gun_path = self.gun_placer.gun_template_path
            gun_name = os.path.splitext(os.path.basename(gun_path))[0]
            fp = os.path.join(os.path.dirname(gun_path), f"{gun_name}.json")
        else:
            config_dir = os.path.join(os.path.expanduser("~"), ".catia_gun_tool")
            os.makedirs(config_dir, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            fp = os.path.join(config_dir, f"correction_config_{ts}.json")

        try:
            with open(fp, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            QMessageBox.information(self, "成功", f"配置已保存到:\n{fp}")
            self.log(f"✅ 配置已保存: {fp}")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存配置失败:\n{e}")

    def load_correction_config(self):
        fp, _ = QFileDialog.getOpenFileName(
            self, "选择配置文件", "", "JSON 文件 (*.json);;所有文件 (*.*)")
        if not fp:
            return
        self._load_config_from_file(fp)
        # 如果配置含模板路径，同步更新
        try:
            with open(fp, 'r', encoding='utf-8') as f:
                config = json.load(f)
            if 'gun_template' in config and os.path.exists(config['gun_template']):
                self.on_gun_template_selected(config['gun_template'])
        except Exception:
            pass

    def _load_config_from_file(self, fp: str):
        """从指定路径加载配置（内部复用）。"""
        try:
            with open(fp, 'r', encoding='utf-8') as f:
                config = json.load(f)
            for k, e in self.corr_edits.items():
                if k in config:
                    e.setText(str(config[k]))
            self.lbl_correction_status.setText("校正: 已加载配置")
            self.log(f"✅ 配置已自动导入: {os.path.basename(fp)}")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"加载配置失败:\n{str(e)}")

    # ── 筛选逻辑 ──────────────────────────────────────────────────────────
    def on_filter_all_changed(self, checked):
        if checked:
            self.filter_weld.setChecked(False)
            self.filter_via.setChecked(False)
            self.filter_continuous.setChecked(False)
            self.lbl_filter_info.setText("当前: 插入全部点")

    def on_filter_type_changed(self):
        any_checked = (self.filter_weld.isChecked() or
                       self.filter_via.isChecked() or
                       self.filter_continuous.isChecked())
        if any_checked:
            self.filter_all.setChecked(False)
            selected = []
            if self.filter_weld.isChecked():
                selected.append("焊点")
            if self.filter_via.isChecked():
                selected.append("过渡点")
            if self.filter_continuous.isChecked():
                selected.append("连续点")
            self.lbl_filter_info.setText(f"当前: 插入 {'+'.join(selected)}")
        else:
            self.filter_all.setChecked(True)
            self.lbl_filter_info.setText("当前: 插入全部点")

    # ── CATIA 连接 ────────────────────────────────────────────────────────
    def _apply_connection(self, app, conn_type, silent=False):
        """根据共享连接状态刷新本页：重新绑定 gun_placer + 更新状态条。"""
        self.catia_app = app
        if app:
            self.gun_placer.set_catia_app(app, conn_type)
            self._set_status(f"✅ CATIA 已连接 ({conn_type})", "#27ae60")
            if not silent:
                self.log(f"✅ CATIA 连接成功 ({conn_type})")
        else:
            self._set_status("⚠️ CATIA 未连接", "#e74c3c")
            if not silent:
                self.log("ℹ️ 未检测到运行中的 CATIA，请启动后点击连接。")

    def _on_conn_changed(self, app, conn_type):
        self._apply_connection(app, conn_type)

    def try_auto_connect_catia(self):
        """无共享管理器时的自连回退。"""
        self._set_status("🔄 正在检测 CATIA...", "#f39c12")
        QApplication.processEvents()
        try:
            catia_app, conn_type = connect_active_catia()
        except Exception as e:
            self._set_status("❌ CATIA 连接异常", "#c0392b")
            self.log(f"❌ 自动连接过程发生异常: {str(e)}")
            return
        self._apply_connection(catia_app, conn_type or "")

    def connect_catia(self):
        self.log("🔄 正在尝试连接 CATIA...")
        if self.conn is None:
            self.try_auto_connect_catia()
        else:
            try:
                self.conn.reconnect()   # 成功后经 connection_changed 自动刷新本页
            except Exception as e:
                self._set_status("❌ CATIA 连接异常", "#c0392b")
                self.log(f"❌ 连接过程发生异常: {str(e)}")
                QMessageBox.critical(self, "连接异常", str(e))
                return
        if self.gun_placer.is_catia_connected():
            QMessageBox.information(self, "成功", "CATIA 连接成功！")
        else:
            QMessageBox.warning(self, "未检测到 CATIA",
                                "未检测到正在运行的 CATIA 实例。\n请确保 CATIA 已完全启动后重试。")

    # ── 模板选择 ──────────────────────────────────────────────────────────
    def on_gun_template_selected(self, fp: str):
        self.gun_placer.gun_template_path = fp
        gun_name = os.path.splitext(os.path.basename(fp))[0]
        file_name = os.path.basename(fp)

        self.drop_label.setText(f"✅  {file_name}\n\n🔄 可重新拖放或点击更换")
        self.drop_label.setStyleSheet(
            "border: 2px solid #27ae60; border-radius: 6px; background: #eafaf1;"
            "color: #27ae60; font-size: 11px; padding: 6px; font-weight: bold;"
        )
        self.log(f"✅ 已选择模板: {file_name}")

        config_fp = os.path.join(os.path.dirname(fp), f"{gun_name}.json")
        if os.path.exists(config_fp):
            reply = QMessageBox.question(
                self, "检测到配置文件",
                f"在焊钳同目录下发现配置文件:\n{os.path.basename(config_fp)}\n\n是否自动导入坐标校正配置?",
                QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.Yes:
                self._load_config_from_file(config_fp)

    def select_gun_template(self):
        fp, _ = QFileDialog.getOpenFileName(
            self, "选择焊枪模板文件", "",
            "CATIA 文件 (*.CATPart *.CATProduct *.cgr);;所有文件 (*.*)")
        if fp:
            self.on_gun_template_selected(fp)

    # ── 表格操作 ──────────────────────────────────────────────────────────
    def add_row(self):
        self.data_table.add_row()
        self._update_statistics()

    def paste_excel(self):
        self.data_table._paste_from_excel_file()
        self._update_statistics()

    def _update_statistics(self):
        data = self.data_table.get_all_data()
        total = len(data)
        weld_cnt = sum(1 for g in data if g['Type'] == 'PmWeldLocationOperation')
        via_cnt = sum(1 for g in data if g['Type'] == 'PmViaLocationOperation')
        cont_cnt = sum(1 for g in data if g['Type'] == 'PmContinuousFeatureOperation')
        self.lbl_total.setText(f"总数: {total}")
        self.lbl_weld.setText(f"🔥 焊点: {weld_cnt}")
        self.lbl_via.setText(f"🔄 过渡点: {via_cnt}")
        self.lbl_continuous.setText(f"➰ 连续点: {cont_cnt}")

    def progress_callback(self, current, total, message):
        pct = int((current / max(1, total)) * 100)
        self.progress_bar.setValue(pct)
        self.lbl_progress.setText(f"{message} ({current}/{total})")
        self.log(message)
        QApplication.processEvents()

    def log(self, text: str):
        self.log_text.appendPlainText(text)

    def test_single_insert(self):
        if not self.gun_placer.is_catia_connected():
            QMessageBox.warning(self, "警告", "请先连接 CATIA")
            return
        if not self.gun_placer.gun_template_path:
            QMessageBox.warning(self, "警告", "请先选择焊枪模板文件")
            return
        tp = os.path.normpath(self.gun_placer.gun_template_path).replace('/', '\\')
        self.log("=" * 40)
        self.log("🧪 开始测试插入...")
        self.log(f"模板文件: {tp}")
        self.log(f"文件存在: {os.path.exists(tp)}")
        try:
            sz = os.path.getsize(tp) / 1024
            self.log(f"文件大小: {sz:.2f} KB")
        except Exception:
            pass
        self.log("（测试完成，实际插入请点击批量插入）")

    # ── 批量插入 ──────────────────────────────────────────────────────────
    def _set_batch_running(self, running):
        """批量插入运行态：锁住会引发重入/冲突的按钮。"""
        self._batch_running = running
        for b in (self._btn_batch, self._btn_test, self._btn_connect):
            b.setEnabled(not running)
        self._btn_batch.setText("⏳ 正在插入..." if running else "🚀 批量插入")

    def start_batch_placement(self):
        if self._batch_running:
            return  # 已在执行，避免重复触发
        if not self.gun_placer.is_catia_connected():
            QMessageBox.warning(self, "警告", "请先连接 CATIA")
            return
        if not self.gun_placer.has_active_product():
            QMessageBox.warning(self, "警告", "请在 CATIA 中打开一个 Product 文档")
            return
        if not self.gun_placer.gun_template_path:
            QMessageBox.warning(self, "警告", "请先选择焊枪模板文件")
            return

        all_gun_data = self.data_table.get_all_data()
        if not all_gun_data:
            QMessageBox.warning(self, "警告", "没有有效的插枪数据")
            return

        raw_corr_values = self.get_correction_values()

        kuka_tcp_input = None
        simple_offset = None
        apply_correction = any(v != 0 for v in raw_corr_values.values())

        if self.chk_use_tcp.isChecked():
            # TCP 模式下，UI 上的 Rx, Ry, Rz 对应 A, B, C
            kuka_tcp_input = {
                'X': raw_corr_values['X'], 'Y': raw_corr_values['Y'], 'Z': raw_corr_values['Z'],
                'A': raw_corr_values['Rx'], 'B': raw_corr_values['Ry'], 'C': raw_corr_values['Rz'],
            }
        elif apply_correction:
            simple_offset = raw_corr_values

        rotation_order = None
        for btn in self.rotation_group.buttons():
            if btn.isChecked():
                rotation_order = btn.property("value")
                break

        # 筛选逻辑
        if self.filter_all.isChecked():
            gun_data = all_gun_data
            filter_desc = "全部点"
        else:
            selected_types = []
            if self.filter_weld.isChecked():
                selected_types.append("PmWeldLocationOperation")
            if self.filter_via.isChecked():
                selected_types.append("PmViaLocationOperation")
            if self.filter_continuous.isChecked():
                selected_types.append("PmContinuousFeatureOperation")
            if not selected_types:
                QMessageBox.warning(self, "警告", "请至少选择一种点类型")
                return
            gun_data = [g for g in all_gun_data if g['Type'] in selected_types]
            filter_desc = " + ".join(filter(None, [
                "焊点" if self.filter_weld.isChecked() else None,
                "过渡点" if self.filter_via.isChecked() else None,
                "连续点" if self.filter_continuous.isChecked() else None,
            ]))

        if not gun_data:
            QMessageBox.warning(self, "警告", f"筛选后没有数据\n当前筛选: {filter_desc}")
            return

        confirm_msg = (f"即将插入 {len(gun_data)} 个组件（共 {len(all_gun_data)} 个）\n"
                       f"筛选条件: {filter_desc}\n"
                       f"模板: {os.path.basename(self.gun_placer.gun_template_path)}\n")
        if self.chk_use_tcp.isChecked():
            confirm_msg += f"将应用 TCP 矩阵变换: {kuka_tcp_input}\n"
        elif apply_correction:
            confirm_msg += f"将应用普通坐标偏移: {simple_offset}\n"
        confirm_msg += "\n是否继续?"
        if QMessageBox.question(self, "确认", confirm_msg) != QMessageBox.Yes:
            return

        self.progress_bar.setValue(0)
        self.lbl_success.setText("成功: 0")
        self.lbl_failed.setText("失败: 0")
        self.log("=" * 40)
        self.log(f"🚀 开始批量插入 ({filter_desc})...")

        self._set_batch_running(True)
        try:
            success, failed, status_list = self.gun_placer.place_guns_batch(
                self.gun_placer.gun_template_path,
                gun_data,
                self.progress_callback,
                correction_values=simple_offset,
                rotation_order=rotation_order,
                kuka_tcp_input=kuka_tcp_input,
            )

            # 更新状态列（按筛选顺序回写到 all_gun_data 对应行）
            s_idx = 0
            for a_idx, gun in enumerate(all_gun_data):
                if gun in gun_data and s_idx < len(status_list):
                    self.data_table.update_status(a_idx, status_list[s_idx])
                    s_idx += 1

            self.lbl_success.setText(f"成功: {success}")
            self.lbl_failed.setText(f"失败: {failed}")
            self.log(f"✅ 完成 | 成功: {success} | 失败: {failed}")
            QMessageBox.information(self, "完成", f"批量插入完成!\n成功: {success}\n失败: {failed}")
            self._update_statistics()
        except Exception as e:
            self.log(f"❌ 批量插入发生异常: {e}")
            QMessageBox.critical(self, "批量插入失败", str(e))
        finally:
            self._set_batch_running(False)

    def show_help(self):
        help_text = """📖 CATIA 焊枪批量插入工具使用说明

1️⃣ 数据输入方式
  • 双击单元格直接编辑
  • 点击"添加行"添加新数据
  • Ctrl+V 粘贴 Excel 数据
  • 右键选择"从Excel文件导入"

2️⃣ 数据格式
  Name | Type | X | Y | Z | Rx | Ry | Rz

3️⃣ 点类型
  PmWeldLocationOperation     → 焊点
  PmViaLocationOperation      → 过渡点
  PmContinuousFeatureOperation → 连续点

4️⃣ 操作流程
  ① 打开 CATIA，创建/打开 Product 文档
  ② 点击"连接 CATIA"
  ③ 点击"选择模板"，选择焊枪文件
  ④ 输入或粘贴插枪数据
  ⑤ 选择要插入的点类型（筛选）
  ⑥ 点击"批量插入"

5️⃣ 注意事项
  • 模板文件路径不要包含中文字符
  • 插入过程中不要操作 CATIA
  • 欧拉角采用 ZYX 旋转顺序（库卡标准）"""
        ScrollableMessageBox(title="使用说明", intro="CATIA 焊枪批量插入工具使用说明",
                             content=help_text, parent=self).exec_()
