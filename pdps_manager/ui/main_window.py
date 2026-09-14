# -*- coding: utf-8 -*-
"""主窗口：组织四个功能页签。"""

import os

from PyQt5.QtWidgets import (
    QMainWindow, QTabWidget, QWidget, QVBoxLayout, QLabel, QPushButton
)
from PyQt5.QtGui import QIcon
from PyQt5.QtCore import Qt

from .. import config
from ..catia_utils import CatiaConnectionManager
from .project_manager_page import ProjectManagerPage
from .psz_parser_page import PszParserPage
from .toolbox_page import ToolBoxPage
from .gun_placement_page import CatiaGunPlacementPage

# Excle 工具来自独立的 ExcelTools 包，缺失时降级为占位页签
try:
    config.ensure_base_on_path()
    from ExcelTools.excel_shape_tool import ExcelShapeWidget
    _EXCEL_TOOL_AVAILABLE = True
except Exception as _e:  # 导入失败不应阻断主程序
    _EXCEL_TOOL_AVAILABLE = False
    _EXCEL_IMPORT_ERROR = str(_e)


_TAB_STYLE = """
QTabWidget::pane {
    border: 1px solid #d0d0d0;
    background: #ffffff;
}
QTabBar::tab {
    font-family: "微软雅黑";
    font-size: 14px;
    padding: 10px 22px;
    margin-right: 2px;
    background: #ecf0f1;
    color: #2c3e50;
    border: 1px solid #d0d0d0;
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
}
QTabBar::tab:selected {
    background: #3498db;
    color: #ffffff;
}
QTabBar::tab:hover:!selected {
    background: #d6eaf8;
}
"""


class MainApp(QMainWindow):
    """应用主窗口，承载所有功能模块。"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Tecnomatix PDPS工具箱")
        self.resize(1300, 800)

        # 仅在图标文件存在时设置，避免找不到文件报错
        if os.path.exists(config.LOGO_FILE):
            self.setWindowIcon(QIcon(config.LOGO_FILE))

        # 全局共享的 CATIA 连接管理器（两个 CATIA 页签共用一份连接）
        self.conn = CatiaConnectionManager(self)

        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(_TAB_STYLE)
        self.setCentralWidget(self.tabs)

        self.project_page = ProjectManagerPage()
        self.tabs.addTab(self.project_page, "📂 项目路径管理")
        self.psz_page = PszParserPage()
        self.tabs.addTab(self.psz_page, "📦 PSZ解析")
        self.tabs.addTab(ToolBoxPage(conn_manager=self.conn), "🛠️ Catia工具箱")
        self.tabs.addTab(CatiaGunPlacementPage(conn_manager=self.conn), "🔧 插枪工具")
        self.tabs.addTab(self._build_excel_tab(), "Excle工具")

        # 项目路径管理 → PSZ 解析：右键打开选中的 .psz
        self.project_page.open_in_psz.connect(self._open_psz_tool)

        # ── 状态栏：全局 CATIA 指示灯 + 重连按钮 ────────────────────────────
        self.statusBar().showMessage("就绪 | 请选择功能模块")
        self.lbl_catia = QLabel("CATIA: 未连接")
        self.lbl_catia.setStyleSheet("color:#e74c3c; padding:0 8px;")
        btn_reconnect = QPushButton("🔌 重连 CATIA")
        btn_reconnect.clicked.connect(self._reconnect_catia)
        self.statusBar().addPermanentWidget(self.lbl_catia)
        self.statusBar().addPermanentWidget(btn_reconnect)
        self.conn.connection_changed.connect(self._on_conn_changed)

        # 未保存更改：项目页标脏时在标题栏加 *
        self.project_page.dirty_changed.connect(self._on_dirty_changed)

        # 启动时静默连接一次（CATIA 没开/缺环境都不应阻断启动）
        try:
            self.conn.reconnect()
        except Exception:
            pass

    def _reconnect_catia(self):
        try:
            self.conn.reconnect()
        except Exception as e:
            self.statusBar().showMessage(f"CATIA 连接异常: {e}", 5000)

    def _on_conn_changed(self, app, conn_type):
        if app:
            self.lbl_catia.setText(f"CATIA: 已连接 ({conn_type})")
            self.lbl_catia.setStyleSheet("color:#27ae60; padding:0 8px;")
        else:
            self.lbl_catia.setText("CATIA: 未连接")
            self.lbl_catia.setStyleSheet("color:#e74c3c; padding:0 8px;")

    def _on_dirty_changed(self, dirty):
        base = "Tecnomatix PDPS工具箱"
        self.setWindowTitle(f"*{base}" if dirty else base)

    def _open_psz_tool(self, psz_path):
        """从项目路径管理跳转到 PSZ 解析页签并加载该文件。"""
        self.tabs.setCurrentWidget(self.psz_page)
        self.psz_page.load_file(psz_path)

    def closeEvent(self, event):
        """关闭前若项目页有未保存改动则提示。"""
        if getattr(self.project_page, "dirty", False):
            from PyQt5.QtWidgets import QMessageBox
            ret = QMessageBox.question(
                self, "未保存的更改",
                "项目数据有未保存的更改，是否保存？",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Save)
            if ret == QMessageBox.Cancel:
                event.ignore()
                return
            if ret == QMessageBox.Save and not self.project_page.save_config():
                event.ignore()
                return
        event.accept()

    def _build_excel_tab(self) -> QWidget:
        """构建 Excle 工具页签；依赖缺失时给出友好占位。"""
        if _EXCEL_TOOL_AVAILABLE:
            return ExcelShapeWidget()

        placeholder = QWidget()
        layout = QVBoxLayout(placeholder)
        layout.setAlignment(Qt.AlignCenter)
        tip = QLabel(
            "Excle 工具未能加载。\n\n"
            "请确认 ExcelTools 包与主程序位于同一目录。\n"
            f"错误信息：{_EXCEL_IMPORT_ERROR}"
        )
        tip.setAlignment(Qt.AlignCenter)
        tip.setStyleSheet('font-family:"微软雅黑"; font-size:14px; color:#7f8c8d;')
        layout.addWidget(tip)
        return placeholder
