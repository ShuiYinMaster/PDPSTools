"""PSZ edits must keep object identity and leave the source intact on failure."""

import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from pdps_manager.core.psz_parser import (
    EMS_STATE, PS_STATE, decode_xml, replace_zip_entries,
)
from pdps_manager.workers.psz_worker import PszRepairWorker, build_snapshot


def xml(name, ext_id, uses="", broken=False):
    reference = f"<PmContinuousMfgUsage><uses>{uses}</uses></PmContinuousMfgUsage>" if uses else ""
    three_d = "<threeDRep>TuneTbm-missing</threeDRep>" if broken else ""
    return (f'<Root><Objects><PmLayout ExternalId="{ext_id}" name="{name}">'
            f'{three_d}</PmLayout>{reference}</Objects></Root>').encode('utf-8')


class PszTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.psz = self.root / 'project.psz'
        self.write_zip(xml('PS object', 'shared', uses='shared'),
                       xml('EMS object', 'shared', uses='shared'))

    def write_zip(self, ps, ems):
        with zipfile.ZipFile(self.psz, 'w') as archive:
            archive.writestr(PS_STATE, ps)
            archive.writestr(EMS_STATE, ems)
            archive.writestr('assets/keep.bin', b'unchanged\x00\xff')

    def run_repair(self, mode, params):
        snapshot = build_snapshot(str(self.psz))
        worker = PszRepairWorker(str(self.psz), snapshot['contents'],
                                 snapshot['encs'], mode, params,
                                 expected_fingerprint=snapshot['fingerprint'])
        done, failed = [], []
        worker.done.connect(done.append)
        worker.failed.connect(failed.append)
        worker.run()
        return done, failed

    def test_same_id_in_ps_and_ems_are_distinct_and_scoped_delete(self):
        snapshot = build_snapshot(str(self.psz))
        self.assertEqual(len(snapshot['obj_list']), 2)
        self.assertIn((PS_STATE, 'shared'), snapshot['objects'])
        self.assertIn((EMS_STATE, 'shared'), snapshot['objects'])
        done, failed = self.run_repair('delete', {
            'targets': [(PS_STATE, 'shared')], 'backup': True,
        })
        self.assertEqual(failed, [])
        self.assertEqual(len(done), 1)
        self.assertEqual(len(done[0]['entry_objects'][PS_STATE]), 0)
        self.assertEqual(len(done[0]['entry_objects'][EMS_STATE]), 1)
        with zipfile.ZipFile(self.psz) as archive:
            self.assertIn(b'<uses>shared</uses>', archive.read(PS_STATE))
            self.assertIn(b'EMS object', archive.read(EMS_STATE))
            self.assertEqual(archive.read('assets/keep.bin'), b'unchanged\x00\xff')
        self.assertTrue(Path(done[0]['backup_path']).exists())
        with zipfile.ZipFile(done[0]['backup_path']) as backup:
            self.assertIn(b'PS object', backup.read(PS_STATE))

    def test_batch_delete_changes_both_entries_in_one_commit(self):
        self.write_zip(xml('PS broken', 'ps-id', broken=True),
                       xml('EMS broken', 'ems-id', broken=True))
        done, failed = self.run_repair('delete_broken', {'backup': True})
        self.assertEqual(failed, [])
        self.assertEqual([len(done[0]['entry_objects'][entry])
                          for entry in (PS_STATE, EMS_STATE)], [0, 0])
        self.assertEqual(len(list(self.root.glob('*.bak'))), 1)

    def test_invalid_xml_does_not_replace_or_leave_temp_file(self):
        before = self.psz.read_bytes()
        with self.assertRaisesRegex(ValueError, 'XML 无效'):
            replace_zip_entries(str(self.psz), {PS_STATE: b'<Root>'})
        self.assertEqual(self.psz.read_bytes(), before)
        self.assertEqual(list(self.root.glob('.pdps-psz-*.tmp')), [])
        self.assertEqual(list(self.root.glob('*.bak')), [])

    def test_commit_error_keeps_original_and_removes_stage(self):
        before = self.psz.read_bytes()
        with patch('pdps_manager.core.psz_parser.os.replace',
                   side_effect=OSError('simulated commit failure')):
            with self.assertRaisesRegex(OSError, 'simulated'):
                replace_zip_entries(str(self.psz), {PS_STATE: xml('changed', 'shared')})
        self.assertEqual(self.psz.read_bytes(), before)
        self.assertEqual(list(self.root.glob('.pdps-psz-*.tmp')), [])
        backups = list(self.root.glob('*.bak'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), before)

    def test_stale_snapshot_rejects_external_change(self):
        snapshot = build_snapshot(str(self.psz))
        self.write_zip(xml('externally modified', 'shared'), xml('EMS object', 'shared'))
        changed = self.psz.read_bytes()
        worker = PszRepairWorker(
            str(self.psz), snapshot['contents'], snapshot['encs'], 'delete',
            {'targets': [(PS_STATE, 'shared')]},
            expected_fingerprint=snapshot['fingerprint'],
        )
        failed = []
        worker.failed.connect(failed.append)
        worker.run()
        self.assertEqual(len(failed), 1)
        self.assertIn('已变化', failed[0])
        self.assertEqual(self.psz.read_bytes(), changed)
        self.assertEqual(list(self.root.glob('*.bak')), [])

    def test_successive_repairs_keep_distinct_backups(self):
        first = replace_zip_entries(str(self.psz), {PS_STATE: xml('first', 'shared')})
        second = replace_zip_entries(str(self.psz), {PS_STATE: xml('second', 'shared')})
        self.assertNotEqual(first[1], second[1])
        with zipfile.ZipFile(first[1]) as backup:
            self.assertIn(b'PS object', backup.read(PS_STATE))
        with zipfile.ZipFile(second[1]) as backup:
            self.assertIn(b'first', backup.read(PS_STATE))

    def test_external_xml_rebuild_updates_encoding_declaration(self):
        original = ('<?xml version="1.0" encoding="gbk"?>'
                    '<Root><Objects/></Root>').encode('gbk')
        self.write_zip(original, xml('EMS object', 'ems-id'))
        external = self.root / 'edited.xml'
        external.write_bytes(('<?xml version="1.0" encoding="utf-8"?>'
                              '<Root><Objects><PmLayout ExternalId="new" name="焊点"/>'
                              '</Objects></Root>').encode('utf-8'))
        done, failed = self.run_repair('rebuild', {'xml_path': str(external)})
        self.assertEqual(failed, [])
        self.assertEqual(len(done[0]['entry_objects'][PS_STATE]), 1)
        with zipfile.ZipFile(self.psz) as archive:
            payload = archive.read(PS_STATE)
            self.assertIn(b'encoding="gbk"', payload)
            self.assertIn('焊点', decode_xml(payload))


if __name__ == '__main__':
    unittest.main()
