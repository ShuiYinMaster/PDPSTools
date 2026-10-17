"""Transactional CATIA point/symbol creation in CATPart or Product proxies.

CATIA may expose a 3DXML window as a normal ProductDocument. Test capabilities,
not the window suffix. Never save the imported source or launch another CATIA.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4

from .stud_point_extractor import CATIAStudPointExtractor
from .weld_layers import weld_layer, layer_counts


COLORS = {2: (37, 99, 235), 3: (234, 138, 35), 4: (17, 149, 111)}


def raw(value):
    return getattr(value, "com_object", value)


def attribute(obj, name, default=None):
    try:
        return getattr(obj, name)
    except Exception:
        return default


def document_token(document):
    return (str(attribute(document, "Name", "")), str(attribute(document, "FullName", "")))


def validate_rows(rows):
    output = []
    for index, row in enumerate(rows, 1):
        try:
            xyz = tuple(float(row[axis]) for axis in ("X", "Y", "Z")) if all(
                axis in row for axis in ("X", "Y", "Z")) else tuple(float(v) for v in row["_coords"])
            if len(xyz) != 3 or not all(map(math.isfinite, xyz)):
                raise ValueError("坐标必须是三个有限数值")
            if str(row.get("Unit", "mm")).strip().casefold() != "mm":
                raise ValueError("生成前请将坐标单位统一为 mm")
        except (KeyError, ValueError, TypeError, OverflowError) as exc:
            raise ValueError(f"第 {index} 行 {row.get('Name', '')} 坐标无效：{exc}") from exc
        output.append((row, xyz, weld_layer(row)))
    return output


@dataclass
class GenerationTarget:
    app: object
    document: object
    part: object
    source_document: object
    products: object = None
    component: object = None
    component_name: str = ""
    allocated: list = field(default_factory=list)
    original_work: object = None
    placement: tuple = None

    def local(self, xyz):
        if self.component is None:
            return xyz
        if self.placement is None:
            self.placement = CATIAStudPointExtractor._components(self.component)
        matrix = self.placement
        delta = [xyz[a]-matrix[9+a] for a in range(3)]
        return tuple(sum(matrix[col*3+row]*delta[row] for row in range(3)) for col in range(3))

    def restore(self):
        if self.original_work is not None:
            try:
                self.part.InWorkObject = self.original_work
            except Exception:
                pass
        try:
            self.source_document.Activate()
        except Exception:
            pass

    def rollback(self):
        if self.products is not None:
            _remove_component(self.products, self.component_name)
            alerts = attribute(self.app, "DisplayFileAlerts", True)
            try:
                self.app.DisplayFileAlerts = False
                self.document.Close()
            finally:
                self.app.DisplayFileAlerts = alerts
        elif self.allocated:
            selection = self.document.Selection
            selection.Clear()
            try:
                for obj in self.allocated:
                    selection.Add(obj)
                selection.Delete()
            finally:
                selection.Clear()
            self.part.Update()


def _remove_component(products, name):
    matches = [i for i in range(1, int(products.Count)+1)
               if str(attribute(products.Item(i), "Name", "")) == name]
    if len(matches) != 1:
        raise RuntimeError(f"无法唯一定位本次新建组件 {name}，未删除其他组件")
    products.Remove(matches[0])


def create_part_in_product(catia, document, prefix="WeldGeometry"):
    app, document = raw(catia), raw(document)
    product = attribute(document, "Product")
    products = attribute(product, "Products")
    if products is None:
        raise RuntimeError("活动文档未提供可创建零件的 Product.Products")
    name = f"{prefix}_{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex[:6]}"
    component = None
    try:
        component = products.AddNewComponent("Part", name)
        if component is None:
            raise RuntimeError("CATIA 未返回新建 Part 组件")
        component_name = str(component.Name)
        part_document = component.ReferenceProduct.Parent
        part = part_document.Part
        return GenerationTarget(app, part_document, part, document, products,
                                component, component_name)
    except Exception as exc:
        try:
            # Never use the last open CATPart or remove unrelated components.
            if component is not None:
                _remove_component(products, str(component.Name))
            else:
                matches = [products.Item(i) for i in range(1, int(products.Count)+1)
                           if str(attribute(products.Item(i), "PartNumber", "")) == name]
                if len(matches) == 1:
                    _remove_component(products, str(matches[0].Name))
                elif matches:
                    raise RuntimeError("新组件定位不唯一")
        except Exception as cleanup:
            raise RuntimeError(f"新建 Part 失败：{exc}；自动清理未完成：{cleanup}；请检查 {name}") from exc
        raise RuntimeError(f"当前 Product 无法新建 Part：{exc}") from exc


def generation_target(catia, document=None):
    app = raw(catia)
    document = raw(document) if document is not None else attribute(app, "ActiveDocument")
    if document is None:
        raise RuntimeError("没有活动的 CATIA 文档")
    part = attribute(document, "Part")
    if part is not None:
        return GenerationTarget(app, document, part, document,
                                original_work=attribute(part, "InWorkObject"))
    return create_part_in_product(app, document)


def _appearance(document, obj, color=None, hide=False):
    selection = document.Selection
    selection.Clear()
    try:
        selection.Add(obj)
        if color is not None:
            selection.VisProperties.SetRealColor(*color, 1)
        if hide:
            selection.VisProperties.SetShow(1)
    finally:
        selection.Clear()


def _sketch(part, body, plane, axes, vertices, axis=None):
    part.InWorkObject = body
    sketch = body.Sketches.Add(part.CreateReferenceFromObject(plane))
    sketch.SetAbsoluteAxisData(tuple(axes))
    factory = sketch.OpenEdition()
    try:
        for first, second in zip(vertices, vertices[1:]+vertices[:1]):
            factory.CreateLine(*first, *second)
        if axis is not None:
            center_line = factory.CreateLine(*axis)
            center_line.Construction = True
            sketch.CenterLine = center_line
    finally:
        sketch.CloseEdition()
    part.UpdateObject(sketch)
    return sketch


def _symbol(target, geometry, point, xyz, layer, radius, name):
    part = target.part
    x, y, z = xyz
    if layer == 2:
        reference = part.CreateReferenceFromObject(point)
        shape = part.HybridShapeFactory.AddNewSphere(reference, None, radius, -90., 90., 0., 360.)
        shape.Limitation = 1
        return shape, []
    # A sketch's out-of-plane origin is projected onto its support by CATIA.
    # Use a plane through the real coordinate, not the origin XY/ZX planes.
    plane = part.HybridShapeFactory.AddNewPlaneEquation(
        *((0., 0., 1., z-radius) if layer == 3 else (0., -1., 0., -y)))
    plane.Name = f"SUPPORT_{name}"
    geometry.AppendHybridShape(plane)
    part.UpdateObject(plane)
    body = part.Bodies.Add()
    target.allocated.append(body)
    body.Name = name
    if layer == 3:
        sketch = _sketch(part, body, plane,
                         (x, y, z-radius, 1., 0., 0., 0., 1., 0.),
                         [(-radius, -radius), (radius, -radius),
                          (radius, radius), (-radius, radius)])
        shape = part.ShapeFactory.AddNewPad(sketch, radius*2)
    else:
        # Z-aligned cone: base radius r, height 2r, volume centroid at XYZ.
        sketch = _sketch(part, body, plane,
                         (x, y, z-radius/2, 1., 0., 0., 0., 0., 1.),
                         [(0., 0.), (radius, 0.), (0., radius*2)],
                         axis=(0., -radius, 0., radius*3))
        shape = part.ShapeFactory.AddNewShaft(sketch)
        shape.FirstAngle.Value = 360.
        shape.SecondAngle.Value = 0.
    return shape, [sketch, plane]


class GenerationCancelled(RuntimeError):
    pass


def generate_weld_geometry(catia, rows, radius_mm=3., name_prefix="WELD",
                           set_name="WeldGeometry", cancel_event=None, progress=None,
                           expected_document=None, symbols=True):
    """Create one batch; fail/cancel rolls back only this batch, never save files.

    XYZ uses the active Product's root frame (or the active CATPart's local
    frame). In a Product, points are transformed into the new component frame.
    Sphere radius/cube half-edge/cone base radius share ``radius_mm``.
    """
    validated = validate_rows(list(rows))
    radius = float(radius_mm)
    if not math.isfinite(radius) or radius <= 0:
        raise ValueError("尺寸必须是大于零的有限数值")
    if not validated:
        raise ValueError("没有可生成的点位")
    app = raw(catia)
    source = attribute(app, "ActiveDocument")
    if expected_document is not None and document_token(source) != expected_document:
        raise RuntimeError("活动文档已变化，未创建几何体；请重新确认目标文档")
    if cancel_event is not None and cancel_event.is_set():
        return {"cancelled": True, "count": 0, "counts": {2: 0, 3: 0, 4: 0}}
    target = generation_target(app, source)
    warnings = []
    try:
        # Capture the component placement once before any geometry is created.
        local_rows = [(row, target.local(xyz), layer) for row, xyz, layer in validated]
        part, document = target.part, target.document
        geometry = part.HybridBodies.Add()
        target.allocated.append(geometry)
        geometry.Name = f"{set_name}_{uuid4().hex[:6]}"
        for index, (row, xyz, layer) in enumerate(local_rows, 1):
            if cancel_event is not None and cancel_event.is_set():
                raise GenerationCancelled("已取消")
            name = f"{name_prefix}_{index:04d}_{layer}T_{str(row.get('Name', 'Point'))[:80]}"
            try:
                part.InWorkObject = geometry
                point = part.HybridShapeFactory.AddNewPointCoord(*xyz)
                point.Name = f"CENTER_{name}"
                geometry.AppendHybridShape(point)
                part.UpdateObject(point)
                if symbols:
                    shape, construction = _symbol(target, geometry, point, xyz, layer, radius, name)
                    shape.Name = name
                    if layer == 2:
                        geometry.AppendHybridShape(shape)
                    part.UpdateObject(shape)
                    try:
                        _appearance(document, shape, COLORS[layer])
                        _appearance(document, point, hide=True)
                        for helper in construction:
                            _appearance(document, helper, hide=True)
                    except Exception as exc:
                        warnings.append(f"{name} 显示属性未设置：{exc}")
                if progress:
                    progress(index, len(local_rows))
            except Exception as exc:
                raise RuntimeError(f"第 {index} 行 {row.get('Name', '')}（{layer}T）生成失败：{exc}") from exc
        part.Update()
        if cancel_event is not None and cancel_event.is_set():
            raise GenerationCancelled("已取消")
        return {"cancelled": False, "count": len(local_rows),
                "counts": layer_counts(row for row, _, _ in validated),
                "part_name": str(attribute(document, "Name", "")),
                "component_name": target.component_name, "warnings": warnings}
    except Exception as exc:
        try:
            target.rollback()
        except Exception as cleanup:
            raise RuntimeError(f"{exc}；自动清理未完成：{cleanup}。请检查本次新建的 {target.component_name or set_name}") from exc
        if isinstance(exc, GenerationCancelled):
            return {"cancelled": True, "count": 0, "counts": {2: 0, 3: 0, 4: 0}}
        raise RuntimeError(f"{exc}；本批次已回滚，未保留半成品") from exc
    finally:
        target.restore()
