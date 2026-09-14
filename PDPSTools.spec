# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build definition for the Windows desktop application."""

hiddenimports = [
    "pycatia",
    "pycatia.mec_mod_interfaces.part_document",
    "win32com.client",
    "pythoncom",
    "pywintypes",
]


analysis = Analysis(
    ["PDPSManagerApp.py"],
    pathex=["."],
    binaries=[],
    datas=[("logo.png", ".")],
    hiddenimports=hiddenimports,
    hooksconfig={},
    runtime_hooks=[],
    # 这些是当前 Python 环境中的无关重型库，不能因为 PyInstaller
    # 的可选依赖探测而被带入桌面工具。
    excludes=[
        "torch", "matplotlib", "scipy", "sklearn", "tensorflow",
        "cv2", "pytest", "IPython", "notebook",
    ],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="PDPSTools",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon="logo.ico",
)
