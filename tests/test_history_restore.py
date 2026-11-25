"""Offline restoration of real archived raster and checkpoint data."""
from io import BytesIO
from pathlib import Path
import json
import shutil
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from openpyxl import load_workbook
import rasterio
from rasterio.transform import from_origin

from water_monitoring_app import processing, results
from water_monitoring_app.auth import AuthStore, PermissionDenied
from water_monitoring_app.cache import _save_month_checkpoint
from water_monitoring_app.config import (
    ALL_PARAMETERS, PARAM_CDOM, PARAM_CHLOROPHYLL, PARAM_TURBIDITY,
    STATUS_COMPLETE, STATUS_FAILED, STATUS_NO_DATA,
)
from water_monitoring_app.expert_snapshot import dump_expert_state
from water_monitoring_app.history import HistoryStore
from water_monitoring_app.history_restore import restore_run


class Session(dict):
    """Minimal mapping with Streamlit's attribute-style session access."""
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        self[name] = value


class HistoryRestoreTests(unittest.TestCase):
    def setUp(self):
        network = patch('socket.socket.connect', side_effect=AssertionError('Unexpected network access'))
        network.start()
        self.addCleanup(network.stop)
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        auth = AuthStore(Path(temporary.name) / 'accounts.sqlite3')
        self.admin = auth.bootstrap_admin('admin', 'admin-test-password')
        self.owner = auth.create_user(self.admin.id, 'owner', 'owner-test-password')
        self.other = auth.create_user(self.admin.id, 'other', 'other-test-password')
        self.store = HistoryStore(auth)
        self.config = {
            'polygon_coords': [[51., 35.], [51.1, 35.], [51.1, 35.1], [51., 35.1], [51., 35.]],
            'start_date': '2023-01-01', 'end_date': '2023-03-01',
            'scale': 10, 'cloudy_pixel_percentage': 10,
        }
        self.run = self.store.create(self.owner.id, self.config)
        self.folder = Path(self.store.run_dir(self.owner.id, self.run.id))
        self.expected = {
            PARAM_TURBIDITY: ('turbidity', 0.2),
            PARAM_CHLOROPHYLL: ('chlorophyll', 0.5),
            PARAM_CDOM: ('cdom', 3.0),
        }
        for parameter, (short, value) in self.expected.items():
            pixels = np.full((4, 4), value, dtype=np.float32)
            pixels[0, 0] = 0
            rgb = np.stack([np.arange(1, 17, dtype=np.float32).reshape(4, 4) + channel
                            for channel in (0, 1, 2)])
            self.write_tiff(self.folder / f'wq_index_{short}_2023-01.tif', pixels[None, :, :])
            self.write_tiff(self.folder / f'rgb_{short}_2023-01.tif', rgb)
            feb_status = STATUS_FAILED if parameter == PARAM_TURBIDITY else STATUS_NO_DATA
            _save_month_checkpoint(str(self.folder), parameter, {
                '2023-01': {'status': STATUS_COMPLETE, 'message': 'fixture'},
                '2023-02': {'status': feb_status, 'message': 'fixture'},
            })
        self.store.update_status(self.owner.id, self.run.id, 'incomplete')
        self.store.save_state(self.owner.id, self.run.id, {'download_summary': {PARAM_TURBIDITY: [1, 2]}})
        self.expert = {
            'expert_analysis_json': '{"summary": "test"}',
            'executive_summary': 'خلاصه آزمایشی',
            'executive_summary_signature': ('fixture', 1),
            'expert_analysis_signature': ('fixture',),
            'expert_chat_history': [{'role': 'user', 'content': 'پرسش آزمایشی'},
                                    {'role': 'assistant', 'content': 'پاسخ آزمایشی'}],
            'param_briefs': {}, 'param_brief_signatures': {},
        }
        self.store.save_expert(self.owner.id, self.run.id, dump_expert_state(self.expert))

    @staticmethod
    def write_tiff(path, bands):
        with rasterio.open(path, 'w', driver='GTiff', width=bands.shape[2],
                           height=bands.shape[1], count=bands.shape[0], dtype='float32',
                           crs='EPSG:4326', transform=from_origin(51, 35.1, .01, .01)) as dst:
            dst.write(bands)

    def test_restore_results_excel_and_offline_resume(self):
        session = Session(auth_user_id=self.owner.id)
        with patch('water_monitoring_app.satellite.create_water_quality_collection') as create, \
                patch('water_monitoring_app.satellite.get_monthly_composite') as composite, \
                patch('water_monitoring_app.satellite.download_monthly_data') as download:
            restored = restore_run(self.store, self.owner.id, self.run.id, session)
            create.assert_not_called()
            composite.assert_not_called()
            download.assert_not_called()

            self.assertEqual(restored.id, self.run.id)
            self.assertTrue(session.processing_complete)
            self.assertTrue(session.resume_after_interruption)
            self.assertEqual(session.executive_summary, self.expert['executive_summary'])
            self.assertEqual(session.expert_chat_history, self.expert['expert_chat_history'])
            self.assertEqual(session.executive_summary_signature, ('fixture', 1))
            for parameter, (_, expected_mean) in self.expected.items():
                row = session.results[parameter][0]
                self.assertEqual(row['month_name'], '2023-01')
                self.assertAlmostEqual(row['mean_value'], expected_mean, places=5)
                self.assertAlmostEqual(row['water_coverage'], 93.75)
                self.assertEqual(row['valid_pixels'], 15)
                self.assertEqual(row['wq_image'].size, (4, 4))
            self.assertEqual(session.month_statuses[PARAM_TURBIDITY]['2023-02']['status'], STATUS_FAILED)

            with patch.object(results, 'st', SimpleNamespace(session_state=session)):
                workbook = load_workbook(BytesIO(results.generate_combined_timeseries_excel()), data_only=True)
            self.assertEqual(len(workbook.sheetnames), 3)
            for sheet, (_, expected_mean) in zip(workbook.worksheets, self.expected.values()):
                self.assertEqual(sheet.cell(2, 1).value, '2023-01')
                self.assertAlmostEqual(sheet.cell(2, 2).value, expected_mean, places=4)
                self.assertEqual(sheet.cell(2, 3).value, 93.8)
            workbook.close()

            create.return_value = object()
            composite.return_value = (None, 0, {})
            with patch.object(processing, 'st', SimpleNamespace(session_state=session)):
                processed, _, count, _ = processing.process_single_parameter(
                    object(), '2023-01-01', '2023-03-01', PARAM_TURBIDITY,
                    str(self.folder), resume=True)
            self.assertEqual(count, 1)
            self.assertEqual(len(processed), 1)
            self.assertEqual(processed[0]['month_name'], '2023-01')
            self.assertEqual(session.month_statuses[PARAM_TURBIDITY]['2023-02']['status'], STATUS_NO_DATA)
            self.assertEqual(create.call_count, 1)
            self.assertEqual(composite.call_count, 1)
            self.assertEqual(composite.call_args.args[2:4], (2023, 2))
            download.assert_not_called()

    def test_other_analyst_cannot_restore_or_change_session(self):
        session = Session(auth_user_id=self.other.id, sentinel='preserved')
        before = dict(session)
        with self.assertRaises(PermissionDenied):
            restore_run(self.store, self.other.id, self.run.id, session)
        self.assertEqual(session, before)

    def test_shared_run_restores_scene_counts_with_saved_results(self):
        config = dict(self.config, pipeline_version='shared_v1')
        saved = self.store.create(self.owner.id, config)
        folder = Path(self.store.run_dir(self.owner.id, saved.id))
        for path in self.folder.iterdir():
            if path.is_file() and (path.suffix == '.tif' or path.name == 'run_state.json'):
                shutil.copyfile(path, folder / path.name)
        (folder / 'shared_months.json').write_text(json.dumps(
            {'version': 1, 'months': {'2023-01': {'scene_count': 4}}}), encoding='utf-8')
        session = Session(auth_user_id=self.owner.id)
        restore_run(self.store, self.owner.id, saved.id, session)
        self.assertEqual(session.shared_scene_counts, {'2023-01': 4})
        self.assertEqual(len(session.results[PARAM_TURBIDITY]), 1)

    def test_admin_can_restore_another_users_run(self):
        session = Session(auth_user_id=self.admin.id)
        restored = restore_run(self.store, self.admin.id, self.run.id, session)
        self.assertEqual(restored.owner_id, self.owner.id)
        self.assertEqual(session.expert_chat_history, self.expert['expert_chat_history'])
        self.assertEqual(set(session.results), set(ALL_PARAMETERS))

    def test_corrupt_tiff_requires_resumption_instead_of_showing_stale_results(self):
        image = self.folder / 'wq_index_turbidity_2023-01.tif'
        image.write_bytes(b'not a valid GeoTIFF')
        session = Session(auth_user_id=self.owner.id)
        restore_run(self.store, self.owner.id, self.run.id, session)
        self.assertTrue(session.resume_after_interruption)
        self.assertFalse(session.results[PARAM_TURBIDITY])
        self.assertNotIn('2023-01', session.downloaded_months[PARAM_TURBIDITY])
        self.assertTrue(session.results[PARAM_CHLOROPHYLL])

    def test_invalid_expert_snapshot_never_partially_overwrites_session(self):
        self.store.save_expert(self.owner.id, self.run.id, {'version': 1})
        session = Session(auth_user_id=self.owner.id, sentinel='preserved')
        before = dict(session)
        with self.assertRaises(ValueError):
            restore_run(self.store, self.owner.id, self.run.id, session)
        self.assertEqual(session, before)

    def test_stale_counts_from_an_interrupted_run_are_not_displayed(self):
        self.store.save_state(self.owner.id, self.run.id,
                              {'download_summary': {PARAM_TURBIDITY: [0, 0]}})
        session = Session(auth_user_id=self.owner.id)
        restore_run(self.store, self.owner.id, self.run.id, session)
        self.assertEqual(session.download_summary, {})
        self.assertEqual(len(session.results[PARAM_TURBIDITY]), 1)

    def test_unsaved_expert_changes_block_replacing_the_session(self):
        session = Session(auth_user_id=self.owner.id, history_unsaved=True,
                          expert_chat_history=[{'role': 'user', 'content': 'unsaved'}])
        before = dict(session)
        with self.assertRaises(ValueError):
            restore_run(self.store, self.owner.id, self.run.id, session)
        self.assertEqual(session, before)


if __name__ == '__main__':
    unittest.main()
