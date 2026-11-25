"""TLS configuration is passed to the actual PostgreSQL account store."""
import os
import unittest
from unittest.mock import MagicMock, patch

from water_monitoring_app.postgres_auth import PostgresAuthStore


class PostgresConnectionConfigTests(unittest.TestCase):
    def test_verified_tls_and_configured_ca_reach_driver(self):
        connection = MagicMock()
        with patch.dict(os.environ, {'WATER_MONITOR_DB_SSLROOTCERT': '/private/test-ca.crt'}), \
                patch('psycopg.connect', return_value=connection) as connect:
            with PostgresAuthStore('dbname=test')._connect():
                pass
        self.assertEqual(connect.call_args.kwargs['sslmode'], 'verify-full')
        self.assertEqual(connect.call_args.kwargs['sslrootcert'], '/private/test-ca.crt')
        self.assertEqual(connect.call_args.kwargs['connect_timeout'], 10)


if __name__ == '__main__':
    unittest.main()
