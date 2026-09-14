# -*- coding: utf-8 -*-
"""程序入口：负责高 DPI 设置、全局样式、崩溃日志与启动主窗口。"""

import sys
import ctypes
import traceback
from datetime import datetime

from PyQt5.QtWidgets import QApplication, QMessageBox
from PyQt5.QtGui import QFont
from PyQt5.QtCore import Qt

from . import config


def _write_crash_log(exc_type, exc_value, exc_tb) -> str:
    """把未捕获异常写入 exe 同级的 crash.log，返回写入的文本。"""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    detail = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    text = f"\n{'='*60}\n[{stamp}] 未捕获异常\n{detail}"
    try:
        with open(config.CRASH_LOG, "a", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        pass  # 连日志都写不了时不再二次抛错
    return detail


def _install_excepthook() -> None:
    """安装全局异常钩子：记录日志并尽量弹窗提示，避免“静默闪退”。"""
    def hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        detail = _write_crash_log(exc_type, exc_value, exc_tb)
        # 控制台也打一份，方便从命令行启动时直接看到
        sys.stderr.write(detail)
        try:
            QMessageBox.critical(
                None, "程序发生错误",
                "发生了未预期的错误，已记录到日志：\n"
                f"{config.CRASH_LOG}\n\n"
                f"{exc_type.__name__}: {exc_value}")
        except Exception:
            pass  # Qt 已不可用时忽略
    sys.excepthook = hook


def _enable_dpi_awareness() -> None:
    """在创建 QApplication 之前开启进程级 DPI 感知（仅 Windows 生效）。"""
    try:
        # Windows 8.1+ ：按显示器 DPI 感知
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            # 旧系统回退：系统级 DPI 感知
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass  # 非 Windows 或调用失败时忽略


def main() -> int:
    """应用主入口。"""
    config.ensure_base_on_path()
    _install_excepthook()
    _enable_dpi_awareness()

    # 高 DPI 缩放属性必须在 QApplication 实例化之前设置
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("微软雅黑", 10))

    # 延迟导入，确保 DPI 属性已生效
    from .ui import MainApp

    window = MainApp()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
