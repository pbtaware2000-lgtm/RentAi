"""KPI rendering for the dashboard and admin page."""

import pandas as pd
import streamlit as st

from frontend.components.data_utils import numeric_column


def dashboard_metrics(data: pd.DataFrame) -> dict[str, str]:
	rent = numeric_column(data, "rent")
	cities = data["city"].nunique() if "city" in data.columns else None
	return {
		"TOTAL PROPERTIES": f"{len(data):,}" if not data.empty else "0",
		"AVERAGE RENT": f"₹{rent.mean():,.0f}" if not rent.empty else "-",
		"CITIES COVERED": f"{cities:,}" if cities is not None and not data.empty else "-",
		"ML PREDICTIONS": "-",
		"ML ALGORITHMS": "-",
	}



def render_dashboard_metrics(data: pd.DataFrame) -> None:
	metrics = dashboard_metrics(data)
	columns = st.columns(5)
	for column, (label, value) in zip(columns, metrics.items()):
		with column:
			with st.container(border=True):
				st.caption(label)
				st.metric(label=label, value=value, label_visibility="collapsed")