"""Exercise HTTP response and timeout translation used by Earth Engine."""
import unittest
from unittest.mock import Mock

import requests

from water_monitoring_app.earth_engine_transport import EarthEngineTransport


class EarthEngineTransportTests(unittest.TestCase):
    def test_request_preserves_response_and_sets_timeout(self):
        session = Mock()
        session.request.return_value = Mock(
            status_code=200, headers={'content-type': 'application/json'}, content=b'{}'
        )
        transport = EarthEngineTransport(timeout=12, session=session)
        response, content = transport.request('https://example.test', 'POST', body=b'data')
        self.assertEqual(response.status, 200)
        self.assertEqual(content, b'{}')
        session.request.assert_called_once_with(
            'POST', 'https://example.test', data=b'data', headers=None, timeout=12
        )

    def test_requests_timeout_is_visible_to_google_client(self):
        session = Mock()
        session.request.side_effect = requests.exceptions.Timeout('timeout')
        with self.assertRaises(TimeoutError):
            EarthEngineTransport(timeout=1, session=session).request('https://example.test')

    def test_403_does_not_report_ok(self):
        session = Mock()
        session.request.return_value = Mock(status_code=403, reason='Forbidden',
                                            headers={'content-type': 'text/html'},
                                            content=b'<html>Forbidden</html>')
        response, _ = EarthEngineTransport(timeout=1, session=session).request('https://example.test')
        self.assertEqual(response.status, 403)
        self.assertEqual(response.reason, 'Forbidden')


if __name__ == '__main__':
    unittest.main()
