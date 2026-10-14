"""Cached access to the project's CSV data source."""

from pathlib import Path

import pandas as pd
import streamlit as st


DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "rentai_dataset.csv"


@st.cache_data(show_spinner=False)
def load_dataset() -> pd.DataFrame:
    """Load the property CSV, returning an empty frame when it is unavailable."""
    if not DATA_PATH.is_file() or DATA_PATH.stat().st_size == 0:
        return pd.DataFrame()

    try:
        return pd.read_csv(DATA_PATH)
    except (OSError, UnicodeDecodeError, pd.errors.EmptyDataError, pd.errors.ParserError):
        return pd.DataFrame()


def dataset_status(data: pd.DataFrame) -> str:
    if data.empty:
        return "No records loaded"
    return f"{len(data):,} properties loaded"