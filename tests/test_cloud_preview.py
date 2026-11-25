"""Cloud preview makes its temporary nature explicit and tests the database safely."""
from pathlib import Path
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


class CloudPreviewTests(unittest.TestCase):
    def test_preview_requires_explicit_admin_credentials(self):
        entry = Path(__file__).resolve().parents[1] / 'water_monitoring.py'
        variables = {'WATER_MONITOR_BACKEND': 'cloud_preview',
                     'WATER_MONITOR_PREVIEW_ADMIN_USERNAME': '',
                     'WATER_MONITOR_PREVIEW_ADMIN_PASSWORD': ''}
        with patch.dict('os.environ', variables):
            app = AppTest.from_file(str(entry), default_timeout=90).run()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any('گذرواژه مدیر' in item.value for item in app.error))
        self.assertEqual(len(app.text_input), 0)

    def test_preview_login_and_connection_status(self):
        entry = Path(__file__).resolve().parents[1] / 'water_monitoring.py'
        variables = {'WATER_MONITOR_BACKEND': 'cloud_preview',
                     'WATER_MONITOR_PREVIEW_ADMIN_USERNAME': 'preview-admin',
                     'WATER_MONITOR_PREVIEW_ADMIN_PASSWORD': 'preview-secret-password'}
        with patch.dict('os.environ', variables), \
                patch('water_monitoring_app.interface._get_app_logo_base64', return_value=''), \
                patch('water_monitoring_app.deployment_check.check_database', return_value=(True, 'اتصال موفق')):
            app = AppTest.from_file(str(entry), default_timeout=90).run()
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(any('موقت' in item.value for item in app.warning))
            self.assertNotIn('preview_database_check', [button.key for button in app.button])
            app.text_input[0].set_value('preview-admin')
            app.text_input[1].set_value('preview-secret-password')
            next(button for button in app.button if 'ورود' in button.label).click().run()
            self.assertEqual(len(app.exception), 0, [item.message for item in app.exception])
            self.assertEqual(app.session_state['active_page'], 'setup')
            app.button(key='preview_database_check').click().run()
            self.assertTrue(any('اتصال موفق' in item.value for item in app.success))


if __name__ == '__main__':
    unittest.main()
