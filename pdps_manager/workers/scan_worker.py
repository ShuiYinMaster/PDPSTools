# -*- coding: utf-8 -*-
"""ScanWorker —— 在后台线程中递归扫描 .psz 文件。

原 `scan_files` 在主线程同步 os.walk，目录大时界面会假死。这里拆成
两阶段并通过信号回报进度：
  阶段 1：收集所有 .psz 路径（总数未知，进度条走忙碌态）
  阶段 2：逐个解析（总数已知，进度条走确定态）
"""

import os
import traceback

from PyQt5.QtCore import QThread, pyqtSignal


class ScanWorker(QThread):
    phase = pyqtSignal(str)          # 阶段提示文字
    range_known = pyqtSignal(int)    # 已发现的文件总数 → 进度条切确定态
    progress = pyqtSignal(int, str)  # (已处理数量, 当前文件名)
    finished_ok = pyqtSignal(list, int, int)  # (记录列表, 复用数, 新解析数)
    failed = pyqtSignal(str)         # 异常信息

    def __init__(self, search_paths, existing, extract_info, parent=None):
        """
        search_paths: 待扫描的根目录列表
        existing:     {full_path: record_dict}，已存在记录直接复用避免重复解析
        extract_info: 可调用对象 extract_info(psz_path) -> dict，仅做文件IO/正则，
                      不得触碰任何 Qt 控件（线程安全要求）
        """
        super().__init__(parent)
        self._paths = list(search_paths or [])
        self._existing = existing or {}
        self._extract_info = extract_info
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            # ── 阶段 1：收集 .psz 路径 ──────────────────────────────────
            self.phase.emit("正在搜索 .psz 文件...")
            psz_paths = []
            seen = set()
            for bp in self._paths:
                if self._cancelled:
                    return
                if not bp or not os.path.isdir(bp):
                    continue
                for root, _, files in os.walk(bp):
                    if self._cancelled:
                        return
                    for f in files:
                        if f.lower().endswith(".psz"):
                            p = os.path.normpath(os.path.join(root, f))
                            if p not in seen:
                                seen.add(p)
                                psz_paths.append(p)

            total = len(psz_paths)
            self.range_known.emit(total)

            # ── 阶段 2：逐个解析 ────────────────────────────────────────
            records = []
            reused = 0
            parsed = 0
            for i, path in enumerate(psz_paths, 1):
                if self._cancelled:
                    return
                existing = self._existing.get(path, {})
                try:
                    stat = os.stat(path)
                except OSError as exc:
                    rec = dict(existing)
                    rec.update({"File Name": os.path.basename(path), "Full Path": path,
                                "_ScanError": str(exc)})
                    records.append(rec)
                    self.progress.emit(i, os.path.basename(path))
                    continue
                try:
                    cached_size = int(existing.get("_SourceSize"))
                    cached_mtime = int(existing.get("_SourceMtimeNs"))
                except (TypeError, ValueError):
                    cached_size = cached_mtime = None
                if (existing and not existing.get("_ScanError")
                        and cached_size == stat.st_size
                        and cached_mtime == stat.st_mtime_ns):
                    records.append(dict(existing))
                    reused += 1
                else:
                    rec = dict(existing)
                    rec.update({"File Name": os.path.basename(path), "Full Path": path})
                    try:
                        extracted = self._extract_info(path)
                        for key, value in extracted.items():
                            if key == "System Root":
                                if (not rec.get(key)
                                        or rec.get(key) == rec.get("_AutoSystemRoot")):
                                    rec[key] = value
                                rec["_AutoSystemRoot"] = value
                            elif not rec.get(key):
                                rec[key] = value
                        rec["_SourceSize"] = stat.st_size
                        rec["_SourceMtimeNs"] = stat.st_mtime_ns
                        rec["_ScanError"] = ""
                    except Exception as exc:
                        rec["_ScanError"] = str(exc)
                    records.append(rec)
                    parsed += 1
                self.progress.emit(i, os.path.basename(path))

            self.finished_ok.emit(records, reused, parsed)
        except Exception as e:
            traceback.print_exc()
            self.failed.emit(str(e))
