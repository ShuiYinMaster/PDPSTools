"""Point import converts coordinates and keeps useful provenance."""

import unittest

from pdps_manager.core.point_import import import_point_rows
from pdps_manager.core.point_records import canonicalize_point_record


class PointImportTests(unittest.TestCase):
    def test_unit_conversion_aliases_and_provenance(self):
        records, failures, errors = import_point_rows(
            [{
                "center_x_mm": 10, "center_y_mm": 20, "center_z_mm": 30,
                "unit": "mm", "name": "spot-1", "source_object": "proxy-4",
                "coordinate_system": "assembly", "confidence": "high",
                "extraction_method": "3DXML proxy",
            }],
            source_path="sample.csv",
            source_kind="CSV",
        )
        self.assertEqual((failures, errors), (0, []))
        self.assertEqual((records[0]["X"], records[0]["Y"], records[0]["Z"]),
                         (10, 20, 30))
        self.assertEqual(records[0]["Unit"], "mm")
        self.assertEqual(records[0]["SourceUnit"], "mm")
        self.assertEqual(records[0]["SourceObject"], "proxy-4")
        self.assertEqual(records[0]["_Confidence"], "high")

    def test_cm_and_invalid_rows_are_reported_without_losing_valid_rows(self):
        records, failures, errors = import_point_rows(
            [
                {"x": 1, "y": 2, "z": 3, "unit": "cm", "id": "cm"},
                {"x": "bad", "y": 2, "z": 3, "unit": "mm"},
            ],
            source_path="sample.xlsx",
            source_kind="Excel",
        )
        self.assertEqual((records[0]["X"], records[0]["Y"], records[0]["Z"]),
                         (10, 20, 30))
        self.assertEqual(records[0]["SourceUnit"], "cm")
        self.assertEqual(failures, 1)
        self.assertIn("第 3 行", errors[0])

    def test_millimeter_columns_reject_conflicting_unit_and_bad_header(self):
        records, failures, errors = import_point_rows(
            [{"x_mm": 1, "y_mm": 2, "z_mm": 3, "unit": "cm"}],
            source_path="sample.csv",
            source_kind="CSV",
        )
        self.assertEqual((records, failures), ([], 1))
        self.assertIn("已标明 mm", errors[0])
        with self.assertRaisesRegex(ValueError, "缺少 X/Y/Z"):
            import_point_rows([{"x": 1, "y": 2}],
                              source_path="sample.csv", source_kind="CSV")

    def test_canonicalization_does_not_convert_normalized_rows_twice(self):
        row = {"Name": "already-mm", "X": 10, "Y": 20, "Z": 30,
               "Unit": "mm", "SourceUnit": "cm", "Source": "Excel"}
        result = canonicalize_point_record(row)
        self.assertEqual((result["X"], result["Y"], result["Z"]), (10, 20, 30))
        self.assertEqual(result["SourceUnit"], "cm")
        self.assertEqual(result["CoordinateSystem"], "assembly")


if __name__ == "__main__":
    unittest.main()
