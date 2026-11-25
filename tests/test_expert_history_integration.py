"""Offline integration coverage for persisted expert conversations."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from water_monitoring_app.auth import AuthStore
from water_monitoring_app.expert_snapshot import dump_expert_state, load_expert_state
from water_monitoring_app.history import HistoryStore


CONFIG = {'polygon_coords': [[51., 35.], [51.1, 35.], [51., 35.1], [51., 35.]],
          'start_date': '2023-01-01', 'end_date': '2023-02-01'}


class ExpertHistoryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.db = str(Path(self.temporary.name) / 'users.sqlite3')
        self.auth = AuthStore(self.db)
        self.admin = self.auth.bootstrap_admin('admin', 'admin-test-password')
        self.owner = self.auth.create_user(self.admin.id, 'owner', 'owner-test-password')
        self.store = HistoryStore(self.auth)
        self.run_record = self.store.create(self.owner.id, CONFIG)
        signature = ((('2023-01', .5),), (), ())
        self.initial = dump_expert_state({
            'expert_analysis_json': '{}', 'expert_analysis_signature': signature,
            'executive_summary': None, 'executive_summary_signature': None,
            'expert_chat_history': [], 'param_briefs': {}, 'param_brief_signatures': {},
        })
        self.store.save_expert(self.owner.id, self.run_record.id, self.initial)

        # Exercise the real Streamlit render functions with a synthetic result;
        # this never accesses real credentials or needs an external service.
        self.script = f'''
import streamlit as st
from water_monitoring_app.auth import AuthStore
from water_monitoring_app.config import ALL_PARAMETERS
from water_monitoring_app.expert import render_expert_chat_tab, render_summary_page
from water_monitoring_app.expert_snapshot import load_expert_state
from water_monitoring_app.history import HistoryStore, expert_token

store = HistoryStore(AuthStore())
run_id = {self.run_record.id!r}
actor_id = {self.owner.id!r}
if 'auth_user_id' not in st.session_state:
    payload = store.load_expert(actor_id, run_id)
    st.session_state.update(load_expert_state(payload))
    st.session_state.auth_user_id = actor_id
    st.session_state.active_history_id = run_id
    st.session_state.history_expert_token = expert_token(payload)
    st.session_state.history_generation = store.get_run(actor_id, run_id).generation
    st.session_state.results = {{parameter: [] for parameter in ALL_PARAMETERS}}
    st.session_state.results[ALL_PARAMETERS[0]] = [{{'month_name': '2023-01', 'mean_value': .5}}]
    st.session_state.view = 'chat'
if st.button('test summary', key='test_summary'):
    st.session_state.view = 'summary'
if st.button('test chat', key='test_chat'):
    st.session_state.view = 'chat'
if st.session_state.view == 'summary':
    render_summary_page()
else:
    render_expert_chat_tab()
'''

    def run_app(self):
        return AppTest.from_string(self.script, default_timeout=90).run()

    def test_chat_summary_restore_and_reset_revision(self):
        with patch.dict('os.environ', {'WATER_MONITOR_AUTH_DB': self.db}), \
                patch('water_monitoring_app.expert.ask_water_quality_expert',
                      side_effect=['پاسخ آزمایشی', 'خلاصه آزمایشی']) as model:
            app = self.run_app()
            self.assertEqual(len(app.exception), 0, [item.message for item in app.exception])
            app.chat_input[0].set_value('پرسش آزمایشی').run()
            self.assertEqual(len(app.exception), 0, [item.message for item in app.exception])
            saved = load_expert_state(self.store.load_expert(self.owner.id, self.run_record.id))
            self.assertEqual([entry['content'] for entry in saved['expert_chat_history']],
                             ['پرسش آزمایشی', 'پاسخ آزمایشی'])
            app.button(key='test_summary').click().run()
            saved = load_expert_state(self.store.load_expert(self.owner.id, self.run_record.id))
            self.assertEqual(saved['executive_summary'], 'خلاصه آزمایشی')
            self.assertEqual(model.call_count, 2)
            reopened = self.run_app()
            self.assertEqual(len(reopened.exception), 0, [item.message for item in reopened.exception])
            self.assertEqual([entry['content'] for entry in reopened.session_state['expert_chat_history']],
                             ['پرسش آزمایشی', 'پاسخ آزمایشی'])
            self.assertEqual(reopened.session_state['executive_summary'], 'خلاصه آزمایشی')
            self.assertEqual(model.call_count, 2)
            reopened.button(key='expert_chat_reset').click().run()
            current = load_expert_state(self.store.load_expert(self.owner.id, self.run_record.id))
            self.assertEqual(current['expert_chat_history'], [])
            revisions = self.store.list_expert_revisions(self.owner.id, self.run_record.id)
            self.assertTrue(any(load_expert_state(item)['expert_chat_history'] for item in revisions))

    def test_failed_question_persistence_never_invokes_model(self):
        original = HistoryStore.save_expert_if_unchanged

        def fail_question(store, actor, run_id, payload, token, generation, **kwargs):
            if payload.get('expert_chat_history'):
                raise OSError('simulated storage failure')
            return original(store, actor, run_id, payload, token, generation, **kwargs)

        with patch.dict('os.environ', {'WATER_MONITOR_AUTH_DB': self.db}), \
                patch.object(HistoryStore, 'save_expert_if_unchanged', new=fail_question), \
                patch('water_monitoring_app.expert.ask_water_quality_expert') as model:
            app = self.run_app()
            app.chat_input[0].set_value('پرسش ذخیره‌نشده').run()
            model.assert_not_called()
            saved = load_expert_state(self.store.load_expert(self.owner.id, self.run_record.id))
            self.assertEqual(saved['expert_chat_history'], [])

    def test_stale_session_cannot_overwrite_another_conversation(self):
        with patch.dict('os.environ', {'WATER_MONITOR_AUTH_DB': self.db}), \
                patch('water_monitoring_app.expert.ask_water_quality_expert',
                      return_value='پاسخ نخست') as model:
            first = self.run_app()
            second = self.run_app()
            first.chat_input[0].set_value('پرسش نخست').run()
            second.chat_input[0].set_value('پرسش نشست قدیمی').run()
            model.assert_called_once()
            saved = load_expert_state(self.store.load_expert(self.owner.id, self.run_record.id))
            self.assertEqual([entry['content'] for entry in saved['expert_chat_history']],
                             ['پرسش نخست', 'پاسخ نخست'])

    def test_stale_processing_generation_cannot_send_a_question(self):
        with patch.dict('os.environ', {'WATER_MONITOR_AUTH_DB': self.db}), \
                patch('water_monitoring_app.expert.ask_water_quality_expert') as model:
            app = self.run_app()
            self.store.begin_processing(self.owner.id, self.run_record.id, self.run_record.generation)
            app.chat_input[0].set_value('پرسش با نتایج قدیمی').run()
            model.assert_not_called()
            self.assertEqual(self.store.load_expert(self.owner.id, self.run_record.id), self.initial)

    def test_failed_answer_persistence_preserves_generated_text_in_session(self):
        original = HistoryStore.save_expert_if_unchanged

        def fail_answer(store, actor, run_id, payload, token, generation, **kwargs):
            messages = payload.get('expert_chat_history', [])
            if messages and messages[-1].get('role') == 'assistant':
                raise OSError('simulated storage failure')
            return original(store, actor, run_id, payload, token, generation, **kwargs)

        with patch.dict('os.environ', {'WATER_MONITOR_AUTH_DB': self.db}), \
                patch.object(HistoryStore, 'save_expert_if_unchanged', new=fail_answer), \
                patch('water_monitoring_app.expert.ask_water_quality_expert',
                      return_value='پاسخ پرداخت‌شده') as model:
            app = self.run_app()
            app.chat_input[0].set_value('پرسش').run()
            model.assert_called_once()
            history = app.session_state['expert_chat_history']
            self.assertIn('پاسخ پرداخت‌شده', [entry['content'] for entry in history])
            saved = load_expert_state(self.store.load_expert(self.owner.id, self.run_record.id))
            self.assertEqual([entry['content'] for entry in saved['expert_chat_history']], ['پرسش'])


if __name__ == '__main__':
    unittest.main()
