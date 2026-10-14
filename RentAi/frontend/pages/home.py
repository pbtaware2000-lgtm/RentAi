"""Home page; implementation is planned for Phase 2."""
"""Data-backed RentAI dashboard."""

import pandas as pd
import plotly.express as px
import streamlit as st

from frontend.components.charts import GREEN, INDIGO, ORANGE, render_chart
from frontend.components.data_utils import numeric_column, show_empty
from frontend.components.header import render_dashboard_header
from frontend.components.metric_cards import render_dashboard_metrics
from frontend.components.property_card import render_action_card
from frontend.components.tables import render_table


def _rent_overview(data: pd.DataFrame) -> None:
	if "rent" not in data.columns:
		show_empty("The dataset does not include a `rent` column.")
		return

	chart_data = data.copy()
	chart_data["rent"] = pd.to_numeric(chart_data["rent"], errors="coerce")
	chart_data = chart_data.dropna(subset=["rent"])
	if chart_data.empty:
		show_empty("No numeric rental prices are available in the dataset.")
		return

	if "posted_date" in chart_data.columns:
		dates = pd.to_datetime(chart_data["posted_date"], errors="coerce")
		if dates.nunique() > 1:
			chart_data = chart_data.assign(month=dates.dt.to_period("M").astype(str))
			trend = chart_data.groupby("month", as_index=False)["rent"].mean()
			figure = px.line(trend, x="month", y="rent", markers=True, color_discrete_sequence=[ORANGE])
			figure.update_layout(xaxis_title="Posted month", yaxis_title="Average rent")
			render_chart(figure, "home-rent-trend")
			return

	figure = px.histogram(
		chart_data,
		x="rent",
		nbins=24,
		color_discrete_sequence=[ORANGE],
		labels={"rent": "Monthly rent", "count": "Properties"},
	)
	figure.update_layout(xaxis_title="Monthly rent", yaxis_title="Properties", bargap=0.08)
	render_chart(figure, "home-rent-distribution")


def _property_distribution(data: pd.DataFrame) -> None:
	if "property_type" not in data.columns:
		show_empty("The dataset does not include a `property_type` column.")
		return
	counts = data["property_type"].dropna().astype(str).value_counts().rename_axis("Property type").reset_index(name="Properties")
	if counts.empty:
		show_empty("No property type values are available.")
		return
	figure = px.pie(
		counts,
		names="Property type",
		values="Properties",
		hole=0.64,
		color_discrete_sequence=[ORANGE, INDIGO, GREEN, "#93c5fd", "#fbbf24"],
	)
	figure.update_traces(textposition="inside", textinfo="percent")
	figure.add_annotation(text=f"{len(data):,}<br><span style='font-size:11px'>properties</span>", showarrow=False, font={"size": 20, "color": "#0f172a"})
	render_chart(figure, "home-property-distribution", height=340)


def _popular_locations(data: pd.DataFrame) -> None:
	dimensions = [column for column in ("city", "locality") if column in data.columns]
	if not dimensions or "rent" not in data.columns:
		show_empty("City, locality, and rent columns are needed to calculate popular locations.")
		return
	work = data[dimensions + ["rent"]].copy()
	work["rent"] = pd.to_numeric(work["rent"], errors="coerce")
	summary = work.groupby(dimensions, dropna=True).agg(
		average_rent=("rent", "mean"),
		properties=("rent", "size"),
	).reset_index()
	if summary.empty:
		show_empty("No location and rent values are available.")
		return
	summary["average_rent"] = summary["average_rent"].round(0)
	summary = summary.sort_values(["properties", "average_rent"], ascending=[False, True]).head(8)
	summary = summary.rename(columns={"city": "CITY", "locality": "LOCALITY", "average_rent": "AVERAGE RENT", "properties": "PROPERTIES"})
	if "CITY" not in summary:
		summary.insert(0, "CITY", "-")
	if "LOCALITY" not in summary:
		summary.insert(1, "LOCALITY", "-")
	summary["AVERAGE RENT"] = summary["AVERAGE RENT"].map(lambda value: f"₹{value:,.0f}" if pd.notna(value) else "-")
	render_table(summary[["CITY", "LOCALITY", "AVERAGE RENT", "PROPERTIES"]], "home-popular-locations", height=320)


def render(data: pd.DataFrame) -> None:
	render_dashboard_header()

	search_col, status_col = st.columns([4, 1])
	with search_col:
		with st.form("dashboard-search", border=False):
			query_col, button_col = st.columns([4, 1.25])
			with query_col:
				query = st.text_input("Search the property dataset", placeholder="City, locality, or property type", label_visibility="collapsed")
			with button_col:
				submitted = st.form_submit_button("Search", width="stretch", type="primary")
			if submitted:
				st.session_state.property_search_query = query
				st.session_state.page = "Property Search"
				st.rerun()
	with status_col:
		st.caption("ACTIVE WORKSPACE")
		st.markdown("**Rental Analytics**")

	with st.container(border=True):
		left, right = st.columns([3, 1])
		with left:
			st.markdown("<div class='eyebrow'>RENTAL MARKET INTELLIGENCE</div>", unsafe_allow_html=True)
			st.header("Smart Rental Intelligence")
			st.write("Predict rental prices, discover similar properties, compare listings and analyze rental trends using Machine Learning.")
			st.caption("CSV-backed market analysis · ML services are not connected yet")
		with right:
			st.markdown("<div class='hero-mark'>₹</div>", unsafe_allow_html=True)
			if st.button("Predict Rent", type="primary", width="stretch", key="hero-predict"):
				st.session_state.page = "Rent Prediction"
				st.rerun()
			if st.button("Search Properties", width="stretch", key="hero-search"):
				st.session_state.page = "Property Search"
				st.rerun()

	render_dashboard_metrics(data)

	chart_col, distribution_col = st.columns([1.65, 1])
	with chart_col:
		with st.container(border=True):
			st.subheader("Rental Price Overview")
			st.caption("Price trend when dated listings are available; otherwise, the observed rent distribution.")
			_rent_overview(data)
	with distribution_col:
		with st.container(border=True):
			st.subheader("Property Distribution")
			st.caption("Listings by property type")
			_property_distribution(data)

	st.subheader("Quick Actions & ML Workflows")
	action_columns = st.columns(4)
	actions = [
		("↗", "Rent Prediction", "Estimate a listing's rent when a model is connected.", "Rent Prediction"),
		("⌕", "Property Search", "Filter available listings from the CSV dataset.", "Property Search"),
		("⇄", "Property Comparison", "Compare selected listings side by side.", "Property Comparison"),
		("◎", "Similar Properties", "Explore clustering workflows; no model is trained yet.", "Property Clustering"),
	]
	for index, (column, action) in enumerate(zip(action_columns, actions)):
		with column:
			render_action_card(*action, key=f"home-action-{index}")

	location_col, recent_col = st.columns([1.6, 1])
	with location_col:
		with st.container(border=True):
			st.subheader("Popular Rental Locations")
			st.caption("Calculated from the loaded property records")
			_popular_locations(data)
	with recent_col:
		with st.container(border=True):
			st.subheader("Recent Predictions")
			st.caption("Prediction history will appear after model integration")
			st.info("No ML predictions available yet.")

	if data.empty:
		show_empty()