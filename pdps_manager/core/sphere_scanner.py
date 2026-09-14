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
        self.connection_type = None
        self.sphere_data: List[Dict] = []

    def set_catia_app(self, catia_app, connection_type=None):
        self.catia_app = catia_app
        if connection_type is not None:
            self.connection_type = connection_type

    @staticmethod
    def _raw(value):
        return getattr(value, "com_object", value)

    def _raw_app(self):
        return self._raw(self.catia_app)

    def _active_raw_document(self):
        app = self._raw_app()
        return getattr(app, "ActiveDocument", None)

    @staticmethod
    def _document_name(document) -> str:
        return str(getattr(document, "Name", getattr(document, "name", "")) or "")

    @staticmethod
    def _collection_count(collection) -> int:
        try:
            return int(collection.Count)
        except Exception:
            try:
                return int(collection.count)
            except Exception:
                return 0

    @staticmethod
    def _collection_item(collection, index):
        try:
            return collection.Item(index)
        except Exception:
            return collection.item(index)

    @classmethod
    def _get_collection(cls, obj, *names):
        for name in names:
            try:
                value = getattr(obj, name)
                if value is not None:
                    return value
            except Exception:
                continue
        return None

    def _part_context(self, raw_document):
        """Return ``(part, spa_workbench, use_pycatia)`` for a CATPart document."""
        if self.connection_type == "pycatia":
            if PartDocument is None:
                raise RuntimeError("当前连接需要 pycatia 才能访问 CATPart")
            part_document = PartDocument(raw_document)
            return part_document.part, part_document.spa_workbench(), True

        part = getattr(raw_document, "Part", None)
        if part is None:
            raise RuntimeError("未找到 CATPart 对象")
        return part, raw_document.GetWorkbench("SPAWorkbench"), False

    @staticmethod
    def _identity_transform():
        return (1.0, 0.0, 0.0, 0.0, 1.0, 0.0,
                0.0, 0.0, 1.0, 0.0, 0.0, 0.0)

    @staticmethod
    def _numeric_sequence(value):
        if value is None:
            return None
        try:
            values = [float(item) for item in value]
        except (TypeError, ValueError):
            return None
        return values

    @classmethod
    def _product_position(cls, instance):
        """Read a CATIA Product.Position as 9 rotation + 3 translation values."""
        try:
            position = getattr(instance, "Position")
        except Exception:
            position = None
        if position is None:
            try:
                position = getattr(instance, "position")
            except Exception:
                return cls._identity_transform()

        values = [0.0] * 12
        for method_name in ("GetComponents", "get_components"):
            try:
                method = getattr(position, method_name)
                # pycatia 的 get_components() 无参数并直接返回 tuple；
                # win32com 的 GetComponents 通常需要传入数组，但部分版本
                # 会返回全零 tuple 且不回写数组，因此两种返回方式都校验。
                try:
                    result = method() if method_name == "get_components" else method(values)
                except TypeError:
                    result = method(values)
                candidate = cls._numeric_sequence(result)
                if candidate is None:
                    candidate = cls._numeric_sequence(values)
                if (
                    candidate and len(candidate) >= 12
                    and any(abs(value) > 1e-12 for value in candidate[:9])
                ):
                    return tuple(candidate[:12])
            except Exception:
                continue
        return cls._identity_transform()

    @staticmethod
    def _compose_transform(parent, child):
        """Compose CATIA transforms in row-major 3x3 + translation form."""
        p = parent
        c = child
        pr = (p[0:3], p[3:6], p[6:9])
        cr = (c[0:3], c[3:6], c[6:9])
        rotation = tuple(
            sum(pr[row][k] * cr[k][col] for k in range(3))
            for row in range(3) for col in range(3)
        )
        translation = tuple(
            sum(pr[row][k] * c[9 + k] for k in range(3)) + p[9 + row]
            for row in range(3)
        )
        return rotation + translation

    @staticmethod
    def _transform_point(point, transform):
        x, y, z = point
        return (
            transform[0] * x + transform[1] * y + transform[2] * z + transform[9],
            transform[3] * x + transform[4] * y + transform[5] * z + transform[10],
            transform[6] * x + transform[7] * y + transform[8] * z + transform[11],
        )

    def _iter_part_sources(self):
        """Yield every CATPart in the active Part or Product.

        A Product instance is yielded separately, so repeated CATParts with
        different placements are not accidentally collapsed.  Centers measured
        in the CATPart coordinate system are transformed into the root Product
        coordinate system.
        """
        document = self._active_raw_document()
        if document is None:
            raise RuntimeError("没有活动的 CATIA 文档")
        document_name = self._document_name(document)
        if document_name.lower().endswith(".catpart"):
            part = getattr(document, "Part", None)
            if part is not None:
                yield {
                    "document": document,
                    "part": part,
                    "instance_path": document_name,
                    "transform": self._identity_transform(),
                }
            return
        if not document_name.lower().endswith(".catproduct"):
            raise RuntimeError(
                "读取几何体需要激活 CATPart 或 CATProduct；"
                f"当前活动文档为 {document_name or '未知文档'}"
            )

        root_product = getattr(document, "Product", None)
        if root_product is None:
            raise RuntimeError("当前 CATProduct 没有 Product 对象")

        def visit(product, parent_transform, parent_path, depth):
            if depth > 100:
                return
            products = self._get_collection(product, "Products", "products")
            for index in range(1, self._collection_count(products) + 1):
                try:
                    instance = self._collection_item(products, index)
                except Exception:
                    continue
                name = str(getattr(instance, "Name", getattr(instance, "name", f"Part_{index}")) or "")
                path = f"{parent_path}/{name}" if parent_path else name
                transform = self._compose_transform(
                    parent_transform, self._product_position(instance)
                )
                reference_doc = None
                try:
                    reference = getattr(instance, "ReferenceProduct")
                    reference_doc = getattr(reference, "Parent", None)
                except Exception:
                    pass
                if reference_doc is not None:
                    part = getattr(reference_doc, "Part", None)
                    if part is not None:
                        yield {
                            "document": reference_doc,
                            "part": part,
                            "instance_path": path,
                            "transform": transform,
                        }
                yield from visit(instance, transform, path, depth + 1)

        yield from visit(root_product, self._identity_transform(), document_name, 0)

    @staticmethod
    def _measure_cog(measurable, use_pycatia):
        if use_pycatia:
            return tuple(float(value) for value in measurable.get_cog())
        for method_name in ("GetCOG", "GetCog"):
            try:
                method = getattr(measurable, method_name)
                result = method([0.0, 0.0, 0.0])
                values = result if result is not None else [0.0, 0.0, 0.0]
                return tuple(float(value) for value in values[:3])
            except Exception:
                continue
        raise RuntimeError("SPAWorkbench 无法获取几何重心")

    @classmethod
    def _measure_volume(cls, measurable, use_pycatia):
        for name in (("volume",) if use_pycatia else ("Volume", "volume")):
            try:
                value = getattr(measurable, name)
                if callable(value):
                    value = value()
                return float(value)
            except Exception:
                continue
        return 0.0

    @classmethod
    def _part_reference(cls, part, obj, use_pycatia):
        if use_pycatia:
            return part.create_reference_from_object(obj)
        return part.CreateReferenceFromObject(obj)

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
        document = self._active_raw_document()
        document_name = self._document_name(document)
        # Product 中的选择对象可能属于多个实例，CATIA 的 SPA 接口通常只
        # 把它们暴露为 Product 代理。此时直接扫描全部 Part，保证不漏读。
        if document_name.lower().endswith(".catproduct"):
            return self.scan_all_bodies()
        try:
            if self.connection_type == "pycatia":
                document_api = self.catia_app.active_document
            else:
                document_api = document
            part_doc = PartDocument(document) if self.connection_type == "pycatia" else None
            part = part_doc.part if part_doc else document.Part
            selection = document_api.selection if self.connection_type == "pycatia" else document.Selection
            spa_workbench = (
                part_doc.spa_workbench()
                if part_doc else document.GetWorkbench("SPAWorkbench")
            )
            count = selection.count if self.connection_type == "pycatia" else selection.Count
            if count == 0:
                return []
            self.sphere_data = []
            for i in range(1, count + 1):
                try:
                    selected_obj = selection.item(i).value
                    obj_name = getattr(selected_obj, 'name', f"Object_{i}")
                    reference = self._part_reference(part, selected_obj, self.connection_type == "pycatia")
                    measurable = (
                        spa_workbench.get_measurable(reference)
                        if self.connection_type == "pycatia"
                        else spa_workbench.GetMeasurable(reference)
                    )
                    cog = self._measure_cog(measurable, self.connection_type == "pycatia")
                    volume = self._measure_volume(measurable, self.connection_type == "pycatia")
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
            self.sphere_data = []
            for source in self._iter_part_sources():
                try:
                    part, spa_workbench, use_pycatia = self._part_context(source["document"])
                    bodies = self._get_collection(part, "bodies", "Bodies")
                    for i in range(1, self._collection_count(bodies) + 1):
                        body = self._collection_item(bodies, i)
                        body_name = str(getattr(body, "name", getattr(body, "Name", f"Body_{i}")) or "")
                        if search_keywords and not any(
                            kw.lower() in body_name.lower() for kw in search_keywords
                        ):
                            continue
                        try:
                            reference = self._part_reference(part, body, use_pycatia)
                            measurable = (
                                spa_workbench.get_measurable(reference)
                                if use_pycatia else spa_workbench.GetMeasurable(reference)
                            )
                            local_cog = self._measure_cog(measurable, use_pycatia)
                            cog = self._transform_point(local_cog, source["transform"])
                            volume = self._measure_volume(measurable, use_pycatia)
                            self.sphere_data.append({
                                "Name": f"{source['instance_path']}/{body_name}",
                                "X": f"{cog[0]:.2f}",
                                "Y": f"{cog[1]:.2f}",
                                "Z": f"{cog[2]:.2f}",
                                "Radius": f"{(volume * 3 / (4 * 3.14)) ** (1 / 3):.2f}" if volume > 0 else "0",
                                "Volume": round(volume, 3),
                                "Type": "Body",
                                "Source": "CATIA Part Body",
                                "_coords": cog,
                            })
                        except Exception as exc:
                            print(f"无法测量 {source['instance_path']}/{body_name}: {exc}")
                except Exception as exc:
                    print(f"无法读取 Part {source.get('instance_path', '')}: {exc}")
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
        count = self._collection_count(bodies)
        for index in range(1, count + 1):
            body = self._collection_item(bodies, index)
            name = str(getattr(body, "name", getattr(body, "Name", "GeometrySet")) or "GeometrySet")
            path = f"{parent_path}/{name}" if parent_path else name
            yield path, body
            children = self._get_collection(body, "hybrid_bodies", "HybridBodies")
            if children is not None:
                yield from self._iter_hybrid_bodies(children, path)

    def scan_geometrical_set_spheres(self) -> List[Dict]:
        """Scan CATIA HybridShapeSphere features in all geometrical sets."""
        if not self.catia_app:
            raise RuntimeError("CATIA 未连接")
        try:
            result = []
            for source in self._iter_part_sources():
                try:
                    part, spa_workbench, use_pycatia = self._part_context(source["document"])
                    root_bodies = self._get_collection(part, "hybrid_bodies", "HybridBodies")
                    body_iter = self._iter_hybrid_bodies(root_bodies)
                except Exception as exc:
                    print(f"无法读取 Part {source.get('instance_path', '')}: {exc}")
                    continue
                for body_path, body in body_iter:
                    try:
                        shapes = self._get_collection(body, "hybrid_shapes", "HybridShapes")
                        shape_count = self._collection_count(shapes)
                    except Exception:
                        continue
                    for index in range(1, shape_count + 1):
                        try:
                            shape = self._collection_item(shapes, index)
                            type_name = self._type_name(shape).lower()
                            if "hybridshapesphere" not in type_name:
                                continue
                            reference = self._part_reference(part, shape, use_pycatia)
                            measurable = (
                                spa_workbench.get_measurable(reference)
                                if use_pycatia else spa_workbench.GetMeasurable(reference)
                            )
                            local_center = self._measure_cog(measurable, use_pycatia)
                            center = self._transform_point(local_center, source["transform"])
                            radius = None
                            for radius_name in (("radius",) if use_pycatia else ("Radius", "radius")):
                                try:
                                    radius = self._numeric_value(getattr(shape, radius_name))
                                    if radius is not None:
                                        break
                                except Exception:
                                    continue
                            # pycatia 将部分 HybridShapeSphere 包装成通用
                            # HybridShape，包装层没有 radius 属性；此时从
                            # 底层 COM 的 Radius.Value 读取真实半径。
                            if radius is None:
                                try:
                                    raw_shape = self._raw(shape)
                                    radius = self._numeric_value(raw_shape.Radius)
                                except Exception:
                                    radius = None
                            if radius is None or radius < 0:
                                continue
                            shape_name = str(getattr(shape, "name", getattr(shape, "Name", f"Sphere_{index}")))
                            result.append({
                                "Name": f"{source['instance_path']}/{body_path}/{shape_name}",
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

    def _new_raw_part_in_active_product(self):
        """Create and return the raw CATPart document inside the active Product."""
        raw_app = self._raw_app()
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
        return candidates[-1]

    def _new_part_in_active_product(self):
        """Create and return a pycatia PartDocument inside the active Product."""
        raw_document = self._new_raw_part_in_active_product()
        if PartDocument is None:
            raise RuntimeError("当前连接需要 pycatia 才能访问新建 CATPart")
        return PartDocument(raw_document)

    def _generation_part_context(self):
        """Return the writable Part context for a Part or Product document."""
        raw_document = self._active_raw_document()
        document_name = self._document_name(raw_document)
        raw_app = self._raw_app()
        window_name = str(getattr(getattr(raw_app, "ActiveWindow", None), "Name", "") or "")
        if window_name.lower().endswith(".3dxml"):
            raise RuntimeError("3DXML 是只读代理，无法写入点")
        if document_name.lower().endswith(".catproduct"):
            raw_document = self._new_raw_part_in_active_product()
        elif not document_name.lower().endswith(".catpart"):
            raise RuntimeError(
                "生成重心点需要激活 CATPart 或 CATProduct；"
                f"当前活动文档为 {document_name or '未知文档'}"
            )
        return self._part_context(raw_document)

    def _generation_part_document(self):
        raw_document = self._active_raw_document()
        document_name = self._document_name(raw_document)
        raw_app = self._raw_app()
        window_name = str(getattr(getattr(raw_app, "ActiveWindow", None), "Name", "") or "")
        if window_name.lower().endswith(".3dxml"):
            raise RuntimeError(
                "3DXML 是只读代理，无法写入点；请激活 CATProduct 或 CATPart"
            )
        if document_name.lower().endswith(".catpart"):
            if self.connection_type == "pycatia":
                return PartDocument(raw_document)
            return raw_document
        if document_name.lower().endswith(".catproduct"):
            raw_part_document = self._new_raw_part_in_active_product()
            if self.connection_type == "pycatia":
                return PartDocument(raw_part_document)
            return raw_part_document
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
            part, _, use_pycatia = self._generation_part_context()
            hs_factory = self._get_collection(part, "hybrid_shape_factory", "HybridShapeFactory")
            hybrid_bodies = self._get_collection(part, "hybrid_bodies", "HybridBodies")
            target_hb = None
            for index in range(1, self._collection_count(hybrid_bodies) + 1):
                candidate = self._collection_item(hybrid_bodies, index)
                candidate_name = str(getattr(candidate, "name", getattr(candidate, "Name", "")) or "")
                if candidate_name == geometrical_set_name:
                    target_hb = candidate
                    break
            if target_hb is None:
                target_hb = (
                    hybrid_bodies.add()
                    if use_pycatia else hybrid_bodies.Add()
                )
                if use_pycatia:
                    target_hb.name = geometrical_set_name
                else:
                    target_hb.Name = geometrical_set_name
            for data in data_rows:
                coords = data.get("_coords")
                if coords is None:
                    coords = (data["X"], data["Y"], data["Z"])
                x, y, z = (float(value) for value in coords)
                new_point = (
                    hs_factory.add_new_point_coord(x, y, z)
                    if use_pycatia else hs_factory.AddNewPointCoord(x, y, z)
                )
                point_name = f"COG_{data.get('Name', 'Point')}"
                if use_pycatia:
                    new_point.name = point_name
                    target_hb.append_hybrid_shape(new_point)
                else:
                    new_point.Name = point_name
                    target_hb.AppendHybridShape(new_point)
            if use_pycatia:
                part.update()
            else:
                part.Update()
            return len(data_rows)
        except Exception as e:
            print(f"生成点失败: {e}")
            raise RuntimeError(f"生成重心点失败：{e}") from e

    def get_data(self):
        return self.sphere_data
