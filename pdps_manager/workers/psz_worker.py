# -*- coding: utf-8 -*-
"""PSZ 解析/修复后台线程：把耗时解析与 zip 重建移出主线程，避免界面卡死。

复用 core/psz_parser.py 中的纯函数（不调用会 print 到 stdout 的 cmd_* 命令），
解析结果以“快照”dict 形式通过信号回传给 GUI。

.psz 内 StandaloneStudy_PsState.xml 与 StandaloneStudy_EmsState.xml 都可能是
对象模型，快照同时读取两者并合并：对象/断裂/悬空均带所属条目（ObjInfo.entry），
删除类修复会分别写回对应 zip 条目。
"""

import os
import zipfile

from PyQt5.QtCore import QThread, pyqtSignal

from ..core.psz_parser import (
    PS_STATE, EMS_STATE, read_zip_entry, detect_encoding, decode_xml, encode_xml,
    find_objects, check_broken, check_broken_entries, check_xml_validity_entries,
    delete_many,
    find_usage_elements_containing, rebuild_zip, ObjInfo,
)


def _entry_short(name):
    """zip 条目名 → 短标签（PS / EMS），用于进度提示。"""
    if name == PS_STATE:
        return "PS"
    if name == EMS_STATE:
        return "EMS"
    return name


def _parse_entries_serial(psz_path, entry_names, progress_cb=None):
    """串行解析多个状态条目（默认路径，稳妥可靠）。

    返回 (contents, encs, entry_objects, dup)。
    """
    contents, encs, entry_objects, dup = {}, {}, {}, []
    for idx, name in enumerate(entry_names, 1):
        if progress_cb:
            progress_cb(f"正在解析 {_entry_short(name)} 对象模型 ({idx}/{len(entry_names)})…")
        with zipfile.ZipFile(psz_path, 'r') as z:
            raw = read_zip_entry(z, name)
        encs[name] = detect_encoding(raw)
        text = decode_xml(raw)
        contents[name] = text
        objs, dup_here = find_objects(text, entry=name)
        entry_objects[name] = objs
        dup.extend(dup_here)
    return contents, encs, entry_objects, dup


def build_snapshot(psz_path, progress_cb=None):
    """解析一个 .psz 并返回完整快照，供 GUI 显示与后续编辑。

    progress_cb: 可选回调 (阶段文本) -> None，用于 GUI 实时反馈进度。

    返回 dict：
      path / contents({entry:文本}) / encs({entry:编码})
      objects(ext_id->ObjInfo) / entry_objects({entry:{ext_id:ObjInfo}})
      dup / broken_3d(带entry) / dangling
      obj_list(排序列表) / entry_stats / file_size / entry_count
    """
    def _report(stage):
        if progress_cb:
            progress_cb(stage)

    _report("读取压缩包…")
    with zipfile.ZipFile(psz_path, 'r') as z:
        names = z.namelist()
        entry_count = len(names)
        entry_names = [n for n in (PS_STATE, EMS_STATE) if n in names]
    if not entry_names:
        raise ValueError(f"包内不存在 {PS_STATE} / {EMS_STATE}，无法解析该文件")

    contents, encs, entry_objects, dup = _parse_entries_serial(
        psz_path, entry_names, progress_cb)

    # 合并对象：同一 ID 可能同时存在于 PS 与 EMS（两份独立对象模型，非重复），
    # 合并时后者覆盖，但不计入 dup（dup 仅指单条目内部的重复 ID）
    objects = {}
    for objs in entry_objects.values():
        objects.update(objs)

    _report("检查断裂引用…")
    broken_3d, dangling, entry_stats = check_broken_entries(contents, entry_objects, objects)

    _report("XML 合规性审计…")
    xml_violations, _ = check_xml_validity_entries(contents)

    obj_list = sorted(objects.values(), key=lambda o: (o.tag, o.name or o.ext_id))
    return {
        "path": psz_path,
        "contents": contents,
        "encs": encs,
        "objects": objects,
        "entry_objects": entry_objects,
        "dup": dup,
        "broken_3d": broken_3d,
        "dangling": dangling,
        "xml_violations": xml_violations,
        "obj_list": obj_list,
        "entry_stats": entry_stats,
        "file_size": os.path.getsize(psz_path),
        "entry_count": entry_count,
    }


class PszLoadWorker(QThread):
    """只读解析：加载 psz 后返回快照。"""

    loaded = pyqtSignal(dict)
    failed = pyqtSignal(str)
    progress = pyqtSignal(str)      # 阶段进度文本

    def __init__(self, psz_path, parent=None):
        super().__init__(parent)
        self._path = psz_path

    def run(self):
        try:
            snap = build_snapshot(self._path, progress_cb=self.progress.emit)
            self.loaded.emit(snap)
        except Exception as e:
            self.failed.emit(str(e))


def _clean_usage_loop(content, ids):
    """反复清理内容中引用 ids 的 Pm*Usage，返回 (新内容, 删除数)。"""
    removed = 0
    while True:
        targets = find_usage_elements_containing(content, ids)
        if not targets:
            break
        content = delete_many(content, [(s, e) for _, s, e in targets])
        removed += len(targets)
    return content, removed


class PszRepairWorker(QThread):
    """执行修改操作（删除对象/批量删除断裂/重建打包）后重建 zip 并重解析。

    支持同时对 PsState 与 EmsState 生效：删除按对象所属条目定位，
    引用清理在所有条目中执行，最终把每个被改动的条目写回 zip。
    """

    done = pyqtSignal(dict)      # 新快照
    failed = pyqtSignal(str)

    def __init__(self, psz_path, contents, encs, mode, params, parent=None):
        """
        contents: {entry: XML文本}   encs: {entry: 编码}
        mode ∈ {"delete", "delete_broken", "rebuild"}
        params: delete 需 {"ids": [...], "clean": bool}
                rebuild 需 {"xml_path": str}
                均可含 {"backup": bool}（默认 True）
        """
        super().__init__(parent)
        self._path = psz_path
        self._contents = dict(contents)
        self._encs = dict(encs)
        self._mode = mode
        self._params = params or {}

    def run(self):
        try:
            backup = self._params.get("backup", True)

            if self._mode == "rebuild":
                self._run_rebuild(backup)
                return

            contents = dict(self._contents)

            if self._mode == "delete":
                ids = self._params.get("ids", [])
                # 按对象所属条目删除；同一 ID 可能同时存在于 PS 与 EMS
                for entry in list(contents):
                    objects, _ = find_objects(contents[entry], entry=entry)
                    spans = [(objects[eid].start, objects[eid].end)
                             for eid in ids if eid in objects]
                    if spans:
                        contents[entry] = delete_many(contents[entry], spans)
                if self._params.get("clean", True):
                    for entry in list(contents):
                        contents[entry], _ = _clean_usage_loop(contents[entry], ids)

            elif self._mode == "delete_broken":
                for entry in list(contents):
                    objects, _ = find_objects(contents[entry], entry=entry)
                    broken_3d, _, _ = check_broken(contents[entry], objects)
                    ids = [eid for eid, *_ in broken_3d]
                    if not ids:
                        continue
                    spans = [(objects[eid].start, objects[eid].end)
                             for eid in ids if eid in objects]
                    contents[entry] = delete_many(contents[entry], spans)
                    # 清理所有条目里引用这些对象的 Pm*Usage
                    for e2 in list(contents):
                        contents[e2], _ = _clean_usage_loop(contents[e2], ids)

            else:
                raise ValueError(f"未知修复模式: {self._mode}")

            # 逐个写回被修改的条目（首次写时做备份，避免同一次操作重复备份）
            written = False
            for entry, text in contents.items():
                if text != self._contents[entry]:
                    xml_bytes = encode_xml(text, self._encs[entry])
                    rebuild_zip(self._path, entry, xml_bytes,
                                backup=backup and not written)
                    written = True
            self.done.emit(build_snapshot(self._path))
        except Exception as e:
            self.failed.emit(str(e))

    def _run_rebuild(self, backup):
        """把外部修改后的 XML 打回 psz（自动转回源条目编码）。"""
        xml_path = self._params.get("xml_path")
        if not xml_path:
            raise ValueError("rebuild 需要指定 xml_path")
        with zipfile.ZipFile(self._path, 'r') as z:
            raw_old = read_zip_entry(z, PS_STATE)
        src_enc = detect_encoding(raw_old)
        with open(xml_path, "rb") as f:
            new_bytes = f.read()
        new_enc = detect_encoding(new_bytes)
        if new_enc != src_enc:
            text = new_bytes.decode(new_enc)
            new_bytes = encode_xml(text, src_enc)
        rebuild_zip(self._path, PS_STATE, new_bytes, backup=backup)
        self.done.emit(build_snapshot(self._path))