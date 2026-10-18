"""Shared helpers for the authenticated Renter workspace."""

from html import escape
import math

import pandas as pd
import streamlit as st

from frontend.application_api import ApplicationAPIError, create, delete, listing_images, records, request_booking
from frontend.components.property_images import render_property_image

CATALOG_SOURCE = "catalog"
OWNER_LISTING_SOURCE = "owner_listing"
UNKNOWN_SOURCE = "unknown"
KNOWN_PROPERTY_SOURCES = {CATALOG_SOURCE, OWNER_LISTING_SOURCE}


def renter_key(name: str) -> str:
    uid = st.session_state.get("rentai_uid") or "session"
    return f"renter_{name}_{uid}"


def load_renter_records() -> dict[str, list[dict]]:
    listings = records("listing")
    images_by_listing = {}
    for image in listing_images():
        if image.get("record_id") and image.get("url", "").startswith(("https://", "http://")):
            images_by_listing.setdefault(image["record_id"], []).append(image["url"])
    for listing in listings:
        detail = listing.get("data") or {}
        urls = images_by_listing.get(listing.get("id"), [])
        if urls:
            detail["image_urls"] = urls
            detail["image_url"] = urls[0]
            listing["data"] = detail
    return {
        "favorites": records("favorite"),
        "enquiries": records("inquiry"),
        "listings": listings,
        "profiles": records("profile"),
        "maintenance": records("maintenance"),
    }


def load_search_records() -> tuple[dict[str, list[dict] | None], list[ApplicationAPIError]]:
    result: dict[str, list[dict] | None] = {
        "listings": None, "favorites": None, "profiles": None,
    }
    errors = []
    try:
        result["listings"] = records("listing")
    except ApplicationAPIError as exc:
        return result, [exc]

    try:
        images_by_listing = {}
        for image in listing_images():
            if image.get("record_id") and image.get("url", "").startswith(("https://", "http://")):
                images_by_listing.setdefault(image["record_id"], []).append(image["url"])
        for listing in result["listings"]:
            detail = listing.get("data") or {}
            urls = images_by_listing.get(listing.get("id"), [])
            if urls:
                listing["data"] = {**detail, "image_urls": urls, "image_url": urls[0]}
    except ApplicationAPIError as exc:
        errors.append(exc)

    for key, kind in (("favorites", "favorite"), ("profiles", "profile")):
        try:
            result[key] = records(kind)
        except ApplicationAPIError as exc:
            errors.append(exc)
            break
    return result, errors


def property_id(item: dict) -> str:
    return str(item.get("property_id") or item.get("id") or "")


def favorite_for(favorites: list[dict], source: str, key: str) -> dict | None:
    for record in favorites:
        data = record.get("data") or {}
        record_source = data.get("source")
        if not isinstance(record_source, str) or record_source not in KNOWN_PROPERTY_SOURCES:
            record_source = UNKNOWN_SOURCE
        if record_source == source and str(data.get("property_id", "")) == str(key):
            return record
    return None


def save_property(favorites: list[dict], prop: dict, source: str) -> None:
    key = str(prop.get("property_id") or prop.get("id") or "")
    if not key:
        raise ValueError("This property does not have a reference ID.")
    existing = favorite_for(favorites, source, key)
    if existing:
        delete(existing)
    else:
        if source not in KNOWN_PROPERTY_SOURCES:
            raise ValueError("This property's source is not verified, so it cannot be saved as a new listing.")
        create("favorite", {
            "property_id": key,
            "source": source,
            "property": prop,
        })


def saved_properties(favorites: list[dict]) -> list[dict]:
    saved = []
    for favorite in favorites:
        data = favorite.get("data") or {}
        prop = data.get("property")
        if not isinstance(prop, dict):
            continue
        source = data.get("source")
        if not isinstance(source, str) or source not in KNOWN_PROPERTY_SOURCES:
            source = UNKNOWN_SOURCE
        saved.append({
            "favorite_record": favorite,
            **prop,
            "source": source,
            "property_id": str(data.get("property_id", "")),
        })
    return saved


def add_to_comparison(prop: dict, source: str) -> bool:
    key = str(prop.get("property_id") or prop.get("id") or "")
    if not key:
        return False
    state_key = renter_key("compare")
    current = st.session_state.get(state_key, [])
    identity = f"{source}:{key}"
    if identity not in current and len(current) < 4:
        st.session_state[state_key] = [*current, identity]
        return True
    return identity in current


def use_for_rent_estimate(prop: dict) -> None:
    """Open the estimator with recorded dataset features prefilled when present."""
    st.session_state["prediction_result"] = None
    st.session_state["prediction_error"] = None
    state_values = {
        "prediction-city": ("city", str),
        "prediction-locality": ("locality", str),
        "prediction-property_type": ("property_type", str),
        "prediction-furnishing_status": ("furnishing_status", str),
        "prediction-parking": ("parking", str),
        "prediction-area-sqft": ("area_sqft", float),
        "prediction-bedrooms": ("bedrooms", int),
        "prediction-bathrooms": ("bathrooms", int),
        "prediction-balcony": ("balcony", int),
        "prediction-floor": ("floor", int),
        "prediction-total-floors": ("total_floors", int),
        "prediction-distance": ("distance_to_city_center_km", float),
        "prediction-age": ("age_of_property", int),
        "prediction-water-supply": ("water_supply", str),
    }
    optional_distances = (
        "distance_to_metro_km", "distance_to_school_km",
        "distance_to_hospital_km", "distance_to_it_park_km",
    )
    for key in state_values:
        st.session_state.pop(key, None)
    st.session_state.pop("prediction-amenities", None)
    for name in optional_distances:
        st.session_state.pop(f"prediction-provide-{name}", None)
        st.session_state.pop(f"prediction-{name}", None)
    for widget_key, (field, convert) in state_values.items():
        value = prop.get(field)
        if value is not None and not pd.isna(value):
            try:
                st.session_state[widget_key] = convert(value)
            except (TypeError, ValueError):
                continue
    for name in optional_distances:
        value = prop.get(name)
        if value is not None and not pd.isna(value):
            try:
                st.session_state[f"prediction-provide-{name}"] = True
                st.session_state[f"prediction-{name}"] = float(value)
            except (TypeError, ValueError):
                continue
    amenity_fields = ("lift", "gym", "security", "power_backup", "swimming_pool", "air_conditioning", "gated_community")
    st.session_state["prediction-amenities"] = [
        name for name in amenity_fields
        if str(prop.get(name, "")).strip().casefold() in {"yes", "true", "1"}
    ]
    st.session_state["renter_active_page"] = "AI Rent Prediction"


def open_owner_enquiry(prop: dict) -> None:
    listing_id = str(prop.get("id") or "")
    if not listing_id:
        return
    st.session_state[renter_key("enquiry_listing_id")] = listing_id
    st.session_state["renter_active_page"] = "My Enquiries"


def format_money(value: object) -> str:
    try:
        amount = float(value)
        if not math.isfinite(amount):
            return "Rent not listed"
        return f"₹{amount:,.0f} / month"
    except (TypeError, ValueError):
        return "Rent not listed"


def display_property_card(
    prop: dict,
    *,
    source: str,
    favorites: list[dict] | None,
    key_suffix: str,
    published_owner_listing_ids: set[str] | None = None,
) -> None:
    title = escape(str(prop.get("title") or f"{prop.get('property_type', 'Rental')} property"))
    locality = escape(str(prop.get("locality") or ""))
    city = escape(str(prop.get("city") or ""))
    details = " · ".join(filter(None, [locality, city]))
    key = str(prop.get("property_id") or prop.get("id") or "")
    safe_key = "".join(char if char.isalnum() or char in "-_" else "-" for char in key)[:80]
    with st.container(border=True, key=f"rentai-property-card-{source}-{key_suffix}-{safe_key}"):
        illustrative = render_property_image(prop, source, key_suffix=key_suffix)
        if illustrative:
            st.caption("Illustrative image · this dataset property's photo is not verified.")
        st.markdown(f"**{title}**")
        st.caption(details or "Location not provided")
        st.markdown(f"### {format_money(prop.get('rent'))}")
        attrs = []
        for field, label in (("bedrooms", "bd"), ("bathrooms", "ba"), ("area_sqft", "sq ft")):
            value = prop.get(field)
            if value is not None and not pd.isna(value):
                attrs.append(f"{value} {label}")
        st.caption("  ·  ".join(attrs) if attrs else "Property details are limited")
        is_published_owner_listing = source == OWNER_LISTING_SOURCE and (
            key_suffix != "saved" or key in (published_owner_listing_ids or set())
        )
        if source == OWNER_LISTING_SOURCE:
            if key_suffix == "saved":
                st.caption(
                    "Saved Owner listing snapshot · currently published"
                    if is_published_owner_listing
                    else "Saved Owner listing snapshot · publication status not verified"
                )
            else:
                if prop.get("occupancy_status") == "vacant":
                    st.caption("Published Owner listing · owner reports vacant; availability is checked again when you request")
                elif prop.get("occupancy_status") == "occupied":
                    st.caption("Published Owner listing · owner reports occupied")
                else:
                    st.caption("Published Owner listing · vacancy is not tracked; booking requests are disabled")
        elif source == CATALOG_SOURCE:
            st.caption("ML dataset property — availability not verified.")
        else:
            st.caption("RentAI dataset property · not a live Owner listing")
        with st.expander("Property details"):
            gallery = prop.get("image_urls")
            if isinstance(gallery, list):
                gallery_urls = [url for url in gallery if isinstance(url, str) and url.startswith(("https://", "http://"))]
                if gallery_urls:
                    st.markdown("**Property photos**")
                    gallery_columns = st.columns(min(4, len(gallery_urls)))
                    for index, url in enumerate(gallery_urls):
                        with gallery_columns[index % len(gallery_columns)]:
                            st.image(url, caption=f"Photo {index + 1}", width="stretch")
            description = prop.get("description")
            if isinstance(description, str) and description.strip():
                st.write(description)
            for field in (
                "property_type", "furnishing_status", "occupancy_status", "bathrooms",
                "area_sqft", "security_deposit", "maintenance", "available_from",
            ):
                value = prop.get(field)
                if value is not None:
                    st.write(f"**{field.replace('_', ' ').title()}:** {value}")
        widget_ref = f"{source}_{key}"
        existing = favorite_for(favorites or [], source, key)
        a, b, c = st.columns(3)
        with a:
            if favorites is None:
                st.button("Save unavailable", key=f"save_{key_suffix}_{widget_ref}", width="stretch", disabled=True)
            elif st.button("Remove saved" if existing else "Save", key=f"save_{key_suffix}_{widget_ref}", width="stretch"):
                try:
                    save_property(favorites, prop, source)
                    st.rerun()
                except (ApplicationAPIError, ValueError) as exc:
                    st.error(str(exc))
        with b:
            if st.button("Compare", key=f"compare_{key_suffix}_{widget_ref}", width="stretch"):
                added = add_to_comparison(prop, source)
                st.success("Added to comparison." if added else "Comparison is limited to four properties.")
        with c:
            if source == CATALOG_SOURCE:
                if st.button("Use for rent estimate", key=f"estimate_{key_suffix}_{widget_ref}", width="stretch"):
                    use_for_rent_estimate(prop)
                    st.rerun()
            elif source == OWNER_LISTING_SOURCE and is_published_owner_listing:
                if st.button("Send enquiry", key=f"enquire_{key_suffix}_{widget_ref}", width="stretch"):
                    open_owner_enquiry(prop)
                    st.rerun()
        if (source == OWNER_LISTING_SOURCE and is_published_owner_listing
                and prop.get("occupancy_status") == "vacant"):
            with st.expander("Request booking"):
                with st.form(f"booking_request_{widget_ref}"):
                    move_in = st.date_input("Requested move-in date", min_value=__import__("datetime").date.today())
                    submitted = st.form_submit_button("Send booking request", type="primary", width="stretch")
                if submitted:
                    try:
                        request_booking(key, move_in.isoformat())
                        st.success("Booking request sent. The owner must approve it before payment is available.")
                        st.session_state["renter_active_page"] = "My Bookings"
                        st.rerun()
                    except ApplicationAPIError as exc:
                        st.error(str(exc))
        st.caption("Saving only bookmarks this property; it does not reserve or book it.")
def dataset_properties(data: pd.DataFrame) -> list[dict]:
    if data.empty:
        return []
    return [{**prop, "source": CATALOG_SOURCE}
            for prop in data.where(pd.notna(data), None).to_dict(orient="records")]


def owner_listing_properties(listings: list[dict]) -> list[dict]:
    return [
        {**(record.get("data") or {}), "id": record.get("id"), "source": OWNER_LISTING_SOURCE}
        for record in listings
        if (record.get("data") or {}).get("status") == "published"
    ]
