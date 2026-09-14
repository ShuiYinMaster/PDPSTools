# -*- coding: utf-8 -*-
"""CATIASphereScanner —— 几何体重心(COG)扫描与重心点生成。"""

import traceback
from datetime import datetime
from typing import List, Dict, Optional

from ..config import PartDocument
from .threedxml_center_extractor import extract_3dxml_centers


class CATIASphereScanner:
    def __init__(self, catia_app=None):
        self.catia_app = catia_app
        self.sphere_data: List[Dict] = []

    def set_catia_app(self, catia_app):
        self.catia_app = catia_app

    def scan_3dxml(
        self,
        file_path: str,
        include_internal: bool = False,
        max_radius_mm: float = 10.0,
    ) -> List[Dict]:
        """Read shape-center candidates from a CATIA 3DXML file.

        CATIA exposes binary 3DXML as a Product proxy, so the normal Part/Spa
        route cannot measure it.  The parser is deliberately independent from
        the CATIA connection and reads the associated 3DRep entry instead.
        """

        self.sphere_data = extract_3dxml_centers(
            file_path,
            include_internal=include_internal,
            max_radius_mm=max_radius_mm,
        )
        for item in self.sphere_data:
            item.setdefault("_source_path", file_path)
        return self.sphere_data

    def scan_selected_objects(self) -> List[Dict]:
        if not self.catia_app:
            raise Exception("CATIA 未连接")
        try:
            document = self.catia_app.active_document
            part_doc = PartDocument(document.com_object)
            part = part_doc.part
            selection = document.selection
            spa_workbench = part_doc.spa_workbench()
            count = selection.count
            if count == 0:
                return []
            self.sphere_data = []
            for i in range(1, count + 1):
                try:
                    selected_obj = selection.item(i).value
                    obj_name = getattr(selected_obj, 'name', f"Object_{i}")
                    reference = part.create_reference_from_object(selected_obj)
                    measurable = spa_workbench.get_measurable(reference)
                    cog = measurable.get_cog()
                    try:
                        volume = measurable.volume
                    except Exception:
                        volume = 0
                    radius = (volume * 3 / (4 * 3.14159265359)) ** (1 / 3) if volume > 0 else 0
                    self.sphere_data.append({
                        "Name": obj_name,
                        "X": f"{cog[0]:.2f}",
                        "Y": f"{cog[1]:.2f}",
                        "Z": f"{cog[2]:.2f}",
                        "Radius": f"{radius:.2f}",
                        "Volume": round(volume, 3),
                        "Type": "实体" if volume > 0 else "几何图形",
                        "_coords": cog
                    })
                except Exception as e:
                    print(f"无法测量对象 {i}: {e}")
            return self.sphere_data
        except Exception:
            traceback.print_exc()
            return []

    def scan_all_bodies(self, search_keywords=None):
        if not self.catia_app:
            raise Exception("CATIA 未连接")
        try:
            document = self.catia_app.active_document
            part_doc = PartDocument(document.com_object)
            part = part_doc.part
            spa_workbench = part_doc.spa_workbench()
            self.sphere_data = []
            bodies = part.bodies
            for i in range(1, bodies.count + 1):
                body = bodies.item(i)
                if search_keywords:
                    if not any(kw.lower() in body.name.lower() for kw in search_keywords):
                        continue
                try:
                    reference = part.create_reference_from_object(body)
                    measurable = spa_workbench.get_measurable(reference)
                    cog = measurable.get_cog()
                    volume = measurable.volume
                    self.sphere_data.append({
                        "Name": body.name,
                        "X": f"{cog[0]:.2f}",
                        "Y": f"{cog[1]:.2f}",
                        "Z": f"{cog[2]:.2f}",
                        "Radius": f"{(volume * 3 / (4 * 3.14)) ** (1 / 3):.2f}" if volume > 0 else "0",
                        "Volume": round(volume, 3),
                        "Type": "Body",
                        "_coords": cog
                    })
                except Exception:
                    continue
            return self.sphere_data
        except Exception:
            traceback.print_exc()
            return []

    @staticmethod
    def _type_name(obj) -> str:
        """Return the CATIA automation type name when available."""
        raw = getattr(obj, "com_object", obj)
        try:
            return str(raw._oleobj_.GetTypeInfo().GetDocumentation(-1)[0] or "")
        except Exception:
            return type(obj).__name__

    @staticmethod
    def _numeric_value(value) -> Optional[float]:
        for attr in ("value", "Value"):
            try:
                candidate = getattr(value, attr)
                if callable(candidate):
                    candidate = candidate()
                return float(candidate)
            except Exception:
                continue
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _iter_hybrid_bodies(self, bodies, parent_path=""):
        try:
            count = bodies.count
        except Exception:
            count = bodies.Count
        for index in range(1, count + 1):
            body = bodies.item(index) if hasattr(bodies, "item") else bodies.Item(index)
            name = str(getattr(body, "name", getattr(body, "Name", "GeometrySet")))
            path = f"{parent_path}/{name}" if parent_path else name
            yield path, body
            try:
                children = body.hybrid_bodies
            except Exception:
                try:
                    children = body.HybridBodies
                except Exception:
                    children = None
            if children is not None:
                yield from self._iter_hybrid_bodies(children, path)

    def scan_geometrical_set_spheres(self) -> List[Dict]:
        """Scan CATIA HybridShapeSphere features in all geometrical sets."""
        if not self.catia_app:
            raise RuntimeError("CATIA 未连接")
        try:
            document = self.catia_app.active_document
            document_name = str(getattr(document, "name", "") or "")
            if not document_name.lower().endswith(".catpart"):
                raise RuntimeError(
                    "扫描几何图形集中的球体需要激活 CATPart；"
                    f"当前活动文档为 {document_name or '未知文档'}"
                )
            part_doc = PartDocument(document.com_object)
            part = part_doc.part
            spa_workbench = part_doc.spa_workbench()
            result = []
            for body_path, body in self._iter_hybrid_bodies(part.hybrid_bodies):
                try:
                    shapes = body.hybrid_shapes
                    shape_count = shapes.count
                except Exception:
                    continue
                for index in range(1, shape_count + 1):
                    try:
                        shape = shapes.item(index)
                        type_name = self._type_name(shape).lower()
                        if "hybridshapesphere" not in type_name:
                            continue
                        reference = part.create_reference_from_object(shape)
                        measurable = spa_workbench.get_measurable(reference)
                        center = tuple(float(value) for value in measurable.get_cog())
                        radius = None
                        try:
                            radius = self._numeric_value(shape.radius)
                        except Exception:
                            radius = self._numeric_value(shape.com_object.Radius)
                        if radius is None or radius < 0:
                            continue
                        shape_name = str(getattr(shape, "name", f"Sphere_{index}"))
                        result.append({
                            "Name": f"{body_path}/{shape_name}",
                            "X": f"{center[0]:.3f}",
                            "Y": f"{center[1]:.3f}",
                            "Z": f"{center[2]:.3f}",
                            "Radius": f"{radius:.3f}",
                            "Volume": 4.0 / 3.0 * 3.141592653589793 * radius ** 3,
                            "Type": "CATIA几何图形集球体",
                            "Source": "CATIA HybridShapeSphere",
                            "GeometryType": "球体",
                            "WeldLayers": "两层板焊点",
                            "VertexCount": 0,
                            "_coords": center,
                            "_radius": radius,
                        })
                    except Exception as exc:
                        print(f"读取几何图形集球体 {body_path}#{index} 失败: {exc}")
            self.sphere_data = result
            return result
        except Exception:
            traceback.print_exc()
            raise

    def _new_part_in_active_product(self):
        """Create and return a pycatia PartDocument inside the active Product."""
        raw_app = getattr(self.catia_app, "com_object", self.catia_app)
        raw_doc = getattr(raw_app, "ActiveDocument", None)
        if raw_doc is None:
            raise RuntimeError("无法获取 CATIA 活动文档")
        raw_product = getattr(raw_doc, "Product", None)
        raw_products = getattr(raw_product, "Products", None)
        if raw_products is None:
            raise RuntimeError("当前 Product 不支持创建内部 Part")

        base_name = f"Geometry_COG_Points_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        before_count = raw_app.Documents.Count
        raw_products.AddNewComponent("Part", base_name)

        candidates = []
        for index in range(1, raw_app.Documents.Count + 1):
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
        return PartDocument(candidates[-1])

    def _generation_part_document(self):
        document = self.catia_app.active_document
        document_name = str(getattr(document, "name", "") or "")
        raw_app = getattr(self.catia_app, "com_object", self.catia_app)
        window_name = str(getattr(getattr(raw_app, "ActiveWindow", None), "Name", "") or "")
        if window_name.lower().endswith(".3dxml"):
            raise RuntimeError(
                "3DXML 是只读代理，无法写入点；请激活 CATProduct 或 CATPart"
            )
        if document_name.lower().endswith(".catpart"):
            return PartDocument(document.com_object)
        if document_name.lower().endswith(".catproduct"):
            return self._new_part_in_active_product()
        raise RuntimeError(
            "生成重心点需要激活 CATProduct 或 CATPart；"
            f"当前活动文档为 {document_name or '未知文档'}"
        )

    def generate_points_at_centers(
        self,
        geometrical_set_name="Geometry_COG_Points",
        points_data: Optional[List[Dict]] = None,
    ) -> int:
        """Create CATIA points from the supplied rows or scanner data.

        ``points_data`` is used by the UI to pass the current table contents,
        so edits made in the table are authoritative.  The old behavior is
        retained when the argument is omitted for callers outside the UI.
        """

        data_rows = list(self.sphere_data if points_data is None else points_data)
        if not data_rows:
            return 0
        try:
            part_doc = self._generation_part_document()
            part = part_doc.part
            hs_factory = part.hybrid_shape_factory
            hybrid_bodies = part.hybrid_bodies
            try:
                target_hb = hybrid_bodies.item(geometrical_set_name)
            except Exception:
                target_hb = hybrid_bodies.add()
                target_hb.name = geometrical_set_name
            for data in data_rows:
                coords = data.get("_coords")
                if coords is None:
                    coords = (data["X"], data["Y"], data["Z"])
                x, y, z = (float(value) for value in coords)
                new_point = hs_factory.add_new_point_coord(x, y, z)
                new_point.name = f"COG_{data.get('Name', 'Point')}"
                target_hb.append_hybrid_shape(new_point)
            part.update()
            return len(data_rows)
        except Exception as e:
            print(f"生成点失败: {e}")
            raise RuntimeError(f"生成重心点失败：{e}") from e

    def get_data(self):
        return self.sphere_data
