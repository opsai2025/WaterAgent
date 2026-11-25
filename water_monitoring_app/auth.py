"""Local accounts for the single-computer deployment.

Account metadata is kept separately from temporary monitoring caches.  Passwords
are never stored in plain text, and the first administrator must be provisioned
explicitly by an operator with local access.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import getpass
import hashlib
import hmac
import os
from pathlib import Path
import secrets
import sqlite3
import unicodedata
from uuid import uuid4


_ITERATIONS = 600_000
_SCHEME = 'pbkdf2_sha256'
_DEFAULT_DB = Path(__file__).resolve().parent.parent / '.water_monitor_data' / 'users.sqlite3'
_ROLES = frozenset(('admin', 'analyst'))


class AuthError(Exception):
    """Invalid account operation or invalid supplied account data."""


class PermissionDenied(AuthError):
    """The acting account cannot perform this operation."""


@dataclass(frozen=True)
class User:
    id: str
    username: str
    role: str
    active: bool


def _username(value):
    if not isinstance(value, str):
        raise AuthError('نام کاربری معتبر نیست.')
    value = unicodedata.normalize('NFKC', value.strip())
    if not value or len(value) > 64 or any(c.isspace() or unicodedata.category(c).startswith('C') for c in value):
        raise AuthError('نام کاربری باید حداکثر ۶۴ نویسه و بدون فاصله یا نویسه کنترلی باشد.')
    return value, value.casefold()


def _password(value):
    if not isinstance(value, str) or not 8 <= len(value) <= 1024:
        raise AuthError('گذرواژه باید بین ۸ تا ۱۰۲۴ نویسه داشته باشد.')
    # Disallow surrogates that cannot be encoded to UTF-8.
    try:
        value.encode('utf-8')
    except UnicodeEncodeError as exc:
        raise AuthError('گذرواژه معتبر نیست.') from exc
    return value


def _hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, _ITERATIONS)
    return f'{_SCHEME}${_ITERATIONS}${salt.hex()}${digest.hex()}'


def _verify_password(password, stored):
    try:
        scheme, iterations, salt, expected = stored.split('$')
        rounds = int(iterations)
        if scheme != _SCHEME or not 100_000 <= rounds <= 2_000_000:
            return False
        salt_bytes, expected_bytes = bytes.fromhex(salt), bytes.fromhex(expected)
        if len(salt_bytes) != 16 or len(expected_bytes) != 32:
            return False
        actual = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt_bytes, rounds)
        return hmac.compare_digest(actual, expected_bytes)
    except (ValueError, TypeError, UnicodeEncodeError, AttributeError):
        return False


def _user(row):
    return None if row is None else User(row['id'], row['username'], row['role'], bool(row['active']))


class AuthStore:
    """SQLite account store; mutating account operations verify administrator status."""

    def __init__(self, path=None):
        configured = os.environ.get('WATER_MONITOR_AUTH_DB')
        self.path = Path(path if path is not None else configured or _DEFAULT_DB).expanduser().absolute()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # SQLite uses the already-created file and does not expose a transient
        # world-readable file before restrictive permissions are applied.
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
        with self._connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                username_key TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('admin', 'analyst')),
                active INTEGER NOT NULL CHECK(active IN (0, 1))
            )''')

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(str(self.path), timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _require_admin(db, actor_id):
        actor = db.execute('SELECT * FROM users WHERE id = ?', (actor_id,)).fetchone()
        if actor is None or not actor['active'] or actor['role'] != 'admin':
            raise PermissionDenied('این عملیات به حساب مدیر فعال نیاز دارد.')

    def has_users(self):
        with self._connect() as db:
            return db.execute('SELECT 1 FROM users LIMIT 1').fetchone() is not None

    def bootstrap_admin(self, username, password):
        username, key = _username(username)
        password_hash = _hash_password(_password(password))
        user_id = str(uuid4())
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM users LIMIT 1').fetchone():
                raise AuthError('مدیر اولیه قبلاً ایجاد شده است.')
            db.execute('INSERT INTO users VALUES (?, ?, ?, ?, ?, 1)',
                       (user_id, username, key, password_hash, 'admin'))
        return User(user_id, username, 'admin', True)

    def authenticate(self, username, password):
        try:
            _, key = _username(username)
            if not isinstance(password, str) or len(password) > 1024:
                return None
        except AuthError:
            return None
        with self._connect() as db:
            row = db.execute('SELECT * FROM users WHERE username_key = ?', (key,)).fetchone()
        if row is None:
            # Match the expensive password verification path for unknown users.
            _verify_password(password, f'{_SCHEME}${_ITERATIONS}$' + '00' * 16 + '$' + '00' * 32)
            return None
        if not _verify_password(password, row['password_hash']) or not row['active']:
            return None
        return _user(row)

    def get_user(self, user_id):
        with self._connect() as db:
            return _user(db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone())

    def list_users(self, actor_id):
        with self._connect() as db:
            self._require_admin(db, actor_id)
            return [_user(row) for row in db.execute('SELECT * FROM users ORDER BY username_key')]

    def create_user(self, actor_id, username, password, role='analyst'):
        username, key = _username(username)
        if role not in _ROLES:
            raise AuthError('نقش انتخاب‌شده معتبر نیست.')
        password_hash = _hash_password(_password(password))
        user_id = str(uuid4())
        try:
            with self._connect() as db:
                db.execute('BEGIN IMMEDIATE')
                self._require_admin(db, actor_id)
                db.execute('INSERT INTO users VALUES (?, ?, ?, ?, ?, 1)',
                           (user_id, username, key, password_hash, role))
        except sqlite3.IntegrityError as exc:
            raise AuthError('این نام کاربری قبلاً ثبت شده است.') from exc
        return User(user_id, username, role, True)

    def set_active(self, actor_id, user_id, active):
        if not isinstance(active, bool):
            raise AuthError('وضعیت حساب معتبر نیست.')
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._require_admin(db, actor_id)
            row = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
            if row is None:
                raise AuthError('حساب کاربری پیدا نشد.')
            if not active and row['role'] == 'admin' and row['active']:
                admins = db.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND active = 1").fetchone()[0]
                if admins <= 1:
                    raise AuthError('آخرین مدیر فعال را نمی‌توان غیرفعال کرد.')
            db.execute('UPDATE users SET active = ? WHERE id = ?', (int(active), user_id))
            return _user(db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone())

    def change_password(self, user_id, old_password, new_password):
        replacement = _hash_password(_password(new_password))
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
            if row is None or not row['active'] or not _verify_password(old_password, row['password_hash']):
                raise AuthError('گذرواژه فعلی معتبر نیست یا حساب غیرفعال است.')
            db.execute('UPDATE users SET password_hash = ? WHERE id = ?', (replacement, user_id))


def main(argv=None):
    parser = argparse.ArgumentParser(description='مدیریت حساب اولیه سامانه پایش کیفیت آب')
    parser.add_argument('--db', help='مسیر پایگاه حساب‌ها (اختیاری)')
    parser.add_argument('command', choices=['bootstrap-admin'])
    args = parser.parse_args(argv)
    try:
        store = AuthStore(args.db)
        if store.has_users():
            raise AuthError('مدیر اولیه قبلاً ایجاد شده است.')
        username = input('نام کاربری مدیر: ')
        password = getpass.getpass('گذرواژه مدیر (حداقل ۸ نویسه): ')
        confirmation = getpass.getpass('تکرار گذرواژه: ')
        if password != confirmation:
            raise AuthError('گذرواژه و تکرار آن یکسان نیستند.')
        store.bootstrap_admin(username, password)
        print('حساب مدیر اولیه ایجاد شد.')
        return 0
    except (AuthError, OSError, sqlite3.Error, EOFError, KeyboardInterrupt) as exc:
        print(f'ایجاد مدیر انجام نشد: {exc}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
