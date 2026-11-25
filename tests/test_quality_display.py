"""Display thresholds must agree across cards and monthly charts."""
import unittest
from unittest.mock import patch

from water_monitoring_app.config import (
    PARAM_CHLOROPHYLL, PARAM_TURBIDITY, WQ_DISPLAY_CONDITIONS,
)
from water_monitoring_app.results import _build_water_quality_chart, _render_quality_statistics


class QualityDisplayTests(unittest.TestCase):
    def test_cards_and_charts_use_requested_conditions(self):
        cases = [(-0.21, 'normal'), (-0.2, 'warning'), (0.2, 'warning'),
                 (0.21, 'dangerous'), (0.4, 'dangerous'), (0.41, 'critical')]
        for parameter in (PARAM_TURBIDITY, PARAM_CHLOROPHYLL):
            for value, expected in cases:
                with self.subTest(parameter=parameter, value=value):
                    rows = [{'month_name': '2024-01', 'mean_value': value, 'water_coverage': 75}]
                    spec = _build_water_quality_chart(rows, parameter)
                    self.assertEqual(spec['data']['values'][0]['condition'], expected)
                    self.assertEqual([band['upper'] for band in spec['layer'][0]['data']['values'][:3]],
                                     [-0.2, 0.2, 0.4])
                    self.assertNotEqual(spec['layer'][0]['encoding']['color']['scale']['range'][1],
                                        spec['layer'][0]['encoding']['color']['scale']['range'][2])
                    with patch('water_monitoring_app.results.st.markdown') as markdown, \
                         patch('water_monitoring_app.results._render_monthly_quality_table'):
                        _render_quality_statistics(rows, parameter)
                    card = markdown.call_args_list[1].args[0]
                    self.assertIn(WQ_DISPLAY_CONDITIONS[expected]['label'], card)
                    self.assertIn(WQ_DISPLAY_CONDITIONS[expected]['accent'], card)


if __name__ == '__main__':
    unittest.main()
