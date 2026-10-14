"""Broker workspace limited to records assigned to the signed-in broker."""

import pandas as pd
import streamlit as st

from frontend.application_api import ApplicationAPIError, records, update


def render(_data) -> None:
    st.title("Broker Dashboard")
    st.caption("Assigned records and client details are loaded from your authorized assignments.")
    try:
        listings = records("listing")
        inquiries = records("inquiry")
    except ApplicationAPIError as exc:
        st.error(str(exc))
        return
    c1, c2, c3 = st.columns(3)
    c1.metric("Assigned properties", len(listings))
    c2.metric("Assigned enquiries", len(inquiries))
    c3.metric("Open enquiries", sum((r.get("data") or {}).get("status", "new") not in {"closed", "completed"} for r in inquiries))
    st.subheader("Assigned Properties")
    if listings:
        st.dataframe(pd.DataFrame([r.get("data", {}) for r in listings]), use_container_width=True, hide_index=True)
    else:
        st.info("No properties have been assigned to you.")
    st.subheader("Rental Enquiries and Clients")
    if not inquiries:
        st.info("No enquiries have been assigned to you.")
        return
    st.dataframe(pd.DataFrame([r.get("data", {}) for r in inquiries]), use_container_width=True, hide_index=True)
    selected = st.selectbox("Manage enquiry", inquiries, format_func=lambda r: (r.get("data") or {}).get("client_name") or (r.get("data") or {}).get("message", "Enquiry")[:70])
    data = selected.get("data") or {}
    with st.form("broker_update_enquiry"):
        status = st.selectbox("Status", ["new", "contacted", "viewing_scheduled", "completed", "closed"], index=["new", "contacted", "viewing_scheduled", "completed", "closed"].index(data.get("status", "new")))
        note = st.text_area("Broker note", value=data.get("broker_note", ""))
        submitted = st.form_submit_button("Update enquiry")
    if submitted:
        try:
            update(selected, {**data, "status": status, "broker_note": note.strip()})
            st.success("Enquiry updated.")
            st.rerun()
        except ApplicationAPIError as exc:
            st.error(str(exc))
