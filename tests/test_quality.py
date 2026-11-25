"""Regression tests for the version 18 display and session behaviour."""
import importlib.util
import math
import sys
import types
import unittest

# These tests exercise scalar logic only and can run before NumPy is installed.
if importlib.util.find_spec('numpy') is None:
    numpy_stub = types.ModuleType('numpy')
    numpy_stub.isnan = math.isnan
    numpy_stub.bool_ = bool
    sys.modules['numpy'] = numpy_stub

from water_monitoring_app import config, quality


class QualityTests(unittest.TestCase):
    def test_classification_boundaries_and_missing_values(self):
        for parameter in (config.PARAM_TURBIDITY, config.PARAM_CHLOROPHYLL):
            for value, expected in ((-0.20001, 'normal'), (-0.2, 'warning'),
                                    (0.2, 'warning'), (0.20001, 'dangerous'),
                                    (0.4, 'dangerous'), (0.40001, 'critical')):
                with self.subTest(parameter=parameter, value=value):
                    self.assertEqual(quality._water_quality_condition(value, parameter), expected)
            for value in (None, float('nan'), float('inf'), True):
                self.assertEqual(quality._water_quality_condition(value, parameter), 'unavailable')

    def test_overall_and_monthly_classification_use_the_same_boundaries(self):
        for parameter in (config.PARAM_TURBIDITY, config.PARAM_CHLOROPHYLL):
            rows = [{'month_name': '2023-01', 'mean_value': -0.4},
                    {'month_name': '2023-02', 'mean_value': 0.0}]
            mean, condition = quality._overall_water_quality_condition(rows, parameter)
            self.assertEqual(mean, -0.2)
            self.assertEqual(condition, 'warning')
            self.assertEqual([r['condition'] for r in quality._monthly_quality_records(rows, parameter)],
                             ['normal', 'warning'])

    def test_zero_is_included_in_monthly_average(self):
        mean, condition = quality._overall_water_quality_condition(
            [{'mean_value': 0}, {'mean_value': 0.2}, {'mean_value': None}],
            config.PARAM_TURBIDITY,
        )
        self.assertAlmostEqual(mean, 0.1)
        self.assertEqual(condition, quality._water_quality_condition(mean))

    def test_parameter_containers_are_independent(self):
        data = quality.empty_param_dict(list)
        data[config.PARAM_TURBIDITY].append('one')
        self.assertEqual(data[config.PARAM_CHLOROPHYLL], [])


if __name__ == '__main__':
    unittest.main()
