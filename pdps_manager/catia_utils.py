# -*- coding: utf-8 -*-
"""CATIA 连接公共逻辑。

原代码在 ToolBoxPage 与 CatiaGunPlacementPage 各写了一份几乎相同的
`try_auto_connect_catia`，这里抽成一个函数供两处复用。
"""

import os
from pathlib import Path

from PyQt5.QtCore import QObject, pyqtSignal

from .config import PYCATIA_AVAILABLE, pycatia_catia


def _com_value(obj, name):
    try:
        value = getattr(obj, name)
        return getattr(value, "com_object", value)
    except Exception:
        return None


def active_3dxml_path(catia_app):
    """Resolve the file behind CATIA's active 3DXML proxy.

    CATIA normally exposes ``Document.FullName``.  For an imported 3DXML
    proxy, some V5 releases expose only the display name and return an empty
    path.  In that case use the exact basename and directories of documents
    already open in CATIA as a read-only path hint.  The function never reads
    source geometry and returns ``None`` when the path cannot be resolved.
    """

    if catia_app is None:
        return None
    app = getattr(catia_app, "com_object", catia_app)
    window = _com_value(app, "ActiveWindow")
    document = _com_value(app, "ActiveDocument")
    window_name = str(getattr(window, "Name", "") or "")
    document_name = str(getattr(document, "Name", "") or "")
    active_name = window_name if window_name.lower().endswith(".3dxml") else document_name
    if not active_name.lower().endswith(".3dxml"):
        return None

    candidates = []
    for obj in (window, document, _com_value(window, "Parent")):
        if obj is None:
            continue
        for attribute in ("FullName", "Path", "FileName"):
            try:
                value = str(getattr(obj, attribute) or "")
            except Exception:
                continue
            if os.path.isabs(value) and value.lower().endswith(".3dxml"):
                candidates.append(Path(value))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)

    basename = Path(active_name).name
    roots = []
    documents = _com_value(app, "Documents")
    try:
        for index in range(1, documents.Count + 1):
            item = documents.Item(index)
            value = str(getattr(item, "FullName", "") or "")
            if os.path.isabs(value):
                parent = Path(value).parent
                roots.extend((parent, parent.parent, parent.parent.parent))
    except Exception:
        pass

    seen_roots = set()
    for root in roots:
        root_key = str(root).casefold()
        if root_key in seen_roots:
            continue
        seen_roots.add(root_key)
        direct = root / basename
        if direct.is_file():
            return str(direct)
        # The file may differ only in case on a non-native share.  Keep the
        # fallback shallow and bounded; normal CATIA projects resolve above.
        try:
            for entry in root.iterdir():
                if entry.is_file() and entry.name.casefold() == basename.casefold():
                    return str(entry)
        except (OSError, PermissionError):
            continue
    return None


def connect_active_catia():
    """连接“已经在运行”的 CATIA 实例（绝不主动启动新进程）。

    返回:
        (catia_app, conn_type)
          - 连接成功: (对象, 'pycatia' 或 'win32com')
          - CATIA 未运行: (None, None)
    异常:
        环境缺失（如未安装 pywin32）时向上抛出，由调用方决定如何提示。
    """
    import win32com.client
    import pythoncom

    try:
        # GetActiveObject 只从运行对象表(ROT)抓取，CATIA 没开会抛 com_error
        raw_catia = win32com.client.GetActiveObject("CATIA.Application")
    except pythoncom.com_error:
        return None, None

    if PYCATIA_AVAILABLE:
        return pycatia_catia(), "pycatia"
    return raw_catia, "win32com"


class CatiaConnectionManager(QObject):
    """全局唯一的 CATIA 连接管理器。

    各功能页共享同一个实例，避免每个页面各自连接；连接状态变化时
    通过 connection_changed 信号广播，页面据此重新绑定工具对象并刷新状态。
    """

    # (app 对象或 None, 连接类型字符串 'pycatia'/'win32com'/'')
    connection_changed = pyqtSignal(object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.app = None
        self.conn_type = ""

    @property
    def connected(self) -> bool:
        return self.app is not None

    def reconnect(self):
        """重新连接已运行的 CATIA，并广播结果。

        返回 (app, conn_type)；CATIA 未运行返回 (None, '')。
        环境异常（缺 pywin32 等）向上抛出，由调用方提示。
        """
        app, ctype = connect_active_catia()  # 环境异常在此向上抛
        self.app = app
        self.conn_type = ctype or ""
        self.connection_changed.emit(self.app, self.conn_type)
        return self.app, self.conn_type
