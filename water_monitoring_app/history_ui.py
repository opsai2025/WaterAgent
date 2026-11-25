"""History navigation for already-authorized analysis records.

The store is responsible for enforcing the actor's access on every request.
Selecting a record returns its identifier; the caller must authorize it again
when opening it, because an account or a record may have changed meanwhile.
"""

from datetime import datetime, timezone
from html import escape

import streamlit as st


_STATUS_LABELS = {
    'created': 'ثبت‌شده، آماده پردازش',
    'pending': 'در انتظار پردازش',
    'running': 'در حال پردازش یا نیازمند بازیابی پس از وقفه',
    'complete': 'تکمیل‌شده',
    'completed': 'تکمیل‌شده',
    'partial': 'ناتمام',
    'incomplete': 'ناتمام',
    'interrupted': 'متوقف‌شده، قابل بررسی برای ادامه',
    'failed': 'پردازش ناموفق',
    'no_data': 'بدون داده',
}


def _ltr(value):
    """Escape and isolate a left-to-right date inside a Persian sentence."""
    return f'<bdi dir="ltr">{escape(str(value))}</bdi>'


def _format_created_at(value):
    """Show stored UTC times without exposing fractional seconds as dates."""
    try:
        moment = datetime.fromisoformat(value)
        if moment.tzinfo is None:
            return value
        return moment.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    except (TypeError, ValueError, OverflowError):
        return str(value)


def render_history(store, actor):
    """Display permitted runs and return a selected run ID, or ``None``.

    ``store.list_runs(actor.id)`` must perform the actual access control and
    return records with id, owner_name, created_at, status and config fields.
    This function deliberately does not accept a user-selected owner filter.
    """
    st.markdown(
        '''<style>
        [data-testid="stMain"] h2,
        [data-testid="stMain"] [data-testid="stText"],
        [data-testid="stMain"] [data-testid="stCaptionContainer"],
        [data-testid="stMain"] [data-testid="stAlert"] {
            direction: rtl; text-align: right;
        }
        .wq-history-details, .wq-history-details p {
            direction: rtl; text-align: right;
        }
        [data-testid="stElementContainer"]:has(.wq-history-details) {
            width: 100% !important;
        }
        .wq-history-details p { margin: 0 0 .6rem; }
        </style>''',
        unsafe_allow_html=True,
    )
    st.header('تاریخچه تحلیل‌ها')
    if actor.role == 'admin':
        st.caption('تحلیل‌های ثبت‌شده همه کاربران در این فهرست نمایش داده می‌شوند.')
    else:
        st.caption('تحلیل‌های ثبت‌شده حساب شما در این فهرست نمایش داده می‌شوند.')

    try:
        runs = store.list_runs(actor.id)
    except Exception:
        # Do not print raw database errors, paths or other sensitive details in
        # the UI. The error remains distinguishable from an empty history.
        st.error('دریافت تاریخچه ممکن نشد. دوباره تلاش کنید یا با مدیر سامانه تماس بگیرید.')
        return None

    if not runs:
        st.info('هنوز تحلیلی در تاریخچه ثبت نشده است.')
        return None

    busy = bool(st.session_state.get('processing_in_progress', False))
    if busy:
        st.info('پس از پایان پردازش جاری می‌توانید تحلیل دیگری را باز کنید.')

    for run in runs:
        config = run.config or {}
        start = config.get('start_date') or 'نامشخص'
        end = config.get('end_date') or 'نامشخص'
        status = _STATUS_LABELS.get(run.status, 'نامشخص')
        st.divider()
        details, action = st.columns([4, 1])
        with details:
            # Values are escaped before HTML insertion; bdi prevents the browser
            # from reversing the order of dates in a Persian sentence.
            st.markdown(
                f'<div class="wq-history-details" dir="rtl">'
                f'<p>کاربر: <bdi>{escape(str(run.owner_name))}</bdi></p>'
                f'<p>زمان ثبت: {_ltr(_format_created_at(run.created_at))}</p>'
                f'<p>بازه پایش: از {_ltr(start)} تا {_ltr(end)} (پایان غیرشامل)</p>'
                f'<p>وضعیت: {escape(status)}</p></div>',
                unsafe_allow_html=True,
            )
        with action:
            if st.button('باز کردن', key=f'history_open_{run.id}', disabled=busy):
                return run.id
    return None
