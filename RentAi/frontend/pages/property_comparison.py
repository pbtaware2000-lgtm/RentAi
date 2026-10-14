"""Compare properties selected from the loaded CSV."""

import pandas as pd
import streamlit as st

from frontend.components.data_utils import format_currency, page_heading, property_label


COMPARISON_FIELDS = {
	"City": "city",
	"Locality": "locality",
	"Property type": "property_type",
	"Area (sq ft)": "area_sqft",
	"Bedrooms": "bedrooms",
	"Bathrooms": "bathrooms",
	"Furnishing": "furnishing_status",
	"Parking": "parking",
	"Rent": "rent",
	"Security deposit": "security_deposit",
	"Maintenance": "maintenance",
	"Distance to city center (km)": "distance_to_city_center_km",
	"Distance to metro (km)": "distance_to_metro_km",
	"Distance to school (km)": "distance_to_school_km",
	"Distance to hospital (km)": "distance_to_hospital_km",
	"Distance to IT park (km)": "distance_to_it_park_km",
}


def render(data: pd.DataFrame) -> None:
	page_heading("Property Comparison", "Select listings from the CSV and compare their available attributes.")
	if data.empty:
		st.info("No property records are available to compare.")
		return
	if "city" not in data.columns or "locality" not in data.columns:
		st.info("City and locality columns are required to compare properties from the same location.")
		return

	city_options = sorted(data["city"].dropna().astype(str).unique().tolist())
	if not city_options:
		st.info("No properties have a city value to filter by.")
		return
	filter_columns = st.columns(2)
	with filter_columns[0]:
		city = st.selectbox("City", city_options, key="comparison-city")
	city_rows = data.loc[data["city"].astype(str) == city]
	locality_options = sorted(city_rows["locality"].dropna().astype(str).unique().tolist())
	if not locality_options:
		st.info("No properties have a locality for the selected city.")
		return
	with filter_columns[1]:
		current_locality = st.session_state.get("comparison-locality")
		if current_locality not in locality_options:
			st.session_state["comparison-locality"] = locality_options[0]
		locality = st.selectbox("Locality", locality_options, key="comparison-locality")

	filtered_data = data.loc[
		(data["city"].astype(str) == city)
		& (data["locality"].astype(str) == locality)
	]
	valid_indices = set(filtered_data.index)
	selected_state = st.session_state.get("comparison-properties", [])
	st.session_state["comparison-properties"] = [index for index in selected_state if index in valid_indices]
	if len(filtered_data) < 2:
		st.info(f"Only {len(filtered_data)} matching property is available for {city} / {locality}; at least two are required to compare.")
		return

	indices = filtered_data.index.tolist()
	labels = {index: property_label(row, index) for index, row in data.iterrows()}
	selected = st.multiselect("Properties", indices, format_func=lambda index: labels[index], max_selections=4, key="comparison-properties")
	if len(selected) < 2:
		st.info("Select at least two properties to see a side-by-side comparison.")
		return

	selected_rows = data.loc[selected]
	selected_ids = set(selected_rows["property_id"].astype(str)) if "property_id" in selected_rows.columns else set(map(str, selected))
	valid_ids = set(filtered_data["property_id"].astype(str)) if "property_id" in filtered_data.columns else set(map(str, filtered_data.index))
	if not selected_ids.issubset(valid_ids) or not (
		(selected_rows["city"].astype(str) == city)
		& (selected_rows["locality"].astype(str) == locality)
	).all():
		st.error("All selected properties must belong to the selected city and locality.")
		return

	fields = [(label, column) for label, column in COMPARISON_FIELDS.items() if column in data.columns]
	amenity_fields = [column for column in ("balcony", "lift", "gym", "security", "power_backup", "swimming_pool", "air_conditioning", "water_supply", "gated_community") if column in data.columns]
	if amenity_fields:
		fields.append(("Amenities", "__amenities__"))
	rows = []
	for label, column in fields:
		values = []
		for index in selected:
			row = data.loc[index]
			if column == "__amenities__":
				enabled = [name.replace("_", " ").title() for name in amenity_fields if str(row[name]).strip().casefold() in {"yes", "true", "1", "available"}]
				value = ", ".join(enabled) if enabled else "None listed"
			else:
				value = row[column]
				if pd.isna(value):
					value = "-"
				elif column in {"rent", "security_deposit", "maintenance"}:
					value = format_currency(value)
				elif column == "area_sqft":
					value = f"{value:,.0f} sq ft" if isinstance(value, (int, float)) else value
			values.append(str(value))
		rows.append({"Attribute": label, **{labels[index]: value for index, value in zip(selected, values)}})
	st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True, height=620)
