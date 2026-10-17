"""Presentation functions for secondary Owner workspace sections."""

import pandas as pd
import plotly.express as px
import streamlit as st
from datetime import date
from html import escape
import hashlib
import math
import re
from pydantic import ValidationError

from backend.schemas.prediction_schema import RentPredictionRequest
from backend.services.prediction_service import predict_rent
from frontend.application_api import (ApplicationAPIError, booking_notifications, bookings as booking_records,
                                      create, decide_booking, delete, records, update)
from backend.services.image_service import MAX_IMAGE_BYTES
from frontend.application_api import (
    ApplicationAPIError,
    classify_rent,
    create,
    delete,
    listing_images,
    predict_rent,
    profile_images,
    records,
    similar_properties_for_features,
    upload_image,
    update,
)
from frontend.components.data_utils import category_options
from frontend.components.property_images import store_owner_image
from frontend.pages.owner_support import load_or_report, money, owner_name, rows


PROPERTY_TYPES = ["Apartment", "House", "Villa", "Studio", "Other"]


def _upload_selected_owner_image(uploaded_file, record_id: str, session_key: str, label: str) -> None:
    """Upload once when a photo is selected; offer an explicit retry after errors."""
    if uploaded_file is None:
        return
    if uploaded_file.size > MAX_IMAGE_BYTES:
        st.error(f"Choose an image no larger than {MAX_IMAGE_BYTES / (1024 * 1024):g} MiB.")
        return

    content = uploaded_file.getvalue()
    fingerprint = hashlib.sha256(content).hexdigest()
    attempt = st.session_state.get(session_key)
    if attempt and attempt.get("fingerprint") == fingerprint:
        if attempt.get("status") == "failed":
            st.error(f"{label} was not uploaded. {attempt['message']}")
            if st.button("Retry upload", key=f"{session_key}_retry"):
                st.session_state.pop(session_key, None)
                st.rerun()
        elif attempt.get("status") == "saved" and not attempt.get("announced"):
            st.success(f"{label} uploaded and saved.")
            attempt["announced"] = True
        return

    st.session_state[session_key] = {"fingerprint": fingerprint, "status": "uploading"}
    try:
        upload_image(record_id, content, uploaded_file.name, uploaded_file.type or "")
    except ApplicationAPIError as exc:
        st.session_state[session_key] = {
            "fingerprint": fingerprint, "status": "failed", "message": str(exc),
        }
        st.error(f"{label} was not uploaded. {exc}")
        if st.button("Retry upload", key=f"{session_key}_retry"):
            st.session_state.pop(session_key, None)
            st.rerun()
    else:
        st.session_state[session_key] = {"fingerprint": fingerprint, "status": "saved"}
        st.rerun()


def _property_options(listings: list[dict]):
    return listings or []


def _validated_property_photos(uploaded_files, saved_count: int, record_id: str | None = None) -> bool:
    """Validate the existing plus newly selected listing photo count and sizes."""
    photos = list(uploaded_files or [])
    uploaded = set(st.session_state.get(f"owner_listing_uploaded_photos_{record_id}", [])) if record_id else set()
    pending_count = sum(
        hashlib.sha256(photo.getvalue()).hexdigest() not in uploaded for photo in photos
    )
    if saved_count + pending_count > 5:
        st.error(f"A property can have up to 5 photos. {saved_count} are already saved.")
        return False
    max_mib = MAX_IMAGE_BYTES / (1024 * 1024)
    for photo in photos:
        if photo.size > MAX_IMAGE_BYTES:
            st.error(f"{photo.name} exceeds the {max_mib:g} MiB image limit.")
            return False
    return True


def _show_property_photo_previews(uploaded_files) -> None:
    photos = list(uploaded_files or [])
    if photos:
        st.caption(f"Selected photos ({len(photos)}). Remove any photo in the uploader before saving.")
        columns = st.columns(min(5, len(photos)))
        for index, photo in enumerate(photos):
            with columns[index % len(columns)]:
                st.image(photo, caption=photo.name, width="stretch")


def _upload_property_photos(uploaded_files, record_id: str, saved_count: int) -> bool:
    """Upload selected photos once per content fingerprint through the authenticated API."""
    photos = list(uploaded_files or [])
    if not _validated_property_photos(photos, saved_count, record_id):
        return False
    uploaded_key = f"owner_listing_uploaded_photos_{record_id}"
    uploaded = set(st.session_state.get(uploaded_key, []))
    for photo in photos:
        content = photo.getvalue()
        fingerprint = hashlib.sha256(content).hexdigest()
        if fingerprint in uploaded:
            continue
        try:
            upload_image(record_id, content, photo.name, photo.type or "")
        except ApplicationAPIError as exc:
            st.session_state[uploaded_key] = sorted(uploaded)
            st.error(f"{photo.name} was not uploaded. {exc}")
            return False
        uploaded.add(fingerprint)
        st.session_state[uploaded_key] = sorted(uploaded)
    return True


def _property_label(record: dict) -> str:
    data = record.get("data") or {}
    return f"{data.get('title', 'Property')} · {data.get('locality', '')} · {record.get('id', '')[:8]}"


def render_add_property(data=None) -> None:
    st.title("Add New Property")
    st.caption("Create a listing in your private Owner portfolio.")
    property_types = category_options(data, "property_type") if isinstance(data, pd.DataFrame) else []
    property_types = property_types or PROPERTY_TYPES
    furnishing_options = category_options(data, "furnishing_status") if isinstance(data, pd.DataFrame) else []
    with st.container(border=True):
        with st.form("owner_workspace_add_property", clear_on_submit=False):
            title = st.text_input("Property title *", placeholder="e.g. Sunny 2 bedroom apartment", key="owner_add_title")
            first, second, third = st.columns(3)
            city = first.text_input("City *", key="owner_add_city")
            locality = second.text_input("Locality *", key="owner_add_locality")
            property_type = third.selectbox("Property type", property_types, key="owner_add_property_type")
            rent_col, bed_col, occupancy_col = st.columns(3)
            rent = rent_col.number_input("Monthly asking rent (₹) *", min_value=1, step=1000, key="owner_add_asking_rent")
            bedrooms = bed_col.number_input("Bedrooms", min_value=1, max_value=30, step=1, key="owner_add_bedrooms")
            occupancy = occupancy_col.selectbox("Occupancy", ["vacant", "occupied", "not_tracked"], format_func=lambda value: {"vacant": "Vacant", "occupied": "Occupied", "not_tracked": "Not tracked"}[value], key="owner_add_occupancy")
            security_deposit = st.number_input("Security deposit amount", min_value=0, step=5000, key="owner_add_security_deposit")

            features_first, features_second, features_third = st.columns(3)
            area_sqft = features_first.number_input("Area (sq ft) *", min_value=1.0, value=800.0, step=50.0, key="owner_add_area_sqft")
            bathrooms = features_second.number_input("Bathrooms", min_value=0, value=1, step=1, key="owner_add_bathrooms")
            floor = features_third.number_input("Floor for estimate", min_value=0, value=0, step=1, key="owner_add_floor")

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
            property_photo = st.file_uploader(
                "Property photo (optional)", type=["jpg", "jpeg", "png", "webp"],
                help="JPEG, PNG, or WebP · maximum 8 MB. The photo is stored in the project assets folder.",
                key="owner_add_property_photo",
            )
            estimate_col, save_col = st.columns(2)
            status = st.radio("Listing visibility", ["published", "draft", "unlisted"], horizontal=True, format_func=lambda value: {"published": "Published", "draft": "Draft", "unlisted": "Unlisted"}[value], key="owner_add_status")
            st.markdown("**Property Images**")
            st.caption("Upload up to 5 photos. Remove selected photos in the uploader before saving.")
            property_photos = st.file_uploader(
                "Choose property photos", type=["jpg", "jpeg", "png", "webp", "gif"],
                accept_multiple_files=True, key="owner_add_property_photos",
            )
            estimate_col, preview_col, save_col = st.columns(3)
            estimate_clicked = estimate_col.form_submit_button("Estimate monthly rent", key="owner_estimate_rent", width="stretch")
            preview_clicked = preview_col.form_submit_button("Preview selected photos", key="owner_preview_property_photos", width="stretch")
            submitted = save_col.form_submit_button("Save property", key="owner_save_property", type="primary", width="stretch")

        if preview_clicked:
            _validated_property_photos(property_photos, 0)
        if preview_clicked:
            _show_property_photo_previews(property_photos)

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
                validated_features = RentPredictionRequest.model_validate(prediction_features).model_dump(exclude_unset=True)
                predicted_rent = predict_rent(validated_features)
                if not math.isfinite(predicted_rent) or predicted_rent <= 0:
                    raise ValueError("The model did not produce a positive monthly estimate.")
                rent_category = None
                similar = []
                classification_features = {
                    "rent": predicted_rent,
                    "area_sqft": validated_features["area_sqft"],
                    "city": validated_features["city"],
                    "locality": validated_features["locality"],
                    "property_type": validated_features["property_type"],
                }
                similarity_features = {
                    **validated_features,
                    "rent": predicted_rent,
                }
                try:
                    rent_category = classify_rent(classification_features)
                except ApplicationAPIError as exc:
                    st.warning(f"Rent category is unavailable: {exc}")
                try:
                    similar = similar_properties_for_features(similarity_features, limit=3)
                except ApplicationAPIError as exc:
                    st.warning(f"Comparable dataset properties are unavailable: {exc}")
                st.session_state["owner_rent_estimate"] = {
                    "features": validated_features,
                    "asking_rent": rent,
                    "predicted_rent": predicted_rent,
                    "rent_category": rent_category,
                    "similar_properties": similar,
                }
            except (ApplicationAPIError, ValidationError, ValueError) as exc:
                st.error(f"Could not estimate rent: {exc}")

        if submitted:
            if not title.strip() or not city.strip() or not locality.strip():
                st.error("Complete the title, city, and locality fields.")
                return
            if not _validated_property_photos(property_photos, 0):
                return
            try:
                payload = {
                    "title": title.strip(), "city": city.strip(), "locality": locality.strip(),
                    "property_type": property_type, "rent": rent, "bedrooms": bedrooms,
                    "occupancy_status": occupancy, "security_deposit": security_deposit,
                    "description": description.strip(), "status": status,
                }
                if property_photo is not None:
                    payload["image_path"] = store_owner_image(property_photo)
                create("listing", payload)
                listing_data = {
                    "title": title.strip(), "city": city.strip(), "locality": locality.strip(),
                    "property_type": property_type, "rent": rent, "bedrooms": bedrooms,
                    "bathrooms": bathrooms, "area_sqft": area_sqft, "floor": floor,
                    "furnishing_status": furnishing_status, "distance_to_city_center_km": distance,
                    "age_of_property": age, "parking": parking,
                    "occupancy_status": occupancy, "description": description.strip(), "status": status,
                }
                created = create("listing", listing_data)
                if property_photos:
                    record_id = created.get("id") if isinstance(created, dict) else None
                    if not record_id:
                        st.error("The property was created, but its photos could not be associated. Open My Properties and add them there.")
                        st.session_state.owner_active_page = "My Properties"
                        st.rerun()
                    if not _upload_property_photos(property_photos, record_id, 0):
                        st.session_state["owner_property_update_feedback"] = "Property saved. Some photos still need to be uploaded; select them again in My Properties."
                        st.session_state.owner_active_page = "My Properties"
                        st.rerun()
                st.success("Property saved to your portfolio.")
                st.session_state.pop("owner_rent_estimate", None)
                st.session_state.owner_active_page = "My Properties"
                st.rerun()
            except (ApplicationAPIError, ValueError) as exc:
                st.error(str(exc))

        estimate = st.session_state.get("owner_rent_estimate")
        if estimate:
            try:
                current_features = RentPredictionRequest.model_validate(prediction_features).model_dump(exclude_unset=True)
            except ValidationError:
                current_features = None
            if estimate["features"] != current_features:
                st.session_state.pop("owner_rent_estimate", None)
            else:
                asking_col, predicted_col = st.columns(2)
                asking_col.metric("Monthly asking rent", f"₹{estimate['asking_rent']:,.0f} INR")
                predicted_col.metric("ML-predicted monthly rent", f"₹{estimate['predicted_rent']:,.0f} INR")
                st.caption("The estimate is informational only. Saving this property uses the asking rent above.")
                if estimate.get("rent_category"):
                    st.metric("Estimated rent category", estimate["rent_category"])
                similar = estimate.get("similar_properties") or []
                if similar:
                    st.markdown("**Similar dataset properties**")
                    st.dataframe(pd.DataFrame([{
                        "Property ID": item.get("property_id", ""),
                        "City": item.get("city", ""),
                        "Locality": item.get("locality", ""),
                        "Property type": item.get("property_type", ""),
                        "Area (sq ft)": item.get("area_sqft", ""),
                        "Bedrooms": item.get("bedrooms", ""),
                        "Dataset rent": money(item.get("rent")),
                    } for item in similar]), use_container_width=True, hide_index=True)


def render_properties(_data=None) -> None:
    st.title("My Properties")
    update_feedback = st.session_state.pop("owner_property_update_feedback", None)
    if update_feedback:
        st.success(update_feedback)
    st.caption("Only listings owned by your account are shown here.")
    image_feedback = st.session_state.pop("owner_property_image_feedback", None)
    if image_feedback:
        st.warning(image_feedback)
    property_images_loaded = True
    try:
        listings = records("listing")
        tenancies = records("tenancy")
    except ApplicationAPIError as exc:
        st.error(str(exc))
        return
    if not listings:
        st.info("Your portfolio is empty. Add your first property to get started.")
        if st.button("Add a property", type="primary"):
            st.session_state.owner_active_page = "Add New Property"
            st.rerun()
        return

    filters = st.columns([1.4, 1, 1])
    search = filters[0].text_input("Search properties", placeholder="Title, city, or locality", key="owner_property_search").strip().casefold()
    statuses = sorted({str((item.get("data") or {}).get("status", "draft")) for item in listings})
    status_filter = filters[1].selectbox("Status", ["All statuses", *statuses], key="owner_property_status_filter")
    cities = sorted({str((item.get("data") or {}).get("city", "")).strip() for item in listings if (item.get("data") or {}).get("city")})
    city_filter = filters[2].selectbox("City", ["All cities", *cities], key="owner_property_city_filter")
    filtered = []
    for item in listings:
        detail = item.get("data") or {}
        searchable = " ".join(str(detail.get(key, "")) for key in ("title", "city", "locality")).casefold()
        if search and search not in searchable:
            continue
        if status_filter != "All statuses" and detail.get("status", "draft") != status_filter:
            continue
        if city_filter != "All cities" and detail.get("city") != city_filter:
            continue
        filtered.append(item)
    if not filtered:
        st.info("No properties match those filters. Clear a filter or add a property.")
        return

    page_size = st.selectbox("Properties per page", [10, 25, 50], index=0, key="owner_property_page_size")
    page_count = max(1, (len(filtered) + page_size - 1) // page_size)
    current_page = min(max(int(st.session_state.get("owner_property_page", 1)), 1), page_count)
    st.session_state["owner_property_page"] = current_page
    page = st.selectbox("Page", list(range(1, page_count + 1)), key="owner_property_page")
    start = (page - 1) * page_size
    visible = filtered[start:start + page_size]
    st.caption(f"Showing {start + 1}-{start + len(visible)} of {len(filtered)} properties")
    st.dataframe(rows(visible), use_container_width=True, hide_index=True)
    selected = st.selectbox("View or edit property", visible, format_func=_property_label, key="owner_property_edit_selector")
    current = selected.get("data") or {}
    active_tenancy = next((
        item for item in tenancies
        if (item.get("data") or {}).get("listing_id") == selected.get("id")
        and (item.get("data") or {}).get("status") == "active"
    ), None)
    st.subheader("Property details")
    st.json(current, expanded=False)
    try:
        property_images = [
            image for image in listing_images()
            if image.get("record_id") == selected.get("id") and image.get("url")
        ]
    except ApplicationAPIError as exc:
        property_images_loaded = False
        property_images = []
        st.warning(f"Property photos could not be loaded. {exc}")
    st.markdown("**Property photos**")
    if property_images:
        photo_columns = st.columns(min(4, len(property_images)))
        for index, image in enumerate(property_images):
            with photo_columns[index % len(photo_columns)]:
                st.image(image["url"], caption=f"Property photo {len(property_images) - index}", width=180)
    else:
        st.caption("No property photos uploaded yet.")
    with st.form("owner_workspace_edit_property"):
        title = st.text_input("Title", value=current.get("title", ""), key="owner_edit_title")
        cols = st.columns(3)
        city = cols[0].text_input("City", value=current.get("city", ""), key="owner_edit_city")
        locality = cols[1].text_input("Locality", value=current.get("locality", ""), key="owner_edit_locality")
        property_types = category_options(_data, "property_type") if isinstance(_data, pd.DataFrame) else []
        property_types = sorted(set(property_types or PROPERTY_TYPES) | {str(current.get("property_type", "Other"))})
        type_index = property_types.index(current.get("property_type")) if current.get("property_type") in property_types else 0
        property_type = cols[2].selectbox("Property type", property_types, index=type_index, key="owner_edit_property_type")
        cols2 = st.columns(3)
        rent = cols2[0].number_input("Monthly asking rent (₹)", min_value=1, value=max(1, int(current.get("rent", 1) or 1)), step=1000, key="owner_edit_rent")
        bedrooms = cols2[1].number_input("Bedrooms", min_value=1, max_value=30, value=min(30, max(1, int(current.get("bedrooms", 1) or 1))), key="owner_edit_bedrooms")
        occupancy_values = ["vacant", "occupied", "not_tracked"]
        occupancy = cols2[2].selectbox("Occupancy", occupancy_values, index=occupancy_values.index(current.get("occupancy_status", "not_tracked")) if current.get("occupancy_status", "not_tracked") in occupancy_values else 2, format_func=lambda value: {"vacant": "Vacant", "occupied": "Occupied", "not_tracked": "Not tracked"}[value])
        security_deposit = st.number_input("Security deposit amount", min_value=0, value=max(0, int(current.get("security_deposit", 0) or 0)), step=5000)
        description = st.text_area("Description", value=current.get("description", ""), max_chars=5000)
        property_photo = st.file_uploader(
            "Replace property photo (optional)", type=["jpg", "jpeg", "png", "webp"],
            help="JPEG, PNG, or WebP · maximum 8 MB.", key=f"owner_edit_property_photo_{selected['id']}",
        )
        listing_status = st.selectbox("Listing visibility", ["published", "draft"], index=0 if current.get("status") == "published" else 1, format_func=lambda value: "Listed" if value == "published" else "Draft")
        occupancy_index = occupancy_values.index(current.get("occupancy_status", "not_tracked")) if current.get("occupancy_status", "not_tracked") in occupancy_values else 2
        if active_tenancy:
            occupancy_index = occupancy_values.index("occupied")
        occupancy = cols2[2].selectbox(
            "Occupancy", occupancy_values, index=occupancy_index,
            format_func=lambda value: {"vacant": "Vacant", "occupied": "Occupied", "not_tracked": "Not tracked"}[value],
            key="owner_edit_occupancy", disabled=bool(active_tenancy),
            help="An active tenancy determines that this property is occupied.",
        )
        if active_tenancy:
            renter_name = (active_tenancy.get("data") or {}).get("renter_name") or "a renter"
            cols2[2].caption(
                f"Occupied due to active tenancy with {renter_name}. "
                f"Saved occupancy remains {current.get('occupancy_status', 'not tracked').replace('_', ' ').title()} until you change it after the tenancy ends."
            )
        model_cols = st.columns(3)
        area_sqft = model_cols[0].number_input("Area (sq ft)", min_value=0.0, value=max(0.0, float(current.get("area_sqft", 0) or 0)), key="owner_edit_area")
        bathrooms = model_cols[1].number_input("Bathrooms", min_value=0, max_value=30, value=min(30, max(0, int(current.get("bathrooms", 0) or 0))), key="owner_edit_bathrooms")
        floor = model_cols[2].number_input("Floor", min_value=0, max_value=500, value=min(500, max(0, int(current.get("floor", 0) or 0))), key="owner_edit_floor")
        furnishing_options = category_options(_data, "furnishing_status") if isinstance(_data, pd.DataFrame) else []
        furnishing_options = sorted(set(furnishing_options or ["Not tracked"]) | {str(current.get("furnishing_status", "Not tracked"))})
        feature_cols = st.columns(3)
        furnishing = feature_cols[0].selectbox("Furnishing", furnishing_options, index=furnishing_options.index(current.get("furnishing_status", "Not tracked")), key="owner_edit_furnishing")
        distance = feature_cols[1].number_input("Distance to city center (km)", min_value=0.0, value=max(0.0, float(current.get("distance_to_city_center_km", 0) or 0)), key="owner_edit_distance")
        age = feature_cols[2].number_input("Property age (years)", min_value=0, max_value=1000, value=min(1000, max(0, int(current.get("age_of_property", 0) or 0))), key="owner_edit_age")
        parking = st.number_input("Parking spaces", min_value=0, max_value=100, value=min(100, max(0, int(current.get("parking", 0) or 0))), key="owner_edit_parking")
        description = st.text_area("Description", value=current.get("description", ""), max_chars=5000, key="owner_edit_description")
        listing_status = st.selectbox("Listing visibility", ["published", "draft", "unlisted"], index=["published", "draft", "unlisted"].index(current.get("status")) if current.get("status") in {"published", "draft", "unlisted"} else 1, format_func=lambda value: value.title(), key="owner_edit_status")
        st.markdown("**Property Images**")
        st.caption(f"{len(property_images)} of 5 photos saved. Add photos here; remove selected photos before saving.")
        property_photos = st.file_uploader(
            "Add property photos", type=["jpg", "jpeg", "png", "webp", "gif"],
            accept_multiple_files=True, key=f"owner_edit_property_photos_{selected.get('id')}",
            disabled=not property_images_loaded or len(property_images) >= 5,
        )
        preview_clicked = st.form_submit_button("Preview selected photos")
        submitted = st.form_submit_button("Save changes", type="primary")
    if preview_clicked:
        _validated_property_photos(property_photos, len(property_images), selected["id"])
        _show_property_photo_previews(property_photos)
    if submitted:
        if not property_images_loaded:
            st.error("Photos cannot be updated until the saved property photos load successfully.")
            return
        if not _validated_property_photos(property_photos, len(property_images), selected["id"]):
            return
        try:
            payload = {"title": title.strip(), "city": city.strip(), "locality": locality.strip(), "property_type": property_type, "rent": rent, "bedrooms": bedrooms, "occupancy_status": occupancy, "security_deposit": security_deposit, "description": description.strip(), "status": listing_status}
            if property_photo is not None:
                payload["image_path"] = store_owner_image(property_photo)
            elif current.get("image_path"):
                payload["image_path"] = current["image_path"]
            update(selected, payload)
            st.success("Property updated.")
            property_data = {
                "title": title.strip(), "city": city.strip(), "locality": locality.strip(),
                "property_type": property_type, "rent": rent, "bedrooms": bedrooms,
                "bathrooms": bathrooms, "area_sqft": area_sqft, "floor": floor,
                "furnishing_status": furnishing, "distance_to_city_center_km": distance,
                "age_of_property": age, "parking": parking,
                "occupancy_status": current.get("occupancy_status", "not_tracked") if active_tenancy else occupancy,
                "description": description.strip(),
                "status": listing_status,
            }
            saved = update(selected, property_data)
            if (
                not isinstance(saved, dict)
                or saved.get("id") != selected.get("id")
                or saved.get("owner_uid") != selected.get("owner_uid")
                or saved.get("data") != property_data
            ):
                raise ApplicationAPIError(
                    "RentAI could not confirm the property update. Refresh and try again."
                )
            if property_photos and not _upload_property_photos(property_photos, selected["id"], len(property_images)):
                st.session_state["owner_property_image_feedback"] = "Property details were saved. Some photos still need to be uploaded; select them again to retry."
                st.rerun()
            st.session_state["owner_property_update_feedback"] = "Property updated successfully."
            st.rerun()
        except (ApplicationAPIError, ValueError) as exc:
            st.error(str(exc))

    confirm_delete = st.checkbox(
        "I understand that deletion is permanent and will be blocked if linked records exist.",
        key=f"confirm_delete_owner_property_{selected['id']}",
    )
    if st.button("Delete property", key=f"delete_owner_property_{selected['id']}", disabled=not confirm_delete):
        try:
            delete(selected)
            st.success("Property removed from your portfolio.")
            st.rerun()
        except ApplicationAPIError as exc:
            st.error(exc.response_detail if exc.status_code == 409 and exc.response_detail else str(exc))


def render_enquiries(_data=None) -> None:
    st.title("Applications & Enquiries")
    st.caption("Review enquiries sent by Renters about your published properties and update your response status.")
    if st.session_state.pop("owner_enquiry_updated", False):
        st.success("Enquiry status updated.")
    try:
        enquiries = records("inquiry")
        listings = {record["id"]: record.get("data", {}) for record in records("listing")}
    except ApplicationAPIError as exc:
        st.error(str(exc))
        return
    pending_count = sum((record.get("data") or {}).get("status", "pending") in {"new", "pending", "contacted", "viewing_scheduled"} for record in enquiries)
    a, b = st.columns(2)
    enquiry_metrics = (
        (a, "Total Enquiries", len(enquiries), "All enquiries received for your properties."),
        (b, "Pending Enquiries", pending_count, "Includes new, pending, contacted, and viewing-scheduled enquiries."),
    )
    for column, label, value, explanation in enquiry_metrics:
        with column:
            # The shared theme hides Streamlit's built-in metric labels.
            st.markdown(f"**{label}**")
            st.metric(label, value, label_visibility="collapsed")
            st.caption(explanation)
    if not enquiries:
        st.info("Renter enquiries about your listings will appear here.")
        return
    statuses = ["pending", "accepted", "rejected"]
    for record in sorted(enquiries, key=lambda item: (item.get("data") or {}).get("created_at", ""), reverse=True):
        detail = record.get("data") or {}
        property_data = detail.get("property") or listings.get(detail.get("listing_id"), {})
        current_status = detail.get("status", "pending")
        current_status = "pending" if current_status in {"new", "contacted", "viewing_scheduled"} else current_status
        if current_status not in statuses:
            current_status = "pending"
        with st.container(border=True):
            header = st.columns([2, 1])
            header[0].markdown(f"**{property_data.get('title', 'Property listing')}**")
            header[0].caption(", ".join(filter(None, [property_data.get("locality"), property_data.get("city")])) or "Owner property")
            header[1].metric("Status", current_status.title())
            st.markdown(f"**Renter:** {detail.get('client_name') or 'Renter'}")
            contact = " · ".join(filter(None, [detail.get("client_email"), detail.get("client_phone")]))
            if contact:
                st.caption(contact)
            st.write(detail.get("message") or "No message included.")
            st.caption(f"Submitted {detail.get('created_at') or 'date not recorded'}")
            with st.form(f"owner_enquiry_status_{record.get('id')}"):
                selected_status = st.selectbox(
                    "Enquiry status", statuses,
                    index=statuses.index(current_status),
                    format_func=str.title,
                    key=f"owner_enquiry_status_value_{record.get('id')}",
                )
                submitted = st.form_submit_button("Save status", type="primary")
            if submitted:
                try:
                    update(record, {**detail, "status": selected_status})
                    st.session_state["owner_enquiry_updated"] = True
                    st.rerun()
                except ApplicationAPIError as exc:
                    st.error(f"The enquiry status was not updated. {exc}")


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
        st.dataframe(pd.DataFrame(renter_by_uid.values()), use_container_width=True, hide_index=True)
    else:
        st.info("Renter details will appear here when someone enquires about one of your published properties.")


MODEL_FEATURES = (
    "city", "locality", "property_type", "furnishing_status", "area_sqft",
    "bedrooms", "bathrooms", "floor", "distance_to_city_center_km",
    "age_of_property", "parking",
)


def calculate_owner_property_analytics(listings: list[dict], tenancies: list[dict] | None = None) -> dict:
    """Calculate metrics only from authenticated Owner application records."""
    details = [record.get("data") or {} for record in listings]
    statuses = [str(item.get("status", "draft")).strip().casefold() for item in details]
    active_listing_ids = {
        (record.get("data") or {}).get("listing_id")
        for record in (tenancies or [])
        if (record.get("data") or {}).get("status") == "active"
        and (record.get("data") or {}).get("listing_id")
    }
    occupancies = [
        "occupied" if record.get("id") in active_listing_ids
        else str(item.get("occupancy_status", "")).strip().casefold()
        for record, item in zip(listings, details)
    ]
    asking_rents = pd.to_numeric(
        pd.Series([item.get("rent") for item in details], dtype="object"),
        errors="coerce",
    ).replace([float("inf"), float("-inf")], float("nan"))
    asking_rents = asking_rents[asking_rents > 0].dropna()

    def distribution(field: str) -> pd.Series:
        values = []
        for item in details:
            value = item.get(field)
            if value is None or (isinstance(value, str) and not value.strip()):
                value = "Not recorded"
            values.append(str(value).strip())
        return pd.Series(values, dtype="string").value_counts()

    rent_rows = []
    for record, item in zip(listings, details):
        amount = pd.to_numeric(pd.Series([item.get("rent")]), errors="coerce").iloc[0]
        if pd.notna(amount) and math.isfinite(float(amount)) and amount > 0:
            rent_rows.append({
                "Property": item.get("title") or item.get("locality") or f"Property {record.get('id', '')[:8]}",
                "Asking rent": float(amount),
            })

    return {
        "total": len(details),
        "published": sum(status == "published" for status in statuses),
        "draft": sum(status == "draft" for status in statuses),
        "unlisted": sum(status == "unlisted" for status in statuses),
        "vacant": sum(status == "vacant" for status in occupancies),
        "occupied": sum(status == "occupied" for status in occupancies),
        "occupancy_unknown": sum(status not in {"vacant", "occupied"} for status in occupancies),
        "average_rent": float(asking_rents.mean()) if not asking_rents.empty else None,
        "minimum_rent": float(asking_rents.min()) if not asking_rents.empty else None,
        "maximum_rent": float(asking_rents.max()) if not asking_rents.empty else None,
        "rent_by_property": pd.DataFrame(rent_rows, columns=["Property", "Asking rent"]),
        "city_counts": distribution("city"),
        "property_type_counts": distribution("property_type"),
        "furnishing_counts": distribution("furnishing_status"),
        "bedroom_counts": distribution("bedrooms"),
    }


def calculate_comparable_rents(data: pd.DataFrame, property_data: dict) -> dict:
    """Find dataset comparables using exact location/type and available features."""
    result = {"count": 0, "median_rent": None, "filters": [], "notes": []}
    if not isinstance(data, pd.DataFrame) or data.empty or "rent" not in data.columns:
        result["notes"].append("The rental dataset or rent column is unavailable.")
        return result

    work = data.copy()
    work["_rent_numeric"] = pd.to_numeric(work["rent"], errors="coerce")
    work = work[
        work["_rent_numeric"].notna()
        & work["_rent_numeric"].gt(0)
        & work["_rent_numeric"].map(lambda value: math.isfinite(float(value)))
    ]
    for field, label in (("city", "city"),):
        requested = str(property_data.get(field, "")).strip()
        if not requested or field not in work.columns:
            result["notes"].append("A city match is required for a meaningful comparison.")
            return result
        matches = work[field].astype("string").str.strip().str.casefold().eq(requested.casefold())
        work = work[matches]
        if work.empty:
            result["notes"].append(f"No dataset properties match the saved {label}.")
            return result
        result["filters"].append(f"{requested.title()} city")

    for field, label in (("locality", "locality"), ("property_type", "property type")):
        requested = str(property_data.get(field, "")).strip()
        if not requested:
            continue
        if field not in work.columns:
            result["notes"].append(f"Dataset {label} values are unavailable; this filter was omitted.")
            continue
        matches = work[field].astype("string").str.strip().str.casefold().eq(requested.casefold())
        narrowed = work[matches]
        if not narrowed.empty:
            work = narrowed
            result["filters"].append(f"{requested} {label}")
        else:
            result["notes"].append(f"No exact {label} match; broader location matches are used.")

    bedrooms = pd.to_numeric(pd.Series([property_data.get("bedrooms")]), errors="coerce").iloc[0]
    if pd.notna(bedrooms) and "bedrooms" in work.columns:
        dataset_bedrooms = pd.to_numeric(work["bedrooms"], errors="coerce")
        narrowed = work[dataset_bedrooms.eq(bedrooms)]
        if not narrowed.empty:
            work = narrowed
            result["filters"].append(f"{int(bedrooms)} bedrooms")
        else:
            result["notes"].append("No exact bedroom match; the bedroom filter was relaxed.")

    area = pd.to_numeric(pd.Series([property_data.get("area_sqft")]), errors="coerce").iloc[0]
    if pd.notna(area) and area > 0 and "area_sqft" in work.columns:
        dataset_area = pd.to_numeric(work["area_sqft"], errors="coerce")
        narrowed = work[dataset_area.between(area * 0.75, area * 1.25)]
        if not narrowed.empty:
            work = narrowed
            result["filters"].append("area within ±25%")
        else:
            result["notes"].append("No nearby area matches; the area filter was relaxed.")

    comparable_rents = work["_rent_numeric"]
    result["count"] = len(comparable_rents)
    result["median_rent"] = float(comparable_rents.median()) if not comparable_rents.empty else None
    result["filters"] = result["filters"] or ["city-level dataset matches"]
    return result


def _rent_variance(asking_rent: float | None, comparison_rent: float | None) -> tuple[str, str]:
    if asking_rent is None or comparison_rent is None or comparison_rent <= 0:
        return "Not available", ""
    difference = asking_rent - comparison_rent
    percentage = difference / comparison_rent * 100
    currency_difference = f"−₹{abs(difference):,.0f}" if difference < 0 else f"₹{difference:+,.0f}"
    return currency_difference, f"{percentage:+.1f}% vs reference"


def _indian_grouped_integer(value: float) -> str:
    number = str(abs(int(round(value))))
    if len(number) <= 3:
        grouped = number
    else:
        grouped = number[-3:]
        number = number[:-3]
        while number:
            grouped = number[-2:] + "," + grouped
            number = number[:-2]
    return ("-" if value < 0 else "") + grouped


def _axis_ticks(maximum: float, *, currency: bool) -> tuple[list[float], list[str]]:
    axis_maximum = max(float(maximum) * 1.12, 1.0)
    values = [axis_maximum * index / 4 for index in range(5)]
    labels = [
        f"₹{_indian_grouped_integer(value)}" if currency else f"{int(round(value)):,}"
        for value in values
    ]
    return values, labels


def build_owner_rent_chart(rent_by_property: pd.DataFrame):
    """Build a compact, horizontal asking-rent chart with full property labels."""
    required = {"Property", "Asking rent"}
    if not isinstance(rent_by_property, pd.DataFrame) or not required.issubset(rent_by_property.columns):
        return None
    chart_data = rent_by_property.loc[:, ["Property", "Asking rent"]].copy()
    chart_data["Asking rent"] = pd.to_numeric(chart_data["Asking rent"], errors="coerce")
    chart_data = chart_data[
        chart_data["Asking rent"].notna()
        & chart_data["Asking rent"].gt(0)
        & chart_data["Asking rent"].map(lambda value: math.isfinite(float(value)))
    ]
    if chart_data.empty:
        return None
    chart_data["Property"] = chart_data["Property"].fillna("Untitled property").astype(str)
    longest_label = max(len(label) for label in chart_data["Property"])
    left_margin = min(360, max(150, 80 + longest_label * 5))
    height = min(520, 150 + 44 * len(chart_data))
    maximum = float(chart_data["Asking rent"].max())
    tick_values, tick_labels = _axis_ticks(maximum, currency=True)
    figure = px.bar(
        chart_data,
        x="Asking rent",
        y="Property",
        orientation="h",
        color_discrete_sequence=["#ff6b1a"],
        template="plotly_white",
    )
    figure.update_layout(
        height=height,
        title={"text": "Monthly asking rent by property", "x": 0.01, "xanchor": "left"},
        margin={"l": left_margin, "r": 24, "t": 48, "b": 58},
        showlegend=False,
        bargap=0.35,
        paper_bgcolor="#ffffff",
        plot_bgcolor="#ffffff",
        font={"color": "#0f172a", "family": "Aptos, Segoe UI, sans-serif", "size": 12},
        title_font={"color": "#0f172a", "size": 17},
    )
    figure.update_traces(
        hovertemplate="<b>%{y}</b><br>Owner asking rent: ₹%{x:,.0f} / month<extra></extra>"
    )
    figure.update_xaxes(
        title="Owner asking rent (₹ / month)",
        range=[0, tick_values[-1]],
        tickmode="array",
        tickvals=tick_values,
        ticktext=tick_labels,
        automargin=True,
        fixedrange=True,
        tickfont={"color": "#334155", "size": 12},
        title_font={"color": "#334155", "size": 13},
        gridcolor="#e2e8f0",
        zerolinecolor="#cbd5e1",
    )
    figure.update_yaxes(
        title="Property",
        automargin=True,
        categoryorder="total ascending",
        ticklabeloverflow="allow",
        tickfont={"color": "#0f172a", "size": 12},
        title_font={"color": "#334155", "size": 13},
        gridcolor="#ffffff",
        zerolinecolor="#ffffff",
    )
    return figure


def build_owner_count_chart(series: pd.Series, category: str, color: str):
    """Build a horizontal distribution chart with an integer count axis."""
    if not isinstance(series, pd.Series) or series.empty:
        return None
    chart_data = series.rename_axis(category).rename("Listings").reset_index()
    longest_label = max(len(str(label)) for label in chart_data[category])
    left_margin = min(340, max(140, 70 + longest_label * 6))
    height = min(360, 130 + 38 * len(chart_data))
    maximum = float(pd.to_numeric(chart_data["Listings"], errors="coerce").max())
    tick_values, tick_labels = _axis_ticks(maximum, currency=False)
    figure = px.bar(
        chart_data,
        x="Listings",
        y=category,
        orientation="h",
        color_discrete_sequence=[color],
    )
    figure.update_layout(
        height=height,
        title={"text": f"Listings by {category.casefold()}", "x": 0.01, "xanchor": "left"},
        margin={"l": left_margin, "r": 24, "t": 44, "b": 48},
        showlegend=False,
        bargap=0.35,
    )
    figure.update_traces(hovertemplate=f"%{{y}}<br>Listings: %{{x:,.0f}}<extra></extra>")
    figure.update_xaxes(
        title="Listings",
        range=[0, tick_values[-1]],
        tickmode="array",
        tickvals=tick_values,
        ticktext=tick_labels,
        automargin=True,
        fixedrange=True,
    )
    figure.update_yaxes(
        title=category,
        automargin=True,
        categoryorder="total ascending",
        ticklabeloverflow="allow",
    )
    return figure


def _render_owner_analytics_bar(series: pd.Series, category: str, key: str, color: str) -> None:
    if series.empty:
        st.info("No values are recorded yet.")
    elif len(series) == 1:
        label, count = series.index[0], int(series.iloc[0])
        _render_owner_analytics_metric(category, str(label), f"{count:,} listings in your saved Owner properties.", "•")
    else:
        figure = build_owner_count_chart(series, category, color)
        st.plotly_chart(figure, width="stretch", key=key, config={"displayModeBar": False})


def _render_owner_analytics_metric(title: str, value: str, explanation: str, icon: str) -> None:
    st.markdown(
        f"""
        <article class="owner-analytics-metric" aria-label="{escape(title)}: {escape(str(value))}">
            <div class="owner-analytics-metric-title"><span aria-hidden="true">{escape(icon)}</span><span>{escape(title)}</span></div>
            <div class="owner-analytics-metric-value">{escape(str(value))}</div>
            <div class="owner-analytics-metric-explanation">{escape(explanation)}</div>
        </article>
        """,
        unsafe_allow_html=True,
    )


def render_analytics(data=None) -> None:
    st.title("Property Analytics")
    st.caption("Portfolio metrics use your saved Owner records. Dataset and ML values are estimates, not guaranteed market rent.")
    st.markdown("""
        <style>
            .owner-analytics-metric {
                box-sizing: border-box;
                min-height: 126px;
                height: 100%;
                padding: 14px 16px;
                border: 1px solid #e2e8f0;
                border-left: 3px solid #ff6b1a;
                border-radius: 10px;
                background: #ffffff;
                display: flex;
                flex-direction: column;
                gap: 7px;
                box-shadow: 0 2px 8px rgba(15, 23, 42, .05);
            }
            .owner-analytics-metric-title,
            .owner-analytics-metric-value,
            .owner-analytics-metric-explanation { display: block; }
            .owner-analytics-metric-title {
                display: flex;
                align-items: center;
                gap: 7px;
                color: #243247 !important;
                font-size: 14px;
                font-weight: 700;
                line-height: 1.3;
                overflow-wrap: anywhere;
            }
            .owner-analytics-metric-title span:first-child {
                flex: 0 0 auto;
                color: #ff6b1a !important;
                font-size: 16px;
            }
            .owner-analytics-metric-value {
                color: #0f172a !important;
                font-family: 'Manrope', 'DM Sans', sans-serif;
                font-size: clamp(20px, 1.7vw, 26px);
                font-weight: 800;
                line-height: 1.2;
                overflow-wrap: anywhere;
            }
            .owner-analytics-metric-explanation {
                color: #526176 !important;
                font-size: 12px;
                line-height: 1.35;
                overflow-wrap: anywhere;
            }
        </style>
        """, unsafe_allow_html=True)
    records_data = load_or_report()
    if records_data is None:
        return
    listings = records_data["listings"]
    if not listings:
        st.info("No saved properties yet. Add a property to see portfolio analytics and dataset comparisons.")
        if st.button("Add a property", type="primary", key="owner_analytics_add_property"):
            st.session_state.owner_active_page = "Add New Property"
            st.rerun()
        return

    metrics = calculate_owner_property_analytics(listings, records_data.get("tenancies", []))
    status_columns = st.columns(4, gap="medium")
    status_cards = (
        ("Total Properties", str(metrics["total"]), "Saved in your authenticated Owner portfolio.", "⌂"),
        ("Published Listings", str(metrics["published"]), "Properties currently marked published.", "✓"),
        ("Draft Listings", str(metrics["draft"]), "Properties saved as drafts.", "✎"),
        ("Unlisted Properties", str(metrics["unlisted"]), "Properties intentionally not listed.", "○"),
    )
    for column, card in zip(status_columns, status_cards):
        with column:
            _render_owner_analytics_metric(*card)

    occupancy_columns = st.columns(3, gap="medium")
    occupancy_cards = (
        ("Vacant Properties", str(metrics["vacant"]), "Saved properties marked vacant.", "□"),
        ("Occupied Properties", str(metrics["occupied"]), "Saved properties marked occupied.", "●"),
        ("Unknown Occupancy", str(metrics["occupancy_unknown"]), "Occupancy is missing or not tracked.", "?"),
    )
    for column, card in zip(occupancy_columns, occupancy_cards):
        with column:
            _render_owner_analytics_metric(*card)

    rent_columns = st.columns(3, gap="medium")
    valid_rent_count = len(metrics["rent_by_property"])
    rent_cards = (
        ("Average Asking Rent", money(metrics["average_rent"]), f"Across {valid_rent_count} Owner properties with valid asking rent.", "₹"),
        ("Lowest Asking Rent", money(metrics["minimum_rent"]), "Lowest valid asking rent in your portfolio.", "↓"),
        ("Highest Asking Rent", money(metrics["maximum_rent"]), "Highest valid asking rent in your portfolio.", "↑"),
    )
    for column, card in zip(rent_columns, rent_cards):
        with column:
            _render_owner_analytics_metric(*card)

    st.subheader("Owner portfolio")
    st.caption("These charts contain only properties saved to your authenticated Owner account.")
    if metrics["rent_by_property"].empty:
        st.info("No valid asking rents are recorded for your properties.")
    else:
        rent_chart = build_owner_rent_chart(metrics["rent_by_property"])
        st.plotly_chart(rent_chart, width="stretch", key="owner-analytics-rent-by-property", config={"displayModeBar": False})

    distribution_rows = st.columns(2, gap="medium")
    distributions = (
        ("Listings by city", metrics["city_counts"], "City", "#059669", "owner-analytics-city"),
        ("Listings by property type", metrics["property_type_counts"], "Property type", "#60a5fa", "owner-analytics-type"),
        ("Furnishing distribution", metrics["furnishing_counts"], "Furnishing", "#8b5cf6", "owner-analytics-furnishing"),
        ("Bedroom distribution", metrics["bedroom_counts"], "Bedrooms", "#f59e0b", "owner-analytics-bedrooms"),
    )
    for index, (title, counts, label, color, key) in enumerate(distributions):
        with distribution_rows[index % 2]:
            st.markdown(f"**{title}**")
            if counts.empty:
                st.info("No values are recorded yet.")
            else:
                _render_owner_analytics_bar(counts, label, key, color)

    st.subheader("Dataset and ML comparison")
    if not isinstance(data, pd.DataFrame) or data.empty:
        st.info("The rental dataset is unavailable; owner portfolio metrics above remain available.")
        return
    for record in listings:
        property_data = record.get("data") or {}
        title = property_data.get("title") or property_data.get("locality") or "Saved property"
        st.markdown(f"**{title}** · {property_data.get('city', 'City not recorded')} · {property_data.get('locality', 'Locality not recorded')}")
        comparison = calculate_comparable_rents(data, property_data)
        asking_rent_value = pd.to_numeric(pd.Series([property_data.get("rent")]), errors="coerce").iloc[0]
        asking_rent = float(asking_rent_value) if pd.notna(asking_rent_value) and math.isfinite(float(asking_rent_value)) and asking_rent_value > 0 else None
        predicted_rent = None
        category = None
        similar = []
        missing_features = [
            field for field in MODEL_FEATURES
            if property_data.get(field) is None or (isinstance(property_data.get(field), str) and not property_data[field].strip())
        ]
        if not missing_features:
            try:
                features = RentPredictionRequest.model_validate({field: property_data[field] for field in MODEL_FEATURES}).model_dump()
                predicted_rent = predict_rent(features)
                if not math.isfinite(predicted_rent) or predicted_rent <= 0:
                    predicted_rent = None
            except (ValidationError, ApplicationAPIError, ValueError):
                predicted_rent = None
        if predicted_rent is not None:
            classification_features = {
                "rent": predicted_rent,
                "area_sqft": property_data.get("area_sqft"),
                "city": property_data.get("city"),
                "locality": property_data.get("locality"),
                "property_type": property_data.get("property_type"),
            }
            try:
                category = classify_rent(classification_features)
            except ApplicationAPIError:
                category = None
            try:
                similar = similar_properties_for_features(
                    {**features, "rent": predicted_rent}, limit=3
                )
            except ApplicationAPIError:
                similar = []

        comparison_columns = st.columns(3, gap="medium")
        comparison_cards = (
            ("Owner Asking Rent", money(asking_rent), "Actual asking rent saved in your SQLite property record.", "₹"),
            ("ML Predicted Monthly Rent", money(predicted_rent), "Estimate from RentAI's existing CSV-trained model.", "∑"),
            ("Comparable Dataset Median Rent", money(comparison["median_rent"]), f"Median of {comparison['count']:,} filtered CSV records.", "◎"),
        )
        for column, card in zip(comparison_columns, comparison_cards):
            with column:
                _render_owner_analytics_metric(*card)
        model_delta, model_percent = _rent_variance(asking_rent, predicted_rent)
        dataset_delta, dataset_percent = _rent_variance(asking_rent, comparison["median_rent"])
        difference_columns = st.columns(2, gap="medium")
        with difference_columns[0]:
            _render_owner_analytics_metric(
                "Difference Between Asking Rent and ML Estimate",
                model_delta,
                f"{model_percent}; positive means asking rent is above the estimate." if model_percent else "Estimate is unavailable for this property.",
                "↔",
            )
        with difference_columns[1]:
            _render_owner_analytics_metric(
                "Difference Between Asking Rent and Dataset Median",
                dataset_delta,
                f"{dataset_percent}; positive means asking rent is above the comparable median." if dataset_percent else "Comparable median is unavailable for this property.",
                "↔",
            )
        st.caption(
            f"CSV comparables: {comparison['count']:,} · Filters: {', '.join(comparison['filters'])}. "
            "Positive difference means the asking rent is higher than the reference. Dataset and ML values are estimates, not guaranteed market rent."
        )
        if comparison["notes"]:
            st.caption("Comparison notes: " + " ".join(comparison["notes"]))
        if missing_features:
            st.info("ML estimate unavailable; complete these saved property fields: " + ", ".join(missing_features))
        elif predicted_rent is None:
            st.info("The ML estimate is temporarily unavailable. Owner asking rent is unchanged.")
        if category:
            _render_owner_analytics_metric(
                "Predicted Rent Category",
                category,
                "Classifier result using predicted rent and the saved property features.",
                "◇",
            )
        if similar:
            st.dataframe(pd.DataFrame([{
                "Dataset property": item.get("property_id", ""),
                "City": item.get("city", ""), "Locality": item.get("locality", ""),
                "Type": item.get("property_type", ""), "Bedrooms": item.get("bedrooms", ""),
                "Area (sq ft)": item.get("area_sqft", ""), "Dataset rent": money(item.get("rent")),
            } for item in similar]), width="stretch", hide_index=True)


def render_revenue(_data=None) -> None:
    st.title("Revenue & Rent Collection")
    st.caption("Confirm a tenancy explicitly, then record each rent period and mark it paid only after receipt.")
    try:
        listings = records("listing")
        enquiries = records("inquiry")
        tenancies = records("tenancy")
        payments = records("rent_payment")
    except ApplicationAPIError as exc:
        st.error(str(exc))
        return
    details = [record.get("data") or {} for record in listings]
    active = [item for item in details if item.get("status") == "published"]
    potential = sum(float(item.get("rent", 0) or 0) for item in active)
    collected = sum(float((item.get("data") or {}).get("paid_amount", 0) or 0) for item in payments if (item.get("data") or {}).get("status") == "paid")
    outstanding = sum(float((item.get("data") or {}).get("amount", 0) or 0) for item in payments if (item.get("data") or {}).get("status") == "pending")
    a, b, c = st.columns(3)
    revenue_metrics = (
        (a, "Potential monthly listed rent", potential, "Sum of asking rents for published listings; this is an estimate, not rent due."),
        (b, "Total rent collected", collected, "Sum of paid rent records, using each record's saved paid amount."),
        (c, "Outstanding rent", outstanding, "Sum of pending rent records in your portfolio."),
    )
    for column, label, amount, explanation in revenue_metrics:
        with column:
            # The global theme hides Streamlit's built-in metric labels. Render
            # the heading separately so the amount remains understandable.
            st.markdown(f"**{label}**")
            st.metric(label, money(amount), label_visibility="collapsed")
            st.caption(explanation)
    st.subheader("Published rent schedule")
    if active:
        st.dataframe(pd.DataFrame([{"Property": item.get("title", "Property"), "City": item.get("city", ""), "Occupancy": item.get("occupancy_status", "Not tracked"), "Asking rent / month": money(item.get("rent"))} for item in active]), use_container_width=True, hide_index=True)
    else:
        st.info("Publish properties to see expected monthly asking rent.")
    active_enquiry_ids = {
        (item.get("data") or {}).get("inquiry_id")
        for item in tenancies if (item.get("data") or {}).get("status") == "active"
    }
    accepted = [
        item for item in enquiries
        if (item.get("data") or {}).get("status") == "accepted"
        and item.get("id") not in active_enquiry_ids
    ]
    st.subheader("Tenancies")
    st.caption("An accepted enquiry is only a lead. Use this separate action after you have agreed to start a tenancy.")
    if accepted:
        with st.expander("Confirm a tenancy from an accepted enquiry"):
            with st.form("owner_confirm_tenancy"):
                enquiry = st.selectbox(
                    "Accepted enquiry",
                    accepted,
                    format_func=lambda item: f"{(item.get('data') or {}).get('client_name', 'Renter')} · {(item.get('data') or {}).get('property', {}).get('title', 'Property')}",
                )
                enquiry_data = enquiry.get("data") or {}
                listing = next((item for item in listings if item.get("id") == enquiry_data.get("listing_id")), {})
                listing_data = listing.get("data") or {}
                start_date = st.date_input("Tenancy start date", value=date.today())
                monthly_rent = st.number_input("Agreed monthly rent (₹)", min_value=1.0, value=float(listing_data.get("rent", 1) or 1), step=500.0)
                due_day = st.number_input("Rent due day of month", min_value=1, max_value=28, value=1, step=1)
                confirm = st.form_submit_button("Confirm tenancy", type="primary")
            if confirm:
                try:
                    create("tenancy", {
                        "inquiry_id": enquiry["id"], "start_date": start_date.isoformat(),
                        "monthly_rent": monthly_rent, "due_day": due_day,
                    })
                    st.success("Tenancy confirmed and saved.")
                    st.rerun()
                except ApplicationAPIError as exc:
                    st.error(str(exc))
    elif not any((item.get("data") or {}).get("status") == "active" for item in tenancies):
        st.info("No accepted enquiries are available to confirm. An enquiry does not create a tenancy automatically.")

    active_tenancies = [item for item in tenancies if (item.get("data") or {}).get("status") == "active"]
    if active_tenancies:
        st.dataframe(pd.DataFrame([{
            "Property": (item.get("data") or {}).get("property_title", "Property"),
            "Renter": (item.get("data") or {}).get("renter_name", "Renter"),
            "Start date": (item.get("data") or {}).get("start_date", ""),
            "Monthly rent": money((item.get("data") or {}).get("monthly_rent")),
            "Status": (item.get("data") or {}).get("status", "active").title(),
        } for item in active_tenancies]), width="stretch", hide_index=True)
        with st.form("owner_end_tenancy"):
            tenancy_to_end = st.selectbox(
                "Active tenancy",
                active_tenancies,
                format_func=lambda item: f"{(item.get('data') or {}).get('property_title', 'Property')} · {(item.get('data') or {}).get('renter_name', 'Renter')}",
            )
            end_date = st.date_input("Tenancy end date", value=date.today())
            end_tenancy = st.form_submit_button("End tenancy")
        if end_tenancy:
            try:
                update(tenancy_to_end, {**(tenancy_to_end.get("data") or {}), "status": "ended", "end_date": end_date.isoformat()})
                st.success("Tenancy end date saved.")
                st.rerun()
            except ApplicationAPIError as exc:
                st.error(str(exc))
        with st.expander("Record rent due for a tenancy"):
            with st.form("owner_create_rent_period"):
                tenancy = st.selectbox("Tenancy", active_tenancies, format_func=lambda item: f"{(item.get('data') or {}).get('property_title', 'Property')} · {(item.get('data') or {}).get('renter_name', 'Renter')}")
                period = st.text_input("Rent period (YYYY-MM)", value=date.today().strftime("%Y-%m"), placeholder="2026-10")
                save_due = st.form_submit_button("Create pending rent record")
            if save_due:
                try:
                    create("rent_payment", {"tenancy_id": tenancy["id"], "period": period.strip()})
                    st.success("Pending rent record saved.")
                    st.rerun()
                except ApplicationAPIError as exc:
                    st.error(str(exc))

    st.subheader("Rent records")
    if payments:
        st.dataframe(pd.DataFrame([{
            "Property": (item.get("data") or {}).get("property_title", "Property"),
            "Renter": (item.get("data") or {}).get("renter_name", "Renter"),
            "Period": (item.get("data") or {}).get("period", ""),
            "Due date": (item.get("data") or {}).get("due_date", ""),
            "Amount": money((item.get("data") or {}).get("amount")),
            "Status": (item.get("data") or {}).get("status", "pending").title(),
            "Paid date": (item.get("data") or {}).get("paid_date", "—"),
            "Method": (item.get("data") or {}).get("payment_method", "—"),
        } for item in payments]), width="stretch", hide_index=True)
        pending = [
            item for item in payments
            if (item.get("data") or {}).get("status") == "pending"
            and not (item.get("data") or {}).get("razorpay_order")
        ]
        if pending:
            with st.form("owner_mark_rent_paid"):
                payment = st.selectbox("Pending rent record", pending, format_func=lambda item: f"{(item.get('data') or {}).get('property_title', 'Property')} · {(item.get('data') or {}).get('period', '')} · {money((item.get('data') or {}).get('amount'))}")
                paid_date = st.date_input("Date received", value=date.today())
                method = st.selectbox("Payment method", ["UPI", "Bank transfer", "Cash", "Cheque", "Other"])
                reference = st.text_input("Payment reference (optional)", max_chars=120)
                mark_paid = st.form_submit_button("Record payment received", type="primary")
            if mark_paid:
                payment_data = payment.get("data") or {}
                try:
                    update(payment, {
                        **payment_data, "status": "paid", "paid_date": paid_date.isoformat(),
                        "paid_amount": payment_data.get("amount"), "payment_method": method,
                        "payment_reference": reference.strip(),
                    })
                    st.success("Payment receipt saved.")
                    st.rerun()
                except ApplicationAPIError as exc:
                    st.error(str(exc))
        elif any((item.get("data") or {}).get("razorpay_order") for item in payments):
            st.caption("A rent record with an active Razorpay order can be marked Paid only after Razorpay verification.")
    else:
        st.info("No rent due or payment records have been created yet.")

    st.caption("Income and expense accounting beyond recorded rent payments is not available yet. Payment records are entered by the Owner and are not independently verified by RentAI.")


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
    } for item in requests]), use_container_width=True, hide_index=True)
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


def _owner_profile_validation_errors(name: str, phone: str, business: str, bio: str) -> dict[str, str]:
    errors = {}
    if not name.strip():
        errors["name"] = "Enter your full name."
    elif len(name.strip()) > 160:
        errors["name"] = "Your name must be 160 characters or fewer."
    normalized_phone = phone.strip()
    if normalized_phone:
        digits = re.sub(r"\D", "", normalized_phone)
        if (
            not re.fullmatch(r"\+?[0-9][0-9\s().-]*", normalized_phone)
            or not 7 <= len(digits) <= 15
        ):
            errors["phone"] = "Enter a valid phone number with 7 to 15 digits, or leave it blank."
    if len(business.strip()) > 120:
        errors["business_name"] = "Business or portfolio name must be 120 characters or fewer."
    if len(bio.strip()) > 1000:
        errors["bio"] = "About you must be 1,000 characters or fewer."
    return errors


def _owner_profile_completion(profile_data: dict, account_name: str = "") -> int:
    """Count saved Owner profile details, including the authenticated account name."""
    fields = {
        "name": profile_data.get("name") or account_name,
        "phone": profile_data.get("phone"),
        "business_name": profile_data.get("business_name"),
        "bio": profile_data.get("bio"),
    }
    completed = sum(bool(str(value or "").strip()) for value in fields.values())
    return round(completed / len(fields) * 100)


def _owner_profile_widget_keys(uid: str, token: str | None) -> dict[str, str]:
    """Scope form widgets to one authenticated session to prevent stale values."""
    token_fingerprint = hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()[:12]
    scope = f"{uid}_{token_fingerprint}"
    return {
        "name": f"owner_profile_name_{scope}",
        "phone": f"owner_profile_phone_{scope}",
        "business_name": f"owner_profile_business_{scope}",
        "bio": f"owner_profile_bio_{scope}",
    }


def render_profile(_data=None) -> None:
    st.title("My Profile")
    st.caption("Manage the profile and contact details associated with your Owner workspace.")
    try:
        profiles = records("profile")
    except ApplicationAPIError as exc:
        st.error(f"Your profile could not be loaded. {exc}")
        return

    profile = next(
        (item for item in profiles if (item.get("data") or {}).get("record_type") == "owner_profile"),
        None,
    )
    current = (profile or {}).get("data") or {}
    profile_photo_url = None
    if profile:
        try:
            profile_photo_url = next((
                image.get("url") for image in profile_images()
                if image.get("record_id") == profile.get("id") and image.get("url")
            ), None)
        except ApplicationAPIError as exc:
            st.warning(f"Profile photo could not be loaded. {exc}")
    uid = str(st.session_state.get("rentai_uid") or "owner")
    token = st.session_state.get("rentai_id_token")
    widget_keys = _owner_profile_widget_keys(uid, token)
    loaded_token_key = f"owner_profile_loaded_token_{uid}"
    saved_data_key = f"owner_profile_saved_data_{uid}"
    reset_key = f"owner_profile_reset_data_{uid}"
    errors_key = f"owner_profile_errors_{uid}"
    flash_key = f"owner_profile_success_{uid}"
    edit_key = f"owner_profile_editing_{uid}"

    reset_data = st.session_state.pop(reset_key, None)
    if reset_data is not None or (
        st.session_state.get(loaded_token_key) != token
        or st.session_state.get(saved_data_key) != current
    ):
        for field, key in widget_keys.items():
            fallback = st.session_state.get("rentai_display_name", "") if field == "name" else ""
            st.session_state[key] = str(current.get(field) if current.get(field) is not None else fallback)
        st.session_state[loaded_token_key] = token
        st.session_state[saved_data_key] = dict(current)
        st.session_state[edit_key] = False
        st.session_state.pop(errors_key, None)
    elif not st.session_state.get(edit_key, False):
        # Recover stale/blank widget state while in View mode. Do not rewrite
        # fields during Edit mode, where empty values can be intentional edits.
        for field, key in widget_keys.items():
            fallback = st.session_state.get("rentai_display_name", "") if field == "name" else ""
            saved_value = current.get(field) if current.get(field) is not None else fallback
            if key not in st.session_state or (
                not str(st.session_state.get(key) or "").strip()
                and str(saved_value or "").strip()
            ):
                st.session_state[key] = str(saved_value or "")

    completion = _owner_profile_completion(
        current, str(st.session_state.get("rentai_display_name") or "")
    )
    saved_name = str(current.get("name") or st.session_state.get("rentai_display_name") or "Owner")
    initials = "".join(part[0] for part in saved_name.split()[:2]).upper() or "O"
    email = str(st.session_state.get("rentai_email") or "Email unavailable")
    role = str(st.session_state.get("rentai_role") or "owner").replace("_", " ").title()

    st.markdown("""
    <style>
      .owner-profile-completion { margin:0 0 18px; padding:14px 18px; border:1px solid #e2e8f0;
        border-radius:12px; background:#fff; }
      .owner-profile-completion-row { display:flex; justify-content:space-between; gap:12px;
        color:#243247; font-size:13px; font-weight:700; }
      .owner-profile-completion-note { color:#64748b; font-size:12px; margin-top:4px; }
      .owner-profile-section-title { color:#10243a; font-size:16px; font-weight:750; margin:0 0 2px; }
      .owner-profile-section-note { color:#64748b; font-size:12px; margin-bottom:12px; }
      [data-testid="stMainBlockContainer"] [data-testid="stTextInput"] input:not(:disabled),
      [data-testid="stMainBlockContainer"] [data-testid="stTextArea"] textarea {
        color:#0f172a !important; background-color:#fff !important;
        border:1px solid #cbd5e1 !important; border-radius:8px !important;
      }
      [data-testid="stMainBlockContainer"] [data-testid="stTextInput"] input::placeholder,
      [data-testid="stMainBlockContainer"] [data-testid="stTextArea"] textarea::placeholder {
        color:#64748b !important; opacity:1 !important;
      }
      [data-testid="stMainBlockContainer"] [data-testid="stTextInput"] input:disabled {
        color:#475569 !important; background-color:#f1f5f9 !important;
        border:1px solid #cbd5e1 !important; opacity:1 !important;
      }
    </style>
    """, unsafe_allow_html=True)
    avatar_html = (
        f'<img src="{escape(profile_photo_url, quote=True)}" alt="Owner profile photo" '
        'style="width:76px;height:76px;object-fit:cover;border-radius:50%;border:3px solid #ff6b1a;">'
        if profile_photo_url else
        f'<div aria-label="Owner avatar" style="width:76px;height:76px;display:grid;place-items:center;'
        f'border-radius:50%;border:3px solid #ff6b1a;background:#10243a;color:white;font-size:22px;'
        f'font-weight:800">{escape(initials)}</div>'
    )
    st.markdown(
        '<div style="display:flex;align-items:center;gap:16px;margin:4px 0 18px">'
        f'{avatar_html}<div><div style="font-size:22px;font-weight:800;color:#10243a">'
        f'{escape(saved_name)}</div><div style="color:#526176">{escape(email)}</div>'
        f'<div style="margin-top:6px;color:#b9470a;font-size:12px;font-weight:700">{escape(role)} Account</div>'
        '</div></div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div class="owner-profile-completion"><div class="owner-profile-completion-row">'
        f'<span>Profile completion</span><span>{completion}%</span></div>'
        '<div class="owner-profile-completion-note">Based on your saved name, phone, portfolio, and about details.</div></div>',
        unsafe_allow_html=True,
    )
    st.progress(completion / 100)
    if st.session_state.pop(flash_key, False):
        st.success("Your profile was saved successfully.")

    if not st.session_state.get(edit_key, False):
        with st.container(border=True):
            st.markdown("### Profile details")
            st.write(f"**Name:** {saved_name}")
            st.write(f"**Email:** {email}")
            st.write(f"**Phone:** {current.get('phone') or 'Not provided'}")
            st.write(f"**Rental business / portfolio:** {current.get('business_name') or 'Not provided'}")
            st.markdown("#### About the Owner")
            st.write(current.get("bio") or "No introduction added yet.")
        if st.button("Edit Profile", type="primary", key=f"owner_profile_edit_{uid}"):
            for field, key in widget_keys.items():
                fallback = st.session_state.get("rentai_display_name", "") if field == "name" else ""
                value = current.get(field) if current.get(field) is not None else fallback
                st.session_state[key] = str(value or "")
            st.session_state[edit_key] = True
            st.rerun()
        return

    validation_errors = st.session_state.get(errors_key, {})
    if profile is not None:
        st.file_uploader(
            "Choose profile photo", type=["jpg", "jpeg", "png", "webp", "gif"],
            help="Selecting an image uploads it to your authenticated profile photo storage.",
            key=f"owner_profile_photo_{uid}",
        )
        _upload_selected_owner_image(
            st.session_state.get(f"owner_profile_photo_{uid}"), profile["id"],
            f"owner_profile_photo_upload_{uid}", "Profile photo",
        )
    with st.form("owner_profile_form"):
        with st.container(border=True):
            st.markdown('<div class="owner-profile-section-title"><span aria-hidden="true">&#128100;</span> Personal Information</div>', unsafe_allow_html=True)
            st.markdown('<div class="owner-profile-section-note">Use the name you want shown to renters.</div>', unsafe_allow_html=True)
            name = st.text_input(
                "Full name *", key=widget_keys["name"], max_chars=160,
                placeholder="e.g. Aanya Sharma", help="Required; up to 160 characters.",
            )
        with st.container(border=True):
            st.markdown('<div class="owner-profile-section-title"><span aria-hidden="true">&#128222;</span> Contact Information</div>', unsafe_allow_html=True)
            st.markdown('<div class="owner-profile-section-note">Your sign-in email is managed by your account and cannot be changed here.</div>', unsafe_allow_html=True)
            contact_email, contact_phone = st.columns(2)
            contact_email.text_input(
                "Account email", value=email, disabled=True,
                help="This is your authentication identity.",
            )
            phone = contact_phone.text_input(
                "Phone number", key=widget_keys["phone"], max_chars=40,
                placeholder="e.g. +91 98765 43210", help="Optional; include 7 to 15 digits.",
            )
        with st.container(border=True):
            st.markdown('<div class="owner-profile-section-title"><span aria-hidden="true">&#127968;</span> Rental Business / Portfolio</div>', unsafe_allow_html=True)
            st.markdown('<div class="owner-profile-section-note">Add the name renters should associate with your listings.</div>', unsafe_allow_html=True)
            business = st.text_input(
                "Business or portfolio name", key=widget_keys["business_name"], max_chars=120,
                placeholder="e.g. Aundh Homes", help="Optional; up to 120 characters.",
            )
        with st.container(border=True):
            st.markdown('<div class="owner-profile-section-title"><span aria-hidden="true">&#128172;</span> About the Owner</div>', unsafe_allow_html=True)
            st.markdown('<div class="owner-profile-section-note">Share a brief introduction to help renters know who they are contacting.</div>', unsafe_allow_html=True)
            bio = st.text_area(
                "About you", key=widget_keys["bio"], max_chars=1000,
                placeholder="Tell renters about your experience, properties, or how you work.",
                height=110,
            )
        save_col, reset_col = st.columns([1, 1])
        submitted = save_col.form_submit_button("Save Changes", type="primary", width="stretch")
        reset = reset_col.form_submit_button("Cancel", width="stretch")

    if reset:
        st.session_state[reset_key] = dict(current)
        st.session_state.pop(errors_key, None)
        st.session_state[edit_key] = False
        st.rerun()
    if not submitted:
        return

    validation_errors = _owner_profile_validation_errors(name, phone, business, bio)
    if validation_errors:
        st.session_state[errors_key] = validation_errors
        for field, message in validation_errors.items():
            st.error(f"{field.replace('_', ' ').title()}: {message}")
        return

    payload = {
        "record_type": "owner_profile",
        "name": name.strip(),
        "phone": phone.strip(),
        "business_name": business.strip(),
        "bio": bio.strip(),
    }
    try:
        saved = update(profile, payload) if profile else create("profile", payload)
        saved_data = saved.get("data") if isinstance(saved, dict) else None
        saved_data = saved_data or {}
        if not isinstance(saved, dict) or not saved.get("id") or saved_data.get("record_type") != "owner_profile":
            raise ApplicationAPIError("The profile service did not confirm the saved profile. Please try again.")
    except ApplicationAPIError as exc:
        st.session_state[errors_key] = {}
        st.error(f"Profile not saved. {exc}")
        return

    st.session_state[saved_data_key] = dict(saved_data)
    st.session_state[loaded_token_key] = token
    st.session_state[flash_key] = True
    st.session_state[edit_key] = False
    st.session_state.pop(errors_key, None)
    st.rerun()


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
        email_alerts = current.get("email_alerts", True)
        maintenance_alerts = current.get("maintenance_alerts", True)
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


def render_booking_requests(_data=None) -> None:
    st.title("Booking Requests")
    st.caption("Review requests for your own properties. Payment status is separate from your decision.")
    try:
        items = booking_records()
        notifications = booking_notifications()
    except ApplicationAPIError as exc:
        st.error(str(exc))
        return
    if notifications:
        with st.expander("Recent booking notifications", expanded=True):
            for notification in notifications[:5]:
                st.info(f"{notification['message']} · {notification['created_at']}")
    if not items:
        st.info("No booking requests have been submitted for your listings.")
        return
    for item in items:
        prop = item.get("property") or {}
        status = item.get("status", "pending_owner_approval")
        with st.container(border=True):
            st.subheader(prop.get("title", "Owner listing"))
            st.caption(" · ".join(filter(None, [prop.get("locality"), prop.get("city")])))
            columns = st.columns(4)
            columns[0].write(f"**Renter:** {item.get('renter_name', 'Renter')}\n\n{item.get('renter_email', '')}")
            columns[1].write(f"**Move-in:** {item.get('move_in_date', '')}")
            columns[2].write(f"**Monthly rent:** ₹{float(item.get('monthly_rent', 0)):,.0f}")
            columns[3].write(f"**Initial payment:** ₹{int(item.get('amount_due', 0))/100:,.2f} INR")
            st.write(f"**Deposit:** ₹{float(item.get('security_deposit', 0)):,.0f} · **Booking:** {status.replace('_', ' ').title()} · **Payment:** {item.get('payment_status', 'pending').replace('_', ' ').title()}")
            st.caption(f"Booking ID: {item.get('id')} · Request date: {item.get('created_at')}")
            if item.get("availability_conflict"):
                st.error("Verified payment needs manual reconciliation because the listing was no longer available." if item.get("payment_status") == "paid" else "The listing is no longer marked available; payment is disabled.")
            if status == "pending_owner_approval":
                left, right = st.columns(2)
                for column, decision, label in ((left, "accept", "Accept request"), (right, "reject", "Reject request")):
                    if column.button(label, key=f"owner_booking_{decision}_{item['id']}", type="primary" if decision == "accept" else "secondary"):
                        try:
                            decide_booking(item["id"], decision)
                            st.success("Request accepted. Payment remains unpaid until the renter completes verified checkout." if decision == "accept" else "Booking request rejected.")
                            st.rerun()
                        except ApplicationAPIError as exc:
                            st.error(str(exc))
