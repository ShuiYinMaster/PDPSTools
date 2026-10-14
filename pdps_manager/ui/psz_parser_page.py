# -*- coding: utf-8 -*-
"""页签 2：PSZ 解析工具 —— 为 psz_parser CLI 提供图形界面。

功能：打开 .psz → 概要信息 / 对象列表（类型+关键词+仅顶层筛选）/ 对象详情 XML
/ 断裂引用检查 / 删除选中对象 / 批量删除断裂对象 / 提取 XML / 重建打包。
解析与修改在后台线程（workers.psz_worker）执行，避免界面卡死。
"""

import os
import zipfile

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QColor
from PyQt5.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QSplitter, QLabel,
    QLineEdit, QCheckBox, QTableWidget, QTableWidgetItem, QTabWidget,
    QHeaderView, QAbstractItemView, QPlainTextEdit, QListWidget, QGroupBox,
    QMessageBox, QFileDialog, QApplication, QDialog, QProgressBar,
)

from ..core.psz_parser import (PS_STATE, EMS_STATE, read_zip_entry, detect_encoding,
                               decode_xml, check_broken_entries, check_xml_validity_entries)
from ..core.obj_kv_table import parse_obj, to_kv_table
from ..workers import PszLoadWorker, PszRepairWorker
from .style import make_btn, status_style
from .widgets import ScrollableMessageBox

_MAX_DETAIL = 200_000          # 详情原始 XML 最大展示字符数，避免超大对象卡界面
_VALUE_WIDTH_RATIO = 0.30      # 对照表“值”列占表格宽度比例（路径列占其余 70%）


def _entry_tag(name):
    """zip 条目名 → 短标签（PS / EMS），用于界面展示。"""
    if name == PS_STATE:
        return "PS"
    if name == EMS_STATE:
        return "EMS"
    return name


class _PropTable(QTableWidget):
    """路径/值列按 70% / 30% 分配宽度（值列 30%），窗口/分隔条缩放时保持比例。"""

    def _apply_ratio(self):
        w = self.viewport().width()
        if w > 40:
            val_w = max(80, int(w * _VALUE_WIDTH_RATIO))
            self.setColumnWidth(1, val_w)
            self.setColumnWidth(0, max(120, w - val_w))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_ratio()

    def showEvent(self, event):
        super().showEvent(event)
        self._apply_ratio()


class PszParserPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.psz_path = None
        self.enc = None
        self.content = None          # 兼容字段：指向第一个条目（PS 优先）的 XML 文本
        self.contents = {}           # entry -> XML 文本（PS / EMS）
        self.encs = {}               # entry -> 编码
        self.objects = {}            # (entry, ext_id) -> ObjInfo
        self.entry_objects = {}      # entry -> {ext_id: ObjInfo}
        self.dup = []
        self.obj_list = []           # 排序后的对象列表
        self.broken_3d = []          # [(ext_id, tag, name, tbm, entry), ...]
        self.dangling = []           # [(uid, pos), ...]
        self.xml_violations = []     # [{"line","column","start","context","entry"}, ...]
        self.entry_stats = {}        # entry -> {"objects","broken","dangling"}
        self.entry_count = 0
        self.fingerprint = None      # loaded PSZ content fingerprint
        self._current_rows = []      # 表格当前行 -> ObjInfo
        self._current_kv_rows = []   # 当前选中对象的 KV 行（未过滤）
        self._worker = None          # 当前后台线程
        self._busy = False
        self._action_buttons = []
        self._setup_ui()

    # ── UI ──────────────────────────────────────────────────────────────
    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 顶部栏：文件选择
        bar = QFrame()
        bar.setStyleSheet("background:#34495e;")
        bar.setFixedHeight(52)
        b = QHBoxLayout(bar)
        b.setContentsMargins(10, 6, 10, 6)
        b.addWidget(QLabel("📦 PSZ 文件:", styleSheet="color:white;"))
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("选择 .psz 文件…（也可从项目路径管理右键打开）")
        self.path_edit.setStyleSheet("color:white;")
        self.path_edit.returnPressed.connect(lambda: self.load_file(self.path_edit.text().strip()))
        b.addWidget(self.path_edit, 1)
        b.addWidget(make_btn("📂 浏览", "#3498db", self._browse))
        self.btn_parse = make_btn("▶ 解析", "#27ae60", self._parse_clicked)
        b.addWidget(self.btn_parse)
        root.addWidget(bar)

        # 概要
        self.summary_lbl = QLabel("未加载")
        self.summary_lbl.setStyleSheet("color:#7f8c8d;font-size:11px;padding:4px 10px;"
                                       "border-bottom:1px solid #ddd;")
        self.summary_lbl.setWordWrap(True)
        root.addWidget(self.summary_lbl)

        # 解析进度（平时隐藏，解析时显示当前阶段，避免“卡死转圈无反应”的错觉）
        self.progress_row = QFrame()
        self.progress_row.setVisible(False)
        prl = QHBoxLayout(self.progress_row)
        prl.setContentsMargins(8, 2, 8, 2)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)          # 忙碌动画（不确定进度）
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedWidth(160)
        self.progress_lbl = QLabel("正在解析…")
        self.progress_lbl.setStyleSheet("color:#16a085;font-size:11px;")
        prl.addWidget(self.progress_bar)
        prl.addWidget(self.progress_lbl, 1)
        root.addWidget(self.progress_row)

        # 主分割区
        splitter = QSplitter(Qt.Horizontal)

        # ── 左：对象列表 ──
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(6, 6, 6, 6)

        # 类型选择：独立可滚动列表（固定高度，类型多时出滚动条）
        type_row = QHBoxLayout()
        type_row.addWidget(QLabel("类型:"))
        self.type_list = QListWidget()
        self.type_list.setFixedHeight(92)
        self.type_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.type_list.currentItemChanged.connect(lambda *_: self._refresh_object_table())
        type_row.addWidget(self.type_list, 1)
        ll.addLayout(type_row)

        # 对象表筛选行：紧贴对象表，明确是“搜索对象”，避免被误解为类型筛选
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("🔍 搜索对象:"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("按名称 / ID 过滤对象列表")
        self.search_edit.setFixedWidth(180)
        self.search_edit.textChanged.connect(self._refresh_object_table)
        filter_row.addWidget(self.search_edit)
        self.top_chk = QCheckBox("仅顶层")
        self.top_chk.toggled.connect(self._refresh_object_table)
        filter_row.addWidget(self.top_chk)
        filter_row.addStretch()
        self.lbl_obj_count = QLabel("共 0 个对象")
        self.lbl_obj_count.setStyleSheet("color:#7f8c8d;font-size:11px;")
        filter_row.addWidget(self.lbl_obj_count)
        ll.addLayout(filter_row)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["类型", "名称", "外部ID", "条目", "顶层"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.cellClicked.connect(self._on_object_selected)
        ll.addWidget(self.table)
        splitter.addWidget(left)

        # ── 右：详情（对照表为主 + 原始XML切换）+ 断裂引用 ──
        right = QWidget()
        right.setStyleSheet("background:#ecf0f1;")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(8, 8, 8, 8)

        rl.addWidget(QLabel("对象详情", styleSheet="font-weight:bold;"))
        self.detail_tabs = QTabWidget()

        # 页1：对照表（主视图）
        kv_page = QWidget()
        kv_l = QVBoxLayout(kv_page)
        kv_l.setContentsMargins(4, 4, 4, 4)
        kv_search_row = QHBoxLayout()
        kv_search_row.addWidget(QLabel("筛选字段:"))
        self.kv_search = QLineEdit()
        self.kv_search.setPlaceholderText("按路径 / 值过滤…")
        self.kv_search.setClearButtonEnabled(True)
        self.kv_search.textChanged.connect(self._populate_kv_table)
        kv_search_row.addWidget(self.kv_search, 1)
        self.lbl_kv_count = QLabel("字段 0")
        self.lbl_kv_count.setStyleSheet("color:#7f8c8d;font-size:11px;")
        kv_search_row.addWidget(self.lbl_kv_count)
        kv_l.addLayout(kv_search_row)
        self.kv_table = _PropTable(0, 2)
        self.kv_table.setHorizontalHeaderLabels(["路径", "值"])
        # 路径/值列均可换行展示长字段；列宽由 _PropTable 按 70% / 30% 分配
        self.kv_table.setWordWrap(True)
        self.kv_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        self.kv_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Interactive)
        self.kv_table.verticalHeader().setVisible(False)
        self.kv_table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)  # 行高随换行自适应
        self.kv_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.kv_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.kv_table.setAlternatingRowColors(True)
        self.kv_table.setFont(QFont("Consolas", 9))
        kv_l.addWidget(self.kv_table)
        self.detail_tabs.addTab(kv_page, "📋 对照表")

        # 页2：原始 XML
        xml_page = QWidget()
        xl = QVBoxLayout(xml_page)
        xl.setContentsMargins(4, 4, 4, 4)
        self.detail_edit = QPlainTextEdit()
        self.detail_edit.setReadOnly(True)
        self.detail_edit.setFont(QFont("Consolas", 9))
        self.detail_edit.setPlaceholderText("点击左侧对象查看其 XML 片段…")
        xl.addWidget(self.detail_edit)
        self.detail_tabs.addTab(xml_page, "📄 原始XML")

        rl.addWidget(self.detail_tabs, 2)

        # ── XML 合规性审计 ──
        xml_grp = QGroupBox("🔬 XML 合规性审计")
        xml_gv = QVBoxLayout(xml_grp)
        self.lbl_xml_valid = QLabel("合规 0 ｜ 违规 0")
        self.lbl_xml_valid.setStyleSheet("color:#27ae60;font-weight:bold;")
        xml_gv.addWidget(self.lbl_xml_valid)
        self.xml_valid_list = QListWidget()
        self.xml_valid_list.setFont(QFont("Consolas", 8))
        xml_gv.addWidget(self.xml_valid_list)
        rl.addWidget(xml_grp, 1)

        grp = QGroupBox("⚠️ 断裂引用")
        gv = QVBoxLayout(grp)
        self.lbl_broken_count = QLabel("断裂 0 ｜ 悬空 0")
        self.lbl_broken_count.setStyleSheet("color:#e74c3c;font-weight:bold;")
        gv.addWidget(self.lbl_broken_count)
        self.broken_list = QListWidget()
        self.broken_list.setFont(QFont("Consolas", 8))
        gv.addWidget(self.broken_list)
        rl.addWidget(grp, 1)
        splitter.addWidget(right)

        splitter.setSizes([680, 520])
        root.addWidget(splitter, 1)

        # 底部动作栏
        act = QFrame()
        act.setStyleSheet("background:#34495e;")
        al = QHBoxLayout(act)
        al.setContentsMargins(8, 6, 8, 6)
        btns = [
            ("🔍 检查断裂", "#e67e22", self._check_broken),
            ("📤 提取 XML", "#9b59b6", self._extract_xml),
            ("🗑️ 删除选中对象", "#e74c3c", self._delete_selected),
            ("🧹 删除断裂对象", "#c0392b", self._delete_broken),
            ("📦 重建打包", "#2980b9", self._rebuild_from_xml),
        ]
        for text, color, cb in btns:
            btn = make_btn(text, color, cb)
            btn.setEnabled(False)
            self._action_buttons.append(btn)
            al.addWidget(btn)
        al.addStretch()
        self.status_lbl = QLabel("就绪 | 请选择 .psz 文件")
        self.status_lbl.setStyleSheet(status_style("#16a085"))
        al.addWidget(self.status_lbl)
        root.addWidget(act)

    # ── 文件加载 ────────────────────────────────────────────────────────
    def _browse(self):
        fp, _ = QFileDialog.getOpenFileName(
            self, "选择 PSZ 文件", "", "PSZ 文件 (*.psz);;所有文件 (*.*)")
        if fp:
            self.load_file(fp)

    def _parse_clicked(self):
        self.load_file(self.path_edit.text().strip())

    def load_file(self, path):
        """公开入口：加载一个 .psz（供项目路径管理集成调用）。"""
        if self._worker is not None and self._worker.isRunning():
            self.status_lbl.setText("当前任务尚未结束，请稍后再解析")
            return
        path = os.path.normpath(path) if path else ""
        if not path or not os.path.exists(path):
            QMessageBox.warning(self, "警告", "文件不存在:\n" + path)
            return
        self.path_edit.setText(path)
        self.status_lbl.setText(f"正在解析: {os.path.basename(path)} …")
        self._set_busy(True)
        w = PszLoadWorker(path, self)
        w.loaded.connect(self._on_load_done)
        w.failed.connect(self._on_load_failed)
        w.progress.connect(self._on_parse_progress)
        w.finished.connect(self._cleanup_worker)
        self._worker = w
        w.start()

    def _on_parse_progress(self, stage):
        self.progress_lbl.setText(stage)
        self.progress_bar.setValue(0)

    def _on_load_done(self, snapshot):
        self._apply_snapshot(snapshot)
        self._set_busy(False)
        self.status_lbl.setText("解析完成")

    def _on_load_failed(self, msg):
        self._set_busy(False)
        self.status_lbl.setText("解析失败")
        QMessageBox.critical(self, "解析失败", str(msg))

    # ── 快照应用 / 刷新 ────────────────────────────────────────────────
    def _apply_snapshot(self, snap):
        self.psz_path = snap["path"]
        self.contents = snap["contents"]     # {entry: XML文本}
        self.encs = snap["encs"]             # {entry: 编码}
        # 兼容字段：指向第一个条目（PS 优先），旧代码路径可直接用
        self.content = self.contents.get(PS_STATE) or next(iter(self.contents.values()))
        self.enc = self.encs.get(PS_STATE) or next(iter(self.encs.values()))
        self.objects = snap["objects"]
        self.entry_objects = snap["entry_objects"]
        self.dup = snap["dup"]
        self.broken_3d = snap["broken_3d"]
        self.dangling = snap["dangling"]
        self.xml_violations = snap.get("xml_violations", [])
        self.obj_list = snap["obj_list"]
        self.entry_stats = snap["entry_stats"]
        self.entry_count = snap["entry_count"]
        self.fingerprint = snap["fingerprint"]
        self._clear_detail()
        self._refresh_summary()
        self._populate_type_list()
        self._refresh_object_table()
        self._refresh_broken()
        for btn in self._action_buttons:
            btn.setEnabled(True)

    def _refresh_summary(self):
        if not self.psz_path:
            return
        fsize = os.path.getsize(self.psz_path)
        size_str = f"{fsize/1024/1024:.2f} MB" if fsize >= 1024 * 1024 else f"{fsize/1024:.1f} KB"
        top_count = sum(1 for o in self.obj_list if o.top_level)
        nv = len(self.xml_violations)
        per_entry = " / ".join(
            f"{_entry_tag(e)} 对象{s['objects']} 断裂{s['broken']}"
            for e, s in self.entry_stats.items())
        self.summary_lbl.setText(
            f"📄 {os.path.basename(self.psz_path)} | {size_str} | 条目 {self.entry_count} | "
            f"对象 {len(self.obj_list)}（顶层 {top_count}）| "
            f"XML 违规 {nv} | 悬空 {len(self.dangling)} | {per_entry}")

    def _populate_type_list(self):
        """填充类型选择列表（独立可滚动），首项“全部类型”。"""
        tags = sorted({o.tag for o in self.obj_list})
        self.type_list.blockSignals(True)
        self.type_list.setUpdatesEnabled(False)
        try:
            self.type_list.clear()
            self.type_list.addItem("全部类型")
            self.type_list.item(self.type_list.count() - 1).setData(Qt.UserRole, "")
            for t in tags:
                self.type_list.addItem(t)
                self.type_list.item(self.type_list.count() - 1).setData(Qt.UserRole, t)
            self.type_list.setCurrentRow(0)
        finally:
            self.type_list.setUpdatesEnabled(True)
            self.type_list.blockSignals(False)

    def _selected_type(self):
        item = self.type_list.currentItem()
        return item.data(Qt.UserRole) or "" if item else ""

    def _refresh_object_table(self):
        kw = self.search_edit.text().strip().lower()
        type_f = self._selected_type()
        only_top = self.top_chk.isChecked()
        rows = []
        for o in self.obj_list:
            if only_top and not o.top_level:
                continue
            if type_f and o.tag != type_f:
                continue
            if kw and not (kw in o.ext_id.lower() or (o.name and kw in o.name.lower())
                           or kw in _entry_tag(o.entry).lower()):
                continue
            rows.append(o)
        self._current_rows = rows
        # 批量填充时关闭重绘，避免上万行时逐行刷新导致界面卡顿
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(rows))
        try:
            for r, o in enumerate(rows):
                vals = (o.tag, o.name or "-", o.ext_id,
                        _entry_tag(o.entry), "✓" if o.top_level else "")
                for c, v in enumerate(vals):
                    self.table.setItem(r, c, QTableWidgetItem(v))
        finally:
            self.table.setUpdatesEnabled(True)
            self.table.update()
        self.lbl_obj_count.setText(f"显示 {len(rows)} / {len(self.obj_list)} 个对象")

    def _on_object_selected(self, row, _col):
        if row < 0 or row >= len(self._current_rows):
            self._clear_detail()
            return
        o = self._current_rows[row]
        seg = self.contents[o.entry][o.start:o.end]
        # 原始 XML 页
        seg_view = seg
        if len(seg_view) > _MAX_DETAIL:
            seg_view = seg_view[:_MAX_DETAIL] + f"\n… (已截断，共 {len(seg_view)} 字符)"
        self.detail_edit.setPlainText(seg_view)
        # 对照表页（解析失败则留空，原始 XML 仍可看）
        try:
            obj = parse_obj(seg)
            self._current_kv_rows = to_kv_table(obj)
        except Exception:
            self._current_kv_rows = []
        self.kv_search.blockSignals(True)
        self.kv_search.clear()
        self.kv_search.blockSignals(False)
        self._populate_kv_table()

    def _clear_detail(self):
        self.detail_edit.clear()
        self._current_kv_rows = []
        self._populate_kv_table()

    def _populate_kv_table(self):
        """把当前对象的 KV 行渲染到对照表，支持按路径/值过滤。"""
        kw = self.kv_search.text().strip().lower()
        rows = [r for r in self._current_kv_rows
                if not kw or kw in r["path"].lower() or kw in str(r["value"]).lower()]
        self.lbl_kv_count.setText(f"字段 {len(rows)} / {len(self._current_kv_rows)}")
        self.kv_table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            mark = {"attr": "⚙", "leaf": "", "list-item": "•", "nested": "↳"}.get(r["group"], "")
            path_item = QTableWidgetItem(f"{mark} {r['path']}")
            path_item.setToolTip(r["path"])
            if r["group"] == "attr":
                path_item.setBackground(QColor("#f0f0f0"))
            self.kv_table.setItem(i, 0, path_item)

            raw = r["value"]
            if not raw:
                val_item = QTableWidgetItem("(空)")
                val_item.setForeground(QColor("#aaaaaa"))
            else:
                val_item = QTableWidgetItem(str(raw))   # 完整值展示，长字段靠换行 + 悬浮提示
                if r["is_id"]:
                    val_item.setForeground(QColor("#2980b9"))
                val_item.setToolTip(str(raw))
            if r["group"] == "attr":
                val_item.setBackground(QColor("#f0f0f0"))
            self.kv_table.setItem(i, 1, val_item)

    # ── 断裂引用 ───────────────────────────────────────────────────────
    def _check_broken(self):
        if not self.contents:
            return
        self.xml_violations, _ = check_xml_validity_entries(self.contents)
        self.broken_3d, self.dangling, self.entry_stats = \
            check_broken_entries(self.contents, self.entry_objects, self.objects)
        self._refresh_broken()
        self._refresh_summary()
        nv = len(self.xml_violations)
        self.status_lbl.setText(
            f"检查完成：XML 违规 {nv}，断裂 {len(self.broken_3d)}，悬空 {len(self.dangling)}")

    def _refresh_broken(self):
        # XML 合规性
        self.xml_valid_list.clear()
        for v in self.xml_violations[:200]:
            self.xml_valid_list.addItem(
                f"[{_entry_tag(v['entry'])}] 行 {v['line']}, 列 {v['column']}: {v['context']}")
        nv = len(self.xml_violations)
        self.lbl_xml_valid.setText(
            f"违规 {nv}" if nv else "合规 ✓")
        self.lbl_xml_valid.setStyleSheet(
            "color:#e74c3c;font-weight:bold;" if nv else "color:#27ae60;font-weight:bold;")

        # 断裂引用
        self.broken_list.clear()
        for eid, tag, name, tbm, entry in self.broken_3d[:200]:
            self.broken_list.addItem(
                f"[断裂·{_entry_tag(entry)}] <{tag}> {name or '-'} id={eid} → {tbm}")
        for uid, _pos in self.dangling[:100]:
            self.broken_list.addItem(f"[悬空] uses={uid}（引用的对象不存在）")
        self.lbl_broken_count.setText(f"断裂 threeDRep: {len(self.broken_3d)} ｜ 悬空 uses: {len(self.dangling)}")

    # ── 编辑修复 ───────────────────────────────────────────────────────
    def _delete_selected(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self._current_rows):
            QMessageBox.warning(self, "提示", "请先在列表中选中一个对象")
            return
        o = self._current_rows[row]
        same_id_elsewhere = any(
            o.ext_id in objects for entry, objects in self.entry_objects.items()
            if entry != o.entry)
        reference_note = (
            "另一个条目仍有相同 ID 的对象，相关引用会保留。"
            if same_id_elsewhere else "没有同 ID 对象留存时会清理相关 Pm*Usage。")
        msg = (f"将删除对象：\n<{o.tag}> 名称={o.name or '-'}  ID={o.ext_id}\n"
               f"所属条目: {_entry_tag(o.entry)}\n\n"
               f"{reference_note}\n将自动备份原文件。\n是否继续？")
        if QMessageBox.question(self, "确认删除", msg) != QMessageBox.Yes:
            return
        self._start_repair("delete", {"targets": [(o.entry, o.ext_id)],
                                      "clean": True, "backup": True})

    def _delete_broken(self):
        if not self.broken_3d:
            QMessageBox.information(self, "提示", "当前没有断裂对象（threeDRep 指向 TuneTbm-）")
            return
        lines = [f"[{_entry_tag(entry)}] <{tag}> {name or '-'}  id={eid}"
                 for eid, tag, name, _, entry in self.broken_3d]
        intro = ("以下对象 threeDRep 指向 TuneTbm-（断裂，zip 内无资源），将被删除。\n"
                 "仅清理不再由任一条目对象使用的引用，并自动备份原文件。")
        dlg = ScrollableMessageBox(
            title="确认批量删除",
            intro=intro,
            content=f"共 {len(lines)} 个断裂对象（{_entry_tag(self.broken_3d[0][4])} 等）：\n\n"
                    + "\n".join(lines),
            yes_text="删除",
            no_text="取消",
            yes_color="#c0392b",
            parent=self,
        )
        if dlg.exec_() != QDialog.Accepted:
            return
        self._start_repair("delete_broken", {"backup": True})

    def _extract_xml(self):
        if not self.psz_path:
            return
        fp, _ = QFileDialog.getSaveFileName(
            self, "提取 XML 保存到", "StandaloneStudy_PsState.xml", "XML 文件 (*.xml)")
        if not fp:
            return
        try:
            with zipfile.ZipFile(self.psz_path, 'r') as z:
                data = read_zip_entry(z, PS_STATE)
            enc = detect_encoding(data)
            text = decode_xml(data)
            with open(fp, "w", encoding="utf-8", newline="") as f:
                f.write(text)
            QMessageBox.information(
                self, "完成",
                f"已提取 {PS_STATE}（原始编码 {enc}，已转 UTF-8 保存）:\n{fp}")
        except Exception as e:
            QMessageBox.critical(self, "提取失败", str(e))

    def _rebuild_from_xml(self):
        if not self.psz_path:
            return
        fp, _ = QFileDialog.getOpenFileName(
            self, "选择修改后的 XML 文件", "", "XML 文件 (*.xml)")
        if not fp:
            return
        if QMessageBox.question(
                self, "确认重建",
                f"将用 {os.path.basename(fp)} 替换包内 {PS_STATE} 并重建：\n{self.psz_path}\n\n"
                "将自动备份原文件。\n是否继续？") != QMessageBox.Yes:
            return
        self._start_repair("rebuild", {"xml_path": fp})

    def _start_repair(self, mode, params):
        if self._worker is not None and self._worker.isRunning():
            self.status_lbl.setText("当前任务尚未结束，请稍后再修改")
            return
        self.status_lbl.setText("正在执行并重新解析…")
        self._set_busy(True)
        w = PszRepairWorker(self.psz_path, self.contents, self.encs, mode, params,
                            self, expected_fingerprint=self.fingerprint)
        w.done.connect(self._on_repair_done)
        w.failed.connect(self._on_repair_failed)
        w.finished.connect(self._cleanup_worker)
        self._worker = w
        w.start()

    def _on_repair_done(self, snapshot):
        self._apply_snapshot(snapshot)
        self._set_busy(False)
        bak = snapshot.get("backup_path")
        extra = f"\n备份文件: {bak}" if bak else ""
        self.status_lbl.setText("操作完成")
        QMessageBox.information(self, "完成", f"操作完成，已重新解析。{extra}")

    def _on_repair_failed(self, msg):
        self._set_busy(False)
        self.status_lbl.setText("操作失败")
        QMessageBox.critical(self, "操作失败", str(msg))

    # ── 线程状态管理 ───────────────────────────────────────────────────
    def _set_busy(self, busy):
        self._busy = busy
        self.btn_parse.setEnabled(not busy)
        for b in self._action_buttons:
            b.setEnabled(not busy and self.psz_path is not None)
        self.progress_row.setVisible(busy)
        if busy:
            self.progress_lbl.setText("正在解析…")
            QApplication.setOverrideCursor(Qt.WaitCursor)
        else:
            QApplication.restoreOverrideCursor()

    def _cleanup_worker(self):
        w = self.sender()
        if w is not None:
            w.deleteLater()
            if w is self._worker:
                self._worker = None
