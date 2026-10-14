"""Secure Firebase sign-in view for the existing Streamlit application."""

import streamlit as st

from frontend.authentication import (
    AuthenticationError,
    fetch_verified_user,
    firebase_password_sign_in,
    normalize_email,
    save_session,
)


def render(*, selected_role: str | None = None, allow_registration: bool = True) -> None:
    role_title = selected_role.title() if selected_role in {"admin", "owner", "renter", "broker"} else None
    st.title(f"{role_title} sign in" if role_title else "Sign in to RentAI")
    st.caption(
        f"Sign in to your {role_title} account."
        if role_title else "Use your RentAI account to continue to the tools available for your role."
    )
    with st.form("rentai_login", clear_on_submit=False):
        email = st.text_input("Email", key="rentai_login_email", autocomplete="email")
        password = st.text_input(
            "Password", key="rentai_login_password", type="password", autocomplete="current-password"
        )
        submitted = st.form_submit_button("Sign in", type="primary", width="stretch")
    if submitted:
        try:
            email = normalize_email(email)
            tokens = firebase_password_sign_in(email, password)
            user = fetch_verified_user(tokens["id_token"])
            if selected_role and user["role"] != selected_role:
                st.error(
                    f"This account is registered as {user['role'].title()}. "
                    "Go back and choose that account type."
                )
                return
            save_session(st.session_state, tokens, user)
            st.session_state.page = {
                "owner": "Owner Dashboard", "renter": "Renter Dashboard",
                "broker": "Broker Dashboard", "admin": "Admin Dashboard",
            }[user["role"]]
            st.session_state.pop("rentai_selected_role", None)
            st.session_state.pop("rentai_auth_view", None)
        except AuthenticationError as exc:
            st.error(str(exc))
            return
        st.rerun()

    if allow_registration:
        label = f"Create a {role_title} account" if role_title else "Create an account"
        if st.button(label, key="open_registration"):
            if selected_role:
                st.session_state["rentai_auth_view"] = "register"
            else:
                st.session_state.page = "Register"
            st.rerun()
    if selected_role and st.button("← Back to role selection", key="auth_back_to_roles"):
        st.session_state.pop("rentai_selected_role", None)
        st.session_state.pop("rentai_auth_view", None)
        st.rerun()
