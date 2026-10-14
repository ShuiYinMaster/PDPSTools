"""Scanning reuses only unchanged PSZ files and preserves edited project fields."""

import tempfile
import unittest
from pathlib import Path

from pdps_manager.workers.scan_worker import ScanWorker


class ScanWorkerCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.psz = self.root / 'project.psz'
        self.psz.write_bytes(b'first')

    def scan(self, existing, extractor):
        worker = ScanWorker([str(self.root)], existing, extractor)
        results, failures = [], []
        worker.finished_ok.connect(lambda *args: results.append(args))
        worker.failed.connect(failures.append)
        worker.run()
        self.assertEqual(failures, [])
        self.assertEqual(len(results), 1)
        return results[0]

    def test_changed_file_reparses_while_edit_fields_survive(self):
        calls = []

        def extract(path):
            calls.append(path)
            return {'Company': 'auto', 'System Root': f'root-{len(calls)}'}

        rows, reused, parsed = self.scan({}, extract)
        self.assertEqual((reused, parsed), (0, 1))
        rows[0]['Company'] = 'manual'
        rows[0]['_SourceSize'] = str(rows[0]['_SourceSize'])
        rows[0]['_SourceMtimeNs'] = str(rows[0]['_SourceMtimeNs'])
        existing = {str(self.psz): rows[0]}
        rows, reused, parsed = self.scan(existing, extract)
        self.assertEqual((reused, parsed, len(calls)), (1, 0, 1))
        self.psz.write_bytes(b'changed-and-longer')
        rows, reused, parsed = self.scan(existing, extract)
        self.assertEqual((reused, parsed, len(calls)), (0, 1, 2))
        self.assertEqual((rows[0]['Company'], rows[0]['System Root']),
                         ('manual', 'root-2'))

    def test_parse_failure_is_reported_and_retried(self):
        attempts = []

        def extract(path):
            attempts.append(path)
            if len(attempts) == 1:
                raise ValueError('invalid archive')
            return {'Company': 'recovered'}

        rows, _, _ = self.scan({}, extract)
        self.assertIn('invalid archive', rows[0]['_ScanError'])
        rows, reused, parsed = self.scan({str(self.psz): rows[0]}, extract)
        self.assertEqual((reused, parsed), (0, 1))
        self.assertEqual(rows[0]['_ScanError'], '')
        self.assertEqual(rows[0]['Company'], 'recovered')

    def test_manual_system_root_is_not_overwritten(self):
        rows, _, _ = self.scan({}, lambda _: {'System Root': 'auto-root'})
        rows[0]['System Root'] = 'manually-selected-root'
        self.psz.write_bytes(b'changed')
        rows, _, _ = self.scan({str(self.psz): rows[0]},
                               lambda _: {'System Root': 'new-auto-root'})
        self.assertEqual(rows[0]['System Root'], 'manually-selected-root')
        self.assertEqual(rows[0]['_AutoSystemRoot'], 'new-auto-root')


if __name__ == '__main__':
    unittest.main()
