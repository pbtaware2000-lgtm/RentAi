<<<<<<< HEAD
"""SQLite-backed Admin console and its role-protected management sections."""

from datetime import datetime, timezone
from html import escape
from urllib.parse import quote

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from frontend.application_api import (
    ApplicationAPIError,
    admin_activity,
    admin_approvals,
    admin_dashboard,
    admin_predictions,
    admin_records,
    admin_test_prediction,
    admin_users,
    create_support_ticket,
    review_property,
    set_user_status,
    support_tickets,
    update_rental_request,
    update_support_ticket,
)
from backend.schemas.prediction_schema import RentPredictionRequest


def _request(call, fallback=None):
    try:
        return call()
    except ApplicationAPIError as exc:
        st.error(str(exc))
        return fallback


def _format_registration_date(value) -> str:
    if value is None or not str(value).strip():
        return "Not available"
    try:
        registered = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    return registered.strftime("%B %d, %Y, %H:%M:%S")


def _activity_sort_timestamp(item: dict) -> float:
    value = item.get("created_at")
    if value is None or not str(value).strip():
        return float("-inf")
    try:
        timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return float("-inf")
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.timestamp()


def render(data: pd.DataFrame) -> None:
    page = st.session_state.get("page", "Admin Dashboard")
    if page == "Admin Dashboard":
        render_dashboard(data)
    elif page == "User Management":
        render_users()
    elif page == "Owner Management":
        render_users("owner")
    elif page == "Renter Management":
        render_users("renter")
    elif page == "Property Approval Requests":
        render_approvals()
    elif page == "Booking & Rental Requests":
        render_rental_requests()
    elif page == "Analytics & Reports" or page == "Admin & Reports":
        render_analytics(data)
    elif page == "ML Predictions":
        render_predictions(data)
    elif page == "Support & Complaints":
        render_support(admin=True)
    else:
        render_dashboard(data)


def render_dashboard(data: pd.DataFrame) -> None:
    st.title("Admin Dashboard")
    st.caption("Live account and application activity from SQLite, with the CSV catalog reported separately.")
    stats = _request(admin_dashboard, {})
    if not stats:
        st.info("Dashboard statistics are unavailable.")
        return
    roles = stats.get("users_by_role", {})
    cards = [
        ("Managed accounts", stats.get("users_total", 0)),
        ("Owners", roles.get("owner", 0)),
        ("Renters", roles.get("renter", 0)),
        ("Admins", roles.get("admin", 0)),
        ("User listings", stats.get("listings_total", 0)),
        ("Pending approvals", stats.get("pending_approvals", 0)),
        ("Active rental requests", stats.get("active_rental_requests", 0)),
        ("Bookings", "Not tracked"),
        ("Open support requests", stats.get("support_open", 0)),
        ("ML predictions", stats.get("predictions_total", 0)),
    ]
    card_icons = {
        "Managed accounts": "♙", "Owners": "⌂", "Renters": "♧", "Admins": "⚙",
        "User listings": "▤", "Pending approvals": "◷", "Active rental requests": "↔",
        "Bookings": "▣", "Open support requests": "✉", "ML predictions": "✦",
    }
    for start in range(0, len(cards), 5):
        columns = st.columns(5, gap="large")
        for index, (column, (label, value)) in enumerate(zip(columns, cards[start:start + 5]), start=start):
            with column, st.container(border=True, key=f"admin_metric_card_{index}"):
                visible_value = f"{value:,}" if isinstance(value, int) else str(value)
                st.markdown(
                    "<div class='rentai-admin-dashboard-card-content'>"
                    f"<div class='rentai-admin-dashboard-card-heading'><span class='rentai-admin-dashboard-card-icon' aria-hidden='true'>{card_icons[label]}</span>"
                    f"<span class='rentai-admin-dashboard-card-title'>{escape(label)}</span></div>"
                    f"<div class='rentai-admin-dashboard-card-value'>{escape(visible_value)}</div></div>",
                    unsafe_allow_html=True,
                )
    st.session_state.pop("admin_dashboard_selected_card", None)

    if stats.get("dataset_properties") is not None:
        st.caption(f"CSV rental catalog: {stats['dataset_properties']:,} dataset rows. These are not user-submitted listings.")
    else:
        st.caption("CSV rental catalog is unavailable. User-submitted listings remain tracked separately.")

    st.subheader("System activity")
    chart_cols = st.columns(2)
    role_colors = {"Admin": "#8B5CF6", "Owner": "#FF7A21", "Renter": "#3B82F6"}
    role_figure = go.Figure()
    for role, title in (("admin", "Admin"), ("owner", "Owner"), ("renter", "Renter")):
        count = int(roles.get(role, 0) or 0)
        role_figure.add_trace(go.Bar(
            x=[title], y=[count], name=title, marker_color=role_colors[title],
            text=[f"{count:,}"], textposition="outside",
            hovertemplate=f"{title}: %{{y:,}}<extra></extra>",
        ))
    role_figure.update_layout(
        height=330, barmode="group", showlegend=True,
        margin={"l": 18, "r": 18, "t": 18, "b": 24},
        paper_bgcolor="#FFFFFF", plot_bgcolor="#FFFFFF",
        font={"color": "#475569", "family": "DM Sans, Segoe UI, sans-serif"},
        legend={"orientation": "h", "y": 1.12, "x": 0},
        xaxis={"title": None, "automargin": True},
        yaxis={"title": "Accounts", "rangemode": "tozero", "gridcolor": "#F1F5F9", "automargin": True},
        hoverlabel={"bgcolor": "#FFFFFF", "font": {"color": "#17243A"}},
    )
    with chart_cols[0], st.container(border=True, key="admin_dashboard_users_by_role_chart"):
        st.markdown("**Users by Role**")
        st.plotly_chart(
            role_figure, width="stretch", key="admin_dashboard_users_by_role_plot",
            config={"displayModeBar": True, "displaylogo": False, "responsive": True, "scrollZoom": True},
        )

    approval_requests = _request(admin_approvals, None)
    with chart_cols[1], st.container(border=True, key="admin_dashboard_approval_status_chart"):
        st.markdown("**Property Approval Status**")
        if approval_requests is None:
            st.info("Approval status data is currently unavailable.")
        else:
            approval_labels = ["Approved", "Pending", "Rejected"]
            approval_statuses = {"Approved": "approved", "Pending": "pending", "Rejected": "rejected"}
            approval_counts = {
                label: sum(1 for request in approval_requests if request.get("status") == status)
                for label, status in approval_statuses.items()
            }
            if sum(approval_counts.values()) == 0:
                st.info("No data available for this chart yet.")
            else:
                approval_figure = go.Figure(go.Pie(
                    labels=approval_labels,
                    values=[approval_counts[label] for label in approval_labels],
                    hole=0.56,
                    sort=False,
                    marker={"colors": ["#22C55E", "#FF7A21", "#EF4444"], "line": {"color": "#FFFFFF", "width": 2}},
                    textinfo="label+value",
                    hovertemplate="%{label}: %{value:,} (%{percent})<extra></extra>",
                ))
                approval_figure.update_layout(
                    height=330, showlegend=True,
                    margin={"l": 18, "r": 18, "t": 18, "b": 24},
                    paper_bgcolor="#FFFFFF", plot_bgcolor="#FFFFFF",
                    font={"color": "#475569", "family": "DM Sans, Segoe UI, sans-serif"},
                    legend={"orientation": "h", "y": -0.08, "x": 0.12},
                    hoverlabel={"bgcolor": "#FFFFFF", "font": {"color": "#17243A"}},
                )
                with st.container(key="admin_dashboard_approval_status_plot_container"):
                    st.plotly_chart(
                        approval_figure, width="stretch", key="admin_dashboard_approval_status_plot",
                        config={"displayModeBar": True, "displaylogo": False, "responsive": True, "scrollZoom": True},
                    )


def _render_dashboard_card_details(card_index: int) -> None:
    """Show live, read-only records for the selected dashboard summary card."""
    labels = [
        "Managed accounts", "Owners", "Renters", "Admins", "User listings",
        "Pending approvals", "Active rental requests", "Bookings",
        "Open support requests", "ML predictions",
    ]
    title = labels[card_index]
    with st.container(border=True, key="admin_dashboard_card_details"):
        heading, close_column = st.columns([5, 1], vertical_alignment="center")
        heading.subheader(title)
        if close_column.button("Close", key="admin_dashboard_card_details_close"):
            st.session_state.pop("admin_dashboard_selected_card", None)
            st.rerun()

        if card_index in {0, 1, 2, 3}:
            users = _request(admin_users, None)
            if users is None:
                return
            if card_index == 1:
                users = [user for user in users if user.get("role") == "owner"]
            elif card_index == 2:
                users = [user for user in users if user.get("role") == "renter"]
            elif card_index == 3:
                users = [user for user in users if user.get("role") == "admin"]
            rows = [{
                "User ID": user.get("uid") or "Not available",
                "Name": user.get("name") or "Not available",
                "Email": user.get("email") or "Not available",
                "Role": str(user.get("role") or "Not available").replace("_", " ").title(),
                "Account status": str(user.get("account_status") or "Not available").replace("_", " ").title(),
                "Registered": _format_registration_date(user.get("created_at")),
            } for user in users]
            _show_dashboard_detail_rows(rows, "No matching accounts are currently available.")
        elif card_index == 4:
            records = _request(lambda: admin_records("listing"), None)
            if records is None:
                return
            rows = []
            for record in records:
                detail = record.get("data") if isinstance(record.get("data"), dict) else {}
                rows.append({
                    "Property ID": record.get("id") or "Not available",
                    "Property": detail.get("title") or "Not available",
                    "Owner": record.get("submitter_name") or "Not available",
                    "City": detail.get("city") or "Not available",
                    "Type": detail.get("property_type") or "Not available",
                    "Monthly rent": detail.get("rent", "Not available"),
                    "Status": str(detail.get("status") or "Not available").replace("_", " ").title(),
                    "Submitted": _format_registration_date(record.get("created_at")),
                })
            _show_dashboard_detail_rows(rows, "No user-submitted property listings are currently available.")
        elif card_index == 5:
            approvals = _request(lambda: admin_approvals("pending"), None)
            if approvals is None:
                return
            rows = []
            for item in approvals:
                prop = item.get("property") if isinstance(item.get("property"), dict) else {}
                rows.append({
                    "Request ID": item.get("request_id") or "Not available",
                    "Property": prop.get("title") or "Not available",
                    "Submitted by": item.get("submitter_name") or "Not available",
                    "Location": ", ".join(str(value) for value in (prop.get("locality"), prop.get("city")) if value) or "Not available",
                    "Monthly rent": prop.get("rent", "Not available"),
                    "Submitted": _format_registration_date(item.get("created_at")),
                })
            _show_dashboard_detail_rows(rows, "There are no pending property approvals.")
        elif card_index == 6:
            records = _request(lambda: admin_records("inquiry"), None)
            if records is None:
                return
            active_statuses = {"new", "contacted", "viewing_scheduled"}
            rows = []
            for record in records:
                detail = record.get("data") if isinstance(record.get("data"), dict) else {}
                status = str(detail.get("status") or "new")
                if status not in active_statuses:
                    continue
                rows.append({
                    "Request ID": record.get("id") or "Not available",
                    "Renter": detail.get("client_name") or "Not available",
                    "Email": detail.get("client_email") or "Not available",
                    "Property ID": detail.get("listing_id") or "Not available",
                    "Status": status.replace("_", " ").title(),
                    "Requested": _format_registration_date(record.get("created_at") or detail.get("created_at")),
                    "Message": detail.get("message") or "",
                })
            _show_dashboard_detail_rows(rows, "There are no active rental requests.")
        elif card_index == 7:
            st.info("Bookings are not tracked by this application. Rental enquiries are available under Booking & Rental Requests.")
        elif card_index == 8:
            tickets = _request(support_tickets, None)
            if tickets is None:
                return
            rows = [{
                "Ticket ID": ticket.get("ticket_id") or "Not available",
                "Subject": ticket.get("subject") or "Not available",
                "User": ticket.get("user_name") or "Not available",
                "Role": str(ticket.get("user_role") or "Not available").replace("_", " ").title(),
                "Priority": str(ticket.get("priority") or "Not available").title(),
                "Status": str(ticket.get("status") or "Not available").replace("_", " ").title(),
                "Created": _format_registration_date(ticket.get("created_at")),
                "Description": ticket.get("description") or "",
            } for ticket in tickets if ticket.get("status") in {"open", "in_progress"}]
            _show_dashboard_detail_rows(rows, "There are no open support requests.")
        else:
            predictions = _request(admin_predictions, None)
            if predictions is None:
                return
            rows = [{
                "Prediction ID": item.get("prediction_id") or "Not available",
                "Admin": item.get("actor_name") or "Not available",
                "Predicted monthly rent": item.get("predicted_rent", "Not available"),
                "Recorded": _format_registration_date(item.get("created_at")),
            } for item in predictions]
            _show_dashboard_detail_rows(rows, "No recorded ML predictions are currently available.")


def _show_dashboard_detail_rows(rows: list[dict], empty_message: str) -> None:
    if rows:
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    else:
        st.info(empty_message)


def _owner_table_style(frame: pd.DataFrame):
    """Apply the Owner Management table palette without affecting other roles."""
    styled = frame.style.apply(
        lambda row: [
            "background-color: #FFFAF5; color: #1F2937; border-color: #FED7AA;"
            if row.name % 2 == 0
            else "background-color: #FFFFFF; color: #1F2937; border-color: #FED7AA;"
        ] * len(row),
        axis=1,
    )
    for status_column in ("Listing status", "Approval status", "Account Status"):
        if status_column in frame.columns:
            styled = styled.apply(
                lambda values: [
                    "color: #166534; font-weight: 700;" if str(value).casefold() in {"approved", "active"}
                    else "color: #991b1b; font-weight: 700;" if str(value).casefold() in {"rejected", "deactivated", "suspended"}
                    else "color: #9a3412; font-weight: 700;" if str(value).casefold() in {"pending", "inactive"}
                    else "color: #1f2937;"
                    for value in values
                ],
                subset=[status_column],
            )
    return styled.set_table_styles([
        {"selector": "table", "props": [("border-collapse", "separate"), ("border-spacing", "0"), ("border", "1px solid #FED7AA"), ("border-radius", "10px"), ("overflow", "hidden"), ("min-width", "100%")]},
        {"selector": "thead th", "props": [("background-color", "#FFEDD5"), ("color", "#9A3412"), ("font-weight", "700"), ("font-size", ".88rem"), ("padding", "12px 14px"), ("border-color", "#FED7AA"), ("text-align", "left"), ("white-space", "nowrap")]},
        {"selector": "tbody td", "props": [("color", "#1F2937"), ("font-size", ".88rem"), ("padding", "12px 14px"), ("border-color", "#FED7AA"), ("vertical-align", "middle"), ("white-space", "nowrap")]},
        {"selector": "tbody tr:hover td", "props": [("background-color", "#FFE8CC !important")]},
    ], overwrite=False)


def render_users(role_filter: str | None = None) -> None:
    title = f"{role_filter.title()} Management" if role_filter else "User Management"
    st.title(title)
    users = _request(admin_users, []) or []
    if role_filter == "owner":
        users = [user for user in users if user.get("role") == role_filter]
    elif role_filter == "renter":
        # This page defaults to renters but keeps the role filter useful for
        # comparing renter and owner accounts without exposing Admin roles.
        users = [user for user in users if user.get("role") in {"owner", "renter"}]
    search_col, role_col = st.columns([2, 1])
    search = search_col.text_input("Search by name, email, or user ID", key=f"admin_user_search_{role_filter or 'all'}").strip().casefold()
    roles = ["All", "owner", "renter"]
    role_options = roles if role_filter in {None, "renter"} else [role_filter]
    role_default = role_options.index(role_filter) if role_filter in role_options else 0
    selected_role = role_col.selectbox("Role", role_options, index=role_default, key=f"admin_user_role_filter_{role_filter or 'all'}")
    filtered = [user for user in users if
                (selected_role == "All" or user.get("role") == selected_role)
                and (not search or search in " ".join(str(user.get(key, "")) for key in ("uid", "name", "email", "role")).casefold())]
    if not filtered:
        st.info("No matching user accounts.")
        return
    safe_rows = [{key: user.get(key, "") for key in ("uid", "name", "email", "role", "account_status", "created_at")} for user in filtered]
    user_table = pd.DataFrame(safe_rows).rename(columns={
        "uid": "User ID", "name": "Full name", "email": "Email", "role": "Role",
        "account_status": "Account status", "created_at": "Registered",
    })
    if role_filter is None:
        styled_user_table = (
            user_table.style
            .apply(
                lambda row: [
                    "background-color: #FFF0E5; color: #17243A; border-color: #F5C6A5;"
                    if row.name % 2 == 0
                    else "background-color: #FFFFFF; color: #17243A; border-color: #F5C6A5;"
                ] * len(row),
                axis=1,
            )
            .apply_index(
                lambda headers: [
                    "background-color: #F97316; color: #FFFFFF; font-weight: 700; border-color: #F5C6A5;"
                ] * len(headers),
                axis=1,
            )
        )
        st.dataframe(
            styled_user_table,
            width="stretch",
            hide_index=True,
            key="admin_user_management_table",
        )
    elif role_filter == "owner":
        owner_user_table = user_table.rename(columns={
            "Full name": "Full Name", "Account status": "Account Status",
        }).copy()
        owner_user_table["Registered"] = owner_user_table["Registered"].map(_format_registration_date)
        with st.container(border=True, key="admin_owner_user_list_panel"):
            st.dataframe(
                _owner_table_style(owner_user_table), width="stretch", hide_index=True,
                key="admin_owner_management_table",
            )
    else:
        st.dataframe(user_table, width="stretch", hide_index=True)
    selected = st.selectbox("View user details", filtered, format_func=lambda user: f"{user.get('name') or 'Unnamed user'} · {user.get('email') or 'No email'}", key=f"admin_user_detail_{role_filter or 'all'}")
    if role_filter is None:
        st.subheader("User Details")
        details = (
            ("Name", selected.get("name")),
            ("Email", selected.get("email")),
            ("User ID", selected.get("uid")),
            ("Role", str(selected.get("role") or "Not available").replace("_", " ").title()),
            ("Account Status", str(selected.get("account_status") or "Not available").replace("_", " ").title()),
            ("Registration Date", _format_registration_date(selected.get("created_at"))),
        )
        with st.container(
            border=True,
            key="admin_user_management_user_details" if role_filter is None else None,
        ):
            for label, raw_value in details:
                value = str(raw_value).strip() if raw_value is not None and str(raw_value).strip() else "Not available"
                label_col, value_col = st.columns([1, 3])
                label_col.markdown(f"**{label}**")
                if label == "Email" and value != "Not available":
                    value_col.markdown(f"[{escape(value)}](mailto:{quote(value, safe='@.+-_')})")
                else:
                    value_col.write(value)
    elif role_filter == "renter":
        full_name = str(selected.get("name") or "Not available").strip()
        email = str(selected.get("email") or "").strip()
        user_id = str(selected.get("uid") or "Not available").strip()
        account_role = str(selected.get("role") or "Not available").replace("_", " ").title()
        account_status = str(selected.get("account_status") or "Not available").replace("_", " ").title()
        registered = _format_registration_date(selected.get("created_at"))
        badge_background, badge_color = {
            "active": ("#ecfdf5", "#047857"),
            "deactivated": ("#fef2f2", "#b91c1c"),
        }.get(str(selected.get("account_status") or "").casefold(), ("#f1f5f9", "#475569"))
        with st.container(border=True, key="admin_renter_user_details"):
            name_col, status_col = st.columns([4, 1], vertical_alignment="center")
            name_col.markdown(f"### {escape(full_name)}")
            if email:
                name_col.markdown(f"[{escape(email)}](mailto:{quote(email, safe='@.+-_')})")
            else:
                name_col.caption("Email address not available")
            status_col.markdown(
                "<div style='text-align:right'><span style='display:inline-block;padding:.3rem .65rem;"
                f"border-radius:999px;background:{badge_background};color:{badge_color};font-weight:700;font-size:.8rem'>"
                f"{escape(account_status)}</span></div>",
                unsafe_allow_html=True,
            )
            st.divider()
            detail_fields = (
                ("Full Name", full_name),
                ("Email Address", email or "Not available"),
                ("User ID", user_id),
                ("Account Role", account_role),
                ("Account Status", account_status),
                ("Registration Date", registered),
            )
            for start in range(0, len(detail_fields), 2):
                columns = st.columns(2)
                for column, (label, value) in zip(columns, detail_fields[start:start + 2]):
                    column.caption(label)
                    if label == "User ID":
                        column.markdown(
                            f"<div class='rentai-admin-user-id'>{escape(value)}</div>",
                            unsafe_allow_html=True,
                        )
                    elif label == "Email Address" and value != "Not available":
                        column.markdown(f"[{escape(value)}](mailto:{quote(value, safe='@.+-_')})")
                    else:
                        column.write(value)
    else:
        full_name = str(selected.get("name") or "Not available").strip()
        email = str(selected.get("email") or "").strip()
        user_id = str(selected.get("uid") or "Not available").strip()
        account_role = str(selected.get("role") or "Not available").replace("_", " ").title()
        account_status = str(selected.get("account_status") or "Not available").replace("_", " ").title()
        registered = _format_registration_date(selected.get("created_at"))
        badge_background, badge_color = {
            "active": ("#ecfdf5", "#047857"),
            "deactivated": ("#fef2f2", "#b91c1c"),
        }.get(str(selected.get("account_status") or "").casefold(), ("#f1f5f9", "#475569"))
        with st.container(border=True, key="admin_owner_user_details"):
            name_col, status_col = st.columns([4, 1], vertical_alignment="center")
            name_col.markdown(f"### {escape(full_name)}")
            if email:
                name_col.markdown(f"[{escape(email)}](mailto:{quote(email, safe='@.+-_')})")
            else:
                name_col.caption("Email address not available")
            status_col.markdown(
                "<div style='text-align:right'><span style='display:inline-block;padding:.3rem .65rem;"
                f"border-radius:999px;background:{badge_background};color:{badge_color};font-weight:700;font-size:.8rem'>"
                f"{escape(account_status)}</span></div>",
                unsafe_allow_html=True,
            )
            st.divider()
            detail_fields = (
                ("Full Name", full_name),
                ("Email Address", email or "Not available"),
                ("User ID", user_id),
                ("Account Role", account_role),
                ("Account Status", account_status),
                ("Registration Date", registered),
            )
            for start in range(0, len(detail_fields), 2):
                columns = st.columns(2)
                for column, (label, value) in zip(columns, detail_fields[start:start + 2]):
                    column.caption(label)
                    if label == "User ID":
                        column.markdown(
                            f"<div class='rentai-admin-user-id'>{escape(value)}</div>",
                            unsafe_allow_html=True,
                        )
                    elif label == "Email Address" and value != "Not available":
                        column.markdown(f"[{escape(value)}](mailto:{quote(value, safe='@.+-_')})")
                    else:
                        column.write(value)
    st.caption("Password and password hash fields are never returned by the Admin API.")
    if selected.get("uid") == st.session_state.get("rentai_uid"):
        st.info("Your own Admin account cannot be deactivated.")
    status_button = st.button
    new_status = "deactivated" if selected.get("account_status") == "active" else "active"
    if status_button("Deactivate account" if new_status == "deactivated" else "Activate account", key=f"admin_toggle_status_{role_filter or 'all'}", disabled=selected.get("uid") == st.session_state.get("rentai_uid") and new_status != "active"):
        result = _request(lambda: (set_user_status(selected["uid"], new_status), True)[1], False)
        if result is not False:
            st.success(f"Account {new_status}.")
            st.rerun()
    if role_filter:
        if role_filter != "renter":
            st.subheader(f"{role_filter.title()} records")
        records = _request(lambda: admin_records(), []) or []
        if role_filter == "renter" and selected.get("role") == "renter":
            selected_uid = selected.get("uid")
            listings_by_id = {
                record.get("id"): record
                for record in records
                if record.get("kind") == "listing" and record.get("id")
            }
            renter_records = []
            for record in records:
                if record.get("kind") not in {"inquiry", "maintenance"}:
                    continue
                detail = record.get("data") if isinstance(record.get("data"), dict) else {}
                if record.get("owner_uid") != selected_uid and detail.get("client_uid") != selected_uid:
                    continue
                listing_id = detail.get("listing_id")
                listing = listings_by_id.get(listing_id, {})
                listing_data = listing.get("data") if isinstance(listing.get("data"), dict) else {}
                rent = listing_data.get("rent", detail.get("monthly_rent"))
                renter_records.append({
                    "Record type": "Rental enquiry" if record.get("kind") == "inquiry" else "Maintenance request",
                    "Property": listing_data.get("title") or detail.get("title") or ("Property listing" if listing_id else "Not linked"),
                    "Property ID": listing_id or "Not available",
                    "Status": str(detail.get("status") or "Not available").replace("_", " ").title(),
                    "Monthly rent": rent if rent is not None else "Not available",
                    "Request date": _format_registration_date(record.get("created_at") or detail.get("created_at")),
                    "_sort_date": record.get("created_at") or detail.get("created_at") or "",
                })
            renter_records.sort(key=lambda item: str(item.get("_sort_date") or ""), reverse=True)
            for record in renter_records:
                record.pop("_sort_date", None)
            st.subheader("Renter records")
            if renter_records:
                st.dataframe(pd.DataFrame(renter_records), width="stretch", hide_index=True)
            else:
                st.info("No rental records found for this renter.")
            st.caption("The current system stores renter enquiries and maintenance requests; it does not maintain formal booking or lease records.")
        elif role_filter == "renter":
            st.subheader("Renter records")
            st.info("Select a renter to view renter records.")
        else:
            approvals = _request(lambda: admin_approvals(), []) or []
            owner_listings = [
                record for record in records
                if record.get("kind") == "listing" and record.get("owner_uid") == selected.get("uid")
            ]
            approvals_by_property = {
                item.get("property_id"): item
                for item in approvals
                if item.get("property_id")
            }
            owner_rows = []
            for record in owner_listings:
                detail = record.get("data") if isinstance(record.get("data"), dict) else {}
                approval = approvals_by_property.get(record.get("id"), {})
                location = ", ".join(
                    str(value).strip()
                    for value in (detail.get("locality"), detail.get("city"))
                    if value is not None and str(value).strip()
                ) or "Not available"
                rent = detail.get("rent")
                owner_rows.append({
                    "Property": detail.get("title") or "Untitled property",
                    "Property ID": record.get("id") or "Not available",
                    "Location": location,
                    "Monthly rent": rent if rent is not None else "Not available",
                    "Listing status": str(detail.get("status") or "Not available").replace("_", " ").title(),
                    "Approval status": str(approval.get("status") or "Not available").replace("_", " ").title(),
                    "Submitted": _format_registration_date(record.get("created_at")),
                })
            st.markdown('<h3 class="rentai-owner-management-heading">Owner Records</h3>', unsafe_allow_html=True)
            if owner_rows:
                with st.container(border=True, key="admin_owner_records_panel"):
                    st.dataframe(
                        _owner_table_style(pd.DataFrame(owner_rows)), width="stretch", hide_index=True,
                        key="admin_owner_records_table",
                    )
            else:
                st.info("No property records found for this owner.")
        history = _request(admin_activity, []) or []
        user_activity = [item for item in history if item.get("actor_uid") == selected.get("uid")]
        if role_filter == "renter":
            user_activity.sort(key=_activity_sort_timestamp, reverse=True)
            st.subheader("Recent Activity")
            if user_activity:
                activity_rows = [{
                    "Time": _format_registration_date(item.get("created_at")),
                    "Action": str(item.get("action") or "Not available").replace("_", " ").title(),
                    "Target": str(item.get("target_type") or "Not available").replace("_", " ").title(),
                    "Target ID": item.get("target_id") or "Not available",
                } for item in user_activity]
                st.dataframe(pd.DataFrame(activity_rows), width="stretch", hide_index=True)
            else:
                st.info("No recent activity found.")
        else:
            user_activity.sort(key=_activity_sort_timestamp, reverse=True)
            st.markdown('<h3 class="rentai-owner-management-heading">Recent Activity</h3>', unsafe_allow_html=True)
            if user_activity:
                activity_rows = []
                for item in user_activity:
                    action = str(item.get("action") or "Activity").replace("_", " ").title()
                    target_type = str(item.get("target_type") or "").replace("_", " ").strip()
                    target_id = str(item.get("target_id") or "").strip()
                    description = " · ".join(part for part in (action, target_type, target_id) if part)
                    activity_rows.append({
                        "Time": _format_registration_date(item.get("created_at")),
                        "Activity": description or "Activity details unavailable",
                    })
                with st.container(border=True, key="admin_owner_recent_activity_panel"):
                    st.dataframe(
                        _owner_table_style(pd.DataFrame(activity_rows)), width="stretch", hide_index=True,
                        key="admin_owner_recent_activity_table",
                    )
            else:
                st.info("No recent activity found.")
        if role_filter == "owner":
            approval_history = [
                item for item in approvals
                if item.get("submitted_by_uid") == selected.get("uid")
            ]
            approval_history.sort(key=_activity_sort_timestamp, reverse=True)
            st.markdown('<h3 class="rentai-owner-management-heading">Property Approval History</h3>', unsafe_allow_html=True)
            if not approval_history:
                st.info("No property approval history found.")
            else:
                status_colors = {
                    "pending": ("#fff7ed", "#c2410c"),
                    "approved": ("#ecfdf5", "#047857"),
                    "rejected": ("#fef2f2", "#b91c1c"),
                }
                for item in approval_history:
                    property_data = item.get("property") if isinstance(item.get("property"), dict) else {}
                    status = str(item.get("status") or "Not available").casefold()
                    background, color = status_colors.get(status, ("#f1f5f9", "#475569"))
                    with st.container(
                        border=True,
                        key=f"admin_owner_approval_history_{item.get('request_id') or item.get('property_id') or 'record'}",
                    ):
                        title_col, badge_col = st.columns([4, 1], vertical_alignment="center")
                        title_col.markdown(f"**{escape(str(property_data.get('title') or 'Property submission'))}**")
                        badge_col.markdown(
                            "<div style='text-align:right'><span style='display:inline-block;padding:.3rem .65rem;"
                            f"border-radius:999px;background:{background};color:{color};font-weight:700;font-size:.8rem'>"
                            f"{escape(status.replace('_', ' ').title())}</span></div>",
                            unsafe_allow_html=True,
                        )
                        columns = st.columns(3)
                        columns[0].caption("Property ID")
                        columns[0].write(item.get("property_id") or "Not available")
                        columns[1].caption("Submitted")
                        columns[1].write(_format_registration_date(item.get("created_at")))
                        columns[2].caption("Reviewed")
                        columns[2].write(_format_registration_date(item.get("reviewed_at")))
                        notes = item.get("review_notes")
                        if notes and str(notes).strip():
                            st.caption("Review notes")
                            st.write(str(notes))


def render_approvals() -> None:
    st.title("Property Approval Requests")
    requests = _request(lambda: admin_approvals(), None)
    pending = [item for item in (requests or []) if item.get("status") == "pending"]
    reviewed = [item for item in (requests or []) if item.get("status") in {"approved", "rejected"}]

    def property_name(item: dict) -> str:
        prop = item.get("property") or {}
        return prop.get("title") or item.get("property_id") or "Not available"

    def property_location(item: dict) -> str:
        prop = item.get("property") or {}
        return ", ".join(str(value) for value in (prop.get("locality"), prop.get("city")) if value) or "Not available"

    def rent_label(item: dict) -> str:
        value = (item.get("property") or {}).get("rent")
        if not isinstance(value, (int, float)):
            return str(value or "Not available")
        amount = str(int(round(value)))
        if len(amount) > 3:
            prefix, last_three = amount[:-3], amount[-3:]
            groups = []
            while prefix:
                groups.insert(0, prefix[-2:])
                prefix = prefix[:-2]
            amount = ",".join(groups + [last_three])
        return f"₹{amount}"

    def show_section_title(title: str) -> None:
        st.markdown(f'<h3 class="rentai-approval-section-title">{escape(title)}</h3>', unsafe_allow_html=True)

    pending_widths = [2.0, 1.5, 1.7, 1.1, 1.5, 1.0, 2.1]
    show_section_title("Pending User-Submitted Property Approvals")
    with st.container(border=True, key="admin_approval_pending_table"):
        with st.container(key="admin_approval_pending_header"):
            header_columns = st.columns(pending_widths)
            for column, label in zip(header_columns, ["Property Name / ID", "Owner Name", "Location", "Monthly Rent", "Submitted Date", "Status", "Action"]):
                column.markdown(f"**{label}**")

        if requests is None:
            st.markdown('<div class="rentai-approval-empty">Approval requests could not be loaded.</div>', unsafe_allow_html=True)
        elif not pending:
            st.markdown('<div class="rentai-approval-empty">No pending user-submitted property approvals</div>', unsafe_allow_html=True)
        else:
            for item in pending:
                with st.container(key=f"admin_approval_pending_row_{item['property_id']}"):
                    columns = st.columns(pending_widths)
                    columns[0].write(property_name(item))
                    columns[1].write(item.get("submitter_name") or "Not available")
                    columns[2].write(property_location(item))
                    columns[3].write(rent_label(item))
                    columns[4].write(_format_registration_date(item.get("created_at")))
                    columns[5].markdown('<span class="rentai-approval-badge rentai-approval-pending">Pending</span>', unsafe_allow_html=True)
                    notes = columns[6].text_input(
                        "Review notes", key=f"approval_note_{item['property_id']}", label_visibility="collapsed",
                        placeholder="Review notes",
                    )
                    approve, reject = columns[6].columns(2)
                    if approve.button("Approve", key=f"approve_{item['property_id']}", type="primary"):
                        if _request(lambda: review_property(item["property_id"], "approved", notes), False) is not False:
                            st.success("Property approved and published.")
                            st.rerun()
                    if reject.button("Reject", key=f"reject_{item['property_id']}"):
                        if _request(lambda: review_property(item["property_id"], "rejected", notes), False) is not False:
                            st.success("Property submission rejected.")
                            st.rerun()

    show_section_title("Reviewed Requests")
    reviewed_headers = ["Property Name / ID", "Owner Name", "Location", "Monthly Rent", "Approval Status", "Reviewed Date", "Review Notes"]
    reviewed_rows = []
    for item in reviewed:
        status = str(item.get("status") or "").lower()
        status_class = "approved" if status == "approved" else "rejected" if status == "rejected" else "pending"
        status_label = status.title() if status else "Not available"
        reviewed_rows.append(
            "<tr>"
            f"<td>{escape(str(property_name(item)))}</td>"
            f"<td>{escape(str(item.get('submitter_name') or 'Not available'))}</td>"
            f"<td>{escape(property_location(item))}</td>"
            f"<td>{escape(rent_label(item))}</td>"
            f"<td><span class=\"rentai-approval-badge rentai-approval-{status_class}\">{escape(status_label)}</span></td>"
            f"<td>{escape(_format_registration_date(item.get('reviewed_at')))}</td>"
            f"<td>{escape(str(item.get('review_notes') or '—'))}</td>"
            "</tr>"
        )
    if requests is None:
        reviewed_body = f'<tr><td class="rentai-approval-empty-cell" colspan="{len(reviewed_headers)}">Reviewed requests could not be loaded.</td></tr>'
    elif not reviewed_rows:
        reviewed_body = f'<tr><td class="rentai-approval-empty-cell" colspan="{len(reviewed_headers)}">No reviewed requests found</td></tr>'
    else:
        reviewed_body = "".join(reviewed_rows)
    reviewed_table = (
        '<div class="rentai-approval-table-scroll">'
        '<table class="rentai-approval-table"><thead><tr>'
        + "".join(f"<th>{escape(label)}</th>" for label in reviewed_headers)
        + f"</tr></thead><tbody>{reviewed_body}</tbody></table></div>"
    )
    with st.container(border=True, key="admin_approval_reviewed_table"):
        st.markdown(reviewed_table, unsafe_allow_html=True)

def render_rental_requests() -> None:
    st.title("Booking & Rental Requests")
    st.caption("The existing system stores renter enquiries; formal bookings are not tracked yet.")
    requests = _request(lambda: admin_records("inquiry"), []) or []
    if not requests:
        st.info("No renter enquiries are recorded.")
        return
    filters = st.columns([2, 1])
    search = filters[0].text_input("Search rental requests", key="admin_request_search").strip().casefold()
    status_filter = filters[1].selectbox("Filter by status", ["All", "new", "contacted", "viewing_scheduled", "completed", "closed"], key="admin_request_filter")
    statuses = ["new", "contacted", "viewing_scheduled", "completed", "closed"]
    requests = [record for record in requests if
                (status_filter == "All" or (record.get("data") or {}).get("status", "new") == status_filter)
                and (not search or search in str(record.get("data", {})).casefold())]
    for record in requests:
        detail = record.get("data") or {}
        with st.container(border=True):
            st.write(f"**{detail.get('client_name','Renter')}** · {detail.get('client_email','')}")
            st.caption(f"Request {record.get('id')} · Listing {detail.get('listing_id','')} · {detail.get('created_at',record.get('created_at',''))}")
            st.write(detail.get("message", ""))
            current = detail.get("status", "new")
            selected = st.selectbox("Status", statuses, index=statuses.index(current) if current in statuses else 0, key=f"request_status_{record['id']}")
            note = st.text_input("Admin note", value=detail.get("admin_note", ""), key=f"request_note_{record['id']}")
            if st.button("Update request", key=f"update_request_{record['id']}"):
                if _request(lambda: update_rental_request(record["id"], selected, note), False) is not False:
                    st.success("Rental request updated.")
                    st.rerun()


def render_support(*, admin: bool) -> None:
    st.title("Support & Complaints")
    if admin:
        tickets = _request(support_tickets, []) or []
        if not tickets:
            st.info("No support tickets have been submitted.")
            return
        filters = st.columns([2, 1])
        search = filters[0].text_input("Search tickets", key="admin_ticket_search").strip().casefold()
        status_filter = filters[1].selectbox("Filter ticket status", ["All", "open", "in_progress", "resolved", "closed"], key="admin_ticket_filter")
        tickets = [ticket for ticket in tickets if
                   (status_filter == "All" or ticket.get("status") == status_filter)
                   and (not search or search in " ".join(str(ticket.get(key, "")) for key in ("ticket_id", "subject", "description", "user_name", "user_email")).casefold())]
        if not tickets:
            st.info("No support tickets match these filters.")
            return
        for ticket in tickets:
            with st.container(border=True):
                st.subheader(ticket.get("subject", "Support request"))
                st.caption(f"Ticket {ticket.get('ticket_id')} · {ticket.get('user_name')} ({ticket.get('user_role')}) · {ticket.get('priority')} · Created {ticket.get('created_at')} · Updated {ticket.get('updated_at')}")
                st.write(ticket.get("description", ""))
                statuses = ["open", "in_progress", "resolved", "closed"]
                current = ticket.get("status", "open")
                status = st.selectbox("Status", statuses, index=statuses.index(current) if current in statuses else 0, key=f"ticket_status_{ticket['ticket_id']}")
                response = st.text_area("Admin response", value=ticket.get("admin_response", ""), max_chars=4000, key=f"ticket_response_{ticket['ticket_id']}")
                if st.button("Save ticket", key=f"save_ticket_{ticket['ticket_id']}"):
                    if _request(lambda: update_support_ticket(ticket["ticket_id"], status, response), False) is not False:
                        st.success("Support ticket updated.")
                        st.rerun()
        return
    with st.form("rentai_support_ticket_form", clear_on_submit=True):
        subject = st.text_input("Subject", max_chars=160)
        description = st.text_area("Describe the issue", max_chars=4000)
        priority = st.selectbox("Priority", ["low", "normal", "high", "urgent"])
        submitted = st.form_submit_button("Submit support request", type="primary")
    if submitted:
        result = _request(lambda: create_support_ticket(subject.strip(), description.strip(), priority), None)
        if result:
            st.success("Support request submitted.")
    tickets = _request(support_tickets, []) or []
    st.subheader("My support requests")
    st.dataframe(pd.DataFrame([{
        "Subject": item.get("subject"), "Priority": item.get("priority"),
        "Status": item.get("status"), "Created": item.get("created_at"),
        "Admin response": item.get("admin_response", ""),
    } for item in tickets]), width="stretch", hide_index=True)


def render_analytics(data: pd.DataFrame) -> None:
    from frontend.pages.admin_analytics import render as render_analytics_page

    render_analytics_page(data)

def render_predictions(data: pd.DataFrame) -> None:
    st.title("ML Predictions")
    st.caption("Uses the existing CSV-trained rent prediction model. Tests are recorded for Admin review.")
    if not data.empty:
        cities = sorted(data["city"].dropna().astype(str).unique().tolist()) if "city" in data else []
        types = sorted(data["property_type"].dropna().astype(str).unique().tolist()) if "property_type" in data else []
        furnishing = sorted(data["furnishing_status"].dropna().astype(str).unique().tolist()) if "furnishing_status" in data else []
    else:
        cities, types, furnishing = [], [], []
    with st.expander("Model information", expanded=True):
        st.write("Model: existing CSV-trained rent regression model")
        st.write(f"Training source: rentai_dataset.csv ({len(data):,} rows loaded)")
        st.write("Training metrics: not currently persisted by the application.")
    with st.form("admin_ml_prediction"):
        first = st.columns(3)
        city = first[0].selectbox("City", cities) if cities else first[0].text_input("City")
        locality = first[1].text_input("Locality")
        property_type = first[2].selectbox("Property type", types) if types else first[2].text_input("Property type")
        second = st.columns(3)
        furnishing_status = second[0].selectbox("Furnishing", furnishing) if furnishing else second[0].text_input("Furnishing")
        area = second[1].number_input("Area (sq ft)", min_value=1.0, value=800.0)
        bedrooms = second[2].number_input("Bedrooms", min_value=0, value=2, step=1)
        third = st.columns(3)
        bathrooms = third[0].number_input("Bathrooms", min_value=0, value=1, step=1)
        floor = third[1].number_input("Floor", min_value=0, value=1, step=1)
        distance = third[2].number_input("Distance to city center (km)", min_value=0.0, value=5.0)
        fourth = st.columns(2)
        age = fourth[0].number_input("Property age (years)", min_value=0, value=5, step=1)
        parking = fourth[1].number_input("Parking spaces", min_value=0, value=1, step=1)
        submit = st.form_submit_button("Test rent prediction", type="primary")
    if submit:
        features = {"city": city, "locality": locality, "property_type": property_type, "furnishing_status": furnishing_status, "area_sqft": area, "bedrooms": bedrooms, "bathrooms": bathrooms, "floor": floor, "distance_to_city_center_km": distance, "age_of_property": age, "parking": parking}
        try:
            validated = RentPredictionRequest.model_validate(features).model_dump()
            result = admin_test_prediction(validated)
            st.success(f"Estimated monthly rent: ₹{result['predicted_rent']:,.0f}")
        except Exception as exc:
            st.error(str(exc))
    history = _request(admin_predictions, []) or []
    st.subheader("Recorded prediction history")
    st.dataframe(pd.DataFrame([{
        "Date": item.get("created_at"), "Admin": item.get("actor_name"),
        "Inputs": item.get("inputs"), "Predicted monthly rent": item.get("predicted_rent"),
    } for item in history]), width="stretch", hide_index=True)


def render_user_support(_data=None) -> None:
    render_support(admin=False)


def render_support_page(_data=None) -> None:
    render_support(admin=st.session_state.get("rentai_role") == "admin")
=======
"""Dataset administration and downloadable status report."""

from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from frontend.components.data_utils import numeric_column, page_heading
from frontend.application_api import ApplicationAPIError, admin_users, set_user_role


def _report_text(data: pd.DataFrame) -> str:
	lines = [
		"RentAI Dataset & System Report",
		f"Generated at (UTC): {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
		f"Dataset rows: {len(data):,}",
		f"Dataset columns: {len(data.columns):,}",
		f"Duplicate rows: {int(data.duplicated().sum()):,}" if not data.empty else "Duplicate rows: 0",
		f"Missing values: {int(data.isna().sum().sum()):,}" if not data.empty else "Missing values: 0",
		"ML APIs: Prediction, classification, and clustering routes are registered; runtime health is not sampled here.",
		"Dataset storage: CSV; account roles and application records: SQLite",
	]
	rents = numeric_column(data, "rent")
	lines.append(f"Average rent: {rents.mean():,.2f}" if not rents.empty else "Average rent: unavailable")
	return "\n".join(lines) + "\n"


def render(data: pd.DataFrame) -> None:
	page_heading("Admin Dashboard", "Review system statistics, users, and the active CSV dataset.")
	rows_col, columns_col, missing_col, duplicates_col = st.columns(4)
	rows_col.metric("Dataset rows", f"{len(data):,}")
	columns_col.metric("Dataset columns", f"{len(data.columns):,}")
	missing_col.metric("Missing values", f"{int(data.isna().sum().sum()):,}" if not data.empty else "0")
	duplicates_col.metric("Duplicate rows", f"{int(data.duplicated().sum()):,}" if not data.empty else "0")

	status_col, report_col = st.columns(2)
	with status_col:
		with st.container(border=True):
			st.subheader("System status")
			st.markdown("**Streamlit UI** · Available")
			st.markdown("**Dataset** · " + ("Loaded" if not data.empty else "Empty or unavailable"))
			st.markdown("**ML services** · Prediction, classification, and clustering routes are implemented; runtime health is not sampled here")
			st.markdown("**Application records** · SQLite-backed")
	with report_col:
		with st.container(border=True):
			st.subheader("Report generation")
			st.write("Download a text summary of the current CSV and system status.")
			st.download_button("Download report", _report_text(data), file_name="rentai_report.txt", mime="text/plain", type="primary", width="stretch")

	st.subheader("Model statistics")
	st.info("No model training runs, metrics, or prediction history are recorded.")
	if not data.empty:
		with st.expander("Dataset schema"):
			st.dataframe(pd.DataFrame({"Column": data.columns, "Data type": data.dtypes.astype(str).values, "Missing": data.isna().sum().values}), width="stretch", hide_index=True)

	st.subheader("User management")
	try:
		users = admin_users()
		st.metric("Registered role records", len(users))
		if users:
			st.dataframe(pd.DataFrame(users), use_container_width=True, hide_index=True)
			with st.form("admin_change_user_role"):
				uid = st.selectbox("Account UID", [user["uid"] for user in users])
				new_role = st.selectbox("Assign role", ["owner", "renter", "broker"])
				change = st.form_submit_button("Save role")
			if change:
				set_user_role(uid, new_role)
				st.success("Role updated.")
				st.rerun()
	except ApplicationAPIError as exc:
		st.error(str(exc))
>>>>>>> origin/development
