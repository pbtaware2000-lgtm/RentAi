"""Learning module for rental analytics and model concepts."""

import streamlit as st

from frontend.components.data_utils import page_heading


def render(data) -> None:
	page_heading("Learning Module", "A guided introduction to the data and methods behind RentAI.")
	st.subheader("Rental analytics workflow")
	steps = st.columns(4)
	for column, number, title, detail in zip(
		steps,
		("01", "02", "03", "04"),
		("Collect", "Prepare", "Model", "Interpret"),
		("Property listings", "Clean and transform fields", "Train and evaluate", "Explain market signals"),
	):
		with column:
			with st.container(border=True):
				st.caption(number)
				st.subheader(title)
				st.caption(detail)

	st.subheader("Explore the concepts")
	topics = {
		"Rental price prediction": "Regression estimates a numeric rent from property, amenity, and location features. A useful evaluation compares predictions with held-out listings.",
		"Rent category classification": "Classification maps a listing to an ordinal category such as Low, Medium, High, or Premium. Category boundaries must be defined from the data or product requirements.",
		"Property clustering": "Clustering groups listings by similarity without using a target label. Feature scaling and careful feature selection affect which properties appear similar.",
		"Interpretable results": "Feature importance and SHAP can help explain model behavior, but neither should be treated as causal evidence about rent.",
	}
	for title, body in topics.items():
		with st.expander(title):
			st.write(body)

	st.subheader("Dataset at a glance")
	if data.empty:
		st.info("No rows are loaded yet. Add the rental CSV to begin exploring the available fields.")
	else:
		st.write(f"The current CSV contains **{len(data):,} rows** and **{len(data.columns):,} columns**.")
		st.caption("Use Dataset & EDA to inspect actual values, missingness, and distributions.")