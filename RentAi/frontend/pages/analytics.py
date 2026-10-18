"""Location, rental, distance, and amenity analytics from CSV records."""

import pandas as pd
import plotly.express as px
import streamlit as st

from frontend.components.charts import GREEN, INDIGO, ORANGE, style_figure
from frontend.components.data_utils import page_heading, show_empty


def _render_analytics_chart(figure, key: str, *, height: int = 340, categorical_x: bool = False) -> None:
	"""Apply readable page-local styling without changing shared chart styles."""
	style_figure(figure, height)
	figure.update_layout(
		margin={"l": 72, "r": 24, "t": 56, "b": 92 if categorical_x else 64},
		font={"color": "#0f172a", "family": "Aptos, Segoe UI, sans-serif", "size": 14},
		title_font={"color": "#0f172a", "size": 17},
		legend={"font": {"color": "#0f172a", "size": 13}},
	)
	figure.update_xaxes(
		automargin=True,
		tickangle=-35 if categorical_x else 0,
		tickfont={"color": "#0f172a", "size": 13},
		title_font={"color": "#0f172a", "size": 14},
	)
	figure.update_yaxes(
		automargin=True,
		tickfont={"color": "#0f172a", "size": 13},
		title_font={"color": "#0f172a", "size": 14},
	)
	st.plotly_chart(figure, width="stretch", key=key, config={"displayModeBar": False})


def _average_chart(data: pd.DataFrame, dimension: str, key: str, title: str) -> None:
	if dimension not in data.columns or "rent" not in data.columns:
		show_empty(f"Both `{dimension}` and `rent` columns are required.")
		return
	work = data[[dimension, "rent"]].copy()
	work["rent"] = pd.to_numeric(work["rent"], errors="coerce")
	grouped = work.dropna().groupby(dimension)["rent"].agg(["mean", "size"]).reset_index().sort_values("mean", ascending=False).head(20)
	if grouped.empty:
		show_empty("No usable rent and category values are available.")
		return
	figure = px.bar(grouped, x=dimension, y="mean", color_discrete_sequence=[INDIGO], hover_data={"size": True})
	figure.update_layout(title=title, xaxis_title=dimension.replace("_", " ").title(), yaxis_title="Average rent")
	_render_analytics_chart(figure, key, categorical_x=True)


def render(data: pd.DataFrame) -> None:
	page_heading("Location & Analytics", "Analyze observed rent patterns across locations, property types, and amenities.")
	if data.empty:
		st.info("Add property records to the CSV to populate location and rental analysis.")
		return

	city_col, locality_col = st.columns(2)
	with city_col:
		with st.container(border=True):
			st.subheader("City-wise average rent")
			_average_chart(data, "city", "analytics-city-rent", "Average rent by city")
	with locality_col:
		with st.container(border=True):
			st.subheader("Locality-wise average rent")
			_average_chart(data, "locality", "analytics-locality-rent", "Average rent by locality")

	type_col, area_col = st.columns(2)
	with type_col:
		with st.container(border=True):
			st.subheader("Rent by property type")
			_average_chart(data, "property_type", "analytics-type-rent", "Average rent by property type")
	with area_col:
		with st.container(border=True):
			st.subheader("Rent vs area")
			if not {"rent", "area_sqft"}.issubset(data.columns):
				show_empty("Both `rent` and `area_sqft` columns are required.")
			else:
				work = data[["rent", "area_sqft"]].apply(pd.to_numeric, errors="coerce").dropna()
				if work.empty:
					show_empty("No numeric rent and area values are available.")
				else:
					figure = px.scatter(work, x="area_sqft", y="rent", opacity=0.65, color_discrete_sequence=[ORANGE], labels={"area_sqft": "Area (sq ft)", "rent": "Rent"})
					_render_analytics_chart(figure, "analytics-rent-area")

	st.subheader("Distance vs rent")
	distance_columns = [column for column in ("distance_to_city_center_km", "distance_to_metro_km", "distance_to_school_km", "distance_to_hospital_km", "distance_to_it_park_km") if column in data.columns]
	if not distance_columns or "rent" not in data.columns:
		show_empty("No distance metric and rent pair is available in this dataset.")
	else:
		distance = st.selectbox("Distance feature", distance_columns, format_func=lambda name: name.replace("_", " ").title())
		work = data[[distance, "rent"]].apply(pd.to_numeric, errors="coerce").dropna()
		if work.empty:
			show_empty("No numeric values are available for this distance feature.")
		else:
			figure = px.scatter(work, x=distance, y="rent", opacity=0.65, color_discrete_sequence=[GREEN], labels={distance: distance.replace("_", " ").title(), "rent": "Rent"})
			_render_analytics_chart(figure, "analytics-distance-rent")

	st.subheader("Amenity analysis")
	amenities = [column for column in ("balcony", "parking", "lift", "gym", "security", "power_backup", "swimming_pool", "air_conditioning", "water_supply", "gated_community") if column in data.columns]
	if not amenities:
		show_empty("No amenity columns are available in the dataset.")
	else:
		rows = []
		for name in amenities:
			values = data[name]
			if pd.api.types.is_numeric_dtype(values):
				available = pd.to_numeric(values, errors="coerce").gt(0)
			elif name == "water_supply":
				# The dataset stores supply types (for example, "24x7"), not yes/no flags.
				unavailable = {"no", "false", "0", "none", "unavailable", "not available", "n/a"}
				supply = values.astype("string").str.strip().str.casefold()
				available = supply.notna() & supply.ne("") & ~supply.isin(unavailable)
			else:
				available = values.astype(str).str.strip().str.casefold().isin({"yes", "true", "1", "available", "included", "y"})
			observed = values.notna()
			rate = available[observed].mean() if observed.any() else float("nan")
			rows.append({"Amenity": name.replace("_", " ").title(), "Available listings": int(available[observed].sum()), "Availability rate": rate})
		summary = pd.DataFrame(rows)
		summary["Availability rate"] = (summary["Availability rate"] * 100).round(1)
		figure = px.bar(summary, x="Amenity", y="Availability rate", color_discrete_sequence=[ORANGE], labels={"Availability rate": "Availability (%)"})
		_render_analytics_chart(figure, "analytics-amenity-availability", height=340, categorical_x=True)
		st.dataframe(summary, width="stretch", hide_index=True)
