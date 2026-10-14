"""Point exports retain provenance and the exact duplicate decision."""

import copy
import unittest

from pdps_manager.core.point_report import build_point_report, to_mm_coordinates


class PointReportTests(unittest.TestCase):
    def test_scope_and_representative_link_are_stable(self):
        rows = [
            {'Name': 'binary', 'X': 0, 'Y': 0, 'Z': 0,
             '_SourceKind': '3DXML', '_source_path': 'input.3dxml',
             '_Confidence': 'medium'},
            {'Name': 'xml', 'X': 1, 'Y': 0, 'Z': 0,
             '_SourceKind': '3DXML', '_source_path': 'input.3dxml',
             '_Confidence': 'high'},
            {'Name': 'far', 'X': 20, 'Y': 0, 'Z': 0,
             'Source': 'CSV', 'SourcePath': 'table.csv',
             'CoordinateSystem': 'unspecified'},
            {'Name': 'bad', 'X': 'nan', 'Y': 0, 'Z': 0},
        ]
        original = copy.deepcopy(rows)
        all_rows = build_point_report(rows, 5, 'all')
        retained = build_point_report(rows, 5, 'retained')
        duplicates = build_point_report(rows, 5, 'duplicates')
        self.assertEqual([row['Name'] for row in retained], ['xml', 'far'])
        self.assertEqual([row['Name'] for row in duplicates], ['binary'])
        self.assertEqual([row['DuplicateStatus'] for row in all_rows],
                         ['duplicate', 'representative', 'unique', 'invalid'])
        self.assertEqual((duplicates[0]['RepresentativeRow'],
                          duplicates[0]['RepresentativeName'],
                          duplicates[0]['DistanceMM'], duplicates[0]['ThresholdMM']),
                         (2, 'xml', 1.0, 5))
        self.assertEqual((duplicates[0]['Source'], duplicates[0]['SourcePath'],
                          duplicates[0]['Unit']), ('3DXML', 'input.3dxml', 'mm'))
        self.assertEqual(all_rows[2]['CoordinateSystem'], 'unspecified')
        self.assertEqual(rows, original)

    def test_invalid_selection_is_rejected(self):
        with self.assertRaises(ValueError):
            build_point_report([], selection='unknown')

    def test_import_units_are_converted_before_duplicate_detection(self):
        self.assertEqual(to_mm_coordinates((1, 2, 3), 'cm'), (10, 20, 30))
        self.assertEqual(to_mm_coordinates((1, 2, 3), 'm'), (1000, 2000, 3000))
        with self.assertRaises(ValueError):
            to_mm_coordinates((1, 2, 3), 'unknown')
        with self.assertRaises(ValueError):
            to_mm_coordinates((float('nan'), 2, 3), 'mm')


if __name__ == '__main__':
    unittest.main()
