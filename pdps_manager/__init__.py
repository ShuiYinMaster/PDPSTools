# -*- coding: utf-8 -*-
"""PDPSManagerApp - Tecnomatix PDPS 工具箱（PyQt5）。

按功能拆分的包结构：
  config            全局常量、路径、pycatia 可选导入
  catia_utils       CATIA 连接公共逻辑
  core/             业务逻辑（焊点提取 / 重心扫描 / 焊枪放置）
  ui/               界面（页面、控件、样式、主窗口）
  workers/          后台线程（深度扫描等耗时任务）
"""

__version__ = "2.0.0"
