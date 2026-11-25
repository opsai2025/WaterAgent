"""Behavioral tests for local account management and authorization."""
from pathlib import Path
import sqlite3
import tempfile
import unittest

from water_monitoring_app.auth import AuthError, AuthStore, PermissionDenied


class AuthStoreTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'accounts.sqlite3'
        self.store = AuthStore(self.path)
        self.admin = self.store.bootstrap_admin('admin', 'admin-password')

    def test_login_hash_and_bootstrap_only_once(self):
        self.assertEqual(self.store.authenticate('ADMIN', 'admin-password'), self.admin)
        self.assertIsNone(self.store.authenticate('admin', 'incorrect'))
        self.assertIsNone(self.store.authenticate('nobody', 'incorrect'))
        with sqlite3.connect(self.path) as db:
            password_hash = db.execute('SELECT password_hash FROM users').fetchone()[0]
        self.assertNotIn('admin-password', password_hash)
        self.assertTrue(password_hash.startswith('pbkdf2_sha256$'))
        with self.assertRaises(AuthError):
            self.store.bootstrap_admin('other', 'other-password')

    def test_admin_management_and_analyst_denial(self):
        analyst = self.store.create_user(self.admin.id, 'تحلیلگر', 'analyst-password')
        self.assertEqual(analyst.role, 'analyst')
        self.assertEqual(len(self.store.list_users(self.admin.id)), 2)
        for operation in (
            lambda: self.store.list_users(analyst.id),
            lambda: self.store.create_user(analyst.id, 'intruder', 'intruder-password'),
            lambda: self.store.set_active(analyst.id, self.admin.id, False),
        ):
            with self.assertRaises(PermissionDenied):
                operation()
        self.store.set_active(self.admin.id, analyst.id, False)
        self.assertIsNone(self.store.authenticate('تحلیلگر', 'analyst-password'))
        self.assertFalse(self.store.get_user(analyst.id).active)
        self.store.set_active(self.admin.id, analyst.id, True)
        self.assertEqual(self.store.authenticate('تحلیلگر', 'analyst-password'), analyst)

    def test_unique_names_and_last_active_admin(self):
        with self.assertRaises(AuthError):
            self.store.create_user(self.admin.id, 'ADMIN', 'another-password')
        with self.assertRaises(AuthError):
            self.store.set_active(self.admin.id, self.admin.id, False)
        second = self.store.create_user(self.admin.id, 'second', 'second-password', 'admin')
        self.store.set_active(self.admin.id, second.id, False)
        with self.assertRaises(PermissionDenied):
            self.store.list_users(second.id)

    def test_password_change_and_persistence(self):
        analyst = self.store.create_user(self.admin.id, 'analyst', 'old-password')
        with self.assertRaises(AuthError):
            self.store.change_password(analyst.id, 'wrong-password', 'new-password')
        self.store.change_password(analyst.id, 'old-password', 'new-password')
        reopened = AuthStore(self.path)
        self.assertIsNone(reopened.authenticate('analyst', 'old-password'))
        self.assertEqual(reopened.authenticate('analyst', 'new-password'), analyst)

    def test_validation_rejects_bad_inputs(self):
        for username, password, role in (
            ('has spaces', 'valid-password', 'analyst'),
            ('name', 'short', 'analyst'),
            ('name', 'valid-password', 'owner'),
        ):
            with self.assertRaises(AuthError):
                self.store.create_user(self.admin.id, username, password, role)


if __name__ == '__main__':
    unittest.main()
