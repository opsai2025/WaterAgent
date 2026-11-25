"""Durable per-run history with server-side ownership checks.

Run files live alongside the local accounts database and are never subject to
the temporary monitoring cache's expiration. The database is the authority for
both identities and run ownership; paths from the browser are never accepted.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from uuid import UUID, uuid4

from .auth import AuthError, PermissionDenied


class HistoryError(AuthError):
    """A history operation failed or a stored record is invalid."""


class HistoryConflict(HistoryError):
    """The expert snapshot changed since this session last read it."""


@dataclass(frozen=True)
class HistoryRun:
    id: str
    owner_id: str
    owner_name: str
    config: dict
    status: str
    created_at: str
    generation: int = 0


def _uuid(value):
    try:
        result = str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise HistoryError('شناسه تحلیل معتبر نیست.') from None
    if value != result:
        raise HistoryError('شناسه تحلیل معتبر نیست.')
    return result


def _encode(value):
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    except (ValueError, TypeError, OverflowError) as exc:
        raise HistoryError('داده قابل ذخیره نیست.') from exc


def expert_token(payload):
    """Return a deterministic content token for optimistic snapshot updates."""
    if not isinstance(payload, dict):
        raise HistoryError('اطلاعات تحلیل هوشمند معتبر نیست.')
    try:
        canonical = json.dumps(payload, ensure_ascii=False, allow_nan=False,
                               sort_keys=True, separators=(',', ':')).encode('utf-8')
    except (ValueError, TypeError, OverflowError, UnicodeError) as exc:
        raise HistoryError('اطلاعات تحلیل هوشمند معتبر نیست.') from exc
    return hashlib.sha256(canonical).hexdigest()


def _record(row):
    try:
        config = json.loads(row['config_json'])
        if not isinstance(config, dict):
            raise ValueError('not an object')
        return HistoryRun(row['id'], row['owner_id'], row['owner_name'], config,
                          row['status'], row['created_at'], row['generation'])
    except (KeyError, TypeError, ValueError) as exc:
        raise HistoryError('اطلاعات تحلیل ذخیره‌شده معتبر نیست.') from exc


class HistoryStore:
    """Persist run metadata and checked run directories for local accounts."""

    STATUSES = frozenset(('created', 'running', 'complete', 'incomplete', 'failed'))

    def __init__(self, auth_store):
        self.auth_store = auth_store
        self.root = auth_store.path.parent / 'history'
        self._ensure_directory(self.root)
        with self.auth_store._connect() as db:
            # Serialize schema detection and migration with other app sessions.
            db.execute('BEGIN IMMEDIATE')
            db.execute('''CREATE TABLE IF NOT EXISTS history_runs (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL REFERENCES users(id),
                config_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                generation INTEGER NOT NULL DEFAULT 0
            )''')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(history_runs)')}
            if 'generation' not in columns:
                db.execute('ALTER TABLE history_runs ADD COLUMN generation INTEGER NOT NULL DEFAULT 0')
            db.execute('CREATE INDEX IF NOT EXISTS history_runs_owner ON history_runs(owner_id, created_at)')

    @staticmethod
    def _ensure_directory(path):
        if path.is_symlink():
            raise HistoryError('مسیر ذخیره تحلیل نباید پیوند نمادین باشد.')
        path.mkdir(mode=0o700, exist_ok=True)
        if not path.is_dir() or path.is_symlink():
            raise HistoryError('مسیر ذخیره تحلیل معتبر نیست.')

    @staticmethod
    def _actor(db, actor_id):
        actor = db.execute('SELECT id, role, active FROM users WHERE id = ?', (_uuid(actor_id),)).fetchone()
        if actor is None or not actor['active'] or actor['role'] not in ('admin', 'analyst'):
            raise PermissionDenied('حساب فعال لازم است.')
        return actor

    def _authorized(self, db, actor_id, run_id):
        actor = self._actor(db, actor_id)
        row = db.execute('''SELECT h.*, u.username AS owner_name FROM history_runs h
                            JOIN users u ON u.id = h.owner_id WHERE h.id = ?''',
                         (_uuid(run_id),)).fetchone()
        if row is None or (actor['role'] != 'admin' and row['owner_id'] != actor['id']):
            raise PermissionDenied('دسترسی به تحلیل امکان‌پذیر نیست.')
        return _record(row)

    def _directory(self, owner_id, run_id, *, create=False):
        owner_id, run_id = _uuid(owner_id), _uuid(run_id)
        if self.root.is_symlink() or not self.root.is_dir():
            raise HistoryError('مسیر اصلی تاریخچه معتبر نیست.')
        owner_dir = self.root / owner_id
        run_dir = owner_dir / run_id
        if create:
            self._ensure_directory(owner_dir)
            # A generated UUID must be new; never silently reuse a run directory.
            if run_dir.is_symlink() or run_dir.exists():
                raise HistoryError('پوشه تحلیل از قبل وجود دارد.')
            run_dir.mkdir(mode=0o700)
        if owner_dir.is_symlink() or run_dir.is_symlink() or not run_dir.is_dir():
            raise HistoryError('پوشه تحلیل در دسترس نیست.')
        try:
            run_dir.resolve(strict=True).relative_to(self.root.resolve(strict=True))
        except (ValueError, OSError, RuntimeError) as exc:
            raise HistoryError('مسیر تحلیل معتبر نیست.') from exc
        return run_dir

    def create(self, actor_id, config):
        if not isinstance(config, dict):
            raise HistoryError('تنظیمات تحلیل معتبر نیست.')
        config_json = _encode(config)
        run_id = str(uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        with self.auth_store._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            actor = self._actor(db, actor_id)
            folder = self._directory(actor['id'], run_id, create=True)
            self._write_json(folder, 'config.json', config_json)
            db.execute('''INSERT INTO history_runs
                          (id, owner_id, config_json, status, created_at) VALUES (?, ?, ?, ?, ?)''',
                       (run_id, actor['id'], config_json, 'created', created_at))
            return self._authorized(db, actor_id, run_id)

    def list_runs(self, actor_id):
        with self.auth_store._connect() as db:
            actor = self._actor(db, actor_id)
            base = '''SELECT h.*, u.username AS owner_name FROM history_runs h
                      JOIN users u ON u.id = h.owner_id'''
            if actor['role'] == 'admin':
                rows = db.execute(base + ' ORDER BY h.created_at DESC, h.id DESC')
            else:
                rows = db.execute(base + ' WHERE h.owner_id = ? ORDER BY h.created_at DESC, h.id DESC',
                                  (actor['id'],))
            return [_record(row) for row in rows]

    def get_run(self, actor_id, run_id):
        with self.auth_store._connect() as db:
            return self._authorized(db, actor_id, run_id)

    def run_dir(self, actor_id, run_id):
        record = self.get_run(actor_id, run_id)
        return self._directory(record.owner_id, record.id)

    @contextmanager
    def run_lock(self, actor_id, run_id):
        """Hold an exclusive, nonblocking process lock for this authorized run.

        The lock file is deliberately persistent: unlinking a locked file would
        allow a second process to create and lock a different inode. flock is
        released by the operating system even when its owner crashes.
        """
        try:
            import fcntl
        except ImportError as exc:
            raise HistoryError('قفل تحلیل به محیط لینوکس نیاز دارد.') from exc
        folder = self.run_dir(actor_id, run_id)
        lock_path = folder / '.run.lock'
        flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_CLOEXEC', 0)
        if not hasattr(os, 'O_NOFOLLOW'):
            raise HistoryError('قفل امن تحلیل در این محیط در دسترس نیست.')
        flags |= os.O_NOFOLLOW
        try:
            fd = os.open(lock_path, flags, 0o600)
        except OSError as exc:
            raise HistoryError('فایل قفل تحلیل در دسترس نیست یا معتبر نیست.') from exc
        locked = False
        try:
            file_stat = os.fstat(fd)
            if not stat.S_ISREG(file_stat.st_mode):
                raise HistoryError('فایل قفل تحلیل معتبر نیست.')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in (errno.EACCES, errno.EAGAIN):
                    raise HistoryError('این تحلیل اکنون در حال استفاده است؛ بعداً دوباره تلاش کنید.') from exc
                raise HistoryError('قفل تحلیل دریافت نشد.') from exc
            locked = True
            # Account may have been disabled while opening the lock. Ensure the
            # directory still refers to the authorized run and same lock inode.
            current_folder = self.run_dir(actor_id, run_id)
            current_stat = os.stat(lock_path, follow_symlinks=False)
            if (current_folder != folder or not stat.S_ISREG(current_stat.st_mode)
                    or (current_stat.st_dev, current_stat.st_ino) != (file_stat.st_dev, file_stat.st_ino)):
                raise HistoryError('مسیر قفل تحلیل تغییر کرده است.')
            yield folder
        finally:
            if locked:
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def update_status(self, actor_id, run_id, status):
        # Starting a run must advance the generation used by expert snapshots.
        if status not in self.STATUSES or status == 'running':
            raise HistoryError('وضعیت تحلیل معتبر نیست.')
        with self.auth_store._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            record = self._authorized(db, actor_id, run_id)
            db.execute('UPDATE history_runs SET status = ? WHERE id = ?', (status, record.id))
            return self._authorized(db, actor_id, run_id)

    def begin_processing(self, actor_id, run_id, expected_generation):
        """Invalidate old result snapshots before starting or resuming a run."""
        with self.auth_store._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            record = self._authorized(db, actor_id, run_id)
            if type(expected_generation) is not int or record.generation != expected_generation:
                raise HistoryConflict('نتایج این تحلیل تغییر کرده‌اند؛ تحلیل را از تاریخچه دوباره باز کنید.')
            db.execute("UPDATE history_runs SET status = 'running', generation = generation + 1 WHERE id = ?",
                       (record.id,))
            return self._authorized(db, actor_id, run_id)

    @staticmethod
    def _write_json(folder, name, encoded):
        target = folder / name
        if target.is_symlink():
            raise HistoryError('مسیر فایل تحلیل معتبر نیست.')
        pending = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=folder,
                                             prefix='.history_', suffix='.tmp', delete=False) as stream:
                pending = Path(stream.name)
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(pending, target)
        finally:
            if pending is not None:
                try:
                    pending.unlink(missing_ok=True)
                except OSError:
                    pass

    def _save_payload(self, actor_id, run_id, payload, name):
        if not isinstance(payload, dict):
            raise HistoryError('اطلاعات تحلیل معتبر نیست.')
        encoded = _encode(payload)
        # Serialize authorization and writes against account deactivation and
        # other writers. Replacement leaves either the old or complete new JSON.
        with self.auth_store._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            record = self._authorized(db, actor_id, run_id)
            folder = self._directory(record.owner_id, record.id)
            self._write_json(folder, name, encoded)

    @staticmethod
    def _read_json(folder, name, expected_type):
        target = folder / name
        if target.is_symlink():
            raise HistoryError('مسیر تحلیل هوشمند معتبر نیست.')
        try:
            with target.open(encoding='utf-8') as stream:
                data = json.load(stream)
        except FileNotFoundError:
            return expected_type()
        except (ValueError, UnicodeError) as exc:
            raise HistoryError('اطلاعات تحلیل هوشمند آسیب دیده است.') from exc
        if not isinstance(data, expected_type):
            raise HistoryError('اطلاعات تحلیل هوشمند معتبر نیست.')
        return data

    def _load_payload(self, actor_id, run_id, name):
        folder = self.run_dir(actor_id, run_id)
        return self._read_json(folder, name, dict)

    def save_expert(self, actor_id, run_id, payload):
        self._save_payload(actor_id, run_id, payload, 'expert.json')

    def load_expert(self, actor_id, run_id):
        return self._load_payload(actor_id, run_id, 'expert.json')

    def save_expert_if_unchanged(self, actor_id, run_id, payload, expected_token, expected_generation,
                                 archive_current=False):
        """Commit only while the snapshot and processing generation match.

        When replacing a conversation, persist the verified server snapshot as
        a revision before replacing it. A crash between the two atomic writes
        can leave a duplicate revision but cannot lose the old snapshot.
        """
        new_token = expert_token(payload)
        encoded = _encode(payload)
        with self.auth_store._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            record = self._authorized(db, actor_id, run_id)
            if type(expected_generation) is not int or record.generation != expected_generation:
                raise HistoryConflict('نتایج این تحلیل در نشست دیگری تغییر کرده است.')
            folder = self._directory(record.owner_id, record.id)
            current = self._read_json(folder, 'expert.json', dict)
            if expected_token != expert_token(current):
                raise HistoryConflict('گفت‌وگوی این تحلیل در نشست دیگری تغییر کرده است.')
            if archive_current and current:
                revisions = self._read_json(folder, 'expert_revisions.json', list)
                if any(not isinstance(item, dict) for item in revisions):
                    raise HistoryError('نسخه‌های تحلیل هوشمند معتبر نیستند.')
                revisions.append(current)
                self._write_json(folder, 'expert_revisions.json', _encode(revisions))
            self._write_json(folder, 'expert.json', encoded)
        return new_token

    def save_state(self, actor_id, run_id, payload):
        self._save_payload(actor_id, run_id, payload, 'state.json')

    def load_state(self, actor_id, run_id):
        return self._load_payload(actor_id, run_id, 'state.json')

    def append_expert_revision(self, actor_id, run_id, payload):
        """Preserve an older expert conversation before replacing/resetting it."""
        if not isinstance(payload, dict):
            raise HistoryError('نسخه تحلیل هوشمند معتبر نیست.')
        # Validate before taking the write lock and retain only JSON data types.
        payload = json.loads(_encode(payload))
        with self.auth_store._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            record = self._authorized(db, actor_id, run_id)
            folder = self._directory(record.owner_id, record.id)
            revisions = self._read_json(folder, 'expert_revisions.json', list)
            if any(not isinstance(item, dict) for item in revisions):
                raise HistoryError('نسخه‌های تحلیل هوشمند معتبر نیستند.')
            revisions.append(payload)
            self._write_json(folder, 'expert_revisions.json', _encode(revisions))

    def list_expert_revisions(self, actor_id, run_id):
        folder = self.run_dir(actor_id, run_id)
        revisions = self._read_json(folder, 'expert_revisions.json', list)
        if any(not isinstance(item, dict) for item in revisions):
            raise HistoryError('نسخه‌های تحلیل هوشمند معتبر نیستند.')
        return revisions
