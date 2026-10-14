"""Rent classification page with production predictions and model context."""

import plotly.graph_objects as go
import streamlit as st

from backend.services.classification_service import classify_rent
from frontend.components.charts import render_chart
from frontend.components.data_utils import category_options, page_heading
from ml.classification.evaluate_model import get_evaluation_results


CATEGORIES = ("Low", "Medium", "High", "Premium")
CATEGORY_COLORS = ("#059669", "#60a5fa", "#fbbf24", "#ff6b1a")


def _locality_options(data, city: str | None) -> list[str]:
	if data.empty or "locality" not in data.columns or "city" not in data.columns or not city:
		return []
	localities = data.loc[data["city"].astype(str) == str(city), "locality"]
	return sorted({value.strip() for value in localities.dropna().astype(str) if value.strip()})


def _reset_locality_if_invalid(data) -> None:
	city = st.session_state.get("classification-city")
	options = _locality_options(data, city)
	selected = st.session_state.get("classification-locality")
	if selected not in options:
		st.session_state["classification-locality"] = options[0] if options else ""


def _category_distribution(data) -> None:
	st.subheader("Category Distribution")
	if data.empty or "rent_category" not in data.columns:
		st.info("Category distribution is unavailable because the dataset has no rent_category column or records.")
		return

	category_values = data["rent_category"].dropna().astype(str)
	if category_values.empty:
		st.info("No non-missing rent categories are available to chart.")
		return

	counts = category_values.value_counts()
	known_counts = [int(counts.get(category, 0)) for category in CATEGORIES]
	other_count = int(counts[~counts.index.isin(CATEGORIES)].sum())
	chart_categories = list(CATEGORIES)
	chart_counts = known_counts
	chart_colors = list(CATEGORY_COLORS)
	if other_count:
		chart_categories.append("Other")
		chart_counts.append(other_count)
		chart_colors.append("#94a3b8")

	total = int(category_values.size)
	percentages = [count / total * 100 for count in chart_counts]
	if data["rent_category"].isna().any():
		st.caption(f"{int(data['rent_category'].isna().sum()):,} rows with missing categories are excluded from percentages.")

	chart_col, table_col = st.columns([1.5, 1])
	with chart_col:
		figure = go.Figure(go.Bar(
			x=chart_categories,
			y=chart_counts,
			marker_color=chart_colors,
			text=[f"{count:,} ({percent:.1f}%)" for count, percent in zip(chart_counts, percentages)],
			textposition="outside",
			customdata=percentages,
			hovertemplate="%{x}: %{y:,} properties (%{customdata:.1f}%)<extra></extra>",
		))
		figure.update_layout(showlegend=False, xaxis_title="Rent Category", yaxis_title="Properties", yaxis_rangemode="tozero")
		render_chart(figure, "rent-category-distribution", height=330)
	with table_col:
		st.markdown("**Properties by category**")
		st.dataframe(
			{
				"Category": chart_categories,
				"Properties": chart_counts,
				"Share": [f"{percent:.1f}%" for percent in percentages],
			},
			hide_index=True,
			width="stretch",
		)
		st.caption(f"Percentages use {total:,} rows with a recorded category.")


def _model_performance() -> None:
	st.subheader("Model Performance")
	with st.spinner("Loading held-out evaluation results..."):
		results = get_evaluation_results()
	metric_values = (
		("Accuracy", results["accuracy"]),
		("Macro Precision", results["precision_macro"]),
		("Macro Recall", results["recall_macro"]),
		("Macro F1-score", results["f1_macro"]),
	)
	metric_columns = st.columns(4)
	for column, (label, value) in zip(metric_columns, metric_values):
		column.metric(label, f"{value:.2%}")
	st.caption(f"Measured on {results['test_records']:,} held-out test records. The RentAI dataset is synthetic; these metrics describe this evaluation set and do not guarantee real-world market accuracy.")

	matrix = go.Figure(go.Heatmap(
		z=results["confusion_matrix"],
		x=results["labels"],
		y=results["labels"],
		text=results["confusion_matrix"],
		texttemplate="%{text}",
		colorscale=[[0, "#fff1e8"], [1, "#ff6b1a"]],
		showscale=False,
		hovertemplate="Actual: %{y}<br>Predicted: %{x}<br>Records: %{z}<extra></extra>",
	))
	matrix.update_layout(xaxis_title="Predicted Category", yaxis_title="Actual Category", yaxis_autorange="reversed", height=350)
	render_chart(matrix, "rent-classification-confusion-matrix", height=350)
	st.caption(f"Confusion matrix values: rows are Actual Category; columns are Predicted Category. Label order on both axes: {', '.join(results['labels'])}.")


def render(data) -> None:
	page_heading("Rent Classification", "Prepare a listing for rent-band classification.")
	if "classification_result" not in st.session_state:
		st.session_state.classification_result = None
	if "classification_error" not in st.session_state:
		st.session_state.classification_error = None

	with st.container(border=True):
		st.subheader("Category Prediction")
		left, right = st.columns(2)
		with left:
			rent = st.number_input("Monthly rent", min_value=0.0, value=0.0, step=500.0, key="classification-rent")
			type_options = category_options(data, "property_type")
			if type_options:
				property_type = st.selectbox("Property type", type_options, key="classification-property-type")
			else:
				property_type = st.text_input("Property type", key="classification-property-type")
		with right:
			area_sqft = st.number_input("Area (sq ft)", min_value=1, value=1, step=50, key="classification-area-sqft")
			city_options = category_options(data, "city")
			if city_options:
				city = st.selectbox(
					"City", city_options, key="classification-city",
					on_change=_reset_locality_if_invalid, args=(data,),
				)
			else:
				city = st.text_input(
					"City", key="classification-city",
					on_change=_reset_locality_if_invalid, args=(data,),
				)
			locality_options = _locality_options(data, city)
			if locality_options:
				if st.session_state.get("classification-locality") not in locality_options:
					st.session_state["classification-locality"] = locality_options[0]
				locality = st.selectbox("Locality", locality_options, key="classification-locality")
			else:
				locality = None
				st.caption("No locality is available for the selected city; classification will use the other inputs.")
		submitted = st.button("Classify Rent", type="primary", width="stretch")

	if submitted:
		if not property_type or not city:
			st.session_state.classification_error = "Enter both a city and a property type to classify this listing."
			st.session_state.classification_result = None
		else:
			try:
				with st.spinner("Classifying listing..."):
					category = classify_rent({
						"rent": rent,
						"area_sqft": area_sqft,
						"city": city,
						"property_type": property_type,
						"locality": locality,
					})
				st.session_state.classification_result = {
					"category": category,
					"rent": rent,
					"area_sqft": area_sqft,
					"city": city,
					"property_type": property_type,
					"locality": locality,
				}
				st.session_state.classification_error = None
			except (FileNotFoundError, ValueError) as exc:
				st.session_state.classification_error = f"Could not classify this listing: {exc}"
				st.session_state.classification_result = None
			except Exception as exc:
				st.session_state.classification_error = f"Classification failed unexpectedly: {exc}"
				st.session_state.classification_result = None
		st.rerun()

	if st.session_state.classification_error:
		st.error(st.session_state.classification_error)

	result = st.session_state.classification_result
	category_columns = st.columns(4)
	for column, category, color in zip(category_columns, CATEGORIES, CATEGORY_COLORS):
		with column:
			with st.container(border=True):
				st.markdown(f"<span style='color:{color};font-weight:700'>{category.upper()}</span>", unsafe_allow_html=True)
				if result and result["category"] == category:
					st.markdown("<span style='display:inline-block;background:#fff1e8;color:#c2410c;border-radius:12px;padding:3px 9px;font-size:0.75rem;font-weight:700'>PREDICTED</span>", unsafe_allow_html=True)
				else:
					st.caption("Rent category")

	if result:
		st.success(f"Predicted rent category: **{result['category']}**")
		with st.container(border=True):
			st.subheader("Result Explanation")
			detail_columns = st.columns(5)
			detail_columns[0].metric("Monthly rent", f"₹{result['rent']:,.0f}")
			detail_columns[1].metric("Area", f"{result['area_sqft']:,.0f} sq ft")
			detail_columns[2].metric("City", str(result["city"]))
			detail_columns[3].metric("Property type", str(result["property_type"]))
			detail_columns[4].metric("Locality", str(result.get("locality") or "Not provided"))
			st.write(f"The classifier placed the supplied rent in the **{result['category']}** category based on similar dataset listings.")
			st.caption("This model categorizes the rent you entered; it does not independently determine the correct market rent. Treat the category as model output, not real-world market advice.")

	_category_distribution(data)
	_model_performance()
