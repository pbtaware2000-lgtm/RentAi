"""Dataset-derived rental insights and model evaluation results."""

from pathlib import Path

import pandas as pd
import streamlit as st

from backend.services.clustering_service import find_similar_properties
from frontend.components.data_utils import numeric_column, page_heading
from ml.clustering.kmeans import cluster_properties
from ml.classification.evaluate_model import get_evaluation_results as evaluate_classification
from ml.regression.evaluate_model import evaluate_model as evaluate_regression
from ml.regression.rent_model import DATASET_PATH


FEATURE_NAMES = {
	"area_sqft": "Area (sq ft)",
	"bedrooms": "Bedrooms",
	"bathrooms": "Bathrooms",
	"balcony": "Balconies",
	"floor": "Floor",
	"total_floors": "Total floors",
	"age_of_property": "Property age (years)",
	"distance_to_city_center_km": "Distance to city center (km)",
	"distance_to_metro_km": "Distance to metro (km)",
	"distance_to_school_km": "Distance to school (km)",
	"distance_to_hospital_km": "Distance to hospital (km)",
	"distance_to_it_park_km": "Distance to IT park (km)",
	"security_deposit": "Security deposit",
	"maintenance": "Maintenance",
}

CLUSTER_FEATURES = ["area_sqft", "bedrooms", "bathrooms", "rent"]
INSIGHTS_CSS = Path(__file__).resolve().parents[1] / "styles" / "insights.css"


@st.cache_data(show_spinner=False)
def _cluster_dataset(data: pd.DataFrame) -> pd.DataFrame:
	assignments, _ = cluster_properties(data, CLUSTER_FEATURES, cluster_count=4)
	return assignments


@st.cache_data(show_spinner=False)
def _regression_results(
	dataset_signature: tuple[int, int],
	result_schema: int = 2,
) -> dict[str, float]:
	# The dataset signature invalidates results after CSV changes. The schema
	# version also expires prior cached results that lacked split record counts.
	return evaluate_regression()


def _render_rental_insights(data: pd.DataFrame) -> None:
	rent = numeric_column(data, "rent")
	if rent.empty:
		st.info("Rental insights will populate when numeric rent values are present.")
		return
	q25, median, q75 = rent.quantile([0.25, 0.5, 0.75])
	st.write(f"Observed median rent is **₹{median:,.0f}**.")
	st.caption(f"Middle half of observed rents: ₹{q25:,.0f} to ₹{q75:,.0f}.")
	if "city" in data.columns:
		city_counts = data["city"].dropna().astype(str).str.strip().replace("", pd.NA).dropna().value_counts()
		if not city_counts.empty:
			st.caption(f"Most represented city in the dataset: {city_counts.index[0]} ({city_counts.iloc[0]:,} listings).")


def _render_price_drivers(data: pd.DataFrame) -> pd.Series:
	numeric = data.select_dtypes(include="number")
	if "rent" not in numeric or numeric.shape[1] < 2:
		st.info("At least one numeric feature and rent values are needed to inspect correlations.")
		return pd.Series(dtype=float)
	correlations = numeric.corr(numeric_only=True)["rent"].drop(labels=["rent"], errors="ignore").dropna()
	if correlations.empty:
		st.info("No numeric rent correlations can be calculated from the available rows.")
		return correlations
	correlations = correlations.reindex(correlations.abs().sort_values(ascending=False).index)
	display = correlations.rename(index=lambda name: FEATURE_NAMES.get(name, name.replace("_", " ").title()))
	st.dataframe(display.rename("Correlation with rent").to_frame(), width="stretch", height=min(420, 38 + 35 * len(display)))
	st.caption("Positive values mean the feature tends to increase with rent; negative values mean it tends to decrease as rent rises. Correlation does not imply causation.")
	return correlations


def _render_descriptive_features(data: pd.DataFrame, correlations: pd.Series) -> None:
	if correlations.empty:
		st.info("Descriptive feature analysis needs numeric property fields and rent values.")
		return
	st.caption("Descriptive analysis, not model feature importance or a causal effect.")
	top = correlations.head(5)
	rows = [{
		"Feature": FEATURE_NAMES.get(name, name.replace("_", " ").title()),
		"Rent correlation": value,
		"Direction": "Positive" if value > 0 else "Negative",
	} for name, value in top.items()]
	st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True, height=235)
	for column in ("property_type", "furnishing_status", "city"):
		if column not in data.columns or "rent" not in data.columns:
			continue
		grouped = data.assign(_rent=pd.to_numeric(data["rent"], errors="coerce")).dropna(subset=[column, "_rent"])
		if grouped.empty:
			continue
		medians = grouped.groupby(column, observed=True)["_rent"].median().sort_values(ascending=False).head(5)
		st.caption(f"Median rent by {column.replace('_', ' ')} (top groups)")
		st.dataframe(medians.rename("Median rent (₹)").to_frame(), width="stretch", height=230)
		break


def _render_similar_insights(data: pd.DataFrame) -> None:
	available = [name for name in CLUSTER_FEATURES if name in data.columns]
	if len(available) != len(CLUSTER_FEATURES) or len(data) < 4:
		st.info("Cluster profiles need area, bedrooms, bathrooms, rent, and at least four listings.")
		return
	try:
		assignments = _cluster_dataset(data[CLUSTER_FEATURES + [c for c in ("property_id", "city", "locality", "property_type") if c in data.columns]])
	except (TypeError, ValueError) as exc:
		st.info(f"Similar-property groups could not be prepared from these rows: {exc}")
		return

	profiles = assignments.groupby("cluster").agg(
		Properties=("cluster", "size"),
		Median_rent=("rent", "median"),
		Median_area=("area_sqft", "median"),
		Median_bedrooms=("bedrooms", "median"),
	).rename_axis("Cluster")
	profiles.columns = ["Properties", "Median rent (₹)", "Median area (sq ft)", "Median bedrooms"]
	profiles.index = [f"Group {int(value) + 1}" for value in profiles.index]
	st.caption("Four groups from the existing K-Means implementation, using area, bedrooms, bathrooms, and rent.")
	st.dataframe(profiles, width="stretch", height=225)

	if "property_id" not in assignments.columns:
		return
	location_columns = {"city", "locality", "property_type", "property_id"}
	if not location_columns.issubset(data.columns):
		st.info("City, locality, property type, or property ID is missing, so a listing cannot be selected.")
		return
	options = data.dropna(subset=["property_id", "city", "locality"]).copy()
	for name in ("city", "locality", "property_id"):
		options[name] = options[name].astype(str).str.strip()
	options["property_type"] = options["property_type"].fillna("").astype(str).str.strip()
	options = options[options["city"].ne("") & options["locality"].ne("") & options["property_id"].ne("")]
	if options.empty:
		st.info("No listings with a property ID, city, and locality are available.")
		return

	cities = sorted(options["city"].unique().tolist())
	city = st.selectbox("City", cities, key="insights-similar-city")
	localities = sorted(options.loc[options["city"].eq(city), "locality"].unique().tolist())
	if not localities:
		st.info("No localities are available for this city.")
		return
	locality = st.selectbox("Locality", localities, key=f"insights-similar-locality-{city}")
	local_matches = options[options["city"].eq(city) & options["locality"].eq(locality)].copy()
	if local_matches.empty:
		st.info("No listings are available for this city and locality.")
		return

	def _property_label(row: pd.Series) -> str:
		return f"{row['city']} · {row['locality']} · {row['property_type'] or 'Property type unavailable'} · {row['property_id']}"

	property_ids = local_matches["property_id"].tolist()
	choice = st.selectbox(
		"Property",
		property_ids,
		format_func=lambda identifier: _property_label(
			local_matches.loc[local_matches["property_id"].eq(identifier)].iloc[0]
		),
		key=f"insights-similar-property-{city}-{locality}",
	)
	selected_rows = assignments[assignments["property_id"].astype(str).eq(str(choice))]
	if selected_rows.empty:
		st.info("The selected listing has no cluster assignment in the current grouping.")
	else:
		selected_cluster = int(selected_rows.iloc[0]["cluster"])
		st.caption(f"Selected listing belongs to Group {selected_cluster + 1} in the K-Means profiles above.")
	try:
		neighbors = find_similar_properties(choice, limit=5)
		if neighbors is None:
			st.info("The selected property could not be found by the similarity service.")
		elif not neighbors:
			st.info("No other listings are available for comparison.")
		else:
			columns = [name for name in ("property_id", "city", "locality", "property_type", "bedrooms", "area_sqft", "rent") if name in neighbors[0]]
			st.caption("Nearest listings ranked by the existing property similarity service.")
			st.dataframe(pd.DataFrame(neighbors)[columns], width="stretch", hide_index=True, height=240)
	except (TypeError, ValueError, KeyError) as exc:
		st.info(f"Nearest-listing comparison is unavailable: {exc}")


def _render_regression_results() -> None:
	regression_topics = (
		"MAE (Mean Absolute Error)",
		"RMSE (Root Mean Squared Error)",
		"R\u00b2 Score",
	)
	regression_topic = st.selectbox(
		"Regression",
		regression_topics,
		index=None,
		placeholder="Choose a regression topic",
		key="insights-regression-topic",
	)
	if regression_topic is None:
		return
	try:
		stat = Path(DATASET_PATH).stat()
		regression = _regression_results((stat.st_mtime_ns, stat.st_size), result_schema=2)
		if regression_topic == regression_topics[0]:
			st.markdown("**Mean Absolute Error (MAE)**")
			st.metric("Value", f"\u20b9{regression['mae']:,.2f}")
			st.caption("Average absolute difference between predicted rent and actual rent on the held-out test records. Lower values mean predictions are closer on average.")
		elif regression_topic == regression_topics[1]:
			st.markdown("**Root Mean Squared Error (RMSE)**")
			st.metric("Value", f"\u20b9{regression['rmse']:,.2f}")
			st.caption("Square root of the average squared prediction errors, in rupees. Lower is better; larger misses count more heavily than with MAE.")
		else:
			st.markdown("**R-squared (R\u00b2) Score**")
			st.metric("Value", f"{regression['r2']:.4f}")
			st.caption(f"Proportion of rent variation explained by the model on the held-out test set ({regression['r2']:.2%}). Values nearer 1 indicate more variation explained; this is not prediction accuracy.")
	except (OSError, ValueError, KeyError) as exc:
		st.info(f"Regression evaluation is unavailable: {exc}")


def _render_classification_results() -> None:
	classification_topics = (
		"Confusion Matrix",
		"Accuracy",
		"Precision",
		"Recall",
	)
	classification_topic = st.selectbox(
		"Classification",
		classification_topics,
		index=None,
		placeholder="Choose a classification topic",
		key="insights-classification-topic",
	)
	if classification_topic is None:
		return
	try:
		classification = evaluate_classification()
		if classification_topic == classification_topics[0]:
			st.markdown("**Confusion Matrix**")
			st.caption("Rows are actual categories; columns are predicted categories.")
			labels = ("Low", "Medium", "High", "Premium")
			label_indices = {label: index for index, label in enumerate(classification["labels"])}
			if any(label not in label_indices for label in labels):
				st.info("The held-out classification data does not contain the expected four rent categories.")
			else:
				matrix = classification["confusion_matrix"]
				indices = [label_indices[label] for label in labels]
				ordered = matrix[indices][:, indices]
				st.dataframe(pd.DataFrame(ordered, index=labels, columns=labels).rename_axis("Actual / Predicted"), width="stretch", height=250)
				st.caption(f"Each cell counts held-out listings. The diagonal shows correct classifications. Evaluated on {classification['test_records']:,} records.")
		elif classification_topic == classification_topics[1]:
			st.markdown("**Accuracy**")
			st.metric("Value", f"{classification['accuracy']:.2%}")
			st.caption("Share of held-out listings assigned the correct rent category. Higher means more predictions were correct.")
		elif classification_topic == classification_topics[2]:
			st.markdown("**Macro Precision**")
			st.metric("Value", f"{classification['precision_macro']:.2%}")
			st.caption("Among listings assigned to each category, how many were correct, averaged equally across Low, Medium, High, and Premium. Higher is better; each category has equal weight.")
		else:
			st.markdown("**Macro Recall**")
			st.metric("Value", f"{classification['recall_macro']:.2%}")
			st.caption("For each actual category, how many listings were found, averaged equally across Low, Medium, High, and Premium. Higher is better; each category has equal weight.")
	except (OSError, ValueError, KeyError) as exc:
		st.info(f"Classification evaluation is unavailable: {exc}")


def _render_model_results() -> None:
	regression_col, classification_col = st.columns(2, gap="medium")
	with regression_col:
		with st.container(border=True, key="insights-regression-card"):
			_render_regression_results()
	with classification_col:
		with st.container(border=True, key="insights-classification-card"):
			_render_classification_results()

def render(data: pd.DataFrame) -> None:
	styles = INSIGHTS_CSS.read_text(encoding="utf-8")
	st.markdown(f"<style>{styles}</style>", unsafe_allow_html=True)
	with st.container(key="insights-page"):
		page_heading("Property Insights", "Explore observed rental patterns, similar-property groups, and held-out model results.")
		insight_col, drivers_col = st.columns(2, gap="medium")
		with insight_col:
			with st.container(border=True):
				st.subheader("Rental Insights")
				_render_rental_insights(data)
		with drivers_col:
			with st.container(border=True):
				st.subheader("Price Drivers")
				correlations = _render_price_drivers(data)

		feature_col, similar_col = st.columns(2, gap="medium")
		with feature_col:
			with st.container(border=True):
				st.subheader("Important Features")
				_render_descriptive_features(data, correlations)
		with similar_col:
			with st.container(border=True):
				st.subheader("Similar Property Insights")
				_render_similar_insights(data)

		with st.container(border=True):
			st.subheader("ML Results")
			st.caption("Evaluation uses held-out test splits from the synthetic RentAI dataset; results do not guarantee real-world accuracy.")
			_render_model_results()
