"""Shared page header components."""

import streamlit as st


def render_dashboard_header() -> None:
	left, right = st.columns([3, 1])
	with left:
		st.title("RentAI Dashboard")
		st.caption("Smart Rental Price & Property Analysis System")
	with right:
		st.markdown("<div class='engine-status'>CSV analysis ready · ML model not connected</div>", unsafe_allow_html=True)


def render_page_header(title: str, description: str) -> None:
	st.title(title)
	st.caption(description)