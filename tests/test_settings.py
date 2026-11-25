"""Verify local configuration and explicit environment precedence."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from water_monitoring_app.settings import load_settings


class SettingsTests(unittest.TestCase):
    def test_dotenv_fills_missing_values_but_does_not_override_environment(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            path.write_text('OPENAI_API_KEY=from-file\nTAVILY_API_KEY=from-file\n', encoding='utf-8')
            with patch.dict(os.environ, {'OPENAI_API_KEY': 'from-environment'}, clear=True):
                self.assertTrue(load_settings(path))
                self.assertEqual(os.environ['OPENAI_API_KEY'], 'from-environment')
                self.assertEqual(os.environ['TAVILY_API_KEY'], 'from-file')

    def test_missing_file_is_optional(self):
        with TemporaryDirectory() as directory:
            self.assertFalse(load_settings(Path(directory) / 'missing.env'))


if __name__ == '__main__':
    unittest.main()
