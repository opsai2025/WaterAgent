"""Preserve expert context through a non-executable archive representation."""
import json
import math
import unittest

from water_monitoring_app.expert_snapshot import dump_expert_state, load_expert_state


class ExpertSnapshotTests(unittest.TestCase):
    def sample_state(self):
        signature = ((('2024-01', 0.2), ('2024-02', None)), (), ())
        return {
            'expert_analysis_json': '{"mean": 0.2}',
            'expert_analysis_signature': signature,
            'executive_summary': 'خلاصه فارسی',
            'executive_summary_signature': signature,
            'expert_chat_history': [
                {'role': 'user', 'content': 'پرسش کامل', 'display': 'پرسش سریع'},
                {'role': 'assistant', 'content': 'پاسخ', 'error': False},
            ],
            'param_briefs': {'turbidity': 'تحلیل کدورت'},
            'param_brief_signatures': {'turbidity': (signature, 'درخواست تحلیل')},
        }

    def test_round_trip_keeps_signatures_and_chat_without_shared_references(self):
        original = self.sample_state()
        payload = dump_expert_state(original)
        restored = load_expert_state(json.loads(json.dumps(payload, ensure_ascii=False, allow_nan=False)))
        self.assertEqual(restored, original)
        self.assertIsInstance(restored['expert_analysis_signature'], tuple)
        self.assertIsInstance(restored['param_brief_signatures']['turbidity'][0][0][0], tuple)
        original['expert_chat_history'][0]['content'] = 'changed'
        self.assertEqual(restored['expert_chat_history'][0]['content'], 'پرسش کامل')

    def test_empty_state_and_nonfinite_signature(self):
        self.assertEqual(load_expert_state(dump_expert_state({}))['expert_chat_history'], [])
        payload = dump_expert_state({'expert_analysis_signature': ((float('inf'), float('-inf'), float('nan')), )})
        json.dumps(payload, allow_nan=False)
        values = load_expert_state(payload)['expert_analysis_signature'][0]
        self.assertTrue(math.isinf(values[0]) and values[0] > 0)
        self.assertTrue(math.isinf(values[1]) and values[1] < 0)
        self.assertTrue(math.isnan(values[2]))

    def test_bad_snapshots_fail_without_producing_partial_state(self):
        for payload in ({}, {'version': 99}, {'version': True}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                load_expert_state(payload)
        invalid = dump_expert_state(self.sample_state())
        invalid['expert_chat_history'][0]['role'] = 'system'
        with self.assertRaises(ValueError):
            load_expert_state(invalid)
        invalid = dump_expert_state(self.sample_state())
        invalid['expert_analysis_signature'] = {'arbitrary': 'object'}
        with self.assertRaises(ValueError):
            load_expert_state(invalid)
        with self.assertRaises(ValueError):
            dump_expert_state({'param_briefs': []})


if __name__ == '__main__':
    unittest.main()
