"""A window close must not destroy a running scan or PSZ repair thread."""

import os
import tempfile
import threading
import time
import unittest
import zipfile
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtCore import QThread
from PyQt5.QtWidgets import QApplication, QMessageBox, QFileDialog

from pdps_manager.ui import project_manager_page
from pdps_manager.ui.main_window import MainApp
from pdps_manager.core.psz_parser import PS_STATE, EMS_STATE
from pdps_manager.workers.psz_worker import build_snapshot


class ControlledThread(QThread):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.started_in_thread = threading.Event()
        self.release = threading.Event()

    def run(self):
        self.started_in_thread.set()
        self.release.wait(5)


class ControlledScan(ControlledThread):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.cancel_called = False

    def cancel(self):
        self.cancel_called = True
        self.release.set()


class ThreadShutdownTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        names = ('CONFIG_FILE', 'LEGACY_CONFIG_XLSX', 'PATHS_CONFIG_FILE',
                 'LEGACY_PATHS_INI', 'RECENT_FILE', 'UI_SETTINGS_FILE')
        stack = ExitStack()
        self.addCleanup(stack.close)
        for name in names:
            stack.enter_context(patch.object(project_manager_page, name,
                                             str(Path(self.temp.name) / name)))
        stack.enter_context(patch('pdps_manager.catia_utils.connect_active_catia',
                                  return_value=(None, '')))
        self.window = MainApp()
        self.window.show()
        self.app.processEvents()
        self.addCleanup(self.window.deleteLater)

    def process_until(self, condition, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if condition():
                return True
            time.sleep(0.01)
        return False

    def test_scan_cancels_and_window_closes_after_thread_finishes(self):
        worker = ControlledScan(self.window.project_page)
        self.window.project_page._scan_worker = worker
        worker.start()
        self.assertTrue(worker.started_in_thread.wait(1))
        try:
            self.window.close()
            self.assertTrue(worker.cancel_called)
            self.assertTrue(self.process_until(lambda: not self.window.isVisible()))
            self.assertTrue(worker.wait(1000))
        finally:
            worker.release.set()
            worker.wait(1000)

    def test_write_finishes_before_window_closes(self):
        worker = ControlledThread(self.window.psz_page)
        self.window.psz_page._worker = worker
        worker.start()
        self.assertTrue(worker.started_in_thread.wait(1))
        try:
            self.window.close()
            self.assertTrue(self.window.isVisible())
            self.assertTrue(worker.isRunning())
            worker.release.set()
            self.assertTrue(self.process_until(lambda: not self.window.isVisible()))
            self.assertTrue(worker.wait(1000))
        finally:
            worker.release.set()
            worker.wait(1000)

    def test_object_table_shows_entry_and_deletes_only_selected_identity(self):
        psz = Path(self.temp.name) / 'same-id.psz'
        with zipfile.ZipFile(psz, 'w') as archive:
            for entry, name in ((PS_STATE, 'PS object'), (EMS_STATE, 'EMS object')):
                archive.writestr(entry, f'<Root><Objects><PmLayout ExternalId="same" '
                                       f'name="{name}"/></Objects></Root>')
        page = self.window.psz_page
        page._apply_snapshot(build_snapshot(str(psz)))
        self.assertEqual(page.table.rowCount(), 2)
        entries = {page.table.item(row, 3).text() for row in range(2)}
        self.assertEqual(entries, {'PS', 'EMS'})
        ps_row = next(row for row in range(2)
                      if page.table.item(row, 3).text() == 'PS')
        page.table.setCurrentCell(ps_row, 0)
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes), \
             patch.object(page, '_start_repair') as start:
            page._delete_selected()
        self.assertEqual(start.call_args.args[1]['targets'], [(PS_STATE, 'same')])

    def test_weld_export_scope_uses_current_duplicate_threshold(self):
        toolbox = self.window.tabs.widget(2)
        toolbox.weld_extractor.weld_points_data = [
            {'Name': 'first', 'X': 0, 'Y': 0, 'Z': 0},
            {'Name': 'second', 'X': 3, 'Y': 0, 'Z': 0},
        ]
        toolbox.duplicate_threshold_spin.setValue(4)
        toolbox.export_scope.setCurrentIndex(2)
        with patch.object(toolbox, '_export_data') as export:
            toolbox.export_welds_to_excel()
        exported = export.call_args.args[0]
        self.assertEqual(len(exported), 1)
        self.assertEqual((exported[0]['Name'], exported[0]['RepresentativeName'],
                          exported[0]['DistanceMM'], exported[0]['ThresholdMM']),
                         ('second', 'first', 3.0, 4.0))

    def test_csv_import_normalizes_units_and_preserves_source(self):
        csv_path = Path(self.temp.name) / 'points.csv'
        csv_path.write_text('Name,X,Y,Z,unit,coordinate_system\n'
                            'stud,1,2,3,cm,assembly\n', encoding='utf-8')
        toolbox = self.window.tabs.widget(2)
        with patch.object(QFileDialog, 'getOpenFileName',
                          return_value=(str(csv_path), '')), \
             patch.object(toolbox, '_begin_progress'), \
             patch.object(toolbox, '_end_progress'), \
             patch.object(QMessageBox, 'information'):
            toolbox.import_weld_data()
        row = toolbox.weld_extractor.weld_points_data[0]
        self.assertEqual((row['X'], row['Y'], row['Z']), (10.0, 20.0, 30.0))
        self.assertEqual((row['Source'], row['SourcePath'],
                          row['SourceUnit'], row['Unit']),
                         ('CSV', str(csv_path), 'cm', 'mm'))

    def test_stud_preview_shows_instance_path_and_query_changes_require_reread(self):
        toolbox = self.window.tabs.widget(2)
        toolbox.catia_app = object()
        points = [{'Name': 'Q114.1', 'PartNumber': 'Q114',
                   'InstancePath': 'Assembly/Section/Q114.1',
                   'X': 1, 'Y': 2, 'Z': 3}]
        self.assertEqual(toolbox.stud_name_edit.text(), 'Q114')
        with patch.object(toolbox.stud_extractor, 'read_stud_points', return_value=points), \
             patch.object(toolbox, '_begin_progress'), patch.object(toolbox, '_end_progress'):
            toolbox.get_stud_points()
        self.assertEqual(toolbox.stud_table.columnCount(), 6)
        self.assertEqual(toolbox.stud_table.topLevelItem(0).text(2), points[0]['InstancePath'])
        self.assertTrue(toolbox.create_stud_button.isEnabled())
        toolbox.stud_name_edit.setText('FQ114')
        self.assertFalse(toolbox.create_stud_button.isEnabled())


if __name__ == '__main__':
    unittest.main()
