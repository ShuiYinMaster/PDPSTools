# 对象详情面板 · 对照表方案（GUI 改造）

> 目标：把当前右侧"对象详情 (XML)"面板里挤成一坨的原始 XML 文本，改成可读的对照表。
> 状态：历史设计参考；生产实现位于 `pdps_manager/core/obj_kv_table.py`，界面接入位于 `pdps_manager/ui/psz_parser_page.py`。
> 数据接口：核心输出是 `to_kv_table(obj)` 返回的扁平行列表；以下方案示例不代表全部已经实现。
> 推荐实现：方案 A（分组 KV 表）。

---

## 一、设计思路

XML 是嵌套的标签树，但**用户查看对象时关心的是"字段 + 值"**，不在乎标签层级。所以做两层映射：

```
原始: <PmLayout ExternalId="..."><location><item>0</item>...</location></PmLayout>
                ↓ ElementTree 解析为内存树
扁平: [{path:'@ExternalId', value:'...'}, {path:'location/item[0]', value:'0'}, ...]
                ↓ 按 path 渲染
表格: section | path | value
```

要点：
- **属性（开标签 attrs）** 平铺到表头，用 `@` 前缀区别于元素路径；
- **同名子元素**（如 `<item>` 多次出现）自动加 `[i]` 索引，避免 key 冲突；
- **嵌套子元素** 用 `/` 连接路径（如 `NodeInfo/absoluteLocation/x`）；
- **ID 自动识别**：GUID / `TuneTbm-...` / `PP-...` 标记 `is_id=True`，GUI 可着色或加右键跳转。

---

## 二、数据结构（GUI 表格控件直接消费）

```python
rows = to_kv_table(parsed_obj)
# rows: List[Dict]，每行 6 字段
[
  {
    'section': 'PmLayout',          # 顶层对象标签名，可作为表分组依据
    'group':   'attr',              # attr / leaf / list-item 三类
    'path':    '@ExternalId',       # 完整路径（@-开头表示属性）
    'key':     '@ExternalId',       # 表格第一列显示文本
    'value':   '190215E1-...',       # 表格第二列
    'is_id':   True,                # 是否 ID（着色/跳转提示）
  },
  {
    'section': 'PmLayout',
    'group':   'list-item',
    'path':    'location/item[0]',
    'key':     'location/item[0]',
    'value':   '0',
    'is_id':   False,
  },
  ...
]
```

字段语义：

| 字段 | 含义 | GUI 用法 |
|---|---|---|
| `section` | 顶层对象标签 | 分组折叠头（TreeView 节点 / DataGrid Group / Card 标题） |
| `group` | 行类型 | 决定行样式（属性行加 ⚙ 图标，列表项加 • 等） |
| `path` | 完整路径 | 排序、唯一标识 |
| `key` | 显示文本 | 第一列内容 |
| `value` | 值 | 第二列内容；超长时悬浮提示完整值 |
| `is_id` | 是否 ID 标识 | 着色（蓝色）+ 右键"跳转到该对象" |

---

## 三、三种渲染样式

### 样式 1：分组 KV 表（推荐用于 DataGridView / TreeView）

```
[ PmLayout ]  ─── 24 个字段 ──────────────────────────────────────
  ⚙ @ExternalId              = 190215E1-8F7B-4E68-861F-0202E60F15E6
  • location/item[0]         = 0
  • location/item[1]         = 0
  • location/item[2]         = 0
  • rotation/item[0]         = 0
  • rotation/item[1]         = 0
  • rotation/item[2]         = 0
    children                 = （空）
    parent                   = NULL
    node                     = 46906BF0-7C99-4809-AE3C-B18B53AC27CF
    NodeInfo/family          = PmLayout
    NodeInfo/absoluteLocation/x = 0
    NodeInfo/absoluteLocation/y = 0
    ...
    mountedTo                = 29B5138E-5F96-4E85-928E-8CC3073312CC
  • mountLocation/item[0]    = 2.27e-12
  • mountLocation/item[1]    = 1.82e-12
  • mountLocation/item[2]    = 1.34e-11
  • mountRotation/item[0]    = 0
  ...
```

- 适合：WPF DataGrid（两列：path, value）、WinForms ListView、Qt QTableView
- 实现：把 `rows` 绑定到数据源，section 作为 GroupDescription 即可

### 样式 2：嵌套树（适合 TreeView）

把 XML 节点直接渲染为树形控件：

```
▼ PmLayout   (ExternalId: 190215E1-...)
  ▼ 属性
      ExternalId: 190215E1-8F7B-4E68-861F-0202E60F15E6
  ▼ location
      • item[0]: 0
      • item[1]: 0
      • item[2]: 0
  ▼ rotation
      • item[0]: 0
      • item[1]: 0
      • item[2]: 0
    children: （空）
    parent: NULL
    node: 46906BF0-7C99-4809-AE3C-B18B53AC27CF
  ▼ NodeInfo
      family: PmLayout
      ▼ absoluteLocation
          x: 0
          y: 0
          ...
  ...
```

- 适合：WPF TreeView、Qt QTreeView、HTML `<details>`
- 数据源：用 `_node_to_dict` 返回的嵌套 dict 递归构建

### 样式 3：ASCII 文本（适合只读预览框）

直接由 `to_ascii(obj)` 生成，前面已有示例。适合日志、悬浮提示或无表格控件的简单 GUI。

---

## 四、复杂对象示例（PmContinuousMfgUsage，含 simulationInfo）

257 行（嵌套深），按样式 1 渲染片段：

```
[ PmContinuousMfgUsage ]  ─── 257 个字段 ──────────────────────
  ⚙ @ExternalId                        = TuneTbm-83C4DFAF-DA6A-405C-92C7-A2C140A152BF
    NodeInfo/family                    = MfgUsage
    uses                               = E3657790-D57F-49DE-8FD6-A612AB24961B
    continuousUsedBy                   = E54941DD-5DE9-4638-9A03-6161C09E36AE
  ⚙ @simulationInfo/LaserSeamOperation.@ExternalId = 8B399523-3AB1-457C-A6E3-43ADBE513765
    simulationInfo/LaserSeamOperation/NodeInfo/family = Operation
    simulationInfo/LaserSeamOperation/simulationInfo/@userXmlData = true
    simulationInfo/LaserSeamOperation/simulationInfo/SimulationInfoVersion = 53
    simulationInfo/LaserSeamOperation/simulationInfo/type = 64
    simulationInfo/LaserSeamOperation/simulationInfo/simulationParameter[0]/simulationParamId = ARC_PROJECTION_PARAM_SIDE_WALL_OBJECT
    simulationInfo/LaserSeamOperation/simulationInfo/simulationParameter[0]/simulationParamType = 7
    ...
```

特征：
- **路径会变长**（嵌套深），路径列宽要自适应或加水平滚动
- **属性会重复出现**（多个 `@`，因为每层都有 attrs），用前缀区分
- 实际可考虑**折叠** simulationInfo 这种"重型"嵌套块（折叠点可加在 path 含 `simulationInfo/` 处）

---

## 五、推荐控件映射

| GUI 框架 | 推荐控件 | 适配说明 |
|---|---|---|
| WPF | `DataGrid` 两列 + `CollectionViewSource` | section 做 GroupDescription；path 列宽 35% 字符 |
| WinForms | `ListView` Details 模式 | 添加 `.ListViewItemSorter` 按 path 排序 |
| Qt | `QTableView` + 自定义 model | section 分组用 QStandardItem.setData(Qt.UserRole) |
| Web | `<table>` + CSS `[data-group="attr"]` 高亮 | 路径列加 `font-family: monospace` |

---

## 六、视觉建议

- **属性行**（`group=attr`）：等宽字体 + 浅灰背景 + `⚙` 图标
- **叶子行**（`group=leaf`）：等宽字体
- **列表项**（`group=list-item`，即同名元素）：前缀 `•`，淡灰
- **ID 值**（`is_id=True`）：蓝色字体 + 下划线悬停 + 右键菜单"跳转到该对象" / "在表格中搜索"
- **超长值**：表格列宽限制 + 悬浮 tooltip 显示完整值 + 末尾 `...` 标记
- **重复 `<item>`**（路径带 `[i]`）：背景交替着色便于横向扫视
- **空值**（如 `<children/>`）：显示 `(空)` 而不是空白，避免"这是 0 还是空"的歧义

---

## 七、性能与实现注意

1. **解析用 ElementTree 即可**——单个对象段最长也就几 KB（即使 PmContinuousMfgUsage 含 257 行也只有 ~200KB 文本），完全无压力。
2. **缓存解析结果**：GUI 选中对象时若频繁刷新，把解析后的 dict 缓存到对象 ID 的 dict 里，避免重复解析。
3. **大表格虚拟化**：超过 500 行的对象（罕见，但 PmContinuousMfgUsage 接近）建议用虚拟列表（DataGrid EnableRowVirtualization、QTableView 优化等）。
4. **不要在 UI 线程解析**——简单对象无需 Worker，但若未来要支持批量对比，可在后台 Task 解析。
5. **路径含 `/` 时**：表格排序按 `/` 分段比较，比纯字符串排序更直观（`location` 排在 `location/item` 之前）。可以用 split 后按段比较。

---

## 八、参考代码（Python，可翻译到任意语言）

`pdps_manager/core/obj_kv_table.py` 里的核心函数：

```python
def to_kv_table(d):
    """d = parse_obj(obj_xml_text) 返回的嵌套 dict"""
    rows = []
    # 顶层属性
    for k, v in d['attrs'].items():
        rows.append(_row('attr', '@' + k, '@' + k, v, d['tag']))
    # 递归子元素
    _flatten(d['children'], '', d['tag'], rows)
    return rows

def _flatten(children, parent_path, section, rows):
    groups = {}
    for ch in children:
        if ch['tag'] == '#text': continue
        groups.setdefault(ch['tag'], []).append(ch)
    for tag, items in groups.items():
        indexed = len(items) > 1
        for i, item in enumerate(items):
            seg = f'{tag}[{i}]' if indexed else tag
            path = f'{parent_path}/{seg}' if parent_path else seg
            for k, v in item['attrs'].items():
                rows.append(_row('attr', f'{path}/@{k}', f'{path}.@{k}', v, section))
            if not item['children'] and not item['attrs']:
                rows.append(_row('list-item' if indexed else 'leaf',
                                 path, path, item['value'], section))
            elif item['children']:
                _flatten(item['children'], path, section, rows)
```

生产解析器与 ASCII 预览函数见 `pdps_manager/core/obj_kv_table.py`。

---

*生成日期：2026-08-11，基于 T18-20240218.psz（UTF-8）的真实 PmLayout / PmContinuousMfgUsage / ArcContinuousMfg 对象数据。*
