"""Extract reviewable weld-symbol candidates, with optional assembly-name hints.

UVR/V5_CFV3 is only partially decoded here: validated mesh-node envelopes and
one raw eight-vertex encoding are supported. Repeated small mesh families are
candidates, not proof of a sphere or a weld-layer count. No arbitrary part
envelope is returned when symbol evidence is absent.
"""

from __future__ import annotations

import itertools
import math
import struct
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

from .threedxml_center_extractor import ThreeDXmlError, _iter_binary_records, _local_name


IDENTITY = ((1., 0., 0., 0.), (0., 1., 0., 0.), (0., 0., 1., 0.))
MODES = {"auto", "xws_only", "geometry_only"}


def _child_text(element, name, default=""):
    return next(((child.text or "").strip() for child in element
                 if _local_name(child.tag) == name), default)


def _resolve_entry(names, uri):
    value = uri.strip().replace("\\", "/")
    if value.lower().startswith("urn:3dxml:"):
        value = value[10:]
    exact = [name for name in names if name.casefold() == value.casefold()]
    if len(exact) == 1:
        return exact[0]
    matches = [name for name in names if name.rsplit("/", 1)[-1].casefold()
               == value.rsplit("/", 1)[-1].casefold()]
    if len(matches) != 1:
        raise ThreeDXmlError(f"无法唯一定位 3DXML 关联文件: {uri}")
    return matches[0]


def _matrix(text):
    if not text:
        return IDENTITY
    try:
        values = tuple(float(value) for value in text.split())
    except ValueError as exc:
        raise ThreeDXmlError("RelativeMatrix 包含非数字") from exc
    if len(values) != 12 or not all(math.isfinite(value) for value in values):
        raise ThreeDXmlError("RelativeMatrix 必须包含 12 个有限数值")
    # 3DXML serializes the three basis vectors, then the translation (CATIA
    # column-vector convention). Internally use three rows of a 3x4 matrix.
    matrix = tuple(tuple(values[col * 3 + row] for col in range(3)) + (values[9 + row],)
                   for row in range(3))
    if any(abs(sum(matrix[k][i] * matrix[k][j] for k in range(3)) - (i == j)) > 1e-3
           for i in range(3) for j in range(3)):
        raise ThreeDXmlError("暂不支持含缩放或剪切的 RelativeMatrix，无法保证毫米阈值")
    return matrix


def _compose(parent, child):
    return tuple(tuple(sum(parent[row][k] * child[k][col] for k in range(3))
                       for col in range(3))
                 + (sum(parent[row][k] * child[k][3] for k in range(3)) + parent[row][3],)
                 for row in range(3))


def _transform(matrix, point):
    return tuple(sum(matrix[row][col] * point[col] for col in range(3)) + matrix[row][3]
                 for row in range(3))


@dataclass(frozen=True)
class RepOccurrence:
    entry: str
    part_name: str
    instance_path: str
    transform: tuple
    name_hint: bool


def _occurrences(archive, hint):
    names = archive.namelist()
    manifest_name = _resolve_entry(names, "Manifest.xml")
    manifest = ET.fromstring(archive.read(manifest_name))
    root_uri = next(((element.text or "").strip() for element in manifest.iter()
                     if _local_name(element.tag) == "Root"), "")
    product = ET.fromstring(archive.read(_resolve_entry(names, root_uri)))
    structure = next((element for element in product.iter()
                      if _local_name(element.tag) == "ProductStructure"), None)
    if structure is None:
        raise ThreeDXmlError("3DXML 缺少 ProductStructure，不能确认装配坐标")
    references, reps = {}, {}
    children, attachments = defaultdict(list), defaultdict(list)
    for element in structure:
        kind = _local_name(element.tag)
        if kind == "Reference3D":
            references[element.attrib["id"]] = element
        elif kind == "ReferenceRep":
            reps[element.attrib["id"]] = element
        elif kind == "Instance3D":
            children[_child_text(element, "IsAggregatedBy")].append(element)
        elif kind == "InstanceRep":
            attachments[_child_text(element, "IsAggregatedBy")].append(element)

    def visit(ref_id, path, matrix, inherited_hint, ancestry):
        if ref_id in ancestry:
            raise ThreeDXmlError(f"装配树包含循环引用: {ref_id}")
        if ref_id not in references:
            raise ThreeDXmlError(f"缺失或外部 Reference3D 暂不支持: {ref_id}")
        reference = references[ref_id]
        part_name = reference.attrib.get("name", ref_id)
        semantic_text = " ".join((part_name, _child_text(reference, "PLM_ExternalID"), path))
        matched = inherited_hint or bool(hint and hint.casefold() in semantic_text.casefold())
        for attachment in attachments[ref_id]:
            rep_id = _child_text(attachment, "IsInstanceOf")
            if rep_id not in reps:
                raise ThreeDXmlError(f"缺失或外部 ReferenceRep 暂不支持: {rep_id}")
            rep = reps[rep_id]
            entry = _resolve_entry(names, rep.attrib.get("associatedFile", ""))
            yield RepOccurrence(entry, part_name, path, matrix, matched)
        for instance in children[ref_id]:
            instance_name = instance.attrib.get("name", instance.attrib.get("id", "instance"))
            child_path = f"{path}/{instance_name}[{instance.attrib.get('id', '')}]"
            yield from visit(
                _child_text(instance, "IsInstanceOf"), child_path,
                _compose(matrix, _matrix(_child_text(instance, "RelativeMatrix"))),
                matched, ancestry | {ref_id},
            )

    root_id = structure.attrib.get("root", "")
    root_name = references.get(root_id)
    path = root_name.attrib.get("name", root_id) if root_name is not None else root_id
    return list(visit(root_id, path, IDENTITY, False, set()))


def _compact_count(payload, offset):
    if offset >= len(payload):
        raise ValueError("truncated compact count")
    value = payload[offset]
    if value != 255:
        return value, offset + 1
    return struct.unpack_from("<I", payload, offset + 1)[0], offset + 5


def _binary_mesh_nodes(payload):
    """Validate the node flags, reserved field, and paired mesh counts.

    04FDFF also appears inside compressed mesh arrays. Looking for the marker
    alone produces plausible-looking but meaningless coordinates.
    """
    for record in _iter_binary_records(payload):
        offset = record.offset
        if (payload[offset + 7] != 0 or payload[offset + 12:offset + 16] != b"\0" * 4
                or payload[offset + 32:offset + 37] != b"\xff\xff\x02\x00\x01"):
            continue
        try:
            count, cursor = _compact_count(payload, offset + 37)
            repeated, cursor = _compact_count(payload, cursor)
        except (ValueError, struct.error):
            continue
        if not 4 <= count <= 10_000_000 or count != repeated or payload[cursor:cursor+3] != b"\0" * 3:
            continue
        yield record, count, cursor + 3


def _cube_fit(vertices):
    if len(vertices) != 8 or not all(math.isfinite(v) for p in vertices for v in p):
        return None
    distances = sorted(math.dist(a, b) for a, b in itertools.combinations(vertices, 2))
    edge = sum(distances[:12]) / 12
    if edge <= 0.05:
        return None
    expected = [edge] * 12 + [edge * math.sqrt(2)] * 12 + [edge * math.sqrt(3)] * 4
    error = max(abs(actual - ideal) / edge for actual, ideal in zip(distances, expected))
    if error > 0.005:
        return None
    center = tuple(sum(p[axis] for p in vertices) / 8 for axis in range(3))
    return center, edge, error


def _raw_cube(payload, record, cursor):
    # This supported encoding contains all eight XYZ triples as float32.
    # Other compression variants remain unverified symbol candidates.
    signature = bytes.fromhex("14 45 51 14 45 51 18")
    start = payload.find(signature, cursor, min(cursor + 24, len(payload)))
    if start < 0 or start + 7 + 96 > len(payload):
        return None
    values = struct.unpack_from("<24f", payload, start + 7)
    vertices = [values[index:index+3] for index in range(0, 24, 3)]
    fit = _cube_fit(vertices)
    # The stored bound is conservative and need not equal the AABB half-
    # diagonal. Require containment, not equality with a minimal sphere.
    enclosed = all(math.dist(point, record.center) <= record.radius + 0.02 for point in vertices)
    if (fit and math.dist(fit[0], record.center) <= 0.02
            and enclosed):
        return fit
    return None


def _binary_symbols(payload, max_radius, has_hint, min_family_size):
    if not payload.startswith(b"V5_CFV3"):
        raise ThreeDXmlError("仅验证过 V5_CFV3 二进制表示；不猜测其他版本的节点布局")
    meshes = list(_binary_mesh_nodes(payload))
    small = [(r, count, cursor) for r, count, cursor in meshes
             if 0.1 <= r.radius <= max_radius and count <= 5000]
    family_counts = Counter(count for _, count, _ in small)
    radii = defaultdict(list)
    for record, count, _ in small:
        radii[count].append(record.radius)
    output = []
    for record, count, cursor in small:
        cube = _raw_cube(payload, record, cursor) if count == 24 else None
        repeated = family_counts[count] >= min_family_size
        if not (cube or repeated or has_hint):
            continue
        stable_radius = (max(radii[count]) - min(radii[count])) / max(radii[count]) <= 0.002
        if cube:
            shape, shape_confidence = "正方体", "high"
            evidence = f"8顶点的28组距离通过旋转不变立方体检验；边长={cube[1]:.6f}mm；相对残差={cube[2]:.6g}"
            center = cube[0]
            method = "binary_raw_cube_fit"
        else:
            shape, shape_confidence = "小型符号候选", "unverified"
            evidence = "压缩网格未完全解码；只确认小型独立网格及同族重复特征，不能证明球面/正方体"
            center = record.center
            method = "binary_validated_mesh_family"
        hypothesis = ("" if cube else "球形符号族（待球面拟合）" if repeated and count >= 48 and stable_radius
                      else "立方体同计数族（待顶点解码）" if count == 24 and repeated else "")
        output.append({
            "_coords": center, "_radius": record.radius, "Offset": record.offset,
            # VertexCount is retained for the older app schema; the observed
            # compact field is not asserted to be a unique-vertex count.
            "VertexCount": count, "MeshCount": count,
            "GeometryType": shape, "ShapeHypothesis": hypothesis,
            "ShapeConfidence": shape_confidence, "WeldLayers": "3T" if cube else "2T",
            "Confidence": "high" if cube or (has_hint and repeated) else "medium",
            "ExtractionMethod": method, "FamilySize": family_counts[count],
            "FamilyStableRadius": stable_radius,
            "CubeEdgeMM": cube[1] if cube else "",
            "Evidence": evidence,
        })
    return output, len(meshes)


class ThreeDXmlWeldExtractor:
    """Assembly-aware symbol extraction; inspect ``diagnostics`` after a run."""

    def __init__(self):
        self.diagnostics = {}

    def extract(self, path, *, mode="auto", name_hint="XWS", max_radius_mm=20.0,
                min_family_size=3):
        if mode not in MODES:
            raise ValueError(f"未知 3DXML 焊点识别策略: {mode}")
        if not math.isfinite(max_radius_mm) or max_radius_mm <= 0:
            raise ValueError("最大符号包络半径必须是正有限毫米数")
        if min_family_size < 2:
            raise ValueError("几何族最小重复数必须至少为 2")
        if mode == "xws_only" and not name_hint.strip():
            raise ValueError("仅名称筛选模式需要非空零件名称线索")
        file_path = Path(path)
        self.diagnostics = {"path": str(file_path), "mode": mode, "warnings": []}
        result, cache = [], {}
        try:
            with zipfile.ZipFile(file_path) as archive:
                occurrences = _occurrences(archive, "" if mode == "geometry_only" else name_hint.strip())
                self.diagnostics.update({"representation_instances": len(occurrences),
                                         "hint_instances": sum(o.name_hint for o in occurrences),
                                         "representations": []})
                for occurrence in occurrences:
                    if mode == "xws_only" and not occurrence.name_hint:
                        continue
                    key = (occurrence.entry, occurrence.name_hint)
                    if key not in cache:
                        payload = archive.read(occurrence.entry)
                        if not payload.startswith(b"V5_CFV3"):
                            self.diagnostics["warnings"].append(
                                f"{occurrence.entry}: 此焊点识别路径尚不支持该表示编码，未返回整件包络代替焊点")
                            cache[key] = ([], 0)
                        else:
                            cache[key] = _binary_symbols(payload, max_radius_mm, occurrence.name_hint, min_family_size)
                    symbols, mesh_count = cache[key]
                    self.diagnostics["representations"].append({
                        "entry": occurrence.entry, "instance_path": occurrence.instance_path,
                        "name_hint": occurrence.name_hint, "mesh_nodes": mesh_count,
                        "candidate_count": len(symbols),
                    })
                    for index, symbol in enumerate(symbols, 1):
                        point = _transform(occurrence.transform, symbol["_coords"])
                        result.append({
                            **symbol,
                            "Name": f"{occurrence.part_name}:WeldCandidate_{index:04d}",
                            "X": f"{point[0]:.6f}", "Y": f"{point[1]:.6f}", "Z": f"{point[2]:.6f}",
                            "Radius": f"{symbol['_radius']:.6f}", "Volume": 0,
                            "Type": "3DXML焊点候选", "Source": "3DXML",
                            "SourcePath": str(file_path.resolve()),
                            "SourceRepresentation": occurrence.entry,
                            "SourceObject": f"{occurrence.instance_path}/0x{symbol['Offset']:X}",
                            "PartName": occurrence.part_name, "InstancePath": occurrence.instance_path,
                            "NameHintMatched": occurrence.name_hint,
                            "CoordinateSystem": "assembly", "Unit": "mm", "_coords": point,
                            "Evidence": symbol["Evidence"] + (
                                f"；零件/装配路径命中名称线索 {name_hint}" if occurrence.name_hint else "；未使用名称线索"),
                        })
        except (OSError, zipfile.BadZipFile, ET.ParseError) as exc:
            raise ThreeDXmlError(f"3DXML 读取失败: {file_path}: {exc}") from exc
        self.diagnostics["candidate_count"] = len(result)
        return result


def extract_3dxml_welds(path, **options):
    return ThreeDXmlWeldExtractor().extract(path, **options)
