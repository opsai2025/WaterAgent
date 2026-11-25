"""Persistence, account isolation, and failure behavior of analysis history."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from water_monitoring_app.auth import AuthStore, PermissionDenied
from water_monitoring_app.history import HistoryConflict, HistoryError, HistoryStore, expert_token


CONFIG = {'polygon_coords': [[51.0, 35.0], [51.1, 35.0], [51.0, 35.1], [51.0, 35.0]],
          'start_date': '2023-01-01', 'end_date': '2023-03-01',
          'scale': 10, 'cloudy_pixel_percentage': 20}


class HistoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.auth = AuthStore(Path(self.temp.name) / 'users.sqlite3')
        self.admin = self.auth.bootstrap_admin('admin', 'admin-password')
        self.first = self.auth.create_user(self.admin.id, 'first', 'first-password')
        self.second = self.auth.create_user(self.admin.id, 'second', 'second-password')
        self.history = HistoryStore(self.auth)

    def test_run_persistence_and_per_user_authorization(self):
        first_run = self.history.create(self.first.id, CONFIG)
        second_run = self.history.create(self.second.id, CONFIG)
        self.assertNotEqual(first_run.id, second_run.id)
        self.assertNotEqual(self.history.run_dir(self.first.id, first_run.id),
                            self.history.run_dir(self.second.id, second_run.id))
        self.assertEqual(json.loads((self.history.run_dir(self.first.id, first_run.id)
                                     / 'config.json').read_text(encoding='utf-8')), CONFIG)
        self.assertEqual([r.id for r in self.history.list_runs(self.first.id)], [first_run.id])
        self.assertEqual({r.id for r in self.history.list_runs(self.admin.id)}, {first_run.id, second_run.id})
        self.assertEqual(self.history.get_run(self.admin.id, first_run.id).owner_name, 'first')
        for action in (
            lambda: self.history.get_run(self.second.id, first_run.id),
            lambda: self.history.run_dir(self.second.id, first_run.id),
            lambda: self.history.update_status(self.second.id, first_run.id, 'complete'),
            lambda: self.history.save_expert(self.second.id, first_run.id, {'chat': []}),
            lambda: self.history.load_expert(self.second.id, first_run.id),
            lambda: self.history.save_state(self.second.id, first_run.id, {}),
            lambda: self.history.load_state(self.second.id, first_run.id),
        ):
            with self.assertRaises(PermissionDenied):
                action()
        reopened = HistoryStore(AuthStore(self.auth.path))
        self.assertEqual(reopened.get_run(self.first.id, first_run.id).config, CONFIG)

    def test_state_and_expert_persist_for_owner_and_admin(self):
        run = self.history.create(self.first.id, CONFIG)
        state = {'counts': {'کدورت': [2, 3]}, 'partial': True}
        expert = {'summary': 'خلاصه', 'chat': [{'role': 'user', 'content': 'پرسش'}]}
        self.assertEqual(self.history.load_state(self.first.id, run.id), {})
        self.assertEqual(self.history.load_expert(self.first.id, run.id), {})
        self.history.save_state(self.first.id, run.id, state)
        self.history.save_expert(self.first.id, run.id, expert)
        self.history.update_status(self.first.id, run.id, 'incomplete')
        reopened = HistoryStore(AuthStore(self.auth.path))
        self.assertEqual(reopened.load_state(self.first.id, run.id), state)
        self.assertEqual(reopened.load_expert(self.admin.id, run.id), expert)
        self.assertEqual(reopened.get_run(self.admin.id, run.id).status, 'incomplete')

    def test_disabled_actor_cannot_read_or_write(self):
        run = self.history.create(self.first.id, CONFIG)
        self.auth.set_active(self.admin.id, self.first.id, False)
        for action in (lambda: self.history.list_runs(self.first.id),
                       lambda: self.history.get_run(self.first.id, run.id),
                       lambda: self.history.create(self.first.id, CONFIG),
                       lambda: self.history.save_state(self.first.id, run.id, {})):
            with self.assertRaises(PermissionDenied):
                action()
        self.assertEqual(self.history.get_run(self.admin.id, run.id).owner_id, self.first.id)

    def test_failed_atomic_replace_keeps_the_previous_data(self):
        run = self.history.create(self.first.id, CONFIG)
        self.history.save_expert(self.first.id, run.id, {'summary': 'first'})
        with patch('water_monitoring_app.history.os.replace', side_effect=OSError('disk error')):
            with self.assertRaises(OSError):
                self.history.save_expert(self.first.id, run.id, {'summary': 'second'})
        self.assertEqual(self.history.load_expert(self.first.id, run.id), {'summary': 'first'})
        self.assertEqual(list(self.history.run_dir(self.first.id, run.id).glob('.history_*.tmp')), [])

    def test_expert_revisions_are_preserved_and_authorized(self):
        run = self.history.create(self.first.id, CONFIG)
        first = {'summary': 'اول', 'chat': [{'role': 'user', 'content': 'الف'}]}
        second = {'summary': 'دوم', 'chat': [{'role': 'user', 'content': 'ب'}]}
        self.assertEqual(self.history.list_expert_revisions(self.first.id, run.id), [])
        self.history.append_expert_revision(self.first.id, run.id, first)
        self.history.append_expert_revision(self.first.id, run.id, second)
        self.history.save_expert(self.first.id, run.id, {'summary': 'کنونی'})
        self.assertEqual(self.history.list_expert_revisions(self.admin.id, run.id), [first, second])
        reopened = HistoryStore(AuthStore(self.auth.path))
        self.assertEqual(reopened.list_expert_revisions(self.first.id, run.id), [first, second])
        with self.assertRaises(PermissionDenied):
            self.history.list_expert_revisions(self.second.id, run.id)
        with self.assertRaises(PermissionDenied):
            self.history.append_expert_revision(self.second.id, run.id, {'summary': 'دیگری'})
        with patch('water_monitoring_app.history.os.replace', side_effect=OSError('disk error')):
            with self.assertRaises(OSError):
                self.history.append_expert_revision(self.first.id, run.id, {'summary': 'سوم'})
        self.assertEqual(reopened.list_expert_revisions(self.first.id, run.id), [first, second])

    def test_optimistic_expert_write_rejects_stale_sessions(self):
        run = self.history.create(self.first.id, CONFIG)
        session_a = self.history.load_expert(self.first.id, run.id)
        session_b = self.history.load_expert(self.first.id, run.id)
        token_a, token_b = expert_token(session_a), expert_token(session_b)
        first = {'chat': ['first'], 'summary': 'first summary'}
        next_token = self.history.save_expert_if_unchanged(self.first.id, run.id, first, token_a, run.generation)
        self.assertEqual(next_token, expert_token({'summary': 'first summary', 'chat': ['first']}))
        with self.assertRaises(HistoryConflict):
            self.history.save_expert_if_unchanged(self.first.id, run.id, {'chat': ['second']}, token_b, run.generation)
        self.assertEqual(self.history.load_expert(self.admin.id, run.id), first)
        with self.assertRaises(PermissionDenied):
            self.history.save_expert_if_unchanged(self.second.id, run.id, {}, next_token, run.generation)
        with patch('water_monitoring_app.history.os.replace', side_effect=OSError('disk error')):
            with self.assertRaises(OSError):
                self.history.save_expert_if_unchanged(self.first.id, run.id, {'chat': ['lost']}, next_token, run.generation)
        self.assertEqual(self.history.load_expert(self.first.id, run.id), first)

    def test_processing_generation_blocks_stale_expert_snapshot(self):
        run = self.history.create(self.first.id, CONFIG)
        token = expert_token(self.history.load_expert(self.first.id, run.id))
        running = self.history.begin_processing(self.first.id, run.id, run.generation)
        self.assertEqual(running.generation, run.generation + 1)
        self.assertEqual(running.status, 'running')
        with self.assertRaises(HistoryError):
            self.history.update_status(self.first.id, run.id, 'running')
        with self.assertRaises(HistoryConflict):
            self.history.save_expert_if_unchanged(self.first.id, run.id, {'chat': ['stale']},
                                                  token, run.generation)
        self.assertEqual(self.history.load_expert(self.first.id, run.id), {})
        with self.assertRaises(PermissionDenied):
            self.history.begin_processing(self.second.id, run.id, running.generation)
        with self.assertRaises(HistoryConflict):
            self.history.begin_processing(self.first.id, run.id, run.generation)
        self.assertEqual(self.history.get_run(self.first.id, run.id), running)
        running_again = self.history.begin_processing(self.admin.id, run.id, running.generation)
        self.assertEqual(running_again.generation, running.generation + 1)
        self.history.save_expert_if_unchanged(self.first.id, run.id, {'chat': ['fresh']},
                                              token, running_again.generation)

    def test_archiving_current_snapshot_is_guarded_and_lossless_on_failure(self):
        run = self.history.create(self.first.id, CONFIG)
        first = {'chat': ['first']}
        second = {'chat': ['second']}
        token = self.history.save_expert_if_unchanged(self.first.id, run.id, first,
                                                      expert_token({}), run.generation)
        next_token = self.history.save_expert_if_unchanged(self.first.id, run.id, second,
                                                           token, run.generation, archive_current=True)
        self.assertEqual(self.history.list_expert_revisions(self.first.id, run.id), [first])
        with self.assertRaises(HistoryConflict):
            self.history.save_expert_if_unchanged(self.first.id, run.id, {}, token,
                                                   run.generation, archive_current=True)
        self.assertEqual(self.history.list_expert_revisions(self.first.id, run.id), [first])
        replace = os.replace
        calls = []

        def fail_second_replace(source, target):
            calls.append(target)
            if len(calls) == 2:
                raise OSError('disk error')
            return replace(source, target)

        with patch('water_monitoring_app.history.os.replace', side_effect=fail_second_replace):
            with self.assertRaises(OSError):
                self.history.save_expert_if_unchanged(self.first.id, run.id, {'chat': ['third']},
                                                       next_token, run.generation, archive_current=True)
        self.assertEqual(self.history.load_expert(self.first.id, run.id), second)
        self.assertEqual(self.history.list_expert_revisions(self.first.id, run.id), [first, second])

    def test_old_history_schema_is_migrated_without_data_loss(self):
        with TemporaryDirectory() as temporary:
            auth = AuthStore(Path(temporary) / 'old.sqlite3')
            owner = auth.bootstrap_admin('old-admin', 'old-admin-password')
            run_id = '005e86b6-77ab-40a6-97f3-ae2ce8ef5097'
            with sqlite3.connect(auth.path) as db:
                db.execute('''CREATE TABLE history_runs (
                    id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, config_json TEXT NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL)''')
                db.execute('INSERT INTO history_runs VALUES (?, ?, ?, ?, ?)',
                           (run_id, owner.id, json.dumps(CONFIG), 'complete', '2023-01-01'))
            history = HistoryStore(auth)
            old = history.get_run(owner.id, run_id)
            self.assertEqual(old.config, CONFIG)
            self.assertEqual(old.status, 'complete')
            self.assertEqual(old.generation, 0)
            self.assertEqual(history.begin_processing(owner.id, run_id, old.generation).generation, 1)
            self.assertEqual(HistoryStore(auth).get_run(owner.id, run_id).generation, 1)
    def test_invalid_inputs_do_not_create_metadata_or_escape(self):
        for config in (None, [], {'number': float('nan')}, {'bad': object()}):
            with self.assertRaises(HistoryError):
                self.history.create(self.first.id, config)
        self.assertEqual(self.history.list_runs(self.first.id), [])
        with self.assertRaises(HistoryError):
            self.history.get_run(self.first.id, '../other')
        run = self.history.create(self.first.id, CONFIG)
        with self.assertRaises(HistoryError):
            self.history.update_status(self.first.id, run.id, 'finished')
        with self.assertRaises(HistoryError):
            self.history.save_state(self.first.id, run.id, {'invalid': float('nan')})

    def test_failed_run_creation_does_not_publish_metadata(self):
        with patch('water_monitoring_app.history.os.replace', side_effect=OSError('disk error')):
            with self.assertRaises(OSError):
                self.history.create(self.first.id, CONFIG)
        self.assertEqual(self.history.list_runs(self.first.id), [])

    def test_symlinked_run_and_json_cannot_escape_storage(self):
        run = self.history.create(self.first.id, CONFIG)
        folder = self.history.run_dir(self.first.id, run.id)
        outside = Path(self.temp.name) / 'outside'
        outside.mkdir()
        (outside / 'expert.json').write_text('{"summary":"other"}', encoding='utf-8')
        (folder / 'expert.json').symlink_to(outside / 'expert.json')
        with self.assertRaises(HistoryError):
            self.history.load_expert(self.first.id, run.id)
        with self.assertRaises(HistoryError):
            self.history.save_expert(self.first.id, run.id, {'summary': 'replacement'})
        self.assertEqual((outside / 'expert.json').read_text(encoding='utf-8'), '{"summary":"other"}')
        (folder / 'expert.json').unlink()
        moved = folder.with_name(folder.name + '-original')
        folder.rename(moved)
        folder.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(HistoryError):
            self.history.run_dir(self.first.id, run.id)

    def test_run_lock_excludes_competing_sessions_and_releases_on_error(self):
        run = self.history.create(self.first.id, CONFIG)
        with self.history.run_lock(self.first.id, run.id) as folder:
            self.assertEqual(folder, self.history.run_dir(self.first.id, run.id))
            with self.assertRaises(HistoryError):
                with self.history.run_lock(self.first.id, run.id):
                    pass
            with self.assertRaises(PermissionDenied):
                with self.history.run_lock(self.second.id, run.id):
                    pass
        with self.assertRaisesRegex(RuntimeError, 'test'):
            with self.history.run_lock(self.first.id, run.id):
                raise RuntimeError('test')
        with self.history.run_lock(self.first.id, run.id):
            pass

    def test_run_lock_releases_when_other_process_dies(self):
        run = self.history.create(self.first.id, CONFIG)
        lock_path = self.history.run_dir(self.first.id, run.id) / '.run.lock'
        code = ('import fcntl,sys,time; f=open(sys.argv[1], "a+"); '
                'fcntl.flock(f, fcntl.LOCK_EX); print("ready", flush=True); time.sleep(60)')
        process = subprocess.Popen([sys.executable, '-c', code, str(lock_path)],
                                   stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'ready')
            with self.assertRaises(HistoryError):
                with self.history.run_lock(self.first.id, run.id):
                    pass
        finally:
            process.terminate()
            process.wait(timeout=10)
            process.stdout.close()
        with self.history.run_lock(self.first.id, run.id):
            pass

    def test_run_lock_rejects_symlink_and_rechecks_active_account(self):
        run = self.history.create(self.first.id, CONFIG)
        folder = self.history.run_dir(self.first.id, run.id)
        outside = Path(self.temp.name) / 'outside-lock'
        outside.write_text('unchanged', encoding='utf-8')
        (folder / '.run.lock').symlink_to(outside)
        with self.assertRaises(HistoryError):
            with self.history.run_lock(self.first.id, run.id):
                pass
        self.assertEqual(outside.read_text(encoding='utf-8'), 'unchanged')
        (folder / '.run.lock').unlink()
        real_flock = __import__('fcntl').flock

        def revoke_on_lock(fd, operation):
            real_flock(fd, operation)
            if operation & __import__('fcntl').LOCK_NB:
                self.auth.set_active(self.admin.id, self.first.id, False)

        with patch('fcntl.flock', side_effect=revoke_on_lock):
            with self.assertRaises(PermissionDenied):
                with self.history.run_lock(self.first.id, run.id):
                    pass


if __name__ == '__main__':
    unittest.main()
