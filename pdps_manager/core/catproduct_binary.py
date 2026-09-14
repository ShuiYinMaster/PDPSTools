# -*- coding: utf-8 -*-
"""保守读取 CATProduct 中的 BiW Joint Element 坐标。

CATIA V5 的 CATProduct 是专有二进制格式，Dassault 没有公开一个可长期依赖
的 Python/Automation 接口来读取 BiW ``Joint Element`` 的显式坐标。本模块只
处理已经确认过的 ``V5_CFV2`` 记录布局：

* ``Joint Element.XXXXX`` 名称以 ASCII 保存；
* 坐标记录以 ``FE FE 32 3C`` 开头；
* 记录中三组坐标是 little-endian IEEE-754 double，彼此之间有 ``E6`` 标记；
* 同一坐标还会以 ``3E`` 记录保存一份镜像值。

这不是通用 CATIA 文件格式解析器。所有结构和数量条件必须同时满足，否则
返回空结果，避免在 CATProduct 版本或内容不同的时候静默生成错误坐标。
"""

from __future__ import annotations

import math
import re
import struct
from typing import Dict, List, Sequence, Tuple


_JOINT_ELEMENT_RE = re.compile(rb"Joint Element\.([0-9]+)")
_MARKER = b"\xfe\xfe\x32"


class CATProductBinaryError(ValueError):
    """当前文件不满足本模块支持的 CATProduct 二进制布局。"""


class CATProductBiWBinaryReader:
    """从当前已验证布局中读取 BiW Joint Element 名称和显式坐标。"""

    MAGIC = b"V5_CFV2"
    COORD_RECORD_KIND = 0x3C
    MIRROR_RECORD_KIND = 0x3E
    SEPARATOR = 0xE6

    def __init__(self, path: str):
        self.path = path
        try:
            with open(path, "rb") as stream:
                self.data = stream.read()
        except OSError as exc:
            raise CATProductBinaryError(f"无法读取 CATProduct: {exc}") from exc

    def read(self) -> List[Dict]:
        if not self.data.startswith(self.MAGIC):
            raise CATProductBinaryError("文件头不是 V5_CFV2")

        names = self._joint_element_names()
        coordinates = self._coordinate_records()
        if not names:
            raise CATProductBinaryError("未找到 Joint Element 名称")
        if not coordinates:
            raise CATProductBinaryError("未找到受支持的 3C 坐标记录")
        if len(names) != len(coordinates):
            raise CATProductBinaryError(
                "Joint Element 数量与 3C 坐标记录数量不一致 "
                f"({len(names)} != {len(coordinates)})"
            )

        # 当前 V5_CFV2 文件的名称表和 3C 坐标记录都按 Joint Element 的内部
        # 序列排列。该顺序不是公开格式契约，因此上面的数量校验必须保留。
        return [
            self._record(name, xyz)
            for name, xyz in zip(names, coordinates)
        ]

    def _joint_element_names(self) -> List[str]:
        names: List[str] = []
        seen = set()
        for match in _JOINT_ELEMENT_RE.finditer(self.data):
            number = match.group(1).decode("ascii")
            # 00000 是属性/默认对象占位符，不是当前装配中的点。
            if number == "00000":
                continue
            name = f"Joint Element.{number}"
            if name not in seen:
                names.append(name)
                seen.add(name)
        return names

    def _coordinate_records(self) -> List[Tuple[float, float, float]]:
        records: List[Tuple[float, float, float]] = []
        data = self.data
        start = 0
        while True:
            marker = data.find(_MARKER, start)
            if marker < 0:
                break
            start = marker + 1

            # 3C 是当前文件中第一套显式坐标记录；3E 是相同坐标的镜像记录，
            # 不重复计数。坐标的固定相对位置和 E6 分隔符是防误识别条件。
            if marker + 43 >= len(data) or data[marker + 3] != self.COORD_RECORD_KIND:
                continue
            value_start = marker + 15
            if data[marker + 14] != self.SEPARATOR:
                continue
            if data[value_start + 8] != self.SEPARATOR:
                continue
            if data[value_start + 17] != self.SEPARATOR:
                continue
            try:
                xyz = tuple(
                    struct.unpack_from("<d", data, value_start + index * 9)[0]
                    for index in range(3)
                )
            except struct.error:
                continue
            if not all(math.isfinite(value) and abs(value) < 1e7 for value in xyz):
                continue
            records.append(xyz)  # type: ignore[arg-type]
        return records

    @staticmethod
    def _record(name: str, xyz: Sequence[float]) -> Dict:
        x, y, z = xyz
        return {
            "Class": "PmWeldPoint",
            "ExternalId": name,
            "Name": name,
            "Location": f"{x:.3f},{y:.3f},{z:.3f}",
            "X": round(x, 3),
            "Y": round(y, 3),
            "Z": round(z, 3),
            "Type": "BiW",
            "Source": "CATProduct binary V5_CFV2",
        }


def extract_biw_points_from_catproduct(path: str) -> List[Dict]:
    """读取受支持的 CATProduct 二进制布局；不满足条件时返回空列表。"""

    try:
        return CATProductBiWBinaryReader(path).read()
    except CATProductBinaryError:
        return []
