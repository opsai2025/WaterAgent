"""Streamlit entry point for the water quality monitoring application."""
import os
import warnings

os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

import streamlit as st

st.set_page_config(layout="wide", page_title="سامانه پایش کیفیت آب | نسخه ۱۸", page_icon="🌊")

if __name__ == "__main__":
    import sqlite3

    backend = os.environ.get('WATER_MONITOR_BACKEND', 'local')
    preview = backend == 'cloud_preview'
    if backend not in ('local', 'cloud_preview'):
        st.error('ذخیره‌سازی ابری هنوز در این نسخه فعال نیست؛ برنامه قابل استقرار نیست.')
        st.stop()

    if preview:
        import tempfile

        @st.cache_resource
        def _preview_directory():
            return tempfile.mkdtemp(prefix='water-monitor-preview-')

        os.environ['WATER_MONITOR_AUTH_DB'] = os.path.join(_preview_directory(), 'users.sqlite3')
        st.warning('نسخه آزمایشی: حساب‌ها، تحلیل‌ها و تصاویر فقط موقت‌اند و ممکن است با راه‌اندازی مجدد حذف شوند. Supabase هنوز محل ذخیره نتایج برنامه نیست.')

    from water_monitoring_app.auth import AuthError, AuthStore
    from water_monitoring_app.auth_ui import render_account_controls, render_login
    from water_monitoring_app.history import HistoryStore
    from water_monitoring_app.history_restore import restore_run
    from water_monitoring_app.history_ui import render_history
    from water_monitoring_app.interface import main, _inject_global_app_css, _render_sidebar_nav
    from water_monitoring_app.session import initialize_session

    try:
        store = AuthStore()
        if preview:
            preview_user = os.environ.get('WATER_MONITOR_PREVIEW_ADMIN_USERNAME', '')
            preview_password = os.environ.get('WATER_MONITOR_PREVIEW_ADMIN_PASSWORD', '')
            if not preview_user or not preview_password:
                st.error('برای ورود آزمایشی، نام کاربری و گذرواژه مدیر را در متغیرهای امن میزبان تنظیم کنید.')
                st.stop()
            if not store.has_users():
                try:
                    store.bootstrap_admin(preview_user, preview_password)
                except AuthError:
                    if not store.has_users():
                        st.error('ساخت حساب آزمایشی ناموفق بود؛ نام کاربری و گذرواژه را بررسی کنید.')
                        st.stop()
        user_id = st.session_state.get("auth_user_id")
        user = store.get_user(user_id) if user_id else None
    except (OSError, sqlite3.Error):
        st.error("دسترسی به پایگاه حساب‌ها امکان‌پذیر نیست. تنظیمات و دسترسی فایل را بررسی کنید.")
    else:
        if user is None or not user.active:
            if user_id:
                st.session_state.clear()
            render_login(store)
        else:
            if preview and st.button('بررسی اتصال Supabase', key='preview_database_check'):
                from water_monitoring_app.deployment_check import check_database
                ok, message = check_database()
                (st.success if ok else st.error)(message)
            initialize_session()
            _inject_global_app_css()
            _render_sidebar_nav()
            managing = render_account_controls(store, user)
            showing_history = st.session_state.get('showing_history', False)
            label = 'بازگشت به پایش' if showing_history else 'تاریخچه تحلیل‌ها'
            if st.sidebar.button('📚 ' + label, key='history_navigation',
                         disabled=(st.session_state.processing_in_progress or
                                   st.session_state.get('history_unsaved', False))):
                st.session_state.showing_history = not showing_history
                st.session_state.auth_manage_users = False
                st.rerun()
            if not managing:
                if showing_history:
                    try:
                        history = HistoryStore(store)
                        selected = render_history(history, user)
                        if selected:
                            restore_run(history, user.id, selected, st.session_state)
                            st.session_state.showing_history = False
                            st.rerun()
                    except (AuthError, OSError, sqlite3.Error, ValueError):
                        st.error('بازگشایی تاریخچه انجام نشد. فایل‌ها و دسترسی حساب را بررسی کنید.')
                else:
                    main()
