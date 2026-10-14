"""Shared data access and small view helpers for Owner sections."""

import pandas as pd
import streamlit as st

from frontend.application_api import ApplicationAPIError, records


def load_owner_records() -> dict[str, list[dict]]:
    return {
        "listings": records("listing"),
        "enquiries": records("inquiry"),
        "maintenance": records("maintenance"),
        "profiles": records("profile"),
    }


def load_or_report() -> dict[str, list[dict]] | None:
    try:
        return load_owner_records()
    except ApplicationAPIError as exc:
        st.error(str(exc))
        return None


def owner_name() -> str:
    return st.session_state.get("rentai_display_name") or "Owner"


def rows(records: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([
        {**(record.get("data") or {}), "record_id": record.get("id", "")}
        for record in records
    ])


def money(value: object) -> str:
    amount = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return f"₹{amount:,.0f}" if pd.notna(amount) else "Not tracked yet"
