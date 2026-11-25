"""Numeric results can be stored without serializing any image data."""
import json
import math
from io import BytesIO
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from openpyxl import load_workbook

from water_monitoring_app.config import ALL_PARAMETERS
from water_monitoring_app.numeric_snapshot import dump_numeric_results, load_numeric_results
from water_monitoring_app import results as result_ui


class NumericSnapshotTests(unittest.TestCase):
    def test_round_trip_retains_values_without_images(self):
        source = {p: [] for p in ALL_PARAMETERS}
        source[ALL_PARAMETERS[0]] = [
            {'month_name': '2023-01', 'mean_value': 0.123456789, 'water_coverage': 87.25,
             'valid_pixels': 42, 'rgb_image': object(), 'wq_image': object()},
            {'month_name': '2023-02', 'mean_value': float('nan'), 'water_coverage': 0,
             'valid_pixels': 0},
        ]
        serialized = json.dumps(dump_numeric_results(source), allow_nan=False)
        self.assertNotIn('rgb_image', serialized)
        self.assertNotIn('wq_image', serialized)
        restored = load_numeric_results(json.loads(serialized))
        self.assertEqual(restored[ALL_PARAMETERS[0]][0]['mean_value'], 0.123456789)
        self.assertEqual(restored[ALL_PARAMETERS[0]][0]['valid_pixels'], 42)
        self.assertTrue(math.isnan(restored[ALL_PARAMETERS[0]][1]['mean_value']))

    def test_invalid_and_duplicate_months_are_rejected(self):
        row = {'month_name': '2023-01', 'mean_value': 1, 'water_coverage': 101, 'valid_pixels': 1}
        with self.assertRaises(ValueError):
            dump_numeric_results({ALL_PARAMETERS[0]: [row]})
        row['water_coverage'] = 20
        with self.assertRaises(ValueError):
            dump_numeric_results({ALL_PARAMETERS[0]: [row, row]})
        with self.assertRaises(ValueError):
            load_numeric_results({'version': True, 'parameters': {}})

    def test_restored_numeric_results_export_and_display_without_images(self):
        parameter = ALL_PARAMETERS[0]
        row = {'month_name': '2023-01', 'mean_value': 0.42,
               'water_coverage': 82.4, 'valid_pixels': 42}
        restored = load_numeric_results(dump_numeric_results({parameter: [row]}))

        class State(dict):
            __getattr__ = dict.__getitem__

        messages = []
        ui = SimpleNamespace(session_state=State(results=restored, processing_config=None),
                             info=messages.append)
        with patch.object(result_ui, 'st', ui):
            result_ui.display_side_by_side_imagery(restored[parameter], parameter)
            workbook = load_workbook(BytesIO(result_ui.generate_combined_timeseries_excel()), data_only=True)
        self.assertIn('تصاویر ماه 2023-01 ذخیره نشده‌اند', messages[0])
        self.assertEqual(workbook.worksheets[0].cell(2, 1).value, '2023-01')
        self.assertEqual(workbook.worksheets[0].cell(2, 2).value, 0.42)
        workbook.close()


if __name__ == '__main__':
    unittest.main()
