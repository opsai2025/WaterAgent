"""PostgreSQL account store for an explicitly configured server deployment.

This module is not wired into the application yet. Schema setup and the other
durable stores must be completed before selecting it for a deployment.
"""
from contextlib import contextmanager
import os
from uuid import uuid4

from .auth import (AuthError, PermissionDenied, User, _hash_password, _password,
                   _username, _verify_password, _ITERATIONS, _SCHEME)


class PostgresAuthStore:
    def __init__(self, dsn, *, connect=None):
        if not isinstance(dsn, str) or not dsn.strip():
            raise AuthError('اتصال پایگاه داده تنظیم نشده است.')
        self._dsn = dsn
        self._connect_override = connect

    @contextmanager
    def _connect(self):
        if self._connect_override is not None:
            factory = self._connect_override
        else:
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc:
                raise AuthError('درایور پایگاه داده نصب نشده است.') from exc
            options = {'row_factory': dict_row, 'sslmode': 'verify-full', 'connect_timeout': 10}
            rootcert = os.environ.get('WATER_MONITOR_DB_SSLROOTCERT', '').strip()
            if rootcert:
                options['sslrootcert'] = rootcert
            factory = lambda: psycopg.connect(self._dsn, **options)
        try:
            with factory() as db:
                yield db
        except AuthError:
            raise
        except Exception:
            # The connection string and server error must never enter the UI/log.
            raise AuthError('عملیات پایگاه حساب‌ها انجام نشد.') from None

    @staticmethod
    def _user(row):
        return None if row is None else User(str(row['id']), row['username'], row['role'], bool(row['active']))

    @staticmethod
    def _require_admin(db, actor_id):
        row = db.execute('SELECT role, active FROM water_monitor_private.users WHERE id = %s FOR SHARE',
                         (actor_id,)).fetchone()
        if row is None or not row['active'] or row['role'] != 'admin':
            raise PermissionDenied('این عملیات به حساب مدیر فعال نیاز دارد.')

    def has_users(self):
        with self._connect() as db:
            return db.execute('SELECT 1 FROM water_monitor_private.users LIMIT 1').fetchone() is not None

    def bootstrap_admin(self, username, password):
        username, key = _username(username)
        hashed = _hash_password(_password(password))
        user_id = str(uuid4())
        with self._connect() as db:
            db.execute('SELECT pg_advisory_xact_lock(%s)', (74629101,))
            if db.execute('SELECT 1 FROM water_monitor_private.users LIMIT 1').fetchone():
                raise AuthError('مدیر اولیه قبلاً ایجاد شده است.')
            db.execute('''INSERT INTO water_monitor_private.users
                       (id, username, username_key, password_hash, role, active)
                       VALUES (%s, %s, %s, %s, 'admin', TRUE)''', (user_id, username, key, hashed))
        return User(user_id, username, 'admin', True)

    def authenticate(self, username, password):
        try:
            _, key = _username(username)
            if not isinstance(password, str) or len(password) > 1024:
                return None
        except AuthError:
            return None
        with self._connect() as db:
            row = db.execute('SELECT * FROM water_monitor_private.users WHERE username_key = %s',
                             (key,)).fetchone()
        if row is None:
            _verify_password(password, f'{_SCHEME}${_ITERATIONS}$' + '00' * 16 + '$' + '00' * 32)
            return None
        if not _verify_password(password, row['password_hash']) or not row['active']:
            return None
        return self._user(row)

    def get_user(self, user_id):
        with self._connect() as db:
            return self._user(db.execute('SELECT * FROM water_monitor_private.users WHERE id = %s',
                                         (user_id,)).fetchone())

    def list_users(self, actor_id):
        with self._connect() as db:
            self._require_admin(db, actor_id)
            rows = db.execute('SELECT * FROM water_monitor_private.users ORDER BY username_key').fetchall()
            return [self._user(row) for row in rows]

    def create_user(self, actor_id, username, password, role='analyst'):
        username, key = _username(username)
        if role not in ('admin', 'analyst'):
            raise AuthError('نقش انتخاب‌شده معتبر نیست.')
        hashed = _hash_password(_password(password))
        user_id = str(uuid4())
        with self._connect() as db:
            db.execute('SELECT pg_advisory_xact_lock(%s)', (74629101,))
            self._require_admin(db, actor_id)
            db.execute('''INSERT INTO water_monitor_private.users
                       (id, username, username_key, password_hash, role, active)
                       VALUES (%s, %s, %s, %s, %s, TRUE)''', (user_id, username, key, hashed, role))
        return User(user_id, username, role, True)

    def set_active(self, actor_id, user_id, active):
        if not isinstance(active, bool):
            raise AuthError('وضعیت حساب معتبر نیست.')
        with self._connect() as db:
            db.execute('SELECT pg_advisory_xact_lock(%s)', (74629101,))
            self._require_admin(db, actor_id)
            row = db.execute('SELECT * FROM water_monitor_private.users WHERE id = %s FOR UPDATE',
                             (user_id,)).fetchone()
            if row is None:
                raise AuthError('حساب کاربری پیدا نشد.')
            if not active and row['role'] == 'admin' and row['active']:
                count = db.execute("SELECT COUNT(*) AS total FROM water_monitor_private.users WHERE role = 'admin' AND active").fetchone()['total']
                if count <= 1:
                    raise AuthError('آخرین مدیر فعال را نمی‌توان غیرفعال کرد.')
            row = db.execute('UPDATE water_monitor_private.users SET active = %s WHERE id = %s RETURNING *',
                             (active, user_id)).fetchone()
            return self._user(row)

    def change_password(self, user_id, old_password, new_password):
        replacement = _hash_password(_password(new_password))
        with self._connect() as db:
            row = db.execute('SELECT * FROM water_monitor_private.users WHERE id = %s FOR UPDATE',
                             (user_id,)).fetchone()
            if row is None or not row['active'] or not _verify_password(old_password, row['password_hash']):
                raise AuthError('گذرواژه فعلی معتبر نیست یا حساب غیرفعال است.')
            db.execute('UPDATE water_monitor_private.users SET password_hash = %s WHERE id = %s',
                       (replacement, user_id))
