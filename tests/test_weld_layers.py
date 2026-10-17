"""Layer metadata is interpreted identically across imported and live points."""

import unittest

from pdps_manager.core.weld_layers import weld_layer, layer_counts
from pdps_manager.core.point_import import import_point_rows
from pdps_manager.core.point_report import build_point_report


class WeldLayerTests(unittest.TestCase):
    def test_explicit_numeric_chinese_and_type_aliases(self):
        for row, expected in (
            ({"Type": "2T"}, 2), ({"type": "RSW (3t)"}, 3),
            ({"Type": "４Ｔ"}, 4), ({"WeldLayers": "两层板焊点"}, 2),
            ({"WeldLayers": "三层板焊点"}, 3), ({"板层数": "四层板"}, 4),
            ({"LayerCount": 3.0}, 3), ({"weld_layers": "4层"}, 4),
        ):
            with self.subTest(row=row):
                self.assertEqual(weld_layer(row), expected)

    def test_missing_or_conflicting_metadata_defaults_to_two_layers(self):
        for row in ({"Type": "13T"}, {"WeldLayers": "13层"}, {"板层数": "十三层"},
                    {"Name": "3T-point"}, {"ShapeHypothesis": "立方体同计数族"},
                    {"MeshCount": 24}, {"Type": "RSW"},
                    {"Type": "2T", "WeldLayers": "三层板焊点"}):
            with self.subTest(row=row):
                self.assertEqual(weld_layer(row), 2)

    def test_recognized_geometry_overrides_layer_labels(self):
        for row, expected in (
            ({"GeometryType": "正方体"}, 3),
            ({"GeometryType": "立方体", "Type": "2T"}, 3),
            ({"geometry_type": "Cube", "WeldLayers": "4T"}, 3),
            ({"GeometryType": "圆锥", "Type": "3T"}, 4),
            ({"shape": "cone"}, 4),
            ({"GeometryType": "球形", "Type": "3T"}, 2),
            ({"Type": "Sphere"}, 2),
            ({"GeometryType": "小型符号候选"}, 2),
            ({"GeometryType": "长方体"}, 2),
        ):
            with self.subTest(row=row):
                self.assertEqual(weld_layer(row), expected)

    def test_import_preserves_layer_metadata_and_conflicts(self):
        inputs = [{"X": 1, "Y": 2, "Z": 3, **extra} for extra in (
            {"Type": "RSW", "layer_count": "4"},
            {"Type": "3T"}, {"Type": "2T", "WeldLayers": "3T"},
        )]
        rows, failures, _ = import_point_rows(inputs, source_path="points.csv", source_kind="CSV")
        self.assertEqual(failures, 0)
        self.assertEqual([weld_layer(row) for row in rows], [4, 3, 2])
        self.assertEqual(layer_counts(rows), {2: 1, 3: 1, 4: 1})

    def test_geometry_classification_survives_import_and_export(self):
        inputs = [{"X": i*20, "Y": 0, "Z": 0, **extra} for i, extra in enumerate((
            {"GeometryType": "正方体", "Type": "2T"},
            {"shape_candidate": "cone"},
            {"GeometryType": "未识别"},
        ))]
        rows, failures, _ = import_point_rows(inputs, source_path="points.csv", source_kind="CSV")
        self.assertEqual(failures, 0)
        self.assertEqual([r["WeldLayers"] for r in rows], ["3T", "4T", "2T"])
        exported = build_point_report(inputs)
        self.assertEqual([r["WeldLayers"] for r in exported], ["3T", "4T", "2T"])
        reimported, failures, _ = import_point_rows(exported, source_path="export.csv", source_kind="CSV")
        self.assertEqual(failures, 0)
        self.assertEqual([weld_layer(r) for r in reimported], [3, 4, 2])


if __name__ == "__main__":
    unittest.main()
