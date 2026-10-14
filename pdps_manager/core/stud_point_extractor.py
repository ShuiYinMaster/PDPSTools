# -*- coding: utf-8 -*-
"""Read CATIA stud instances and create points at their assembly origins."""

import math
import re
from collections import Counter


class CATIAStudPointExtractor:
    """Create one CATIA point per instance matching a name or PartNumber."""

    IDENTITY = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0,
                0.0, 0.0, 1.0, 0.0, 0.0, 0.0)

    def __init__(self, catia_app=None):
        self.catia_app = catia_app

    def set_catia_app(self, catia_app, connection_type=None):
        self.catia_app = catia_app

    @staticmethod
    def _raw(value):
        return getattr(value, "com_object", value)

    @staticmethod
    def _text(obj, attribute):
        try:
            return str(getattr(obj, attribute) or "")
        except Exception:
            return ""

    @classmethod
    def _components(cls, product):
        """CATIA stores X/Y/Z basis *columns*, then the origin."""
        name = cls._text(product, "Name") or cls._text(product, "PartNumber")

        def valid(candidate):
            try:
                components = tuple(float(value) for value in candidate)
            except (TypeError, ValueError):
                return None
            if len(components) != 12 or not all(map(math.isfinite, components)):
                return None
            if not all(
                abs(sum(components[axis * 3 + row] ** 2 for row in range(3)) - 1.0)
                <= 0.001
                for axis in range(3)
            ):
                return None
            if any(abs(sum(components[a * 3 + row] * components[b * 3 + row]
                           for row in range(3))) > 0.001
                   for a, b in ((0, 1), (0, 2), (1, 2))):
                return None
            return components

        try:
            position = product.Position
            method = getattr(position, "GetComponents", None)
            if method is None:
                method = position.get_components
            for args in (([0.0] * 12,), ()):
                values = args[0] if args else None
                try:
                    returned = method(*args)
                except Exception:
                    continue
                for candidate in (returned, values):
                    components = valid(candidate)
                    if components is not None:
                        return components
            # Some win32com bindings need an explicit by-reference SAFEARRAY,
            # like the C# GetComponents call used by the source utility.
            try:
                import pythoncom
                from win32com.client import VARIANT

                values = VARIANT(
                    pythoncom.VT_BYREF | pythoncom.VT_ARRAY | pythoncom.VT_VARIANT,
                    [0.0] * 12,
                )
                returned = method(values)
                for candidate in (returned, values.value):
                    components = valid(candidate)
                    if components is not None:
                        return components
            except Exception:
                pass
            # CATIA's CATSafeArrayVariant output is not marshalled correctly by
            # some dynamic win32com bindings. pycatia uses a read-only VBA
            # GetComponents bridge for this exact API.
            try:
                from pycatia.in_interfaces.position import Position

                components = valid(Position(cls._raw(position)).get_components())
                if components is not None:
                    return components
            except Exception:
                pass
        except Exception as exc:
            raise RuntimeError(f"无法读取零件 {name} 的 CATIA Position") from exc
        raise RuntimeError(f"零件 {name} 的 CATIA Position 无效")

    @staticmethod
    def _compose(parent, child):
        result = [0.0] * 12
        for column in range(3):
            for row in range(3):
                result[column * 3 + row] = sum(
                    parent[k * 3 + row] * child[column * 3 + k]
                    for k in range(3)
                )
        for row in range(3):
            result[9 + row] = parent[9 + row] + sum(
                parent[k * 3 + row] * child[9 + k]
                for k in range(3)
            )
        return tuple(result)

    @staticmethod
    def _to_local(part_world, point_world):
        delta = tuple(point_world[9 + axis] - part_world[9 + axis]
                      for axis in range(3))
        return tuple(
            sum(part_world[column * 3 + row] * delta[row] for row in range(3))
            for column in range(3)
        )

    def _active_product(self):
        if self.catia_app is None:
            raise RuntimeError("请先连接 CATIA")
        app = self._raw(self.catia_app)
        document = getattr(app, "ActiveDocument", None)
        document_name = self._text(document, "Name")
        if not document_name.lower().endswith(".catproduct"):
            raise RuntimeError("请在 CATIA 中激活 CATProduct 装配文档")
        window_name = self._text(getattr(app, "ActiveWindow", None), "Name")
        if window_name.lower().endswith(".3dxml"):
            raise RuntimeError("3DXML 只读代理无法创建点，请激活 CATProduct")
        root = getattr(document, "Product", None)
        if root is None:
            raise RuntimeError("当前 CATProduct 没有 Product 根节点")
        return root

    @staticmethod
    def _search_terms(stud_name):
        terms = []
        for term in re.split(r"[,;，；、|\n\r]+", str(stud_name or "")):
            term = term.strip()
            if term and term.casefold() not in {value.casefold() for value in terms}:
                terms.append(term)
        if not terms:
            raise ValueError("请输入螺柱名称或零件号")
        return terms

    def _collect(self, product, ancestors, terms, output_part_number,
                 matches, state, path, key, depth=0):
        if depth > 100:
            raise RuntimeError("CATIA 装配层级超过 100 层，已停止读取")
        current_part_number = self._text(product, "PartNumber")
        instance_name = self._text(product, "Name")
        if current_part_number.casefold() == output_part_number.casefold():
            state["output_exists"] = True
        if current_part_number.casefold().endswith("_originpoints"):
            return
        instance_path = f"{path}/{instance_name or current_part_number}"
        chain = ancestors + [(key, product)]
        matched_term = next((term for term in terms
                             if term.casefold() in current_part_number.casefold()
                             or term.casefold() in instance_name.casefold()), None)
        if matched_term is not None:
            # Only inspect positions on a matching instance's ancestry. An
            # unrelated unloaded component must not block the entire read.
            world = self.IDENTITY
            for ancestor_key, ancestor in chain:
                if ancestor_key not in state["positions"]:
                    try:
                        state["positions"][ancestor_key] = self._components(ancestor)
                    except RuntimeError as exc:
                        raise RuntimeError(f"无法获取螺柱 {instance_path} 的装配坐标：{exc}") from exc
                world = self._compose(world, state["positions"][ancestor_key])
            matches.append({
                "Name": instance_name or current_part_number,
                "PartNumber": current_part_number,
                "InstancePath": instance_path,
                "MatchedTerm": matched_term,
                "X": world[9], "Y": world[10], "Z": world[11],
                "_world": world,
            })
        products = getattr(product, "Products", None)
        if products is None:
            return
        for index in range(1, int(products.Count) + 1):
            self._collect(products.Item(index), chain, terms,
                          output_part_number, matches, state, instance_path,
                          key + (index,), depth + 1)

    def _scan_stud_points(self, stud_name):
        terms = self._search_terms(stud_name)
        search = ", ".join(terms)
        root = self._active_product()
        output_base = re.sub(r'[\\/:*?"<>|]+', '_', '_'.join(terms))
        output_part_number = f"{output_base}_OriginPoints"
        matches = []
        state = {"output_exists": False, "positions": {}}
        products = root.Products
        root_name = self._text(root, "Name") or self._text(root, "PartNumber")
        for index in range(1, int(products.Count) + 1):
            self._collect(products.Item(index), [], terms,
                          output_part_number, matches, state, root_name, (index,))
        if not matches:
            raise RuntimeError(f"当前装配中未找到名称或零件号包含 {search} 的螺柱")
        counts = Counter(item["Name"].casefold() for item in matches)
        used_names = set()
        document = self._raw(self.catia_app).ActiveDocument
        source_path = self._text(document, "FullName") or self._text(document, "Name")
        for index, item in enumerate(matches, 1):
            point_name = f"{item['Name']}_Origin"
            if counts[item["Name"].casefold()] > 1 or point_name.casefold() in used_names:
                point_name = f"{item['Name']}_{index}_Origin"
            while point_name.casefold() in used_names:
                point_name += "_"
            used_names.add(point_name.casefold())
            item.update({"PointName": point_name, "SourcePath": source_path,
                         "CoordinateSystem": "assembly", "Unit": "mm",
                         "ExtractionMethod": "CATIA instance origin"})
        return root, matches, state, output_part_number

    @staticmethod
    def _visible_points(matches):
        return [{key: value for key, value in item.items() if key != "_world"}
                for item in matches]

    def read_stud_points(self, stud_name):
        """Read assembly-frame coordinates without changing the CATIA document."""
        _, matches, _, _ = self._scan_stud_points(stud_name)
        return self._visible_points(matches)

    def create_stud_points(self, stud_name, expected_points=None):
        """Re-read the active assembly, then create points with rollback on error."""
        search = str(stud_name or "").strip()
        root, matches, state, output_part_number = self._scan_stud_points(search)
        if expected_points is not None and self._visible_points(matches) != expected_points:
            raise RuntimeError("装配点位自上次读取后已变化，请重新读取并核对")
        if state["output_exists"]:
            raise RuntimeError(f"当前装配中已存在 {output_part_number}，未重复创建")

        products = root.Products
        count_before = int(products.Count)
        try:
            new_product = products.AddNewComponent("Part", output_part_number)
            if new_product is None:
                raise RuntimeError("CATIA 未返回新建的点位 CATPart")
            part = new_product.ReferenceProduct.Parent.Part
            body = part.HybridBodies.Add()
            body.Name = output_part_number.removesuffix("_OriginPoints") + "_Origins"
            factory = part.HybridShapeFactory
            new_part_world = self._components(new_product)
            for match in matches:
                x, y, z = self._to_local(new_part_world, match["_world"])
                point = factory.AddNewPointCoord(x, y, z)
                point.Name = match["PointName"]
                body.AppendHybridShape(point)
            part.Update()
            if int(body.HybridShapes.Count) != len(matches):
                raise RuntimeError("CATIA 点位数量校验失败")
        except Exception as exc:
            try:
                count_now = int(products.Count)
                if count_now == count_before + 1:
                    products.Remove(count_before + 1)
                elif count_now != count_before:
                    raise RuntimeError("装配组件数量变化，无法安全定位本次创建的组件")
            except Exception as rollback_error:
                raise RuntimeError(
                    f"创建螺柱点失败: {exc}；自动清理未完成: {rollback_error}。"
                    f"请检查装配中的 {output_part_number}"
                ) from exc
            raise
        return self._visible_points(matches)

    def get_stud_points(self, stud_name):
        """Compatibility entry point for callers expecting read and create."""
        return self.create_stud_points(stud_name)
