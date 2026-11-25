"""Initialization failures must be safe and stop before retrying every month."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import requests

from water_monitoring_app import cache, processing, satellite, shared_satellite
from water_monitoring_app.config import ALL_PARAMETERS, PARAM_TURBIDITY, STATUS_COMPLETE, STATUS_FAILED
from water_monitoring_app.earth_engine_errors import (EarthEngineInitializationError,
                                                      initialization_error_message,
                                                      is_initialization_message)
from test_shared_processing import State, raster_bytes


class EarthEngineRecoveryTests(unittest.TestCase):
    def test_old_403_html_is_explained_without_echoing_sensitive_response(self):
        old_error = ('2026-01: Earth Engine initialization failed: <HttpError 403 when requesting '
                     'https://example.test/$discovery/rest?key=secret-value returned "Ok". '
                     'Details: "<!DOCTYPE html><html>private-content</html>">')
        message = cache._failure_message(old_error)
        self.assertIn('HTTP 403', message)
        self.assertIn('HTML', message)
        self.assertNotIn('secret-value', message)
        self.assertNotIn('private-content', message)
        self.assertNotIn('https://', message)
        self.assertTrue(is_initialization_message(message))
        self.assertTrue(is_initialization_message(old_error))

    def test_credentials_and_permission_errors_are_safe(self):
        message = initialization_error_message(RuntimeError('HttpError 403 permission secret-value'))
        self.assertIn('HTTP 403', message)
        self.assertIn('Google Cloud', message)
        self.assertNotIn('secret-value', message)
        message = initialization_error_message(RuntimeError('invalid_grant secret-value'))
        self.assertIn('احراز هویت', message)
        self.assertNotIn('secret-value', message)

    def test_proxy_errors_do_not_expose_proxy_credentials(self):
        message = cache._failure_message(requests.exceptions.ProxyError('http://user:secret-value@proxy.invalid'))
        self.assertNotIn('secret-value', message)
        self.assertNotIn('proxy.invalid', message)

    def test_legacy_initialization_failure_stops_after_one_month_and_keeps_cached_result(self):
        with TemporaryDirectory() as folder:
            source = Path(folder, 'shared.tif')
            source.write_bytes(raster_bytes())
            shared_satellite.split_shared_month(str(source), folder, '2023-01')
            cache._save_month_checkpoint(folder, PARAM_TURBIDITY, {
                '2023-01': {'status': STATUS_COMPLETE, 'message': 'saved'}
            })
            state = State(downloaded_months={p: {} for p in ALL_PARAMETERS},
                          month_statuses={p: {} for p in ALL_PARAMETERS},
                          connection_interrupted=False)
            resolve = Mock(side_effect=EarthEngineInitializationError('اتصال ناموفق'))
            with patch.object(processing, 'st', SimpleNamespace(session_state=state)), \
                    patch.object(satellite, 'create_water_quality_collection') as create:
                results, _, downloaded, _ = processing.process_single_parameter(
                    resolve, '2023-01-01', '2023-05-01', PARAM_TURBIDITY, folder, resume=True)
            resolve.assert_called_once()
            create.assert_not_called()
            self.assertTrue(state.connection_interrupted)
            self.assertEqual(downloaded, 1)
            self.assertEqual(results[0]['month_name'], '2023-01')
            self.assertEqual(state.month_statuses[PARAM_TURBIDITY]['2023-02']['status'], STATUS_FAILED)
            self.assertNotIn('2023-03', state.month_statuses[PARAM_TURBIDITY])


if __name__ == '__main__':
    unittest.main()
