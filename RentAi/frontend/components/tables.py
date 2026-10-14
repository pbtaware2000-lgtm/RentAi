"""Shared dataframe presentation helpers."""

import pandas as pd
import streamlit as st


def render_table(data: pd.DataFrame, key: str, height: int = 360) -> None:
	st.dataframe(data, width="stretch", hide_index=True, height=height, key=key)