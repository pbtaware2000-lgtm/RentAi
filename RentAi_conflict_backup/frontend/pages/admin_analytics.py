"""Interactive Admin Analytics & Reports page built from live RentAI data."""

from datetime import datetime, timezone
from html import escape
import math

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

from frontend.application_api import (
    ApplicationAPIError,
    admin_analytics,
    admin_approvals,
    admin_dashboard,
    admin_records,
)


PALETTE = ["#ff6817", "#3b82f6", "#14b8a6", "#8b5cf6", "#ec4899", "#f59e0b"]


def _request(call, fallback):
    try:
        return call()
    except ApplicationAPIError as exc:
        st.error(str(exc))
        return fallback


def _clean_numbers(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").replace(
        [float("inf"), float("-inf")], float("nan")
    ).dropna()


def _style(figure):
    figure.update_layout(
        height=360,
        margin={"l": 16, "r": 18, "t": 26, "b": 28},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"color": "#0f172a", "family": "DM Sans, Segoe UI, sans-serif", "size": 12},
        hoverlabel={"bgcolor": "#07101f", "font": {"color": "#ffffff"}},
        legend={"orientation": "h", "y": 1.12, "x": 0},
        xaxis={"gridcolor": "#edf1f6", "zerolinecolor": "#e2e8f0", "automargin": True},
        yaxis={"gridcolor": "#edf1f6", "zerolinecolor": "#e2e8f0", "automargin": True},
    )
    return figure


def _chart(figure, key: str, alt: str) -> list[dict]:
    state = st.plotly_chart(
        _style(figure), width="stretch", theme=None, key=key,
        on_select="rerun", selection_mode=("points", "box", "lasso"),
        config={"displayModeBar": True, "responsive": True, "scrollZoom": True},
        alt=alt,
    )
    points = state.selection.points
    if points:
        ys = [point.get("y") for point in points]
        ys = [float(value) for value in ys if isinstance(value, (int, float)) and math.isfinite(float(value))]
        message = f"Selected {len(points):,} chart point(s)."
        if ys:
            message += f" Mean plotted value: {sum(ys) / len(ys):,.2f}."
        st.caption(message)
    return list(points)


def _reset_filters(*keys: str) -> None:
    for key in keys:
        st.session_state.pop(key, None)


def _report_csv(data: pd.DataFrame, stats: dict, analytics: dict) -> str:
    """Keep source catalog rows and the live report summaries in one CSV."""
    rows = []

    def add(section, metric, value=None, count=None, percentage=None):
        rows.append({"Report Section": section, "Metric": metric, "Value": value, "Count": count, "Percentage": percentage})

    stats = stats if isinstance(stats, dict) else {}
    analytics = analytics if isinstance(analytics, dict) else {}
    roles = stats.get("users_by_role", {}) or {}
    summary = [
        ("Generated at (UTC)", datetime.now(timezone.utc).isoformat(timespec="seconds")),
        ("Managed accounts", stats.get("users_total", "Unavailable")),
        ("Admins", roles.get("admin", "Unavailable")),
        ("Owners", roles.get("owner", "Unavailable")),
        ("Renters", roles.get("renter", "Unavailable")),
        ("User-submitted listings", stats.get("listings_total", "Unavailable")),
        ("Pending approvals", stats.get("pending_approvals", "Unavailable")),
        ("Rental requests", stats.get("rental_requests_total", "Unavailable")),
        ("Open support tickets", stats.get("support_open", "Unavailable")),
        ("Recorded ML predictions", stats.get("predictions_total", "Unavailable")),
        ("CSV catalog properties", len(data)),
    ]
    for metric, value in summary:
        add("System Summary", metric, value=value)
    if "rent" in data:
        rent = _clean_numbers(data["rent"])
        add("System Summary", "Average catalog rent", value=float(rent.mean()) if not rent.empty else "Unavailable")
    for column, section in (("city", "Properties by City"), ("property_type", "Property Types")):
        if column not in data:
            continue
        values = data[column].dropna().astype(str).str.strip()
        counts = values[values.ne("")].value_counts()
        total = int(counts.sum())
        for label, count in counts.items():
            add(section, label, count=int(count), percentage=(100 * count / total) if total else None)
    if {"posted_date", "rent"}.issubset(data.columns):
        dates = pd.to_datetime(data["posted_date"], errors="coerce")
        values = pd.to_numeric(data["rent"], errors="coerce").replace([float("inf"), float("-inf")], float("nan"))
        monthly = pd.DataFrame({"month": dates.dt.to_period("M").astype("string"), "rent": values}).dropna()
        for month, group in monthly.groupby("month", sort=True):
            add("Average Rental Price by Posted Month", str(month), value=float(group["rent"].mean()), count=len(group))
    for record in analytics.get("users_by_month", []) or []:
        if isinstance(record, dict):
            add("Monthly User Registrations (SQLite)", record.get("month", ""), count=record.get("users"))
    for record in data.to_dict(orient="records"):
        rows.append({"Report Section": "CSV Rental Catalog", **record})
    return pd.DataFrame(rows).to_csv(index=False)


def render(data: pd.DataFrame) -> None:
    st.title("Analytics & Reports")
    if not isinstance(data, pd.DataFrame):
        data = pd.DataFrame()
    report = _request(admin_analytics, {}) or {}
    stats = _request(admin_dashboard, {}) or {}
    rents = _clean_numbers(data["rent"]) if "rent" in data else pd.Series(dtype=float)
    average = float(rents.mean()) if not rents.empty else None
    listing_total = stats.get("listings_total")
    pending_total = stats.get("pending_approvals")
    cards = [
        ("🏘️", "CSV Dataset Properties", f"{len(data):,}", "Properties in the rental catalog",),
        ("💰", "Average Catalog Rent", f"₹{average:,.0f}" if average is not None else "Not available", "Average monthly catalog rent",),
        ("🏢", "User Listings", f"{listing_total:,}" if isinstance(listing_total, (int, float)) else "Unavailable", "Properties submitted by users",),
        ("✅", "Pending Approvals", f"{pending_total:,}" if isinstance(pending_total, (int, float)) else "Unavailable", "Listings waiting for review",),
    ]
    card_columns = st.columns(4, gap="medium")
    for index, (column, (icon, label, value, description)) in enumerate(zip(card_columns, cards)):
        with column, st.container(border=True, key=f"admin_analytics_card_{index}"):
            st.markdown(
                "<div class='rentai-analytics-card-content "
                f"rentai-analytics-card-content--{index}'>"
                f"<div class='rentai-analytics-card-icon' aria-hidden='true'>{icon}</div>"
                f"<div class='rentai-analytics-card-label'>{escape(label)}</div>"
                f"<div class='rentai-analytics-card-value'>{escape(value)}</div>"
                f"<div class='rentai-analytics-card-description'>{escape(description)}</div>"
                "<div class='rentai-analytics-card-action-hint'>View details</div>"
                "</div>",
                unsafe_allow_html=True,
            )
            if st.button(
                f"Open {label} details",
                key=f"admin_analytics_card_action_{index}",
                help=f"Open {label} details",
                width="stretch",
                type="tertiary",
            ):
                st.session_state["admin_analytics_selected_card"] = index

    selected_card = st.session_state.get("admin_analytics_selected_card")
    if isinstance(selected_card, int) and 0 <= selected_card < len(cards):
        _, detail_title, _, _ = cards[selected_card]
        with st.container(border=True):
            heading, close = st.columns([8, 1], vertical_alignment="center")
            heading.subheader(f"{detail_title} details")
            if close.button("Close", key="admin_analytics_close_card_details"):
                st.session_state.pop("admin_analytics_selected_card", None)
                st.rerun()

            if selected_card == 0:
                st.caption(f"{len(data):,} catalog rows · {len(data.columns):,} fields")
                missing = int(data.isna().sum().sum()) if not data.empty else 0
                st.caption(f"Missing cell values: {missing:,}")
                if data.empty:
                    st.info("The rental catalog has no property records.")
                else:
                    st.dataframe(data.head(100), width="stretch", hide_index=True)
                    if len(data) > 100:
                        st.caption(f"Showing the first 100 of {len(data):,} catalog records.")
            elif selected_card == 1:
                if rents.empty:
                    st.info("No valid rental prices are available in the catalog.")
                else:
                    summary = rents.describe()
                    metric_columns = st.columns(5)
                    for col, label, key in zip(
                        metric_columns,
                        ("Average", "Median", "Minimum", "Maximum", "Valid prices"),
                        ("mean", "50%", "min", "max", "count"),
                    ):
                        value = f"{summary[key]:,.0f}"
                        col.metric(label, f"₹{value}" if key != "count" else value)
            elif selected_card == 2:
                listings = _request(lambda: admin_records(kind="listing"), []) or []
                if not listings:
                    st.info("There are no user-submitted property listings.")
                else:
                    st.dataframe(pd.DataFrame(listings), width="stretch", hide_index=True)
            else:
                approvals = _request(lambda: admin_approvals(status="pending"), []) or []
                if not approvals:
                    st.info("There are no pending property approvals.")
                else:
                    st.dataframe(pd.DataFrame(approvals), width="stretch", hide_index=True)

    st.subheader("CSV Rental Catalog")
    left, right = st.columns(2, gap="large")
    with left, st.container(border=True, key="admin_analytics_graph_city"):
        st.markdown("**Properties by City (CSV)**")
        if "city" not in data:
            st.info("The rental catalog does not include a city column.")
        else:
            city_values = data["city"].dropna().astype(str).str.strip()
            city_counts = city_values[city_values.ne("")].value_counts().sort_values(ascending=False)
            cities = city_counts.index.tolist()
            if not cities:
                st.info("No valid city values are available in the rental catalog.")
            else:
                city_controls = st.columns([4, 1], vertical_alignment="bottom")
                choice = city_controls[0].selectbox("Filter by city", ["All cities", *cities], key="analytics_city_filter")
                city_controls[1].button(
                    "Reset",
                    key="analytics_city_reset",
                    width="stretch",
                    on_click=_reset_filters,
                    args=("analytics_city_filter",),
                )
                if choice != "All cities":
                    city_counts = city_counts.loc[[choice]]
                frame = city_counts.rename_axis("City").reset_index(name="Properties")
                fig = px.bar(frame, x="City", y="Properties", custom_data=["City", "Properties"])
                fig.update_traces(marker_color="#F97316", hovertemplate="City: %{customdata[0]}<br>Properties: %{customdata[1]:,}<extra></extra>")
                fig.update_layout(showlegend=False, clickmode="event+select", xaxis_title="City", yaxis_title="Number of Properties", xaxis_tickangle=-30)
                selected_points = _chart(fig, "analytics_properties_by_city", "CSV property counts by city")
                selected_cities = {
                    str((point.get("customdata") or [point.get("x")])[0])
                    for point in selected_points
                    if point.get("customdata") or point.get("x") is not None
                }
                if selected_cities:
                    normalized_cities = data["city"].fillna("").astype(str).str.strip()
                    for selected in sorted(selected_cities):
                        selected_count = int(normalized_cities.eq(selected).sum())
                        st.caption(f"Selected city: {selected} · {selected_count:,} catalog properties")
                        with st.expander(f"View {selected} catalog records"):
                            records = data.loc[normalized_cities.eq(selected)]
                            if records.empty:
                                st.info("No catalog records are available for this city.")
                            else:
                                st.dataframe(records.head(100), width="stretch", hide_index=True)
                                if len(records) > 100:
                                    st.caption(f"Showing the first 100 of {len(records):,} matching records.")

    with right, st.container(border=True, key="admin_analytics_graph_types"):
        st.markdown("**Property Types (CSV)**")
        if "property_type" not in data:
            st.info("The rental catalog does not include a property type column.")
        else:
            type_values = data["property_type"].dropna().astype(str).str.strip()
            type_counts = type_values[type_values.ne("")].value_counts().sort_values(ascending=False)
            if type_counts.empty:
                st.info("No valid property type values are available in the rental catalog.")
            else:
                type_controls = st.columns([4, 1], vertical_alignment="bottom")
                selected = type_controls[0].multiselect("Property types", type_counts.index.tolist(), default=type_counts.index.tolist(), key="analytics_property_type_filter")
                type_controls[1].button(
                    "Reset",
                    key="analytics_property_type_reset",
                    width="stretch",
                    on_click=_reset_filters,
                    args=("analytics_property_type_filter",),
                )
                type_counts = type_counts.loc[selected] if selected else type_counts.iloc[0:0]
                if type_counts.empty:
                    st.info("Select at least one property type to view its distribution.")
                else:
                    frame = type_counts.rename_axis("Property Type").reset_index(name="Properties")
                    fig = px.pie(frame, names="Property Type", values="Properties", hole=0.52, color_discrete_sequence=PALETTE, custom_data=["Properties"])
                    fig.update_traces(textposition="inside", textinfo="percent", hovertemplate="Property type: %{label}<br>Properties: %{customdata[0]:,}<br>Share: %{percent}<extra></extra>")
                    fig.update_layout(clickmode="event+select", showlegend=True, legend={"orientation": "v", "x": 0.98, "xanchor": "right", "y": 0.5})
                    selected_points = _chart(fig, "analytics_property_types", "CSV property type distribution")
                    selected_types = sorted({str(point.get("label")) for point in selected_points if point.get("label") is not None})
                    for selected_type in selected_types:
                        selected_count = int(type_values.eq(selected_type).sum())
                        total_count = int(type_values.ne("").sum())
                        share = (selected_count / total_count * 100) if total_count else 0
                        st.caption(f"Selected type: {selected_type} · {selected_count:,} properties ({share:.1f}% of valid catalog types)")

    st.subheader("Rental Price Distribution (CSV)")
    with st.container(border=True, key="admin_analytics_graph_rent"):
        if "rent" not in data:
            st.info("The rental catalog does not include a rental price column.")
        elif rents.empty:
            st.info("No valid rental prices are available in the rental catalog.")
        else:
            minimum, maximum = float(rents.min()), float(rents.max())
            controls = st.columns([4, 1], vertical_alignment="bottom")
            if minimum < maximum:
                price_range = controls[0].slider(
                    "Rental price range (₹ per month)",
                    min_value=minimum,
                    max_value=maximum,
                    value=(minimum, maximum),
                    step=max((maximum - minimum) / 1000, 1.0),
                    key="analytics_rent_price_range",
                )
                chart_rents = rents[rents.between(*price_range)].copy()
            else:
                controls[0].caption("All valid catalog records have the same rental price.")
                chart_rents = rents.copy()
            controls[1].button(
                "Reset filters",
                key="analytics_rent_reset",
                width="stretch",
                on_click=_reset_filters,
                args=("analytics_rent_price_range", "analytics_rent_trim_outliers", "analytics_rent_bins", "analytics_rent_sample_size"),
            )
            options = st.columns(3, gap="medium")
            trim = options[0].checkbox("Exclude extreme outliers (1st–99th percentile)", key="analytics_rent_trim_outliers")
            bins = options[1].slider("Price range bins", 5, 50, 24, 1, key="analytics_rent_bins")
            sample_size = options[2].selectbox("Records to chart", ["All valid records", 250, 500, 1000, 5000], key="analytics_rent_sample_size")
            if trim and len(chart_rents) >= 3:
                low, high = chart_rents.quantile([0.01, 0.99]).tolist()
                if low < high:
                    chart_rents = chart_rents[chart_rents.between(low, high)]
            if chart_rents.empty:
                st.info("No valid rental prices fall inside the selected range.")
            else:
                filtered_count = len(chart_rents)
                if sample_size != "All valid records" and filtered_count > int(sample_size):
                    chart_rents = chart_rents.sample(n=int(sample_size), random_state=42)
                counts, edges = np.histogram(chart_rents.to_numpy(), bins=bins)
                histogram = pd.DataFrame({
                    "Rental Price": (edges[:-1] + edges[1:]) / 2,
                    "Number of Properties": counts,
                    "Price Range": [f"₹{a:,.0f} – ₹{b:,.0f}" for a, b in zip(edges[:-1], edges[1:])],
                })
                histogram = histogram[histogram["Number of Properties"].gt(0)].copy()
                histogram["Bin"] = [str(i + 1) for i in range(len(histogram))]
                fig = px.bar(histogram, x="Rental Price", y="Number of Properties", custom_data=["Price Range", "Number of Properties"])
                fig.update_traces(marker_color="#F97316", hovertemplate="Price range: %{customdata[0]}<br>Number of Properties: %{customdata[1]:,}<extra></extra>", showlegend=False)
                fig.update_layout(showlegend=False, xaxis_title="Rental Price (₹ per month)", yaxis_title="Number of Properties")
                selected_bins = _chart(fig, "analytics_rental_price_distribution", "Rental price histogram from the CSV catalog")
                for point in selected_bins:
                    custom = point.get("customdata") or []
                    if len(custom) >= 2:
                        st.caption(f"Selected price bin: {custom[0]} · {custom[1]:,} catalog properties")
                st.caption(f"Charted {len(chart_rents):,} of {filtered_count:,} valid prices in the selected range; {len(data) - len(rents):,} rows have missing or invalid prices.")

    st.subheader("Average Rental Price by Posted Month (CSV)")
    with st.container(border=True, key="admin_analytics_graph_monthly_rent"):
        if not {"posted_date", "rent"}.issubset(data.columns):
            st.info("Monthly rent trends need both posted_date and rent columns in the rental catalog.")
        else:
            dates = pd.to_datetime(data["posted_date"], errors="coerce")
            monthly_data = pd.DataFrame({"date": dates, "rent": pd.to_numeric(data["rent"], errors="coerce")})
            monthly_data["rent"] = monthly_data["rent"].replace([float("inf"), float("-inf")], float("nan"))
            monthly_data = monthly_data.dropna(subset=["date", "rent"])
            if monthly_data.empty:
                st.info("No valid posted dates and rental prices are available for a monthly trend.")
            else:
                first, last = monthly_data["date"].min().date(), monthly_data["date"].max().date()
                date_controls = st.columns([4, 1], vertical_alignment="bottom")
                date_range = date_controls[0].date_input("Posted date range", value=(first, last), min_value=first, max_value=last, key="analytics_posted_date_range")
                date_controls[1].button(
                    "Reset",
                    key="analytics_posted_date_reset",
                    width="stretch",
                    on_click=_reset_filters,
                    args=("analytics_posted_date_range",),
                )
                if isinstance(date_range, (tuple, list)) and len(date_range) == 2:
                    start, end = date_range
                    monthly_data = monthly_data[monthly_data["date"].dt.date.between(start, end)].copy()
                else:
                    monthly_data = monthly_data.iloc[0:0].copy()
                if monthly_data.empty:
                    st.info("No valid records fall within the selected date range.")
                else:
                    monthly_data["Month"] = monthly_data["date"].dt.to_period("M").dt.to_timestamp()
                    monthly = monthly_data.groupby("Month", sort=True).agg(**{"Average Rent": ("rent", "mean"), "Listings": ("rent", "count")}).reset_index()
                    monthly["Month Label"] = monthly["Month"].dt.strftime("%b %Y")
                    fig = px.line(monthly, x="Month", y="Average Rent", markers=True, color_discrete_sequence=["#ff6817"], custom_data=["Month Label", "Listings"])
                    fig.update_traces(line={"width": 3}, marker={"size": 9, "color": "#3b82f6"}, hovertemplate="Month: %{customdata[0]}<br>Average rental price: ₹%{y:,.0f}<br>Listings: %{customdata[1]:,}<extra></extra>")
                    fig.update_layout(clickmode="event+select", xaxis_title="Posted Month", yaxis_title="Average Rental Price (₹ per month)")
                    selected_months = _chart(fig, "analytics_monthly_rent_trend", "Average CSV rental price by posted month with listing counts")
                    for point in selected_months:
                        custom = point.get("customdata") or []
                        if len(custom) >= 2:
                            st.caption(f"Selected month: {custom[0]} · average rent ₹{point.get('y', 0):,.0f} · {custom[1]:,} listings")

    st.download_button("Download CSV & system report", _report_csv(data, stats, report), file_name="rentai_admin_report.csv", mime="text/csv", type="primary")
