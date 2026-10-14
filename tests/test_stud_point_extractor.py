"""CATIA stud reads are side-effect free and failed creation is recoverable."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pdps_manager.core.stud_point_extractor import CATIAStudPointExtractor


def transform(x=0, y=0, z=0):
    return (1.0, 0.0, 0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 1.0, x, y, z)


class ProductCollection:
    def __init__(self, products=None, fail_part=False, fail_remove=False):
        self.items = list(products or [])
        self.fail_part = fail_part
        self.fail_remove = fail_remove
        self.removed = []

    @property
    def Count(self):
        return len(self.items)

    def Item(self, index):
        return self.items[index - 1]

    def AddNewComponent(self, kind, part_number):
        part = FakePart(self.fail_part)
        product = SimpleNamespace(
            Name=part_number, PartNumber=part_number,
            Position=SimpleNamespace(GetComponents=lambda *_: transform()),
            Products=ProductCollection(),
            ReferenceProduct=SimpleNamespace(Parent=SimpleNamespace(Part=part)),
        )
        self.items.append(product)
        return product

    def Remove(self, index):
        if self.fail_remove:
            raise RuntimeError('COM removal failed')
        self.removed.append(self.items.pop(index - 1))


class FakePart:
    def __init__(self, fail):
        self.HybridBodies = SimpleNamespace(Add=self.add_body)
        self.HybridShapeFactory = SimpleNamespace(
            AddNewPointCoord=lambda x, y, z: SimpleNamespace(X=x, Y=y, Z=z))
        self.fail = fail
        self.body = None

    def add_body(self):
        if self.fail:
            raise RuntimeError('point creation failed')
        self.body = SimpleNamespace(Name='', points=[])
        self.body.AppendHybridShape = self.body.points.append
        self.body.HybridShapes = type('Shapes', (), {
            'Count': property(lambda _: len(self.body.points)),
        })()
        return self.body

    def Update(self):
        pass


def instance(name, part_number, coords, children=None):
    return SimpleNamespace(
        Name=name, PartNumber=part_number,
        Position=SimpleNamespace(GetComponents=lambda *_: transform(*coords)),
        Products=ProductCollection(children),
    )


class StudPointExtractorTests(unittest.TestCase):
    def setUp(self):
        stud = instance('Stud 1', 'stud-123', (0, 5, 0))
        parent = instance('Subassembly', 'sub', (10, 0, 0), [stud])
        self.products = ProductCollection([parent])
        root = SimpleNamespace(Products=self.products)
        document = SimpleNamespace(Name='assembly.CATProduct', Product=root)
        app = SimpleNamespace(ActiveDocument=document,
                              ActiveWindow=SimpleNamespace(Name='assembly.CATProduct'))
        self.extractor = CATIAStudPointExtractor(app)

    def test_read_then_create_in_assembly_coordinates(self):
        before = self.products.Count
        preview = self.extractor.read_stud_points('stud')
        self.assertEqual(self.products.Count, before)
        self.assertEqual((preview[0]['X'], preview[0]['Y'], preview[0]['Z']),
                         (10.0, 5.0, 0.0))
        result = self.extractor.create_stud_points('stud', expected_points=preview)
        self.assertEqual(result, preview)
        self.assertEqual(self.products.Count, before + 1)
        point = self.products.Item(before + 1).ReferenceProduct.Parent.Part.body.points[0]
        self.assertEqual((point.X, point.Y, point.Z), (10.0, 5.0, 0.0))

    def test_changed_preview_blocks_creation(self):
        preview = self.extractor.read_stud_points('stud')
        preview[0]['X'] = 999
        with self.assertRaisesRegex(RuntimeError, '已变化'):
            self.extractor.create_stud_points('stud', expected_points=preview)
        self.assertEqual(self.products.Count, 1)

    def test_failed_creation_removes_its_new_component(self):
        self.products.fail_part = True
        with self.assertRaisesRegex(RuntimeError, 'point creation failed'):
            self.extractor.create_stud_points('stud')
        self.assertEqual(self.products.Count, 1)
        self.assertEqual(len(self.products.removed), 1)

    def test_failed_rollback_identifies_component_for_manual_cleanup(self):
        self.products.fail_part = True
        self.products.fail_remove = True
        with self.assertRaisesRegex(RuntimeError, '自动清理未完成.*stud_OriginPoints'):
            self.extractor.create_stud_points('stud')
        self.assertEqual(self.products.Count, 2)

    def test_q114_and_fq114_keywords_return_each_instance_once(self):
        self.products.items = [instance('Q1140614.1', 'Q1140614', (1, 2, 3)),
                               instance('FQ114D0612.1', 'FQ114D0612', (4, 5, 6))]
        points = self.extractor.read_stud_points('Q114，FQ114;q114')
        self.assertEqual(len(points), 2)
        self.assertEqual([point['X'] for point in points], [1, 4])

    def test_same_names_in_separate_rotated_subassemblies_are_distinct(self):
        first = instance('Assembly A', 'assembly-a', (10, 0, 0),
                         [instance('Q114.1', 'Q114', (2, 3, 0))])
        first.Position.GetComponents = lambda *_: (
            0, 1, 0, -1, 0, 0, 0, 0, 1, 10, 0, 0)
        second = instance('Assembly B', 'assembly-b', (20, 0, 0),
                          [instance('Q114.1', 'Q114', (2, 3, 0))])
        self.products.items = [first, second]
        points = self.extractor.read_stud_points('Q114')
        self.assertEqual([(p['X'], p['Y'], p['Z']) for p in points],
                         [(7, 2, 0), (22, 3, 0)])
        self.assertEqual(len({p['InstancePath'] for p in points}), 2)
        self.assertEqual(len({p['PointName'] for p in points}), 2)
        self.extractor.create_stud_points('Q114', expected_points=points)
        created = self.products.items[-1].ReferenceProduct.Parent.Part.body.points
        self.assertEqual([(p.X, p.Y, p.Z) for p in created], [(7, 2, 0), (22, 3, 0)])

    def test_unrelated_unreadable_position_does_not_block_studs(self):
        unrelated = instance('unloaded', 'unloaded', (0, 0, 0))
        unrelated.Position = None
        self.products.items.append(unrelated)
        points = self.extractor.read_stud_points('stud')
        self.assertEqual(len(points), 1)

    def test_invalid_com_output_uses_catia_array_bridge(self):
        product = instance('Q114.1', 'Q114', (1, 2, 3))
        product.Position.GetComponents = lambda *_: (0.0,) * 12
        with patch('pycatia.in_interfaces.position.Position') as bridge:
            bridge.return_value.get_components.return_value = transform(1, 2, 3)
            self.assertEqual(self.extractor._components(product), transform(1, 2, 3))
            bridge.return_value.get_components.assert_called_once()


if __name__ == '__main__':
    unittest.main()
