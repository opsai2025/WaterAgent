"""Deployment checks are read-only and do not expose connection secrets."""
import os
import unittest
from unittest.mock import MagicMock, Mock, patch

from water_monitoring_app.deployment_check import check_database


class DeploymentCheckTests(unittest.TestCase):
    def test_missing_configuration_does_not_connect(self):
        connect = Mock()
        ok, _ = check_database({}, connect)
        self.assertFalse(ok)
        connect.assert_not_called()

    def test_tls_verification_and_read_only_query(self):
        connection = MagicMock()
        connection.__enter__.return_value.execute.return_value.fetchone.return_value = (1,)
        connect = Mock(return_value=connection)
        dsn = 'postgresql://user:secret@db.example.invalid:5432/postgres'
        ok, message = check_database({'WATER_MONITOR_DATABASE_URL': dsn,
                                      'WATER_MONITOR_DB_SSLROOTCERT': '/tmp/ca.crt'}, connect)
        self.assertTrue(ok)
        self.assertNotIn('secret', message)
        self.assertEqual(connect.call_args.kwargs, {'sslmode': 'verify-full', 'connect_timeout': 10,
                                                    'sslrootcert': '/tmp/ca.crt'})
        connection.__enter__.return_value.execute.assert_called_once_with('SELECT 1')

    def test_failure_does_not_expose_dsn_or_server_error(self):
        dsn = 'postgresql://user:secret@db.example.invalid:5432/postgres'
        ok, message = check_database({'WATER_MONITOR_DATABASE_URL': dsn},
                                     Mock(side_effect=RuntimeError('secret db.example.invalid')))
        self.assertFalse(ok)
        self.assertNotIn('secret', message)
        self.assertNotIn('db.example.invalid', message)

    def test_certificate_variable_creates_a_temporary_file_only_during_connection(self):
        paths = []
        connection = MagicMock()
        connection.__enter__.return_value.execute.return_value.fetchone.return_value = (1,)
        def connect(dsn, **kwargs):
            path = kwargs['sslrootcert']
            paths.append(path)
            with open(path, encoding='utf-8') as stream:
                self.assertEqual(stream.read(), 'fake-pem\n')
            return connection
        with patch('ssl.PEM_cert_to_DER_cert', return_value=b'test'):
            ok, _ = check_database({'WATER_MONITOR_DATABASE_URL': 'postgresql://user@db.invalid/db',
                                    'WATER_MONITOR_DB_CA_PEM': 'fake-pem'}, connect)
        self.assertTrue(ok)
        self.assertFalse(os.path.exists(paths[0]))


if __name__ == '__main__':
    unittest.main()
