"""Dedicated navigation for the authenticated Owner workspace."""

import streamlit as st


OWNER_MENU = (
    ("Dashboard", "▦"),
    ("My Properties", "⌂"),
    ("Add New Property", "+"),
    ("Applications & Enquiries", "✉"),
    ("Renter List", "♙"),
    ("Property Analytics", "▥"),
    ("Revenue & Rent Collection", "₹"),
    ("Maintenance Requests", "⚒"),
    ("My Profile", "◉"),
    ("Settings", "⚙"),
    ("App Reviews", "★"),
    ("Support & Complaints", "?"),
)


def _select_owner_page(page: str) -> None:
    st.session_state.owner_active_page = page


def _return_to_main() -> None:
    st.session_state.page = "Home"


def render_owner_sidebar(*, on_logout) -> None:
    active = st.session_state.get("owner_active_page", "Dashboard")
    with st.sidebar:
        st.markdown("<div class='brand-mark'>R</div>", unsafe_allow_html=True)
        st.title("Owner Workspace")
        st.caption("RENTAL MANAGEMENT")
        st.divider()
        for label, icon in OWNER_MENU:
            st.button(
                f"{icon}  {label}",
                key=f"owner_nav_{label}",
                width="stretch",
                type="primary" if active == label else "secondary",
                on_click=_select_owner_page,
                args=(label,),
            )
        st.divider()
        st.button(
            "←  Back to Main Dashboard", key="owner_back_to_main",
            width="stretch", on_click=_return_to_main,
        )
        st.button("Sign out", key="owner_logout", width="stretch", on_click=on_logout)
