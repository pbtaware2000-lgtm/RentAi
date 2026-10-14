"""Small helpers for handling optional and incomplete CSV columns."""

import pandas as pd
import streamlit as st


def numeric_column(data: pd.DataFrame, name: str) -> pd.Series:
    if name not in data.columns:
        return pd.Series(dtype="float64")
    return pd.to_numeric(data[name], errors="coerce").dropna()


def category_options(data: pd.DataFrame, name: str) -> list[str]:
    if name not in data.columns:
        return []
    return sorted(data[name].dropna().astype(str).unique().tolist())


def show_empty(message: str = "Add data to `data/rentai_dataset.csv` to populate this view.") -> None:
    st.info(message)


def page_heading(title: str, description: str) -> None:
    st.title(title)
    st.caption(description)


def format_currency(value: object) -> str:
    try:
        return f"₹{float(value):,.0f}"
    except (TypeError, ValueError):
        return "-"


def property_label(row: pd.Series, index: object) -> str:
    identifier = row.get("property_id", index)
    location = ", ".join(str(row[key]) for key in ("locality", "city") if key in row and pd.notna(row[key]))
    rent = format_currency(row.get("rent"))
    details = " · ".join(part for part in (location, rent) if part)
    return f"{identifier} — {details}" if details else str(identifier)