"""Account isolation for processing caches and their recovery checkpoints."""
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from water_monitoring_app import cache
from water_monitoring_app.config import ALL_PARAMETERS, STATUS_COMPLETE


USER_A = '66ded0d3-96d8-4d53-aa64-4232515178c4'
USER_B = '6884e3c8-8f7c-4ca0-8337-5393e901344a'
CONFIG = {
    'polygon_coords': [[51.0, 35.0], [51.1, 35.0], [51.0, 35.1], [51.0, 35.0]],
    'start_date': '2023-01-01',
    'end_date': '2023-01-31',
}


class CacheIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = {'auth_user_id': USER_A}
        self.streamlit = patch.object(cache, 'st', SimpleNamespace(session_state=self.state))
        self.streamlit.start()
        self.addCleanup(self.streamlit.stop)
        self.root = patch.object(cache, '_cache_root', return_value=self.temp.name)
        self.root.start()
        self.addCleanup(self.root.stop)

    def test_identity_is_required_and_must_be_canonical_uuid(self):
        for identity in (None, '', '../other', USER_A.upper(), 123):
            with self.subTest(identity=identity):
                self.state['auth_user_id'] = identity
                with self.assertRaises(PermissionError):
                    cache.get_cache_dir_for_config(CONFIG)
                with self.assertRaises(PermissionError):
                    cache.prune_old_cache_dirs()

    def test_same_configuration_recovers_only_the_current_users_checkpoint(self):
        first = cache.get_cache_dir_for_config(CONFIG)
        statuses = {'2023-01': {'status': STATUS_COMPLETE}}
        cache._save_month_checkpoint(first, ALL_PARAMETERS[0], statuses)
        cache.save_run_config(first, CONFIG)
        self.assertEqual(cache.get_cache_dir_for_config(CONFIG), first)
        self.assertEqual(cache._load_month_checkpoint(first, ALL_PARAMETERS[0]), statuses)

        self.state.update(auth_user_id=USER_B, current_temp_dir=first)
        second = cache._cache_dir_for_active_run(CONFIG)
        self.assertNotEqual(first, second)
        self.assertEqual(cache._load_month_checkpoint(second, ALL_PARAMETERS[0]), {})
        self.state['auth_user_id'] = USER_A
        self.assertEqual(cache._cache_dir_for_active_run(CONFIG), first)

    def test_invalid_configuration_does_not_reuse_a_default_run(self):
        with self.assertRaises(ValueError):
            cache.get_cache_dir_for_config({})

    def test_reset_and_prune_do_not_remove_other_users_or_legacy_cache(self):
        first = Path(cache.get_cache_dir_for_config(CONFIG))
        (first / 'own').write_text('own')
        legacy = Path(self.temp.name) / 'legacy-run'
        legacy.mkdir()
        (legacy / 'data').write_text('legacy')
        self.state['auth_user_id'] = USER_B
        second = Path(cache.get_cache_dir_for_config(CONFIG))
        (second / 'other').write_text('other')
        cache.reset_run_cache(str(first))
        cache.reset_run_cache(str(legacy))
        self.assertTrue((first / 'own').exists())
        self.assertTrue((legacy / 'data').exists())
        os.utime(first, (1, 1))
        os.utime(second, (1, 1))
        os.utime(legacy, (1, 1))
        cache.prune_old_cache_dirs(max_age_days=1)
        self.assertTrue((first / 'own').exists())
        self.assertTrue((legacy / 'data').exists())
        self.assertFalse(second.exists())

    def test_reset_rejects_namespace_root_and_prefix_sibling(self):
        run = Path(cache.get_cache_dir_for_config(CONFIG))
        user_root = run.parent
        sibling = Path(str(user_root) + '-other')
        sibling.mkdir()
        (sibling / 'data').write_text('sibling')
        cache.reset_run_cache(str(user_root))
        cache.reset_run_cache(str(sibling))
        self.assertTrue(run.exists())
        self.assertTrue((sibling / 'data').exists())


if __name__ == '__main__':
    unittest.main()
