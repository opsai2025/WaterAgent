"""Windows proxy fallback stays scoped to EE and respects explicit settings."""
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from water_monitoring_app import earth_engine_network as network


class EarthEngineNetworkTests(unittest.TestCase):
    def setUp(self):
        network.clear_windows_proxy_cache()

    def tearDown(self):
        network.clear_windows_proxy_cache()

    def test_proxy_is_queried_per_origin_without_sending_signed_url(self):
        result = SimpleNamespace(returncode=0, stdout='http://proxy.invalid:8080')
        with patch.dict(os.environ, {'WSL_DISTRO_NAME': 'ubuntu-monk'}, clear=True), \
                patch.object(network.subprocess, 'run', return_value=result) as run:
            kwargs = network.earth_engine_proxy_kwargs('https://earthengine.googleapis.com/image?token=secret-value')
            self.assertEqual(kwargs['proxies']['https'], 'http://proxy.invalid:8080')
            network.earth_engine_proxy_kwargs('https://earthengine.googleapis.com/another?token=other-secret')
            run.assert_called_once()
            self.assertNotIn('secret-value', repr(run.call_args))
            self.assertNotIn('other-secret', repr(run.call_args))
            network.clear_windows_proxy_cache()
            network.earth_engine_proxy_kwargs('https://earthengine.googleapis.com/')
            self.assertEqual(run.call_count, 2)

    def test_explicit_proxy_and_no_proxy_are_respected(self):
        for environ in ({'WSL_DISTRO_NAME': 'ubuntu-monk', 'HTTPS_PROXY': 'http://chosen.invalid'},
                        {'WSL_DISTRO_NAME': 'ubuntu-monk', 'NO_PROXY': 'earthengine.googleapis.com'},
                        {}):
            with self.subTest(environ=environ), patch.dict(os.environ, environ, clear=True), \
                    patch.object(network.subprocess, 'run') as run:
                self.assertEqual(network.earth_engine_proxy_kwargs('https://earthengine.googleapis.com/'), {})
                run.assert_not_called()

    def test_unavailable_windows_proxy_fails_without_exposing_details(self):
        result = SimpleNamespace(returncode=1, stdout='secret-value')
        with patch.dict(os.environ, {'WSL_DISTRO_NAME': 'ubuntu-monk'}, clear=True), \
                patch.object(network.subprocess, 'run', return_value=result):
            self.assertEqual(network.earth_engine_proxy_kwargs('https://earthengine.googleapis.com/'), {})

    def test_session_preserves_proxy_explicitly_passed_by_caller(self):
        chosen = {'https': 'http://chosen.invalid'}
        with patch.object(network, 'earth_engine_proxy_kwargs') as fallback, \
                patch('requests.Session.request', return_value='response') as request:
            result = network.EarthEngineSession().request('GET', 'https://example.invalid', proxies=chosen)
        self.assertEqual(result, 'response')
        fallback.assert_not_called()
        self.assertEqual(request.call_args.kwargs['proxies'], chosen)


if __name__ == '__main__':
    unittest.main()
