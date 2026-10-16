"""Pages and shared rendering for the authenticated Renter workspace."""

from html import escape

import pandas as pd
import streamlit as st

from frontend.application_api import (ApplicationAPIError, booking_checkout, booking_notifications, booking_payment_configured,
                                      bookings as booking_records, create, delete, records, update)
from frontend.pages import rent_prediction
from frontend.pages.renter_support import (
    CATALOG_SOURCE, OWNER_LISTING_SOURCE, UNKNOWN_SOURCE,
    add_to_comparison, dataset_properties, display_property_card, favorite_for,
    format_money, load_renter_records, owner_listing_properties,
    load_search_records, property_id, renter_key, saved_properties,
)
from frontend.components.property_images import render_property_image


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
        renter_profile = next((r.get("data") or {} for r in result["profiles"]
                               if (r.get("data") or {}).get("record_type") == "renter_profile"), {})
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
    _page_header("Search Properties", "Search the RentAI dataset and published Owner listings. Each source is labeled.")
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
        sort_by = row2[3].selectbox("Sort by", ["rent", "area_sqft", "bedrooms"], format_func=lambda x: {"rent": "Monthly rent", "area_sqft": "Area", "bedrooms": "Bedrooms"}[x])
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
    filters.setdefault("sort_order", "asc")
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


def render_prediction(data: pd.DataFrame) -> None:
    st.markdown("""
    <style>
      .st-key-renter-prediction-layout .renter-prediction-info-card {
        box-sizing:border-box; width:100%; min-height:220px; padding:1.5rem;
        border-radius:16px; background:linear-gradient(150deg,#07101f,#17415e);
        color:#E2E8F0; overflow-wrap:anywhere;
      }
      .st-key-renter-prediction-layout .renter-prediction-info-card h2 {
        color:#FFFFFF !important; font-size:1.35rem; font-weight:700;
        line-height:1.3; margin:0 0 .85rem;
      }
      .st-key-renter-prediction-layout .renter-prediction-info-card p {
        color:#E2E8F0 !important; font-size:.95rem; line-height:1.6;
        margin:.4rem 0 0;
      }
      @media (max-width: 640px) {
        .st-key-renter-prediction-layout .renter-prediction-info-card { min-height:0; }
      }
    </style>
    """, unsafe_allow_html=True)
    with st.container(key="renter-prediction-layout"):
        left, right = st.columns([1, 2], gap="large", vertical_alignment="top")
        with left:
            st.markdown("""
            <section class="renter-prediction-info-card">
              <h2>Understand the rent</h2>
              <p>Provide the same property features supported by RentAI’s trained model to get an estimate.</p>
              <p>No estimate is shown until the model successfully runs.</p>
            </section>
            """, unsafe_allow_html=True)
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
        if key:
            options.setdefault(f"{prop['source']}:{key}", prop)
    return options


def _comparison_source_label(prop: dict, live_owner_ids: set[str]) -> str:
    source = prop.get("source")
    property_key = str(prop.get("id") or prop.get("property_id") or "")
    if source == CATALOG_SOURCE:
        return "RentAI dataset"
    if source == OWNER_LISTING_SOURCE:
        return "Published Owner listing" if property_key in live_owner_ids else "Saved Owner listing snapshot"
    return "Source not recorded"


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
        labels = {key: f"{item.get('title') or item.get('property_type', 'Property')} · {item.get('locality', '')}, {item.get('city', '')} · {format_money(item.get('rent'))} · {_comparison_source_label(item, live_owner_ids)}" for key, item in options.items()}
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
    for index, (col, key) in enumerate(zip(controls, selected)):
        with col:
            property_item = options[key]
            illustrative = render_property_image(property_item, property_item.get("source", "unknown"), key_suffix=f"compare-{index}")
            if illustrative:
                st.caption("Illustrative image · this dataset property's photo is not verified.")
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
            if field == "source": value = _comparison_source_label(prop, live_owner_ids)
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
    published_ids = {
        str(listing.get("id"))
        for listing in owner_listing_properties(result["listings"])
    }
    current_owner_listings = {
        str(listing.get("id")): listing
        for listing in owner_listing_properties(result["listings"])
    }
    for prop in saved:
        if prop.get("source") == OWNER_LISTING_SOURCE and str(prop.get("property_id")) in current_owner_listings:
            prop.update(current_owner_listings[str(prop.get("property_id"))])
    for start in range(0, len(saved), 3):
        cols = st.columns(3)
        for col, prop in zip(cols, saved[start:start + 3]):
            with col:
                display_property_card(
                    prop,
                    source=prop["source"],
                    favorites=result["favorites"],
                    key_suffix="saved",
                    published_owner_listing_ids=published_ids,
                )


def render_enquiries(data: pd.DataFrame) -> None:
    result = _load(data)
    if result is None: return
    _page_header("My Enquiries", "Review the enquiry records you have sent to published property Owners.")
    listings = {item["id"]: item for item in owner_listing_properties(result["listings"])}
    with st.expander("Send a new enquiry", expanded=not result["enquiries"]):
        if not listings:
            st.info("There are no published Owner listings to enquire about. Dataset properties do not have an Owner inbox.")
        else:
            choices = list(listings)
            listing_state_key = renter_key("enquiry_listing_id")
            if st.session_state.get(listing_state_key) not in choices:
                st.session_state.pop(listing_state_key, None)
            selected_listing = st.session_state.get(listing_state_key)
            default_index = choices.index(selected_listing) if selected_listing in choices else 0
            with st.form("renter_send_enquiry"):
                listing_id = st.selectbox(
                    "Published property", choices, index=default_index,
                    format_func=lambda key: f"{listings[key].get('title', 'Property')} · {listings[key].get('locality', '')}, {listings[key].get('city', '')}",
                    key=listing_state_key,
                )
                name = st.text_input("Your name", value=st.session_state.get("rentai_display_name", ""), max_chars=160)
                phone = st.text_input("Phone (optional)", max_chars=40)
                message = st.text_area("Message to the Owner", max_chars=2000)
                submitted = st.form_submit_button("Send enquiry", type="primary")
            if submitted:
                if not name.strip() or not message.strip():
                    st.error("Enter your name and a message.")
                else:
                    try:
                        create("inquiry", {"listing_id": listing_id, "client_name": name.strip(), "client_phone": phone.strip(), "message": message.strip()})
                        st.success("Your enquiry was sent to the property Owner.")
                        st.rerun()
                    except ApplicationAPIError as exc:
                        st.error(str(exc))
    enquiries = result["enquiries"]
    filter_choice = st.selectbox("Filter enquiries", ["All", "New", "In progress", "Closed"])
    status_groups = {"New": {"new"}, "In progress": {"contacted", "viewing_scheduled"}, "Closed": {"completed", "closed"}}
    view = [item for item in enquiries if filter_choice == "All" or (item.get("data") or {}).get("status", "new") in status_groups.get(filter_choice, set())]
    if not view:
        st.info("No enquiries in this view yet.")
        return
    for item in sorted(view, key=lambda record: (record.get("data") or {}).get("created_at", ""), reverse=True):
        detail = item.get("data") or {}
        listing = listings.get(detail.get("listing_id"), {})
        with st.container(border=True):
            cols = st.columns([2, 1, 1])
            cols[0].markdown(f"**{listing.get('title', 'Property listing')}**")
            cols[0].caption(", ".join(filter(None, [listing.get("locality"), listing.get("city")])) or "Owner listing reference")
            cols[1].metric("Status", str(detail.get("status", "new")).replace("_", " ").title())
            cols[2].caption(f"Reference: {item.get('id', '')}")
            st.write(detail.get("message", ""))
            sent = detail.get("created_at") or "Date not recorded"
            st.caption(f"Sent {sent} as {detail.get('client_name') or 'Renter'} · your provided contact details are available to the listing Owner.")


def render_profile(data: pd.DataFrame | None = None) -> None:
    st.markdown("""
    <style>
      .profile-crumb { color:#69758a; font-size:.82rem; margin:0 0 .35rem; }
      .profile-crumb b { color:#e56d32; font-weight:600; }
      .profile-title { color:#172033; font-size:1.9rem; font-weight:750; margin:0; }
      .profile-subtitle { color:#738096; margin:.1rem 0 1rem; font-size:.9rem; }
      .profile-banner { display:flex; align-items:center; gap:1rem; min-height:120px; padding:1rem 1.25rem;
        border:1px solid #edf0f5; border-radius:15px; margin:0 0 1rem;
        background:radial-gradient(ellipse at 88% 115%,#f5e8d9 0 19%,transparent 20%),
        linear-gradient(105deg,#fff 0%,#fff 63%,#f8f5f1 100%); box-shadow:0 5px 16px #1720330a; }
      .profile-avatar { flex:0 0 76px; width:76px; height:76px; border-radius:50%; display:grid; place-items:center;
        background:#f8e9dc; color:#c95c28; font-size:1.6rem; font-weight:750; }
      .profile-person { min-width:0; }
      .profile-person h3 { margin:0 0 .2rem; color:#172033; font-size:1.25rem; }
      .profile-person p { margin:.12rem 0; color:#66738a; font-size:.8rem; }
      .profile-role { display:inline-block; border-radius:20px; background:#fff1e3; color:#a65320; padding:.18rem .55rem; font-size:.7rem; margin-left:.4rem; }
      .profile-card-title { font-size:1.02rem; font-weight:700; color:#172033; margin:0; }
      .profile-card-hint { color:#8490a3; font-size:.77rem; margin:.1rem 0 .45rem; }
      div[data-testid='stVerticalBlockBorderWrapper'] { border-radius:13px; border-color:#e7ebf1; box-shadow:0 4px 12px #17203308; }
      div[data-testid='stFormSubmitButton'] button[kind='primary'] { background:#df7136; border-color:#df7136; }
    </style>
    """, unsafe_allow_html=True)
    st.markdown("<div class='profile-crumb'>⌂ &nbsp;›&nbsp; Account &nbsp;›&nbsp; <b>My Profile</b></div><h1 class='profile-title'>My Profile</h1><p class='profile-subtitle'>Manage your personal information and rental preferences.</p>", unsafe_allow_html=True)
    try:
        profiles = records("profile")
    except ApplicationAPIError as exc:
        st.error(str(exc)); return
    profile = next((r for r in profiles if (r.get("data") or {}).get("record_type") == "renter_profile"), None)
    current = (profile or {}).get("data") or {}
    actual_role = st.session_state.get("rentai_role") or "Renter"
    full_name = current.get("name") or st.session_state.get("rentai_display_name", "Renter")
    initials = "".join(part[0] for part in full_name.split()[:2] if part).upper() or "R"
    st.markdown(f"<div class='profile-banner'><div class='profile-avatar'>{escape(initials)}</div><div class='profile-person'><h3>{escape(full_name)} <span class='profile-role'>{escape(str(actual_role).title())}</span></h3><p>✉ &nbsp; {escape(st.session_state.get('rentai_email', ''))}</p><p>☎ &nbsp; {escape(current.get('phone') or 'Not added')}</p><p>⌖ &nbsp; {escape(current.get('preferred_city') or 'Not added')}</p></div></div>", unsafe_allow_html=True)
    city_options = sorted({str(p.get("city")) for p in (data.to_dict("records") if data is not None else []) if p.get("city")})
    city_options = list(dict.fromkeys(["", *city_options, current.get("preferred_city", "")]))
    property_types = ["Any", "Apartment", "House", "Villa", "Studio", "Other"]
    furnishing_options = ["Any", "Furnished", "Semi-Furnished", "Unfurnished"]
    budget_options = ["Any budget", "₹10,000 – ₹20,000", "₹20,000 – ₹40,000", "₹40,000 – ₹60,000", "₹60,000+"]
    budget_values = {"Any budget": (None, None), "₹10,000 – ₹20,000": (10000, 20000), "₹20,000 – ₹40,000": (20000, 40000), "₹40,000 – ₹60,000": (40000, 60000), "₹60,000+": (60000, None)}
    current_budget = next((label for label, values in budget_values.items() if (current.get("monthly_budget_min"), current.get("monthly_budget_max")) == values), "Any budget")
    current_type = current.get("preferred_property_type", "Any")
    current_furnishing = current.get("furnishing_preference", "Any")
    current_bhk = current.get("preferred_bhk", "Any")
    if current_bhk not in {"Any", "1 BHK", "2 BHK", "3 BHK", "4+ BHK"}: current_bhk = "Any"
    with st.form("renter_profile_form"):
        columns = st.columns(2, gap="medium")
        with columns[0]:
            with st.container(border=True):
                st.markdown("<p class='profile-card-title'>👤 &nbsp; Personal information</p><p class='profile-card-hint'>Your basic account details.</p>", unsafe_allow_html=True)
                name = st.text_input("Full name", value=full_name, max_chars=160)
                email = st.text_input("Account email", value=st.session_state.get("rentai_email", ""), disabled=True)
                phone = st.text_input("Phone number", value=current.get("phone", ""), max_chars=40, placeholder="Enter your phone number")
                preferred_city = st.selectbox("Preferred city", city_options, index=city_options.index(current.get("preferred_city", "")) if current.get("preferred_city", "") in city_options else 0, format_func=lambda value: value or "Choose a city")
        with columns[1]:
            with st.container(border=True):
                st.markdown("<p class='profile-card-title'>☷ &nbsp; Rental preferences</p><p class='profile-card-hint'>Help us show you more relevant properties.</p>", unsafe_allow_html=True)
                pref_city = st.selectbox("Preference city", city_options, index=city_options.index(preferred_city) if preferred_city in city_options else 0, format_func=lambda value: value or "Choose a city")
                type_idx = property_types.index(current_type) if current_type in property_types else 0
                budget_idx = budget_options.index(current_budget)
                fields = st.columns(2)
                chosen_type = fields[0].selectbox("Property type", property_types, index=type_idx)
                chosen_budget = fields[1].selectbox("Monthly budget", budget_options, index=budget_idx)
                bhk_options = ["Any", "1 BHK", "2 BHK", "3 BHK", "4+ BHK"]
                preferred_bhk = st.radio("Preferred BHK", bhk_options, index=bhk_options.index(current_bhk), horizontal=True)
                furnishing_idx = furnishing_options.index(current_furnishing) if current_furnishing in furnishing_options else 0
                furnished = st.selectbox("Furnished preference", furnishing_options, index=furnishing_idx)
        actions = st.columns([1, 2, 5])
        submitted = actions[0].form_submit_button("✓  Save changes", type="primary", width="stretch")
        cancelled = actions[1].form_submit_button("Cancel", width="stretch")
    if submitted:
        if not name.strip(): st.error("Your name is required."); return
        minimum, maximum = budget_values[chosen_budget]
        payload = {"record_type": "renter_profile", "name": name.strip(), "phone": phone.strip(), "preferred_city": pref_city or preferred_city,
                   "preferred_property_type": chosen_type, "monthly_budget_min": minimum, "monthly_budget_max": maximum,
                   "preferred_bhk": preferred_bhk, "furnishing_preference": furnished}
        try:
            update(profile, payload) if profile else create("profile", payload)
            st.session_state[renter_key("profile_name")] = name.strip()
            st.success("Profile saved.")
            st.rerun()
        except ApplicationAPIError as exc: st.error(str(exc))
    if cancelled:
        st.rerun()


def render_bookings(data: pd.DataFrame | None = None) -> None:
    _page_header("My Bookings", "Track owner decisions, payment status, and confirmed owner listings.")
    try:
        items = booking_records()
        payment_ready = booking_payment_configured()
        notifications = booking_notifications()
    except ApplicationAPIError as exc:
        st.error(str(exc))
        return
    if notifications:
        with st.expander("Recent booking notifications", expanded=True):
            for notification in notifications[:5]:
                st.info(f"{notification['message']} · {notification['created_at']}")
    if not items:
        st.info("You have no booking requests yet. Only published owner listings marked vacant can receive requests.")
        return
    for item in items:
        prop = item.get("property") or {}
        payment = item.get("latest_payment") or {}
        status = item.get("status", "pending_owner_approval")
        with st.container(border=True):
            st.subheader(prop.get("title", "Owner listing"))
            st.caption(" · ".join(filter(None, [prop.get("property_type"), f"{prop.get('bedrooms')} bedrooms" if prop.get("bedrooms") is not None else ""])))
            st.caption(" · ".join(filter(None, [prop.get("locality"), prop.get("city")])))
            cols = st.columns(4)
            cols[0].metric("Booking ID", str(item.get("id", "")))
            cols[1].metric("Requested move-in", item.get("move_in_date", "Not recorded"))
            cols[2].metric("Agreed monthly rent", format_money(item.get("monthly_rent")))
            cols[3].metric("Booking status", status.replace("_", " ").title())
            st.write(f"**Owner:** {item.get('owner_name', 'Owner')} · {item.get('owner_email', '')}")
            st.write(f"**Initial payment due:** ₹{int(item.get('amount_due', 0)) / 100:,.2f} INR (first month rent plus listed deposit)")
            st.write(f"**Deposit:** ₹{float(item.get('security_deposit', 0)):,.0f} INR · **Payment status:** {item.get('payment_status', 'pending').replace('_', ' ').title()}")
            if payment.get("transaction_id"):
                st.caption(f"Verified transaction reference: {payment['transaction_id']}")
            if status == "pending_owner_approval":
                st.info("Awaiting owner approval.")
            elif item.get("availability_conflict"):
                if item.get("payment_status") == "paid":
                    st.error("Payment was verified, but availability changed before confirmation. The captured transaction is recorded for manual reconciliation; this booking is not confirmed.")
                else:
                    st.error("The listing is no longer marked available. Payment is disabled; contact the owner about this request.")
            elif status == "confirmed":
                st.success("Booking confirmed.")
            elif status == "accepted_pending_payment" and item.get("payment_status") == "processing":
                if payment.get("checkout_url"):
                    st.link_button("Continue to secure Razorpay checkout", payment["checkout_url"], type="primary")
                st.info("Payment processing. The booking will confirm after Razorpay verifies the capture. Refresh this page to check the status.")
            elif status == "accepted_pending_payment" and item.get("payment_status") in {"pending", "failed"}:
                if not payment_ready:
                    st.warning("Payments are unavailable: the API server needs Razorpay API credentials and a webhook secret.")
                elif st.button("Pay Now" if item.get("payment_status") == "pending" else "Retry payment", key=f"booking_pay_{item['id']}", type="primary"):
                    try:
                        result = booking_checkout(item["id"])
                        st.link_button("Continue to secure Razorpay checkout", result["checkout_url"], type="primary")
                        st.info("Payment remains processing until RentAI verifies Razorpay's signed webhook.")
                    except ApplicationAPIError as exc:
                        st.error(str(exc))

