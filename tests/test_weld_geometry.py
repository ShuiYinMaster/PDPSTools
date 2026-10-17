"""Native CATIA generation contract, proxy support, and transaction safety."""

import threading
import unittest
from types import SimpleNamespace as Node
from unittest.mock import patch

from pdps_manager.core.weld_geometry import generate_weld_geometry, document_token
from pdps_manager.core.weld_extractor import CATIAWeldPointExtractor
from pdps_manager.core.sphere_scanner import CATIASphereScanner


IDENTITY = (1., 0., 0., 0., 1., 0., 0., 0., 1., 0., 0., 0.)


class Collection:
    def __init__(self, factory, values=()):
        self.factory = factory
        self.items = list(values)

    @property
    def Count(self):
        return len(self.items)

    def Item(self, index):
        return self.items[index-1]

    def Add(self, *args):
        value = self.factory(*args)
        self.items.append(value)
        return value


class Sketch:
    def __init__(self, support):
        self.support, self.axes, self.lines = support, (), []
        self.closed = False

    def SetAbsoluteAxisData(self, axes):
        self.axes = axes

    def OpenEdition(self):
        def line(*coords):
            value = Node(coords=coords)
            self.lines.append(value)
            return value
        return Node(CreateLine=line)

    def CloseEdition(self):
        self.closed = True


def body():
    shapes = Collection(lambda: Node())
    return Node(Name="", HybridShapes=shapes,
                AppendHybridShape=shapes.items.append, Sketches=Collection(Sketch))


class Part:
    def __init__(self, fail_point=None, fail_update=False):
        self.HybridBodies = Collection(body)
        self.Bodies = Collection(body, [body()])
        self.InWorkObject = self.Bodies.Item(1)
        self.points, self.spheres, self.pads, self.shafts, self.planes = [], [], [], [], []
        self.fail_point, self.fail_update = fail_point, fail_update
        self.point_calls = 0
        self.HybridShapeFactory = Node(AddNewPointCoord=self.point, AddNewSphere=self.sphere,
                                       AddNewPlaneEquation=self.plane)
        self.ShapeFactory = Node(AddNewPad=self.pad, AddNewShaft=self.shaft)

    def point(self, *xyz):
        self.point_calls += 1
        if self.point_calls == self.fail_point:
            raise RuntimeError("injected point failure")
        value = Node(xyz=xyz)
        self.points.append(value)
        return value

    def sphere(self, center, axis, radius, *angles):
        value = Node(center=center, radius=radius, angles=angles)
        self.spheres.append(value)
        return value

    def plane(self, *equation):
        value = Node(equation=equation)
        self.planes.append(value)
        return value

    def pad(self, sketch, height):
        value = Node(sketch=sketch, height=height)
        self.pads.append(value)
        return value

    def shaft(self, sketch):
        value = Node(sketch=sketch, FirstAngle=Node(Value=0), SecondAngle=Node(Value=0))
        self.shafts.append(value)
        return value

    def CreateReferenceFromObject(self, obj):
        return obj

    def UpdateObject(self, obj):
        if self.fail_update:
            raise RuntimeError("injected update failure")

    def Update(self):
        pass


class Selection:
    def __init__(self, part):
        self.part, self.items = part, []
        self.colors, self.hidden = [], []
        self.VisProperties = Node(
            SetRealColor=lambda *rgba: self.colors.append((tuple(self.items), rgba)),
            SetShow=lambda value: self.hidden.extend(self.items))

    def Clear(self):
        self.items.clear()

    def Add(self, obj):
        self.items.append(obj)

    def Delete(self):
        for obj in self.items:
            for collection in (self.part.HybridBodies, self.part.Bodies):
                collection.items[:] = [value for value in collection.items if value is not obj]


class Products(Collection):
    def __init__(self, app):
        super().__init__(None)
        self.app = app
        self.matrix = IDENTITY
        self.fail_point, self.fail_update, self.fail_remove = None, False, False
        self.reject_add, self.bad_reference = False, False
        self.last_doc = None

    def AddNewComponent(self, kind, name):
        if self.reject_add:
            raise RuntimeError("product is not editable")
        assert kind == "Part"
        part = Part(self.fail_point, self.fail_update)
        document = Node(Name=name+".CATPart", FullName="", Part=part,
                        Selection=Selection(part), closed=False)
        document.Close = lambda: setattr(document, "closed", True)
        document.Activate = lambda: setattr(self.app, "ActiveDocument", document)
        component = Node(Name=name+".1", PartNumber=name,
                         Position=Node(GetComponents=lambda *_: self.matrix),
                         ReferenceProduct=Node(Parent=Node() if self.bad_reference else document))
        self.items.append(component)
        self.last_doc = document
        return component

    def Remove(self, index):
        if self.fail_remove:
            raise RuntimeError("injected removal failure")
        self.items.pop(index-1)


def app_product():
    app = Node(DisplayFileAlerts=True)
    products = Products(app)
    document = Node(Name="imported.CATProduct", FullName="imported.CATProduct",
                    Product=Node(Products=products))
    document.Activate = lambda: setattr(app, "ActiveDocument", document)
    app.ActiveDocument, app.ActiveWindow = document, Node(Name="imported.3dxml")
    return app, products


def rows():
    return [dict(Name=f"p{i}", X=100.+i*20, Y=-40., Z=1000., Type=f"{i+2}T") for i in range(3)]


class WeldGeometryTests(unittest.TestCase):
    def test_3dxml_product_creates_layer_specific_geometry_and_colors(self):
        app, products = app_product()
        result = generate_weld_geometry(app, rows(), radius_mm=3.)
        self.assertEqual(result["counts"], {2: 1, 3: 1, 4: 1})
        self.assertEqual(products.Count, 1)
        part = products.last_doc.Part
        self.assertEqual((len(part.spheres), len(part.pads), len(part.shafts)), (1, 1, 1))
        self.assertEqual(part.spheres[0].Limitation, 1)
        self.assertEqual(part.pads[0].height, 6.)
        self.assertEqual(part.shafts[0].FirstAngle.Value, 360.)
        # Support planes must carry the out-of-plane coordinate; sketch origin
        # alone is projected away by real CATIA.
        self.assertEqual(part.pads[0].sketch.support.equation, (0., 0., 1., 997.))
        self.assertEqual(part.shafts[0].sketch.support.equation, (0., -1., 0., 40.))
        self.assertTrue(part.pads[0].sketch.closed)
        self.assertEqual(len(products.last_doc.Selection.colors), 3)
        self.assertFalse(products.last_doc.closed)

    def test_geometry_rules_override_stale_labels_and_unknown_defaults_to_sphere(self):
        app, products = app_product()
        points = rows()
        points[0].update(GeometryType="正方体", Type="2T")
        points[1].update(GeometryType="球体", Type="3T")
        points[2].update(GeometryType="小型符号候选", Type="RSW")
        result = generate_weld_geometry(app, points)
        self.assertEqual(result["counts"], {2: 2, 3: 1, 4: 0})

    def test_new_component_frame_is_inverted_once(self):
        app, products = app_product()
        products.matrix = (0., 1., 0., -1., 0., 0., 0., 0., 1., 10., 20., 30.)
        with patch("pdps_manager.core.weld_geometry.CATIAStudPointExtractor._components",
                   return_value=products.matrix) as placement:
            generate_weld_geometry(app, [dict(Name="x", X=13., Y=25., Z=37.),
                                         dict(Name="y", X=14., Y=26., Z=38.)])
            placement.assert_called_once()
        self.assertEqual(products.last_doc.Part.points[0].xyz, (5., -3., 7.))

    def test_bad_coordinates_and_dimensions_fail_before_document_mutation(self):
        app, products = app_product()
        for point in ({"X": 1, "Y": 2}, {"X": "nan", "Y": 2, "Z": 3},
                      {"X": 1, "Y": 2, "Z": 3, "Unit": "cm"}):
            with self.assertRaises(ValueError):
                generate_weld_geometry(app, [point])
        for radius in (0, -1, float("inf"), float("nan")):
            with self.assertRaises(ValueError):
                generate_weld_geometry(app, rows(), radius_mm=radius)
        self.assertEqual(products.Count, 0)

    def test_document_switch_is_rejected_before_creation(self):
        app, products = app_product()
        expected = document_token(app.ActiveDocument)
        app.ActiveDocument.Name = "other.CATProduct"
        with self.assertRaisesRegex(RuntimeError, "活动文档已变化"):
            generate_weld_geometry(app, rows(), expected_document=expected)
        self.assertEqual(products.Count, 0)

    def test_readonly_product_is_attempted_and_reports_real_api_error(self):
        app, products = app_product()
        products.reject_add = True
        with self.assertRaisesRegex(RuntimeError, "product is not editable"):
            generate_weld_geometry(app, rows())
        self.assertEqual(products.Count, 0)

    def test_failure_rolls_back_new_part_without_touching_existing_components(self):
        app, products = app_product()
        existing = Node(Name="original", PartNumber="original")
        products.items.append(existing)
        products.fail_point = 2
        with self.assertRaisesRegex(RuntimeError, "第 2 行.*已回滚"):
            generate_weld_geometry(app, rows())
        self.assertEqual(products.items, [existing])
        self.assertTrue(products.last_doc.closed)
        self.assertTrue(app.DisplayFileAlerts)

    def test_failed_update_is_not_counted_as_success(self):
        app, products = app_product()
        products.fail_update = True
        with self.assertRaisesRegex(RuntimeError, "update failure.*已回滚"):
            generate_weld_geometry(app, rows())
        self.assertEqual(products.Count, 0)

    def test_failed_rollback_names_only_its_new_component(self):
        app, products = app_product()
        products.fail_point, products.fail_remove = 2, True
        with self.assertRaisesRegex(RuntimeError, "自动清理未完成.*WeldGeometry_"):
            generate_weld_geometry(app, rows())
        self.assertEqual(products.Count, 1)

    def test_cancel_before_or_during_creation_removes_batch(self):
        for before in (True, False):
            with self.subTest(before=before):
                app, products = app_product()
                cancel = threading.Event()
                if before:
                    cancel.set()
                result = generate_weld_geometry(app, rows(), cancel_event=cancel,
                                                progress=lambda *_: cancel.set())
                self.assertTrue(result["cancelled"])
                self.assertEqual(result["count"], 0)
                self.assertEqual(products.Count, 0)

    def test_bad_new_part_reference_removes_exact_new_component(self):
        app, products = app_product()
        products.bad_reference = True
        with self.assertRaisesRegex(RuntimeError, "新建 Part"):
            generate_weld_geometry(app, rows())
        self.assertEqual(products.Count, 0)

    def test_active_part_rollback_preserves_old_geometry_and_work_object(self):
        app, products = app_product()
        part = Part(fail_point=2)
        existing = part.HybridBodies.Add()
        work = part.InWorkObject
        app.ActiveDocument = Node(Name="existing.CATPart", FullName="", Part=part,
                                  Selection=Selection(part), Activate=lambda: None)
        with self.assertRaisesRegex(RuntimeError, "已回滚"):
            generate_weld_geometry(app, rows())
        self.assertEqual(part.HybridBodies.items, [existing])
        self.assertIs(part.InWorkObject, work)
        self.assertEqual(products.Count, 0)

    def test_scanner_center_generation_uses_same_product_capability_path(self):
        app, products = app_product()
        scanner = CATIASphereScanner(app)
        result = scanner.generate_points_at_centers(points_data=rows())
        self.assertEqual(result, 3)
        self.assertEqual(len(products.last_doc.Part.spheres), 0)
        self.assertEqual(len(products.last_doc.Part.points), 3)

    def test_legacy_weld_context_no_longer_rejects_3dxml_window(self):
        app, products = app_product()
        document, part = CATIAWeldPointExtractor._generation_raw_part(app, app.ActiveDocument)
        self.assertIs(document, products.last_doc)
        self.assertIs(part, document.Part)


if __name__ == "__main__":
    unittest.main()
