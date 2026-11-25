"""Unsaved expert text must not be silently lost by navigation or logout."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from water_monitoring_app.auth import AuthStore
from water_monitoring_app.config import ALL_PARAMETERS
from water_monitoring_app.history import HistoryStore, expert_token


class UnsavedHistoryUITests(unittest.TestCase):
    def test_unsaved_text_is_guarded_until_explicit_successful_discard(self):
        entry = Path(__file__).resolve().parents[1] / 'water_monitoring.py'
        with TemporaryDirectory() as directory:
            database = str(Path(directory) / 'users.sqlite3')
            auth = AuthStore(database)
            admin = auth.bootstrap_admin('admin', 'admin-test-password')
            store = HistoryStore(auth)
            run = store.create(admin.id, {
                'polygon_coords': [[51, 35], [51.1, 35], [51, 35.1], [51, 35]],
                'start_date': '2023-01-01', 'end_date': '2023-02-01',
                'scale': 10, 'cloudy_pixel_percentage': 10,
            })
            with patch.dict('os.environ', {'WATER_MONITOR_AUTH_DB': database, 'WATER_MONITOR_BACKEND': 'local'}), \
                    patch('water_monitoring_app.interface._get_app_logo_base64', return_value=''):
                app = AppTest.from_file(str(entry), default_timeout=90).run()
                app.text_input[0].set_value('admin')
                app.text_input[1].set_value('admin-test-password')
                app.button[0].click().run()
                self.assertEqual(len(app.exception), 0, [item.message for item in app.exception])

                # Simulate an answer that was generated but could not be saved.
                unsaved = [{'role': 'assistant', 'content': 'متن ذخیره‌نشده'}]
                app.session_state['history_unsaved'] = True
                app.session_state['history_unsaved_conflict'] = True
                app.session_state['active_history_id'] = run.id
                app.session_state['history_expert_token'] = expert_token({})
                app.session_state['history_generation'] = run.generation
                app.session_state['expert_chat_history'] = unsaved
                app.run()
                self.assertTrue(app.button(key='auth_logout').disabled)
                self.assertTrue(app.button(key='history_navigation').disabled)
                clear = next(button for button in app.button if button.label == '🗑️ پاک کردن نتایج')
                self.assertTrue(clear.disabled)
                self.assertEqual(app.session_state['expert_chat_history'], unsaved)

                # Open chat so the recovery controls are shown without calling a model.
                results = {parameter: [] for parameter in ALL_PARAMETERS}
                results[ALL_PARAMETERS[0]] = [{'month_name': '2023-01', 'mean_value': 0.2}]
                app.session_state['results'] = results
                app.session_state['active_page'] = 'chat'
                app.run()
                self.assertEqual(len(app.exception), 0, [item.message for item in app.exception])
                with patch('water_monitoring_app.history_restore.restore_run',
                           side_effect=OSError('simulated read failure')):
                    app.button(key='expert_discard_unsaved').click().run()
                self.assertTrue(app.session_state['history_unsaved'])
                self.assertEqual(app.session_state['expert_chat_history'], unsaved)
                self.assertTrue(any('حفظ شده' in item.value for item in app.error))

                # Explicitly accepting discard may replace the text only after
                # the saved analysis has been loaded successfully.
                app.button(key='expert_discard_unsaved').click().run()
                self.assertEqual(len(app.exception), 0, [item.message for item in app.exception])
                self.assertFalse(app.session_state['history_unsaved'])
                self.assertEqual(app.session_state['expert_chat_history'], [])
                self.assertEqual(app.session_state['active_history_id'], run.id)


if __name__ == '__main__':
    unittest.main()
