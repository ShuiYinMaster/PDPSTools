"""Exercise real Qt clicks and row identity through sorting/filtering/refresh."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import nullcontext
from types import SimpleNamespace
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QObject, QPoint, QPointF, Qt, QEvent, QTimer, pyqtSignal
from PyQt5.QtGui import QFont, QFontDatabase, QWheelEvent, QMouseEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QPushButton, QMessageBox

from pdps_manager.ui.toolbox_page import ToolBoxPage
from pdps_manager.ui.weld_point_style import StrongPointSelectionDelegate
from pdps_manager.core.weld_layers import weld_layer


class DisconnectedCatia(QObject):
    connection_changed = pyqtSignal(object, str)
    app = None
    conn_type = ""


def point(x, y=0, z=0, name="same-name"):
    return {"Name": name, "X": x, "Y": y, "Z": z, "Radius": "3",
            "Class": "PmWeldPoint", "ExternalId": name, "Type": "RSW"}


class PointProjectionGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        # Windows' offscreen plugin does not enumerate the system font list.
        font_path = Path("C:/Windows/Fonts/msyh.ttc")
        if font_path.exists():
            QFontDatabase.addApplicationFont(str(font_path))
            cls.app.setFont(QFont("Microsoft YaHei", 9))

    def setUp(self):
        self.connection = DisconnectedCatia()
        self.page = ToolBoxPage(conn_manager=self.connection)
        self.page.resize(1380, 900)
        self.page.show()
        self.app.processEvents()
        self.canvas = self.page.point_projection.canvas

    def tearDown(self):
        self.page.close()
        self.page.deleteLater()
        self.app.processEvents()

    def load_geometry(self, rows):
        rows = self.page._canonicalize_geometry_rows(rows, "3DXML", "test.3dxml")
        self.page._refresh_sphere_table(rows)
        self.page.point_tabs.setCurrentIndex(self.page._source_tabs["geometry"])
        self.app.processEvents()

    def click_row(self, row_id):
        QTest.mouseClick(self.canvas, Qt.LeftButton,
                         pos=self.canvas.position_for_row(row_id).toPoint())
        self.app.processEvents()

    def selected_ids(self, table):
        return {item.data(0, Qt.UserRole) for item in table.selectedItems()}

    def test_chart_and_table_select_the_same_source_row_after_sorting(self):
        self.load_geometry([point(0, z=0), point(40, z=30), point(80, z=-20)])
        table = self.page.sphere_table
        table.setSortingEnabled(True)
        table.sortItems(1, Qt.DescendingOrder)
        self.click_row(1)
        self.assertEqual(self.selected_ids(table), {1})
        self.assertEqual(table.currentItem().data(0, Qt.UserRole), 1)
        target = next(table.topLevelItem(i) for i in range(3)
                      if table.topLevelItem(i).data(0, Qt.UserRole) == 2)
        table.clearSelection()
        target.setSelected(True)
        self.assertEqual(self.canvas.selected_ids, {2})
        self.assertIn("X=80.000000", self.page.point_projection.details.toPlainText())

    def test_duplicate_filter_does_not_confuse_visual_row_and_source_row(self):
        self.page.weld_extractor.weld_points_data = [point(0), point(3, 4), point(50)]
        self.page._apply_current_rename()
        self.page.point_tabs.setCurrentIndex(self.page._source_tabs["weld"])
        self.page.duplicate_only_chk.setChecked(True)
        self.app.processEvents()
        self.assertEqual([p["id"] for p in self.canvas.points], [1])
        self.assertTrue(self.canvas.points[0]["duplicate"])
        self.click_row(1)
        self.assertEqual(self.selected_ids(self.page.weld_table), {1})
        self.page.duplicate_threshold_spin.setValue(4.9)
        self.assertEqual(self.canvas.points, [])
        self.assertEqual(self.canvas.selected_ids, set())

    def test_coincident_projection_cycles_points_with_different_third_axis(self):
        self.load_geometry([point(0, 0, 0), point(0, 20, 0), point(30, 0, 30)])
        self.click_row(0)
        self.assertEqual(self.selected_ids(self.page.sphere_table), {0})
        self.click_row(0)
        self.assertEqual(self.selected_ids(self.page.sphere_table), {1})
        self.page.point_projection.plane_combo.setCurrentIndex(1)
        self.assertNotEqual(self.canvas.position_for_row(0), self.canvas.position_for_row(1))
        self.assertEqual(self.canvas.selected_ids, {1})

    def test_invalid_rows_are_reported_and_new_dataset_clears_old_selection(self):
        self.load_geometry([point(10, z=20), point("nan")])
        self.assertEqual(len(self.canvas.points), 1)
        self.assertIn("坐标无效 1 行", self.page.point_projection.count_label.text())
        self.click_row(0)
        self.load_geometry([point(300, z=400)])
        self.assertEqual(self.page.sphere_table.selectedItems(), [])
        self.assertEqual(self.canvas.selected_ids, set())
        self.assertEqual(self.page.point_projection.details.toPlainText(), "")

    def test_navigation_and_png_export_use_the_live_plot(self):
        self.load_geometry([point(0, z=0), point(40, z=30), point(80, z=-20)])
        self.click_row(1)
        pos = self.canvas.position_for_row(1).toPoint()
        world = self.canvas._world(pos)
        scale = self.canvas._scale
        event = QWheelEvent(QPointF(pos), QPointF(self.canvas.mapToGlobal(pos)),
                            QPoint(), QPoint(0, 120), Qt.NoButton, Qt.NoModifier,
                            Qt.NoScrollPhase, False)
        QApplication.sendEvent(self.canvas, event)
        self.assertGreater(self.canvas._scale, scale)
        self.assertAlmostEqual(self.canvas._world(pos)[0], world[0])
        center = self.canvas._center
        QTest.mousePress(self.canvas, Qt.LeftButton, pos=pos)
        move = QMouseEvent(QEvent.MouseMove, QPointF(pos+QPoint(30, 20)),
                           Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(self.canvas, move)
        QTest.mouseRelease(self.canvas, Qt.LeftButton, pos=pos+QPoint(30, 20))
        self.assertNotEqual(center, self.canvas._center)
        self.assertEqual(self.canvas.selected_ids, {1})
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "projection.png"
            with patch("pdps_manager.ui.point_projection.QFileDialog.getSaveFileName",
                       return_value=(str(path), "PNG")):
                self.page.point_projection._save_image()
            self.assertEqual(path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")

    def test_tabs_and_narrow_layout_keep_one_active_data_source(self):
        self.load_geometry([point(0), point(30)])
        self.click_row(1)
        self.page.point_tabs.setCurrentIndex(self.page._source_tabs["weld"])
        self.assertEqual(self.canvas.points, [])
        self.page.point_tabs.setCurrentIndex(self.page._source_tabs["geometry"])
        self.assertEqual(self.canvas.selected_ids, {1})
        self.page.resize(900, 900)
        self.app.processEvents()
        self.assertEqual(self.page.workspace_splitter.orientation(), Qt.Vertical)
        self.assertGreaterEqual(self.canvas.height(), 160)
        self.assertFalse(self.page.point_projection.details.isVisible())
        self.assertGreaterEqual(self.page.sphere_table.viewport().height(), 80)
        self.page.resize(1380, 900)
        self.app.processEvents()
        self.assertEqual(self.page.workspace_splitter.orientation(), Qt.Horizontal)

    def test_display_rounding_preserves_exact_generation_and_transfer_data(self):
        precise = 1234.123456789
        self.load_geometry([point(precise, 20, 30)])
        self.assertEqual(self.page.sphere_table.topLevelItem(0).text(1), "1234.123")
        rows, invalid = self.page._read_sphere_table()
        self.assertEqual(invalid, [])
        self.assertEqual(float(rows[0]["X"]), precise)
        with patch.object(self.page, "_waiting", return_value=nullcontext()):
            self.page.transfer_sphere_data_to_weld_table()
        self.assertEqual(float(self.page.weld_extractor.weld_points_data[0]["X"]), precise)

    def test_layer_symbols_and_statistics_follow_source_filter_and_selection(self):
        rows = [dict(point(0), Type="2T"), dict(point(3, 4), Type="3T"),
                dict(point(50), Type="4T"), dict(point("nan"), Type="4T")]
        self.page.weld_extractor.weld_points_data = rows
        self.page._apply_current_rename()
        self.page.point_tabs.setCurrentIndex(self.page._source_tabs["weld"])
        panel = self.page.point_projection
        self.assertEqual([p["layer"] for p in self.canvas.points], [2, 3, 4])
        self.assertEqual(panel.layer_statistics[4], {"total": 2, "visible": 2, "valid": 1, "selected": 0})
        self.page.duplicate_only_chk.setChecked(True)
        self.assertEqual(panel.layer_statistics[2]["total"], 1)
        self.assertEqual(panel.layer_statistics[2]["visible"], 0)
        self.click_row(1)
        self.assertEqual(panel.layer_statistics[3]["selected"], 1)
        self.assertIn("3T", panel.details.toPlainText())

    def test_all_pages_share_layer_rules_and_geometry_transfer_preserves_them(self):
        rows = [dict(point(0), Type="2T"), dict(point(30), Type="RSW", WeldLayers="三层板焊点"),
                dict(point(60), Type="4T")]
        self.load_geometry(rows)
        expected = {2: 1, 3: 1, 4: 1}
        self.assertEqual({k: v["total"] for k, v in self.page.point_projection.layer_statistics.items()}, expected)
        self.assertIsInstance(self.page.sphere_table.itemDelegate(), StrongPointSelectionDelegate)
        with patch.object(self.page, "_waiting", return_value=nullcontext()):
            self.page.transfer_sphere_data_to_weld_table()
        self.assertEqual([weld_layer(r) for r in self.page.weld_extractor.weld_points_data], [2, 3, 4])
        self.assertEqual({k: v["total"] for k, v in self.page.point_projection.layer_statistics.items()}, expected)
        self.assertIsInstance(self.page.weld_table.itemDelegate(), StrongPointSelectionDelegate)
        self.page.point_tabs.setCurrentIndex(self.page._source_tabs["stud"])
        self.page.catia_app = object()
        studs = [dict(row, PartNumber="part", InstancePath=f"Assembly/{i}") for i, row in enumerate(rows)]
        with patch.object(self.page, "_waiting", side_effect=lambda _text: nullcontext()), \
             patch.object(self.page.stud_extractor, "read_stud_points", return_value=studs):
            self.page.get_stud_points()
        self.assertEqual({k: v["total"] for k, v in self.page.point_projection.layer_statistics.items()}, expected)
        self.assertIsInstance(self.page.stud_table.itemDelegate(), StrongPointSelectionDelegate)

    def test_geometry_business_rules_have_no_unknown_category_after_transfer(self):
        self.load_geometry([
            dict(point(0), GeometryType="正方体", Type="2T"),
            dict(point(30), GeometryType="圆锥", WeldLayers="3T"),
            dict(point(60), GeometryType="球体"),
            dict(point(90), GeometryType="小型符号候选"),
        ])
        panel = self.page.point_projection
        self.assertEqual([p["layer"] for p in self.canvas.points], [3, 4, 2, 2])
        self.assertEqual([self.page.sphere_table.topLevelItem(i).text(6) for i in range(4)],
                         ["3T", "4T", "2T", "2T"])
        self.assertEqual(panel.statistics_table.topLevelItemCount(), 3)
        self.assertNotIn("未知", panel.legend_label.text())
        expected = {2: 2, 3: 1, 4: 1}
        self.assertEqual({k: v["total"] for k, v in panel.layer_statistics.items()}, expected)
        with patch.object(self.page, "_waiting", return_value=nullcontext()):
            self.page.transfer_sphere_data_to_weld_table()
        self.assertEqual([r["Type"] for r in self.page.weld_extractor.weld_points_data],
                         ["3T", "4T", "2T", "2T"])
        self.assertEqual({k: v["total"] for k, v in panel.layer_statistics.items()}, expected)

    def test_3d_rotation_pan_and_click_preserve_point_identity(self):
        rows = [dict(point(0, 0, 0), Type="2T"), dict(point(40, 20, 15), Type="3T"),
                dict(point(-30, 40, 50), Type="4T")]
        self.load_geometry(rows)
        self.page.point_projection.plane_combo.setCurrentIndex(3)
        self.assertEqual(self.canvas.plane, "3D")
        self.click_row(1)
        self.assertEqual(self.selected_ids(self.page.sphere_table), {1})
        original = [p["xyz"] for p in self.canvas.points]
        yaw, projected = self.canvas._yaw, self.canvas.position_for_row(0)
        pos = self.canvas.plot_rect().center().toPoint()
        QTest.mousePress(self.canvas, Qt.LeftButton, pos=pos)
        QApplication.sendEvent(self.canvas, QMouseEvent(
            QEvent.MouseMove, QPointF(pos+QPoint(30, 10)), Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
        QTest.mouseRelease(self.canvas, Qt.LeftButton, pos=pos+QPoint(30, 10))
        self.assertNotEqual(self.canvas._yaw, yaw)
        self.assertNotEqual(self.canvas.position_for_row(0), projected)
        self.assertEqual([p["xyz"] for p in self.canvas.points], original)
        center, yaw = self.canvas._center, self.canvas._yaw
        QTest.mousePress(self.canvas, Qt.LeftButton, Qt.ShiftModifier, pos=pos)
        QApplication.sendEvent(self.canvas, QMouseEvent(
            QEvent.MouseMove, QPointF(pos+QPoint(20, 10)), Qt.NoButton, Qt.LeftButton, Qt.ShiftModifier))
        QTest.mouseRelease(self.canvas, Qt.LeftButton, Qt.ShiftModifier, pos=pos+QPoint(20, 10))
        self.assertEqual(self.canvas._yaw, yaw)
        self.assertNotEqual(self.canvas._center, center)
        self.assertEqual(self.canvas.selected_ids, {1})
        self.click_row(2)
        self.assertEqual(self.selected_ids(self.page.sphere_table), {2})

    def test_3d_depth_pick_can_cycle_occluded_points(self):
        self.load_geometry([point(0, 0, 0), point(0, 20, 0), point(30, 10, 30)])
        self.page.point_projection.plane_combo.setCurrentIndex(3)
        self.canvas._yaw = self.canvas._pitch = 0
        self.canvas.fit_points()
        self.click_row(1)
        self.assertEqual(self.canvas.selected_ids, {1})
        self.click_row(1)
        self.assertEqual(self.canvas.selected_ids, {0})

    def test_generation_dialog_passes_rows_and_dimensions_to_worker(self):
        extractor = self.page.weld_extractor
        extractor.weld_points_data = [dict(point(0), Type="2T"), dict(point(30), Type="3T"),
                                      dict(point(60), Type="4T")]
        document = SimpleNamespace(Name="imported.CATProduct", FullName="imported.CATProduct")
        extractor.catia_app = SimpleNamespace(ActiveDocument=document)
        result = {"cancelled": False, "count": 3, "counts": {2: 1, 3: 1, 4: 1},
                  "part_name": "Generated.CATPart", "warnings": []}
        with patch("pdps_manager.core.weld_extractor.QInputDialog.getText",
                   side_effect=[("3", True), ("TEST", True), ("Geometry", True)]), \
             patch("pdps_manager.core.weld_extractor.QMessageBox.question", return_value=QMessageBox.Yes), \
             patch("pdps_manager.core.weld_extractor.QMessageBox.information") as info, \
             patch("pdps_manager.core.weld_extractor.QMessageBox.critical") as error, \
             patch("win32com.client.GetActiveObject", return_value=extractor.catia_app), \
             patch("pdps_manager.core.weld_extractor.generate_weld_geometry", return_value=result) as generate:
            self.page.newball()
        error.assert_not_called()
        self.assertIn("正方体 1", info.call_args.args[2])
        self.assertEqual(generate.call_args.kwargs["radius_mm"], 3.)
        self.assertEqual(generate.call_args.kwargs["expected_document"],
                         ("imported.CATProduct", "imported.CATProduct"))
        self.assertEqual(len(generate.call_args.args[1]), 3)

    def test_closing_generation_dialog_waits_for_worker_cancellation(self):
        extractor = self.page.weld_extractor
        extractor.weld_points_data = [point(0)]
        extractor.catia_app = SimpleNamespace(ActiveDocument=SimpleNamespace(Name="proxy", FullName=""))
        cleaned = threading.Event()

        def generate(*_args, cancel_event, **_kwargs):
            if not cancel_event.wait(3):
                raise RuntimeError("Cancellation was not delivered")
            cleaned.set()
            return {"cancelled": True, "count": 0, "counts": {2: 0, 3: 0, 4: 0}}

        with patch("pdps_manager.core.weld_extractor.QInputDialog.getText",
                   side_effect=[("3", True), ("TEST", True), ("Geometry", True)]), \
             patch("pdps_manager.core.weld_extractor.QMessageBox.question", return_value=QMessageBox.Yes), \
             patch("pdps_manager.core.weld_extractor.QMessageBox.information") as info, \
             patch("pdps_manager.core.weld_extractor.QMessageBox.critical") as error, \
             patch("win32com.client.GetActiveObject", return_value=extractor.catia_app), \
             patch("pdps_manager.core.weld_extractor.generate_weld_geometry", side_effect=generate):
            timer = QTimer(self.page)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda: QApplication.activeModalWidget().close()
                                  if QApplication.activeModalWidget() is not None else None)
            timer.start(40)
            self.page.newball()
        self.assertTrue(cleaned.is_set())
        error.assert_not_called()
        self.assertIn("已取消", info.call_args.args[2])

    def test_manufacturing_export_button_is_renamed_without_changing_export_action(self):
        buttons = self.page.findChildren(QPushButton)
        export = [button for button in buttons if "导出制造特征（mfg）" in button.text()]
        self.assertEqual(len(export), 1)
        self.assertFalse(any("导出点位" in button.text() for button in buttons))

    def test_geometry_page_can_generate_symbols_directly_without_transfer(self):
        self.load_geometry([dict(point(1234.123456789), GeometryType="正方体")])
        with patch.object(self.page.weld_extractor, "generate_spheres_from_points") as generate:
            self.page.generate_geometry_from_centers()
        rows = generate.call_args.kwargs["points_data"]
        self.assertEqual(rows[0]["GeometryType"], "正方体")
        self.assertEqual(float(rows[0]["X"]), 1234.123456789)
        self.assertEqual(rows[0]["WeldLayers"], "3T")
        self.assertEqual(self.page.weld_extractor.weld_points_data, [])


if __name__ == "__main__":
    unittest.main()
