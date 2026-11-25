"""Transactional PostgreSQL history primitives (not yet wired to the UI)."""
import json
from contextlib import contextmanager
from datetime import date
from uuid import uuid4

from .auth import PermissionDenied
from .history import HistoryConflict, HistoryError, HistoryRun, _uuid, expert_token
from .numeric_snapshot import dump_numeric_results


class PostgresHistoryStore:
    def __init__(self, auth_store):
        self.auth_store = auth_store

    @staticmethod
    def _actor(db, actor_id):
        row = db.execute('SELECT id, role, active FROM water_monitor_private.users WHERE id = %s FOR SHARE',
                         (_uuid(actor_id),)).fetchone()
        if row is None or not row['active'] or row['role'] not in ('admin', 'analyst'):
            raise PermissionDenied('حساب فعال لازم است.')
        return row

    @classmethod
    def _authorized(cls, db, actor_id, run_id, *, lock=False):
        actor = cls._actor(db, actor_id)
        query = '''SELECT h.*, u.username AS owner_name FROM water_monitor_private.history_runs h
                   JOIN water_monitor_private.users u ON u.id = h.owner_id WHERE h.id = %s'''
        if lock:
            query += ' FOR UPDATE OF h'
        row = db.execute(query, (_uuid(run_id),)).fetchone()
        if row is None or (actor['role'] != 'admin' and str(row['owner_id']) != str(actor['id'])):
            raise PermissionDenied('دسترسی به تحلیل امکان‌پذیر نیست.')
        return row

    @staticmethod
    def _record(row):
        return HistoryRun(str(row['id']), str(row['owner_id']), row['owner_name'], row['config'],
                          row['status'], row['created_at'].isoformat(), row['generation'])

    def create(self, actor_id, config):
        from psycopg.types.json import Jsonb
        try:
            encoded = json.loads(json.dumps(config, ensure_ascii=False, allow_nan=False))
        except (ValueError, TypeError, OverflowError) as exc:
            raise HistoryError('تنظیمات تحلیل معتبر نیست.') from exc
        if not isinstance(encoded, dict):
            raise HistoryError('تنظیمات تحلیل معتبر نیست.')
        run_id = str(uuid4())
        with self.auth_store._connect() as db:
            actor = self._actor(db, actor_id)
            db.execute('''INSERT INTO water_monitor_private.history_runs
                       (id, owner_id, config, status) VALUES (%s, %s, %s, 'created')''',
                       (run_id, actor['id'], Jsonb(encoded)))
            return self._record(self._authorized(db, actor_id, run_id))

    def list_runs(self, actor_id):
        with self.auth_store._connect() as db:
            actor = self._actor(db, actor_id)
            query = '''SELECT h.*, u.username AS owner_name FROM water_monitor_private.history_runs h
                       JOIN water_monitor_private.users u ON u.id = h.owner_id'''
            if actor['role'] == 'admin':
                rows = db.execute(query + ' ORDER BY h.created_at DESC, h.id DESC').fetchall()
            else:
                rows = db.execute(query + ' WHERE h.owner_id = %s ORDER BY h.created_at DESC, h.id DESC',
                                  (actor['id'],)).fetchall()
            return [self._record(row) for row in rows]

    def get_run(self, actor_id, run_id):
        with self.auth_store._connect() as db:
            return self._record(self._authorized(db, actor_id, run_id))

    @contextmanager
    def run_lock(self, actor_id, run_id):
        """Keep one session-mode advisory lock for a long-running analysis.

        This requires a direct or session-pooler connection: transaction pools
        cannot retain session-level locks. The generation check remains needed
        after acquiring the lock to reject an already stale browser session.
        """
        self.get_run(actor_id, run_id)
        run_id = _uuid(run_id)
        with self.auth_store._connect() as db:
            locked = db.execute('SELECT pg_try_advisory_lock(hashtextextended(%s, 74629101)) AS acquired',
                                (run_id,)).fetchone()['acquired']
            db.commit()  # Never leave the connection idle inside a transaction.
            if not locked:
                raise HistoryConflict('این تحلیل در نشست دیگری در حال پردازش است.')
            try:
                yield
            finally:
                db.execute('SELECT pg_advisory_unlock(hashtextextended(%s, 74629101))', (run_id,))
                db.commit()

    def begin_processing(self, actor_id, run_id, expected_generation):
        with self.auth_store._connect() as db:
            row = self._authorized(db, actor_id, run_id, lock=True)
            if type(expected_generation) is not int or row['generation'] != expected_generation:
                raise HistoryConflict('نتایج این تحلیل تغییر کرده‌اند؛ آن را دوباره باز کنید.')
            db.execute("UPDATE water_monitor_private.history_runs SET status = 'running', generation = generation + 1 WHERE id = %s", (row['id'],))
            return self._record(self._authorized(db, actor_id, run_id))

    def update_status(self, actor_id, run_id, status):
        if status not in ('created', 'complete', 'incomplete', 'failed'):
            raise HistoryError('وضعیت تحلیل معتبر نیست.')
        with self.auth_store._connect() as db:
            row = self._authorized(db, actor_id, run_id, lock=True)
            db.execute('UPDATE water_monitor_private.history_runs SET status = %s WHERE id = %s',
                       (status, row['id']))
            return self._record(self._authorized(db, actor_id, run_id))

    def save_numeric_results(self, actor_id, run_id, expected_generation, results):
        """Atomically persist only numerical monthly fields, not image objects."""
        snapshot = dump_numeric_results(results)
        with self.auth_store._connect() as db:
            row = self._authorized(db, actor_id, run_id, lock=True)
            if type(expected_generation) is not int or row['generation'] != expected_generation:
                raise HistoryConflict('نتایج این تحلیل تغییر کرده‌اند.')
            for parameter, records in snapshot['parameters'].items():
                for item in records:
                    saved = db.execute('''INSERT INTO water_monitor_private.monthly_results AS existing
                        (run_id, parameter, month, status, mean_value, water_coverage, valid_pixels)
                        VALUES (%s, %s, %s::date, 'complete', %s, %s, %s)
                        ON CONFLICT (run_id, parameter, month) DO UPDATE SET
                        status = EXCLUDED.status, mean_value = EXCLUDED.mean_value,
                        water_coverage = EXCLUDED.water_coverage, valid_pixels = EXCLUDED.valid_pixels
                        WHERE existing.status <> 'complete' OR
                         (existing.mean_value IS NOT DISTINCT FROM EXCLUDED.mean_value AND
                          existing.water_coverage IS NOT DISTINCT FROM EXCLUDED.water_coverage AND
                          existing.valid_pixels IS NOT DISTINCT FROM EXCLUDED.valid_pixels)
                        RETURNING 1''',
                        (row['id'], parameter, item['month_name'] + '-01', item['mean_value'],
                         item['water_coverage'], item['valid_pixels'])).fetchone()
                    if saved is None:
                        raise HistoryConflict('نتیجه عددی ذخیره‌شده با داده جدید متفاوت است.')

    def save_month(self, actor_id, run_id, expected_generation, parameter, month, status,
                   *, result=None, message=None, stage=None):
        """Commit one month's status and scalar values together, without rasters."""
        from .config import ALL_PARAMETERS
        if parameter not in ALL_PARAMETERS or status not in ('complete', 'no_data', 'failed', 'skipped'):
            raise HistoryError('وضعیت ماه معتبر نیست.')
        try:
            parsed = date.fromisoformat(month + '-01')
            if parsed.strftime('%Y-%m') != month:
                raise ValueError('invalid month')
        except (ValueError, TypeError) as exc:
            raise HistoryError('ماه تحلیل معتبر نیست.') from exc
        if message is not None and not isinstance(message, str):
            raise HistoryError('پیام وضعیت معتبر نیست.')
        if stage is not None and not isinstance(stage, str):
            raise HistoryError('مرحله وضعیت معتبر نیست.')
        if status == 'complete':
            if not isinstance(result, dict) or result.get('month_name') != month:
                raise HistoryError('نتیجه ماه کامل نیست.')
            scalar = dump_numeric_results({parameter: [result]})['parameters'][parameter][0]
        else:
            if result is not None:
                raise HistoryError('نتیجه عددی برای این وضعیت مجاز نیست.')
            scalar = {'mean_value': None, 'water_coverage': None, 'valid_pixels': None}
        with self.auth_store._connect() as db:
            row = self._authorized(db, actor_id, run_id, lock=True)
            if type(expected_generation) is not int or row['generation'] != expected_generation:
                raise HistoryConflict('نتایج این تحلیل تغییر کرده‌اند.')
            saved = db.execute('''INSERT INTO water_monitor_private.monthly_results AS existing
                (run_id, parameter, month, status, mean_value, water_coverage, valid_pixels, message, stage)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (run_id, parameter, month) DO UPDATE SET
                status = EXCLUDED.status, mean_value = EXCLUDED.mean_value,
                water_coverage = EXCLUDED.water_coverage, valid_pixels = EXCLUDED.valid_pixels,
                message = EXCLUDED.message, stage = EXCLUDED.stage
                WHERE existing.status <> 'complete' OR
                 (EXCLUDED.status = 'complete' AND
                  existing.mean_value IS NOT DISTINCT FROM EXCLUDED.mean_value AND
                  existing.water_coverage IS NOT DISTINCT FROM EXCLUDED.water_coverage AND
                  existing.valid_pixels IS NOT DISTINCT FROM EXCLUDED.valid_pixels)
                RETURNING 1''',
                (row['id'], parameter, parsed, status, scalar['mean_value'],
                 scalar['water_coverage'], scalar['valid_pixels'], message, stage)).fetchone()
            if saved is None:
                raise HistoryConflict('نتیجه عددی ذخیره‌شده با داده جدید متفاوت است.')

    def save_shared_month(self, actor_id, run_id, expected_generation, month, scene_count, results):
        """Commit all three scalar results and their common scene count atomically."""
        from .config import ALL_PARAMETERS
        try:
            parsed = date.fromisoformat(month + '-01')
            if parsed.strftime('%Y-%m') != month:
                raise ValueError('invalid month')
        except (ValueError, TypeError) as exc:
            raise HistoryError('ماه تحلیل معتبر نیست.') from exc
        if type(scene_count) is not int or scene_count < 0:
            raise HistoryError('تعداد صحنه‌ها معتبر نیست.')
        if scene_count == 0:
            if results:
                raise HistoryError('برای ماه بدون تصویر نباید نتیجه عددی ثبت شود.')
            snapshot = None
        else:
            if not isinstance(results, dict) or set(results) != set(ALL_PARAMETERS):
                raise HistoryError('نتایج هر سه شاخص لازم است.')
            try:
                snapshot = dump_numeric_results({p: [results[p]] for p in ALL_PARAMETERS})['parameters']
                if any(snapshot[p][0]['month_name'] != month for p in ALL_PARAMETERS):
                    raise ValueError('month mismatch')
            except (TypeError, ValueError, KeyError) as exc:
                raise HistoryError('نتیجه عددی ماه معتبر نیست.') from exc
        with self.auth_store._connect() as db:
            row = self._authorized(db, actor_id, run_id, lock=True)
            if type(expected_generation) is not int or row['generation'] != expected_generation:
                raise HistoryConflict('نتایج این تحلیل تغییر کرده‌اند.')
            saved = db.execute('''INSERT INTO water_monitor_private.shared_months AS existing
                (run_id, month, scene_count) VALUES (%s, %s, %s)
                ON CONFLICT (run_id, month) DO UPDATE SET scene_count = EXCLUDED.scene_count
                WHERE existing.scene_count = EXCLUDED.scene_count RETURNING 1''',
                (row['id'], parsed, scene_count)).fetchone()
            if saved is None:
                raise HistoryConflict('تعداد صحنه‌های ذخیره‌شده با داده جدید متفاوت است.')
            for parameter in ALL_PARAMETERS:
                scalar = snapshot[parameter][0] if snapshot else {}
                status = 'complete' if snapshot else 'no_data'
                saved = db.execute('''INSERT INTO water_monitor_private.monthly_results AS existing
                    (run_id, parameter, month, status, mean_value, water_coverage, valid_pixels)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (run_id, parameter, month) DO UPDATE SET
                    status = EXCLUDED.status, mean_value = EXCLUDED.mean_value,
                    water_coverage = EXCLUDED.water_coverage, valid_pixels = EXCLUDED.valid_pixels
                    WHERE existing.status NOT IN ('complete', 'no_data') OR
                     (existing.status = EXCLUDED.status AND
                      existing.mean_value IS NOT DISTINCT FROM EXCLUDED.mean_value AND
                      existing.water_coverage IS NOT DISTINCT FROM EXCLUDED.water_coverage AND
                      existing.valid_pixels IS NOT DISTINCT FROM EXCLUDED.valid_pixels)
                    RETURNING 1''',
                    (row['id'], parameter, parsed, status, scalar.get('mean_value'),
                     scalar.get('water_coverage'), scalar.get('valid_pixels'))).fetchone()
                if saved is None:
                    raise HistoryConflict('نتیجه عددی ذخیره‌شده با داده جدید متفاوت است.')

    def load_months(self, actor_id, run_id):
        """Return all persisted statuses, including months without imagery."""
        with self.auth_store._connect() as db:
            row = self._authorized(db, actor_id, run_id)
            entries = db.execute('''SELECT parameter, month, status, mean_value, water_coverage,
                         valid_pixels, message, stage FROM water_monitor_private.monthly_results
                         WHERE run_id = %s ORDER BY parameter, month''', (row['id'],)).fetchall()
        return [dict(entry, month=entry['month'].strftime('%Y-%m')) for entry in entries]

    def load_run_snapshot(self, actor_id, run_id):
        """Read metadata, expert state and monthly data under one row lock."""
        with self.auth_store._connect() as db:
            row = self._authorized(db, actor_id, run_id, lock=True)
            entries = db.execute('''SELECT parameter, month, status, mean_value, water_coverage,
                         valid_pixels, message, stage FROM water_monitor_private.monthly_results
                         WHERE run_id = %s ORDER BY parameter, month''', (row['id'],)).fetchall()
            months = [dict(entry, month=entry['month'].strftime('%Y-%m')) for entry in entries]
            counts = db.execute('''SELECT month, scene_count FROM water_monitor_private.shared_months
                                   WHERE run_id = %s ORDER BY month''', (row['id'],)).fetchall()
            state = dict(row['state'])
            state['shared_scene_counts'] = {item['month'].strftime('%Y-%m'): item['scene_count']
                                            for item in counts}
            return self._record(row), state, row['expert'], months

    def load_numeric_results(self, actor_id, run_id):
        from .config import ALL_PARAMETERS
        from .numeric_snapshot import load_numeric_results
        with self.auth_store._connect() as db:
            row = self._authorized(db, actor_id, run_id)
            entries = db.execute('''SELECT parameter, month, mean_value, water_coverage, valid_pixels
                         FROM water_monitor_private.monthly_results
                         WHERE run_id = %s AND status = 'complete' ORDER BY month''', (row['id'],)).fetchall()
        parameters = {parameter: [] for parameter in ALL_PARAMETERS}
        for entry in entries:
            if entry['parameter'] not in parameters:
                raise HistoryError('شاخص ذخیره‌شده معتبر نیست.')
            parameters[entry['parameter']].append({
                'month_name': entry['month'].strftime('%Y-%m'), 'mean_value': entry['mean_value'],
                'water_coverage': entry['water_coverage'], 'valid_pixels': entry['valid_pixels'],
            })
        return load_numeric_results({'version': 1, 'parameters': parameters})

    def load_expert(self, actor_id, run_id):
        with self.auth_store._connect() as db:
            return self._authorized(db, actor_id, run_id)['expert']

    def load_state(self, actor_id, run_id):
        with self.auth_store._connect() as db:
            return self._authorized(db, actor_id, run_id)['state']

    def save_state(self, actor_id, run_id, expected_generation, state):
        from psycopg.types.json import Jsonb
        try:
            payload = json.loads(json.dumps(state, ensure_ascii=False, allow_nan=False))
        except (TypeError, ValueError, OverflowError) as exc:
            raise HistoryError('وضعیت تحلیل معتبر نیست.') from exc
        if not isinstance(payload, dict):
            raise HistoryError('وضعیت تحلیل معتبر نیست.')
        with self.auth_store._connect() as db:
            row = self._authorized(db, actor_id, run_id, lock=True)
            if type(expected_generation) is not int or row['generation'] != expected_generation:
                raise HistoryConflict('نتایج این تحلیل تغییر کرده‌اند.')
            db.execute('UPDATE water_monitor_private.history_runs SET state = %s WHERE id = %s',
                       (Jsonb(payload), row['id']))

    def save_expert_if_unchanged(self, actor_id, run_id, payload, expected_token,
                                 expected_generation, archive_current=False):
        from psycopg.types.json import Jsonb
        token = expert_token(payload)
        with self.auth_store._connect() as db:
            row = self._authorized(db, actor_id, run_id, lock=True)
            if type(expected_generation) is not int or row['generation'] != expected_generation:
                raise HistoryConflict('نتایج این تحلیل تغییر کرده‌اند.')
            if expert_token(row['expert']) != expected_token:
                raise HistoryConflict('گفت‌وگوی این تحلیل تغییر کرده است.')
            if archive_current and row['expert']:
                db.execute('INSERT INTO water_monitor_private.expert_revisions (run_id, snapshot) VALUES (%s, %s)',
                           (row['id'], Jsonb(row['expert'])))
            db.execute('UPDATE water_monitor_private.history_runs SET expert = %s WHERE id = %s',
                       (Jsonb(payload), row['id']))
        return token

    def list_expert_revisions(self, actor_id, run_id):
        with self.auth_store._connect() as db:
            row = self._authorized(db, actor_id, run_id)
            return [item['snapshot'] for item in db.execute(
                'SELECT snapshot FROM water_monitor_private.expert_revisions WHERE run_id = %s ORDER BY revision',
                (row['id'],)).fetchall()]
