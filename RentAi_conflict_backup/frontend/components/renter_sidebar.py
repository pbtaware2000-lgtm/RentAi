"""Dedicated navigation for the authenticated Renter workspace."""

import streamlit as st


RENTER_MENU = (
    ("Dashboard", "⌂"),
    ("Search Properties", "⌕"),
    ("AI Rent Prediction", "✦"),
    ("Compare Properties", "⇄"),
    ("Saved Properties", "♡"),
    ("My Enquiries", "✉"),
    ("My Profile", "◉"),
    ("Settings", "⚙"),
    ("App Reviews", "★"),
    ("Support & Complaints", "?"),
)


def _select_page(page: str) -> None:
    st.session_state.renter_active_page = page


def render_renter_sidebar(*, on_logout) -> None:
    active = st.session_state.get("renter_active_page", "Dashboard")
    name = (st.session_state.get(f"renter_profile_name_{st.session_state.get('rentai_uid') or 'session'}")
            or st.session_state.get("rentai_display_name") or "Renter")
    with st.sidebar:
        st.markdown("<div class='brand-mark'>R</div>", unsafe_allow_html=True)
        st.title("RentAI")
        st.caption("RENTER WORKSPACE")
        st.markdown(f"**{name}**")
        st.caption("Renter account")
        st.divider()
        for label, icon in RENTER_MENU:
            st.button(
                f"{icon}  {label}", key=f"renter_nav_{label}", width="stretch",
                type="primary" if active == label else "secondary",
                on_click=_select_page, args=(label,),
            )
        st.divider()
        st.button("Log out", key="renter_logout", width="stretch", on_click=on_logout)
