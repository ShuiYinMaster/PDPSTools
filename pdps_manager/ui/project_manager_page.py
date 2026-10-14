# -*- coding: utf-8 -*-
"""页面 1：项目路径管理。

本次改造重点：
  • 深度扫描改为后台线程 + QProgressDialog 进度条，界面不再假死，可取消；
  • 项目数据库改为 JSON（pdps_master_config.json），内存中仍用 pandas
    DataFrame，仅在落盘/读取时转换；旧 Excel 数据库首次启动自动迁移；
  • 保存逻辑加固：占用检测、原子写入、搜索路径改用 JSON（修复含逗号路径
    被 split(',') 截断的隐患），并兼容读取旧 .ini。
"""

import os
import re
import json
import zipfile
import winreg
import subprocess
from datetime import datetime
from typing import Dict

import pandas as pd
from PIL import Image
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont, QColor, QPixmap, QImage
from PyQt5.QtWidgets import (
    QWidget, QFrame, QSplitter, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QTextEdit, QComboBox, QTreeWidget, QTreeWidgetItem,
    QHeaderView, QAbstractItemView, QMenu, QMessageBox, QFileDialog,
    QSizePolicy, QApplication, QProgressDialog, QPushButton,
)

from ..config import (
    CONFIG_FILE, LEGACY_CONFIG_XLSX, PATHS_CONFIG_FILE, LEGACY_PATHS_INI,
    UI_SETTINGS_FILE,
    REG_PATH, REG_PATH_DEFINE_COMPONENT, REG_PATH_INSERT_COMPONENT,
    REG_PATH_IMPORT_MFGS,
    REG_VALUE_LAST_FOLDER, REG_VALUE_GET_COMPONENT_DIALOG_LAST_FOLDER,
    REG_VALUE_LAST_BROWSED_DIRECTORY,
    RECENT_FILE, RECENT_MAX,
)
from ..core.psz_parser import resolve_system_root_from_psz
from ..workers import ScanWorker
from .style import make_btn


# 数据库列定义：内存中用 pandas DataFrame，落盘为 JSON 记录
_DB_COLUMNS = ["Company", "Plant", "Area", "Project Name",
               "Project Code", "File Name", "Full Path", "System Root",
               "_SourceSize", "_SourceMtimeNs", "_ScanError", "_AutoSystemRoot"]

# 文件列表树列定义：(列索引 → 表头, 默认是否可见)
# 默认只显示常用列，状态/文件路径/文件名 通过“列显示”菜单按需开启
_TREE_COLUMNS = [
    ("org",    "组织架构", True),
    ("code",   "项目代号", True),
    ("status", "状态",     False),
    ("path",   "文件路径", False),
    ("name",   "文件名",   False),
    ("size",   "大小",     True),
    ("mtime",  "修改时间", True),
]


class ProjectManagerPage(QWidget):
    # 未保存更改状态变化（True=有未保存改动），供主窗口在标题栏加 *
    dirty_changed = pyqtSignal(bool)
    # 请求在 PSZ 解析页签打开指定 .psz（由主窗口连接并切换页签）
    open_in_psz = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.search_paths = []
        self.df_data = pd.DataFrame(columns=_DB_COLUMNS)
        self.current_preview_pixmap = None
        self._preview_raw_image = None
        self._scan_worker = None
        self._scan_dialog = None
        self._scan_done = False
        self.dirty = False
        self.recent = []                 # 最近开启的项目（full_path 列表）
        self.setAcceptDrops(True)        # 支持把文件夹拖进来添加搜索路径
        self._setup_ui()
        self._apply_saved_column_prefs()   # 恢复上次的列显示设置
        self.recent = self._load_recent()
        self._refresh_recent_menu()
        self.load_local_config()

    # ── 未保存更改标记 ────────────────────────────────────────────────────
    def _set_dirty(self, value: bool):
        if value != self.dirty:
            self.dirty = value
            self.dirty_changed.emit(value)


    # ── UI ────────────────────────────────────────────────────────────────
    def _setup_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # 顶部工具栏
        toolbar = QFrame()
        toolbar.setStyleSheet("background-color:#34495e;")
        toolbar.setFixedHeight(50)
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(10, 5, 10, 5)

        tb_layout.addWidget(QLabel("🔍 搜索项目:", styleSheet="color:white;"))
        self.search_edit = QLineEdit()
        self.search_edit.setFixedWidth(220)
        self.search_edit.setStyleSheet("color: white")
        self.search_edit.textChanged.connect(self.refresh_tree)
        tb_layout.addWidget(self.search_edit)

        tb_layout.addWidget(QLabel("  🏢 公司筛选:", styleSheet="color:white;"))
        self.combo_company = QComboBox()
        self.combo_company.setFixedWidth(140)
        self.combo_company.addItem("全部公司")
        self.combo_company.setStyleSheet("""
            QComboBox { color: white; }
            QComboBox QAbstractItemView { color: white; }
        """)
        self.combo_company.currentTextChanged.connect(self.refresh_tree)
        tb_layout.addWidget(self.combo_company)

        tb_layout.addStretch()
        tb_layout.addWidget(make_btn("打开Catia", "#3498db", self.open_catia))
        tb_layout.addWidget(make_btn("＋ 添加目录", "#3498db", self.add_search_folder))
        tb_layout.addWidget(make_btn("🔄 深度扫描", "#27ae60", self.scan_files))

        # 最近开启的项目（下拉菜单）
        self.btn_recent = make_btn("🕘 最近", "#16a085", None)
        self._recent_menu = QMenu(self)
        self.btn_recent.setMenu(self._recent_menu)
        tb_layout.addWidget(self.btn_recent)

        # 自定义列表显示：可随时开关 状态/文件路径/文件名 等列
        self.btn_columns = make_btn("☰ 列显示", "#8e44ad", self._show_column_menu_button)
        self.btn_columns.setToolTip("自定义文件列表显示列\n(也可在列表表头上右键打开)")
        tb_layout.addWidget(self.btn_columns)

        tb_layout.addWidget(make_btn("💾 保存数据", "#f1c40f",
                                     lambda: self.save_config()))
        root_layout.addWidget(toolbar)

        # 主体分割
        splitter = QSplitter(Qt.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(4, 4, 4, 4)

        # 树上方的小工具行：展开/折叠
        tree_tools = QHBoxLayout()
        tree_tools.setContentsMargins(0, 0, 0, 0)
        btn_expand = QPushButton("展开全部")
        btn_collapse = QPushButton("折叠全部")
        for b in (btn_expand, btn_collapse):
            b.setFixedHeight(24)
            b.setStyleSheet("font-size:11px; padding:2px 10px;")
        btn_expand.clicked.connect(lambda: self.tree.expandAll())
        btn_collapse.clicked.connect(lambda: self.tree.collapseAll())
        tree_tools.addWidget(btn_expand)
        tree_tools.addWidget(btn_collapse)
        tree_tools.addStretch()
        self.lbl_tree_count = QLabel("共 0 个文件")
        self.lbl_tree_count.setStyleSheet("color:#7f8c8d; font-size:11px;")
        tree_tools.addWidget(self.lbl_tree_count)
        left_layout.addLayout(tree_tools)

        # 没有项目数据时，在列表位置提供可直接操作的目录引导。
        self.empty_projects_panel = QFrame()
        self.empty_projects_panel.setStyleSheet(
            "QFrame { background:#f4f9fd; border-radius:8px; }"
            "QLabel { color:#34495e; background:transparent; }")
        guide_layout = QVBoxLayout(self.empty_projects_panel)
        guide_layout.setContentsMargins(28, 28, 28, 28)
        guide_layout.addStretch()
        title = QLabel("添加项目目录，开始使用")
        title.setStyleSheet("font-size:20px; font-weight:bold; color:#2c3e50;")
        title.setAlignment(Qt.AlignCenter)
        guide_layout.addWidget(title)
        self.empty_projects_hint = QLabel()
        self.empty_projects_hint.setWordWrap(True)
        self.empty_projects_hint.setAlignment(Qt.AlignCenter)
        guide_layout.addWidget(self.empty_projects_hint)
        steps = QLabel(
            "① 选择存放项目 .psz 文件的目录，程序会自动扫描子目录。\n"
            "② 扫描完成后，在列表中选择项目，查看详情或启动项目。\n"
            "③ 点击“保存数据”，下次打开时即可继续使用。")
        steps.setWordWrap(True)
        steps.setAlignment(Qt.AlignCenter)
        steps.setStyleSheet("font-size:13px;")
        guide_layout.addWidget(steps)
        guide_actions = QHBoxLayout()
        guide_actions.addStretch()
        self.guide_add_folder_btn = make_btn(
            "＋ 添加项目目录", "#3498db", self.add_search_folder, height=38)
        guide_actions.addWidget(self.guide_add_folder_btn)
        self.guide_scan_btn = make_btn(
            "重新扫描已添加目录", "#27ae60", self.scan_files, height=38)
        guide_actions.addWidget(self.guide_scan_btn)
        guide_actions.addStretch()
        guide_layout.addLayout(guide_actions)
        drag_hint = QLabel("也可以将项目文件夹拖到窗口中，直接添加并扫描。")
        drag_hint.setWordWrap(True)
        drag_hint.setAlignment(Qt.AlignCenter)
        drag_hint.setStyleSheet("color:#7f8c8d; font-size:12px;")
        guide_layout.addWidget(drag_hint)
        guide_layout.addStretch()
        left_layout.addWidget(self.empty_projects_panel, 1)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(_TREE_COLUMNS))
        self.tree.setHeaderLabels([label for _, label, _ in _TREE_COLUMNS])
        # 默认只显示常用列，状态/文件路径/文件名 通过“列显示”菜单按需开启
        for col, (_, _, visible) in enumerate(_TREE_COLUMNS):
            self.tree.setColumnHidden(col, not visible)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.header().customContextMenuRequested.connect(self._show_header_column_menu)
        self._apply_column_layout()
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.itemSelectionChanged.connect(self.on_item_select)
        self.tree.itemDoubleClicked.connect(lambda item, _: self.launch_pdps())
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_tree_context_menu)

        left_layout.addWidget(self.tree, 1)
        splitter.addWidget(left)
        splitter.setSizes([900, 300])

        # 右侧详情
        right = QWidget()
        right.setStyleSheet("background:#ecf0f1;")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(12, 8, 12, 8)

        right_layout.addWidget(QLabel("项目预览", styleSheet="font-weight:bold;"))
        self.lbl_preview = QLabel()
        self.lbl_preview.setMinimumHeight(160)
        self.lbl_preview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.lbl_preview.setAlignment(Qt.AlignCenter)
        self.lbl_preview.setStyleSheet("background:black;color:white;border-radius:4px;")
        self.lbl_preview.setText("无预览图")
        right_layout.addWidget(self.lbl_preview)

        self.entries: Dict[str, QLineEdit] = {}
        form = QFormLayout()
        for label, key in [("所属公司:", "Company"), ("所在厂房:", "Plant"),
                            ("所属区域:", "Area"), ("项目名称:", "Project Name"),
                            ("项目代号:", "Project Code")]:
            e = QLineEdit()
            self.entries[key] = e
            form.addRow(label, e)
        right_layout.addLayout(form)

        lbl_root = QLabel("System Root (修改将同步同级文件):")
        lbl_root.setStyleSheet("color:#e67e22;font-weight:bold;")
        right_layout.addWidget(lbl_root)
        self.txt_root = QTextEdit()
        self.txt_root.setFixedHeight(80)
        self.txt_root.setFont(QFont("Consolas", 9))
        right_layout.addWidget(self.txt_root)

        right_layout.addWidget(make_btn("🔥 确认修改并同步", "#34495e",
                                        self.update_metadata_and_sync, height=32))
        right_layout.addWidget(make_btn("📂 定位文件位置", "#bdc3c7",
                                        self.open_folder, height=28))
        right_layout.addStretch()
        right_layout.addWidget(make_btn("🚀 启动项目", "#27ae60",
                                        self.launch_pdps, height=42))

        self.status_lbl = QLabel("就绪")
        self.status_lbl.setStyleSheet("color:#7f8c8d;font-size:11px;")
        right_layout.addWidget(self.status_lbl)

        splitter.addWidget(right)
        root_layout.addWidget(splitter)

    # ── 自定义列显示 ─────────────────────────────────────────────────────
    def _apply_column_layout(self):
        """列布局：全部列自动适应内容宽度；由“路径列（可见时）/组织架构列”拉伸填满右侧。"""
        header = self.tree.header()
        for col in range(len(_TREE_COLUMNS)):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        fill_col = 3 if not self.tree.isColumnHidden(3) else 0   # 路径列优先，否则组织架构列
        header.setSectionResizeMode(fill_col, QHeaderView.Stretch)

    def _build_column_menu(self) -> QMenu:
        menu = QMenu(self)
        for col, (_, label, _) in enumerate(_TREE_COLUMNS):
            act = menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(not self.tree.isColumnHidden(col))   # 先设状态，避免触发信号
            act.toggled.connect(lambda checked, c=col: self._toggle_column(c, checked))
        return menu

    def _toggle_column(self, col, checked):
        self.tree.setColumnHidden(col, not checked)
        self._apply_column_layout()
        self._save_ui_settings()

    def _visible_column_keys(self):
        """返回当前可见列的 key 列表。"""
        return [key for col, (key, _, _) in enumerate(_TREE_COLUMNS)
                if not self.tree.isColumnHidden(col)]

    def _save_ui_settings(self):
        """把列显示偏好写入界面设置文件（切换即保存，重启后保留）。"""
        try:
            payload = {"tree_columns": {"visible": self._visible_column_keys()}}
            self._atomic_write_json(UI_SETTINGS_FILE, payload)
        except Exception as e:
            print(f"界面设置写入失败: {e}")

    def _apply_saved_column_prefs(self):
        """启动时恢复上次保存的列显示设置；无设置时保持默认。"""
        saved = []
        if os.path.exists(UI_SETTINGS_FILE):
            try:
                with open(UI_SETTINGS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                saved = data.get("tree_columns", {}).get("visible", []) or []
            except Exception:
                pass
        if saved:
            for col, (key, _, _) in enumerate(_TREE_COLUMNS):
                self.tree.setColumnHidden(col, key not in saved)
        self._apply_column_layout()

    def _show_header_column_menu(self, pos):
        self._build_column_menu().exec_(self.tree.header().mapToGlobal(pos))

    def _show_column_menu_button(self):
        self._build_column_menu().exec_(
            self.btn_columns.mapToGlobal(self.btn_columns.rect().bottomLeft()))

    # ── 右键菜单 ─────────────────────────────────────────────────────────
    def _show_tree_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if item and item.data(0, Qt.UserRole) is not None:
            idx = item.data(0, Qt.UserRole)
            full_path = str(self.df_data.loc[idx, "Full Path"])
            menu = QMenu(self)
            menu.addAction("复制完整路径", self.copy_full_path)
            menu.addAction("打开所在文件夹", self.open_folder)
            menu.addSeparator()
            if full_path.lower().endswith(".psz"):
                menu.addAction("🔍 PSZ解析",
                               lambda: self.open_in_psz.emit(full_path))
                menu.addAction(
                    "🔄 重新解析 System Root",
                    lambda _checked=False, path=full_path:
                    self.reparse_system_root(path),
                )
                menu.addSeparator()
            menu.addAction("查看文件属性", self.show_file_properties)
            menu.exec_(self.tree.mapToGlobal(pos))

    def copy_full_path(self):
        item = self._selected_file_item()
        if item:
            idx = item.data(0, Qt.UserRole)
            path = self.df_data.loc[idx, "Full Path"]
            QApplication.clipboard().setText(path)
            self.status_lbl.setText(f"已复制: {path}")

    def show_file_properties(self):
        item = self._selected_file_item()
        if not item:
            return
        idx = item.data(0, Qt.UserRole)
        row = self.df_data.loc[idx]
        full_path = row["Full Path"]
        if os.path.exists(full_path):
            size = os.path.getsize(full_path)
            mtime = datetime.fromtimestamp(os.path.getmtime(full_path)).strftime('%Y-%m-%d %H:%M:%S')
            info = (f"路径: {full_path}\n大小: {size/1024:.2f} KB\n修改时间: {mtime}\n"
                    f"公司: {row['Company']}\n厂房: {row['Plant']}\n"
                    f"项目: {row['Project Name']}\n代号: {row['Project Code']}")
            QMessageBox.information(self, "文件属性", info)
        else:
            QMessageBox.warning(self, "警告", "文件不存在")

    def reparse_system_root(self, psz_path):
        """重新解析指定 PSZ 的 System Root，并同步同目录项目。"""
        if not os.path.exists(psz_path):
            QMessageBox.warning(self, "解析失败", "文件不存在")
            return

        try:
            system_root = resolve_system_root_from_psz(psz_path)
        except Exception as e:
            QMessageBox.critical(self, "解析失败", f"无法解析 System Root：\n{e}")
            return

        target_path = os.path.normcase(os.path.normpath(psz_path))
        same_file = self.df_data["Full Path"].map(
            lambda path: os.path.normcase(
                os.path.normpath(str(path))) == target_path
        )
        file_indices = self.df_data.index[same_file].tolist()
        if not file_indices:
            QMessageBox.warning(self, "解析失败", "配置中未找到该 PSZ 记录")
            return

        source_path = str(self.df_data.loc[file_indices[0], "Full Path"])
        current_dir = os.path.normcase(
            os.path.normpath(os.path.dirname(source_path)))
        same_dir = self.df_data["Full Path"].map(
            lambda path: os.path.normcase(
                os.path.normpath(os.path.dirname(str(path)))) == current_dir
        )
        target_indices = self.df_data.index[same_dir].tolist()

        old_roots = self.df_data.loc[target_indices, "System Root"].astype(str)
        if old_roots.eq(system_root).all():
            self.status_lbl.setText("System Root 已是最新")
            QMessageBox.information(self, "解析完成", f"System Root：\n{system_root}")
            return

        self.df_data.loc[target_indices, "System Root"] = system_root
        self.refresh_tree()
        self._set_dirty(True)
        if self.save_config(show_message=False):
            QMessageBox.information(
                self,
                "解析完成",
                f"System Root 已更新并保存：\n{system_root}\n"
                f"同步文件数：{len(target_indices)}",
            )

    def _selected_file_item(self):
        items = self.tree.selectedItems()
        if items and items[0].data(0, Qt.UserRole) is not None:
            return items[0]
        return None

    # ── 核心逻辑：信息解析 ───────────────────────────────────────────────
    def auto_extract_info(self, psz_path):
        """从 .psz 路径推断公司/厂房/项目等信息（纯文件IO+正则，线程安全）。"""
        path_parts = os.path.normpath(psz_path).split(os.sep)
        psz_dir = os.path.dirname(psz_path)
        info = {"Company": "", "Plant": "", "Area": "默认区域",
                "Project Name": "", "Project Code": "", "System Root": ""}
        # ── System Root 解析 ────────────────────────────────────────────
        # PSZ 中的资源引用带有 #\\ 根标记，从引用在磁盘上的实际位置反推
        # System Root；无法解析时由 helper 保留兼容旧数据的安全回退。
        try:
            info["System Root"] = resolve_system_root_from_psz(psz_path)
        except Exception:
            # 兜底：异常时也给出最优猜测
            info["System Root"] = os.path.normpath(psz_dir)

        proj_idx = next((i for i, p in enumerate(path_parts) if "项目" in p), -1)
        comp_idx = next((i for i, p in enumerate(path_parts) if "公司" in p), -1)

        if proj_idx != -1:
            part = path_parts[proj_idx]
            bracket = re.search(r'[（(]([^）)]+)[）)]', part)
            if bracket:
                info["Project Code"] = bracket.group(1).strip()
                info["Project Name"] = part[:bracket.start()].strip()
            else:
                info["Project Name"] = part
            if proj_idx > 0:
                info["Plant"] = path_parts[proj_idx - 1]
            info["Company"] = path_parts[comp_idx] if comp_idx != -1 else (
                path_parts[proj_idx - 2] if proj_idx > 1 else "")
        elif comp_idx != -1:
            info["Company"] = path_parts[comp_idx]
            info["Project Name"] = "未识别项目"

        if not info["Company"]:
            info["Company"] = "通用客户"
        if not info["Plant"]:
            info["Plant"] = "未知厂房"
        if not info["Project Name"]:
            info["Project Name"] = os.path.basename(psz_path)
        return info

    # ── 树刷新 ───────────────────────────────────────────────────────────
    def refresh_tree(self):
        self.tree.clear()
        df = self.df_data.copy()
        company_filter = self.combo_company.currentText()
        if company_filter != "全部公司":
            df = df[df["Company"] == company_filter]
        kw = self.search_edit.text().strip().lower()
        if kw:
            df = df[df.apply(lambda r: kw in str(r.values).lower(), axis=1)]

        companies = ["全部公司"] + sorted(self.df_data["Company"].unique().tolist())
        current = self.combo_company.currentText()
        self.combo_company.blockSignals(True)
        self.combo_company.clear()
        self.combo_company.addItems(companies)
        idx = self.combo_company.findText(current)
        self.combo_company.setCurrentIndex(max(0, idx))
        self.combo_company.blockSignals(False)

        for c, c_grp in df.groupby("Company"):
            company_node = QTreeWidgetItem(self.tree, [f"🏢 {c} ({len(c_grp)})"])
            company_node.setForeground(0, QColor("#2c3e50"))
            for p, p_grp in c_grp.groupby("Plant"):
                plant_node = QTreeWidgetItem(company_node, [f"🏭 {p} ({len(p_grp)})"])
                for pr, pr_grp in p_grp.groupby("Project Name"):
                    proj_node = QTreeWidgetItem(plant_node, [f"📂 {pr} ({len(pr_grp)})", pr_grp.iloc[0]["Project Code"]])
                    for a, a_grp in pr_grp.groupby("Area"):
                        area_node = QTreeWidgetItem(proj_node, [f"📁 {a}"])
                        for idx2, row in a_grp.iterrows():
                            fp = row["Full Path"]
                            size_str, mtime_str, status = "N/A", "N/A", "Missing"
                            if os.path.exists(fp):
                                sz = os.path.getsize(fp)
                                size_str = f"{sz/1024:.1f} KB" if sz < 1024*1024 else f"{sz/1024/1024:.1f} MB"
                                mtime_str = datetime.fromtimestamp(os.path.getmtime(fp)).strftime('%Y-%m-%d %H:%M')
                                status = "Ready"
                            file_node = QTreeWidgetItem(area_node, [
                                f"📄 {row['File Name']}", "", status, fp,
                                row['File Name'], size_str, mtime_str])
                            file_node.setData(0, Qt.UserRole, idx2)
                            file_node.setForeground(
                                2, QColor("#e74c3c") if status == "Missing" else QColor("#27ae60"))

        if kw:
            self.tree.expandAll()

        # 更新总数标签（显示当前筛选后的可见数 / 总数）
        total = len(self.df_data)
        shown = len(df)
        if shown == total:
            self.lbl_tree_count.setText(f"共 {total} 个文件")
        else:
            self.lbl_tree_count.setText(f"显示 {shown} / 共 {total} 个文件")
        self._update_empty_projects_guide()

    def _update_empty_projects_guide(self):
        empty = self.df_data.empty
        self.empty_projects_panel.setVisible(empty)
        self.tree.setVisible(not empty)
        scanning = self._scan_worker is not None and self._scan_worker.isRunning()
        self.guide_add_folder_btn.setEnabled(not scanning)
        self.guide_scan_btn.setEnabled(not scanning)
        self.guide_scan_btn.setVisible(bool(self.search_paths))
        if scanning:
            hint = "正在扫描项目目录，请稍候…"
        elif not self.search_paths:
            hint = "还没有项目数据。请先添加一个项目目录。"
        elif self._scan_done:
            hint = "还没有可显示的项目文件。请确认目录中包含 .psz 文件，或添加其他目录。"
        else:
            hint = f"已添加 {len(self.search_paths)} 个目录。点击扫描，加载项目文件。"
        self.empty_projects_hint.setText(hint)
        self.guide_scan_btn.setToolTip("\n".join(self.search_paths))

    def on_item_select(self):
        item = self._selected_file_item()
        if not item:
            return
        idx = item.data(0, Qt.UserRole)
        row = self.df_data.loc[idx]
        for k, e in self.entries.items():
            e.setText(str(row.get(k, "")))
        self.txt_root.setPlainText(str(row.get("System Root", "")))
        self.load_preview(row["Full Path"])

    def update_metadata_and_sync(self):
        item = self._selected_file_item()
        if not item:
            return
        idx = item.data(0, Qt.UserRole)
        current_path = str(self.df_data.loc[idx, "Full Path"])
        curr_dir = os.path.normcase(os.path.normpath(os.path.dirname(current_path)))
        new_values = {key: entry.text() for key, entry in self.entries.items()}
        new_values["System Root"] = self.txt_root.toPlainText().strip()
        columns = list(new_values)

        mask = self.df_data["Full Path"].map(
            lambda path: os.path.normcase(
                os.path.normpath(os.path.dirname(str(path)))) == curr_dir
        )
        sync_indices = self.df_data.index[mask & (self.df_data.index != idx)].tolist()
        target_indices = [idx] + sync_indices
        replacement = pd.DataFrame(
            [new_values] * len(target_indices),
            index=target_indices,
            columns=columns,
        )
        before = self.df_data.loc[target_indices, columns].astype(str)
        self.df_data.loc[target_indices, columns] = replacement
        if before.equals(self.df_data.loc[target_indices, columns].astype(str)):
            self.status_lbl.setText("未检测到修改")
            return

        self.refresh_tree()
        self._set_dirty(True)
        if self.save_config(show_message=False):
            synced = len(sync_indices)
            detail = f"及同级 {synced} 个文件" if synced else ""
            QMessageBox.information(self, "修改成功", f"修改已保存，已更新当前文件{detail}")

    def open_folder(self):
        item = self._selected_file_item()
        if not item:
            return
        idx = item.data(0, Qt.UserRole)
        fp = os.path.normpath(self.df_data.loc[idx, "Full Path"])
        subprocess.run(['explorer', '/select,', fp])

    def open_catia(self):
        def get_catia_path():
            reg_paths = [(r"SOFTWARE\Dassault Systemes\B32\0", "DEST_FOLDER_OSDS"),
                         (r"SOFTWARE\Dassault Systemes\B28\0", "DEST_FOLDER_OSDS")]
            for root_key in [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]:
                for rp, val in reg_paths:
                    try:
                        with winreg.OpenKey(root_key, rp) as key:
                            install_dir, _ = winreg.QueryValueEx(key, val)
                            for exe in [r"code\bin\CNEXT.exe", r"code\bin\CATSTART.exe"]:
                                p = os.path.join(install_dir, exe)
                                if os.path.exists(p):
                                    return p
                    except Exception:
                        pass
            return None

        catia_exe = get_catia_path()
        if not catia_exe:
            catia_exe, _ = QFileDialog.getOpenFileName(
                self, "选择 CATIA 可执行文件",
                r"C:\Program Files\Dassault Systemes",
                "Executable (*.exe)")
            if not catia_exe:
                return
        try:
            subprocess.Popen([catia_exe], cwd=os.path.dirname(catia_exe))
            QMessageBox.information(self, "成功", "CATIA 正在启动...")
        except Exception as e:
            QMessageBox.critical(self, "启动失败", f"无法启动 CATIA:\n{str(e)}")

    def launch_pdps(self):
        item = self._selected_file_item()
        if not item:
            return
        idx = item.data(0, Qt.UserRole)
        row = self.df_data.loc[idx]
        self._launch_path(row["Full Path"], str(row.get("System Root", "")))

    @staticmethod
    def _sync_system_root_to_registry(system_root: str) -> None:
        """将 System Root 同步写入三个注册表位置，保证对话框默认路径一致。"""
        targets = [
            (REG_PATH, "System Root Path"),
            (REG_PATH_DEFINE_COMPONENT, REG_VALUE_LAST_FOLDER),
            (REG_PATH_INSERT_COMPONENT, REG_VALUE_GET_COMPONENT_DIALOG_LAST_FOLDER),
            (REG_PATH_IMPORT_MFGS, REG_VALUE_LAST_BROWSED_DIRECTORY),
        ]
        for subkey, value_name in targets:
            try:
                key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, subkey)
                winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, system_root)
                winreg.CloseKey(key)
            except Exception as e:
                print(f"[Registry] 写入失败 {subkey}\\{value_name}: {e}")

    def _launch_path(self, full_path, system_root=""):
        """按完整路径启动一个 .psz（同步注册表 + os.startfile），并记入最近。"""
        if str(system_root).strip():
            self._sync_system_root_to_registry(system_root)
        if os.path.exists(full_path):
            os.startfile(full_path)
            self._record_recent(full_path)
        else:
            QMessageBox.critical(self, "错误", "文件不存在")

    # ── 最近开启的项目 ────────────────────────────────────────────────────
    def _record_recent(self, full_path):
        full_path = os.path.normpath(full_path)
        # 去重后置顶，截断到上限
        self.recent = [p for p in self.recent if os.path.normpath(p) != full_path]
        self.recent.insert(0, full_path)
        self.recent = self.recent[:RECENT_MAX]
        self._save_recent()
        self._refresh_recent_menu()

    def _refresh_recent_menu(self):
        self._recent_menu.clear()
        if not self.recent:
            act = self._recent_menu.addAction("（暂无记录）")
            act.setEnabled(False)
            return
        for p in self.recent:
            exists = os.path.exists(p)
            label = os.path.basename(p) + ("" if exists else "  (已失效)")
            act = self._recent_menu.addAction(label)
            act.setToolTip(p)
            act.setEnabled(exists)
            act.triggered.connect(lambda _checked, path=p: self._launch_recent(path))
        self._recent_menu.addSeparator()
        self._recent_menu.addAction("🧹 清空最近记录", self._clear_recent)

    def _launch_recent(self, path):
        # 尽量带上 System Root：若该路径仍在当前数据里，取其 System Root
        system_root = ""
        try:
            hit = self.df_data[self.df_data["Full Path"].apply(
                lambda x: os.path.normpath(str(x)) == os.path.normpath(path))]
            if not hit.empty:
                system_root = str(hit.iloc[0].get("System Root", ""))
        except Exception:
            pass
        self._launch_path(path, system_root)

    def _clear_recent(self):
        self.recent = []
        self._save_recent()
        self._refresh_recent_menu()

    @staticmethod
    def _load_recent():
        if os.path.exists(RECENT_FILE):
            try:
                with open(RECENT_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return [p for p in data.get("recent", []) if p][:RECENT_MAX]
            except Exception:
                pass
        return []

    def _save_recent(self):
        try:
            with open(RECENT_FILE, "w", encoding="utf-8") as f:
                json.dump({"recent": self.recent}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"最近项目写入失败: {e}")

    # ── 深度扫描（后台线程 + 进度条）────────────────────────────────────
    # 说明：进度对话框必须是【非模态】。Qt 的 QProgressDialog.setValue() 在对话框
    # 为模态时会内部调用 processEvents()，会让排队中的 finished_ok 槽在 setValue
    # 执行途中被重入触发、提前关闭并销毁对话框，返回后再访问已释放的 C++ 对象 →
    # 程序闪退。改为非模态 + 重入防护后即可避免。扫描期间用禁用按钮来阻止重复触发。
    def scan_files(self):
        if not self.search_paths:
            QMessageBox.information(self, "提示", "请先通过“添加目录”指定搜索路径")
            return
        if self._scan_worker and self._scan_worker.isRunning():
            return  # 已有扫描在跑，避免重复触发

        existing = {row["Full Path"]: row.to_dict() for _, row in self.df_data.iterrows()}

        self._scan_done = False  # 重入/重复完成防护

        dlg = QProgressDialog("正在准备扫描...", "取消", 0, 0, self)
        dlg.setWindowTitle("深度扫描")
        dlg.setWindowModality(Qt.NonModal)   # 关键：非模态，避免 setValue 重入事件循环
        dlg.setMinimumWidth(440)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.setValue(0)
        self._scan_dialog = dlg

        worker = ScanWorker(self.search_paths, existing, self.auto_extract_info, self)
        worker.phase.connect(self._on_scan_phase)
        worker.range_known.connect(self._on_scan_range_known)
        worker.progress.connect(self._on_scan_progress)
        worker.finished_ok.connect(self._on_scan_finished)
        worker.failed.connect(self._on_scan_failed)
        worker.finished.connect(self._cleanup_worker)  # 线程结束后安全回收
        dlg.canceled.connect(worker.cancel)
        self._scan_worker = worker

        worker.start()
        self._update_empty_projects_guide()
        dlg.show()

    def _cleanup_worker(self):
        """线程真正结束后再回收，杜绝“QThread destroyed while running”。"""
        w = self.sender()
        if w is not None:
            w.deleteLater()
            if w is self._scan_worker:
                self._scan_worker = None
                self._update_empty_projects_guide()

    def _safe_dialog(self):
        """返回仍然有效的进度对话框；若已被关闭/销毁则返回 None。"""
        dlg = self._scan_dialog
        if dlg is None:
            return None
        try:
            dlg.isVisible()  # 触发对已删除 C++ 对象的访问检测
        except RuntimeError:
            self._scan_dialog = None
            return None
        return dlg

    def _close_dialog(self):
        dlg = self._safe_dialog()
        self._scan_dialog = None
        if dlg is not None:
            try:
                dlg.close()
                dlg.deleteLater()
            except RuntimeError:
                pass

    def _on_scan_phase(self, text):
        dlg = self._safe_dialog()
        if dlg is not None:
            dlg.setLabelText(text)

    def _on_scan_range_known(self, total):
        dlg = self._safe_dialog()
        if dlg is None:
            return
        if total <= 0:
            dlg.setLabelText("未找到 .psz 文件")
            dlg.setRange(0, 1)
            dlg.setValue(1)
        else:
            dlg.setRange(0, total)
            dlg.setValue(0)
            dlg.setLabelText(f"共发现 {total} 个文件，正在解析...")

    def _on_scan_progress(self, done, name):
        if self._scan_done:
            return
        dlg = self._safe_dialog()
        if dlg is None:
            return
        total = dlg.maximum()
        dlg.setLabelText(f"正在解析 ({done}/{total})\n{name}")
        dlg.setValue(done)

    def _on_scan_finished(self, records, reused=0, parsed=0):
        if self._scan_done:
            return  # 防止重复处理
        self._scan_done = True
        self._close_dialog()
        old_paths = set(self.df_data["Full Path"].dropna())
        if records:
            self.df_data = (pd.DataFrame(records)
                            .drop_duplicates(subset=["Full Path"])
                            .reset_index(drop=True))
        else:
            # 保留列结构，避免后续 refresh_tree / groupby 因缺列报错
            self.df_data = self.df_data.iloc[0:0]
        self.refresh_tree()
        self.status_lbl.setText(
            f"扫描完成，共 {len(self.df_data)} 个文件（复用 {reused} / 新解析 {parsed}）")
        failures = [row for row in records if row.get("_ScanError")]
        if failures:
            self.status_lbl.setText(
                f"扫描完成，{len(failures)} 个文件解析失败；首个错误："
                f"{failures[0]['_ScanError']}")
        # 扫描结果有变化（有新解析或数量变化）时标记为未保存
        new_paths = {row["Full Path"] for row in records}
        if parsed or new_paths != old_paths:
            self._set_dirty(True)

    def _on_scan_failed(self, msg):
        if self._scan_done:
            return
        self._scan_done = True
        self._close_dialog()
        QMessageBox.critical(self, "扫描失败", msg)

    # ── 预览图 ───────────────────────────────────────────────────────────
    def load_preview(self, path):
        self._preview_raw_image = None
        try:
            with zipfile.ZipFile(path, 'r') as z:
                if "Preview Images/previmg.jpg" in z.namelist():
                    with z.open("Preview Images/previmg.jpg") as f:
                        self._preview_raw_image = Image.open(f).copy()
        except Exception as e:
            print(f"预览图加载失败: {e}")
        self._update_preview_display()

    def _update_preview_display(self):
        """根据当前 lbl_preview 的实际宽度缩放并显示预览图。"""
        if self._preview_raw_image is None:
            self.lbl_preview.setPixmap(QPixmap())
            self.lbl_preview.setText("无预览图")
            return
        w = max(self.lbl_preview.width(), 100)
        h = max(int(w * 0.65), 120)          # 保持约 3:2 比例
        self.lbl_preview.setFixedHeight(h)
        img = self._preview_raw_image.copy()
        img.thumbnail((w - 4, h - 4), Image.Resampling.LANCZOS)
        data = img.convert("RGBA").tobytes("raw", "RGBA")
        qimg = QImage(data, img.width, img.height, QImage.Format_RGBA8888)
        self.lbl_preview.setPixmap(QPixmap.fromImage(qimg))
        self.lbl_preview.setText("")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_preview_display()

    # ── 拖放文件夹添加搜索目录 ────────────────────────────────────────────
    def dragEnterEvent(self, event):
        md = event.mimeData()
        if md.hasUrls() and any(os.path.isdir(u.toLocalFile()) for u in md.urls()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        added = []
        for u in event.mimeData().urls():
            d = u.toLocalFile()
            if os.path.isdir(d) and d not in self.search_paths:
                self.search_paths.append(d)
                added.append(d)
        if added:
            self._set_dirty(True)
            self.status_lbl.setText(f"已添加 {len(added)} 个搜索目录，开始扫描...")
            self.scan_files()

    def add_search_folder(self):
        d = QFileDialog.getExistingDirectory(self, "选择搜索目录")
        if d:
            if d not in self.search_paths:
                self.search_paths.append(d)
                self._set_dirty(True)
            self.scan_files()

    # ── 保存 / 加载（加固版）────────────────────────────────────────────
    @staticmethod
    def _atomic_write_json(path, payload):
        """先写临时文件再原子替换，避免写一半损坏数据库。"""
        tmp = f"{path}.{os.getpid()}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def _save_config_json(self):
        """把当前项目数据写为 JSON 数据库。"""
        self._atomic_write_json(
            CONFIG_FILE,
            {"projects": self.df_data.to_dict(orient="records")},
        )

    def save_config(self, *, show_message=True):
        # 一次写入项目数据和搜索路径
        try:
            self._save_config_json()
            self._atomic_write_json(
                PATHS_CONFIG_FILE,
                {"search_paths": self.search_paths},
            )
        except PermissionError:
            QMessageBox.critical(
                self, "保存失败",
                "配置文件被占用，请关闭后重试")
            return False
        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"写入数据库失败：\n{e}")
            return False

        self.status_lbl.setText("本地数据库已更新")
        self._set_dirty(False)
        if show_message:
            QMessageBox.information(self, "保存", "本地数据库已更新")
        return True

    def load_local_config(self):
        # 1) 优先读 JSON 数据库；无 JSON 但存在旧 Excel 时自动迁移为 JSON
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                records = data.get("projects", []) if isinstance(data, dict) else data
                self.df_data = pd.DataFrame(records, columns=_DB_COLUMNS).fillna("")
                self.refresh_tree()
            except Exception as e:
                print(f"读取配置数据库失败: {e}")
        elif os.path.exists(LEGACY_CONFIG_XLSX):
            try:
                self.df_data = pd.read_excel(LEGACY_CONFIG_XLSX).fillna("")
                self._save_config_json()   # 迁移：按新格式落一份 JSON
                print(f"已把旧 Excel 数据库迁移为 JSON: {CONFIG_FILE}")
                self.refresh_tree()
            except Exception as e:
                print(f"迁移旧 Excel 数据库失败: {e}")
        self.search_paths = self._load_search_paths()
        self.refresh_tree()

    @staticmethod
    def _load_search_paths():
        # 新格式：JSON
        if os.path.exists(PATHS_CONFIG_FILE):
            try:
                with open(PATHS_CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return [p for p in data.get("search_paths", []) if p]
            except Exception:
                pass
        # 旧格式：逗号分隔 .ini（向后兼容）
        if os.path.exists(LEGACY_PATHS_INI):
            try:
                with open(LEGACY_PATHS_INI, "r", encoding="utf-8") as f:
                    return [p for p in f.read().split(",") if p.strip()]
            except Exception:
                pass
        return []
