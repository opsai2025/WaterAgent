"""Offline smoke tests for sign-in and session isolation."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from water_monitoring_app.auth import AuthStore
from water_monitoring_app.history import HistoryStore


class ApplicationSmokeTests(unittest.TestCase):
    def test_unfinished_cloud_mode_never_falls_back_to_local_storage(self):
        entry = Path(__file__).resolve().parents[1] / 'water_monitoring.py'
        with patch.dict('os.environ', {'WATER_MONITOR_BACKEND': 'postgres'}), \
                patch('water_monitoring_app.auth.AuthStore') as local_store:
            app = AppTest.from_file(str(entry), default_timeout=90).run()
        local_store.assert_not_called()
        self.assertTrue(any('ذخیره‌سازی ابری' in error.value for error in app.error))
        self.assertEqual(len(app.exception), 0)

    def test_login_logout_and_disabled_account(self):
        entry = Path(__file__).resolve().parents[1] / 'water_monitoring.py'
        with TemporaryDirectory() as directory:
            db = str(Path(directory) / 'users.sqlite3')
            store = AuthStore(db)
            admin = store.bootstrap_admin('admin', 'admin-test-password')
            analyst = store.create_user(admin.id, 'analyst', 'analyst-test-password')
            saved_run = HistoryStore(store).create(admin.id, {
                'polygon_coords': [[51, 35], [51.1, 35], [51, 35.1], [51, 35]],
                'start_date': '2023-01-01', 'end_date': '2023-03-01',
                'scale': 10, 'cloudy_pixel_percentage': 10,
            })
            with patch.dict('os.environ', {'WATER_MONITOR_AUTH_DB': db, 'WATER_MONITOR_BACKEND': 'local'}), \
                    patch('water_monitoring_app.interface._get_app_logo_base64', return_value=''):
                app = AppTest.from_file(str(entry), default_timeout=90).run()
                self.assertEqual(len(app.exception), 0, [item.message for item in app.exception])
                with self.assertRaises(KeyError):
                    _ = app.session_state['processing_config']
                app.text_input[0].set_value('admin')
                app.text_input[1].set_value('admin-test-password')
                app.button[0].click().run()
                self.assertEqual(len(app.exception), 0, [item.message for item in app.exception])
                self.assertFalse(app.error, [item.value for item in app.error])
                self.assertEqual(app.session_state['auth_user_id'], admin.id)
                self.assertEqual(app.session_state['active_page'], 'setup')
                self.assertTrue(app.button(key='wqnav_turbidity').disabled)
                self.assertFalse(app.button(key='wqnav_setup').disabled)
                app.button(key='auth_manage_button').click().run()
                self.assertFalse(app.button(key='history_navigation').disabled)
                app.button(key='history_navigation').click().run()
                self.assertTrue(app.session_state['showing_history'])
                self.assertFalse(app.session_state['auth_manage_users'])
                app.button(key='wqnav_setup').click().run()
                self.assertFalse(app.session_state['showing_history'])
                app.button(key='auth_manage_button').click().run()
                fields = {field.label: field for field in app.text_input}
                fields['نام کاربری حساب جدید'].set_value('second-analyst')
                fields['گذرواژه حساب جدید'].set_value('second-analyst-password')
                fields['تکرار گذرواژه حساب جدید'].set_value('second-analyst-password')
                next(button for button in app.button if button.label == 'ایجاد حساب').click().run()
                self.assertIsNotNone(store.authenticate('second-analyst', 'second-analyst-password'))
                app.button(key='auth_manage_button').click().run()
                app.button(key='history_navigation').click().run()
                app.button(key=f'history_open_{saved_run.id}').click().run()
                self.assertEqual(len(app.exception), 0, [item.message for item in app.exception])
                self.assertEqual(app.session_state['active_history_id'], saved_run.id)
                self.assertTrue(app.session_state['resume_after_interruption'])
                self.assertEqual(app.selectbox(key='start_year').value, 2023)
                self.assertEqual(app.selectbox(key='start_month').value, 1)
                self.assertEqual(app.selectbox(key='end_year').value, 2023)
                self.assertEqual(app.selectbox(key='end_month').value, 3)
                app.session_state['history_unsaved'] = True
                app.run()
                self.assertTrue(app.button(key='history_navigation').disabled)
                self.assertTrue(app.button(key='auth_logout').disabled)
                app.session_state['history_unsaved'] = False
                from water_monitoring_app.config import ALL_PARAMETERS
                config = dict(app.session_state['processing_config'], pipeline_version='shared_v1')
                app.session_state['processing_config'] = config
                app.session_state['download_summary'] = {p: (1, 1) for p in ALL_PARAMETERS}
                app.session_state['shared_scene_counts'] = {'2023-01': 4}
                old_error = ('Earth Engine initialization failed: <HttpError 403 when requesting '
                             'https://example.test/$discovery/rest?key=secret-value returned "Ok". '
                             'Details: "<!DOCTYPE html><html>private-content</html>">')
                app.session_state['last_run_error'] = old_error
                app.session_state['month_statuses'] = {
                    p: {'2023-01': {'status': 'failed', 'message': old_error}}
                    for p in ALL_PARAMETERS
                }
                app.run()
                self.assertTrue(any('دریافت مشترک' in item.value for item in app.info))
                self.assertTrue(any('2023-01: 4' in item.value for item in app.text))
                self.assertTrue(any('ابتدا علت خطای اتصال' in item.value for item in app.warning))
                visible_text = ' '.join(item.value for item in app.markdown)
                self.assertIn('HTTP 403', visible_text)
                self.assertNotIn('private-content', visible_text)
                self.assertNotIn('secret-value', visible_text)
                app.session_state['expert_chat_history'] = ['private-data']
                app.button(key='auth_logout').click().run()
                with self.assertRaises(KeyError):
                    _ = app.session_state['expert_chat_history']
                app.text_input[0].set_value('analyst')
                app.text_input[1].set_value('analyst-test-password')
                app.button[0].click().run()
                self.assertEqual(app.session_state['auth_user_id'], analyst.id)
                self.assertNotIn('auth_manage_button', [button.key for button in app.button])
                try:
                    self.assertNotEqual(app.session_state['expert_chat_history'], ['private-data'])
                except KeyError:
                    pass
                store.set_active(admin.id, analyst.id, False)
                app.run()
                with self.assertRaises(KeyError):
                    _ = app.session_state['auth_user_id']
                self.assertEqual(len(app.exception), 0, [item.message for item in app.exception])


if __name__ == '__main__':
    unittest.main()
