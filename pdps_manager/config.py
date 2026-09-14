# -*- coding: utf-8 -*-
"""全局常量、路径与可选依赖。

所有需要落盘的文件（数据库、配置）统一相对 BASE_DIR 定位，
避免原代码使用相对路径、随当前工作目录漂移导致找不到/写错位置。
"""

import os
import sys

# ── 目录定位 ──────────────────────────────────────────────────────────────
PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
# 项目根目录：与包同级（也是启动脚本与 ExcelTools/ 所在目录）
BASE_DIR = os.path.dirname(PACKAGE_DIR)

# PyInstaller one-file 模式下，资源会解压到临时目录，但用户配置必须
# 写入 exe 所在目录，否则程序退出后临时目录会被清理，配置无法保留。
if getattr(sys, "frozen", False):
    RESOURCE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    DATA_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    RESOURCE_DIR = BASE_DIR
    DATA_DIR = BASE_DIR

# ── 持久化文件 ────────────────────────────────────────────────────────────
CONFIG_FILE = os.path.join(DATA_DIR, "pdps_master_config.json")            # 项目数据库（JSON）
LEGACY_CONFIG_XLSX = os.path.join(DATA_DIR, "pdps_master_config_v13.xlsx")  # 旧：Excel 数据库（自动迁移）
PATHS_CONFIG_FILE = os.path.join(DATA_DIR, "paths_config.json")   # 新：JSON 格式
LEGACY_PATHS_INI = os.path.join(DATA_DIR, "paths_config.ini")     # 旧：逗号分隔（向后兼容读取）
RECENT_FILE = os.path.join(DATA_DIR, "recent_projects.json")      # 最近开启的项目
UI_SETTINGS_FILE = os.path.join(DATA_DIR, "pdps_ui_settings.json")  # 界面个性化（列显示等）
CRASH_LOG = os.path.join(DATA_DIR, "crash.log")                   # 崩溃 / 未捕获异常日志
LOGO_FILE = os.path.join(RESOURCE_DIR, "logo.png")

RECENT_MAX = 12   # 最近项目最多保留条数

# ── 注册表路径 ────────────────────────────────────────────────────────────
REG_PATH = r"Software\TECNOMATIX\TUNE\NewAssembler\Options\EMS"
REG_PATH_DEFINE_COMPONENT = r"Software\TECNOMATIX\TUNE\NewAssembler\Commands Info\DnDataAdministrationCommands\Values\CCoDACDefineComponentTypeCmd"
REG_PATH_INSERT_COMPONENT = r"Software\TECNOMATIX\TUNE\NewAssembler\Commands Info\ComponentOperations\Dialogs\2005\Default"
REG_PATH_IMPORT_MFGS = r"Software\TECNOMATIX\TUNE\NewAssembler\Commands Info\DnProcessSimulateCommands\Dialogs\CUiImportMFGsFromFileDialog"
REG_VALUE_LAST_FOLDER = "LastFolder"
REG_VALUE_GET_COMPONENT_DIALOG_LAST_FOLDER = "GET_COMPONENT_DIALOG_LAST_FOLDER"
REG_VALUE_LAST_BROWSED_DIRECTORY = "LastBrowsedDirectory"

# ── pycatia 可选导入 ──────────────────────────────────────────────────────
try:
    from pycatia import catia as pycatia_catia
    from pycatia.mec_mod_interfaces.part_document import PartDocument
    PYCATIA_AVAILABLE = True
except Exception:
    pycatia_catia = None
    PartDocument = None
    PYCATIA_AVAILABLE = False


def ensure_base_on_path() -> None:
    """把项目根目录加入 sys.path，保证 `ExcelTools` 等同级包可被导入。"""
    if BASE_DIR not in sys.path:
        sys.path.insert(0, BASE_DIR)
