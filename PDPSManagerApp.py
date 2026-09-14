2# -*- coding: utf-8 -*-
"""Tecnomatix PDPS 工具箱启动器。

将本文件所在目录加入模块搜索路径，
以便正确解析 pdps_manager 包及同级的 ExcelTools 包。
"""

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from pdps_manager.main import main

if __name__ == "__main__":
    sys.exit(main())
