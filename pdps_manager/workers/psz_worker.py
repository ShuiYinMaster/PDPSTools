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
    find_usage_elements_containing, replace_zip_entries, psz_fingerprint,
    encode_replacement_xml,
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
      objects((entry,ext_id)->ObjInfo) / entry_objects({entry:{ext_id:ObjInfo}})
      dup / broken_3d(带entry) / dangling
      obj_list(排序列表) / entry_stats / file_size / entry_count
    """
    def _report(stage):
        if progress_cb:
            progress_cb(stage)

    fingerprint = psz_fingerprint(psz_path)
    _report("读取压缩包…")
    with zipfile.ZipFile(psz_path, 'r') as z:
        names = z.namelist()
        entry_count = len(names)
        entry_names = [n for n in (PS_STATE, EMS_STATE) if n in names]
    if not entry_names:
        raise ValueError(f"包内不存在 {PS_STATE} / {EMS_STATE}，无法解析该文件")

    contents, encs, entry_objects, dup = _parse_entries_serial(
        psz_path, entry_names, progress_cb)

    # Entry is part of an object's identity. Equal IDs in PS and EMS are distinct.
    objects = {(entry, eid): obj for entry, objs in entry_objects.items()
               for eid, obj in objs.items()}

    _report("检查断裂引用…")
    broken_3d, dangling, entry_stats = check_broken_entries(contents, entry_objects, objects)

    _report("XML 合规性审计…")
    xml_violations, _ = check_xml_validity_entries(contents)

    obj_list = sorted(objects.values(), key=lambda o: (o.tag, o.name or o.ext_id, o.entry))
    if psz_fingerprint(psz_path) != fingerprint:
        raise RuntimeError("PSZ 在解析期间发生变化，请重新解析")
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
        "fingerprint": fingerprint,
    }


class PszLoadWorker(QThread):
    """只读解析：加载 psz 后返回快照。"""

    loaded = pyqtSignal(dict)
    failed = pyqtSignal(str)
    progress = pyqtSignal(str)      # 阶段进度文本

    def __init__(self, psz_path, parent=None):
        super().__init__(parent)
        self._path = psz_path
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            def report(stage):
                if self._cancelled:
                    raise InterruptedError("解析已取消")
                self.progress.emit(stage)

            snap = build_snapshot(self._path, progress_cb=report)
            if not self._cancelled:
                self.loaded.emit(snap)
        except InterruptedError:
            pass
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

    def __init__(self, psz_path, contents, encs, mode, params, parent=None,
                 expected_fingerprint=None):
        """
        contents: {entry: XML文本}   encs: {entry: 编码}
        mode ∈ {"delete", "delete_broken", "rebuild"}
        params: delete 需 {"targets": [(entry, ext_id)], "clean": bool}
                rebuild 需 {"xml_path": str}
                均可含 {"backup": bool}（默认 True）
        """
        super().__init__(parent)
        self._path = psz_path
        self._contents = dict(contents)
        self._encs = dict(encs)
        self._mode = mode
        self._params = params or {}
        self._expected_fingerprint = expected_fingerprint

    def run(self):
        try:
            backup = self._params.get("backup", True)
            if self._mode == "rebuild":
                replacements = {PS_STATE: self._rebuild_bytes()}
            else:
                contents = dict(self._contents)
                deleted_ids = set()
                if self._mode == "delete":
                    targets = self._params.get("targets", [])
                    if not targets:
                        raise ValueError("删除对象需要所属条目和对象 ID")
                    by_entry = {}
                    for entry, ext_id in targets:
                        if entry not in contents:
                            raise ValueError(f"PSZ 中不存在条目: {entry}")
                        by_entry.setdefault(entry, set()).add(ext_id)
                    for entry, ids in by_entry.items():
                        objects, _ = find_objects(contents[entry], entry=entry)
                        missing = ids - objects.keys()
                        if missing:
                            raise ValueError(f"条目 {entry} 中不存在对象: {sorted(missing)}")
                        spans = [(objects[eid].start, objects[eid].end) for eid in ids]
                        contents[entry] = delete_many(contents[entry], spans)
                        deleted_ids.update(ids)
                elif self._mode == "delete_broken":
                    for entry, text in self._contents.items():
                        objects, _ = find_objects(text, entry=entry)
                        broken, _, _ = check_broken(text, objects)
                        ids = {eid for eid, *_ in broken}
                        if ids:
                            spans = [(objects[eid].start, objects[eid].end) for eid in ids]
                            contents[entry] = delete_many(text, spans)
                            deleted_ids.update(ids)
                else:
                    raise ValueError(f"未知修复模式: {self._mode}")

                if deleted_ids and self._params.get("clean", True):
                    remaining_ids = {eid for entry, text in contents.items()
                                     for eid in find_objects(text, entry=entry)[0]}
                    # A surviving object with the same ID may still own references.
                    unreferenced_ids = deleted_ids - remaining_ids
                    for entry in contents:
                        contents[entry], _ = _clean_usage_loop(
                            contents[entry], unreferenced_ids)

                replacements = {
                    entry: encode_xml(text, self._encs[entry])
                    for entry, text in contents.items()
                    if text != self._contents[entry]
                }

            _, backup_path = replace_zip_entries(
                self._path, replacements, backup=backup,
                expected_fingerprint=self._expected_fingerprint,
            )
            snapshot = build_snapshot(self._path)
            snapshot["backup_path"] = backup_path
            self.done.emit(snapshot)
        except Exception as exc:
            self.failed.emit(str(exc))

    def _rebuild_bytes(self):
        """Read an externally edited XML and convert it to the source encoding."""
        xml_path = self._params.get("xml_path")
        if not xml_path:
            raise ValueError("rebuild 需要指定 xml_path")
        with zipfile.ZipFile(self._path, 'r') as archive:
            src_enc = detect_encoding(read_zip_entry(archive, PS_STATE))
        with open(xml_path, 'rb') as source:
            return encode_replacement_xml(source.read(), src_enc)
