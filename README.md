# PDPSTools

PDPS 单机版项目工具集，当前包含：

- 项目路径管理与 PSZ 解析
- CATIA 焊点/BiW 点处理
- 3DXML 与 CATIA 几何体重心解析
- 插枪工具
- Excel 工具

## 本地运行

```powershell
python -m pip install -r requirements.txt
python PDPSManagerApp.py
```

CATIA 相关功能需要在 Windows 上安装 CATIA V5，并确保 Python 环境可使用 `pycatia`/`pywin32`。

## 打包 EXE

```powershell
python -m PyInstaller --noconfirm --clean PDPSTools.spec
```

生成文件位于 `dist/PDPSTools.exe`。用户配置会保存到 EXE 所在目录，图标等资源会随 EXE 一起打包。
