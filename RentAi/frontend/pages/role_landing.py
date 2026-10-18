"""Unauthenticated role-selection entry page for RentAI."""

import streamlit as st


PUBLIC_ROLES = {"owner", "renter", "broker"}
ROLE_CARDS = (
    ("admin", "Admin", "Manage platform", "verified_user"),
    ("owner", "Owner", "Manage properties", "home"),
    ("renter", "Renter", "Find a rental home", "key"),
    ("broker", "Broker", "Manage clients", "business_center"),
)


def _select_role(role: str) -> None:
  for key in (
    "rentai_login_password", "rentai_register_name", "rentai_register_email",
    "rentai_register_password", "rentai_register_confirm",
  ):
    st.session_state.pop(key, None)
    st.session_state["rentai_selected_role"] = role
    st.session_state["rentai_auth_view"] = "signin"


def render() -> None:
    st.markdown(
        """
        <style>
        [data-testid="stAppViewContainer"] { background: #090b0e; }
        [data-testid="stSidebar"] { display: none; }

        [data-testid="stSidebar"] { display: none; }

        [data-testid="stMain"] { align-items: flex-start !important; }
        [data-testid="stMainBlockContainer"] {
          width: 100%; max-width: 960px; padding-top: 2.2rem; color: #f4f5f6;
        }
        [data-testid="stMainBlockContainer"] h1,
        [data-testid="stMainBlockContainer"] h2,
        [data-testid="stMainBlockContainer"] p {
          color: #f4f5f6 !important;
        }
        [data-testid="stMainBlockContainer"] [data-testid="stCaptionContainer"] {
          color: #a6abb3 !important;
        }
        .rentai-landing-brand {
          display: flex; align-items: center; gap: 14px; margin: .4rem 0 5.2rem;
        }
        .rentai-landing-mark {
          display: grid; place-items: center; width: 52px; height: 52px;
          border-radius: 12px; background: #ff6b1a; color: #fff;
          font-size: 28px; font-weight: 700;
        }
        .rentai-landing-name { color: #fff; font-size: 1.35rem; font-weight: 750; }
        .rentai-landing-tagline { color: #a6abb3; font-size: .88rem; }
        .rentai-landing-heading { text-align: center; margin: 0 auto 2rem; }
        .rentai-landing-heading h1 {
          font-size: 2.25rem !important; margin-bottom: .45rem;
        }
        .rentai-landing-heading p { color: #a6abb3 !important; font-size: 1rem; }
        [class*="st-key-role_select_"] .stButton button {
          min-height: 142px; height: 142px; padding: 1.25rem 1.4rem;
          border: 1px solid #292d33; border-radius: 12px;
          background: #14171b !important; color: #f4f5f6 !important;
          justify-content: flex-start !important; text-align: left; white-space: pre-wrap;
          transition: border-color .16s ease, background .16s ease, transform .16s ease;
        }
        [class*="st-key-role_select_"] .stButton button p {
          color: #f4f5f6 !important; font-size: 1rem; line-height: 1.8;
        }
        [class*="st-key-role_select_"] .stButton button [data-testid="stIconMaterial"] {
          color: #ff6b1a !important;
        }
        [class*="st-key-role_select_"] .stButton button:hover {
          border-color: #ff6b1a; background: #1b1d20 !important;
          transform: translateY(-2px);
        }
        @media (max-width: 640px) {
          .rentai-landing-brand { margin-bottom: 3.4rem; }
          .rentai-landing-heading h1 { font-size: 1.8rem !important; }
        }
        </style>
        <div class="rentai-landing-brand">
          <div class="rentai-landing-mark">⌂</div>
          <div><div class="rentai-landing-name">RentAI</div>
          <div class="rentai-landing-tagline">Smart Rental Property Platform</div></div>
        </div>
        <div class="rentai-landing-heading">
          <h1>Welcome to RentAI</h1>
          <p>Select your account type to continue</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    with st.container(key="rentai_role_cards"):
        for row_start in (0, 2):
            columns = st.columns(2, gap="medium")
            for column, (role, title, description, icon) in zip(
                columns, ROLE_CARDS[row_start:row_start + 2]
            ):
                with column:
                    st.button(
                        f"**{title}**  \n{description}",
                        key=f"role_select_{role}",
                        icon=f":material/{icon}:",
                        width="stretch",
                        on_click=_select_role,
                        args=(role,),
                    )
                    )

                    )

