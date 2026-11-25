"""Opt-in isolated PostgreSQL integration; never connects to Supabase."""
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from io import BytesIO
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from water_monitoring_app.auth import AuthError, PermissionDenied
from water_monitoring_app.postgres_auth import PostgresAuthStore
from water_monitoring_app.postgres_history import PostgresHistoryStore
from water_monitoring_app.history import HistoryConflict, expert_token
from water_monitoring_app.config import ALL_PARAMETERS
from water_monitoring_app.postgres_restore import restore_postgres_run
from water_monitoring_app import results as result_ui


@unittest.skipUnless(os.environ.get('WATER_MONITOR_TEST_POSTGRES') == '1',
                     'requires an isolated local PostgreSQL test server')
class PostgresAuthIntegrationTests(unittest.TestCase):
    def test_accounts_and_roles_on_real_postgres(self):
        import psycopg
        from psycopg import sql
        from psycopg.rows import dict_row

        name = 'wq_test_' + uuid4().hex
        with psycopg.connect('dbname=postgres', autocommit=True) as admin:
            admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        try:
            def connection():
                return psycopg.connect(dbname=name, row_factory=dict_row)

            root = Path(__file__).resolve().parents[1]
            schema = (root / 'supabase/migrations/20261115000000_initial_water_monitor.sql').read_text(encoding='utf-8')
            self.assertEqual(schema, (root / 'sql/postgres_schema.sql').read_text(encoding='utf-8'))
            with connection() as db:
                db.execute(schema)
            store = PostgresAuthStore('local-test', connect=connection)
            self.assertFalse(store.has_users())
            admin = store.bootstrap_admin('admin', 'test-admin-password')
            self.assertEqual(store.authenticate('ADMIN', 'test-admin-password'), admin)
            self.assertIsNone(store.authenticate('admin', 'incorrect'))
            with self.assertRaises(AuthError):
                store.bootstrap_admin('another', 'test-other-password')
            analyst = store.create_user(admin.id, 'analyst', 'test-analyst-password')
            with self.assertRaises(PermissionDenied):
                store.list_users(analyst.id)
            with self.assertRaises(AuthError):
                store.set_active(admin.id, admin.id, False)
            store.change_password(analyst.id, 'test-analyst-password', 'test-new-password')
            self.assertEqual(store.authenticate('analyst', 'test-new-password'), analyst)
            store.set_active(admin.id, analyst.id, False)
            self.assertIsNone(store.authenticate('analyst', 'test-new-password'))
            self.assertFalse(store.get_user(analyst.id).active)
            store.set_active(admin.id, analyst.id, True)

            history = PostgresHistoryStore(store)
            run = history.create(analyst.id, {'start_date': '2023-01-01', 'end_date': '2023-02-01'})
            other = store.create_user(admin.id, 'other', 'test-other-password')
            with self.assertRaises(PermissionDenied):
                history.get_run(other.id, run.id)
            self.assertEqual(history.get_run(admin.id, run.id).owner_id, analyst.id)
            with self.assertRaises(PermissionDenied):
                with history.run_lock(other.id, run.id):
                    pass
            with history.run_lock(analyst.id, run.id):
                with self.assertRaises(HistoryConflict):
                    with history.run_lock(admin.id, run.id):
                        pass
            with self.assertRaises(HistoryConflict):
                with history.run_lock(analyst.id, run.id):
                    raise HistoryConflict('interrupted')
            with history.run_lock(admin.id, run.id):
                pass
            started = history.begin_processing(analyst.id, run.id, run.generation)
            with self.assertRaises(HistoryConflict):
                history.begin_processing(analyst.id, run.id, run.generation)
            history.save_state(analyst.id, run.id, started.generation, {'download_summary': {'done': 1}})
            with self.assertRaises(HistoryConflict):
                history.save_state(analyst.id, run.id, run.generation, {'download_summary': {'done': 0}})
            self.assertEqual(history.load_state(admin.id, run.id), {'download_summary': {'done': 1}})
            row = {'month_name': '2023-01', 'mean_value': 0.42, 'water_coverage': 82.5,
                   'valid_pixels': 33, 'rgb_image': object(), 'wq_image': object()}
            shared = history.create(analyst.id, {'start_date': '2023-01-01', 'end_date': '2023-03-01'})
            shared = history.begin_processing(analyst.id, shared.id, shared.generation)
            shared_results = {parameter: dict(row) for parameter in ALL_PARAMETERS}
            history.save_shared_month(analyst.id, shared.id, shared.generation, '2023-01', 4, shared_results)
            history.save_shared_month(analyst.id, shared.id, shared.generation, '2023-01', 4, shared_results)
            history.save_shared_month(analyst.id, shared.id, shared.generation, '2023-02', 0, {})
            self.assertEqual(len(history.load_months(analyst.id, shared.id)), 6)
            self.assertEqual(history.load_run_snapshot(analyst.id, shared.id)[1]['shared_scene_counts'],
                             {'2023-01': 4, '2023-02': 0})
            with self.assertRaises(HistoryConflict):
                history.save_shared_month(analyst.id, shared.id, shared.generation, '2023-01', 5, shared_results)
            rollback = history.create(analyst.id, {'start_date': '2023-01-01', 'end_date': '2023-02-01'})
            rollback = history.begin_processing(analyst.id, rollback.id, rollback.generation)
            history.save_month(analyst.id, rollback.id, rollback.generation, ALL_PARAMETERS[1],
                               '2023-01', 'complete', result=dict(row, mean_value=0.99))
            with self.assertRaises(HistoryConflict):
                history.save_shared_month(analyst.id, rollback.id, rollback.generation, '2023-01', 4, shared_results)
            self.assertEqual(len(history.load_months(analyst.id, rollback.id)), 1)
            with connection() as db:
                self.assertEqual(db.execute('SELECT count(*) AS n FROM water_monitor_private.shared_months WHERE run_id = %s',
                                            (rollback.id,)).fetchone()['n'], 0)
            history.save_numeric_results(analyst.id, run.id, started.generation, {ALL_PARAMETERS[0]: [row]})
            loaded = history.load_numeric_results(analyst.id, run.id)
            self.assertEqual(loaded[ALL_PARAMETERS[0]][0]['mean_value'], 0.42)
            self.assertNotIn('rgb_image', loaded[ALL_PARAMETERS[0]][0])
            history.save_month(analyst.id, run.id, started.generation, ALL_PARAMETERS[0],
                               '2023-01', 'complete', result=row)
            with self.assertRaises(HistoryConflict):
                history.save_month(analyst.id, run.id, started.generation, ALL_PARAMETERS[0],
                                   '2023-01', 'no_data')
            changed = dict(row, mean_value=0.99)
            with self.assertRaises(HistoryConflict):
                history.save_numeric_results(analyst.id, run.id, started.generation,
                                             {ALL_PARAMETERS[0]: [changed]})
            history.save_month(analyst.id, run.id, started.generation, ALL_PARAMETERS[0],
                               '2023-02', 'no_data', message='no images')
            self.assertEqual([item['status'] for item in history.load_months(admin.id, run.id)],
                             ['complete', 'no_data'])
            with self.assertRaises(HistoryConflict):
                history.save_month(analyst.id, run.id, run.generation, ALL_PARAMETERS[0],
                                   '2023-01', 'no_data')
            self.assertEqual(history.load_numeric_results(analyst.id, run.id)[ALL_PARAMETERS[0]][0]['mean_value'], 0.42)
            with self.assertRaises(PermissionDenied):
                history.load_months(other.id, run.id)
            token = history.save_expert_if_unchanged(analyst.id, run.id, {'chat': ['one']},
                                                      expert_token({}), started.generation)
            with self.assertRaises(HistoryConflict):
                history.save_expert_if_unchanged(analyst.id, run.id, {'chat': ['stale']},
                                                 expert_token({}), started.generation)
            history.save_expert_if_unchanged(analyst.id, run.id, {'chat': ['two']},
                                             token, started.generation, archive_current=True)
            self.assertEqual(history.list_expert_revisions(admin.id, run.id), [{'chat': ['one']}])

            config = {'start_date': '2023-01-01', 'end_date': '2023-03-01',
                      'polygon_coords': [[51, 35], [51.1, 35], [51, 35.1], [51, 35]]}
            saved = history.create(analyst.id, config)
            saved = history.begin_processing(analyst.id, saved.id, saved.generation)
            history.save_month(analyst.id, saved.id, saved.generation, ALL_PARAMETERS[0],
                               '2023-01', 'complete', result=row)
            session = {}
            restore_postgres_run(history, analyst.id, saved.id, session)
            self.assertEqual(session['results'][ALL_PARAMETERS[0]][0]['mean_value'], 0.42)
            self.assertNotIn('rgb_image', session['results'][ALL_PARAMETERS[0]][0])
            self.assertTrue(session['resume_after_interruption'])

            class State(dict):
                __getattr__ = dict.__getitem__

            from openpyxl import load_workbook
            with patch.object(result_ui, 'st', SimpleNamespace(session_state=State(session))):
                workbook = load_workbook(BytesIO(result_ui.generate_combined_timeseries_excel()), data_only=True)
            self.assertEqual(workbook.worksheets[0].cell(2, 2).value, 0.42)
            workbook.close()
            with self.assertRaises(PermissionDenied):
                restore_postgres_run(history, other.id, saved.id, {})

            second_admin = store.create_user(admin.id, 'admin2', 'test-second-admin-password', 'admin')

            def deactivate(actor, target):
                try:
                    store.set_active(actor, target, False)
                    return 'ok'
                except AuthError:
                    return 'denied'

            with ThreadPoolExecutor(max_workers=2) as executor:
                outcomes = list(executor.map(lambda pair: deactivate(*pair),
                                             [(admin.id, second_admin.id), (second_admin.id, admin.id)]))
            self.assertCountEqual(outcomes, ['ok', 'denied'])
            self.assertEqual(sum(user.active for user in (store.get_user(admin.id),
                                                           store.get_user(second_admin.id))), 1)
        finally:
            with psycopg.connect('dbname=postgres', autocommit=True) as admin:
                admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
