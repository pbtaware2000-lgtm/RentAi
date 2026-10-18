"""Presentation functions for secondary Owner workspace sections."""

import pandas as pd
import streamlit as st
from datetime import date
import math
from pydantic import ValidationError

from backend.schemas.prediction_schema import RentPredictionRequest
from backend.services.prediction_service import predict_rent
from frontend.application_api import ApplicationAPIError, create, delete, property_approval_history, records, update
from frontend.components.data_utils import category_options
from frontend.pages.owner_support import load_or_report, money, owner_name, rows


PROPERTY_TYPES = ["Apartment", "House", "Villa", "Studio", "Other"]


def _property_options(listings: list[dict]):
    return listings or []


def _property_label(record: dict) -> str:
    data = record.get("data") or {}
    return f"{data.get('title', 'Property')} · {data.get('locality', '')} · {record.get('id', '')[:8]}"


def render_add_property(data=None) -> None:
    st.title("Add New Property")
    role = st.session_state.get("rentai_role", "owner")
    st.caption("Submit a property for Admin approval or save it as a draft.")
    with st.container(border=True):
        with st.form("owner_workspace_add_property", clear_on_submit=False):
            title = st.text_input("Property title *", placeholder="e.g. Sunny 2 bedroom apartment", key="owner_add_title")
            first, second, third = st.columns(3)
            city = first.text_input("City *", key="owner_add_city")
            locality = second.text_input("Locality *", key="owner_add_locality")
            property_type = third.selectbox("Property type", PROPERTY_TYPES, key="owner_add_property_type")
            rent_col, bed_col, occupancy_col = st.columns(3)
            rent = rent_col.number_input("Monthly asking rent (₹) *", min_value=1, step=1000, key="owner_add_asking_rent")
            bedrooms = bed_col.number_input("Bedrooms", min_value=1, max_value=30, step=1, key="owner_add_bedrooms")
            occupancy = occupancy_col.selectbox("Occupancy", ["vacant", "occupied", "not_tracked"], format_func=lambda value: {"vacant": "Vacant", "occupied": "Occupied", "not_tracked": "Not tracked"}[value], key="owner_add_occupancy")

            features_first, features_second, features_third = st.columns(3)
            area_sqft = features_first.number_input("Area (sq ft) for estimate", min_value=0.0, value=0.0, step=50.0, key="owner_add_area_sqft")
            bathrooms = features_second.number_input("Bathrooms for estimate", min_value=0, value=0, step=1, key="owner_add_bathrooms")
            floor = features_third.number_input("Floor for estimate", min_value=0, value=0, step=1, key="owner_add_floor")

            furnishing_options = category_options(data, "furnishing_status") if isinstance(data, pd.DataFrame) else []
            model_first, model_second, model_third = st.columns(3)
            if furnishing_options:
                furnishing_status = model_first.selectbox("Furnishing for estimate", furnishing_options, key="owner_add_furnishing")
            else:
                furnishing_status = model_first.text_input("Furnishing for estimate", key="owner_add_furnishing")
            distance = model_second.number_input("Distance to city center (km)", min_value=0.0, value=0.0, step=0.5, key="owner_add_distance")
            age = model_third.number_input("Property age (years)", min_value=0, value=0, step=1, key="owner_add_age")
            parking = st.number_input("Parking spaces for estimate", min_value=0, value=0, step=1, key="owner_add_parking")
            st.caption("An optional ML estimate is separate from your monthly asking rent and will not change it.")
            description = st.text_area("Description", max_chars=5000, key="owner_add_description")
            status = st.radio("Listing status", ["published", "draft"], horizontal=True, format_func=lambda value: "Submit for approval" if value == "published" else "Keep as draft", key="owner_add_status")
            estimate_col, save_col = st.columns(2)
            estimate_clicked = estimate_col.form_submit_button("Estimate monthly rent", key="owner_estimate_rent", width="stretch")
            submitted = save_col.form_submit_button("Save property", key="owner_save_property", type="primary", width="stretch")

        prediction_features = {
            "city": city,
            "locality": locality,
            "property_type": property_type,
            "furnishing_status": furnishing_status,
            "area_sqft": area_sqft,
            "bedrooms": bedrooms,
            "bathrooms": bathrooms,
            "floor": floor,
            "distance_to_city_center_km": distance,
            "age_of_property": age,
            "parking": parking,
        }
        if estimate_clicked:
            st.session_state.pop("owner_rent_estimate", None)
            try:
                validated_features = RentPredictionRequest.model_validate(prediction_features).model_dump()
                predicted_rent = predict_rent(validated_features)
                if not math.isfinite(predicted_rent) or predicted_rent <= 0:
                    raise ValueError("The model did not produce a positive monthly estimate.")
                st.session_state["owner_rent_estimate"] = {
                    "features": validated_features,
                    "asking_rent": rent,
                    "predicted_rent": predicted_rent,
                }
            except (ValidationError, FileNotFoundError, ValueError) as exc:
                st.error(f"Could not estimate rent: {exc}")

        if submitted:
            if not title.strip() or not city.strip() or not locality.strip():
                st.error("Complete the title, city, and locality fields.")
                return
            try:
                create("listing", {
                    "title": title.strip(), "city": city.strip(), "locality": locality.strip(),
                    "property_type": property_type, "rent": rent, "bedrooms": bedrooms,
                    "bathrooms": int(bathrooms), "area_sqft": float(area_sqft), "floor": int(floor),
                    "furnishing_status": furnishing_status, "parking": int(parking),
                    "occupancy_status": occupancy, "description": description.strip(), "status": status,
                })
                st.success("Property submitted for Admin approval." if status == "published" else "Property saved as a draft.")
                st.session_state.pop("owner_rent_estimate", None)
                if role == "owner":
                    st.session_state.owner_active_page = "My Properties"
                else:
                    st.session_state.page = "My Broker Listings"
                st.rerun()
            except ApplicationAPIError as exc:
                st.error(str(exc))

        estimate = st.session_state.get("owner_rent_estimate")
        if estimate:
            try:
                current_features = RentPredictionRequest.model_validate(prediction_features).model_dump()
            except ValidationError:
                current_features = None
            if estimate["features"] != current_features:
                st.session_state.pop("owner_rent_estimate", None)
            else:
                asking_col, predicted_col = st.columns(2)
                asking_col.metric("Monthly asking rent", f"₹{estimate['asking_rent']:,.0f} INR")
                predicted_col.metric("ML-predicted monthly rent", f"₹{estimate['predicted_rent']:,.0f} INR")
                st.caption("The estimate is informational only. Saving this property uses the asking rent above.")


def render_properties(_data=None) -> None:
    st.title("My Properties")
    st.caption("Only listings owned by your account are shown here.")
    try:
        listings = records("listing")
        approvals = property_approval_history()
    except ApplicationAPIError as exc:
        st.error(str(exc))
        return
    if not listings:
        st.info("Your portfolio is empty. Add your first property to get started.")
        if st.button("Add a property", type="primary"):
            st.session_state.owner_active_page = "Add New Property"
            st.rerun()
        return
    st.dataframe(rows(listings), width="stretch", hide_index=True)
    decision_rows = [{
        "Property": (item.get("property") or {}).get("title", item.get("property_id")),
        "Status": item.get("status"), "Review notes": item.get("review_notes", ""),
        "Reviewed at": item.get("reviewed_at", ""),
    } for item in approvals]
    if decision_rows:
        st.subheader("Admin review decisions")
        st.dataframe(pd.DataFrame(decision_rows), width="stretch", hide_index=True)
    selected = st.selectbox("Edit a property", listings, format_func=_property_label, key="owner_property_edit_selector")
    current = selected.get("data") or {}
    with st.form("owner_workspace_edit_property"):
        title = st.text_input("Title", value=current.get("title", ""))
        cols = st.columns(3)
        city = cols[0].text_input("City", value=current.get("city", ""))
        locality = cols[1].text_input("Locality", value=current.get("locality", ""))
        type_index = PROPERTY_TYPES.index(current["property_type"]) if current.get("property_type") in PROPERTY_TYPES else len(PROPERTY_TYPES) - 1
        property_type = cols[2].selectbox("Property type", PROPERTY_TYPES, index=type_index)
        cols2 = st.columns(3)
        rent = cols2[0].number_input("Monthly asking rent (₹)", min_value=1, value=max(1, int(current.get("rent", 1) or 1)), step=1000)
        bedrooms = cols2[1].number_input("Bedrooms", min_value=1, max_value=30, value=min(30, max(1, int(current.get("bedrooms", 1) or 1))))
        occupancy_values = ["vacant", "occupied", "not_tracked"]
        occupancy = cols2[2].selectbox("Occupancy", occupancy_values, index=occupancy_values.index(current.get("occupancy_status", "not_tracked")) if current.get("occupancy_status", "not_tracked") in occupancy_values else 2, format_func=lambda value: {"vacant": "Vacant", "occupied": "Occupied", "not_tracked": "Not tracked"}[value])
        description = st.text_area("Description", value=current.get("description", ""), max_chars=5000)
        status_choices = ["draft", "published"]
        if current.get("status") not in status_choices:
            status_choices.append(current.get("status"))
        listing_status = st.selectbox("Listing status", status_choices, index=status_choices.index(current.get("status", "draft")), format_func=lambda value: {"published": "Approved and listed", "pending": "Pending Admin approval", "rejected": "Rejected", "inactive": "Inactive", "draft": "Draft"}.get(value, value.title()))
        submitted = st.form_submit_button("Save changes", type="primary")
    if submitted:
        try:
            update(selected, {"title": title.strip(), "city": city.strip(), "locality": locality.strip(), "property_type": property_type, "rent": rent, "bedrooms": bedrooms, "occupancy_status": occupancy, "description": description.strip(), "status": listing_status})
            st.success("Property updated.")
            st.rerun()
        except ApplicationAPIError as exc:
            st.error(str(exc))
    if st.button("Delete this property", key=f"delete_owner_property_{selected['id']}"):
        try:
            delete(selected)
            st.success("Property removed from your portfolio.")
            st.rerun()
        except ApplicationAPIError as exc:
            st.error(str(exc))


def render_broker_listings(_data=None) -> None:
    st.title("My Broker Listings")
    st.caption("Listings you submitted or manage, with their Admin review status.")
    try:
        listings = records("listing")
        approvals = property_approval_history()
    except ApplicationAPIError as exc:
        st.error(str(exc))
        return
    if not listings:
        st.info("You have not submitted any listings yet.")
        return
    st.dataframe(rows(listings), width="stretch", hide_index=True)
    st.subheader("Admin review decisions")
    st.dataframe(pd.DataFrame([{
        "Property": (item.get("property") or {}).get("title", item.get("property_id")),
        "Status": item.get("status"), "Review notes": item.get("review_notes", ""),
        "Reviewed at": item.get("reviewed_at", ""),
    } for item in approvals]), width="stretch", hide_index=True)


def render_enquiries(_data=None) -> None:
    st.title("Applications & Enquiries")
    st.caption("This workspace currently stores renter enquiries. Formal applications are not part of the database yet.")
    try:
        enquiries = records("inquiry")
        listings = {record["id"]: record.get("data", {}).get("title", "Property") for record in records("listing")}
    except ApplicationAPIError as exc:
        st.error(str(exc))
        return
    new_count = sum((record.get("data") or {}).get("status", "new") == "new" for record in enquiries)
    a, b, c = st.columns(3)
    a.metric("All enquiries", len(enquiries))
    b.metric("New", new_count)
    c.metric("Formal applications", "Not tracked")
    if not enquiries:
        st.info("Renter enquiries about your listings will appear here.")
        return
    view = []
    for record in enquiries:
        detail = record.get("data") or {}
        view.append({"Renter": detail.get("client_name", "Renter"), "Email": detail.get("client_email", ""), "Phone": detail.get("client_phone", ""), "Property": listings.get(detail.get("listing_id"), "Property"), "Message": detail.get("message", ""), "Status": detail.get("status", "new")})
    st.dataframe(pd.DataFrame(view), width="stretch", hide_index=True)
    selected = st.selectbox("Review enquiry", enquiries, format_func=lambda item: f"{(item.get('data') or {}).get('client_name','Renter')} · {listings.get((item.get('data') or {}).get('listing_id'), 'Property')}", key="owner_enquiry_review")
    detail = selected.get("data") or {}
    statuses = ["new", "contacted", "viewing_scheduled", "completed", "closed"]
    with st.form("owner_update_enquiry"):
        status = st.selectbox("Request status", statuses, index=statuses.index(detail.get("status", "new")) if detail.get("status", "new") in statuses else 0)
        note = st.text_area("Owner response", value=detail.get("owner_note", ""), max_chars=2000)
        submitted = st.form_submit_button("Update enquiry")
    if submitted:
        try:
            update(selected, {**detail, "status": status, "owner_note": note.strip()})
            st.success("Enquiry updated.")
            st.rerun()
        except ApplicationAPIError as exc:
            st.error(str(exc))


def render_renters(_data=None) -> None:
    st.title("Renter List")
    st.caption("People who have sent enquiries about your properties. Tenant and lease records are not currently maintained here.")
    try:
        enquiries = records("inquiry")
    except ApplicationAPIError as exc:
        st.error(str(exc))
        return
    renter_by_uid = {}
    for record in enquiries:
        detail = record.get("data") or {}
        key = detail.get("client_uid") or detail.get("client_email") or detail.get("client_name")
        if key:
            renter_by_uid[key] = {
                "Name": detail.get("client_name", ""), "Email": detail.get("client_email", ""),
                "Phone": detail.get("client_phone", ""), "Enquiries": renter_by_uid.get(key, {}).get("Enquiries", 0) + 1,
                "Latest status": detail.get("status", "new"),
            }
    st.metric("Unique enquirers", len(renter_by_uid))
    if renter_by_uid:
        st.dataframe(pd.DataFrame(renter_by_uid.values()), width="stretch", hide_index=True)
    else:
        st.info("Renter details will appear here when someone enquires about one of your published properties.")


def render_analytics(_data=None) -> None:
    st.title("Property Analytics")
    records_data = load_or_report()
    if records_data is None:
        return
    listings = records_data["listings"]
    details = [record.get("data") or {} for record in listings]
    published = [item for item in details if item.get("status") == "published"]
    occupied = sum(item.get("occupancy_status") == "occupied" for item in details)
    vacant = sum(item.get("occupancy_status") == "vacant" for item in details)
    tracked = occupied + vacant
    cols = st.columns(4)
    cols[0].metric("Portfolio size", len(details))
    cols[1].metric("Listed", len(published))
    cols[2].metric("Average asking rent", money(pd.to_numeric(pd.Series([item.get("rent") for item in details]), errors="coerce").mean()))
    cols[3].metric("Occupancy rate", f"{occupied / tracked:.0%}" if tracked else "Not tracked")
    chart1, chart2 = st.columns(2)
    with chart1, st.container(border=True):
        st.markdown("**Asking rent by property**")
        if details:
            rent_frame = pd.DataFrame({"Property": [str(i.get("title") or i.get("locality") or "Listing")[:24] for i in details], "Rent (₹)": [float(i.get("rent", 0) or 0) for i in details]}).set_index("Property")
            st.bar_chart(rent_frame)
        else:
            st.info("Add listings to see portfolio analytics.")
    with chart2, st.container(border=True):
        st.markdown("**Listings by city**")
        if details:
            city_counts = pd.Series([item.get("city", "Unknown") for item in details]).value_counts().rename_axis("City").to_frame("Properties")
            st.bar_chart(city_counts)
        else:
            st.info("City breakdown will appear when you add a property.")
    st.caption("Metrics use your saved listing records. The dataset does not contain historical views, lease dates, or payment events.")


def render_revenue(_data=None) -> None:
    st.title("Revenue & Rent Collection")
    st.caption("Listing rents provide an asking-rent estimate; completed payments are not yet recorded in RentAI.")
    try:
        listings = records("listing")
    except ApplicationAPIError as exc:
        st.error(str(exc))
        return
    details = [record.get("data") or {} for record in listings]
    active = [item for item in details if item.get("status") == "published"]
    potential = sum(float(item.get("rent", 0) or 0) for item in active)
    a, b, c = st.columns(3)
    a.metric("Potential monthly listed rent", money(potential))
    b.metric("Rent collected", "Not tracked yet")
    c.metric("Outstanding rent", "Not tracked yet")
    st.subheader("Published rent schedule")
    if active:
        st.dataframe(pd.DataFrame([{"Property": item.get("title", "Property"), "City": item.get("city", ""), "Occupancy": item.get("occupancy_status", "Not tracked"), "Asking rent / month": item.get("rent", 0)} for item in active]), width="stretch", hide_index=True)
    else:
        st.info("Publish properties to see expected monthly asking rent.")
    st.info("Payment receipts, due dates, collection status, income, and expenses are not stored by the current database. Collected and outstanding amounts are therefore not estimated.")


def render_maintenance(_data=None) -> None:
    st.title("Maintenance Requests")
    st.caption("Track work items tied to your properties. Renter-submitted requests are marked separately.")
    try:
        listings = records("listing")
        requests = records("maintenance")
    except ApplicationAPIError as exc:
        st.error(str(exc))
        return
    if listings:
        with st.expander("Log a maintenance request", expanded=not requests):
            with st.form("owner_log_maintenance"):
                property_record = st.selectbox("Property", listings, format_func=_property_label)
                title = st.text_input("Issue title")
                description = st.text_area("Describe the work")
                schedule = st.checkbox("Add a target date")
                due_date = st.date_input("Target date", value=date.today()) if schedule else None
                submitted = st.form_submit_button("Log request", type="primary")
            if submitted:
                try:
                    payload = {"listing_id": property_record["id"], "title": title.strip(), "description": description.strip()}
                    if due_date:
                        payload["due_date"] = due_date.isoformat()
                    create("maintenance", payload)
                    st.success("Maintenance item added.")
                    st.rerun()
                except ApplicationAPIError as exc:
                    st.error(str(exc))
    if not requests:
        st.info("There are no maintenance requests for your portfolio.")
        return
    st.dataframe(pd.DataFrame([{
        "Property": next((r.get("data", {}).get("title", "Property") for r in listings if r.get("id") == (item.get("data") or {}).get("listing_id")), "Property"),
        "Issue": (item.get("data") or {}).get("title", ""),
        "Description": (item.get("data") or {}).get("description", ""),
        "Reported by": "Renter" if (item.get("data") or {}).get("reported_by_role") == "renter" else "Owner",
        "Status": (item.get("data") or {}).get("status", "open"),
        "Due date": (item.get("data") or {}).get("due_date", "Not scheduled"),
    } for item in requests]), width="stretch", hide_index=True)
    selected = st.selectbox("Update request status", requests, format_func=lambda item: (item.get("data") or {}).get("title", "Maintenance request"), key="owner_maintenance_selector")
    current = selected.get("data") or {}
    with st.form("owner_update_maintenance"):
        status_values = ["open", "in_progress", "completed", "cancelled"]
        status = st.selectbox("Status", status_values, index=status_values.index(current.get("status", "open")) if current.get("status", "open") in status_values else 0)
        owner_note = st.text_area("Work note", value=current.get("owner_note", ""), max_chars=2000)
        submitted = st.form_submit_button("Save status")
    if submitted:
        try:
            update(selected, {**current, "status": status, "owner_note": owner_note.strip()})
            st.success("Maintenance status updated.")
            st.rerun()
        except ApplicationAPIError as exc:
            st.error(str(exc))


def render_profile(_data=None) -> None:
    st.title("My Profile")
    st.caption("Update the contact details shown to renters who enquire about your listings.")
    try:
        profiles = records("profile")
    except ApplicationAPIError as exc:
        st.error(str(exc))
        return
    profile = next((item for item in profiles if (item.get("data") or {}).get("record_type") == "owner_profile"), None)
    current = (profile or {}).get("data") or {}
    with st.form("owner_profile_form"):
        name = st.text_input("Full name", value=current.get("name") or owner_name())
        email = st.text_input("Email", value=st.session_state.get("rentai_email", ""), disabled=True)
        phone = st.text_input("Phone", value=current.get("phone", ""), max_chars=40)
        business = st.text_input("Business or portfolio name", value=current.get("business_name", ""), max_chars=120)
        bio = st.text_area("About you", value=current.get("bio", ""), max_chars=1000)
        submitted = st.form_submit_button("Save profile", type="primary")
    if submitted:
        if not name.strip():
            st.error("Your name is required.")
            return
        payload = {"record_type": "owner_profile", "name": name.strip(), "phone": phone.strip(), "business_name": business.strip(), "bio": bio.strip()}
        try:
            update(profile, payload) if profile else create("profile", payload)
            st.success("Profile saved.")
            st.rerun()
        except ApplicationAPIError as exc:
            st.error(str(exc))


def render_settings(_data=None) -> None:
    st.title("Settings")
    st.caption("Manage workspace notification preferences.")
    try:
        profiles = records("profile")
    except ApplicationAPIError as exc:
        st.error(str(exc))
        return
    settings = next((item for item in profiles if (item.get("data") or {}).get("record_type") == "owner_settings"), None)
    current = (settings or {}).get("data") or {}
    with st.form("owner_settings_form"):
        email_alerts = st.toggle("Email me about new enquiries", value=current.get("email_alerts", True))
        maintenance_alerts = st.toggle("Email me about maintenance status changes", value=current.get("maintenance_alerts", True))
        summary_frequency = st.selectbox("Portfolio summary", ["weekly", "monthly", "off"], index=["weekly", "monthly", "off"].index(current.get("summary_frequency", "weekly")) if current.get("summary_frequency", "weekly") in {"weekly", "monthly", "off"} else 0)
        submitted = st.form_submit_button("Save preferences", type="primary")
    if submitted:
        payload = {"record_type": "owner_settings", "email_alerts": email_alerts, "maintenance_alerts": maintenance_alerts, "summary_frequency": summary_frequency}
        try:
            update(settings, payload) if settings else create("profile", payload)
            st.success("Preferences saved.")
            st.rerun()
        except ApplicationAPIError as exc:
            st.error(str(exc))
