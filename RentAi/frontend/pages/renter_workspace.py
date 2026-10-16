"""Pages and shared rendering for the authenticated Renter workspace."""

from html import escape
import logging

import pandas as pd
import streamlit as st

from frontend.application_api import (
    ApplicationAPIError,
    create,
    create_rent_payment_order,
    delete,
    records,
    update,
    verify_rent_payment,
)
from frontend.components.razorpay_checkout import razorpay_checkout
from frontend.pages import rent_prediction
from frontend.pages.renter_support import (
    add_to_comparison, dataset_properties, display_property_card, favorite_for,
    format_money, load_renter_records, owner_listing_properties,
    load_search_records, property_id, renter_key, saved_properties,
)


# Temporary, value-free checkout diagnostics; remove after confirming the live flow.
_payment_logger = logging.getLogger(__name__)


def filter_properties(items: list[tuple[dict, str]], filters: dict) -> list[tuple[dict, str]]:
    """Apply renter search filters to catalog and public Owner listing records."""
    matches = []
    for prop, item_source in items:
        rent = pd.to_numeric(pd.Series([prop.get("rent")]), errors="coerce").iloc[0]
        if filters.get("city") and str(prop.get("city")) != filters["city"]: continue
        if filters.get("locality") and filters["locality"].casefold() not in str(prop.get("locality", "")).casefold(): continue
        if filters.get("property_type") and str(prop.get("property_type")) != filters["property_type"]: continue
        if filters.get("source") == "RentAI dataset" and item_source != "catalog": continue
        if filters.get("source") == "Owner listings" and item_source != "owner_listing": continue
        if filters.get("min_rent", 0) and (pd.isna(rent) or rent < filters["min_rent"]): continue
        if filters.get("max_rent", 0) and (pd.isna(rent) or rent > filters["max_rent"]): continue
        if filters.get("bedrooms", "Any") != "Any" and str(prop.get("bedrooms")) != str(filters["bedrooms"]): continue
        matches.append((prop, item_source))
    sort_by = filters.get("sort_by", "rent")
    matches.sort(key=lambda pair: pd.to_numeric(pd.Series([pair[0].get(sort_by)]), errors="coerce").fillna(float("inf")).iloc[0], reverse=filters.get("sort_order", "asc") == "desc")
    return matches


def _load(data: pd.DataFrame) -> dict | None:
    try:
        result = load_renter_records()
        result["catalog"] = dataset_properties(data)
        renter_settings = next((r.get("data") or {} for r in result["profiles"]
                                if (r.get("data") or {}).get("record_type") == "renter_settings"), {})
        renter_profile = next((r.get("data") or {} for r in result["profiles"]
                               if (r.get("data") or {}).get("record_type") == "renter_profile"), {})
        if renter_settings:
            st.session_state[renter_key("settings")] = renter_settings
        if renter_profile.get("name"):
            st.session_state[renter_key("profile_name")] = renter_profile["name"]
        return result
    except ApplicationAPIError as exc:
        st.error(str(exc))
        return None


def _page_header(title: str, description: str) -> None:
    st.title(title)
    st.caption(description)


def render_dashboard(data: pd.DataFrame) -> None:
    result = _load(data)
    if result is None:
        return
    name = escape(st.session_state.get(renter_key("profile_name")) or st.session_state.get("rentai_display_name") or "there")
    listings = owner_listing_properties(result["listings"])
    listings_by_id = {str(prop.get("id")): prop for prop in listings}
    searches = st.session_state.get(renter_key("searches"), [])
    st.markdown("""
    <style>
      [data-testid='stMetricLabel'] { display:block!important; }
      .renter-hero { background:linear-gradient(115deg,#07101f,#17415e); color:#fff;
        border-radius:18px; padding:1.6rem 1.9rem; margin:.3rem 0 1rem; }
      .renter-hero h1 { color:#fff!important; font-size:1.8rem!important; margin:0 0 .35rem; }
      .renter-hero p { color:#cbd5e1!important; margin:0; }
      .renter-property-placeholder { height:110px; border-radius:10px; display:grid; place-items:center;
        color:#94a3b8; font-size:3rem; background:linear-gradient(135deg,#eaf0f5,#f8fafc); }
      div[data-testid='stVerticalBlockBorderWrapper'] { border-radius:14px; border-color:#e2e8f0; }
    </style>
    """, unsafe_allow_html=True)
    top = st.columns([2.5, 1, 1, 1])
    top[0].markdown("**RentAI  /  Renter Dashboard**")
    if top[1].button("⌕  Search", key="renter_top_search", width="stretch"):
        st.session_state.renter_active_page = "Search Properties"
        st.rerun()
    if top[2].button(f"✉  Enquiries ({len(result['enquiries'])})", key="renter_top_enquiries", width="stretch"):
        st.session_state.renter_active_page = "My Enquiries"
        st.rerun()
    top[3].caption(f"Signed in as **{name}**")
    st.markdown(f"<div class='renter-hero'><h1>Welcome home, {name}</h1><p>Find a rental that fits your next move.</p></div>", unsafe_allow_html=True)

    with st.container(border=True):
        st.markdown("**Find your next home**")
        city_options = sorted({str(p.get("city")) for p in result["catalog"] if p.get("city")})
        with st.form("renter_quick_search"):
            cols = st.columns([1, 1, 1, 1, 1, .75])
            city = cols[0].selectbox("City", ["Any"] + city_options)
            locality = cols[1].text_input("Locality")
            type_options = sorted({str(p.get("property_type")) for p in result["catalog"] if p.get("property_type")})
            property_type = cols[2].selectbox("Property type", ["Any"] + type_options)
            min_budget = cols[3].number_input("Min budget (₹)", min_value=0, step=5000)
            max_budget = cols[4].number_input("Max budget (₹)", min_value=0, step=5000)
            submitted = cols[5].form_submit_button("Search", type="primary", width="stretch")
        if submitted:
            if max_budget and min_budget > max_budget:
                st.error("Minimum budget cannot exceed the maximum budget.")
                return
            st.session_state[renter_key("search_filters")] = {
                "city": "" if city == "Any" else city,
                "locality": locality.strip(),
                "property_type": "" if property_type == "Any" else property_type,
                "min_rent": min_budget or None,
                "max_rent": max_budget or None,
            }
            if city != "Any" or locality.strip() or property_type != "Any" or min_budget or max_budget:
                searches = st.session_state.get(renter_key("searches"), [])
                budget_label = f"₹{min_budget:,.0f}–₹{max_budget:,.0f}" if max_budget else f"from ₹{min_budget:,.0f}" if min_budget else ""
                query = " · ".join(filter(None, [city if city != "Any" else "", locality.strip(), property_type if property_type != "Any" else "", budget_label]))
                st.session_state[renter_key("searches")] = ([query] + [item for item in searches if item != query])[:5]
            st.session_state.renter_active_page = "Search Properties"
            st.rerun()

    cards = st.columns(4)
    metrics = (
        ("Catalog properties", f"{len(result['catalog']):,}", "Dataset entries · availability unverified"),
        ("Saved properties", str(len(result["favorites"])), "Your private saved list"),
        ("My enquiries", str(len(result["enquiries"])), "Enquiries you have sent"),
        ("Recent searches", str(len(searches)), "This session only"),
    )
    for column, (label, value, note) in zip(cards, metrics):
        with column, st.container(border=True):
            st.metric(label, value)
            st.caption(note)

    st.subheader("Lower-rent dataset options")
    st.caption("Sorted by the dataset asking rent; this is not a personalized recommendation or a live availability claim.")
    if result["catalog"]:
        recommendations = sorted(
            result["catalog"], key=lambda p: float(p.get("rent") or float("inf"))
        )[:3]
        cols = st.columns(3)
        for col, prop in zip(cols, recommendations):
            with col:
                display_property_card(prop, source="catalog", favorites=result["favorites"], key_suffix="home")
    else:
        st.info("The property catalog is unavailable right now.")

    st.subheader("Published Owner listings")
    if listings:
        cols = st.columns(min(3, len(listings)))
        for col, prop in zip(cols, listings[:3]):
            with col:
                display_property_card(prop, source="owner_listing", favorites=result["favorites"], key_suffix="home_owner")
    else:
        st.info("No published Owner listings are available yet. Dataset properties are shown separately and cannot receive enquiries.")
    st.subheader("Recently posted in the catalog")
    dated_catalog = [p for p in result["catalog"] if p.get("posted_date")]
    if dated_catalog:
        recent_properties = sorted(dated_catalog, key=lambda p: str(p.get("posted_date")), reverse=True)[:3]
        cols = st.columns(len(recent_properties))
        for col, prop in zip(cols, recent_properties):
            with col:
                display_property_card(prop, source="catalog", favorites=result["favorites"], key_suffix="recent")
    else:
        st.caption("The catalog does not provide posting dates for recent property ordering.")
    left, right = st.columns(2)
    with left, st.container(border=True):
        st.markdown("**AI rent estimate**")
        st.caption("Use RentAI’s trained model to estimate rent from property details.")
        if st.button("Open AI Rent Prediction", key="renter_home_prediction"):
            st.session_state.renter_active_page = "AI Rent Prediction"
            st.rerun()
    with right, st.container(border=True):
        st.markdown("**Recent searches**")
        if searches:
            for search in searches[:4]:
                st.write(f"⌕  {search}")
        else:
            st.caption("Search history from this session will appear here.")

    st.subheader("Maintenance requests")
    if listings:
        with st.expander("Report a maintenance issue", expanded=not result["maintenance"]):
            with st.form("renter_dashboard_maintenance"):
                listing_id = st.selectbox("Published property", list(listings_by_id), format_func=lambda key: f"{listings_by_id[key].get('title', 'Property')} · {listings_by_id[key].get('city', '')}")
                issue = st.text_input("Issue title", max_chars=160)
                description = st.text_area("Describe the problem", max_chars=2000)
                send_request = st.form_submit_button("Send maintenance request")
            if send_request:
                if not issue.strip() or not description.strip():
                    st.error("Enter an issue title and description.")
                else:
                    try:
                        create("maintenance", {"listing_id": listing_id, "title": issue.strip(), "description": description.strip()})
                        st.success("Maintenance request sent to the property Owner.")
                        st.rerun()
                    except ApplicationAPIError as exc:
                        st.error(str(exc))
    elif not result["maintenance"]:
        st.info("Maintenance can be reported against a published Owner listing when one is available.")
    if result["maintenance"]:
        st.dataframe(pd.DataFrame([{
            "Issue": (record.get("data") or {}).get("title", ""),
            "Property": listings_by_id.get((record.get("data") or {}).get("listing_id"), {}).get("title", "Property listing"),
            "Status": (record.get("data") or {}).get("status", "open").replace("_", " ").title(),
        } for record in result["maintenance"]]), width="stretch", hide_index=True)


def render_search(data: pd.DataFrame) -> None:
    records_result, errors = load_search_records()
    for exc in errors:
        st.warning(f"{exc} Your RentAI dataset search is still available.")
        if exc.status_code == 404 and exc.method and exc.path:
            detail = f" · {exc.response_detail}" if exc.response_detail else ""
            st.caption(f"{exc.method} {exc.path} returned HTTP 404{detail}")
    result = {**records_result, "catalog": dataset_properties(data)}
    settings = next((r.get("data") or {} for r in (result["profiles"] or [])
                     if (r.get("data") or {}).get("record_type") == "renter_settings"), {})
    _page_header("Search Properties", "Search the RentAI dataset and published Owner listings. Each source is labeled.")
    if st.session_state.pop(renter_key("enquiry_success"), False):
        st.success("Your enquiry was sent to the property Owner.")
    catalog = result["catalog"]
    owner_listings = owner_listing_properties(result["listings"] or [])
    all_items = [(p, "catalog") for p in catalog] + [(p, "owner_listing") for p in owner_listings]
    if not all_items:
        if result["listings"] is None:
            st.info("The CSV catalog is empty. Live Owner listings could not be loaded.")
        else:
            st.info("No published Owner listings or CSV catalog properties are available.")
        return
    city_options = sorted({str(p.get("city")) for p, _ in all_items if p.get("city")})
    type_options = sorted({str(p.get("property_type")) for p, _ in all_items if p.get("property_type")})
    rents = [float(p["rent"]) for p, _ in all_items if p.get("rent") is not None]
    highest = max(rents) if rents else 0
    saved_filters = st.session_state.get(renter_key("search_filters"), {})
    with st.form("renter_property_search"):
        row1 = st.columns(4)
        city = row1[0].selectbox("City", ["Any"] + city_options, index=(city_options.index(saved_filters["city"])+1 if saved_filters.get("city") in city_options else 0), key="renter_search_city")
        locality = row1[1].text_input("Locality contains", value=saved_filters.get("locality", ""), key="renter_search_locality")
        prop_type = row1[2].selectbox("Property type", ["Any"] + type_options, index=(type_options.index(saved_filters["property_type"])+1 if saved_filters.get("property_type") in type_options else 0), key="renter_search_type")
        source = row1[3].selectbox("Source", ["All sources", "RentAI dataset", "Owner listings"], key="renter_search_source")
        row2 = st.columns(4)
        min_rent = row2[0].number_input("Minimum rent (₹)", min_value=0, max_value=int(highest) if highest else 0, value=min(int(saved_filters.get("min_rent") or 0), int(highest) if highest else 0), step=1000, key="renter_search_min")
        max_rent = row2[1].number_input("Maximum rent (₹)", min_value=0, max_value=int(highest) if highest else 0, value=min(int(saved_filters.get("max_rent") or highest), int(highest)) if highest else 0, step=1000, key="renter_search_max")
        bedrooms = row2[2].selectbox("Bedrooms", ["Any"] + sorted({str(p.get("bedrooms")) for p, _ in all_items if p.get("bedrooms") is not None}), key="renter_search_bedrooms")
        default_sort = settings.get("sort_by", st.session_state.get(renter_key("settings"), {}).get("sort_by", "rent"))
        sort_by = row2[3].selectbox("Sort by", ["rent", "area_sqft", "bedrooms"], index=["rent", "area_sqft", "bedrooms"].index(default_sort) if default_sort in {"rent", "area_sqft", "bedrooms"} else 0, format_func=lambda x: {"rent": "Monthly rent", "area_sqft": "Area", "bedrooms": "Bedrooms"}[x])
        controls = st.columns(2)
        submit = controls[0].form_submit_button("Search properties", type="primary", width="stretch")
        clear = controls[1].form_submit_button("Clear filters", width="stretch")
    if clear:
        st.session_state.pop(renter_key("search_filters"), None)
        st.session_state.pop(renter_key("search_results"), None)
        st.session_state.pop(renter_key("owner_search_page"), None)
        st.session_state.pop(renter_key("catalog_search_page"), None)
        for widget_key in ("renter_search_city", "renter_search_locality", "renter_search_type", "renter_search_source", "renter_search_min", "renter_search_max", "renter_search_bedrooms"):
            st.session_state.pop(widget_key, None)
        st.rerun()
    if submit:
        if min_rent > max_rent:
            st.error("Minimum rent cannot exceed the maximum rent.")
            return
        filters = {"city": "" if city == "Any" else city, "locality": locality.strip(), "property_type": "" if prop_type == "Any" else prop_type,
                   "source": source, "min_rent": min_rent, "max_rent": max_rent, "bedrooms": bedrooms, "sort_by": sort_by}
        st.session_state[renter_key("search_filters")] = filters
        st.session_state[renter_key("owner_search_page")] = 0
        st.session_state[renter_key("catalog_search_page")] = 0
        label = " · ".join(filter(None, [filters["city"], filters["locality"], filters["property_type"], f"₹{min_rent:,.0f}–₹{max_rent:,.0f}" if max_rent else ""]))
        if label:
            previous = st.session_state.get(renter_key("searches"), [])
            st.session_state[renter_key("searches")] = ([label] + [x for x in previous if x != label])[:5]
    filters = dict(st.session_state.get(renter_key("search_filters"), {}))
    filters.setdefault("sort_order", st.session_state.get(renter_key("settings"), {}).get("sort_order", "asc"))
    owner_matches = filter_properties([(prop, "owner_listing") for prop in owner_listings], filters)
    catalog_matches = filter_properties([(prop, "catalog") for prop in catalog], filters)
    page_size = 30
    st.subheader("Published Owner listings")
    if result["listings"] is None:
        st.info("Published Owner listings are unavailable because the live listing request failed.")
    elif not owner_matches:
        st.info("No published Owner listings match these filters right now.")
    else:
        st.caption(f"{len(owner_matches):,} matching live listings")
        owner_page = st.session_state.get(renter_key("owner_search_page"), 0)
        visible_owner = owner_matches[:(owner_page + 1) * page_size]
        for start in range(0, len(visible_owner), 3):
            cols = st.columns(3)
            for col, (prop, _) in zip(cols, visible_owner[start:start + 3]):
                with col:
                    display_property_card(prop, source="owner_listing", favorites=result["favorites"], key_suffix="search")
        _render_listing_enquiry(owner_listings)
        if len(visible_owner) < len(owner_matches) and st.button("Load more Owner listings", key="renter_owner_search_load_more"):
            st.session_state[renter_key("owner_search_page")] = owner_page + 1
            st.rerun()

    st.subheader("RentAI dataset catalog")
    st.caption("Dataset asking rents are estimates; these rows are not verified live rental availability.")
    if not catalog_matches:
        st.info("No CSV catalog properties match these filters.")
    else:
        st.caption(f"{len(catalog_matches):,} matching catalog entries")
        catalog_page = st.session_state.get(renter_key("catalog_search_page"), 0)
        visible_catalog = catalog_matches[:(catalog_page + 1) * page_size]
        for start in range(0, len(visible_catalog), 3):
            cols = st.columns(3)
            for col, (prop, _) in zip(cols, visible_catalog[start:start + 3]):
                with col:
                    display_property_card(prop, source="catalog", favorites=result["favorites"], key_suffix="search")
        if len(visible_catalog) < len(catalog_matches) and st.button("Load more catalog properties", key="renter_catalog_search_load_more"):
            st.session_state[renter_key("catalog_search_page")] = catalog_page + 1
            st.rerun()


def _render_listing_enquiry(owner_listings: list[dict]) -> None:
    state_key = renter_key("enquiry_listing")
    selected_id = st.session_state.get(state_key)
    if not selected_id:
        return
    selected = next((item for item in owner_listings if str(item.get("id")) == str(selected_id)), None)
    if selected is None:
        st.session_state.pop(state_key, None)
        st.info("That listing is no longer published and cannot receive an enquiry.")
        return

    st.subheader("Send an enquiry")
    st.caption(
        f"{selected.get('title') or 'Property'} · "
        f"{', '.join(filter(None, [selected.get('locality'), selected.get('city')]))} · "
        f"{format_money(selected.get('rent'))}"
    )
    with st.form("renter_search_send_enquiry"):
        name = st.text_input(
            "Your name *", value=st.session_state.get("rentai_display_name", ""), max_chars=160,
        )
        phone = st.text_input("Phone (optional)", max_chars=40)
        message = st.text_area("Optional message to the Owner", max_chars=2000)
        send = st.form_submit_button("Submit enquiry", type="primary")
    cancel_col, _ = st.columns([1, 4])
    cancel = cancel_col.button("Cancel", key="renter_cancel_search_enquiry")
    if cancel:
        st.session_state.pop(state_key, None)
        st.rerun()
    if not send:
        return
    if not name.strip():
        st.error("Enter your name so the Owner knows who sent the enquiry.")
        return
    try:
        create("inquiry", {
            "listing_id": str(selected["id"]),
            "client_name": name.strip(),
            "client_phone": phone.strip(),
            "message": message.strip(),
        })
        st.session_state.pop(state_key, None)
        st.session_state[renter_key("enquiry_success")] = True
        st.rerun()
    except ApplicationAPIError as exc:
        st.error(str(exc))


def render_prediction(data: pd.DataFrame) -> None:
    _page_header("AI Rent Prediction", "Estimate monthly rent using the existing RentAI regression model.")
    left, right = st.columns([.75, 1.5])
    with left:
        st.markdown("<div style='background:linear-gradient(150deg,#07101f,#17415e);color:#fff;border-radius:16px;padding:1.5rem;min-height:240px'><h2 style='color:#fff'>Understand the rent</h2><p style='color:#cbd5e1'>Provide the same property features supported by RentAI’s trained model to get an estimate.</p><p style='color:#cbd5e1'>No estimate is shown until the model successfully runs.</p></div>", unsafe_allow_html=True)
    with right:
        rent_prediction.render(data)


def _available_for_compare(data: pd.DataFrame, result: dict) -> dict[str, dict]:
    options = {}
    for prop in result["catalog"]:
        key = str(prop.get("property_id") or "")
        if key:
            options[f"catalog:{key}"] = {**prop, "source": "catalog", "property_id": key}
    for prop in owner_listing_properties(result["listings"]):
        key = str(prop.get("id") or "")
        if key:
            options[f"owner_listing:{key}"] = {**prop, "source": "owner_listing", "property_id": key}
    for prop in saved_properties(result["favorites"]):
        key = str(prop.get("property_id") or "")
        options.setdefault(f"{prop['source']}:{key}", prop)
    return options


def render_compare(data: pd.DataFrame) -> None:
    result = _load(data)
    if result is None: return
    _page_header("Compare Properties", "Compare saved or searchable properties side by side. Comparison selection is private to your account session.")
    options = _available_for_compare(data, result)
    state_key = renter_key("compare")
    selected = [key for key in st.session_state.get(state_key, []) if key in options][:4]
    st.session_state[state_key] = selected
    if options:
        live_owner_ids = {str(prop.get("id")) for prop in owner_listing_properties(result["listings"])}
        labels = {key: f"{item.get('title') or item.get('property_type', 'Property')} · {item.get('locality', '')}, {item.get('city', '')} · {format_money(item.get('rent'))} · {'Published Owner listing' if item.get('source') == 'owner_listing' and str(item.get('id') or item.get('property_id')) in live_owner_ids else 'Saved Owner listing snapshot' if item.get('source') == 'owner_listing' else 'Dataset'}" for key, item in options.items()}
        add_col, button_col, clear_col = st.columns([3, 1, 1])
        choice = add_col.selectbox("Add a property", list(options), format_func=lambda key: labels[key], key="renter_compare_add_choice")
        if button_col.button("Add", key="renter_compare_add"):
            if choice not in selected and len(selected) < 4: selected.append(choice)
            st.session_state[state_key] = selected
            st.rerun()
        if clear_col.button("Clear all", key="renter_compare_clear"):
            st.session_state[state_key] = []
            st.rerun()
    if not selected:
        st.info("Add properties from Search Properties or your saved list to compare them here.")
        return
    controls = st.columns(len(selected))
    for col, key in zip(controls, selected):
        with col:
            if st.button(f"Remove · {options[key].get('title') or key}", key=f"remove_compare_{key}"):
                st.session_state[state_key] = [x for x in selected if x != key]
                st.rerun()
    fields = (("Source", "source"), ("Monthly rent", "rent"), ("City", "city"), ("Locality", "locality"),
              ("Property type", "property_type"), ("Bedrooms", "bedrooms"), ("Bathrooms", "bathrooms"), ("Area (sq ft)", "area_sqft"), ("Furnishing", "furnishing_status"))
    rows = []
    for label, field in fields:
        row = {"Attribute": label}
        for key in selected:
            prop = options[key]
            value = prop.get(field, "Not provided")
            if field == "source": value = ("Published Owner listing" if str(prop.get("id") or prop.get("property_id")) in live_owner_ids else "Saved Owner listing snapshot") if value == "owner_listing" else "RentAI dataset"
            if field == "rent": value = format_money(value)
            row[labels[key]] = value if value is not None else "Not provided"
        rows.append(row)
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def render_saved(data: pd.DataFrame) -> None:
    result = _load(data)
    if result is None: return
    _page_header("Saved Properties", "Your private list of saved RentAI dataset properties and published Owner listings.")
    saved = saved_properties(result["favorites"])
    if not saved:
        st.info("You have not saved any properties yet. Browse Search Properties to save a home.")
        return
    for start in range(0, len(saved), 3):
        cols = st.columns(3)
        for col, prop in zip(cols, saved[start:start + 3]):
            with col:
                display_property_card(prop, source=prop["source"], favorites=result["favorites"], key_suffix="saved")


def render_enquiries(data: pd.DataFrame) -> None:
    result = _load(data)
    if result is None: return
    _page_header("My Enquiries", "Review the enquiry records you have sent to published property Owners.")
    if st.session_state.pop(renter_key("enquiry_success"), False):
        st.success("Your enquiry was sent to the property Owner.")
    listings = {item["id"]: item for item in owner_listing_properties(result["listings"])}
    with st.expander("Send a new enquiry", expanded=not result["enquiries"]):
        if not listings:
            st.info("There are no published Owner listings to enquire about. Dataset properties do not have an Owner inbox.")
        else:
            choices = list(listings)
            with st.form("renter_send_enquiry"):
                listing_id = st.selectbox("Published property", choices, format_func=lambda key: f"{listings[key].get('title', 'Property')} · {listings[key].get('locality', '')}, {listings[key].get('city', '')}")
                name = st.text_input("Your name", value=st.session_state.get("rentai_display_name", ""), max_chars=160)
                phone = st.text_input("Phone (optional)", max_chars=40)
                message = st.text_area("Optional message to the Owner", max_chars=2000)
                submitted = st.form_submit_button("Send enquiry", type="primary")
            if submitted:
                if not name.strip():
                    st.error("Enter your name.")
                else:
                    try:
                        create("inquiry", {"listing_id": listing_id, "client_name": name.strip(), "client_phone": phone.strip(), "message": message.strip()})
                        st.session_state[renter_key("enquiry_success")] = True
                        st.rerun()
                    except ApplicationAPIError as exc:
                        st.error(str(exc))
    enquiries = result["enquiries"]
    status_groups = {
        "Pending": {"new", "pending", "contacted", "viewing_scheduled"},
        "Accepted": {"accepted"}, "Rejected": {"rejected"},
    }
    filter_choice = st.selectbox("Filter enquiries", ["All", *status_groups])
    view = [item for item in enquiries if filter_choice == "All" or (item.get("data") or {}).get("status", "pending") in status_groups.get(filter_choice, set())]
    if not view:
        st.info("No enquiries in this view yet.")
        return
    for item in sorted(view, key=lambda record: (record.get("data") or {}).get("created_at", ""), reverse=True):
        detail = item.get("data") or {}
        listing = listings.get(detail.get("listing_id"), {})
        property_data = detail.get("property") or listing
        with st.container(border=True):
            cols = st.columns([2, 1, 1])
            cols[0].markdown(f"**{property_data.get('title', 'Property listing')}**")
            cols[0].caption(", ".join(filter(None, [property_data.get("locality"), property_data.get("city")])) or "Owner listing reference")
            status = detail.get("status", "pending")
            cols[1].metric("Status", "Pending" if status in {"new", "contacted", "viewing_scheduled"} else status.title())
            cols[2].caption(f"Reference: {item.get('id', '')}")
            if detail.get("message"):
                st.write(detail["message"])
            else:
                st.caption("No message was included.")
            sent = detail.get("created_at") or "Date not recorded"
            st.caption(f"Sent {sent} as {detail.get('client_name') or 'Renter'} · your provided contact details are available to the listing Owner.")


def render_rent_payments(data: pd.DataFrame | None = None) -> None:
    _page_header("Pay Rent", "Review rent due for your confirmed tenancies and pay securely with Razorpay Test Mode.")
    if st.session_state.pop("rentai_rent_payment_success", False):
        st.success("Razorpay confirmed the captured payment. Your rent record is now Paid.")
    try:
        rent_records = records("rent_payment")
    except ApplicationAPIError as exc:
        st.error(str(exc))
        return
    if not rent_records:
        st.info("There are no rent records linked to your account. An Owner must confirm a tenancy and record rent due first.")
        return

    checkout = st.session_state.get("rentai_rent_checkout")
    checkout_record = next((item for item in rent_records if item.get("id") == (checkout or {}).get("rent_record_id")), None)
    if checkout_record is None or (checkout_record.get("data") or {}).get("status") != "pending":
        st.session_state.pop("rentai_rent_checkout", None)
        checkout = None

    for record in sorted(rent_records, key=lambda item: (item.get("data") or {}).get("due_date", "")):
        detail = record.get("data") or {}
        status = detail.get("status", "pending")
        try:
            amount = float(detail.get("amount"))
            amount_label = f"₹{amount:,.2f}"
        except (TypeError, ValueError):
            amount_label = "Amount unavailable"
        with st.container(border=True):
            cols = st.columns([2, 1, 1])
            cols[0].markdown(f"**{detail.get('property_title') or 'Rental property'}**")
            cols[0].caption(f"Rent period: {detail.get('period', 'Not recorded')} · Due: {detail.get('due_date', 'Not recorded')}")
            cols[1].metric("Amount", amount_label)
            cols[2].metric("Status", status.title())
            if status == "paid":
                cols[0].caption(f"Paid {detail.get('paid_date', '')} · Receipt: {detail.get('payment_reference', 'Recorded')}")
            elif status == "pending":
                if checkout and checkout.get("rent_record_id") == record.get("id"):
                    st.caption("Complete checkout below. Rent is not marked Paid until RentAI verifies the Razorpay signature and captured payment status.")
                elif st.button("Pay with Razorpay", key=f"renter_pay_rent_{record.get('id')}", type="primary"):
                    _payment_logger.info("Razorpay checkout stage=pay_rent_button_clicked")
                    try:
                        order = create_rent_payment_order(record["id"])
                        st.session_state["rentai_rent_checkout"] = {
                            **order, "rent_record_id": record["id"],
                        }
                        _payment_logger.info(
                            "Razorpay checkout stage=checkout_details_saved key_id_present=%s order_id_present=%s amount_present=%s",
                            bool(order.get("key_id")), bool(order.get("order_id")),
                            order.get("amount") is not None,
                        )
                        st.rerun()
                    except ApplicationAPIError as exc:
                        st.error(str(exc))

    checkout = st.session_state.get("rentai_rent_checkout")
    if checkout:
        st.subheader("Secure checkout")
        st.write(f"{checkout.get('description', 'Rent payment')} · ₹{checkout['amount'] / 100:,.2f}")
        component_nonce = st.session_state.get("rentai_rent_checkout_nonce", 0)
        response = razorpay_checkout(
            checkout=checkout,
            key=f"razorpay-checkout-{checkout['order_id']}-{component_nonce}",
        )
        if response is not None:
            _payment_logger.info(
                "Razorpay checkout stage=component_return_received response_type=%s status=%s",
                type(response).__name__,
                response.get("status") if isinstance(response, dict) else "unrecognized",
            )
        if isinstance(response, dict) and response.get("status") == "checkout_success":
            try:
                if response.get("razorpay_order_id") != checkout.get("order_id"):
                    raise ApplicationAPIError("Razorpay returned an order that does not match this rent record.")
                verify_rent_payment(
                    response.get("razorpay_payment_id", ""),
                    response.get("razorpay_order_id", ""),
                    response.get("razorpay_signature", ""),
                )
                st.session_state.pop("rentai_rent_checkout", None)
                st.session_state["rentai_rent_payment_success"] = True
                st.rerun()
            except ApplicationAPIError as exc:
                st.session_state["rentai_rent_checkout_nonce"] = component_nonce + 1
                st.error(str(exc))
        elif isinstance(response, dict) and response.get("status") == "checkout_failed":
            st.session_state["rentai_rent_checkout_nonce"] = component_nonce + 1
            st.error(response.get("message") or "Razorpay could not complete this payment. The rent record remains Pending.")
        elif isinstance(response, dict) and response.get("status") == "checkout_cancelled":
            st.session_state["rentai_rent_checkout_nonce"] = component_nonce + 1
            st.info("Checkout was cancelled. The rent remains Pending; you can retry when ready.")


def render_profile(data: pd.DataFrame | None = None) -> None:
    _page_header("My Profile", "Manage the profile details stored with your RentAI account.")
    try:
        profiles = records("profile")
    except ApplicationAPIError as exc:
        st.error(str(exc)); return
    profile = next((r for r in profiles if (r.get("data") or {}).get("record_type") == "renter_profile"), None)
    current = (profile or {}).get("data") or {}
    actual_role = st.session_state.get("rentai_role")
    st.caption(f"Role: {str(actual_role).title() if actual_role else 'Not available'}")
    with st.form("renter_profile_form"):
        name = st.text_input("Full name", value=current.get("name") or st.session_state.get("rentai_display_name", ""), max_chars=160)
        email = st.text_input("Account email", value=st.session_state.get("rentai_email", ""), disabled=True)
        phone = st.text_input("Phone", value=current.get("phone", ""), max_chars=40)
        preferred_city = st.text_input("Preferred city", value=current.get("preferred_city", ""), max_chars=120)
        submitted = st.form_submit_button("Save profile", type="primary")
    if submitted:
        if not name.strip(): st.error("Your name is required."); return
        payload = {"record_type": "renter_profile", "name": name.strip(), "phone": phone.strip(), "preferred_city": preferred_city.strip()}
        try:
            update(profile, payload) if profile else create("profile", payload)
            st.session_state[renter_key("profile_name")] = name.strip()
            st.success("Profile saved.")
            st.rerun()
        except ApplicationAPIError as exc: st.error(str(exc))


def render_settings(data: pd.DataFrame | None = None) -> None:
    _page_header("Settings", "Set the default ordering used on your property search page.")
    try:
        profiles = records("profile")
    except ApplicationAPIError as exc:
        st.error(str(exc)); return
    setting = next((r for r in profiles if (r.get("data") or {}).get("record_type") == "renter_settings"), None)
    current = (setting or {}).get("data") or {"sort_by": "rent", "sort_order": "asc"}
    current_sort = current.get("sort_by") if current.get("sort_by") in {"rent", "area_sqft", "bedrooms"} else "rent"
    current_order = current.get("sort_order") if current.get("sort_order") in {"asc", "desc"} else "asc"
    with st.form("renter_settings_form"):
        sort_by = st.selectbox("Default search sort", ["rent", "area_sqft", "bedrooms"], index=["rent", "area_sqft", "bedrooms"].index(current_sort), format_func=lambda x: {"rent": "Monthly rent", "area_sqft": "Area", "bedrooms": "Bedrooms"}[x])
        sort_order = st.selectbox("Order", ["asc", "desc"], index=["asc", "desc"].index(current_order), format_func=lambda x: "Lowest first" if x == "asc" else "Highest first")
        submitted = st.form_submit_button("Save settings", type="primary")
    if submitted:
        payload = {"record_type": "renter_settings", "sort_by": sort_by, "sort_order": sort_order}
        try:
            update(setting, payload) if setting else create("profile", payload)
            st.session_state[renter_key("settings")] = payload
            st.success("Search settings saved.")
            st.rerun()
        except ApplicationAPIError as exc: st.error(str(exc))

