from pathlib import Path

import streamlit as st

from backend.core.config import startup_configuration_issues
from frontend.components.sidebar import render_sidebar
from frontend.components.owner_sidebar import render_owner_sidebar
from frontend.components.renter_sidebar import render_renter_sidebar
from frontend.components.chatbot import render as render_chatbot
from frontend.authentication import (
    AuthenticationError,
    accessible_pages,
    clear_auth_session,
    current_session_user,
)
from frontend.data_loader import load_dataset
from frontend.pages import admin_reports, algo_lab, analytics, clustering, eda, home, implementation, insights, learning, property_comparison, property_search, rent_classification, rent_prediction
from frontend.pages.login import render as render_login
from frontend.pages.register import render as render_register
from frontend.pages.role_landing import PUBLIC_ROLES, render as render_role_landing
from frontend.pages import broker_dashboard, owner_dashboard, owner_sections, renter_dashboard, renter_workspace


PAGE_RENDERERS = {
    "Home": home.render,
    "Learning Module": learning.render,
    "Implementation Overview": implementation.render,
    "Dataset & EDA": eda.render,
    "Rent Prediction": rent_prediction.render,
    "Rent Classification": rent_classification.render,
    "Property Clustering": clustering.render,
    "Property Search": property_search.render,
    "Property Comparison": property_comparison.render,
    "Location & Analytics": analytics.render,
    "Property Insights": insights.render,
    "Algo Lab": algo_lab.render,
    "Admin & Reports": admin_reports.render,
}

DASHBOARD_RENDERERS = {
    "Admin Dashboard": admin_reports.render,
    "Owner Dashboard": owner_dashboard.render,
    "Renter Dashboard": renter_dashboard.render,
    "Broker Dashboard": broker_dashboard.render,
}

OWNER_SECTION_RENDERERS = {
    "Dashboard": owner_dashboard.render,
    "My Properties": owner_sections.render_properties,
    "Add New Property": owner_sections.render_add_property,
    "Applications & Enquiries": owner_sections.render_enquiries,
    "Booking Requests": owner_sections.render_booking_requests,
    "Renter List": owner_sections.render_renters,
    "Property Analytics": owner_sections.render_analytics,
    "Revenue & Rent Collection": owner_sections.render_revenue,
    "Maintenance Requests": owner_sections.render_maintenance,
    "My Profile": owner_sections.render_profile,
}

RENTER_SECTION_RENDERERS = {
    "Dashboard": renter_dashboard.render,
    "Search Properties": renter_workspace.render_search,
    "AI Rent Prediction": renter_workspace.render_prediction,
    "Compare Properties": renter_workspace.render_compare,
    "Saved Properties": renter_workspace.render_saved,
    "My Enquiries": renter_workspace.render_enquiries,
    "My Bookings": renter_workspace.render_bookings,
    "Pay Rent": renter_workspace.render_rent_payments,
    "My Profile": renter_workspace.render_profile,
}


def render_theme() -> None:
    theme_path = Path(__file__).parent / "frontend" / "styles" / "theme.css"
    st.markdown(f"<style>{theme_path.read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)


st.set_page_config(
    page_title="RentAI | Rental Intelligence",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="expanded",
)

configuration_issues = startup_configuration_issues()
if configuration_issues:
    st.warning(
        "RentAI setup needs attention: "
        + ", ".join(configuration_issues)
        + ". Values are read from the local .env file or process environment."
    )

render_theme()
try:
    signed_in_user = current_session_user(st.session_state)
except AuthenticationError as exc:
    clear_auth_session(st.session_state, clear_login_inputs=True)
    st.warning(str(exc))
    signed_in_user = None

if signed_in_user is None:
    selected_role = st.session_state.get("rentai_selected_role")
    auth_view = st.session_state.get("rentai_auth_view", "signin")
    if selected_role not in {"admin", *PUBLIC_ROLES}:
        st.session_state.pop("rentai_selected_role", None)
        st.session_state.pop("rentai_auth_view", None)
        render_role_landing()
    elif auth_view == "register" and selected_role in PUBLIC_ROLES:
        render_register(selected_role)
    else:
        if auth_view not in {"signin", "register"}:
            st.session_state["rentai_auth_view"] = "signin"
        render_login(selected_role=selected_role, allow_registration=selected_role in PUBLIC_ROLES)
else:
    role = signed_in_user["role"]
    if st.session_state.pop("rentai_registration_success", False):
        st.success("Your RentAI account is ready.")
    allowed_pages = accessible_pages(role, [*PAGE_RENDERERS, *DASHBOARD_RENDERERS])
    if role == "renter":
        # These tools now live inside the persistent Renter workspace sidebar.
        # Keep the standalone routes from replacing that dedicated navigation.
        allowed_pages = [page for page in allowed_pages if page not in {
            "Rent Prediction", "Property Search", "Property Comparison",
        }]
    if st.session_state.get("page") not in allowed_pages:
        st.session_state.page = {
            "owner": "Owner Dashboard", "renter": "Renter Dashboard",
            "broker": "Broker Dashboard", "admin": "Admin Dashboard",
        }[role]
    data = load_dataset()
    if role == "owner" and st.session_state.page == "Owner Dashboard":
        render_owner_sidebar(
            on_logout=lambda: clear_auth_session(st.session_state, clear_login_inputs=True)
        )
        selected_owner_page = st.session_state.get("owner_active_page", "Dashboard")
        OWNER_SECTION_RENDERERS.get(selected_owner_page, owner_dashboard.render)(data)
    elif role == "renter" and st.session_state.page == "Renter Dashboard":
        render_renter_sidebar(
            on_logout=lambda: clear_auth_session(st.session_state, clear_login_inputs=True)
        )
        selected_renter_page = st.session_state.get("renter_active_page", "Dashboard")
        RENTER_SECTION_RENDERERS.get(selected_renter_page, renter_dashboard.render)(data)
    else:
        render_sidebar(
            data,
            role=role,
            allowed_pages=allowed_pages,
            on_logout=lambda: clear_auth_session(st.session_state, clear_login_inputs=True),
        )
        renderer = PAGE_RENDERERS.get(st.session_state.page) or DASHBOARD_RENDERERS[st.session_state.page]
        renderer(data)
    active_chat_page = st.session_state.page
    if st.session_state.page == "Owner Dashboard":
        active_chat_page = st.session_state.get("owner_active_page", "Dashboard")
    elif st.session_state.page == "Renter Dashboard":
        active_chat_page = st.session_state.get("renter_active_page", "Dashboard")
    render_chatbot(role, signed_in_user["uid"], active_chat_page)
