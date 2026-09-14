# -*- coding: utf-8 -*-
"""对象 XML → 对照表(KV) 解析器（生产版）。

设计参考 core/demo_obj_table.py 与《对象详情对照表_GUI方案.md》：
  - 开标签属性统一放表头 "@属性" 区（@ 前缀，路径以 @ 开头）；
  - 子元素全部平铺，路径用 / 连接，同名元素追加 [i] 索引；
  - 每行含 section / group / path / key / value / is_id；
  - 自动剥离 XML 命名空间，避免标签名出现 {namespace} 前缀。

适用: PSZ 内 PmLayout / ArcContinuousMfg / PmContinuousMfgUsage 等对象段。
"""

import re
import xml.etree.ElementTree as ET


def parse_obj(xml_text):
    """把对象段 XML 解析为嵌套 dict（已剥离命名空间）。解析失败抛 ET.ParseError。"""
    root = ET.fromstring(xml_text)
    return _node_to_dict(root)


def _local(name):
    """剥离 XML 命名空间前缀：{ns}Tag -> Tag。"""
    return name.rsplit('}', 1)[-1] if '}' in name else name


def _node_to_dict(node):
    tag = _local(node.tag)
    attrs = {_local(k): v for k, v in node.attrib.items()}
    children = [_node_to_dict(ch) for ch in node]
    text = (node.text or '').strip()
    if text and not children:
        return {'tag': tag, 'attrs': attrs, 'value': text, 'children': []}
    if text:
        children.append({'tag': '#text', 'attrs': {}, 'value': text, 'children': []})
    return {'tag': tag, 'attrs': attrs, 'value': '', 'children': children}


def to_kv_table(d):
    """把嵌套 dict 展平为行列表，供 GUI 对照表直接消费。"""
    rows = []
    section = d['tag']
    for k, v in d['attrs'].items():
        rows.append({'section': section, 'group': 'attr',
                     'path': '@' + k, 'key': '@' + k, 'value': v,
                     'is_id': _looks_like_id(v)})
    _flatten(d['children'], '', section, rows)
    return rows


def _flatten(children, parent_path, section, rows):
    groups = {}
    for ch in children:
        if ch['tag'] == '#text':
            continue
        groups.setdefault(ch['tag'], []).append(ch)
    for tag, items in groups.items():
        indexed = len(items) > 1
        for i, item in enumerate(items):
            seg = f'{tag}[{i}]' if indexed else tag
            cur_path = f'{parent_path}/{seg}' if parent_path else seg
            # 开标签属性
            for k, v in item['attrs'].items():
                rows.append({'section': section, 'group': 'attr',
                             'path': f'{cur_path}/@{k}',
                             'key': f'{cur_path}.@{k}', 'value': v,
                             'is_id': _looks_like_id(v)})
            # 叶子 / 继续嵌套
            if not item['children'] and not item['attrs']:
                rows.append({'section': section,
                             'group': 'list-item' if indexed else 'leaf',
                             'path': cur_path, 'key': cur_path,
                             'value': item['value'],
                             'is_id': _looks_like_id(item['value'])})
            elif item['children']:
                _flatten(item['children'], cur_path, section, rows)


def _looks_like_id(value):
    """GUID / TuneTbm-… / PP-… 视为 ID 标识，GUI 可着色或跳转。"""
    if not isinstance(value, str):
        return False
    return bool(re.match(
        r'^(\{?[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-'
        r'[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}?'
        r'|TuneTbm-[0-9A-Fa-f-]+'
        r'|PP-[0-9A-Za-z-]+)$', value))


def to_ascii(d, max_val=60):
    """ASCII 预览（调试 / 只读文本框直接使用）。"""
    rows = to_kv_table(d)
    lines = [f'<{d["tag"]}>']
    last_section = None
    for r in rows:
        if r['section'] != last_section:
            last_section = r['section']
            lines.append('── 属性 ──' if r['group'] == 'attr' and last_section == d['tag']
                         else '── 内容 ──')
        val = str(r['value'])
        if len(val) > max_val:
            val = val[:max_val - 3] + '...'
        mark = {'attr': '⚙', 'leaf': ' ', 'list-item': '•', 'nested': '↳'}.get(r['group'], ' ')
        lines.append(f'  {mark} {r["path"]:<36} = {val}')
    lines.append(f'</{d["tag"]}>')
    return '\n'.join(lines)
