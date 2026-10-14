"""Tests for the dependency-free weld duplicate detector."""

import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "pdps_manager/core/weld_duplicate_detector.py"
SPEC = importlib.util.spec_from_file_location("weld_duplicate_detector", MODULE_PATH)
detector = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = detector
SPEC.loader.exec_module(detector)


def point(x, y=0, z=0, **extra):
    return {"X": x, "Y": y, "Z": z, **extra}


class WeldDuplicateDetectorTests(unittest.TestCase):
    def test_3d_boundary_and_non_transitive_matches(self):
        matches, invalid = detector.find_weld_duplicates(
            [point(0), point(3, 4), point(0, 0, 5.001), point(10)], 5
        )
        self.assertEqual([(m.duplicate_index, m.kept_index, m.distance_mm)
                          for m in matches], [(1, 0, 5.0)])
        self.assertEqual(invalid, [])

        matches, _ = detector.find_weld_duplicates([point(0), point(5), point(10)], 5)
        self.assertEqual([(m.duplicate_index, m.kept_index) for m in matches], [(1, 0)])

    def test_custom_threshold_and_same_name_are_coordinate_based(self):
        rows = [point(0, Name="same"), point(4, Name="same")]
        self.assertEqual(len(detector.find_weld_duplicates(rows, 5)[0]), 1)
        self.assertEqual(detector.find_weld_duplicates(rows, 3.9)[0], [])

    def test_cross_route_prefers_high_confidence(self):
        rows = [
            point(0, _Confidence="medium", _ExtractionMethod="binary_tessellation_signature_fallback"),
            point(1, _Confidence="high", _ExtractionMethod="xml_mesh_feature_matching"),
        ]
        matches, _ = detector.find_weld_duplicates(rows)
        self.assertEqual((matches[0].duplicate_index, matches[0].kept_index), (0, 1))

    def test_invalid_coordinates_do_not_hide_valid_rows(self):
        matches, invalid = detector.find_weld_duplicates(
            [point(0), point("nan"), point(1), {"X": 2, "Y": 0}]
        )
        self.assertEqual([(m.duplicate_index, m.kept_index) for m in matches], [(2, 0)])
        self.assertEqual(invalid, [1, 3])

    def test_threshold_must_be_positive_and_finite(self):
        for threshold in (0, -1, float("nan"), float("inf")):
            with self.subTest(threshold=threshold), self.assertRaises(ValueError):
                detector.find_weld_duplicates([point(0)], threshold)


if __name__ == "__main__":
    unittest.main()
