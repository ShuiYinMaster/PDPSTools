# -*- coding: utf-8 -*-
"""CATIAWeldPointExtractor —— CATIA 焊点提取工具类。

兼容 win32com 与 pycatia 两种连接方式。普通几何点沿用 SPA 测量，
BiW Joint Element 直接遍历当前 Product 的 CATProduct 文件并读取已验证的
二进制记录，不依赖 Selection、SPA 或 ABF Export。
"""

import os
import queue
import re
import subprocess
import tempfile
import threading
import traceback
from datetime import datetime
from xml.sax.saxutils import escape as xml_escape
from typing import List, Dict, Optional

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QPushButton, QProgressBar,
    QLineEdit, QInputDialog, QMessageBox,
)

from ..config import PYCATIA_AVAILABLE, PartDocument, pycatia_catia
from .catproduct_binary import CATProductBiWBinaryReader, CATProductBinaryError


class CATIAWeldPointExtractor:
    """CATIA 焊点提取工具类（兼容 win32com 和 pycatia）"""

    def __init__(self, catia_app=None):
        self.catia_app = catia_app
        self.weld_points_data: List[Dict] = []
        self.status_message = "就绪"
        self.connection_type = None
        self.rename_mode = "original"
        self.custom_rename_config = {
            "main_name": "WeldPoint",
            "prefix": "",
            "suffix": "",
            "start_index": 1,
            "width": 4,
            "separator": "_",
        }

    def connect_catia(self, use_pycatia=False) -> bool:
        try:
            if use_pycatia:
                if not PYCATIA_AVAILABLE:
                    return False
                self.catia_app = pycatia_catia()
                self.connection_type = 'pycatia'
                self.status_message = "已连接 | CATIA (pycatia)"
                return True
            else:
                import win32com.client
                try:
                    self.catia_app = win32com.client.GetActiveObject("CATIA.Application")
                except Exception:
                    self.catia_app = win32com.client.Dispatch("CATIA.Application")
                self.connection_type = 'win32com'
                self.status_message = "已连接 | CATIA (win32com)"
                return True
        except Exception:
            self.status_message = "连接 CATIA 失败"
            return False

    def set_catia_app(self, catia_app, connection_type='win32com'):
        self.catia_app = catia_app
        self.connection_type = connection_type
        self.status_message = f"CATIA 应用已设置 ({connection_type})"

    def get_status(self):
        return self.status_message

    def _extract_weld_points_from_product(
        self, product_document, auto_rename=False, progress_callback=None
    ):
        """Read ordinary CATIA points from every CATPart instance in a Product."""
        from .sphere_scanner import CATIASphereScanner

        scanner = CATIASphereScanner()
        scanner.set_catia_app(self.catia_app, self.connection_type)
        sources = list(scanner._iter_part_sources())
        self.weld_points_data = []

        def build_parent_map(part):
            point_parent_map = {}

            def visit(body, body_name):
                try:
                    shapes = getattr(body, "HybridShapes")
                    for index in range(1, int(shapes.Count) + 1):
                        shape = shapes.Item(index)
                        name = str(getattr(shape, "Name", "") or "")
                        if name:
                            point_parent_map[name] = body_name
                except Exception:
                    pass
                try:
                    children = getattr(body, "HybridBodies")
                    for index in range(1, int(children.Count) + 1):
                        child = children.Item(index)
                        child_name = str(getattr(child, "Name", "GeometrySet") or "GeometrySet")
                        visit(child, child_name)
                except Exception:
                    pass

            try:
                bodies = getattr(part, "HybridBodies")
                for index in range(1, int(bodies.Count) + 1):
                    body = bodies.Item(index)
                    visit(body, str(getattr(body, "Name", "GeometrySet") or "GeometrySet"))
            except Exception:
                pass
            return point_parent_map

        total_sources = len(sources)
        for source_index, source in enumerate(sources, 1):
            raw_document = source["document"]
            part = source["part"]
            self._report_progress(
                progress_callback,
                source_index - 1,
                total_sources,
                f"正在检查 Part {source_index}/{total_sources}: {source['instance_path']}",
            )

            # pycatia 的 HybridShape 包装对象才是可测量的点对象。直接把
            # win32com Selection.Item.Value 交给 GetPoint 会得到 0,0,0，
            # 即使 CATIA 实际已经返回了焊点。
            if PYCATIA_AVAILABLE and PartDocument is not None:
                try:
                    part_document = PartDocument(raw_document)
                    part_api = part_document.part
                    spa_api = part_document.spa_workbench()
                    parent_map = self._build_point_parent_map(part_api)
                    point_objects = []
                    root_bodies = part_api.hybrid_bodies
                    for body_path, body in scanner._iter_hybrid_bodies(root_bodies):
                        shapes = body.hybrid_shapes
                        for shape_index in range(1, shapes.count + 1):
                            shape = shapes.item(shape_index)
                            if "point" in scanner._type_name(shape).lower():
                                point_objects.append((shape, body_path))
                    point_count = len(point_objects)
                    for point_index, (point, body_path) in enumerate(point_objects, 1):
                        try:
                            name = str(point.name or f"Point_{point_index}")
                            reference = part_api.create_reference_from_object(point)
                            measurable = spa_api.get_measurable(reference)
                            local_coords = tuple(float(value) for value in measurable.get_point())
                            x, y, z = scanner._transform_point(
                                local_coords, source["transform"]
                            )
                            geo_set_name = parent_map.get(name, body_path.rsplit("/", 1)[-1])
                            output_name = (
                                self._generate_standard_name(name, geo_set_name)
                                if auto_rename else name
                            )
                            self.weld_points_data.append({
                                "Class": "PmWeldPoint",
                                "ExternalId": output_name,
                                "Name": output_name,
                                "Location": f"{x:.3f},{y:.3f},{z:.3f}",
                                "X": round(x, 3),
                                "Y": round(y, 3),
                                "Z": round(z, 3),
                                "Type": self._weld_type_from_location(geo_set_name),
                                "_OriginalName": name,
                                "_OriginalExternalId": name,
                                "_OriginalGeoSetName": geo_set_name,
                                "_CATPartInstance": source["instance_path"],
                            })
                        except Exception as exc:
                            print(
                                f"无法读取 {source['instance_path']} 中第 "
                                f"{point_index} 个点: {exc}"
                            )
                            self._report_progress(
                                progress_callback,
                                source_index,
                                total_sources,
                                f"点读取失败 {source['instance_path']}#{point_index}：{exc}",
                            )
                    self._report_progress(
                        progress_callback,
                        source_index,
                        total_sources,
                        f"完成 {source['instance_path']}：发现 {point_count} 个点，"
                        f"成功读取 {len(self.weld_points_data)} 个累计点",
                    )
                    continue
                except Exception as exc:
                    print(f"pycatia 读取 Part 失败 {source['instance_path']}: {exc}")
                    self._report_progress(
                        progress_callback,
                        source_index,
                        total_sources,
                        f"Part 读取失败：{source['instance_path']}（{exc}）",
                    )
                    # pycatia 路径失败时不再降级到 raw Selection；后者在
                    # 当前 CATIA 版本可能返回全零坐标，不能作为有效结果。
                    continue

            try:
                selection = raw_document.Selection
                selection.Clear()
                selection.Search("CATGmoSearch.Point,all")
                count = int(getattr(selection, "Count2", getattr(selection, "Count", 0)) or 0)
                spa = raw_document.GetWorkbench("SPAWorkbench")
                parent_map = build_parent_map(part)
            except Exception as exc:
                print(f"无法读取 Product 中的 Part {source['instance_path']}: {exc}")
                self._report_progress(
                    progress_callback,
                    source_index,
                    total_sources,
                    f"Part 读取失败：{source['instance_path']}（{exc}）",
                )
                continue

            for index in range(1, count + 1):
                try:
                    item = selection.Item2(index)
                except Exception:
                    try:
                        item = selection.Item(index)
                    except Exception:
                        continue
                try:
                    point = item.Value
                    name = str(getattr(point, "Name", f"Point_{index}") or f"Point_{index}")
                    reference = part.CreateReferenceFromObject(point)
                    measurable = spa.GetMeasurable(reference)
                    coords = measurable.GetPoint([0.0, 0.0, 0.0])
                    if coords is None or len(coords) < 3:
                        continue
                    local_coords = tuple(float(value) for value in coords[:3])
                    x, y, z = scanner._transform_point(local_coords, source["transform"])
                    geo_set_name = parent_map.get(name, "")
                    output_name = (
                        self._generate_standard_name(name, geo_set_name)
                        if auto_rename else name
                    )
                    self.weld_points_data.append({
                        "Class": "PmWeldPoint",
                        "ExternalId": output_name,
                        "Name": output_name,
                        "Location": f"{x:.3f},{y:.3f},{z:.3f}",
                        "X": round(x, 3),
                        "Y": round(y, 3),
                        "Z": round(z, 3),
                        "Type": self._weld_type_from_location(geo_set_name),
                        "_OriginalName": name,
                        "_OriginalExternalId": name,
                        "_OriginalGeoSetName": geo_set_name,
                        "_CATPartInstance": source["instance_path"],
                    })
                except Exception as exc:
                    print(f"无法读取 {source['instance_path']} 中第 {index} 个点: {exc}")
                    self._report_progress(
                        progress_callback,
                        source_index,
                        total_sources,
                        f"点读取失败 {source['instance_path']}#{index}：{exc}",
                    )
            try:
                selection.Clear()
            except Exception:
                pass
            self._report_progress(
                progress_callback,
                source_index,
                total_sources,
                f"完成 {source['instance_path']}：累计读取 {len(self.weld_points_data)} 个点",
            )

        self.status_message = (
            f"已从 Product 遍历 {len(sources)} 个 Part 实例，提取 "
            f"{len(self.weld_points_data)} 个焊点"
        )
        return self.weld_points_data

    @staticmethod
    def _report_progress(callback, current, total, message):
        if callback is None:
            return
        try:
            callback(current, total, message)
        except TypeError:
            callback(message)

    def extract_weld_points(
        self, auto_rename: bool = False, progress_callback=None
    ) -> List[Dict]:
        if not self.catia_app:
            self.status_message = "请先连接 CATIA"
            return []
        self.weld_points_data = []
        try:
            if self.connection_type == 'pycatia':
                doc = self.catia_app.active_document
                raw_doc = getattr(doc, "com_object", doc)
                document_name = str(getattr(raw_doc, "Name", getattr(doc, "name", "")) or "")
                if document_name.lower().endswith(".catproduct"):
                    return self._extract_weld_points_from_product(
                        raw_doc, auto_rename, progress_callback
                    )
                if not doc or not hasattr(doc, 'part'):
                    self.status_message = "当前文档不是 Part 文档"
                    return []
                part = doc.part
                selection = doc.selection
                part_full_name = doc.name
            else:
                doc = self.catia_app.ActiveDocument
                if not doc:
                    return []
                document_name = str(getattr(doc, "Name", "") or "")
                if document_name.lower().endswith(".catproduct"):
                    return self._extract_weld_points_from_product(
                        doc, auto_rename, progress_callback
                    )
                try:
                    part = doc.Part
                except Exception:
                    self.status_message = "当前文档不是 Part 文档"
                    return []
                selection = doc.Selection
                part_full_name = doc.Name

            spa_workbench = None
            try:
                if self.connection_type == 'pycatia':
                    spa_workbench = doc.spa_workbench()
                else:
                    spa_workbench = doc.GetWorkbench("SPAWorkbench")
            except Exception:
                pass

            if self.connection_type == 'pycatia':
                selection.clear()
                selection.search("CATGmoSearch.Point,all")
                count = selection.count
            else:
                selection.Clear()
                selection.Search("CATGmoSearch.Point,all")
                count = selection.Count

            # ── 预扫描：构建 {点名称 → 直接父图形集名称} 映射表 ────────────
            # 比逐点调用 .Parent 更可靠，避免 win32com/pycatia 的 Parent 属性失效问题
            point_parent_map = self._build_point_parent_map(part)

            for i in range(1, count + 1):
                try:
                    if self.connection_type == 'pycatia':
                        item = selection.item(i)
                        point_obj = item.value
                        name = getattr(point_obj, 'name', f"Point_{i}")
                    else:
                        item = selection.Item(i)
                        point_obj = item.Value
                        name = getattr(point_obj, 'Name', f"Point_{i}")

                    # ── 从映射表获取父图形集名 ──────────────────────────────
                    # 优先用精确名查询，再去掉 GSMPoint 后缀的基本名匹配
                    geo_set_name = point_parent_map.get(name, "")
                    if not geo_set_name:
                        # 点名可能含 ".N" 后缀，尝试去掉后缀再匹配
                        base_name = re.sub(r'\.GSMPoint\.\d+$', '', name, flags=re.IGNORECASE)
                        base_name = re.sub(r'-GSMPoint\.\d+$', '', base_name, flags=re.IGNORECASE)
                        geo_set_name = point_parent_map.get(base_name, "")

                    # ── 获取坐标 ────────────────────────────────────────────
                    x, y, z = 0, 0, 0
                    try:
                        if spa_workbench:
                            if self.connection_type == 'pycatia':
                                ref = part.create_reference_from_object(point_obj)
                                m = spa_workbench.get_measurable(ref)
                                coords = m.get_point()
                            else:
                                ref = part.CreateReferenceFromObject(point_obj)
                                m = spa_workbench.GetMeasurable(ref)
                                # win32com 返回 SAFEARRAY，而不是可靠地回写传入 list。
                                coords = m.GetPoint([0.0, 0.0, 0.0])
                            x, y, z = coords[0], coords[1], coords[2]
                    except Exception:
                        pass

                    # ── 自动重命名 ───────────────────────────────────────────
                    # geo_set_name 即为图形集名（如 DPUB-501000588-XAX&DPUB-501000592-XAX&...）
                    # 直接用它提取零件号，无需再向上遍历父节点
                    std_name = name
                    if auto_rename:
                        std_name = self._generate_standard_name(name, geo_set_name)

                    # ── 根据图形集名称中 & 数量推断焊接层数类型 ────────────
                    weld_type = self._weld_type_from_location(geo_set_name)
                    location_str = f"{x:.3f},{y:.3f},{z:.3f}"
                    self.weld_points_data.append({
                        "Class": "PmWeldPoint",
                        "ExternalId": std_name,
                        "Name": std_name,
                        "Location": location_str,
                        "X": round(x, 3),
                        "Y": round(y, 3),
                        "Z": round(z, 3),
                        "Type": weld_type,
                        "_OriginalName": name,
                        "_OriginalExternalId": name,
                        "_OriginalGeoSetName": geo_set_name,
                    })
                except Exception:
                    pass

            self.status_message = f"已提取 {len(self.weld_points_data)} 个焊点"
        except Exception as e:
            self.status_message = f"提取失败: {e}"
        return self.weld_points_data

    def extract_biw_fasteners(self, append: bool = False, progress_callback=None) -> List[Dict]:
        """从当前活动 Product 树的 CATProduct 二进制读取 BiW 坐标。"""
        return self._extract_biw_fasteners_from_binary_tree(append, progress_callback)

    def _extract_biw_fasteners_from_binary_tree(
        self, append: bool = False, progress_callback=None
    ) -> List[Dict]:
        """从当前活动 Product 及其嵌套 Product 的二进制中读取 BiW。"""
        if not self.catia_app:
            self.status_message = "请先连接 CATIA"
            return []

        if not append:
            self.weld_points_data = []

        try:
            pycatia = (self.connection_type == 'pycatia')
            if pycatia:
                doc = self.catia_app.active_document
            else:
                doc = self.catia_app.ActiveDocument
                if not doc:
                    self.status_message = "没有活动文档"
                    return self.weld_points_data

            try:
                product = doc.product if pycatia else doc.Product
            except Exception:
                product = None
            if product is None:
                self.status_message = "当前文档不是 CATProduct，无法提取 BiW Joint Element"
                return self.weld_points_data

            records, product_paths, errors, matched_paths = (
                self._extract_biw_from_product_tree(doc, pycatia, progress_callback)
            )
            if not records:
                if errors:
                    detail = errors[0]
                    if len(errors) > 1:
                        detail += f"（另有 {len(errors) - 1} 个 Product 未匹配）"
                    self.status_message = (
                        f"BiW 提取失败：遍历 {len(product_paths)} 个 CATProduct 后未找到"
                        f" Joint Element；{detail}"
                    )
                elif product_paths:
                    self.status_message = (
                        f"未在遍历到的 {len(product_paths)} 个 CATProduct 二进制中找到"
                        " BiW Joint Element"
                    )
                else:
                    self.status_message = (
                        "BiW 提取失败：当前活动 Product 没有可读取的已保存 CATProduct 文件"
                    )
                return self.weld_points_data

            self.weld_points_data.extend(records)
            self.status_message = (
                f"已提取 {len(records)} 个 BiW 焊点（共 {len(self.weld_points_data)} 条）"
                f"，来源：CATProduct 二进制（遍历 {len(product_paths)} 个 Product，"
                f"BiW 位于 {len(matched_paths)} 个 Product）"
            )
        except Exception as exc:
            self.status_message = f"BiW 提取失败: {exc}"
        return self.weld_points_data

    @staticmethod
    def _raw_com_object(value):
        """取得 pycatia/win32com 对象对应的原始 COM 对象。"""
        if value is None:
            return None
        try:
            return getattr(value, "com_object", value)
        except Exception:
            return value

    @classmethod
    def _reference_product_path(cls, product) -> str:
        """从 Product.ReferenceProduct.Parent 取得被引用文档的完整路径。"""
        raw_product = cls._raw_com_object(product)
        try:
            reference = getattr(raw_product, "ReferenceProduct", None)
        except Exception:
            reference = None
        if reference is None:
            return ""

        raw_reference = cls._raw_com_object(reference)
        objects = [raw_reference]
        try:
            objects.append(getattr(raw_reference, "Parent", None))
        except Exception:
            pass
        for obj in objects:
            if obj is None:
                continue
            for attribute in ("FullName", "Path"):
                try:
                    value = str(getattr(obj, attribute) or "")
                except Exception:
                    continue
                if value.lower().endswith((".catproduct", ".catpart")):
                    return value
        return ""

    @classmethod
    def _collect_product_binary_paths(cls, doc, pycatia: bool) -> List[str]:
        """递归收集当前 Product 及嵌套 Product 的已保存 CATProduct 路径。"""
        paths: List[str] = []
        seen_paths = set()

        def add_path(value):
            if not value:
                return
            try:
                path = os.path.abspath(os.path.normpath(str(value)))
            except (OSError, TypeError, ValueError):
                return
            if not path.lower().endswith(".catproduct") or not os.path.isfile(path):
                return
            key = os.path.normcase(path)
            if key in seen_paths:
                return
            seen_paths.add(key)
            paths.append(path)

        add_path(cls._get_document_path(doc, pycatia))
        try:
            root_product = doc.product if pycatia else doc.Product
        except Exception:
            root_product = None
        if root_product is None:
            return paths

        stack = [cls._raw_com_object(root_product)]
        visited = set()
        while stack and len(visited) < 10000:
            current = stack.pop()
            if current is None:
                continue
            try:
                node_key = str(current)
            except Exception:
                node_key = str(id(current))
            if node_key in visited:
                continue
            visited.add(node_key)

            try:
                products = getattr(current, "Products", None)
                count = int(getattr(products, "Count", 0) or 0)
            except Exception:
                products = None
                count = 0
            if products is None:
                continue

            for index in range(1, count + 1):
                try:
                    child = products.Item(index)
                except Exception:
                    continue
                child_path = cls._reference_product_path(child)
                if child_path.lower().endswith(".catproduct"):
                    add_path(child_path)
                stack.append(cls._raw_com_object(child))
        return paths

    @classmethod
    def _extract_biw_from_product_tree(cls, doc, pycatia: bool, progress_callback=None):
        """读取产品树中的 CATProduct 二进制并返回记录及诊断信息。"""
        product_paths = cls._collect_product_binary_paths(doc, pycatia)
        records: List[Dict] = []
        errors = []
        matched_paths = []
        seen_records = set()
        total_paths = len(product_paths)
        for index, path in enumerate(product_paths, 1):
            cls._report_progress(
                progress_callback,
                index - 1,
                total_paths,
                f"正在解析 Product {index}/{total_paths}: {os.path.basename(path)}",
            )
            try:
                parsed = CATProductBiWBinaryReader(path).read()
            except CATProductBinaryError as exc:
                errors.append(f"{os.path.basename(path)}：{exc}")
                cls._report_progress(
                    progress_callback,
                    index,
                    total_paths,
                    f"未匹配 BiW：{os.path.basename(path)}（{exc}）",
                )
                continue
            if not parsed:
                continue
            matched_paths.append(path)
            for item in parsed:
                item["_BiWProductPath"] = path
                key = (
                    os.path.normcase(path),
                    str(item.get("ExternalId") or ""),
                    item.get("X"),
                    item.get("Y"),
                    item.get("Z"),
                )
                if key in seen_records:
                    continue
                seen_records.add(key)
                records.append(item)
            cls._report_progress(
                progress_callback,
                index,
                total_paths,
                f"完成 {os.path.basename(path)}：读取 {len(parsed)} 个 BiW 点，"
                f"累计 {len(records)} 个",
            )
        return records, product_paths, errors, matched_paths

    def _ensure_original_fields(self, item: Dict) -> None:
        original_name = str(item.get("_OriginalName") or item.get("Name") or "")
        item.setdefault("_OriginalName", original_name)
        item.setdefault(
            "_OriginalExternalId",
            str(item.get("ExternalId") or original_name),
        )

    @staticmethod
    def _custom_name(original_name: str, index: int, config: Dict) -> str:
        main_name = str(config.get("main_name") or "WeldPoint")
        prefix = str(config.get("prefix") or "")
        suffix = str(config.get("suffix") or "")
        separator = str(config.get("separator") or "")
        start_index = int(config.get("start_index", 1))
        width = max(0, int(config.get("width", 4)))
        number = str(start_index + index)
        if width:
            number = number.zfill(width)
        return f"{prefix}{main_name}{separator}{number}{suffix}"

    def apply_rename(self, mode="original", config: Optional[Dict] = None) -> List[Dict]:
        """Apply or remove naming to the current weld table data."""
        if config is not None:
            self.custom_rename_config = dict(self.custom_rename_config, **config)
        self.rename_mode = mode
        for index, item in enumerate(self.weld_points_data):
            self._ensure_original_fields(item)
            original_name = item["_OriginalName"]
            original_external_id = item["_OriginalExternalId"]
            if mode == "standard":
                geo_set_name = str(item.get("_OriginalGeoSetName") or "")
                name = (
                    self._generate_standard_name(original_name, geo_set_name)
                    if geo_set_name else original_name
                )
                item["Name"] = name
                item["ExternalId"] = name
            elif mode == "custom":
                name = self._custom_name(
                    original_name, index, self.custom_rename_config
                )
                item["Name"] = name
                item["ExternalId"] = name
            else:
                item["Name"] = original_name
                item["ExternalId"] = original_external_id
        return self.weld_points_data

    @staticmethod
    def _extract_biw_from_binary(doc, pycatia: bool) -> List[Dict]:
        """从已保存 CATProduct 的专有二进制中读取 BiW 坐标。

        这是单个 CATProduct 文件的兼容入口；正式提取会额外遍历当前 Product
        树中的嵌套 CATProduct。未识别的 CATProduct 版本会返回空列表。
        """
        path = CATIAWeldPointExtractor._get_document_path(doc, pycatia)
        if not path or not os.path.isfile(path):
            return []
        try:
            return CATProductBiWBinaryReader(path).read()
        except CATProductBinaryError:
            return []

    def _extract_biw_from_selection(self, selection, spa, pycatia: bool) -> int:
        """从被 CATIA 暴露为几何对象的 BiW 特征中读取坐标。

        不能把 ``CATGmoSearch.Point`` 作为 BiW 的兜底查询：它会把普通几何
        点也混入结果，且这些点和 Joint Element 没有一一对应关系。
        """
        def _search(query):
            if pycatia:
                selection.clear()
                selection.search(query)
                return selection.count2
            selection.Clear()
            selection.Search(query)
            return selection.Count2

        count = 0
        for query in (
            "Name=Joint Element*,all",
            "Type=BiW Fastener,all",
            "Type=BfmJointElement,all",
        ):
            try:
                count = _search(query)
            except Exception:
                count = 0
            if count:
                break

        if not count:
            return 0

        added = 0
        for i in range(1, count + 1):
            try:
                item = selection.item2(i) if pycatia else selection.Item2(i)
                obj = item.value if pycatia else item.Value
                name = getattr(obj, "name", f"Joint_{i}") if pycatia else getattr(obj, "Name", f"Joint_{i}")
                ref = item.reference if pycatia else item.Reference
                coords = self._get_measurable_point(spa, ref, pycatia)
                if coords is None:
                    continue
                x, y, z = coords
                self.weld_points_data.append({
                    "Class": "PmWeldPoint",
                    "ExternalId": name,
                    "Name": name,
                    "Location": f"{x:.3f},{y:.3f},{z:.3f}",
                    "X": round(x, 3),
                    "Y": round(y, 3),
                    "Z": round(z, 3),
                    "Type": "BiW",
                })
                added += 1
            except Exception:
                continue
        return added

    @staticmethod
    def _get_measurable_point(spa, reference, pycatia: bool):
        """返回 SPA GetPoint 的返回值。

        win32com 的 SAFEARRAY 输出参数不会可靠地回写到传入的 Python list；
        CATIA 的 COM 返回值才是实际坐标。旧实现忽略了这个返回值。
        """
        try:
            if pycatia:
                measurable = spa.get_measurable(reference)
                coords = measurable.get_point()
            else:
                measurable = spa.GetMeasurable(reference)
                coords = measurable.GetPoint([0.0, 0.0, 0.0])
            if not coords or len(coords) < 3:
                return None
            values = tuple(float(coords[n]) for n in range(3))
            if not all(abs(value) < 1e308 for value in values):
                return None
            return values
        except Exception:
            return None

    def _extract_biw_via_abf_export(self, doc, pycatia: bool):
        """调用 CATIA 的 ABF Export batch 并解析 neutral 文件。

        ABF 的 Joint Element 不属于公开 Automation 对象，因此这是获取其
        装配坐标的官方闭环。返回 ``(records, error_message)``。
        """
        product_path = self._get_document_path(doc, pycatia)
        if not product_path:
            return [], "BiW 提取失败：当前 CATProduct 尚未保存，无法调用 ABF 导出"

        app_path = self._get_application_path(pycatia)
        if not app_path:
            return [], "BiW 提取失败：无法定位 CATBatchStarter.exe"
        batch_starter = os.path.join(os.path.dirname(app_path), "CATBatchStarter.exe")
        if not os.path.isfile(batch_starter):
            return [], f"BiW 提取失败：找不到 CATBatchStarter.exe ({batch_starter})"

        try:
            with tempfile.TemporaryDirectory(prefix="pdps_biw_") as temp_dir:
                output_dir = os.path.join(temp_dir, "output")
                os.makedirs(output_dir, exist_ok=True)
                xml_path = os.path.join(temp_dir, "abf_export.xml")
                self._write_abf_batch_xml(xml_path, product_path, output_dir)
                result = subprocess.run(
                    [batch_starter, "-input", xml_path, "-output", temp_dir],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=180,
                    check=False,
                )

                output_files = []
                log_files = []
                for root, _, files in os.walk(temp_dir):
                    output_files.extend(
                        os.path.join(root, filename)
                        for filename in files
                        if filename.lower().endswith((".txt", ".tsv"))
                        and "log" not in filename.lower()
                    )
                    log_files.extend(
                        os.path.join(root, filename)
                        for filename in files
                        if "log" in filename.lower()
                        and filename.lower().endswith((".txt", ".xml"))
                    )
                for neutral_path in output_files:
                    records = self._parse_biw_neutral_file(neutral_path)
                    if records:
                        return records, ""

                diagnostics = "\n".join(
                    part for part in (result.stdout, result.stderr) if part
                )
                for log_path in log_files:
                    try:
                        with open(log_path, "r", encoding="utf-8", errors="replace") as stream:
                            diagnostics += "\n" + stream.read()
                    except OSError:
                        pass
                if "ABF LICENCE" in diagnostics.upper():
                    return [], (
                        "BiW 提取失败：CATIA 的 ABF Export 不可用。"
                        "请确认当前 CATIA 会话已取得 ABF 许可证；"
                        f"CATBatchStarter 返回码 {result.returncode}。"
                    )
                if result.returncode != 0:
                    return [], (
                        "BiW 提取失败：CATIA ABF Export 批处理失败，"
                        f"CATBatchStarter 返回码 {result.returncode}。"
                    )
                return [], "BiW 提取失败：ABF 导出未生成包含 LOCX/LOCY/LOCZ 的 neutral 文件"
        except subprocess.TimeoutExpired:
            return [], "BiW 提取失败：CATIA ABF 导出超时"
        except Exception as exc:
            return [], f"BiW 提取失败：ABF 导出异常：{exc}"

    @staticmethod
    def _get_document_path(doc, pycatia: bool) -> str:
        try:
            value = doc.full_name if pycatia else doc.FullName
            return str(value or "")
        except Exception:
            return ""

    def _get_application_path(self, pycatia: bool) -> str:
        try:
            value = self.catia_app.full_name if pycatia else self.catia_app.FullName
            return str(value or "")
        except Exception:
            return ""

    @staticmethod
    def _write_abf_batch_xml(xml_path: str, product_path: str, output_dir: str) -> None:
        """写入 CATBatchStarter 使用的 ABFExReport 参数文件。"""
        def esc(value):
            return xml_escape(str(value), {"\"": "&quot;"})

        args = [
            ("Process type (Exp,RepS,RepF)", "Exp"),
            ("Output file", "pdps_biw_export"),
            ("Include additional info (Yes,No)", "Yes"),
            ("Include comments (Yes,No)", "Yes"),
            ("Sort by process (Yes,No)", "No"),
            ("Translate coded values (Yes,No)", "No"),
            ("Recursive mode (Yes,No)", "No"),
            ("Inches unit (Yes=Inch,No=mm)", "No"),
            ("Discretization method (PolSag,PolStep)", "PolSag"),
            ("Discretization sag", "0.2"),
            ("Real numbers precision (Decimal digit number)", "6"),
            ("Scientific notation (Yes,No)", "No"),
        ]
        simple_args = "\n".join(
            f'      <simple_arg id="{esc(key)}" value="{esc(value)}"/>'
            for key, value in args
        )
        xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE root SYSTEM "Parameters.dtd" >
<root batch_name="ABFExReportDesc" user="" password="" env="" version="" licfile="">
  <inputParameters>
    <file id="Product file" destination="" filePath="{esc(product_path)}" type="bin" upLoadable="RightNow" automatic="1"/>
{simple_args}
  </inputParameters>
  <outputParameters>
    <folder id="Output directory" destination="" folderPath="{esc(output_dir)}" type="bin" upLoadable="RightNow" extension=".txt" automatic="1"/>
  </outputParameters>
</root>
'''
        with open(xml_path, "w", encoding="utf-8") as stream:
            stream.write(xml)

    @staticmethod
    def _parse_biw_neutral_file(path: str) -> List[Dict]:
        """解析 ABF neutral 的制表符字段，兼容键值行和表格行。"""
        raw = None
        for encoding in ("utf-8-sig", "gb18030", "cp1252"):
            try:
                with open(path, "r", encoding=encoding) as stream:
                    raw = stream.read()
                break
            except (UnicodeDecodeError, OSError):
                continue
        if not raw:
            return []

        records = []
        header_indexes = None
        aliases = {
            "id": {"ID", "JEID", "JOINT ELEMENT", "JOINTELEMENT"},
            "x": {"LOCX", "X", "LOCATIONX"},
            "y": {"LOCY", "Y", "LOCATIONY"},
            "z": {"LOCZ", "Z", "LOCATIONZ"},
        }

        def normalized(value):
            return re.sub(r"[^A-Z0-9]", "", value.upper())

        def to_float(value):
            match = re.search(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?", value)
            return float(match.group(0)) if match else None

        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped:
                header_indexes = None
                continue
            fields = [item.strip() for item in re.split(r"\t+", stripped)]
            normalized_fields = [normalized(item) for item in fields]

            # 某些版本输出 LOCX=...;LOCY=...;LOCZ=... 的键值行。
            key_values = {
                normalized(key): value
                for key, value in re.findall(r"([A-Za-z][A-Za-z0-9 _-]*)\s*[=:]\s*([^;\t,]+)", stripped)
            }
            if all(key in key_values for key in ("LOCX", "LOCY", "LOCZ")):
                x = to_float(key_values["LOCX"])
                y = to_float(key_values["LOCY"])
                z = to_float(key_values["LOCZ"])
                if x is not None and y is not None and z is not None:
                    raw_id = key_values.get("ID", key_values.get("JEID", ""))
                    records.append(CATIAWeldPointExtractor._biw_record(raw_id, x, y, z))
                continue

            found = {}
            for key, accepted in aliases.items():
                for index, field in enumerate(normalized_fields):
                    if field in accepted:
                        found[key] = index
                        break
            if all(key in found for key in ("x", "y", "z")):
                header_indexes = found
                continue

            if header_indexes is None:
                continue
            try:
                x = to_float(fields[header_indexes["x"]])
                y = to_float(fields[header_indexes["y"]])
                z = to_float(fields[header_indexes["z"]])
                raw_id = fields[header_indexes["id"]] if "id" in header_indexes else ""
                if x is not None and y is not None and z is not None:
                    records.append(CATIAWeldPointExtractor._biw_record(raw_id, x, y, z))
            except (IndexError, TypeError, ValueError):
                continue
        return records

    @staticmethod
    def _biw_record(raw_id, x: float, y: float, z: float) -> Dict:
        name = str(raw_id or "").strip()
        if name and not re.search(r"joint\s*element", name, re.IGNORECASE):
            name = f"Joint Element.{name}"
        if not name:
            name = "Joint Element"
        return {
            "Class": "PmWeldPoint",
            "ExternalId": name,
            "Name": name,
            "Location": f"{x:.3f},{y:.3f},{z:.3f}",
            "X": round(x, 3),
            "Y": round(y, 3),
            "Z": round(z, 3),
            "Type": "BiW",
        }

    def _generate_standard_name(self, original_name: str, geo_set_name: str) -> str:
        """
        生成标准命名：{前缀}_{零件数字段}_{焊点数字段}

        焊点名示例：  T1E-0000010296-RSW-GSMPoint.2
          → 前缀 = T1E，焊点 ID = 10296

        图形集名示例：DPUB-501000588-XAX&DPUB-501000592-XAX&DPUB-501001638-XAX
          → 取第一段 DPUB-501000588-XAX，零件 ID = 501000588（首个纯数字段）

        结果示例：T1E_501000588_10296
        """
        try:
            # ── 1. 提取前缀（焊点名第一个"-"之前）──────────────────────────
            orig_segs = original_name.split('-')
            prefix = orig_segs[0].strip() if orig_segs else "UNK"

            # ── 2. 提取零件 ID（从图形集名第一个 & 段中找首个纯数字段）──────
            part_id = ""
            if geo_set_name:
                # 取 & 之前的第一个零件名段，如 "DPUB-501000588-XAX"
                first_part_seg = geo_set_name.split('&')[0].strip()
                for seg in first_part_seg.split('-'):
                    seg_clean = seg.split('_')[0]       # 去掉下划线后缀，如 "XWS_00" → "XWS"
                    if seg_clean.isdigit():
                        part_id = seg_clean
                        break
                if not part_id:
                    # 降级：取第二段（不要求纯数字）
                    p_segs = first_part_seg.split('-')
                    part_id = p_segs[1].split('_')[0] if len(p_segs) > 1 else p_segs[0]

            if not part_id:
                part_id = re.sub(r'[^A-Za-z0-9]', '', geo_set_name)[:12] or "PART"

            # ── 3. 提取焊点数字（第二段去前导零）──────────────────────────
            weld_id = "0"
            if len(orig_segs) > 1:
                raw = orig_segs[1].strip()
                weld_id = raw.lstrip('0') or "0"

            return f"{prefix}_{part_id}_{weld_id}"
        except Exception:
            return original_name

    @staticmethod
    def _weld_type_from_location(location_name: str) -> str:
        """
        根据图形集名称中 '&' 的数量推断焊接层数类型：
          无 & 或 1 个 & → 双层板 → "2T"
          2 个 &         → 三层板 → "3T"
          N 个 &         → (N+1) 层板 → "{N+1}T"

        规则：N 个 & 连接 (N+1) 个零件，即 (N+1) 层板。
        无 & 时认为只有1个零件段，默认双层板（实际为单零件贴合场景取2T）。
        """
        count = location_name.count('&')
        layers = max(2, count + 1)   # 0 个 & → 1 个零件 → 默认 2T; 2 个 & → 3 个零件 → 3T
        return f"{layers}T"

    def _build_point_parent_map(self, part) -> Dict[str, str]:
        """
        预扫描零件的全部 HybridBodies（图形集）层级，
        构建 {点名称: 直接父图形集名称} 映射表。
        支持 win32com 和 pycatia 两种连接方式。
        """
        point_parent_map: Dict[str, str] = {}

        def _scan_body(hb, conn_type: str):
            """递归扫描一个 HybridBody 内的所有点和子图形集。"""
            try:
                if conn_type == 'pycatia':
                    hb_name = hb.name
                    shapes = hb.hybrid_shapes
                    shape_count = shapes.count
                    sub_bodies = hb.hybrid_bodies
                    sub_count = sub_bodies.count
                    def get_shape(j): return shapes.item(j)
                    def get_shape_name(s): return s.name
                    def get_sub(j): return sub_bodies.item(j)
                else:
                    hb_name = hb.Name
                    shapes = hb.HybridShapes
                    shape_count = shapes.Count
                    sub_bodies = hb.HybridBodies
                    sub_count = sub_bodies.Count
                    def get_shape(j): return shapes.Item(j)
                    def get_shape_name(s): return s.Name
                    def get_sub(j): return sub_bodies.Item(j)

                # 登记本 HybridBody 内直属的所有形状
                for j in range(1, shape_count + 1):
                    try:
                        shape = get_shape(j)
                        sname = get_shape_name(shape)
                        if sname:
                            point_parent_map[sname] = hb_name
                    except Exception:
                        pass

                # 递归处理子图形集（但子图形集内的点归属子图形集，不覆盖父级）
                for j in range(1, sub_count + 1):
                    try:
                        _scan_body(get_sub(j), conn_type)
                    except Exception:
                        pass

            except Exception as e:
                print(f"_scan_body error: {e}")

        try:
            if self.connection_type == 'pycatia':
                root_bodies = part.hybrid_bodies
                for i in range(1, root_bodies.count + 1):
                    _scan_body(root_bodies.item(i), 'pycatia')
            else:
                root_bodies = part.HybridBodies
                for i in range(1, root_bodies.Count + 1):
                    _scan_body(root_bodies.Item(i), 'win32com')
        except Exception as e:
            print(f"_build_point_parent_map error: {e}")

        return point_parent_map

    @staticmethod
    def _new_raw_part_in_product(catia, product_document):
        """Create and return a raw CATPart document inside a CATProduct."""
        raw_app = getattr(catia, "com_object", catia)
        raw_product = getattr(product_document, "Product", None)
        raw_products = getattr(raw_product, "Products", None)
        if raw_products is None:
            raise RuntimeError("当前 Product 不支持创建内部 Part")

        base_name = f"WeldPoint_Geometry_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        before_count = int(getattr(raw_app.Documents, "Count", 0) or 0)
        raw_products.AddNewComponent("Part", base_name)

        candidates = []
        for index in range(1, int(raw_app.Documents.Count) + 1):
            candidate = raw_app.Documents.Item(index)
            name = str(getattr(candidate, "Name", "") or "")
            if not name.lower().endswith(".catpart"):
                continue
            if index > before_count or name.casefold().startswith(base_name.casefold()):
                try:
                    if getattr(candidate, "Part", None) is not None:
                        candidates.append(candidate)
                except Exception:
                    continue
        if not candidates:
            raise RuntimeError("Product 内部 Part 已创建，但未找到对应 CATPart 文档")
        return candidates[-1]

    @classmethod
    def _generation_raw_part(cls, catia, document):
        """Return ``(document, part)`` for a writable CATPart target."""
        document_name = str(getattr(document, "Name", "") or "")
        window = getattr(catia, "ActiveWindow", None)
        window_name = str(getattr(window, "Name", "") or "")
        if window_name.lower().endswith(".3dxml"):
            raise RuntimeError("3DXML 是只读代理，无法写入几何体")
        if document_name.lower().endswith(".catpart"):
            part = getattr(document, "Part", None)
            if part is None:
                raise RuntimeError("当前 CATPart 没有 Part 对象")
            return document, part
        if document_name.lower().endswith(".catproduct"):
            new_document = cls._new_raw_part_in_product(catia, document)
            return new_document, new_document.Part
        raise RuntimeError(
            "生成几何体需要激活 CATPart 或 CATProduct；"
            f"当前活动文档为 {document_name or '未知文档'}"
        )

    # ---------------------------
    # 生成球体（带进度条对话框，CATIA 操作在后台线程）
    # ---------------------------
    def generate_spheres_from_points(self, parent_qt=None):
        """
        从焊点生成球体（win32com 版本为主）
        parent_qt: 可选，传入 QWidget (如 QMainWindow) 用于设置父窗口及更新 status_label
        """
        # 入口判断
        weld_points_data = self.get_data() if hasattr(self, "get_data") else self.weld_points_data
        if not weld_points_data:
            QMessageBox.warning(parent_qt, "警告", "没有焊点数据可生成球体")
            return

        # 参数对话框
        radius_str, ok = QInputDialog.getText(parent_qt, "球体半径", "请输入球体半径（mm）:", QLineEdit.Normal, "10")
        if not ok or not radius_str:
            return
        try:
            radius = float(radius_str)
        except ValueError:
            QMessageBox.critical(parent_qt, "参数错误", "请输入有效的半径数值")
            return

        name_prefix, ok = QInputDialog.getText(parent_qt, "名称前缀", "球体名称前缀:", QLineEdit.Normal, "SPHERE")
        if not ok:
            return
        name_prefix = name_prefix or "SPHERE"

        geom_set_name, ok = QInputDialog.getText(parent_qt, "几何集名称", "几何集名称:", QLineEdit.Normal,
                                                 "Geometry_Spheres")
        if not ok:
            return
        geom_set_name = geom_set_name or "Geometry_Spheres"

        # 创建进度对话框
        progress_dialog = QDialog(parent_qt)
        progress_dialog.setWindowTitle("生成球体")
        progress_dialog.resize(420, 160)
        progress_dialog.setWindowModality(Qt.WindowModal)  # 模态窗口，阻塞父窗口交互

        layout = QVBoxLayout(progress_dialog)

        title_lbl = QLabel(f"将生成 {len(weld_points_data)} 个球体")
        title_lbl.setFont(QFont("Microsoft YaHei", 10))
        layout.addWidget(title_lbl, alignment=Qt.AlignHCenter)

        progress_bar = QProgressBar()
        progress_bar.setRange(0, 100)
        progress_bar.setValue(0)
        layout.addWidget(progress_bar)

        status_lbl = QLabel("准备中...")
        status_lbl.setStyleSheet("color: gray;")
        layout.addWidget(status_lbl, alignment=Qt.AlignHCenter)

        cancel_btn = QPushButton("取消")
        cancel_btn.setFixedSize(100, 30)
        cancel_btn.setStyleSheet("background-color: #7f8c8d; color: white; border-radius: 4px;")
        layout.addWidget(cancel_btn, alignment=Qt.AlignHCenter)

        cancel_event = threading.Event()
        progress_q = queue.Queue()

        def worker():
            """在后台线程中执行 CATIA 操作"""
            try:
                import pythoncom
                pythoncom.CoInitialize()
                try:
                    import win32com.client
                    try:
                        catia = win32com.client.GetActiveObject("CATIA.Application")
                    except Exception:
                        catia = win32com.client.Dispatch("CATIA.Application")

                    doc = getattr(catia, "ActiveDocument", None)
                    if doc is None:
                        progress_q.put(("error", "没有活动的 CATIA 文档"))
                        return

                    try:
                        doc, part = self._generation_raw_part(catia, doc)
                    except Exception as exc:
                        progress_q.put(("error", str(exc)))
                        return

                    hsf = part.HybridShapeFactory
                    hybrid_bodies = part.HybridBodies

                    try:
                        catia.StartWorkbench("PartDesign")
                    except Exception:
                        pass

                    # 获取或创建几何集
                    target_body = None
                    try:
                        for i in range(1, hybrid_bodies.Count + 1):
                            body = hybrid_bodies.Item(i)
                            if body.Name == geom_set_name:
                                target_body = body
                                break
                        if target_body is None:
                            target_body = hybrid_bodies.Add()
                            target_body.Name = geom_set_name
                    except Exception as e:
                        progress_q.put(("error", f"无法获取或创建几何集: {str(e)}"))
                        return

                    try:
                        part.InWorkObject = target_body
                    except Exception:
                        pass

                    total = len(weld_points_data)
                    created_points = []

                    # 1) 批量创建点并 append 到几何集（不频繁 Update）
                    for idx, weld in enumerate(weld_points_data):
                        if cancel_event.is_set():
                            progress_q.put(("info", f"已取消，已创建 {len(created_points)} 个点"))
                            return
                        try:
                            x = float(weld.get("X", 0))
                            y = float(weld.get("Y", 0))
                            z = float(weld.get("Z", 0))
                        except Exception:
                            progress_q.put(("warn", f"第 {idx + 1} 个坐标解析失败，跳过"))
                            continue
                        try:
                            pt = hsf.AddNewPointCoord(x, y, z)
                            if pt is None:
                                progress_q.put(("warn", f"第 {idx + 1} 个点创建失败"))
                                continue
                            target_body.AppendHybridShape(pt)
                            created_points.append(pt)
                        except Exception as e:
                            progress_q.put(("warn", f"第 {idx + 1} 个点创建异常: {str(e)}"))
                            continue

                        if (idx + 1) % 10 == 0 or (idx + 1) == total:
                            progress_q.put(("progress", (idx + 1, total)))

                    try:
                        part.Update()
                    except Exception:
                        pass

                    # 2) 为每个点创建球体
                    success = 0
                    fail = 0
                    created_count = len(created_points)

                    for i, pt in enumerate(created_points):
                        if cancel_event.is_set():
                            progress_q.put(("info", f"已取消，已创建 {success} 个球体"))
                            return
                        try:
                            pt_ref = part.CreateReferenceFromObject(pt)
                            try:
                                sphere = hsf.AddNewSphere(pt_ref, None, radius, -90.0, 90.0, 0.0, 360.0)
                            except Exception:
                                sphere = hsf.AddNewSphere(pt_ref, None, radius)
                            if sphere is None:
                                fail += 1
                                continue
                            try:
                                target_body.AppendHybridShape(sphere)
                            except Exception:
                                pass
                            try:
                                sphere.Name = f"{name_prefix}_{i + 1}"
                            except Exception:
                                pass
                            success += 1

                            if i % 10 == 0:
                                try:
                                    doc.Windows.Item(1).Refresh()
                                except Exception:
                                    pass
                            progress_q.put(("progress", (created_count + i + 1, created_count * 2)))
                        except Exception:
                            fail += 1
                            continue

                    try:
                        part.Update()
                        doc.Windows.Item(1).Refresh()
                    except Exception:
                        pass

                    progress_q.put(("done", (success, fail)))

                finally:
                    pythoncom.CoUninitialize()
            except Exception:
                progress_q.put(("error", traceback.format_exc()))

        t = threading.Thread(target=worker, daemon=True)
        t.start()

        poll_timer = QTimer()

        def poll():
            try:
                while not progress_q.empty():
                    typ, val = progress_q.get_nowait()
                    if typ == "progress":
                        cur, tot = val
                        pct = int(min(100.0, (cur / max(1, tot)) * 100.0))
                        progress_bar.setValue(pct)
                        status_lbl.setText(f"处理中: {cur}/{tot}")
                        if parent_qt and hasattr(parent_qt, "status_label"):
                            parent_qt.status_label.setText(f"生成中: {cur}/{tot}")

                    elif typ == "warn":
                        print("WARN:", val)

                    elif typ == "info":
                        QMessageBox.information(progress_dialog, "信息", val)
                        if parent_qt and hasattr(parent_qt, "status_label"):
                            parent_qt.status_label.setText(val)
                        progress_dialog.accept()

                    elif typ == "error":
                        QMessageBox.critical(progress_dialog, "生成球体失败", val)
                        status_lbl.setText("生成失败")
                        if parent_qt and hasattr(parent_qt, "status_label"):
                            parent_qt.status_label.setText("生成失败")
                        progress_dialog.accept()

                    elif typ == "done":
                        success, fail = val
                        QMessageBox.information(progress_dialog, "完成", f"已生成 {success} 个球体，失败 {fail} 个")
                        status_lbl.setText(f"完成: 成功 {success}，失败 {fail}")
                        if parent_qt and hasattr(parent_qt, "status_label"):
                            parent_qt.status_label.setText(f"完成: 成功 {success}，失败 {fail}")
                        progress_dialog.accept()
            except Exception:
                pass

            if not t.is_alive():
                poll_timer.stop()

        def on_cancel():
            reply = QMessageBox.question(progress_dialog, "取消", "确定要取消生成吗？",
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes:
                cancel_event.set()
                status_lbl.setText("正在取消...")
                cancel_btn.setEnabled(False)  # 防重复点击

        cancel_btn.clicked.connect(on_cancel)

        poll_timer.timeout.connect(poll)
        poll_timer.start(200)
        progress_dialog.exec_()

    def log_error(self, context, error):
        print(f"Error in {context}: {error}")
        traceback.print_exc()
        self.status_message = f"错误: {context}"
