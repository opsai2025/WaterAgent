"""History integration around starting and interrupting a monitoring run."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from water_monitoring_app.auth import AuthStore, PermissionDenied
from water_monitoring_app import cache
from water_monitoring_app.config import ALL_PARAMETERS
from water_monitoring_app.history import HistoryStore


CONFIG = {'polygon_coords': [[51, 35], [51.1, 35], [51, 35.1], [51, 35]],
          'start_date': '2023-01-01', 'end_date': '2023-03-01',
          'scale': 10, 'cloudy_pixel_percentage': 10}


class State(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None

    def __setattr__(self, name, value):
        self[name] = value


class HistoryExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.auth = AuthStore(self.root / 'users.sqlite3')
        self.admin = self.auth.bootstrap_admin('admin', 'admin-password')
        self.first = self.auth.create_user(self.admin.id, 'first', 'first-password')
        self.other = self.auth.create_user(self.admin.id, 'other', 'other-password')
        self.history = HistoryStore(self.auth)
        self.state = State(auth_user_id=self.first.id, current_temp_dir=None)
        fake_st = SimpleNamespace(session_state=self.state, error=lambda *a, **k: None,
                                  warning=lambda *a, **k: None)
        for mock in (patch.object(cache, 'st', fake_st),
                     patch.object(cache, '_cache_root', return_value=str(self.root / 'cache')),
                     patch.dict(os.environ, {'WATER_MONITOR_AUTH_DB': str(self.auth.path)})):
            mock.start()
            self.addCleanup(mock.stop)
        (self.root / 'cache').mkdir()

    def test_new_runs_keep_separate_files_and_reuse_only_owner_cache(self):
        legacy = Path(cache.get_cache_dir_for_config(CONFIG))
        (legacy / 'wq_index_turbidity_2023-01.tif').write_bytes(b'old')
        self.state['expert_chat_history'] = [{'role': 'user', 'content': 'old chat'}]
        cache._prepare_monitoring_run(CONFIG)
        first_id = self.state.active_history_id
        first_dir = Path(self.state.current_temp_dir)
        self.assertEqual((first_dir / 'wq_index_turbidity_2023-01.tif').read_bytes(), b'old')
        self.assertEqual(self.state.expert_chat_history, [])
        self.assertEqual(cache._cache_dir_for_active_run(CONFIG), str(first_dir))
        cache._prepare_monitoring_run(CONFIG)
        second_id = self.state.active_history_id
        second_dir = Path(self.state.current_temp_dir)
        self.assertNotEqual(first_id, second_id)
        self.assertEqual((second_dir / 'wq_index_turbidity_2023-01.tif').read_bytes(), b'old')
        (second_dir / 'wq_index_turbidity_2023-01.tif').write_bytes(b'new')
        self.assertEqual((first_dir / 'wq_index_turbidity_2023-01.tif').read_bytes(), b'old')
        self.assertEqual((legacy / 'wq_index_turbidity_2023-01.tif').read_bytes(), b'old')
        self.state.auth_user_id = self.other.id
        with self.assertRaises(PermissionDenied):
            cache._cache_dir_for_active_run(CONFIG)

    def test_interrupted_run_is_persisted_and_remains_resumable(self):
        cache._prepare_monitoring_run(CONFIG)
        run_id = self.state.active_history_id
        with patch('water_monitoring_app.processing.run_full_analysis',
                   side_effect=ConnectionError('offline')):
            cache._execute_monitoring_run(CONFIG, resume=False)
        self.assertEqual(self.history.get_run(self.first.id, run_id).status, 'incomplete')
        self.assertTrue(self.state.resume_after_interruption)
        self.assertFalse(self.state.processing_in_progress)
        self.assertTrue((self.history.run_dir(self.first.id, run_id) / 'run_config.json').exists())

    def test_stale_session_cannot_resume_or_overwrite_current_run_state(self):
        cache._prepare_monitoring_run(CONFIG)
        run_id = self.state.active_history_id
        self.assertEqual(self.state.history_generation, 0)
        self.history.begin_processing(self.first.id, run_id, 0)
        self.history.save_state(self.first.id, run_id, {'download_summary': {'new': [1, 1]}})
        self.history.update_status(self.first.id, run_id, 'complete')
        self.state.download_summary = {'old': [0, 0]}
        with patch('water_monitoring_app.processing.run_full_analysis') as run_analysis:
            cache._execute_monitoring_run(CONFIG, resume=True)
        run_analysis.assert_not_called()
        self.assertEqual(self.history.get_run(self.first.id, run_id).status, 'complete')
        self.assertEqual(self.history.load_state(self.first.id, run_id),
                         {'download_summary': {'new': [1, 1]}})
        self.assertTrue(self.state.resume_after_interruption)
        with self.history.run_lock(self.first.id, run_id):
            pass

    def test_checkpoint_write_failure_is_not_silently_ignored(self):
        folder = self.root / 'cache'
        with patch.object(cache, '_write_cache_json', return_value=False):
            with self.assertRaises(OSError):
                cache._save_month_checkpoint(str(folder), ALL_PARAMETERS[0], {})

    def test_checkpoint_failure_stops_full_analysis(self):
        from water_monitoring_app import processing

        class Placeholder:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def __getattr__(self, name):
                return lambda *args, **kwargs: None

        cache._prepare_monitoring_run(CONFIG)
        ui = SimpleNamespace(session_state=self.state,
                             empty=lambda: Placeholder(),
                             progress=lambda *args: Placeholder(),
                             spinner=lambda *args: Placeholder())
        with patch.object(processing, 'st', ui), \
                patch.object(processing, 'process_single_parameter',
                             side_effect=cache.CheckpointWriteError('disk full')):
            with self.assertRaises(cache.CheckpointWriteError):
                processing.run_full_analysis(None, CONFIG['start_date'], CONFIG['end_date'])


if __name__ == '__main__':
    unittest.main()
