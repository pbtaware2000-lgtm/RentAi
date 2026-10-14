"""Shared helpers for the authenticated Renter workspace."""

from html import escape
import math

import pandas as pd
import streamlit as st

from frontend.application_api import ApplicationAPIError, create, delete, listing_images, records


def renter_key(name: str) -> str:
    uid = st.session_state.get("rentai_uid") or "session"
    return f"renter_{name}_{uid}"


def load_renter_records() -> dict[str, list[dict]]:
    listings = records("listing")
    image_by_listing = {}
    for image in listing_images():
        image_by_listing.setdefault(image.get("record_id"), image.get("url"))
    for listing in listings:
        detail = listing.get("data") or {}
        if image_by_listing.get(listing.get("id")):
            detail["image_url"] = image_by_listing[listing["id"]]
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
        image_by_listing = {}
        for image in listing_images():
            image_by_listing.setdefault(image.get("record_id"), image.get("url"))
        for listing in result["listings"]:
            detail = listing.get("data") or {}
            if image_by_listing.get(listing.get("id")):
                listing["data"] = {**detail, "image_url": image_by_listing[listing["id"]]}
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
    return next((record for record in favorites
                 if (record.get("data") or {}).get("source", "catalog") == source
                 and str((record.get("data") or {}).get("property_id", "")) == str(key)), None)


def save_property(favorites: list[dict], prop: dict, source: str) -> None:
    key = str(prop.get("property_id") or prop.get("id") or "")
    if not key:
        raise ValueError("This property does not have a reference ID.")
    existing = favorite_for(favorites, source, key)
    if existing:
        delete(existing)
    else:
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
        saved.append({
            "favorite_record": favorite,
            "source": data.get("source", "catalog"),
            "property_id": str(data.get("property_id", "")),
            **prop,
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


def format_money(value: object) -> str:
    try:
        amount = float(value)
        if not math.isfinite(amount):
            return "Rent not listed"
        return f"₹{amount:,.0f} / month"
    except (TypeError, ValueError):
        return "Rent not listed"


def display_property_card(prop: dict, *, source: str, favorites: list[dict] | None, key_suffix: str) -> None:
    title = escape(str(prop.get("title") or f"{prop.get('property_type', 'Rental')} property"))
    locality = escape(str(prop.get("locality") or ""))
    city = escape(str(prop.get("city") or ""))
    details = " · ".join(filter(None, [locality, city]))
    image = prop.get("image_url") or prop.get("image") or prop.get("photo_url")
    with st.container(border=True):
        if isinstance(image, str) and image.startswith(("https://", "http://")):
            st.image(image, width="stretch")
        else:
            st.markdown("<div class='renter-property-placeholder'>⌂</div>", unsafe_allow_html=True)
        st.markdown(f"**{title}**")
        st.caption(details or "Location not provided")
        st.markdown(f"### {format_money(prop.get('rent'))}")
        attrs = []
        for field, label in (("bedrooms", "bd"), ("bathrooms", "ba"), ("area_sqft", "sq ft")):
            value = prop.get(field)
            if value is not None and not pd.isna(value):
                attrs.append(f"{value} {label}")
        st.caption("  ·  ".join(attrs) if attrs else "Property details are limited")
        if source == "owner_listing":
            if key_suffix == "saved":
                st.caption("Saved Owner listing snapshot · publication status may have changed")
            else:
                st.caption("Published Owner listing")
        else:
            st.caption("RentAI dataset property · not a live Owner listing")
        with st.expander("Property details"):
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
        key = str(prop.get("property_id") or prop.get("id") or "")
        widget_ref = f"{source}_{key}"
        existing = favorite_for(favorites or [], source, key)
        a, b = st.columns(2)
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


def dataset_properties(data: pd.DataFrame) -> list[dict]:
    if data.empty:
        return []
    return data.where(pd.notna(data), None).to_dict(orient="records")


def owner_listing_properties(listings: list[dict]) -> list[dict]:
    return [
        {"id": record.get("id"), "source": "owner_listing", **(record.get("data") or {})}
        for record in listings
        if (record.get("data") or {}).get("status") == "published"
    ]
