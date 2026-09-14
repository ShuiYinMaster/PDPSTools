# -*- coding: utf-8 -*-
"""Read geometric centers from a 3DXML representation.

CATIA often exposes a binary 3DXML as a Product proxy rather than as a Part
with measurable geometry.  In that case the useful geometry is in the
associated ``.3DRep`` entry.  This module first supports the public XML mesh
form and then the CATIA V5 ``V5_CFV3`` binary form found in the supplied file.

The binary result is explicitly a *bounding-sphere center* stored by the
visual representation.  For spheres and boxes this is their geometric
center; for an arbitrary tessellated shape it is not a volume centroid.
"""

from __future__ import annotations

import math
import re
import struct
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


_NODE_MARKER = b"\x04\xfd\xff"
DEFAULT_GEOMETRY_RADIUS_LIMITS_MM = {
    "球体": 10.0,
    "正方体": 20.0,
    "三棱锥": 20.0,
}
_FLOAT_RE = re.compile(
    r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
)


class ThreeDXmlError(ValueError):
    """Raised when a 3DXML container cannot be interpreted safely."""


@dataclass(frozen=True)
class BinarySphereRecord:
    """One ``04 FD FF`` visual-node bounding sphere."""

    offset: int
    radius: float
    x: float
    y: float
    z: float
    record_word: int
    vertex_count: Optional[int] = None
    mesh_span: int = 0

    @property
    def center(self) -> Tuple[float, float, float]:
        return self.x, self.y, self.z


def _finite(value: float) -> bool:
    return math.isfinite(value)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _entry_name(uri: str) -> str:
    """Turn a 3DXML urn reference into the ZIP entry basename."""

    value = uri.strip()
    if value.lower().startswith("urn:3dxml:"):
        value = value.split(":", 2)[-1]
    return value.replace("\\", "/").rsplit("/", 1)[-1]


def _zip_entry_casefold(names: Iterable[str], wanted: str) -> Optional[str]:
    wanted_norm = wanted.replace("\\", "/").lower()
    for name in names:
        if name.replace("\\", "/").lower() == wanted_norm:
            return name
    wanted_base = wanted_norm.rsplit("/", 1)[-1]
    for name in names:
        if name.replace("\\", "/").lower().rsplit("/", 1)[-1] == wanted_base:
            return name
    return None


def _numbers_from_element(element: ET.Element, names: Sequence[str]) -> List[float]:
    values: List[float] = []
    for key, value in element.attrib.items():
        if _local_name(key).lower() in {name.lower() for name in names}:
            values.extend(float(token) for token in _FLOAT_RE.findall(value))
    if values:
        return values
    text = "".join(element.itertext())
    return [float(token) for token in _FLOAT_RE.findall(text)]


def _aabb_center(points: Sequence[Tuple[float, float, float]]) -> Tuple[float, float, float, float]:
    if not points:
        raise ThreeDXmlError("3DRep XML 中没有可用的顶点")
    lows = [min(point[index] for point in points) for index in range(3)]
    highs = [max(point[index] for point in points) for index in range(3)]
    center = tuple((lows[index] + highs[index]) / 2.0 for index in range(3))
    radius = math.sqrt(sum(((highs[index] - lows[index]) / 2.0) ** 2 for index in range(3)))
    return center[0], center[1], center[2], radius


def _xml_rep_center(payload: bytes) -> Optional[Dict]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return None

    points: List[Tuple[float, float, float]] = []
    for element in root.iter():
        if _local_name(element.tag).lower() != "vertexbuffer":
            continue
        numbers = _numbers_from_element(element, ("positions", "position", "coordinates", "values"))
        if len(numbers) < 3:
            continue
        points.extend(
            (numbers[index], numbers[index + 1], numbers[index + 2])
            for index in range(0, len(numbers) - 2, 3)
        )
    if not points:
        return None
    x, y, z, radius = _aabb_center(points)
    return {
        "Name": "3DRep#1",
        "X": f"{x:.3f}",
        "Y": f"{y:.3f}",
        "Z": f"{z:.3f}",
        "Radius": f"{radius:.3f}",
        "Volume": 0,
        "Type": "3DRep XML包络中心",
        "Source": "3DRep XML VertexBuffer",
        "_coords": (x, y, z),
        "_radius": radius,
    }


def _iter_binary_records(payload: bytes) -> Iterable[BinarySphereRecord]:
    """Yield valid fixed-size V5_CFV3 node records.

    The marker itself is followed by a four-byte node id, then five little
    endian float32 values.  CGR's compact writer places the center at the
    third through fifth values (payload offsets +16, +20, +24); V5_CFV3 uses
    the same layout in the supplied 3DXML.
    """

    start = 0
    limit = len(payload)
    while True:
        offset = payload.find(_NODE_MARKER, start)
        if offset < 0:
            return
        start = offset + len(_NODE_MARKER)
        if offset + 47 > limit:
            continue
        try:
            radius, _reserved, x, y, z = struct.unpack_from("<5f", payload, offset + 8)
            record_word = struct.unpack_from("<I", payload, offset + 28)[0]
        except struct.error:
            continue
        if not all(_finite(value) for value in (radius, x, y, z)):
            continue
        if radius < 0.0 or radius > 1.0e9:
            continue
        vertex_count = None
        mesh_start = offset + 32
        if payload[mesh_start : mesh_start + 5] == b"\xff\xff\x02\x00\x01":
            compact = payload[mesh_start + 5]
            if compact < 255:
                vertex_count = compact
            elif mesh_start + 10 <= limit:
                vertex_count = struct.unpack_from("<I", payload, mesh_start + 6)[0]
        next_marker = payload.find(_NODE_MARKER, offset + len(_NODE_MARKER))
        mesh_span = next_marker - offset if next_marker >= 0 else limit - offset
        yield BinarySphereRecord(
            offset, radius, x, y, z, record_word, vertex_count, mesh_span
        )


def _record_key(record: BinarySphereRecord) -> Tuple[float, float, float, float]:
    # Repeated scene wrappers can differ by only the last float32 bit.  The
    # 1e-4 mm key removes those wrappers without hiding normal CAD spacing.
    return tuple(round(value, 4) for value in (record.radius, record.x, record.y, record.z))


def _deduplicate_records(records: Sequence[BinarySphereRecord]) -> List[BinarySphereRecord]:
    result: List[BinarySphereRecord] = []
    seen = {}
    for record in records:
        key = _record_key(record)
        if key in seen:
            index = seen[key]
            existing = result[index]
            if existing.vertex_count is None and record.vertex_count is not None:
                result[index] = replace(
                    existing,
                    offset=record.offset,
                    vertex_count=record.vertex_count,
                    mesh_span=record.mesh_span,
                )
            continue
        seen[key] = len(result)
        result.append(record)
    return result


def _primary_radius_threshold(records: Sequence[BinarySphereRecord]) -> Optional[float]:
    """Infer the boundary between shape/branch envelopes and tiny mesh chunks."""

    radii = sorted({round(record.radius, 4) for record in records if record.radius > 1.0})
    if len(radii) < 4:
        return None
    largest = radii[-1]
    # A tessellated 3DRep commonly has a low-radius tail, followed by the
    # larger envelopes that correspond to visible shape/branch groups.  Only
    # consider gaps below 10% of the global radius so the global/root gap does
    # not become the threshold.
    for previous, current in zip(radii, radii[1:]):
        if previous <= largest * 0.1 and current / previous >= 1.5:
            return math.sqrt(previous * current)
    return None


def _classify_geometry(record: BinarySphereRecord) -> Tuple[str, str]:
    """Classify standard visual symbols from their compact mesh size.

    The current V5_CFV3 symbols use a dense tessellation for spheres and a
    much smaller fixed mesh for prismatic symbols.  This is intentionally
    conservative: unknown mesh signatures remain unknown rather than being
    mislabeled as a weld-layer count.
    """

    if record.vertex_count is None:
        return "未知", ""
    if record.vertex_count >= 24:
        return "球体", "两层板焊点"
    if record.vertex_count == 16:
        return "正方体", "三层板焊点"
    if 4 <= record.vertex_count < 16:
        return "三棱锥", "四层板焊点"
    return "未知", ""


def _binary_record_dict(record: BinarySphereRecord, source: str) -> Dict:
    geometry_type, weld_layers = _classify_geometry(record)
    return {
        "Name": f"3DRep@0x{record.offset:X}",
        "X": f"{record.x:.3f}",
        "Y": f"{record.y:.3f}",
        "Z": f"{record.z:.3f}",
        "Radius": f"{record.radius:.3f}",
        "Volume": 0,
        "Type": "3DRep包络中心",
        "Source": source,
        "Offset": record.offset,
        "GeometryType": geometry_type,
        "WeldLayers": weld_layers,
        "VertexCount": record.vertex_count or 0,
        "_coords": record.center,
        "_radius": record.radius,
    }


def _binary_rep_centers(
    payload: bytes,
    include_internal: bool,
    max_radius_mm: float = 10.0,
    geometry_radius_limits_mm: Optional[Dict[str, float]] = None,
) -> List[Dict]:
    version = payload[:7].decode("ascii", errors="ignore")
    if not version.startswith("V5_CFV"):
        raise ThreeDXmlError(f"不支持的 3DRep 二进制头: {payload[:16]!r}")
    raw_records = list(_iter_binary_records(payload))
    occurrence_count = Counter(_record_key(record) for record in raw_records)
    records = _deduplicate_records(raw_records)
    records = [
        record
        for record in records
        if not (record.radius <= 1.0001 and max(abs(value) for value in record.center) <= 1.0e-4)
    ]
    if not records:
        raise ThreeDXmlError("V5_CFV3 中未找到有效的 04 FD FF 几何节点")

    radius_limits = dict(DEFAULT_GEOMETRY_RADIUS_LIMITS_MM)
    if geometry_radius_limits_mm:
        radius_limits.update(geometry_radius_limits_mm)

    def within_geometry_limit(record: BinarySphereRecord) -> bool:
        geometry_type, _ = _classify_geometry(record)
        limit = radius_limits.get(geometry_type, max_radius_mm)
        return record.radius <= limit

    if include_internal:
        selected = records
    else:
        # A visual shape is emitted through multiple branches.  Its 04FDFF
        # bounding node therefore occurs at least twice, while most one-off
        # mesh/branch envelopes occur once.  Classify each repeated shape
        # first, then apply that shape's radius limit so internal tessellation
        # is ignored without dropping larger prismatic symbols.
        selected = [
            record
            for record in records
            if occurrence_count[_record_key(record)] >= 2
            and within_geometry_limit(record)
        ]
        if not selected:
            threshold = _primary_radius_threshold(records)
            selected = [
                record
                for record in records
                if within_geometry_limit(record)
                and (threshold is None or record.radius >= threshold)
            ]
            if len(selected) > 1:
                max_radius = max(record.radius for record in selected)
                selected = [record for record in selected if record.radius < max_radius]
        if not selected:
            selected = [max(records, key=lambda record: record.radius)]

    limit_text = ", ".join(
        f"{geometry}≤{limit:g}mm" for geometry, limit in radius_limits.items()
    )
    source = (
        f"{version} 04FDFF repeated-leaf ({limit_text})"
        if not include_internal
        else f"{version} 04FDFF"
    )
    data = [_binary_record_dict(record, source) for record in selected]
    if not include_internal:
        for item in data:
            item["Type"] = "3DRep几何中心"
    return data


class ThreeDXmlCenterExtractor:
    """Extract center candidates from CATIA 3DXML containers."""

    def inspect(self, path: str) -> Dict:
        file_path = Path(path)
        if not file_path.is_file():
            raise ThreeDXmlError(f"3DXML 文件不存在: {file_path}")
        try:
            archive = zipfile.ZipFile(str(file_path), "r")
        except (OSError, zipfile.BadZipFile) as exc:
            raise ThreeDXmlError(f"不是有效的 3DXML ZIP 容器: {file_path}") from exc

        with archive:
            names = archive.namelist()
            reps: List[str] = []
            manifest_name = _zip_entry_casefold(names, "Manifest.xml")
            if manifest_name:
                try:
                    manifest = ET.fromstring(archive.read(manifest_name))
                    for element in manifest.iter():
                        if _local_name(element.tag).lower() == "root":
                            root_value = element.attrib.get("Name") or element.attrib.get("name") or (element.text or "")
                            if root_value:
                                root_name = _zip_entry_casefold(names, root_value.strip())
                                if root_name:
                                    try:
                                        product = ET.fromstring(archive.read(root_name))
                                        for rep in product.iter():
                                            if _local_name(rep.tag).lower() in {"associatedfile", "fileref"}:
                                                value = rep.attrib.get("Name") or rep.attrib.get("name") or rep.attrib.get("AssociatedFile")
                                                if value:
                                                    entry = _zip_entry_casefold(names, _entry_name(value))
                                                    if entry and entry not in reps:
                                                        reps.append(entry)
                                    except ET.ParseError:
                                        pass
                except ET.ParseError:
                    pass
            if not reps:
                reps = [name for name in names if name.lower().endswith((".3drep", ".3drep.xml"))]
            if not reps:
                raise ThreeDXmlError("3DXML 容器中没有关联的 .3DRep")

            return {
                "path": str(file_path),
                "entries": names,
                "representations": reps,
                "with_authoring_data": False,
            }

    def extract(
        self,
        path: str,
        include_internal: bool = False,
        max_radius_mm: float = 10.0,
        geometry_radius_limits_mm: Optional[Dict[str, float]] = None,
    ) -> List[Dict]:
        inspection = self.inspect(path)
        result: List[Dict] = []
        with zipfile.ZipFile(inspection["path"], "r") as archive:
            for index, rep_name in enumerate(inspection["representations"], 1):
                payload = archive.read(rep_name)
                if payload.lstrip().startswith(b"<"):
                    center = _xml_rep_center(payload)
                    if center:
                        center["Name"] = f"3DRep#{index}"
                        result.append(center)
                    continue
                if payload.startswith(b"V5_CFV"):
                    centers = _binary_rep_centers(
                        payload,
                        include_internal=include_internal,
                        max_radius_mm=max_radius_mm,
                        geometry_radius_limits_mm=geometry_radius_limits_mm,
                    )
                    if len(inspection["representations"]) > 1:
                        for center in centers:
                            center["Name"] = f"3DRep#{index}:{center['Name']}"
                    result.extend(centers)
                    continue
                raise ThreeDXmlError(f"无法识别 3DRep 表示: {rep_name}")
        return result


def extract_3dxml_centers(
    path: str,
    include_internal: bool = False,
    max_radius_mm: float = 10.0,
    geometry_radius_limits_mm: Optional[Dict[str, float]] = None,
) -> List[Dict]:
    """Convenience API used by the CATIA sphere scanner."""

    return ThreeDXmlCenterExtractor().extract(
        path,
        include_internal=include_internal,
        max_radius_mm=max_radius_mm,
        geometry_radius_limits_mm=geometry_radius_limits_mm,
    )
