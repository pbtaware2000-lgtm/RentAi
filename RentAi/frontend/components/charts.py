"""Shared Plotly presentation helpers."""

import plotly.graph_objects as go
import streamlit as st


NAVY = "#0f172a"
ORANGE = "#ff6b1a"
INDIGO = "#60a5fa"
GREEN = "#059669"
MUTED = "#64748b"


def style_figure(figure: go.Figure, height: int = 340) -> go.Figure:
	figure.update_layout(
		height=height,
		margin={"l": 12, "r": 12, "t": 24, "b": 12},
		paper_bgcolor="rgba(0,0,0,0)",
		plot_bgcolor="rgba(0,0,0,0)",
		font={"color": NAVY, "family": "Aptos, Segoe UI, sans-serif", "size": 12},
		legend={"orientation": "h", "y": 1.12, "x": 0},
		xaxis={"gridcolor": "#e2e8f0", "zerolinecolor": "#e2e8f0"},
		yaxis={"gridcolor": "#e2e8f0", "zerolinecolor": "#e2e8f0"},
	)
	return figure


def render_chart(figure: go.Figure, key: str, height: int = 340) -> None:
	st.plotly_chart(style_figure(figure, height), width="stretch", key=key, config={"displayModeBar": False})