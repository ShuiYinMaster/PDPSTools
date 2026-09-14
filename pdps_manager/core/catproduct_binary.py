# -*- coding: utf-8 -*-
"""从 CATProduct 专有二进制中读取 BiW Joint Element 坐标。

CATIA V5 没有公开一个稳定的 Automation 接口，可以直接返回 BiW
``Joint Element`` 的几何坐标。本模块因此不依赖固定文件偏移，而是识别目前
多个样本中保持不变的坐标载荷结构：

* ``Joint Element.XXXXX`` 名称以 ASCII 保存；
* 坐标由三个 little-endian IEEE-754 double 组成；
* 三个 double 由 ``E6`` 分隔，且每组数值之间间隔 9 个字节；
* 同一坐标可能以主记录和镜像记录各保存一次；
* 记录头可能是 ``FE FE 32 <kind>``，也可能是 ``FE 32 <kind>``。

记录类型和记录头属于 CATIA 持久化变体，不能作为唯一识别条件。扫描结果
必须通过名称数量、坐标记录数量、主/镜像重复关系和数值合法性校验，否则
返回错误而不是静默生成可能错误的坐标。
"""

from __future__ import annotations

import math
import re
import struct
from collections import defaultdict
from typing import DefaultDict, Dict, List, Sequence, Tuple


_JOINT_ELEMENT_RE = re.compile(rb"Joint Element\.([0-9]+)")
_DOUBLE_MARKER = b"\xfe\xfe\x32"
_SINGLE_MARKER = b"\xfe\x32"


class CATProductBinaryError(ValueError):
    """当前文件不满足本模块支持的 CATProduct 二进制布局。"""


class CATProductBiWBinaryReader:
    """按结构识别 BiW Joint Element 的名称和显式坐标。"""

    MAGIC = b"V5_CFV2"
    # 已验证样本中出现的记录类型只用于选择主记录，不作为扫描的硬条件。
    # 新版本可能使用新的 kind，只要其载荷结构仍然符合坐标签名即可识别。
    COORD_RECORD_KINDS = frozenset((0x3C, 0x0F, 0x1D))
    MIRROR_RECORD_KINDS = frozenset((0x3E, 0x11, 0x1E))
    SEPARATOR = 0xE6
    COORDINATE_LIMIT = 1e7
    _PREFIXES = ((_DOUBLE_MARKER, 3), (_SINGLE_MARKER, 2))

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
        if not names:
            raise CATProductBinaryError("未找到 Joint Element 名称")

        groups = self._coordinate_groups()
        coordinates, group_label = self._select_coordinate_group(groups, len(names))
        if not coordinates:
            if groups:
                summary = ", ".join(
                    f"{label}={len(values)}" for label, values in groups.items()
                )
                raise CATProductBinaryError(
                    "未找到与 Joint Element 数量匹配的坐标记录 "
                    f"（需要 {len(names)} 条；候选：{summary}）"
                )
            raise CATProductBinaryError(
                "未找到符合结构的 BiW 坐标记录 "
                "（需要 E6 分隔的三个 little-endian double）"
            )

        if len(names) != len(coordinates):
            raise CATProductBinaryError(
                "Joint Element 数量与坐标记录数量不一致 "
                f"({len(names)} != {len(coordinates)}；来源 {group_label})"
            )

        # 当前样本的名称表与主坐标记录按 Joint Element 内部序列排列。
        # 这不是公开格式契约，但数量校验和重复组校验可以避免错配。
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

    def _coordinate_groups(
        self,
    ) -> DefaultDict[str, List[Tuple[float, float, float]]]:
        """扫描所有坐标候选，并按前缀/记录类型分组。

        这里不再只认固定的 0x3C/0x0F。记录类型仅作为分组标签；真正的
        识别条件是记录内部存在三个按固定间距排列的 ``E6 + double``。
        """
        groups: DefaultDict[str, List[Tuple[float, float, float]]] = defaultdict(list)
        data = self.data
        for marker_bytes, kind_offset in self._PREFIXES:
            start = 0
            while True:
                marker = data.find(marker_bytes, start)
                if marker < 0:
                    break
                start = marker + 1

                # ``FE FE 32`` 内部也包含一个 ``FE 32`` 子串，避免重复扫描。
                if marker_bytes == _SINGLE_MARKER and marker > 0 and data[marker - 1] == 0xFE:
                    continue
                kind_index = marker + kind_offset
                if kind_index >= len(data):
                    continue
                kind = data[kind_index]
                xyz = self._parse_coordinate_payload(marker, kind_offset)
                if xyz is None:
                    continue
                label = f"{marker_bytes.hex(' ').upper()}+0x{kind:02X}"
                groups[label].append(xyz)
        return groups

    def _parse_coordinate_payload(
        self, marker: int, kind_offset: int
    ) -> Tuple[float, float, float] | None:
        """解析一条候选记录中的三个 double。

        已验证的两种布局中，第一个分隔符位于 marker+13 或 marker+14；
        为兼容后续小变体，在记录头之后到 marker+32 内寻找第一组符合
        ``E6`` 间隔 9 字节的载荷。
        """
        data = self.data
        search_start = marker + kind_offset + 1
        search_end = min(len(data), marker + 33)
        first_separator = data.find(bytes((self.SEPARATOR,)), search_start, search_end)
        while first_separator >= 0:
            separators = (
                first_separator,
                first_separator + 9,
                first_separator + 18,
            )
            if separators[2] >= len(data):
                return None
            if any(data[offset] != self.SEPARATOR for offset in separators):
                next_start = first_separator + 1
                first_separator = data.find(
                    bytes((self.SEPARATOR,)), next_start, search_end
                )
                continue
            try:
                values = tuple(
                    struct.unpack_from("<d", data, offset + 1)[0]
                    for offset in separators
                )
            except struct.error:
                return None
            if all(
                math.isfinite(value) and abs(value) < self.COORDINATE_LIMIT
                for value in values
            ):
                return values  # type: ignore[return-value]
            return None
        return None

    def _select_coordinate_group(
        self,
        groups: Dict[str, List[Tuple[float, float, float]]],
        name_count: int,
    ) -> Tuple[List[Tuple[float, float, float]], str]:
        """选择与名称数量匹配且最可信的主坐标组。

        主/镜像组通常有完全相同的坐标序列。优先选择已知主记录类型，
        然后选择存在完全重复组的候选；若只有一个匹配组，也允许读取未知
        记录类型，以支持新的 CATIA 持久化变体。
        """
        matching = {
            label: values
            for label, values in groups.items()
            if len(values) == name_count
        }
        if not matching:
            return [], ""

        def label_score(label: str) -> Tuple[int, int, str]:
            try:
                kind = int(label.rsplit("0x", 1)[1], 16)
            except (IndexError, ValueError):
                kind = -1
            # 已验证的主记录优先；镜像记录最后选择。
            if kind in self.COORD_RECORD_KINDS:
                role = 0
            elif kind in self.MIRROR_RECORD_KINDS:
                role = 2
            else:
                role = 1
            return role, kind, label

        duplicated = []
        for label, values in matching.items():
            if any(other != label and other_values == values
                   for other, other_values in matching.items()):
                duplicated.append((label, values))
        if duplicated:
            # 同一坐标可能被多个辅助记录重复保存。先收敛到同一坐标序列，
            # 再按主记录优先级选取标签，避免把镜像记录当成另一批点。
            coordinate_sequences = {tuple(values) for _, values in duplicated}
            if len(coordinate_sequences) > 1:
                raise CATProductBinaryError(
                    "候选 BiW 坐标记录存在多个不一致的重复坐标序列，无法安全判断主记录"
                )
            candidates = duplicated
        else:
            # 只有一个候选组时允许兼容未知 kind；多个互不相同的候选组
            # 不能靠记录编号大小猜测，否则可能误读普通持久化数据。
            if len(matching) > 1:
                raise CATProductBinaryError(
                    "存在多个数量匹配但坐标不一致的候选记录组，无法安全识别 BiW 坐标"
                )
            candidates = list(matching.items())
        candidates.sort(key=lambda item: label_score(item[0]))
        return candidates[0][1], candidates[0][0]

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
