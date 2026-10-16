"""Owner portfolio overview with metrics from authorized SQLite records."""

from datetime import date
from html import escape
import math

import pandas as pd
import plotly.express as px
import streamlit as st

from frontend.pages.owner_support import load_or_report, money, owner_name


def calculate_metrics(listings: list[dict], enquiries: list[dict], maintenance: list[dict], rent_payments: list[dict] | None = None) -> dict:
    """Calculate dashboard values from API-authorized records only."""
    details = [record.get("data") or {} for record in listings]
    published = [item for item in details if item.get("status") == "published"]
    drafts = [item for item in details if item.get("status") == "draft"]
    unlisted = [item for item in details if item.get("status") == "unlisted"]
    occupied = sum(item.get("occupancy_status") == "occupied" for item in details)
    vacant = sum(item.get("occupancy_status") == "vacant" for item in details)
    unknown = len(details) - occupied - vacant
    pending_statuses = {"new", "pending", "contacted", "viewing_scheduled"}
    new_enquiries = sum((record.get("data") or {}).get("status", "new") in pending_statuses for record in enquiries)
    rent = 0.0
    for item in published:
        try:
            amount = float(item.get("rent", 0) or 0)
            if math.isfinite(amount) and amount > 0:
                rent += amount
        except (TypeError, ValueError):
            continue
    payments = rent_payments or []
    collected = 0.0
    outstanding = 0.0
    for record in payments:
        payment = record.get("data") or {}
        try:
            amount = float(payment.get("amount", 0) or 0)
            if not math.isfinite(amount) or amount <= 0:
                continue
            if payment.get("status") == "paid":
                collected += float(payment.get("paid_amount", amount) or 0)
            elif payment.get("status") == "pending":
                outstanding += amount
        except (TypeError, ValueError):
            continue
    return {
        "details": details, "published": published, "drafts": drafts,
        "unlisted": unlisted,
        "occupied": occupied, "vacant": vacant, "occupancy_unknown": unknown,
        "new_enquiries": new_enquiries, "potential_monthly_rent": rent,
        "rent_collected": collected, "rent_outstanding": outstanding,
    }


def calculate_dataset_metrics(data: pd.DataFrame) -> dict:
    """Summarize the public CSV catalog without treating it as owner inventory."""
    if not isinstance(data, pd.DataFrame) or data.empty:
        return {
            "record_count": 0, "average_rent": None, "median_rent": None,
            "city_count": 0, "locality_count": 0,
            "category_counts": pd.Series(dtype="int64"),
            "property_type_counts": pd.Series(dtype="int64"),
            "city_counts": pd.Series(dtype="int64"),
            "location_counts": pd.Series(dtype="int64"),
            "monthly_rent": pd.DataFrame(columns=["posted_month", "average_rent", "postings"]),
        }

    def distribution(column: str) -> pd.Series:
        if column not in data.columns:
            return pd.Series(dtype="int64")
        values = data[column].astype("string").str.strip()
        return values[values.notna() & values.ne("")].value_counts()

    rent = pd.to_numeric(data.get("rent", pd.Series(index=data.index, dtype=float)), errors="coerce")
    rent = rent.replace([float("inf"), float("-inf")], float("nan"))
    rent = rent[rent > 0]
    city_counts = distribution("city")
    locality_counts = distribution("locality")
    location_counts = pd.Series(dtype="int64")
    if {"city", "locality"}.issubset(data.columns):
        locations = data[["city", "locality"]].astype("string").apply(lambda column: column.str.strip())
        locations = locations[locations.notna().all(axis=1) & locations.ne("").all(axis=1)]
        if not locations.empty:
            labels = locations["city"] + " · " + locations["locality"]
            location_counts = labels.value_counts()

    monthly_rent = pd.DataFrame(columns=["posted_month", "average_rent", "postings"])
    if "posted_date" in data.columns and not rent.empty:
        dated_rents = pd.DataFrame({"posted_date": data.loc[rent.index, "posted_date"], "rent": rent})
        dated_rents["posted_month"] = (
            pd.to_datetime(dated_rents["posted_date"], errors="coerce")
            .dt.to_period("M")
            .dt.to_timestamp()
        )
        dated_rents = dated_rents.dropna(subset=["posted_month"])
        monthly_rent = (
            dated_rents.groupby("posted_month", as_index=False)
            .agg(average_rent=("rent", "mean"), postings=("rent", "size"))
            .sort_values("posted_month")
        )

    return {
        "record_count": len(data),
        "average_rent": float(rent.mean()) if not rent.empty else None,
        "median_rent": float(rent.median()) if not rent.empty else None,
        "city_count": len(city_counts),
        "locality_count": len(locality_counts),
        "category_counts": distribution("rent_category"),
        "property_type_counts": distribution("property_type"),
        "city_counts": city_counts,
        "location_counts": location_counts,
        "monthly_rent": monthly_rent,
    }


def render_dataset_analytics(data: pd.DataFrame) -> None:
    st.subheader("Rental market dataset")
    st.caption("Descriptive analytics from the CSV catalog. These records are not part of your owner portfolio.")
    if not isinstance(data, pd.DataFrame) or data.empty:
        st.info("Dataset analytics are unavailable because no CSV records were loaded.")
        return

    metrics = calculate_dataset_metrics(data)
    summary = st.columns(4)
    with summary[0]:
        st.metric("Dataset records", f"{metrics['record_count']:,}", label_visibility="collapsed")
        st.caption("Total Properties")
    with summary[1]:
        st.metric("Average dataset rent", money(metrics["average_rent"]), label_visibility="collapsed")
        st.caption("Average Monthly Rent")
    with summary[2]:
        st.metric("Median dataset rent", money(metrics["median_rent"]), label_visibility="collapsed")
        st.caption("Median Monthly Rent")
    with summary[3]:
        st.metric(
            "City / locality labels",
            f"{metrics['city_count']} / {metrics['locality_count']}",
            label_visibility="collapsed",
        )
        st.caption("Cities / Localities")

    first_row = st.columns(2)
    with first_row[0]:
        _render_distribution_card(
            "Rent category distribution (CSV)", metrics["category_counts"],
            "No rent category labels are available in the dataset.",
            "Rent category", "owner-dataset-category", "#ff6b1a",
        )
    with first_row[1]:
        _render_distribution_card(
            "Property type distribution (CSV)", metrics["property_type_counts"],
            "No property type labels are available in the dataset.",
            "Property type", "owner-dataset-property-type", "#059669",
        )

    second_row = st.columns(2)
    with second_row[0]:
        _render_distribution_card(
            "Listings by city (CSV)", metrics["city_counts"],
            "No city labels are available in the dataset.",
            "City", "owner-dataset-city", "#60a5fa",
        )
    with second_row[1]:
        _render_distribution_card(
            "Top city and locality combinations (CSV)", metrics["location_counts"],
            "No city and locality pairs are available in the dataset.",
            "City · locality", "owner-dataset-locality", "#8b5cf6",
        )

    st.markdown("**Average listed rent by posting month (CSV)**")
    monthly_rent = metrics["monthly_rent"]
    if monthly_rent.empty:
        st.info("No valid posting dates and rents are available for a monthly chart.")
    else:
        trend = px.line(monthly_rent, x="posted_month", y="average_rent", markers=True, template="plotly_white")
        trend.update_layout(
            height=260, margin={"l": 90, "r": 24, "t": 12, "b": 48}, showlegend=False,
            paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
            font={"color": "#0f172a", "family": "Aptos, Segoe UI, sans-serif", "size": 12},
        )
        trend.update_xaxes(
            title="Posting month", automargin=True, tickformat="%b %Y",
            tickfont={"color": "#334155", "size": 11},
            title_font={"color": "#334155", "size": 12},
            gridcolor="#e2e8f0", zerolinecolor="#cbd5e1",
        )
        trend.update_yaxes(
            title="Average rent (₹ / month)", automargin=True, tickprefix="₹", tickformat=",.0f",
            tickfont={"color": "#334155", "size": 11},
            title_font={"color": "#334155", "size": 12},
            gridcolor="#e2e8f0", zerolinecolor="#cbd5e1",
        )
        st.plotly_chart(trend, width="stretch", key="owner-dataset-monthly-rent", config={"displayModeBar": False})
        st.caption("This is a descriptive view of dataset posting dates, not your portfolio's rent collection history.")


def _render_distribution_card(
    title: str,
    series: pd.Series,
    empty_message: str,
    category_label: str,
    key: str,
    color: str,
) -> None:
    with st.container(border=True):
        st.markdown(f"**{title}**")
        if series.empty:
            st.info(empty_message)
        else:
            _render_count_chart(series, category_label, key, color)


def _render_count_chart(series: pd.Series, category_label: str, key: str, color: str) -> None:
    chart_data = series.head(10).rename_axis(category_label).rename("Listings").reset_index()
    figure = px.bar(
        chart_data, x="Listings", y=category_label, orientation="h",
        color_discrete_sequence=[color], template="plotly_white",
    )
    figure.update_layout(
        height=300,
        margin={"l": 150, "r": 40, "t": 8, "b": 32},
        showlegend=False,
        paper_bgcolor="#ffffff",
        plot_bgcolor="#ffffff",
        font={"color": "#0f172a", "family": "Aptos, Segoe UI, sans-serif", "size": 12},
        bargap=0.32,
    )
    figure.update_traces(
        textposition="outside",
        texttemplate="%{x:,.0f}",
        textfont={"color": "#0f172a", "size": 12},
        cliponaxis=False,
    )
    figure.update_xaxes(
        title="Listings", automargin=True, tickformat=",d", rangemode="tozero",
        tickfont={"color": "#334155", "size": 11},
        title_font={"color": "#334155", "size": 12},
        gridcolor="#e2e8f0", zerolinecolor="#cbd5e1",
    )
    figure.update_yaxes(
        title=None, automargin=True, categoryorder="total ascending",
        tickfont={"color": "#0f172a", "size": 12},
        gridcolor="#ffffff", zerolinecolor="#ffffff",
    )
    st.plotly_chart(figure, width="stretch", key=key, config={"displayModeBar": False})


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
      .owner-hero { background:linear-gradient(115deg,#07101f 0%,#132c48 70%,#17415e 100%);
        border-radius:18px; padding:1.5rem 1.8rem; margin:.25rem 0 1.25rem; color:#f8fafc; }
      .owner-hero h1 { color:#fff!important; font-size:1.8rem!important; margin:0 0 .35rem; }
      .owner-hero p { color:#cbd5e1!important; margin:0; }
      .owner-kpi-accent { height:3px; width:100%; border-radius:999px; margin:0 0 .45rem; }
      div[data-testid='stVerticalBlockBorderWrapper'] { border-radius:14px; border-color:#e2e8f0; }
      div[data-testid='stVerticalBlockBorderWrapper']:has(.owner-kpi-accent) { height:100%; }
      div[data-testid='stVerticalBlockBorderWrapper']:has(.owner-kpi-accent) [data-testid='stVerticalBlock'] {
        min-height:158px; padding:.75rem .9rem; box-sizing:border-box;
      }
      div[data-testid='stVerticalBlockBorderWrapper']:has(.owner-kpi-accent) [data-testid='stMetric'] {
        padding:0; text-align:left;
      }
      div[data-testid='stVerticalBlockBorderWrapper']:has(.owner-kpi-accent) [data-testid='stMetricLabel'] {
        min-height:2.5em; color:#475569; font-size:.8rem; line-height:1.25; text-align:left;
      }
      div[data-testid='stVerticalBlockBorderWrapper']:has(.owner-kpi-accent) [data-testid='stMetricValue'] {
        color:#0f172a; font-size:1.55rem; font-weight:700; line-height:1.15; text-align:left;
      }
      div[data-testid='stVerticalBlockBorderWrapper']:has(.owner-kpi-accent) [data-testid='stCaptionContainer'] {
        min-height:2.6em; color:#64748b; font-size:.76rem; line-height:1.3; text-align:left;
      }
      .owner-section-note { color:#64748b; font-size:.88rem; }
    </style>
    """, unsafe_allow_html=True)
    records = load_or_report()
    if records is None:
        render_dataset_analytics(_data)
        return
    listings = records["listings"]
    enquiries = records["enquiries"]
    maintenance = records["maintenance"]
    metrics = calculate_metrics(listings, enquiries, maintenance, records.get("rent_payments", []))
    details = metrics["details"]
    published = metrics["published"]

    st.markdown(
        f"<div class='owner-hero'><h1>Welcome back, {escape(owner_name())}</h1>"
        "<p>Your rental portfolio at a glance, with listings and activity from your account.</p></div>",
        unsafe_allow_html=True,
    )
    actions = st.columns(3)
    if actions[0].button("Add a property", type="primary", key="owner_dashboard_add_property"):
        st.session_state.owner_active_page = "Add New Property"
        st.rerun()
    if actions[1].button("Manage properties", key="owner_dashboard_manage_properties"):
        st.session_state.owner_active_page = "My Properties"
        st.rerun()
    if actions[2].button("Review enquiries", key="owner_dashboard_review_enquiries"):
        st.session_state.owner_active_page = "Applications & Enquiries"
        st.rerun()

    kpis = st.columns(5)
    _metric_card(kpis[0], "Total properties", len(listings), "Your saved SQLite portfolio", "#ff6b1a")
    _metric_card(kpis[1], "Published", len(published), "Visible rental listings", "#059669")
    _metric_card(kpis[2], "Draft / unlisted", len(metrics["drafts"]) + len(metrics["unlisted"]), f"{len(metrics['drafts'])} draft · {len(metrics['unlisted'])} unlisted", "#f59e0b")
    _metric_card(kpis[3], "Vacant", metrics["vacant"], f"{metrics['occupancy_unknown']} without occupancy status", "#60a5fa")
    _metric_card(kpis[4], "Occupied", metrics["occupied"], "Saved portfolio properties", "#8b5cf6")

    st.subheader("Portfolio activity")
    activity = st.columns(3)
    _metric_card(activity[0], "Pending enquiries", metrics["new_enquiries"], f"of {len(enquiries)} total messages", "#ff6b1a")
    _metric_card(activity[1], "Enquiry messages", len(enquiries), "Renter enquiries for your properties", "#60a5fa")
    _metric_card(activity[2], "Maintenance requests", len(maintenance), "Open and recorded work items", "#8b5cf6")

    st.subheader("Revenue overview")
    revenue = st.columns(3)
    _metric_card(revenue[0], "Potential monthly rent", money(metrics["potential_monthly_rent"]), "Published asking rents only", "#059669")
    _metric_card(revenue[1], "Rent collected", money(metrics["rent_collected"]), "Paid rent records in your portfolio", "#059669")
    _metric_card(revenue[2], "Outstanding rent", money(metrics["rent_outstanding"]), "Pending rent records in your portfolio", "#f59e0b")

    render_dataset_analytics(_data)

    st.subheader("Portfolio overview")
    chart_col, table_col = st.columns([1, 1.2])
    with chart_col, st.container(border=True):
        st.markdown("**Monthly asking rent**")
        if published:
            chart = pd.DataFrame({
                "Property": [str(item.get("title") or item.get("locality") or "Listing") for item in published],
                "Monthly rent": [float(item.get("rent", 0) or 0) for item in published],
            })
            rent_chart = px.bar(
                chart, x="Monthly rent", y="Property", orientation="h",
                color_discrete_sequence=["#ff6b1a"], template="plotly_white",
            )
            rent_chart.update_layout(
                height=max(180, 26 * len(chart) + 60),
                margin={"l": 180, "r": 40, "t": 8, "b": 36}, showlegend=False,
                paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
                font={"color": "#0f172a", "family": "Aptos, Segoe UI, sans-serif", "size": 12},
                bargap=0.32,
            )
            rent_chart.update_xaxes(
                title="Asking rent (₹ / month)", automargin=True,
                tickprefix="₹", tickformat=",.0f", rangemode="tozero",
                tickfont={"color": "#334155", "size": 11},
                title_font={"color": "#334155", "size": 12},
                gridcolor="#e2e8f0", zerolinecolor="#cbd5e1",
            )
            rent_chart.update_yaxes(
                title=None, automargin=True, categoryorder="total ascending",
                tickfont={"color": "#0f172a", "size": 12},
                gridcolor="#ffffff", zerolinecolor="#ffffff",
            )
            st.plotly_chart(rent_chart, width="stretch", key="owner-portfolio-asking-rent", config={"displayModeBar": False})
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

    st.subheader("Recent property activity")
    recent_properties = sorted(
        listings, key=lambda item: item.get("created_at", ""), reverse=True
    )[:5]
    if recent_properties:
        st.dataframe(pd.DataFrame([{
            "Property": (item.get("data") or {}).get("title", "Untitled property"),
            "Location": ", ".join(filter(None, [(item.get("data") or {}).get("locality"), (item.get("data") or {}).get("city")])),
            "Status": (item.get("data") or {}).get("status", "draft").title(),
            "Saved": item.get("created_at", ""),
        } for item in recent_properties]), use_container_width=True, hide_index=True)
    else:
        st.info("Add a property to see your recent portfolio activity.")

    st.subheader("Upcoming activities")
    dated = [item.get("data") or {} for item in maintenance
             if (item.get("data") or {}).get("due_date", "") >= date.today().isoformat()]
    if dated:
        st.dataframe(pd.DataFrame(dated), use_container_width=True, hide_index=True)
    else:
        st.caption("No upcoming maintenance dates are recorded.")
