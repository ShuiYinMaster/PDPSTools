# PDPSTools

面向 Windows 的 PDPS / CATIA 桌面工具箱，集中处理项目目录、PSZ 工程、焊点与螺柱点位、几何中心和 Excel 数据。

## 下载与运行

当前版本：**v2.2.0（2026-10-14）**。

- [下载 Windows x64 EXE](https://github.com/ShuiYinMaster/PDPSTools/releases/download/v2.2.0/PDPSTools-v2.2.0-windows-x64.exe)
- [查看发行版说明](https://github.com/ShuiYinMaster/PDPSTools/releases/tag/v2.2.0)
- [完整更新日志](CHANGELOG.md)

将 EXE 放在可写的文件夹中，双击即可运行，无需另外安装 Python。CATIA 功能需要已安装并启动 CATIA V5。

升级时先关闭旧版，将新 EXE 放在原目录中，保留原有配置文件，即可继续使用已有项目数据。

## 功能

| 模块 | 功能 |
| --- | --- |
| 项目路径管理 | 添加目录、后台扫描、项目筛选、预览、元数据维护与启动项目 |
| PSZ 解析 | 区分 PS / EMS 对象、检查异常、按条目删除对象、事务回写与备份 |
| CATIA 点位 | 焊点 / BiW 提取、螺柱装配原点读取与创建、CSV / Excel 导入 |
| 点位检测与导出 | 三维距离去重、跨来源比对、导出全部 / 保留 / 重复点 |
| 几何中心 | 读取 3DXML、扫描 CATIA 几何体与图形集、生成中心点 |
| 插枪与 Excel | CATIA 焊枪放置及 Excel 图形工具 |

## 首次使用

1. 没有项目数据时，项目页会显示目录引导，点击“添加项目目录”。
2. 选择包含 `.psz` 文件的目录，程序会自动扫描子目录；也可以将文件夹拖入窗口。
3. 在列表中选择项目，查看详情或启动项目；点击“保存数据”，下次启动时继续使用。

已添加目录但没有项目数据时，可以从引导页重新扫描。已有项目数据时，引导自动隐藏。

## CATIA 点位工作流

### 螺柱点位

在 CATIA 中激活 CATProduct 后，打开“Catia工具箱”的“螺柱点位”区域。

默认关键字 `Q114` 可同时匹配名称或零件号中包含 `Q114`、`FQ114` 的实例。不区分大小写，也支持逗号分隔多个关键字，例如 `Q114,FQ114`；同一实例不会重复计数。

先点击“读取螺柱点位”，核对实例名称、零件号、装配路径和毫米坐标，再点击“确认并创建原点”。修改关键字后需要重新读取。跨子装配的同名实例按路径区分，生成点的名称保持唯一。

点位表示组件局部原点在活动根装配坐标系中的位置。创建前会重新核对装配数据；已有同名输出 CATPart 时停止重复创建，创建失败时尝试移除本次新建组件。

现场只读验证成功读取 32 个实例：其中 `FQ114` 6 个，其余 `Q114` 26 个，原点坐标与 CATIA 返回值一致。详情见 [螺柱点位获取验证](docs/CATIA_STUD_VALIDATION.md)。

### 导入、去重与导出

CSV / Excel 导入支持 `X/Y/Z`、`x_mm/y_mm/z_mm` 或 `center_x_mm/center_y_mm/center_z_mm` 列。`unit` 列可声明 `mm`、`cm`、`m`、`in`，坐标统一转为 mm；未声明时按 mm 处理。未提供坐标系时记录为 `unspecified`。

焊点和几何中心按三维距离标记重复点，默认阈值 5 mm。可调整阈值，或勾选“仅显示重复点”筛选焊点表；“导入时追加”可检测跨来源重复点。检测保留原始记录，导出可选择全部、保留或重复点，并附来源、坐标系、原始单位、阈值、主点和距离。

工具箱按钮随窗口宽度换行，表格列宽自动适配。长名称和路径显示省略号，悬停可查看完整值。

## 配置与数据

配置保存在 EXE 所在目录：`pdps_master_config.json`（项目数据）、`paths_config.json`（搜索目录）、`recent_projects.json`（最近项目）和 `pdps_ui_settings.json`（界面设置）。

PSZ 回写会先生成临时包、校验 XML 与 ZIP，再原子替换，并保留独立备份。程序关闭时会协调正在运行的后台任务。

## 源码运行与打包

维护基准为 **Python 3.13.3 / Windows x64**，直接依赖版本已固定。运行依赖和构建依赖分别位于 `requirements.txt`、`requirements-build.txt`。

```powershell
python -m pip install -r requirements.txt
python PDPSManagerApp.py
```

打包：

```powershell
python -m pip install -r requirements-build.txt
python -m PyInstaller --noconfirm --clean PDPSTools.spec
```

默认生成 `dist/PDPSTools.exe`，图标等资源随 EXE 打包。构建时建议使用独立输出目录；已有 `dist/` 或 `release/` 可能包含用户配置与导出数据。

回归测试：

```powershell
python -B -m unittest discover -s tests -v
```

Windows CI 会运行正式回归测试、隔离打包并执行 EXE 启动检查。本版本已通过 36 项本地回归测试、不同宽度与字号下的布局检查，以及十秒 EXE 启动检查。真实 CATIA 的点创建与失败回滚仍需按现场工程验收。

## 项目结构

- `PDPSManagerApp.py`、`PDPSTools.spec`：应用和打包入口。
- `pdps_manager/core/`：PSZ、CATIA、3DXML 与点位服务。
- `pdps_manager/ui/`、`pdps_manager/workers/`：界面和后台任务。
- `ExcelTools/`：Excel 工具。
- `tests/`：正式回归测试。
- `docs/`：审查路线、验证记录和历史设计说明。

[项目审查与开发路线图](docs/PROJECT_REVIEW.md)
