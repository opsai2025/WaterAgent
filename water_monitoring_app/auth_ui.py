"""Local sign-in and account controls for the Streamlit application.

Authorization is enforced by the account store; these controls are only the
presentation layer. The entry point rechecks the current account on each rerun.
"""
import streamlit as st

from .auth import AuthError


def _clear_session():
    """Discard all application and widget state when changing identity."""
    for key in list(st.session_state.keys()):
        del st.session_state[key]


def render_login(store):
    """Show the login form; a successful login starts a clean session."""
    # The full application stylesheet is loaded only after authentication.
    st.markdown(
        """<style>
        [data-testid="stMainBlockContainer"] {
            max-width: 34rem; padding-top: 4rem; direction: rtl; text-align: right;
        }
        [data-testid="stForm"] {
            padding: 1.4rem; border-radius: 0.8rem;
        }
        [data-testid="stForm"] label { text-align: right; }
        [data-testid="stForm"] input { text-align: right; }
        [data-testid="stForm"] input[type="password"] { direction: ltr; text-align: left; }
        @media (max-width: 640px) {
            [data-testid="stMainBlockContainer"] { padding-top: 1.5rem; }
        }
        </style>""",
        unsafe_allow_html=True,
    )
    st.title("سامانه پایش کیفیت آب")
    st.subheader("ورود به سامانه")
    if not store.has_users():
        st.info("هنوز حساب مدیری ایجاد نشده است. ابتدا مدیر نخست را طبق راهنمای راه‌اندازی ایجاد کنید.")
        return

    with st.form("auth_login", clear_on_submit=True):
        username = st.text_input("نام کاربری", autocomplete="username")
        password = st.text_input("گذرواژه", type="password", autocomplete="current-password")
        submitted = st.form_submit_button("ورود", type="primary")

    if submitted:
        try:
            user = store.authenticate(username, password)
        except AuthError:
            user = None
        if user is None:
            st.error("نام کاربری یا گذرواژه درست نیست، یا حساب غیرفعال است.")
            return
        _clear_session()
        st.session_state["auth_user_id"] = user.id
        st.rerun()


def _render_password_change(store, user, busy):
    st.caption("گذرواژه باید حداقل ۸ نویسه داشته باشد.")
    with st.form("auth_change_password", clear_on_submit=True):
        old = st.text_input("گذرواژه فعلی", type="password", autocomplete="current-password")
        new = st.text_input("گذرواژه جدید", type="password", autocomplete="new-password")
        confirm = st.text_input("تکرار گذرواژه جدید", type="password", autocomplete="new-password")
        submitted = st.form_submit_button("تغییر گذرواژه", disabled=busy)
    if submitted:
        if new != confirm:
            st.error("گذرواژه جدید و تکرار آن یکسان نیستند.")
            return
        try:
            store.change_password(user.id, old, new)
        except AuthError:
            st.error("تغییر گذرواژه انجام نشد. گذرواژه فعلی و شرایط گذرواژه جدید را بررسی کنید.")
            return
        st.success("گذرواژه تغییر کرد.")


def _render_user_management(store, actor):
    """Render account management; each store operation verifies admin rights."""
    st.subheader("مدیریت کاربران")
    notice = st.session_state.pop("auth_user_notice", None)
    if notice:
        st.success(notice)

    with st.expander("ایجاد حساب جدید", expanded=True):
        st.caption("نام کاربری بدون فاصله و گذرواژه حداقل ۸ نویسه باشد.")
        with st.form("auth_create_user", clear_on_submit=True):
            username = st.text_input("نام کاربری حساب جدید", autocomplete="off")
            password = st.text_input("گذرواژه حساب جدید", type="password", autocomplete="new-password")
            confirm = st.text_input("تکرار گذرواژه حساب جدید", type="password", autocomplete="new-password")
            role_label = st.selectbox("نقش", ("تحلیل‌گر", "مدیر"))
            submitted = st.form_submit_button("ایجاد حساب", type="primary")
        if submitted:
            if password != confirm:
                st.error("گذرواژه و تکرار آن یکسان نیستند.")
            else:
                try:
                    store.create_user(actor.id, username, password,
                                      role="admin" if role_label == "مدیر" else "analyst")
                except AuthError:
                    st.error("ایجاد حساب انجام نشد. نام کاربری، شرایط گذرواژه و دسترسی خود را بررسی کنید.")
                else:
                    st.success("حساب کاربری ایجاد شد.")

    try:
        users = store.list_users(actor.id)
    except AuthError:
        st.error("دسترسی به فهرست کاربران امکان‌پذیر نیست.")
        return

    st.markdown("#### حساب‌های موجود")
    for user in users:
        details, action = st.columns([4, 1])
        with details:
            role = "مدیر" if user.role == "admin" else "تحلیل‌گر"
            status = "فعال" if user.active else "غیرفعال"
            st.text(f"{user.username} — {role} — {status}")
        with action:
            label = "غیرفعال‌سازی" if user.active else "فعال‌سازی"
            # An administrator cannot lock out their own current session.
            if st.button(label, key=f"auth_toggle_{user.id}", disabled=user.id == actor.id):
                try:
                    store.set_active(actor.id, user.id, not user.active)
                except AuthError:
                    st.error("تغییر وضعیت حساب انجام نشد.")
                else:
                    st.session_state["auth_user_notice"] = "وضعیت حساب به‌روزرسانی شد."
                    st.rerun()


def render_account_controls(store, user):
    """Render controls and return whether the account-management page is open.

    Processing must finish before changing identity or leaving the active run.
    """
    processing = bool(st.session_state.get("processing_in_progress", False))
    unsaved = bool(st.session_state.get("history_unsaved", False))
    busy = processing or unsaved
    is_admin = user.role == "admin"
    with st.sidebar.expander("👤 حساب کاربری"):
        st.text(f"کاربر: {user.username}")
        st.write("نقش: مدیر" if is_admin else "نقش: تحلیل‌گر")
        if processing:
            st.caption("پس از پایان پردازش می‌توانید از حساب خارج شوید یا کاربران را مدیریت کنید.")
        elif unsaved:
            st.caption("ابتدا تغییرات ذخیره‌نشده تحلیل هوشمند را تعیین تکلیف کنید.")
        if is_admin:
            managing = bool(st.session_state.get("auth_manage_users", False))
            label = "بازگشت به پایش" if managing else "مدیریت کاربران"
            if st.button(label, key="auth_manage_button", disabled=busy):
                st.session_state["auth_manage_users"] = not managing
                st.session_state["showing_history"] = False
                st.rerun()
        if st.button("خروج از حساب", key="auth_logout", disabled=busy):
            _clear_session()
            st.rerun()
        _render_password_change(store, user, busy)

    managing = is_admin and bool(st.session_state.get("auth_manage_users", False))
    if managing:
        _render_user_management(store, user)
    return managing
