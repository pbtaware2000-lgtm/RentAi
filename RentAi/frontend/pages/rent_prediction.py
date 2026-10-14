"""Rent prediction form backed by the production regression service."""

import streamlit as st

from backend.services.prediction_service import predict_rent
from frontend.components.data_utils import category_options, page_heading


def _category_input(data, column: str, label: str, key: str | None = None):
	options = category_options(data, column)
	widget_key = key or f"prediction-{column}"
	if options:
		return st.selectbox(label, options, key=widget_key)
	return st.text_input(label, key=widget_key, disabled=column in data.columns)


def _locality_options(data, city: str | None) -> list[str]:
	if data.empty or "city" not in data.columns or "locality" not in data.columns or not city:
		return []
	localities = data.loc[data["city"].astype(str) == str(city), "locality"]
	return sorted({value.strip() for value in localities.dropna().astype(str) if value.strip()})


def _reset_locality_if_invalid(data) -> None:
	city = st.session_state.get("prediction-city")
	options = _locality_options(data, city)
	selected = st.session_state.get("prediction-locality")
	if selected not in options:
		st.session_state["prediction-locality"] = options[0] if options else ""


def _valid_locality_pair(data, city: str, locality: str) -> bool:
	if data.empty or "city" not in data.columns or "locality" not in data.columns:
		return True
	rows = data.loc[data["city"].astype(str) == str(city), "locality"]
	options = {value.strip() for value in rows.dropna().astype(str) if value.strip()}
	return not options or locality in options


def render(data) -> None:
	page_heading("Rent Prediction", "Enter property details to prepare a rental estimate request.")
	if "prediction_result" not in st.session_state:
		st.session_state.prediction_result = None
	if "prediction_error" not in st.session_state:
		st.session_state.prediction_error = None

	with st.container(border=True):
		st.subheader("Property details")
		city_col, locality_col = st.columns(2)
		with city_col:
			city = _category_input(data, "city", "City", key="prediction-city")
		with locality_col:
			locality_options = _locality_options(data, city)
			if locality_options:
				if st.session_state.get("prediction-locality") not in locality_options:
					st.session_state["prediction-locality"] = locality_options[0]
				locality = st.selectbox("Locality", locality_options, key="prediction-locality")
			else:
				locality = st.text_input("Locality", key="prediction-locality")

		type_col, furnishing_col, area_col = st.columns(3)
		with type_col:
			property_type = _category_input(data, "property_type", "Property type")
		with furnishing_col:
			furnishing_status = _category_input(data, "furnishing_status", "Furnishing status")
		with area_col:
			area_sqft = st.number_input("Area (sq ft)", min_value=0.0, value=0.0, step=50.0, key="prediction-area-sqft")

		bedroom_col, bathroom_col, floor_col = st.columns(3)
		with bedroom_col:
			bedrooms = st.number_input("Bedrooms", min_value=0, value=0, step=1, key="prediction-bedrooms")
		with bathroom_col:
			bathrooms = st.number_input("Bathrooms", min_value=0, value=0, step=1, key="prediction-bathrooms")
		with floor_col:
			floor = st.number_input("Floor", min_value=0, value=0, step=1, key="prediction-floor")

		distance_col, age_col, parking_col = st.columns(3)
		with distance_col:
			distance = st.number_input("Distance to city center (km)", min_value=0.0, value=0.0, step=0.5, key="prediction-distance")
		with age_col:
			age = st.number_input("Property age (years)", min_value=0, value=0, step=1, key="prediction-age")
		with parking_col:
			parking_value = _category_input(data, "parking", "Parking", key="prediction-parking")

		amenity_columns = [name for name in ("lift", "gym", "security", "power_backup", "swimming_pool", "air_conditioning", "gated_community") if name in data.columns]
		amenities = st.multiselect("Amenities", amenity_columns, key="prediction-amenities") if amenity_columns else []
		if amenity_columns:
			st.caption("Amenities are shown for listing context; the current regression model does not use them.")
		submitted = st.button("Predict Rent", type="primary", width="stretch")

	if submitted:
		validation_error = None
		if area_sqft <= 0:
			validation_error = "Area must be greater than zero."
		elif not city or not str(city).strip():
			validation_error = "Select or enter a city."
		elif not locality or not str(locality).strip():
			validation_error = "Select or enter a locality."
		elif not _valid_locality_pair(data, str(city), str(locality)):
			validation_error = "The selected locality is not available for the selected city."
		elif not property_type or not str(property_type).strip():
			validation_error = "Select or enter a property type."
		elif not furnishing_status or not str(furnishing_status).strip():
			validation_error = "Select or enter a furnishing status."
		else:
			try:
				parking = int(parking_value)
			except (TypeError, ValueError):
				validation_error = "Parking must be a non-negative whole number."
			else:
				try:
					features = {
						"city": str(city).strip(),
						"locality": str(locality).strip(),
						"property_type": str(property_type).strip(),
						"furnishing_status": str(furnishing_status).strip(),
						"area_sqft": float(area_sqft),
						"bedrooms": int(bedrooms),
						"bathrooms": int(bathrooms),
						"floor": int(floor),
						"distance_to_city_center_km": float(distance),
						"age_of_property": int(age),
						"parking": parking,
					}
					with st.spinner("Estimating monthly rent..."):
						predicted_rent = predict_rent(features)
					st.session_state.prediction_result = {
						"predicted_rent": predicted_rent,
						"features": features,
						"amenities": amenities,
					}
					st.session_state.prediction_error = None
				except (FileNotFoundError, ValueError) as exc:
					st.session_state.prediction_error = f"Could not predict rent: {exc}"
					st.session_state.prediction_result = None
				except Exception as exc:
					st.session_state.prediction_error = f"Rent prediction failed unexpectedly: {exc}"
					st.session_state.prediction_result = None
		if validation_error:
			st.session_state.prediction_error = validation_error
			st.session_state.prediction_result = None

	if st.session_state.prediction_error:
		st.error(st.session_state.prediction_error)

	result = st.session_state.prediction_result
	if result:
		st.success("The regression model produced a rent estimate for this listing.")
		with st.container(border=True):
			st.subheader("Predicted Monthly Rent")
			st.metric("Model estimate", f"₹{result['predicted_rent']:,.0f} INR")
			st.caption("This is the model's output for the supplied features, not a guarantee of the real-world market rent.")
			st.markdown("**Submitted property details**")
			features = result["features"]
			detail_rows = [
				("City", features["city"]),
				("Locality", features["locality"]),
				("Property type", features["property_type"]),
				("Furnishing", features["furnishing_status"]),
				("Area", f"{features['area_sqft']:,.0f} sq ft"),
				("Bedrooms", features["bedrooms"]),
				("Bathrooms", features["bathrooms"]),
				("Floor", features["floor"]),
				("Distance to center", f"{features['distance_to_city_center_km']:,.1f} km"),
				("Property age", f"{features['age_of_property']} years"),
				("Parking", features["parking"]),
				("Amenities (not used by model)", ", ".join(result["amenities"]) or "None selected"),
			]
			for start in range(0, len(detail_rows), 3):
				columns = st.columns(3)
				for column, (label, value) in zip(columns, detail_rows[start:start + 3]):
					column.markdown(f"**{label}**")
					column.write(str(value))
