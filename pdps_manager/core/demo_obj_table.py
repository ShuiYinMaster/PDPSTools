"""
对象 XML → 对照表 解析器 (改进版)
解析对象段 <Tag ...>...</Tag>，输出适合 GUI 表格的扁平结构。

设计原则:
  - 开标签属性统一放在表头 "@属性" 区, 路径前缀 @, 如 @ExternalId
  - 子元素全部平铺, 路径用 / 连接, 同名元素追加 [i] 索引
  - 每个 row 含: path / key / value / kind / section / is_id
  - section 按最外层标签分组, GUI 可按 section 折叠

适用: PSZ 内的 PmLayout / ArcContinuousMfg / PmContinuousMfgUsage 等对象
"""
import sys, zipfile, re, json
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import xml.etree.ElementTree as ET


def parse_obj(xml_text):
    root = ET.fromstring(xml_text)
    return _node_to_dict(root)


def _node_to_dict(node):
    tag = node.tag
    attrs = dict(node.attrib)
    children = [_node_to_dict(ch) for ch in node]
    text = (node.text or '').strip()
    if text and not children:
        return {'tag': tag, 'attrs': attrs, 'value': text, 'children': []}
    if text:
        children.append({'tag': '#text', 'attrs': {}, 'value': text, 'children': []})
    return {'tag': tag, 'attrs': attrs, 'value': '', 'children': children}


def to_kv_table(d):
    """
    返回行列表。每行:
      {
        'section':   分组(顶层 tag 名, 如 'PmLayout'),
        'group':     'attr' | 'leaf' | 'nested' | 'list-item',
        'path':      完整路径, 如 'NodeInfo/absoluteLocation/x' 或 '@ExternalId',
        'key':       显示在表格第一列的键,
        'value':     显示在第二列的值,
        'is_id':     True 表示看起来像 GUID / TuneTbm-/PP- 前缀 ID,
      }
    排序: 顶层属性在最前, 其余按出现顺序
    """
    rows = []
    section = d['tag']
    for k, v in d['attrs'].items():
        rows.append({
            'section': section, 'group': 'attr',
            'path': '@' + k, 'key': '@' + k, 'value': v,
            'is_id': _looks_like_id(v),
        })
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
            cur_key = cur_path  # 在 GUI 表格里 key 即带完整路径

            # 开标签属性
            for k, v in item['attrs'].items():
                rows.append({
                    'section': section, 'group': 'attr',
                    'path': f'{cur_path}/@{k}', 'key': f'{cur_key}.@{k}', 'value': v,
                    'is_id': _looks_like_id(v),
                })

            # 叶子
            if not item['children'] and not item['attrs']:
                rows.append({
                    'section': section,
                    'group': 'list-item' if indexed else 'leaf',
                    'path': cur_path, 'key': cur_key, 'value': item['value'],
                    'is_id': _looks_like_id(item['value']),
                })
            elif not item['children']:
                pass
            else:
                _flatten(item['children'], cur_path, section, rows)


def _looks_like_id(value):
    if not isinstance(value, str):
        return False
    return bool(re.match(
        r'^(\{?[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-'
        r'[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}?'
        r'|TuneTbm-[0-9A-Fa-f-]+'
        r'|PP-[0-9A-Za-z-]+)$', value))


def to_ascii(d, max_val=60):
    """ASCII 预览（可直接贴到 GUI 只读文本框）"""
    rows = to_kv_table(d)
    lines = [f'<{d["tag"]}>']
    last_section = None
    for r in rows:
        if r['section'] != last_section:
            last_section = r['section']
            if r['group'] == 'attr' and last_section == d['tag']:
                lines.append('── 属性 ──')
            else:
                lines.append(f'── 内容 ──')
        val = str(r['value'])
        if len(val) > max_val:
            val = val[:max_val-3] + '...'
        mark = {'attr': '⚙', 'leaf': ' ', 'list-item': '•', 'nested': '↳'}.get(r['group'], ' ')
        lines.append(f'  {mark} {r["path"]:<36} = {val}')
    lines.append(f'</{d["tag"]}>')
    return '\n'.join(lines)


def grab(zip_path, regex):
    with zipfile.ZipFile(zip_path, 'r') as z:
        text = z.read('StandaloneStudy_PsState.xml').decode('utf-8')
    m = re.search(regex, text, re.DOTALL)
    return text[m.start():m.end()]


if __name__ == '__main__':
    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding='utf-8') as f:
            xml_text = f.read()
        label = sys.argv[1]
    else:
        zip_path = r'C:/Users/A1875/WorkBuddy/2026-07-24-15-29-30/T18-20240218.psz.backup'
        xml_text = grab(zip_path,
                        r'<PmLayout\s+[^>]*ExternalId="([^"]+)"[^>]*>.*?</PmLayout>')
        label = 'PmLayout (典型)'

    obj = parse_obj(xml_text)
    rows = to_kv_table(obj)

    print(f'=== {label} ===')
    print(f'XML 长度: {len(xml_text):,} 字符')
    print(f'对照表行数: {len(rows)}')
    print()
    print('─ ASCII 预览 ─')
    print(to_ascii(obj, max_val=50))
    print()
    print('─ 表格数据示例 (前 10 行) ─')
    print(f'{"section":<12} {"group":<12} {"path":<35} {"value":<30}')
    print('-' * 90)
    for r in rows[:10]:
        v = str(r['value'])[:28]
        print(f'{r["section"]:<12} {r["group"]:<12} {r["path"]:<35} {v}')
    if len(rows) > 10:
        print(f'... ({len(rows)-10} more)')