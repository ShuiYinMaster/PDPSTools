# 更新记录

## V2.1.0 - 2026-09-14

- 修复 Product 遍历提取焊点时 `PartDocument` 局部变量作用域错误。
- 兼容 Product 中多层 Part 实例遍历，恢复焊点坐标提取。
- 使用 CATIA/pycatia 的点对象进行坐标测量，避免读取到全零坐标。
- 当前 Product 回归验证：遍历 86 个 Part 实例，成功提取 418 个非零坐标焊点。
