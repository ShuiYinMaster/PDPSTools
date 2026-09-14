#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PSZ 解析器 - Process Simulate 工程文件 (.psz) 解析与编辑工具

.psz 本质是 ZIP 压缩包，核心内容是一个对象序列化 XML：
  StandaloneStudy_PsState.xml   - PS 主对象模型（几何/操作/资源/路径等）
  StandaloneStudy_EmsState.xml  - EMS 状态
  ExternalHierarchy.xml         - 外部层级
  EngData/...                   - JT 几何、TuneData 等资源

编码说明：PsState XML 可能为 UTF-8 或 GB2312/GBK，本工具自动检测，无需手动指定。

用法示例:
  # 1) 列出包内文件
  python psz_parser.py study.psz list

  # 2) 查看概要（编码/大小/对象统计/断裂引用数）
  python psz_parser.py study.psz info

  # 3) 按类型列出对象（可 --type 过滤）
  python psz_parser.py study.psz objects --type PmContinuousMfg

  # 4) 按 ID 或名称搜索对象
  python psz_parser.py study.psz search Seam
  python psz_parser.py study.psz search 00F7339F

  # 5) 查看某个对象的完整 XML
  python psz_parser.py study.psz show 00F7339F-13F8-49EE-89B5-D675F9577094

  # 6) 检查断裂引用（threeDRep 指向不存在资源 / <uses> 悬空）
  python psz_parser.py study.psz check

  # 7) 删除对象（自动清理引用它的 Pm*Usage；默认自动备份）
  python psz_parser.py study.psz delete 00F7339F-13F8-49EE-89B5-D675F9577094

  # 8) 批量删除所有 threeDRep 为 TuneTbm-（断裂）的对象
  python psz_parser.py study.psz delete-broken --dry-run   # 先预览
  python psz_parser.py study.psz delete-broken             # 执行

  # 9) 提取 XML 到本地（方便外部工具查看/修改）
  python psz_parser.py study.psz extract

  # 10) 把已修改的 XML 重新打包回 psz（自动备份原文件）
  python psz_parser.py study.psz rebuild --xml PsState_modified.xml

工作流（推荐）:
  1. cp 备份原 psz（工具也会自动备份）
  2. check 查看断裂对象 -> delete-broken --dry-run 预览 -> delete-broken 执行
  3. 打开验证；若需手工改 XML：extract -> 修改 -> rebuild
"""

import argparse
import html
import ntpath
import os
import re
import shutil
import sys
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

PS_STATE = 'StandaloneStudy_PsState.xml'
EMS_STATE = 'StandaloneStudy_EmsState.xml'
TAG_RE = re.compile(r'<(/?)([A-Za-z][A-Za-z0-9_]*)([^>]*?)(/?)>')
EXTERNAL_RE = re.compile(r'ExternalId\s*=\s*"([^"]+)"')
ATTR_NAME_RE = re.compile(r'\bname\s*=\s*"([^"]*)"')
CHILD_NAME_RE = re.compile(r'<name>\s*([^<]*?)\s*</name>')

# ---------------------------------------------------------------------------
# 1. 基础：读取 zip / 编码检测
# ---------------------------------------------------------------------------

def read_zip_entry(zf, name):
    """读取 zip 内条目原始字节。"""
    with zf.open(name) as f:
        return f.read()


def detect_encoding(data: bytes) -> str:
    """
    根据 XML 声明与内容试探编码，返回 'utf-8' 或 'gbk'（兼容 gb2312）。
    声明可能与实际字节不一致（例如工具把 gb2312 声明的内容转存为 UTF-8），
    因此用实际解码做校验：声明解码失败时回退尝试另一种。
    """
    head = data[:512]
    declared = None
    m = re.search(rb'encoding\s*=\s*["\']([^"\']+)["\']', head, re.I)
    if m:
        enc = m.group(1).decode('ascii', errors='ignore').strip().lower()
        declared = 'gbk' if enc in ('gb2312', 'gbk', 'gb18030') else 'utf-8'

    def try_decode(enc):
        try:
            data.decode(enc)
            return True
        except UnicodeDecodeError:
            return False

    if declared and try_decode(declared):
        return declared
    # 声明不可信或声明解码失败：按实际内容判断
    if try_decode('utf-8'):
        return 'utf-8'
    return 'gbk'


def decode_xml(data: bytes) -> str:
    enc = detect_encoding(data)
    return data.decode(enc)


def encode_xml(text: str, enc: str) -> bytes:
    return text.encode(enc)


# PSZ 中外部资源通常通过 PmReferenceFile/fileName 保存为
# ``#\\ResourceLibrary\\...`` 这种形式，``#\\`` 表示 System Root。
# 该正则只取 fileName，避免把 XML 中的普通 relativeTo、position 等字段
# 误判为文件路径。
_FILE_NAME_RE = re.compile(
    r"<fileName\b[^>]*>\s*(.*?)\s*</fileName>", re.IGNORECASE | re.DOTALL
)


def _psz_relative_paths(psz_path: str):
    """读取 PSZ XML 中以 ``#\\`` 开头的相对资源路径。"""
    paths = []
    try:
        with zipfile.ZipFile(psz_path, "r") as zf:
            for name in zf.namelist():
                if not name.lower().endswith(".xml"):
                    continue
                try:
                    text = decode_xml(zf.read(name))
                except Exception:
                    # 单个非关键 XML 编码异常不应阻断整个项目扫描。
                    continue

                for match in _FILE_NAME_RE.finditer(text):
                    value = html.unescape(match.group(1)).strip()
                    if not value.startswith(("#\\", "#/")):
                        continue

                    # 去掉 PSZ 的根标记。#\\C:\\... / #\\\\server\\...
                    # 是外部绝对路径，不是可用于反推 System Root 的相对路径。
                    rootless = value[1:]
                    if rootless.startswith(("\\\\", "//")):
                        continue
                    relative = rootless.lstrip("\\/")
                    if (not relative or ntpath.isabs(relative)
                            or ntpath.splitdrive(relative)[0]):
                        continue

                    # 工程运行环境是 Windows；统一分隔符后交给 os.path
                    # 处理 .、.. 以及路径规范化。
                    relative = relative.replace("/", os.sep).replace("\\", os.sep)
                    normalized = os.path.normpath(relative)
                    if normalized not in paths:
                        paths.append(normalized)
    except (OSError, zipfile.BadZipFile):
        return []
    return paths


def _library_child(directory: str):
    """返回目录下名为 Library 的子目录（大小写不敏感）。"""
    try:
        for entry in os.scandir(directory):
            if entry.is_dir() and entry.name.lower() == "library":
                return entry.path
    except OSError:
        pass
    return None


def _sysroot_candidates(psz_dir: str):
    """按离 PSZ 的距离生成可能的 System Root 目录。"""
    current = os.path.abspath(psz_dir)
    seen = set()
    while current and current not in seen:
        seen.add(current)
        yield current

        # 常见结构是 <project>\\Library\\<study>.psz；PSZ 也可能位于
        # <project>\\<study>.psz，此时 Library 是当前目录的子目录。
        library = _library_child(current)
        if library and library not in seen:
            seen.add(library)
            yield library

        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent


def resolve_system_root_from_psz(psz_path: str) -> str:
    """根据 PSZ 内的相对资源引用解析 System Root 的绝对路径。

    PSZ 中 ``#\\foo\\bar.cojt`` 的 ``#`` 是 System Root 占位符。函数会
    将这些引用分别拼接到 PSZ 目录及其祖先目录（以及各级 ``Library``
    子目录），以磁盘上实际存在的引用数量判断哪个目录才是 ``#`` 的
    绝对根目录。找不到任何已落盘引用时，保留旧逻辑的安全回退：优先
    使用 PSZ 目录下的 Library，其次使用 PSZ 所在目录。
    """
    psz_dir = os.path.dirname(os.path.abspath(psz_path))
    relative_paths = _psz_relative_paths(psz_path)

    best_root = None
    best_matches = 0
    for candidate in _sysroot_candidates(psz_dir):
        matches = sum(
            os.path.exists(os.path.join(candidate, relative_path))
            for relative_path in relative_paths
        )
        if matches > best_matches:
            best_root = candidate
            best_matches = matches

    if best_root:
        return os.path.normpath(best_root)

    library = _library_child(psz_dir)
    if library:
        return os.path.normpath(library)
    if os.path.basename(psz_dir).lower() == "library":
        return os.path.normpath(psz_dir)
    return os.path.normpath(psz_dir)


# ---------------------------------------------------------------------------
# 2. 对象扫描：栈式定位所有顶层对象（带 ExternalId 的元素）
# ---------------------------------------------------------------------------

class ObjInfo:
    __slots__ = ('tag', 'ext_id', 'name', 'start', 'end', 'attrs', 'depth', 'top_level', 'entry')

    def __init__(self, tag, ext_id, name, start, end, attrs='', depth=0, top_level=False,
                 entry=PS_STATE):
        self.tag = tag
        self.ext_id = ext_id
        self.name = name
        self.start = start      # 开标签 '<' 位置
        self.end = end          # 闭标签 '>' 之后的位置（含）
        self.attrs = attrs
        self.depth = depth      # XML 深度（Objects 下第一层 = 1）
        self.top_level = top_level  # 是否是 <Objects> 的直接子元素
        self.entry = entry      # 所属 zip 条目（PS_STATE / EMS_STATE）


def find_objects(content: str, entry: str = PS_STATE):
    """
    扫描 XML，找出所有带 ExternalId 的元素。

    性能优化：用 str.find 逐标签扫描（避免对 4M+ 标签逐个跑正则的引擎开销），
    并且在“开标签”时就抓取 ExternalId/name 存入栈（大部分标签没有 ExternalId，
    用 `in` 快速短路），不再等到闭标签时二次切片+正则提取。

    参数:
      content  某状态条目的 XML 文本
      entry    该文本所属 zip 条目名（PS_STATE/EMS_STATE），写入 ObjInfo 供后续区分

    返回 (objects, dup):
      objects: dict ext_id -> ObjInfo（仅包含开标签自身带 ExternalId 的元素，
               避免把根元素/容器误判；top_level 标记 <Objects> 直接子级）
      dup:     重复 ID 列表
    """
    objects = {}
    dup = []
    # 栈元素: (tag, open_start, open_end, ext_id|None, name_from_attr, attrs)
    stack = []
    n = len(content)
    i = 0
    find = content.find
    while i < n:
        lt = find('<', i)
        if lt < 0:
            break
        gt = find('>', lt + 1)
        if gt < 0:
            break
        body = content[lt + 1:gt]
        i = gt + 1
        if not body:
            continue
        first = body[0]
        if first in '?!':       # <?xml…?> / <!DOCTYPE / <!--…--> 等非元素，跳过
            continue
        if first == '/':
            # 闭标签：出栈找到最近匹配的开标签
            tag = body[1:].split(' ', 1)[0]
            for k in range(len(stack) - 1, -1, -1):
                if stack[k][0] == tag:
                    open_start, open_end, ext_id, name, attrs = stack[k][1:]
                    parent_tag = stack[k - 1][0] if k > 0 else None
                    del stack[k:]
                    if ext_id:
                        if not name:
                            nm = CHILD_NAME_RE.search(content, open_end, lt)
                            name = nm.group(1) if nm else ''
                        obj = ObjInfo(tag, ext_id, name, open_start, gt + 1,
                                      attrs, depth=len(stack) + 1,
                                      top_level=(parent_tag == 'Objects'),
                                      entry=entry)
                        if ext_id in objects:
                            dup.append(ext_id)
                        objects[ext_id] = obj
                    break
        elif body[-1] == '/':
            # 自闭合标签：开标签自身带 ExternalId 才算对象
            attrs = body[:-1]
            if 'ExternalId' in attrs:
                em = EXTERNAL_RE.search(attrs)
                if em:
                    ext_id = em.group(1)
                    nm = ATTR_NAME_RE.search(attrs)
                    name = nm.group(1) if nm else ''
                    parent_tag = stack[-1][0] if stack else None
                    obj = ObjInfo(body.split(' ', 1)[0], ext_id, name, lt, i,
                                  attrs.strip(), depth=len(stack) + 1,
                                  top_level=(parent_tag == 'Objects'),
                                  entry=entry)
                    if ext_id in objects:
                        dup.append(ext_id)
                    objects[ext_id] = obj
        else:
            # 开标签：立即提取 ExternalId（无则不入 objects）
            ext_id = None
            name = ''
            attrs = None
            if 'ExternalId' in body:
                em = EXTERNAL_RE.search(body)
                if em:
                    ext_id = em.group(1)
                    nm = ATTR_NAME_RE.search(body)
                    name = nm.group(1) if nm else ''
                    attrs = body.strip()   # 仅带 ExternalId 的对象需要保留属性
            stack.append((body.split(' ', 1)[0], lt, i, ext_id, name, attrs))

    return objects, dup


def get_top_level_ids(objects: dict) -> set:
    """粗略判断顶层对象：外部ID以 TuneTbm-/PP- 开头或纯 GUID 且不带前缀的，
    主要供参考，不用于删除决策。"""
    return set(objects.keys())


# ---------------------------------------------------------------------------
# 3. 文本级删除（保留格式）
# ---------------------------------------------------------------------------

def delete_element(content: str, start: int, end: int) -> str:
    """删除 [start,end) 区间元素并吸收相邻空白，返回新字符串。"""
    l = start
    while l > 0 and content[l - 1] in ' \t\r\n':
        l -= 1
    r = end
    while r < len(content) and content[r] in ' \t\r\n':
        r += 1
    return content[:l] + content[r:]


def delete_many(content: str, spans) -> str:
    """
    批量删除多个 [start,end) 区间。
    注意：spans 必须来自同一份 content 快照，函数内部按位置倒序处理，
    删除靠后的区间不影响靠前的坐标。
    """
    spans = sorted(spans, key=lambda x: x[1], reverse=True)
    for s, e in spans:
        l = s
        while l > 0 and content[l - 1] in ' \t\r\n':
            l -= 1
        r = e
        while r < len(content) and content[r] in ' \t\r\n':
            r += 1
        content = content[:l] + content[r:]
    return content


def delete_object(content: str, obj: ObjInfo) -> str:
    return delete_element(content, obj.start, obj.end)


# ---------------------------------------------------------------------------
# 4. 引用检查
# ---------------------------------------------------------------------------

def check_broken(content: str, objects: dict, all_ids=None):
    """返回 (broken_3d, dangling_uses, report_lines)

    all_ids: 可选的全局已知 ID 集合（用于跨条目判断 <uses> 悬空）；
             为 None 时以本条目 objects 的 key 为准。
    """
    report = []
    existing_ids = set(all_ids) if all_ids is not None else set(objects.keys())

    # 4.1 threeDRep 指向 TuneTbm-（外部/断开引用，且 zip 内无资源）
    broken_3d = []
    for ext_id, obj in objects.items():
        seg = content[obj.start:obj.end]
        m = re.search(r'<threeDRep>\s*([^<]*TuneTbm[^<]*)</threeDRep>', seg)
        if m:
            broken_3d.append((ext_id, obj.tag, obj.name, m.group(1).strip()))

    # 4.2 <uses> 悬空
    dangling_uses = []
    for m in re.finditer(r'<uses>\s*([^<]+?)\s*</uses>', content):
        uid = m.group(1).strip()
        if uid not in existing_ids:
            dangling_uses.append((uid, m.start()))

    return broken_3d, dangling_uses, report


def check_broken_entries(contents: dict, per_entry_objects: dict, all_objects: dict = None):
    """跨条目检查断裂引用。

    参数:
      contents          {entry名: XML文本}（PS_STATE / EMS_STATE）
      per_entry_objects {entry名: {ext_id: ObjInfo}}，每个条目自己扫描的结果
      all_objects       可选的合并对象字典（用于跨条目判断 <uses> 悬空）

    返回 (broken_3d, dangling_uses, entry_stats):
      broken_3d   5 元组列表 (ext_id, tag, name, tbm, entry)，带来源条目
      dangling_uses  [(uid, pos), ...]（pos 相对各自条目文本）
      entry_stats {entry: {"objects": n, "broken": n, "dangling": n}}
    """
    if all_objects is None:
        all_objects = {}
        for objs in per_entry_objects.values():
            all_objects.update(objs)
    all_ids = set(all_objects.keys())
    broken_3d = []
    dangling_uses = []
    entry_stats = {}
    for entry, text in contents.items():
        objs = per_entry_objects.get(entry, {})
        b, d, _ = check_broken(text, objs, all_ids=all_ids)
        broken_3d.extend((eid, tag, name, tbm, entry) for eid, tag, name, tbm in b)
        dangling_uses.extend(d)
        entry_stats[entry] = {"objects": len(objs), "broken": len(b), "dangling": len(d)}
    return broken_3d, dangling_uses, entry_stats


# ---------------------------------------------------------------------------
# 4.3 XML 合规性审计：检测非转义 & 等非法字符
# ---------------------------------------------------------------------------

# XML 合法的预定义实体
_VALID_ENTITIES = frozenset(('&amp;', '&lt;', '&gt;', '&quot;', '&apos;'))


def check_xml_validity(content: str, entry: str = PS_STATE):
    """扫描 XML 文本，找出所有非转义 & 字符（raw &amp; 不属于合法实体）。

    返回 list[dict]，每条记录包含:
      line   行号（1-based）
      column 列号（1-based）
      start  该错误在 content 中的字符偏移
      context 前后 ±40 字符的上下文片段
      entry  所属 zip 条目名
    """
    violations = []
    n = len(content)

    # 预建行号索引：line_starts[i] = 第 i 行起始的字符偏移
    line_starts = [0]
    for i, ch in enumerate(content):
        if ch == '\n':
            line_starts.append(i + 1)

    def _offset_to_lc(offset):
        """字符偏移 → (line, column)，均为 1-based。"""
        lo = 0
        hi = len(line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if line_starts[mid] <= offset:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1, offset - line_starts[lo] + 1

    def _context(offset, radius=40):
        """截取 offset 附近的上下文。"""
        s = max(0, offset - radius)
        e = min(n, offset + radius + 1)
        seg = content[s:e]
        if s > 0:
            seg = '…' + seg
        if e < n:
            seg = seg + '…'
        return seg

    i = 0
    while i < n:
        amp = content.find('&', i)
        if amp < 0:
            break

        # 检查是否为合法实体引用（&...; 序列匹配预定义实体 或 &#...; 数字引用）
        is_valid = False
        semi = content.find(';', amp + 1)
        if semi >= 0 and semi - amp <= 10:
            entity_str = content[amp:semi + 1]
            if entity_str in _VALID_ENTITIES:
                is_valid = True
            elif entity_str.startswith('&#'):
                # &#xHEX; 或 &#DEC; 数字字符引用
                num_part = entity_str[2:-1]
                if num_part.isdigit() or (num_part.startswith('x') and num_part[1:].isalnum()):
                    is_valid = True

        if not is_valid:
            line, col = _offset_to_lc(amp)
            violations.append({
                'line': line,
                'column': col,
                'start': amp,
                'context': _context(amp),
                'entry': entry,
            })

        i = amp + 1

    return violations


def check_xml_validity_entries(contents: dict):
    """对每个 zip 条目运行 XML 合规性审计。

    返回 (violations, entry_stats):
      violations  list[dict] —— 所有条目的违规记录（每条带 entry 字段）
      entry_stats {entry: {"violations": n}}
    """
    all_violations = []
    entry_stats = {}
    for entry, text in contents.items():
        v = check_xml_validity(text, entry)
        all_violations.extend(v)
        entry_stats[entry] = {"violations": len(v)}
    return all_violations, entry_stats


# ---------------------------------------------------------------------------
# 5. 重新打包
# ---------------------------------------------------------------------------

def _scan_entry_process(psz_path, entry):
    """供多进程并行解析单个状态条目（picklable，无 Qt 依赖）。

    返回 (entry, enc, text, rows, dup)，其中 rows 为可序列化的对象元组列表：
      [(ext_id, tag, name, start, end, attrs, depth, top_level), ...]
    由调用方重建为 ObjInfo 并写入快照。
    """
    with zipfile.ZipFile(psz_path, 'r') as z:
        raw = read_zip_entry(z, entry)
    enc = detect_encoding(raw)
    text = decode_xml(raw)
    objs, dup = find_objects(text, entry=entry)
    rows = [(o.ext_id, o.tag, o.name, o.start, o.end, o.attrs, o.depth, o.top_level)
            for o in objs.values()]
    return entry, enc, text, rows, dup


def rebuild_zip(src_psz: str, xml_name: str, xml_bytes: bytes,
                out_psz: str = None, backup: bool = True):
    """把修改后的 XML 打回 psz，其余条目原样复制。返回输出路径。"""
    if out_psz is None:
        out_psz = src_psz
    if backup and out_psz == src_psz:
        bak = src_psz + '.bak'
        shutil.copy2(src_psz, bak)

    tmp = src_psz + '.tmp'
    with zipfile.ZipFile(src_psz, 'r') as zsrc:
        orig_info = zsrc.getinfo(xml_name)
        names = zsrc.namelist()
        with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as zdst:
            for name in names:
                si = zsrc.getinfo(name)
                if name == xml_name:
                    ni = zipfile.ZipInfo(name, orig_info.date_time)
                    ni.compress_type = orig_info.compress_type
                    ni.external_attr = orig_info.external_attr
                    ni.create_system = orig_info.create_system
                    zdst.writestr(ni, xml_bytes)
                else:
                    raw = read_zip_entry(zsrc, name)
                    ni = zipfile.ZipInfo(name, si.date_time)
                    ni.compress_type = si.compress_type
                    ni.external_attr = si.external_attr
                    ni.create_system = si.create_system
                    zdst.writestr(ni, raw)
    if out_psz == src_psz:
        shutil.copy2(tmp, src_psz)
    else:
        shutil.move(tmp, out_psz)
    try:
        os.remove(tmp)
    except OSError:
        pass
    return out_psz


# ---------------------------------------------------------------------------
# 6. CLI 命令
# ---------------------------------------------------------------------------

def cmd_list(args):
    with zipfile.ZipFile(args.file, 'r') as z:
        infos = z.infolist()
        total = 0
        print(f'{"名称":<60} {"大小":>12} {"压缩":>12}')
        print('-' * 90)
        for info in sorted(infos, key=lambda x: x.filename):
            print(f'{info.filename:<60} {info.file_size:>12,} {info.compress_size:>12,}')
            total += info.file_size
        print('-' * 90)
        print(f'共 {len(infos)} 个条目, 解压后总大小 {total:,} bytes')


def cmd_info(args):
    with zipfile.ZipFile(args.file, 'r') as z:
        infos = z.infolist()
        names = z.namelist()
        print(f'文件: {args.file}')
        print(f'大小: {os.path.getsize(args.file):,} bytes, 条目数: {len(infos)}')
        if PS_STATE in names:
            data = read_zip_entry(z, PS_STATE)
            enc = detect_encoding(data)
            content = decode_xml(data)
            print(f'\n[{PS_STATE}]')
            print(f'  编码: {enc}  解压大小: {len(data):,} bytes')
            objects, dup = find_objects(content)
            top = {eid: o for eid, o in objects.items() if o.top_level}
            print(f'  对象数(顶层): {len(top)}  含嵌套带ID元素: {len(objects)}'
                  + (f' (重复ID: {len(dup)})' if dup else ''))
            cnt = Counter(o.tag for o in top.values())
            print('  顶层对象类型分布:')
            for tag, c in cnt.most_common():
                print(f'    {tag:<30} {c}')
            broken_3d, dangling, _ = check_broken(content, top)
            print(f'  断裂 threeDRep(TuneTbm-): {len(broken_3d)}')
            print(f'  悬空 <uses>: {len(dangling)}')
        if EMS_STATE in names:
            data = read_zip_entry(z, EMS_STATE)
            enc = detect_encoding(data)
            content = decode_xml(data)
            print(f'\n[{EMS_STATE}]')
            print(f'  编码: {enc}  解压大小: {len(data):,} bytes')
            objects, dup = find_objects(content)
            top = {eid: o for eid, o in objects.items() if o.top_level}
            print(f'  对象数(顶层): {len(top)}  含嵌套带ID元素: {len(objects)}'
                  + (f' (重复ID: {len(dup)})' if dup else ''))
            broken_3d, dangling, _ = check_broken(content, top)
            print(f'  断裂 threeDRep(TuneTbm-): {len(broken_3d)}')
            print(f'  悬空 <uses>: {len(dangling)}')


def cmd_objects(args):
    with zipfile.ZipFile(args.file, 'r') as z:
        content = decode_xml(read_zip_entry(z, PS_STATE))
    objects, dup = find_objects(content)
    if args.all:
        items = list(objects.values())
    else:
        items = [o for o in objects.values() if o.top_level]
    if args.type:
        items = [o for o in items if o.tag == args.type]
    items.sort(key=lambda o: (o.tag, o.name or o.ext_id))
    if args.limit:
        items = items[:args.limit]
    cnt = Counter(o.tag for o in items)
    print(f'匹配对象: {len(items)}')
    for tag, c in cnt.most_common():
        print(f'  {tag:<30} {c}')
    print()
    for o in items:
        print(f'<{o.tag}> id={o.ext_id}  name={o.name or "-"}')
    if dup:
        print(f'\n警告: {len(dup)} 个重复 ID（已取后者覆盖）: {dup[:10]}')


def cmd_search(args):
    with zipfile.ZipFile(args.file, 'r') as z:
        content = decode_xml(read_zip_entry(z, PS_STATE))
    objects, _ = find_objects(content)
    kw = args.keyword.lower()
    hits = []
    for ext_id, o in objects.items():
        if kw in ext_id.lower() or (o.name and kw in o.name.lower()):
            hits.append(o)
    hits.sort(key=lambda o: (o.tag, o.name or ''))
    print(f'搜索 "{args.keyword}": {len(hits)} 个结果')
    for o in hits:
        print(f'<{o.tag}> id={o.ext_id}  name={o.name or "-"}')


def cmd_show(args):
    with zipfile.ZipFile(args.file, 'r') as z:
        content = decode_xml(read_zip_entry(z, PS_STATE))
    objects, _ = find_objects(content)
    o = objects.get(args.id)
    if not o:
        print(f'未找到对象 ID: {args.id}')
        return 1
    seg = content[o.start:o.end]
    print(f'<{o.tag}> id={o.ext_id} name={o.name or "-"} (chars {o.start}-{o.end}, len={len(seg)})')
    print('=' * 60)
    print(seg)


def cmd_check(args):
    with zipfile.ZipFile(args.file, 'r') as z:
        names = z.namelist()
        contents = {}
        for entry in (PS_STATE, EMS_STATE):
            if entry in names:
                contents[entry] = decode_xml(read_zip_entry(z, entry))

    print(f'=== 引用与合规检查: {args.file} ===')

    # [1] XML 合规性审计
    violations, xml_stats = check_xml_validity_entries(contents)
    for entry in (PS_STATE, EMS_STATE):
        if entry in contents:
            tag = 'PS' if entry == PS_STATE else 'EMS'
            n = xml_stats.get(entry, {}).get('violations', 0)
            print(f'\n[1] XML 合规性 ({tag}): {"通过 ✓" if n == 0 else f"{n} 个违规"}')
            if n > 0:
                for v in (vi for vi in violations if vi['entry'] == entry):
                    print(f'  行 {v["line"]}, 列 {v["column"]}: {v["context"]}')

    # [2] 断裂引用
    per_entry_objects = {}
    all_objects = {}
    for entry, text in contents.items():
        objs, _ = find_objects(text, entry=entry)
        per_entry_objects[entry] = objs
        all_objects.update(objs)
    broken_3d, dangling, entry_stats = check_broken_entries(contents, per_entry_objects, all_objects)

    print(f'\n[2] threeDRep 指向 TuneTbm- (断开外部引用, zip 内无资源): {len(broken_3d)}')
    for ext_id, tag, name, tbm, entry in broken_3d:
        etag = 'PS' if entry == PS_STATE else 'EMS'
        print(f'  [{etag}] <{tag}> id={ext_id}  name={name or "-"}  -> {tbm}')

    print(f'\n[3] 悬空 <uses> (引用的对象不存在): {len(dangling)}')
    for uid, pos in dangling[:50]:
        # 定位所属条目和父 Usage 元素
        for entry, text in contents.items():
            if pos <= len(text):
                back = text[:pos]
                m = re.finditer(r'<((?:Pm\w*)?Usage)\b[^>]*>', back)
                parent = None
                for pm in m:
                    parent = pm.group(1)
                etag = 'PS' if entry == PS_STATE else 'EMS'
                print(f'  [{etag}] uses={uid}  父元素≈{parent or "?"}')
                break

    print(f'\n[4] 对象总数: {len(all_objects)}')


def cmd_delete(args):
    """删除单个对象（含引用它的 Pm*Usage，可选）。"""
    with zipfile.ZipFile(args.file, 'r') as z:
        content = decode_xml(read_zip_entry(z, PS_STATE))
    objects, _ = find_objects(content)
    o = objects.get(args.id)
    if not o:
        print(f'未找到对象 ID: {args.id}')
        return 1

    if args.dry_run:
        print(f'[dry-run] 将删除对象 <{o.tag}> id={o.ext_id} name={o.name or "-"}')
        # 统计会受影响的 usage
        dangling_targets = find_usage_elements_containing(content, [o.ext_id])
        print(f'[dry-run] 关联将删除的 Pm*Usage 数: {len(dangling_targets)}')
        for t, s, e in dangling_targets:
            print(f'    {t} @ {s}')
        return 0

    # 备份
    if args.backup and not args.out:
        bak = args.file + '.bak'
        shutil.copy2(args.file, bak)
        print(f'备份: {bak}')

    # 1) 删除对象
    content = delete_object(content, o)

    # 2) 清理引用该对象的 usage（默认启用）
    if args.clean:
        removed = 0
        while True:
            targets = find_usage_elements_containing(content, [o.ext_id])
            if not targets:
                break
            content = delete_many(content, [(s, e) for _, s, e in targets])
            removed += len(targets)
        print(f'清理引用 Usage: {removed} 个')

    # 3) 打包
    enc = detect_encoding(content.encode('utf-8'))
    if enc == 'gbk':
        # 保持原编码：用原字节解码出的文本再编码回 gbk
        with zipfile.ZipFile(args.file, 'r') as z:
            raw = read_zip_entry(z, PS_STATE)
        src_enc = detect_encoding(raw)
        xml_bytes = encode_xml(content, src_enc)
    else:
        xml_bytes = content.encode('utf-8')

    out = rebuild_zip(args.file, PS_STATE, xml_bytes, out_psz=args.out, backup=False)
    print(f'完成: {out}')


def cmd_delete_broken(args):
    """批量删除所有 threeDRep=TuneTbm- 的对象及其 usage。"""
    with zipfile.ZipFile(args.file, 'r') as z:
        raw = read_zip_entry(z, PS_STATE)
        src_enc = detect_encoding(raw)
        content = decode_xml(raw)
    objects, _ = find_objects(content)
    broken_3d, dangling, _ = check_broken(content, objects)
    broken_ids = [ext_id for ext_id, *_ in broken_3d]

    print(f'断裂对象: {len(broken_ids)} 个')
    if not broken_ids:
        print('没有需要清理的对象。')
        return 0

    if args.dry_run:
        for ext_id, tag, name, tbm in broken_3d:
            print(f'  <{tag}> id={ext_id}  name={name or "-"}')
        # 统计受影响的 usage
        d_targets = find_usage_elements_containing(content, broken_ids)
        print(f'\n将删除对象 {len(broken_ids)} 个, 关联 Pm*Usage {len(d_targets)} 个')
        return 0

    if args.backup and not args.out:
        bak = args.file + '.bak'
        shutil.copy2(args.file, bak)
        print(f'备份: {bak}')

    # 1) 删除对象（倒序，避免位置失效）
    obj_spans = []
    for ext_id in broken_ids:
        o = objects[ext_id]
        obj_spans.append((o.start, o.end))
    content = delete_many(content, obj_spans)

    # 2) 清理 usage：反复扫描直到无悬空（每次基于最新内容）
    removed = 0
    while True:
        targets = find_usage_elements_containing(content, broken_ids)
        if not targets:
            break
        content = delete_many(content, [(s, e) for _, s, e in targets])
        removed += len(targets)
    print(f'删除对象: {len(broken_ids)}, 清理 Usage: {removed}')

    xml_bytes = encode_xml(content, src_enc)
    out = rebuild_zip(args.file, PS_STATE, xml_bytes, out_psz=args.out, backup=False)
    print(f'完成: {out}')
    # 最终校验
    with zipfile.ZipFile(out, 'r') as z:
        v = decode_xml(read_zip_entry(z, PS_STATE))
    objs2, _ = find_objects(v)
    b3, dg, _ = check_broken(v, objs2)
    print(f'校验: 剩余对象 {len(objs2)}, 断裂 {len(b3)}, 悬空 uses {len(dg)}')


def cmd_extract(args):
    with zipfile.ZipFile(args.file, 'r') as z:
        names = z.namelist()
        target = args.xml or PS_STATE
        if target not in names:
            print(f'zip 中不存在: {target}')
            return 1
        data = read_zip_entry(z, target)
        enc = detect_encoding(data)
        out_dir = args.out or os.path.dirname(os.path.abspath(args.file))
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, os.path.basename(target))
        # 写为 UTF-8 便于通用编辑（带声明）
        text = decode_xml(data)
        with open(out_path, 'w', encoding='utf-8', newline='') as f:
            f.write(text)
        print(f'提取: {out_path}  (原始编码 {enc}, 已转 UTF-8 保存)')


def cmd_rebuild(args):
    """把本地修改过的 XML 打回 psz（自动转回原条目编码）。"""
    if not args.xml:
        print('需要 --xml <path> 指定修改后的 XML 文件')
        return 1
    target = args.psstate if args.psstate else PS_STATE
    with zipfile.ZipFile(args.file, 'r') as z:
        names = z.namelist()
        if target not in names:
            print(f'zip 中不存在 {target}, 无法定位替换')
            return 1
        raw_old = read_zip_entry(z, target)
        src_enc = detect_encoding(raw_old)

    with open(args.xml, 'rb') as f:
        new_bytes = f.read()
    new_enc = detect_encoding(new_bytes)

    if new_enc != src_enc:
        # 外部编辑常存为 UTF-8；此处统一转回 zip 条目原始编码，避免与
        # XML 声明(如 gb2312)不一致导致 PS 读取乱码
        text = new_bytes.decode(new_enc)
        new_bytes = encode_xml(text, src_enc)
        print(f'编码转换: {new_enc} -> {src_enc}')

    out = rebuild_zip(args.file, target, new_bytes, out_psz=args.out)
    print(f'完成: {out}')


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def find_usage_elements_containing(content: str, ids):
    """
    找出内容中包含 <uses>id</uses> 的 Pm*Usage 元素范围。
    返回 [(tag, start, end), ...]
    """
    targets = []
    usage_re = re.compile(r'<((?:Pm\w*)?Usage)\b[^>]*>.*?</\1>', re.DOTALL)
    for m in usage_re.finditer(content):
        seg = m.group(0)
        uses = re.search(r'<uses>\s*([^<]+?)\s*</uses>', seg)
        if uses and uses.group(1).strip() in ids:
            targets.append((m.group(1), m.start(), m.end()))
    return targets


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description='PSZ (.psz) 文件解析与编辑工具 - Process Simulate 工程文件',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    ap.add_argument('file', help='.psz 文件路径')
    sub = ap.add_subparsers(dest='command', required=True)

    sub.add_parser('list', help='列出包内文件')
    sub.add_parser('info', help='概要信息(编码/对象统计/断裂引用)')

    p = sub.add_parser('objects', help='列出对象')
    p.add_argument('--type', help='按类型过滤, 如 PmContinuousMfg')
    p.add_argument('--limit', type=int, default=None)
    p.add_argument('--all', action='store_true', help='含嵌套带ID元素(默认仅顶层对象)')

    p = sub.add_parser('search', help='按 ID/名称搜索')
    p.add_argument('keyword')

    p = sub.add_parser('show', help='显示对象完整 XML')
    p.add_argument('id')

    sub.add_parser('check', help='检查断裂引用')

    p = sub.add_parser('delete', help='删除对象(自动清理引用)')
    p.add_argument('id')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--no-clean', dest='clean', action='store_false', default=True,
                   help='不清理引用它的 Pm*Usage')
    p.add_argument('--no-backup', dest='backup', action='store_false', default=True)
    p.add_argument('--out', help='输出到指定路径(不改原文件)')

    p = sub.add_parser('delete-broken', help='批量删除 threeDRep=TuneTbm- 的断裂对象')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--no-backup', dest='backup', action='store_false', default=True)
    p.add_argument('--out', help='输出到指定路径(不改原文件)')

    p = sub.add_parser('extract', help='提取 XML(转 UTF-8)')
    p.add_argument('--xml', default=None, help='指定条目名, 默认 PsState')
    p.add_argument('--out', default=None, help='输出目录')

    p = sub.add_parser('rebuild', help='把修改后的 XML 打回 psz')
    p.add_argument('--xml', help='修改后的 XML 文件路径')
    p.add_argument('--out', help='输出到指定路径')
    p.add_argument('--psstate', default=PS_STATE, help='要替换的条目名')

    args = ap.parse_args()
    cmd = args.command

    if cmd == 'list':
        cmd_list(args)
    elif cmd == 'info':
        cmd_info(args)
    elif cmd == 'objects':
        cmd_objects(args)
    elif cmd == 'search':
        cmd_search(args)
    elif cmd == 'show':
        cmd_show(args)
    elif cmd == 'check':
        cmd_check(args)
    elif cmd == 'delete':
        cmd_delete(args)
    elif cmd == 'delete-broken':
        cmd_delete_broken(args)
    elif cmd == 'extract':
        cmd_extract(args)
    elif cmd == 'rebuild':
        cmd_rebuild(args)
    else:
        ap.print_help()


if __name__ == '__main__':
    sys.exit(main())
