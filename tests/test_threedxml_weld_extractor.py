"""3DXML symbols require structural evidence and assembly-correct coordinates."""

import math
import itertools
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from pdps_manager.core.threedxml_center_extractor import ThreeDXmlError
from pdps_manager.core.weld_duplicate_detector import find_weld_duplicates
from pdps_manager.core.threedxml_weld_extractor import (
    ThreeDXmlWeldExtractor, _binary_mesh_nodes,
)


def compact(count):
    return bytes([count]) if count < 255 else b"\xff" + struct.pack("<I", count)


def node(center, count=137, radius=5.4, *, flags=0, repeated_count=None, cube=False):
    header = bytes.fromhex("04 fd ff 00 00 00 01") + bytes([flags])
    header += struct.pack("<5fI", radius, 0, *center, 42)
    header += b"\xff\xff\x02\x00\x01" + compact(count)
    header += compact(count if repeated_count is None else repeated_count) + b"\0" * 3
    if cube:
        # Rotate a six-millimeter cube around Z; the bound is deliberately
        # conservative, as in the supplied files.
        angle = .37
        vertices = []
        for x, y, z in itertools.product((-3, 3), repeat=3):
            vertices.extend((center[0] + x*math.cos(angle) - y*math.sin(angle),
                             center[1] + x*math.sin(angle) + y*math.cos(angle), center[2]+z))
        return header + bytes.fromhex("14 45 51 14 45 51 18") + struct.pack("<24f", *vertices)
    return header + b"\0" * 16


def write_container(path, payload, *, part_name="renamed_part", placements=None):
    root = ET.Element("Model_3dxml")
    structure = ET.SubElement(root, "ProductStructure", root="1")
    ET.SubElement(structure, "Reference3D", id="1", name="Assembly")
    ET.SubElement(structure, "Reference3D", id="2", name=part_name)
    for index, matrix in enumerate(placements or ["1 0 0 0 1 0 0 0 1 0 0 0"], 1):
        instance = ET.SubElement(structure, "Instance3D", id=f"i{index}", name=f"instance{index}")
        ET.SubElement(instance, "IsAggregatedBy").text = "1"
        ET.SubElement(instance, "IsInstanceOf").text = "2"
        ET.SubElement(instance, "RelativeMatrix").text = matrix
    attachment = ET.SubElement(structure, "InstanceRep", id="attachment")
    ET.SubElement(attachment, "IsAggregatedBy").text = "2"
    ET.SubElement(attachment, "IsInstanceOf").text = "rep"
    ET.SubElement(structure, "ReferenceRep", id="rep", associatedFile="urn:3DXML:opaque.3DRep")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Manifest.xml", "<Manifest><Root>model.3dxml</Root></Manifest>")
        archive.writestr("model.3dxml", ET.tostring(root))
        archive.writestr("opaque.3DRep", payload)


class ThreeDXmlWeldExtractorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "sample.3dxml"
        self.extractor = ThreeDXmlWeldExtractor()

    def test_arbitrary_repeated_topology_survives_name_removal(self):
        for count in (137, 511):
            with self.subTest(count=count):
                payload = b"V5_CFV3" + b"".join(node((i*30, 10, 20), count=count) for i in range(3))
                write_container(self.path, payload)
                rows = self.extractor.extract(self.path, mode="geometry_only")
                self.assertEqual(len(rows), 3)
                self.assertEqual({row["VertexCount"] for row in rows}, {count})
                self.assertTrue(all(not row["NameHintMatched"] for row in rows))
                self.assertTrue(all(row["WeldLayers"] == "2T" for row in rows))
                self.assertTrue(all(row["ShapeConfidence"] == "unverified" for row in rows))

    def test_hint_is_from_part_association_not_zip_filename(self):
        write_container(self.path, b"V5_CFV3" + node((10, 20, 30)), part_name="welds-XwS")
        rows = self.extractor.extract(self.path, mode="xws_only")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["SourceRepresentation"], "opaque.3DRep")
        self.assertTrue(rows[0]["NameHintMatched"])
        self.assertEqual(self.extractor.extract(self.path, mode="geometry_only"), [])
        self.assertEqual(self.extractor.extract(self.path, name_hint=""), [])

    def test_rotated_cube_is_verified_without_hint_or_repetition(self):
        write_container(self.path, b"V5_CFV3" + node((100, 200, 300), count=24, radius=7, cube=True))
        rows = self.extractor.extract(self.path, mode="geometry_only")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["GeometryType"], "正方体")
        self.assertEqual(rows[0]["ShapeConfidence"], "high")
        self.assertEqual(rows[0]["ShapeHypothesis"], "")
        self.assertAlmostEqual(rows[0]["CubeEdgeMM"], 6, places=4)
        self.assertEqual(rows[0]["WeldLayers"], "3T")

    def test_false_marker_and_mismatched_mesh_count_are_rejected(self):
        payload = b"V5_CFV3" + node((10, 20, 30), flags=0x43)
        payload += node((10, 20, 30), repeated_count=138)
        self.assertEqual(list(_binary_mesh_nodes(payload)), [])
        write_container(self.path, payload, part_name="XWS")
        self.assertEqual(self.extractor.extract(self.path), [])

    def test_large_part_envelope_is_not_returned_as_a_weld(self):
        write_container(self.path, b"V5_CFV3" + node((100, 200, 300), radius=600), part_name="XWS")
        self.assertEqual(self.extractor.extract(self.path), [])

    def test_instances_are_not_collapsed_and_use_column_basis_matrix(self):
        payload = b"V5_CFV3" + b"".join(node((2+i*30, 3, 0)) for i in range(3))
        write_container(self.path, payload, placements=[
            "0 1 0 -1 0 0 0 0 1 10 0 0", "1 0 0 0 1 0 0 0 1 20 0 0",
        ])
        rows = self.extractor.extract(self.path, mode="geometry_only")
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[0]["_coords"], (7., 2., 0.))
        self.assertEqual(rows[3]["_coords"], (22., 3., 0.))
        self.assertEqual(len({r["SourceObject"] for r in rows}), 6)

    def test_nested_instances_accumulate_transform(self):
        write_container(self.path, b"V5_CFV3" + node((2, 3, 0)), part_name="XWS",
                        placements=["0 1 0 -1 0 0 0 0 1 10 0 0"])
        with zipfile.ZipFile(self.path) as archive:
            root = ET.fromstring(archive.read("model.3dxml"))
            payload = archive.read("opaque.3DRep")
        structure = root.find("ProductStructure")
        ET.SubElement(structure, "Reference3D", id="sub", name="Nested")
        first = structure.find("Instance3D")
        first.find("IsInstanceOf").text = "sub"
        child = ET.SubElement(structure, "Instance3D", id="nested", name="leaf")
        ET.SubElement(child, "IsAggregatedBy").text = "sub"
        ET.SubElement(child, "IsInstanceOf").text = "2"
        ET.SubElement(child, "RelativeMatrix").text = "1 0 0 0 1 0 0 0 1 4 5 0"
        with zipfile.ZipFile(self.path, "w") as archive:
            archive.writestr("Manifest.xml", "<Manifest><Root>model.3dxml</Root></Manifest>")
            archive.writestr("model.3dxml", ET.tostring(root))
            archive.writestr("opaque.3DRep", payload)
        rows = self.extractor.extract(self.path)
        self.assertEqual(rows[0]["_coords"], (2., 6., 0.))

    def test_unsupported_encoding_reports_diagnostic_without_fake_points(self):
        write_container(self.path, b"V5_CFV4" + node((10, 20, 30)), part_name="XWS")
        self.assertEqual(self.extractor.extract(self.path), [])
        self.assertEqual(len(self.extractor.diagnostics["warnings"]), 1)

    def test_nonrigid_transform_is_explicitly_rejected(self):
        write_container(self.path, b"V5_CFV3", placements=["2 0 0 0 1 0 0 0 1 0 0 0"])
        with self.assertRaisesRegex(ThreeDXmlError, "缩放"):
            self.extractor.extract(self.path)

    def test_distinct_meshes_at_same_location_remain_visible_for_dedup(self):
        write_container(self.path, b"V5_CFV3" + node((10, 20, 30))*3)
        rows = self.extractor.extract(self.path)
        self.assertEqual(len(rows), 3)
        self.assertEqual(len({r["SourceObject"] for r in rows}), 3)
        self.assertEqual(len(find_weld_duplicates(rows, 5)[0]), 2)

    def test_custom_radius_limit_applies_before_family_selection(self):
        write_container(self.path, b"V5_CFV3" + node((10, 20, 30))*3)
        self.assertEqual(len(self.extractor.extract(self.path, max_radius_mm=6)), 3)
        self.assertEqual(self.extractor.extract(self.path, max_radius_mm=5), [])


if __name__ == "__main__":
    unittest.main()
