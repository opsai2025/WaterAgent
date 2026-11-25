"""Existing progress must never be silently discarded on a read failure."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from water_monitoring_app import cache
from water_monitoring_app.config import (
    PARAM_TURBIDITY, PARAM_CHLOROPHYLL, RUN_STATE_FILE, STATUS_COMPLETE,
    STATUS_FAILED, STATUS_NO_DATA,
)


class CheckpointIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.path = self.folder / RUN_STATE_FILE

    def test_missing_checkpoint_starts_empty(self):
        self.assertEqual(cache._load_month_checkpoint(str(self.folder), PARAM_TURBIDITY), {})
        statuses = {'2024-01': {'status': STATUS_COMPLETE}}
        cache._save_month_checkpoint(str(self.folder), PARAM_TURBIDITY, statuses)
        self.assertEqual(cache._load_month_checkpoint(str(self.folder), PARAM_TURBIDITY), statuses)

    def test_update_preserves_unrecognized_parameter_and_extension_fields(self):
        prior = {
            'version': 1,
            'future_metadata': {'schema': 'retained'},
            'parameters': {
                PARAM_CHLOROPHYLL: {'2024-01': {'status': STATUS_NO_DATA, 'note': 'retained'}},
                'future parameter': {'2024-02': {'status': STATUS_FAILED, 'detail': 'retained'}},
            },
        }
        self.path.write_text(json.dumps(prior), encoding='utf-8')
        changed = {'2024-03': {'status': STATUS_COMPLETE}}
        cache._save_month_checkpoint(str(self.folder), PARAM_TURBIDITY, changed)
        stored = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(stored['future_metadata'], prior['future_metadata'])
        self.assertEqual(stored['parameters'][PARAM_CHLOROPHYLL], prior['parameters'][PARAM_CHLOROPHYLL])
        self.assertEqual(stored['parameters']['future parameter'], prior['parameters']['future parameter'])
        self.assertEqual(stored['parameters'][PARAM_TURBIDITY], changed)

    def test_corrupt_or_ambiguous_checkpoint_is_never_overwritten(self):
        invalid = (
            b'{broken',
            b'{}',
            b'{"version": true, "parameters": {}}',
            b'{"version": 1, "parameters": []}',
            b'{"version": 1, "parameters": {"x": []}}',
            b'{"version": 1, "parameters": {"x": {"2024-01": {"status": "unknown"}}}}',
            b'{"version": 1, "parameters": {"x": {}, "x": {}}}',
            b'{"version": 1, "parameters": {}, "unknown": NaN}',
            b'\xff',
        )
        for content in invalid:
            with self.subTest(content=content):
                self.path.write_bytes(content)
                with self.assertRaises(cache.CheckpointReadError):
                    cache._load_month_checkpoint(str(self.folder), PARAM_TURBIDITY)
                with self.assertRaises(cache.CheckpointWriteError):
                    cache._save_month_checkpoint(str(self.folder), PARAM_TURBIDITY, {})
                self.assertEqual(self.path.read_bytes(), content)

    def test_read_error_never_overwrites_existing_checkpoint(self):
        content = b'{"version": 1, "parameters": {}}'
        self.path.write_bytes(content)
        with patch('builtins.open', side_effect=PermissionError('read denied')):
            with self.assertRaises(cache.CheckpointReadError):
                cache._save_month_checkpoint(str(self.folder), PARAM_TURBIDITY, {})
        self.assertEqual(self.path.read_bytes(), content)

    def test_invalid_new_status_never_overwrites_checkpoint(self):
        content = b'{"version": 1, "parameters": {}}'
        self.path.write_bytes(content)
        with self.assertRaises(cache.CheckpointWriteError):
            cache._save_month_checkpoint(str(self.folder), PARAM_TURBIDITY,
                                         {'2024-01': {'status': 'invalid'}})
        self.assertEqual(self.path.read_bytes(), content)


if __name__ == '__main__':
    unittest.main()
