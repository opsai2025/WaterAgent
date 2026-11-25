"""Tests for history presentation and selection at the UI/store boundary."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from water_monitoring_app import history_ui


class FakeColumn:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class FakeStreamlit:
    def __init__(self, *, clicked=None, busy=False):
        self.session_state = {'processing_in_progress': busy}
        self.clicked = clicked
        self.messages = []
        self.buttons = []

    def header(self, message):
        self.messages.append(('header', message))

    def caption(self, message):
        self.messages.append(('caption', message))

    def info(self, message):
        self.messages.append(('info', message))

    def error(self, message):
        self.messages.append(('error', message))

    def text(self, message):
        self.messages.append(('text', message))

    def markdown(self, message, *, unsafe_allow_html=False):
        self.messages.append(('markdown', message))

    def divider(self):
        pass

    def columns(self, widths):
        return [FakeColumn() for _ in widths]

    def button(self, label, *, key, disabled):
        self.buttons.append((label, key, disabled))
        return not disabled and key == self.clicked


class FakeStore:
    def __init__(self, records_by_actor=None, error=None):
        self.records_by_actor = records_by_actor or {}
        self.error = error
        self.calls = []

    def list_runs(self, actor_id):
        self.calls.append(actor_id)
        if self.error:
            raise self.error
        return self.records_by_actor.get(actor_id, [])


def record(identifier, owner, status):
    return SimpleNamespace(
        id=identifier,
        owner_id=owner,
        owner_name=owner,
        created_at='2026-11-10T09:00:00+00:00',
        status=status,
        config={'start_date': '2023-01-01', 'end_date': '2023-12-31'},
    )


class HistoryUiTests(unittest.TestCase):
    def render(self, streamlit, store, actor):
        with patch.object(history_ui, 'st', streamlit):
            return history_ui.render_history(store, actor)

    def test_empty_history_is_distinct_from_an_error(self):
        ui, store = FakeStreamlit(), FakeStore()
        actor = SimpleNamespace(id='analyst', role='analyst')
        self.assertIsNone(self.render(ui, store, actor))
        self.assertEqual(store.calls, ['analyst'])
        self.assertTrue(any(kind == 'info' and 'هنوز' in text for kind, text in ui.messages))
        self.assertFalse(any(kind == 'error' for kind, _ in ui.messages))
        self.assertFalse(ui.buttons)

    def test_analyst_receives_own_records_and_selects_identifier(self):
        own = record('run-one', 'تحلیلگر اول', 'incomplete')
        ui = FakeStreamlit(clicked='history_open_run-one')
        store = FakeStore({'analyst': [own]})
        actor = SimpleNamespace(id='analyst', role='analyst')
        self.assertEqual(self.render(ui, store, actor), 'run-one')
        self.assertEqual(store.calls, ['analyst'])
        displayed = ' '.join(text for _, text in ui.messages)
        for expected in ('حساب شما', 'تحلیلگر اول', '2023-01-01', '2023-12-31', 'ناتمام'):
            self.assertIn(expected, displayed)
        self.assertIn('2026-11-10 09:00 UTC', displayed)
        self.assertIn('<bdi dir="ltr">2023-01-01</bdi>', displayed)
        self.assertIn('پایان غیرشامل', displayed)
        self.assertEqual(ui.buttons, [('باز کردن', 'history_open_run-one', False)])

    def test_admin_sees_returned_owners_and_busy_run_cannot_open(self):
        records = [record('run-one', 'کاربر اول', 'complete'),
                   record('run-two', 'کاربر دوم', 'failed')]
        store = FakeStore({'admin': records})
        actor = SimpleNamespace(id='admin', role='admin')
        ui = FakeStreamlit(clicked='history_open_run-two', busy=True)
        self.assertIsNone(self.render(ui, store, actor))
        self.assertEqual(store.calls, ['admin'])
        displayed = ' '.join(text for _, text in ui.messages)
        for expected in ('همه کاربران', 'کاربر اول', 'کاربر دوم', 'تکمیل‌شده', 'پردازش ناموفق'):
            self.assertIn(expected, displayed)
        self.assertEqual(len(ui.buttons), 2)
        self.assertTrue(all(button[2] for button in ui.buttons))

        ui = FakeStreamlit(clicked='history_open_run-two')
        self.assertEqual(self.render(ui, store, actor), 'run-two')

    def test_read_error_is_visible_without_leaking_error_details(self):
        ui = FakeStreamlit()
        store = FakeStore(error=OSError('sensitive local path'))
        actor = SimpleNamespace(id='admin', role='admin')
        self.assertIsNone(self.render(ui, store, actor))
        self.assertTrue(any(kind == 'error' for kind, _ in ui.messages))
        self.assertFalse(any('sensitive local path' in text for _, text in ui.messages))
        self.assertFalse(any(kind == 'info' and 'هنوز' in text for kind, text in ui.messages))

    def test_record_values_are_escaped_before_html_rendering(self):
        run = record('run-one', '<script>alert(1)</script>', 'complete')
        run.config['start_date'] = '<img src=x>'
        ui = FakeStreamlit()
        self.render(ui, FakeStore({'admin': [run]}), SimpleNamespace(id='admin', role='admin'))
        displayed = ' '.join(text for kind, text in ui.messages if kind == 'markdown')
        self.assertNotIn('<script>', displayed)
        self.assertNotIn('<img src=x>', displayed)
        self.assertIn('&lt;script&gt;', displayed)
        self.assertIn('&lt;img src=x&gt;', displayed)


if __name__ == '__main__':
    unittest.main()
