"""Filter and search properties directly from the CSV dataset."""

import pandas as pd
import streamlit as st

from frontend.components.data_utils import category_options, numeric_column, page_heading
from frontend.components.tables import render_table


def _filter_amenity(values: pd.Series, choice: str) -> pd.Series:
	normalized = values.astype(str).str.strip().str.casefold()
	if pd.api.types.is_numeric_dtype(values):
		numbers = pd.to_numeric(values, errors="coerce").fillna(0)
		return numbers.gt(0) if choice == "Yes" else numbers.eq(0)
	yes_values = {"yes", "true", "1", "available", "included", "y"}
	no_values = {"no", "false", "0", "not available", "not included", "n"}
	return normalized.isin(yes_values) if choice == "Yes" else normalized.isin(no_values)


def render(data: pd.DataFrame) -> None:
	page_heading("Property Search", "Search and filter the available listings in the CSV dataset.")
	if data.empty:
		st.info("No properties are available. Add listing rows to `data/rentai_dataset.csv` to enable search.")
		return

	filters = st.columns(4)
	selected: dict[str, object] = {}
	for column, name, label in zip(filters[:3], ("city", "locality", "property_type"), ("City", "Locality", "Property type")):
		with column:
			options = category_options(data, name)
			if name in data.columns:
				selected[name] = st.selectbox(label, ["All"] + options, key=f"search-{name}")
			else:
				st.selectbox(label, ["Column unavailable"], disabled=True, key=f"search-{name}-missing")
	with filters[3]:
		bedrooms = category_options(data, "bedrooms")
		if bedrooms:
			selected["bedrooms"] = st.multiselect("Bedrooms", bedrooms, key="search-bedrooms")
		else:
			st.multiselect("Bedrooms", [], disabled=True, key="search-bedrooms-missing")

	second_row = st.columns(4)
	with second_row[0]:
		options = category_options(data, "furnishing_status")
		if options:
			selected["furnishing_status"] = st.selectbox("Furnishing status", ["All"] + options, key="search-furnishing")
		else:
			st.selectbox("Furnishing status", ["Column unavailable"], disabled=True, key="search-furnishing-missing")
	with second_row[1]:
		rent = numeric_column(data, "rent")
		if not rent.empty:
			minimum = float(rent.min())
			maximum = float(rent.max())
			selected["min_rent"] = st.number_input("Minimum rent", min_value=minimum, max_value=maximum, value=minimum, key="search-min-rent")
		else:
			st.number_input("Minimum rent", value=0.0, disabled=True, key="search-min-rent-missing")
	with second_row[2]:
		if not rent.empty:
			selected["max_rent"] = st.number_input("Maximum rent", min_value=float(rent.min()), max_value=float(rent.max()), value=float(rent.max()), key="search-max-rent")
		else:
			st.number_input("Maximum rent", value=0.0, disabled=True, key="search-max-rent-missing")

	distance_row = st.columns(3)
	for column, label, name in zip(
		distance_row,
		("Metro", "School", "Hospital"),
		("distance_to_metro_km", "distance_to_school_km", "distance_to_hospital_km"),
	):
		with column:
			selected[f"min_{name}"] = st.number_input(
				f"Minimum {label} Distance (km)",
				min_value=0.0,
				value=None,
				step=0.1,
				placeholder="Any",
				key=f"search-min-{name}",
			)
			selected[f"max_{name}"] = st.number_input(
				f"Maximum {label} Distance (km)",
				min_value=0.0,
				value=None,
				step=0.1,
				placeholder="Any",
				key=f"search-max-{name}",
			)

	amenity_columns = st.columns(4)
	for column, name in zip(amenity_columns, ("parking", "gym", "lift", "security")):
		with column:
			if name in data.columns:
				selected[name] = st.selectbox(name.replace("_", " ").title(), ["Any", "Yes", "No"], key=f"search-{name}-filter")
			else:
				st.selectbox(name.replace("_", " ").title(), ["Column unavailable"], disabled=True, key=f"search-{name}-missing")

	if st.button("Search Properties", type="primary", key="property-search-submit"):
		filtered = data.copy()
		for name in ("city", "locality", "property_type", "furnishing_status"):
			value = selected.get(name)
			if value and value != "All" and name in filtered.columns:
				filtered = filtered[filtered[name].astype(str) == value]
		bedroom_values = selected.get("bedrooms", [])
		if bedroom_values and "bedrooms" in filtered.columns:
			filtered = filtered[filtered["bedrooms"].astype(str).isin(bedroom_values)]
		if "min_rent" in selected and "rent" in filtered.columns:
			rents = pd.to_numeric(filtered["rent"], errors="coerce")
			filtered = filtered[rents.ge(selected["min_rent"])]
			rents = pd.to_numeric(filtered["rent"], errors="coerce")
			filtered = filtered[rents.le(selected["max_rent"])]
		for name in ("distance_to_metro_km", "distance_to_school_km", "distance_to_hospital_km"):
			if name not in filtered.columns:
				continue
			distances = pd.to_numeric(filtered[name], errors="coerce")
			minimum_distance = selected.get(f"min_{name}")
			maximum_distance = selected.get(f"max_{name}")
			if minimum_distance is not None:
				filtered = filtered[distances.ge(minimum_distance)]
				distances = pd.to_numeric(filtered[name], errors="coerce")
			if maximum_distance is not None:
				filtered = filtered[distances.le(maximum_distance)]
		for name in ("parking", "gym", "lift", "security"):
			choice = selected.get(name, "Any")
			if choice in {"Yes", "No"} and name in filtered.columns:
				filtered = filtered[_filter_amenity(filtered[name], choice)]
		st.session_state["property_search_results"] = filtered.copy()

	if "property_search_results" not in st.session_state:
		return
	filtered = st.session_state["property_search_results"]

	st.caption(f"Showing {len(filtered):,} of {len(data):,} properties")
	if filtered.empty:
		st.info("No properties found. Try changing your filters.")
		return
	render_table(filtered, "property-search-results", height=520)
