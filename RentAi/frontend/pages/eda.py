"""Dataset quality checks and exploratory charts."""

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from frontend.components.charts import INDIGO, NAVY, ORANGE, style_figure
from frontend.components.data_utils import page_heading, show_empty


def _render_eda_chart(figure: go.Figure, key: str, height: int = 340,
					 rotate_x_labels: bool = False, heatmap: bool = False) -> None:
	"""Apply high-contrast Plotly styling consistently to EDA charts only."""
	axis_title_font = {"size": 16, "color": NAVY}
	tick_font = {"size": 15, "color": NAVY}
	figure = style_figure(figure, height)
	figure.update_layout(
		font={"family": "Aptos, Segoe UI, sans-serif", "size": 16, "color": NAVY},
		title_font={"size": 19, "color": NAVY},
		hoverlabel={"bgcolor": "#ffffff", "font": {"size": 15, "color": NAVY}},
		legend={"orientation": "h", "y": 1.12, "x": 0, "font": {"size": 15, "color": NAVY}},
		margin={
			"l": 130 if heatmap else 76,
			"r": 42,
			"t": 54,
			"b": 180 if heatmap else (125 if rotate_x_labels else 76),
		},
		xaxis={
			"automargin": True,
			"tickangle": -45 if rotate_x_labels else 0,
			"tickfont": tick_font,
			"title_font": axis_title_font,
			"gridcolor": "#e2e8f0",
			"zerolinecolor": "#cbd5e1",
		},
		yaxis={
			"automargin": True,
			"tickfont": tick_font,
			"title_font": axis_title_font,
			"gridcolor": "#e2e8f0",
			"zerolinecolor": "#cbd5e1",
		},
	)
	if heatmap:
		figure.update_traces(
			selector={"type": "heatmap"},
			colorbar={"tickfont": tick_font, "title": {"font": axis_title_font}},
		)
	st.plotly_chart(figure, width="stretch", key=key, config={"displayModeBar": False})


def _distribution(data: pd.DataFrame, column: str, title: str) -> None:
	if column not in data.columns:
		return
	counts = data[column].dropna().astype(str).value_counts().head(20).rename_axis(title).reset_index(name="Records")
	if not counts.empty:
		figure = px.bar(counts, x=title, y="Records", color_discrete_sequence=[INDIGO])
		figure.update_layout(xaxis_title=title, yaxis_title="Records")
		_render_eda_chart(figure, f"eda-{column}", rotate_x_labels=True)


def render(data: pd.DataFrame) -> None:
	page_heading("Dataset & EDA", "Inspect the active CSV, data quality, and observed rental distributions.")
	if data.empty:
		st.info("The dataset is empty or unavailable. Add records to `data/rentai_dataset.csv` to enable EDA.")
		return

	rows, columns, missing, duplicates = st.columns(4)
	with rows:
		st.markdown("**Rows**")
		st.metric("Rows", f"{len(data):,}")
	with columns:
		st.markdown("**Columns**")
		st.metric("Columns", f"{len(data.columns):,}")
	with missing:
		st.markdown("**Missing values**")
		st.metric("Missing values", f"{int(data.isna().sum().sum()):,}")
	with duplicates:
		st.markdown("**Duplicate records**")
		st.metric("Duplicate records", f"{int(data.duplicated().sum()):,}")

	with st.expander("Data types and missing values", expanded=True):
		schema = pd.DataFrame({"Column": data.columns, "Data type": data.dtypes.astype(str).values, "Missing": data.isna().sum().values, "Unique": data.nunique(dropna=True).values})
		st.dataframe(schema, width="stretch", hide_index=True)
	with st.expander("Statistical summary"):
		st.dataframe(data.describe(include="all").transpose().reset_index(names="Column"), width="stretch")

	st.subheader("Distributions")
	rent_col, type_col, city_col = st.columns(3)
	with rent_col:
		with st.container(border=True):
			st.markdown("**Rent distribution**")
			if "rent" not in data.columns:
				show_empty("No `rent` column is available.")
			else:
				rent = pd.to_numeric(data["rent"], errors="coerce").dropna()
				if rent.empty:
					show_empty("No numeric rent values are available.")
				else:
					figure = px.histogram(pd.DataFrame({"rent": rent}), x="rent", nbins=24, color_discrete_sequence=[ORANGE])
					figure.update_layout(xaxis_title="Monthly rent (INR)", yaxis_title="Records")
					_render_eda_chart(figure, "eda-rent", height=340)
	with type_col:
		with st.container(border=True):
			st.markdown("**Property type distribution**")
			if "property_type" in data.columns:
				_distribution(data, "property_type", "Property type")
			else:
				show_empty("No `property_type` column is available.")
	with city_col:
		with st.container(border=True):
			st.markdown("**City distribution**")
			if "city" in data.columns:
				_distribution(data, "city", "City")
			else:
				show_empty("No `city` column is available.")

	st.subheader("Correlation analysis")
	numeric = data.select_dtypes(include="number")
	if numeric.shape[1] < 2:
		st.info("At least two numeric columns are required for correlation analysis.")
	else:
		correlation = numeric.corr(numeric_only=True)
		if correlation.notna().sum().sum() == 0:
			st.info("There are not enough numeric observations to calculate correlations.")
		else:
			figure = go.Figure(go.Heatmap(z=correlation.to_numpy(), x=correlation.columns, y=correlation.index, colorscale=[[0, INDIGO], [0.5, "#ffffff"], [1, ORANGE]], zmin=-1, zmax=1, colorbar={"title": "Correlation"}, hovertemplate="%{y} × %{x}<br>%{z:.2f}<extra></extra>"))
			figure.update_layout(xaxis_title="Feature", yaxis_title="Feature")
			_render_eda_chart(figure, "eda-correlation", height=max(420, 34 * len(correlation.columns)), rotate_x_labels=True, heatmap=True)
