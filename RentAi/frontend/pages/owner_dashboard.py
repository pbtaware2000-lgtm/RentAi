"""Owner portfolio overview with metrics from authorized Firestore records."""

from datetime import date
from html import escape
import math

import pandas as pd
import streamlit as st

from frontend.pages.owner_support import load_or_report, money, owner_name


def calculate_metrics(listings: list[dict], enquiries: list[dict], maintenance: list[dict]) -> dict:
    """Calculate dashboard values from API-authorized records only."""
    details = [record.get("data") or {} for record in listings]
    published = [item for item in details if item.get("status") == "published"]
    drafts = [item for item in details if item.get("status") == "draft"]
    occupied = sum(item.get("occupancy_status") == "occupied" for item in details)
    vacant = sum(item.get("occupancy_status") == "vacant" for item in details)
    unknown = len(details) - occupied - vacant
    new_enquiries = sum((record.get("data") or {}).get("status", "new") == "new" for record in enquiries)
    rent = 0.0
    for item in published:
        try:
            amount = float(item.get("rent", 0) or 0)
            if math.isfinite(amount) and amount > 0:
                rent += amount
        except (TypeError, ValueError):
            continue
    return {
        "details": details, "published": published, "drafts": drafts,
        "occupied": occupied, "vacant": vacant, "occupancy_unknown": unknown,
        "new_enquiries": new_enquiries, "potential_monthly_rent": rent,
    }


def _metric_card(column, label: str, value: str | int, hint: str, accent: str) -> None:
    with column, st.container(border=True):
        st.markdown(
            f"<div class='owner-kpi-accent' style='background:{accent}'></div>",
            unsafe_allow_html=True,
        )
        st.metric(label, value)
        st.caption(hint)


def render(_data=None) -> None:
    st.markdown("""
    <style>
      [data-testid='stMetric'] { padding: .15rem .1rem; }
      .owner-hero { background:linear-gradient(115deg,#07101f 0%,#132c48 70%,#17415e 100%);
        border-radius:18px; padding:1.5rem 1.8rem; margin:.25rem 0 1.25rem; color:#f8fafc; }
      .owner-hero h1 { color:#fff!important; font-size:1.8rem!important; margin:0 0 .35rem; }
      .owner-hero p { color:#cbd5e1!important; margin:0; }
      .owner-kpi-accent { height:4px; border-radius:4px; margin:0 0 .65rem; }
      div[data-testid='stVerticalBlockBorderWrapper'] { border-radius:14px; border-color:#e2e8f0; }
      .owner-section-note { color:#64748b; font-size:.88rem; }
    </style>
    """, unsafe_allow_html=True)
    records = load_or_report()
    if records is None:
        return
    listings = records["listings"]
    enquiries = records["enquiries"]
    maintenance = records["maintenance"]
    metrics = calculate_metrics(listings, enquiries, maintenance)
    details = metrics["details"]
    published = metrics["published"]

    st.markdown(
        f"<div class='owner-hero'><h1>Welcome back, {escape(owner_name())}</h1>"
        "<p>Your rental portfolio at a glance, with listings and activity from your account.</p></div>",
        unsafe_allow_html=True,
    )
    kpis = st.columns(4)
    _metric_card(kpis[0], "Total properties", len(listings), "Your saved portfolio", "#ff6b1a")
    _metric_card(kpis[1], "Published", len(published), "Visible rental listings", "#059669")
    _metric_card(kpis[2], "Vacant", metrics["vacant"], f"{metrics['occupancy_unknown']} without occupancy status", "#60a5fa")
    _metric_card(kpis[3], "Occupied", metrics["occupied"], f"{len(metrics['drafts'])} draft or unlisted", "#8b5cf6")

    st.subheader("Portfolio activity")
    activity = st.columns(3)
    _metric_card(activity[0], "New enquiries", metrics["new_enquiries"], f"of {len(enquiries)} total messages", "#ff6b1a")
    _metric_card(activity[1], "Enquiry messages", len(enquiries), "Renter enquiries for your properties", "#60a5fa")
    _metric_card(activity[2], "Maintenance requests", len(maintenance), "Open and recorded work items", "#8b5cf6")

    st.subheader("Revenue overview")
    revenue = st.columns(4)
    _metric_card(revenue[0], "Potential monthly rent", money(metrics["potential_monthly_rent"]), "Published asking rents only", "#059669")
    for column, title in zip(revenue[1:], ("Rent collected", "Outstanding rent", "Income & expenses")):
        _metric_card(column, title, "Not tracked", "Payment and expense records are not stored", "#94a3b8")

    st.subheader("Portfolio overview")
    chart_col, table_col = st.columns([1, 1.2])
    with chart_col, st.container(border=True):
        st.markdown("**Monthly asking rent**")
        if published:
            chart = pd.DataFrame({
                "Property": [str(item.get("title") or item.get("locality") or "Listing")[:24] for item in published],
                "Monthly rent": [float(item.get("rent", 0) or 0) for item in published],
            }).set_index("Property")
            st.bar_chart(chart, height=260, color="#ff6b1a")
        else:
            st.info("No published listings yet. Add a property and publish it to see potential rent here.")
    with table_col, st.container(border=True):
        st.markdown("**Your properties**")
        if listings:
            st.dataframe(pd.DataFrame([{
                "Property": item.get("title") or "Untitled property",
                "Location": ", ".join(filter(None, [item.get("locality"), item.get("city")])),
                "Status": "Listed" if item.get("status") == "published" else "Draft",
                "Occupancy": {"occupied": "Occupied", "vacant": "Vacant"}.get(item.get("occupancy_status"), "Not tracked"),
                "Monthly rent": money(item.get("rent")),
            } for item in details]), use_container_width=True, hide_index=True)
        else:
            st.info("Your portfolio is empty. Add your first property to start tracking listings and enquiries.")

    st.subheader("Recent enquiry activity")
    if enquiries:
        recent = sorted(enquiries, key=lambda item: (item.get("data") or {}).get("created_at", ""), reverse=True)[:5]
        st.dataframe(pd.DataFrame([{
            "Renter": (item.get("data") or {}).get("client_name", "Renter"),
            "Property ID": (item.get("data") or {}).get("listing_id", ""),
            "Status": (item.get("data") or {}).get("status", "new").replace("_", " ").title(),
        } for item in recent]), use_container_width=True, hide_index=True)
    else:
        st.info("Enquiries about your listings will appear here.")

    st.subheader("Upcoming activities")
    dated = [item.get("data") or {} for item in maintenance
             if (item.get("data") or {}).get("due_date", "") >= date.today().isoformat()]
    if dated:
        st.dataframe(pd.DataFrame(dated), use_container_width=True, hide_index=True)
    else:
        st.caption("No upcoming maintenance dates are recorded.")
