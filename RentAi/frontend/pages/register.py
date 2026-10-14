"""Public registration for Owner, Renter, and Broker accounts."""

import streamlit as st

from frontend.authentication import (
    AuthenticationError, fetch_verified_user, firebase_password_sign_up,
    normalize_email, provision_public_role, save_session,
)
from frontend.pages.role_landing import PUBLIC_ROLES


def render(selected_role: str | None = None) -> None:
    role = st.session_state.get("rentai_selected_role")
    if role not in PUBLIC_ROLES or role != selected_role:
        st.error("Choose an account type before registering.")
        return
    role_title = role.title()
    st.title(f"Create your {role_title} account")
    st.caption(f"Registering as {role_title}. Admin accounts are provisioned by an administrator.")
    with st.form("rentai_register", clear_on_submit=False):
        name = st.text_input("Full name", key="rentai_register_name", autocomplete="name")
        email = st.text_input("Email address", key="rentai_register_email", autocomplete="email")
        password = st.text_input("Password", type="password", key="rentai_register_password", autocomplete="new-password")
        confirmation = st.text_input("Confirm password", type="password", key="rentai_register_confirm", autocomplete="new-password")
        submitted = st.form_submit_button("Register", type="primary", width="stretch")
    if st.button("Back to sign in", key="registration_login_link"):
        st.session_state["rentai_auth_view"] = "signin"
        st.rerun()
    if st.button("← Back to role selection", key="register_back_to_roles"):
        st.session_state.pop("rentai_selected_role", None)
        st.session_state.pop("rentai_auth_view", None)
        st.rerun()
    if not submitted:
        return
    if not name.strip() or not email.strip() or not password:
        st.error("Enter your full name, email address, and password.")
        return
    if password != confirmation:
        st.error("The passwords do not match.")
        return
    try:
        email = normalize_email(email)
    except AuthenticationError as exc:
        st.error(str(exc))
        return
    if len(password) < 6:
        st.error("Choose a password with at least 6 characters.")
        return
    try:
        with st.spinner("Creating your account…"):
            tokens = firebase_password_sign_up(email, password, name)
            provision_public_role(tokens["id_token"], role)
            user = fetch_verified_user(tokens["id_token"])
            if user["role"] != role:
                st.error("RentAI could not verify the selected account type. Contact an administrator.")
                return
            save_session(st.session_state, tokens, user)
            st.session_state.pop("rentai_selected_role", None)
            st.session_state.pop("rentai_auth_view", None)
        st.session_state.page = f"{role_title} Dashboard"
        st.session_state["rentai_registration_success"] = True
        st.rerun()
    except AuthenticationError as exc:
        st.error(str(exc))
