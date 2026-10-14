"""Run K-Means clustering and explore the resulting property groups."""

import pandas as pd
import plotly.express as px
import streamlit as st

from frontend.components.charts import render_chart
from frontend.components.data_utils import page_heading
from ml.clustering.dbscan import cluster_properties as dbscan_properties
from ml.clustering.kmeans import cluster_properties
from ml.clustering.pca import explore_pca


def _render_cluster_visualization(
	assignments: pd.DataFrame,
	features: list[str],
	scaled_features: pd.DataFrame,
	method: str,
) -> None:
	chart_data = scaled_features.copy()
	for name in ("property_id", "city", "locality"):
		if name in assignments.columns:
			chart_data[name] = assignments[name].to_numpy()
	chart_data["cluster"] = assignments["cluster"].to_numpy()
	if method == "DBSCAN":
		chart_data["cluster_label"] = chart_data["cluster"].map(
			lambda label: "Noise" if int(label) == -1 else f"Cluster {int(label)}"
		)
	else:
		chart_data["cluster_label"] = chart_data["cluster"].astype(str)
	if len(features) >= 2:
		figure = px.scatter(
			chart_data,
			x=features[0],
			y=features[1],
			color="cluster_label",
			hover_data=[name for name in ("property_id", "city", "locality") if name in chart_data.columns],
			labels={
				"cluster_label": "Cluster",
				features[0]: f"{features[0]} (standardized)",
				features[1]: f"{features[1]} (standardized)",
			},
			title=f"Property clusters: {features[0]} vs {features[1]}",
			opacity=0.65,
		)
	else:
		counts = chart_data["cluster_label"].value_counts().sort_index()
		figure = px.bar(
			x=counts.index,
			y=counts.values,
			labels={"x": "Cluster", "y": "Properties"},
			title="Properties assigned to each cluster",
		)
	render_chart(figure, "property-clustering-visualization", height=440)


def _render_pca_visualization(coordinates: pd.DataFrame, data: pd.DataFrame) -> None:
	chart_data = coordinates.copy()
	for name in ("property_id", "city", "locality"):
		if name in data.columns:
			chart_data[name] = data[name].to_numpy()
	figure = px.scatter(
		chart_data,
		x="PC1",
		y="PC2",
		hover_data=[name for name in ("property_id", "city", "locality") if name in chart_data.columns],
		labels={"PC1": "Principal component 1", "PC2": "Principal component 2"},
		title="Properties in the first two principal components",
		opacity=0.65,
	)
	render_chart(figure, "property-pca-visualization", height=440)


def _clear_similar_property_search() -> None:
	for key in ("similar_property_match_ids", "similar_property_search_config"):
		st.session_state.pop(key, None)
	st.session_state["similar_property_search_revision"] = (
		st.session_state.get("similar_property_search_revision", 0) + 1
	)


def _render_similar_properties(assignments: pd.DataFrame, data: pd.DataFrame, method: str) -> None:
	with st.container(border=True):
		st.subheader("Similar properties")
		required_columns = {"property_id", "city", "locality", "property_type", "bedrooms"}
		missing_columns = required_columns.difference(data.columns)
		if missing_columns:
			st.info(f"Property search is unavailable because the dataset is missing: {', '.join(sorted(missing_columns))}.")
			return

		cities = sorted(data["city"].dropna().astype(str).str.strip().unique().tolist())
		city = st.selectbox("City", ["Select city", *cities], key="similar-property-city")
		localities = []
		if city != "Select city":
			localities = sorted(
				data.loc[data["city"].astype(str).str.strip() == city, "locality"]
				.dropna()
				.astype(str)
				.str.strip()
				.unique()
				.tolist()
			)
		locality = st.selectbox(
			"Locality",
			["Select locality", *localities],
			key=f"similar-property-locality-{city}",
			disabled=not localities,
		)

		property_types = sorted(data["property_type"].dropna().astype(str).str.strip().unique().tolist())
		bedroom_values = pd.to_numeric(data["bedrooms"], errors="coerce").dropna().unique().tolist()
		bedrooms = sorted(float(value) for value in bedroom_values)
		filter_columns = st.columns(2)
		with filter_columns[0]:
			property_type = st.selectbox("Property Type", ["All", *property_types], key="similar-property-type")
		with filter_columns[1]:
			bedroom_options = ["All", *[format(value, "g") for value in bedrooms]]
			bedroom = st.selectbox("Bedrooms", bedroom_options, key="similar-property-bedrooms")

		search_config = (city, locality, property_type, bedroom)
		search_clicked = st.button("Search properties", key="similar-property-search", type="primary")
		if search_clicked:
			st.session_state.pop("similar_property_match_ids", None)
			st.session_state.pop("similar_property_search_config", None)
			revision = st.session_state.get("similar_property_search_revision", 0) + 1
			st.session_state["similar_property_search_revision"] = revision
			if city == "Select city" or locality == "Select locality":
				st.warning("Select a city and locality to search for properties.")
			else:
				matches = data.dropna(subset=["property_id"]).copy()
				matches = matches[matches["city"].astype(str).str.strip() == city]
				matches = matches[matches["locality"].astype(str).str.strip() == locality]
				if property_type != "All":
					matches = matches[matches["property_type"].astype(str).str.strip() == property_type]
				if bedroom != "All":
					matches = matches[pd.to_numeric(matches["bedrooms"], errors="coerce").eq(float(bedroom))]
				st.session_state["similar_property_match_ids"] = matches["property_id"].astype(str).tolist()
				st.session_state["similar_property_search_config"] = search_config

		if st.session_state.get("similar_property_search_config") != search_config:
			return

		match_ids = st.session_state.get("similar_property_match_ids", [])
		if not match_ids:
			st.info("No properties match those filters. Try changing your selection.")
			return

		matches = data[data["property_id"].astype(str).isin(match_ids)].copy()
		st.caption(f"{len(matches):,} matching properties")
		display_names = {
			"property_id": "Property ID",
			"city": "City",
			"locality": "Locality",
			"property_type": "Property Type",
			"bedrooms": "Bedrooms",
			"area_sqft": "Area",
			"rent": "Rent",
		}
		result_columns = [name for name in display_names if name in matches.columns]
		st.dataframe(
			matches[result_columns].rename(columns=display_names),
			width="stretch",
			hide_index=True,
			height=min(360, 38 + 35 * len(matches)),
		)
		if "area_sqft" not in matches.columns or "rent" not in matches.columns:
			st.info("Area or rent is missing from the dataset, so those values cannot be shown in the results table.")

		match_ids = matches["property_id"].astype(str).tolist()
		details = {str(row["property_id"]): row for _, row in matches.iterrows()}
		revision = st.session_state["similar_property_search_revision"]
		selected_property_id = st.selectbox(
			"Select a property",
			match_ids,
			index=None,
			placeholder="Choose a property from the matching results",
			format_func=lambda identifier: (
				f"{identifier} · {details[identifier]['city']}, {details[identifier]['locality']} · "
				f"{details[identifier]['property_type']} · {details[identifier]['bedrooms']} bedrooms"
			),
			key=f"similar-selected-property-{revision}",
		)
		if selected_property_id is None:
			return

		if "cluster" not in assignments.columns or "property_id" not in assignments.columns:
			st.info("Cluster assignments are unavailable for these properties. Run K-Means again.")
			return
		selected_rows = assignments[assignments["property_id"].astype(str) == selected_property_id]
		if selected_rows.empty:
			st.info("The selected property has no cluster assignment. Run K-Means again.")
			return

		reference = selected_rows.iloc[0]
		cluster = reference["cluster"]
		if method == "DBSCAN" and int(cluster) == -1:
			st.info("This property is DBSCAN noise, so it does not belong to a meaningful cluster and has no cluster recommendations.")
			return
		same_cluster = assignments[
			(assignments["cluster"] == cluster)
			& (assignments["property_id"].astype(str) != selected_property_id)
		].copy()

		attribute_columns = ("city", "locality", "property_type")
		attributes_available = all(name in same_cluster.columns for name in attribute_columns) and "bedrooms" in same_cluster.columns
		if not attributes_available or any(pd.isna(reference.get(name)) for name in (*attribute_columns, "bedrooms")):
			same_cluster = same_cluster.iloc[0:0]
		else:
			for name in attribute_columns:
				same_cluster = same_cluster[same_cluster[name].eq(reference[name])]
			reference_bedrooms = pd.to_numeric(pd.Series([reference["bedrooms"]]), errors="coerce").iloc[0]
			candidate_bedrooms = pd.to_numeric(same_cluster["bedrooms"], errors="coerce")
			same_cluster = same_cluster[candidate_bedrooms.eq(reference_bedrooms)]

		st.caption(f"Cluster {int(cluster)} | {len(same_cluster):,} matching properties")
		if same_cluster.empty:
			st.info("No other properties in this cluster match the selected property's city, locality, property type, and bedroom count.")
			return

		for column, reference_value in (("area_sqft", reference.get("area_sqft")), ("rent", reference.get("rent"))):
			reference_numeric = pd.to_numeric(pd.Series([reference_value]), errors="coerce").iloc[0]
			if column not in same_cluster.columns or pd.isna(reference_numeric):
				same_cluster[f"_{column}_difference"] = float("inf")
			else:
				candidate_values = pd.to_numeric(same_cluster[column], errors="coerce")
				same_cluster[f"_{column}_difference"] = (
					(candidate_values - reference_numeric).abs() / max(abs(reference_numeric), 1.0)
				).fillna(float("inf"))

		ranked = same_cluster.sort_values(
			by=["_area_sqft_difference", "_rent_difference"],
			ascending=[True, True],
			kind="stable",
		)
		if len(ranked) < 5:
			st.info(f"Only {len(ranked)} matching properties are available; showing all of them.")
		ranked = ranked.head(5)
		similar_columns = [
			name
			for name in ("property_id", "city", "locality", "property_type", "bedrooms", "area_sqft", "rent")
			if name in ranked.columns
		]
		st.dataframe(
			ranked[similar_columns].rename(columns=display_names),
			width="stretch",
			hide_index=True,
			height=320,
		)
def render(data: pd.DataFrame) -> None:
	page_heading("Property Clustering", "Explore principal components or group listings using selected numeric property features.")
	if data.empty:
		st.info("Add property rows to the CSV to run clustering.")
		return

	numeric_features = [
		column
		for column in data.select_dtypes(include="number").columns
		if column != "cluster_id"
	]
	suggested = [
		column
		for column in ("area_sqft", "bedrooms", "bathrooms", "rent", "distance_to_city_center_km")
		if column in numeric_features
	]
	control_col, preview_col = st.columns([1, 1.5])
	with control_col:
		with st.container(border=True):
			st.subheader("Clustering setup")
			features = st.multiselect(
				"Numeric features",
				numeric_features,
				default=suggested,
				help="Only numeric fields present in the CSV are offered.",
			)
			method = st.selectbox("Method", ["K-Means", "DBSCAN", "PCA exploration"])
			cluster_count = eps = min_samples = None
			if method == "K-Means":
				cluster_count = st.number_input(
					"Number of clusters", min_value=2,
					max_value=max(2, min(20, len(data))),
					value=min(4, max(2, len(data))), step=1,
				)
			elif method == "DBSCAN":
				eps = st.number_input(
					"eps", min_value=0.01, value=0.8, step=0.1, format="%.2f",
					help="Maximum standardized Euclidean distance for neighboring properties.",
				)
				min_samples = st.number_input(
					"min_samples", min_value=1, max_value=max(1, len(data)),
					value=min(10, len(data)), step=1,
					help="Minimum points, including the property itself, needed to form a dense region.",
				)
			run_clicked = st.button("Prepare clustering", type="primary", width="stretch", disabled=len(data) < 2)

	if run_clicked:
		_clear_similar_property_search()
		st.session_state.pop("property_clustering_assignments", None)
		st.session_state.pop("property_clustering_features", None)
		st.session_state.pop("property_clustering_scaled_features", None)
		st.session_state.pop("property_clustering_config", None)
		st.session_state.pop("property_pca_coordinates", None)
		st.session_state.pop("property_pca_variance", None)
		st.session_state.pop("property_pca_config", None)
		if not features:
			st.warning("Select at least one numeric feature that exists in the dataset.")
		elif method == "PCA exploration" and len(features) < 2:
			st.warning("PCA needs at least two selected numeric features to produce PC1 and PC2.")
		elif method == "K-Means" and not 2 <= int(cluster_count) <= len(data):
			st.warning("Choose a cluster count between 2 and the number of properties.")
		else:
			try:
				operation = "PCA exploration" if method == "PCA exploration" else f"{method} clustering"
				with st.spinner(f"Running {operation}..."):
					if method == "PCA exploration":
						coordinates, variance = explore_pca(data, features)
						config = (method, tuple(features))
					elif method == "K-Means":
						assignments, scaled = cluster_properties(data, features, int(cluster_count))
						config = (method, tuple(features), int(cluster_count))
					else:
						assignments, scaled = dbscan_properties(data, features, float(eps), int(min_samples))
						config = (method, tuple(features), float(eps), int(min_samples))
			except (TypeError, ValueError) as exc:
				st.warning(f"{method} could not run: {exc}")
			else:
				if method == "PCA exploration":
					st.session_state.pop("property_clustering_assignments", None)
					st.session_state.pop("property_clustering_features", None)
					st.session_state.pop("property_clustering_scaled_features", None)
					st.session_state.pop("property_clustering_config", None)
					st.session_state["property_pca_coordinates"] = pd.DataFrame(coordinates, columns=["PC1", "PC2"])
					st.session_state["property_pca_variance"] = variance
					st.session_state["property_pca_config"] = config
					st.success("PCA exploration completed successfully.")
				else:
					st.session_state.pop("property_pca_coordinates", None)
					st.session_state.pop("property_pca_variance", None)
					st.session_state.pop("property_pca_config", None)
					st.session_state["property_clustering_assignments"] = assignments
					st.session_state["property_clustering_features"] = list(features)
					st.session_state["property_clustering_scaled_features"] = pd.DataFrame(scaled, columns=features)
					st.session_state["property_clustering_config"] = config
					st.success(f"{method} clustering completed successfully.")

	assignments = st.session_state.get("property_clustering_assignments")
	cluster_features = st.session_state.get("property_clustering_features")
	scaled_features = st.session_state.get("property_clustering_scaled_features")
	pca_coordinates = st.session_state.get("property_pca_coordinates")
	pca_variance = st.session_state.get("property_pca_variance")
	current_config = (
		(method, tuple(features), int(cluster_count)) if method == "K-Means"
		else (method, tuple(features), float(eps), int(min_samples)) if method == "DBSCAN"
		else (method, tuple(features))
	)
	pca_config = st.session_state.get("property_pca_config")
	if pca_coordinates is not None and pca_config != current_config:
		st.session_state.pop("property_pca_coordinates", None)
		st.session_state.pop("property_pca_variance", None)
		st.session_state.pop("property_pca_config", None)
		pca_coordinates = None
		pca_variance = None
	result_config = st.session_state.get("property_clustering_config")
	if assignments is not None and result_config != current_config:
		_clear_similar_property_search()
		st.session_state.pop("property_clustering_assignments", None)
		st.session_state.pop("property_clustering_features", None)
		st.session_state.pop("property_clustering_scaled_features", None)
		st.session_state.pop("property_clustering_config", None)
		assignments = None
		cluster_features = None
		scaled_features = None

	with preview_col:
		with st.container(border=True):
			st.subheader("Clustering visualization")
			if method == "PCA exploration":
				if pca_coordinates is None:
					st.caption(f"{len(data):,} rows available · {len(numeric_features)} numeric feature(s)")
					st.info("Select at least two features and click Prepare clustering to explore the principal components.")
				else:
					st.caption(f"{len(pca_coordinates):,} properties · PCA features: {', '.join(features)}")
					st.info("PCA is dimensionality reduction, not a clustering algorithm. It does not assign cluster labels.")
					variance_columns = st.columns(3)
					with variance_columns[0]:
						st.metric("PC1 explained variance", f"{pca_variance[0]:.2f}%")
					with variance_columns[1]:
						st.metric("PC2 explained variance", f"{pca_variance[1]:.2f}%")
					with variance_columns[2]:
						st.metric("PC1 + PC2", f"{pca_variance.sum():.2f}%")
					_render_pca_visualization(pca_coordinates, data)
			elif assignments is None:
				st.caption(f"{len(data):,} rows available · {len(numeric_features)} numeric feature(s)")
				st.info("Select features and click Prepare clustering to view the cluster analysis.")
			else:
				labels = assignments["cluster"]
				cluster_count_actual = int(labels[labels >= 0].nunique()) if method == "DBSCAN" else int(labels.nunique())
				noise_count = int(labels.eq(-1).sum()) if method == "DBSCAN" else 0
				st.caption(
					f"{len(assignments):,} properties · {cluster_count_actual} clusters · "
					f"Features: {', '.join(cluster_features)}"
				)
				if method == "DBSCAN" and cluster_count_actual == 0:
					st.info(f"DBSCAN discovered no clusters; all {noise_count:,} properties ({noise_count / len(assignments) * 100:.2f}%) are classified as noise.")
				elif method == "DBSCAN" and noise_count:
					st.info(f"DBSCAN classified {noise_count:,} properties ({noise_count / len(assignments) * 100:.2f}%) as noise.")
				if len(cluster_features) > 2:
					st.caption("The graph uses the first two selected standardized features as a 2D view; clustering uses all selected features.")
				_render_cluster_visualization(assignments, cluster_features, scaled_features, method)

	if method == "PCA exploration":
		st.info("PCA does not produce clusters or Similar Properties recommendations. Select K-Means or DBSCAN to use those features.")
		return

	if assignments is None:
		with st.container(border=True):
			st.subheader("Similar properties")
			st.info("Run a clustering method to find properties assigned to the same cluster.")
		return

	with st.container(border=True):
		st.subheader("Cluster assignments")
		st.dataframe(
			assignments[[column for column in ("property_id", "cluster") if column in assignments.columns]],
			width="stretch",
			hide_index=True,
			height=360,
		)
		cluster_sizes = (
			assignments["cluster"]
			.value_counts()
			.sort_index()
			.rename_axis("Cluster")
			.rename("Properties")
			.to_frame()
		)
		cluster_sizes["Percentage"] = cluster_sizes["Properties"].div(len(assignments)).mul(100)
		if method == "DBSCAN":
			cluster_sizes.index = ["Noise" if int(value) == -1 else f"Cluster {int(value)}" for value in cluster_sizes.index]
		st.subheader("Cluster distribution")
		st.dataframe(
			cluster_sizes.reset_index().style.format({"Percentage": "{:.2f}%"}),
			width="stretch",
			hide_index=True,
		)

	_render_similar_properties(assignments, data, method)
