"""Authenticated feedback about the RentAI application."""

from datetime import datetime

import streamlit as st

from frontend.application_api import ApplicationAPIError, app_reviews, submit_app_review


def _stars(rating: int) -> str:
    return "★" * rating + "☆" * (5 - rating)


def _display_date(value: str | None) -> str:
    if not value:
        return "Date unavailable"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%b %d, %Y")
    except ValueError:
        return value


def _load_reviews(*, rating=None, role=None, search=None):
    try:
        return app_reviews(rating=rating, role=role, search=search)
    except ApplicationAPIError as exc:
        st.error(str(exc))
        return None


def render(_data=None) -> None:
    role = st.session_state.get("rentai_role")
    if role not in {"admin", "owner", "renter"}:
        st.error("Sign in with an Admin, Owner, or Renter account to access app reviews.")
        return
    is_admin = role == "admin"
    st.title("App Reviews & Feedback")
    if not is_admin:
        st.caption("Share feedback about your experience using RentAI.")

    rating_filter, role_filter, search_filter = None, None, None
    if is_admin:
        filter_columns = st.columns([1, 1, 2])
        rating_choice = filter_columns[0].selectbox(
            "Filter by rating", ["All ratings", 5, 4, 3, 2, 1], key="app_reviews_rating_filter"
        )
        role_choice = filter_columns[1].selectbox(
            "Filter by role", ["All roles", "owner", "renter"], key="app_reviews_role_filter"
        )
        rating_filter = rating_choice if isinstance(rating_choice, int) else None
        role_filter = role_choice if role_choice in {"owner", "renter"} else None
        search_filter = filter_columns[2].text_input(
            "Search reviews", key="app_reviews_search", max_chars=120
        ).strip() or None

    with st.spinner("Loading app reviews..."):
        data = _load_reviews(rating=rating_filter, role=role_filter, search=search_filter)
    if data is None:
        return

    stats = st.columns(2)
    stats[0].metric("Total App Reviews", data.get("total_reviews", 0))
    average = data.get("average_rating")
    stats[1].metric(
        "Average Rating", f"{average:.2f} / 5" if average is not None else "No ratings yet"
    )

    own_review = data.get("own_review")
    if not is_admin:
        if own_review:
            st.subheader("Your App Review")
            with st.container(border=True):
                st.markdown(f"**{_stars(own_review['rating'])}**")
                st.caption(f"Submitted {_display_date(own_review.get('created_at'))}")
                st.write(own_review.get("feedback", ""))
            st.caption("Submitting another review updates your existing review.")

        st.subheader("Review RentAI")
        generation = st.session_state.get("app_review_form_generation", 0)
        with st.form("app_review_form", clear_on_submit=False):
            rating = st.radio(
                "Rating",
                [1, 2, 3, 4, 5],
                index=4,
                format_func=_stars,
                horizontal=True,
                key=f"app_review_rating_input_{generation}",
            )
            feedback = st.text_area(
                "Your feedback about RentAI",
                max_chars=4000,
                height=140,
                key=f"app_review_feedback_input_{generation}",
                help="Tell us about your overall experience using the RentAI application.",
            )
            submitted = st.form_submit_button(
                "Submit Review", type="primary", width="stretch",
                key=f"app_review_submit_{generation}",
            )
        if submitted:
            if not isinstance(rating, int) or not 1 <= rating <= 5:
                st.error("Choose a rating from 1 to 5 stars.")
            elif not feedback.strip():
                st.error("Write feedback before submitting your review.")
            else:
                try:
                    submit_app_review(rating, feedback.strip())
                except ApplicationAPIError as exc:
                    st.error(str(exc))
                else:
                    st.session_state["app_review_form_generation"] = generation + 1
                    st.session_state["app_review_submit_success"] = True
                    st.rerun()
        if st.session_state.pop("app_review_submit_success", False):
            st.success("Your app review has been saved.")

    recent_section = st.container(key="admin_app_reviews_recent") if is_admin else st.container()
    with recent_section:
        st.subheader("Recent App Reviews")
        reviews = data.get("reviews") or []
        if not reviews:
            st.info("There are no app reviews to show yet.")
        for review in reviews:
            with st.container(border=True):
                header = st.columns([3, 1])
                header[0].markdown(
                    f"**{review.get('reviewer_name') or 'RentAI user'}** · "
                    f"{str(review.get('role') or '').title()}"
                )
                header[1].markdown(
                    f"<span style='color:#F97316'>{_stars(review.get('rating', 0))}</span>",
                    unsafe_allow_html=True,
                )
                st.caption(_display_date(review.get("created_at")))
                st.write(review.get("feedback", ""))