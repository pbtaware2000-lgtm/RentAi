"""Compact, reusable action tile for dashboard workflows."""

import streamlit as st


def render_action_card(icon: str, title: str, description: str, page: str, key: str) -> None:
	with st.container(border=True):
		st.markdown(f"<div class='action-icon'>{icon}</div>", unsafe_allow_html=True)
		st.subheader(title)
		st.caption(description)
		if st.button("Open", key=key, width="stretch"):
			st.session_state.page = page
			st.rerun()