"""Overview of the planned RentAI application architecture."""

import streamlit as st

from frontend.components.data_utils import page_heading


def render(data) -> None:
	page_heading("Implementation Overview", "The application is organized around CSV-backed services and modular user workflows.")
	stages = [
		("Streamlit UI", "Dashboard, search, analysis, and user inputs"),
		("FastAPI backend", "REST endpoints planned for a later phase"),
		("Python services", "Data access and application-level operations"),
		("ML models", "Regression, classification, clustering, and evaluation"),
		("CSV source", "data/rentai_dataset.csv is the current source of truth"),
	]
	for index, (title, detail) in enumerate(stages):
		with st.container(border=True):
			columns = st.columns([1, 5])
			columns[0].markdown(f"<div class='stage-number'>{index + 1:02}</div>", unsafe_allow_html=True)
			columns[1].markdown(f"**{title}**  \n{detail}")
	st.info("The UI and CSV workflows are being built first. No API server, model training, or database is active in this phase.")