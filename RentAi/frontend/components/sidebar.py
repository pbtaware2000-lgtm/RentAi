import streamlit as st

from frontend.data_loader import DATA_PATH, dataset_status


def _navigate_to(page: str) -> None:
    """Update the active page before Streamlit renders the next run."""
    st.session_state.page = page


def render_sidebar(data, role=None, allowed_pages=None, on_logout=None):

    with st.sidebar:

        # =========================
        # BRAND
        # =========================

        st.markdown("<div class='brand-mark'>R</div>", unsafe_allow_html=True)
        st.title("RENTAI")
        st.caption("Smart Rental Intelligence")

        if role:
            st.caption(f"Signed in as **{role.title()}**")
            st.button("Sign out", key="rentai_logout", width="stretch", on_click=on_logout)

        st.divider()

        # =========================
        # MAIN
        # =========================

        st.caption("MAIN")

        st.button("🏠  Home", key="nav_home", width="stretch",
                     type="primary" if st.session_state.page == "Home" else "secondary",
                     on_click=_navigate_to, args=("Home",))

        # =========================
        # LEARNING
        # =========================

        st.caption("LEARNING SECTION")

        st.button("📚  Learning Module", key="nav_learning", width="stretch",
                     type="primary" if st.session_state.page == "Learning Module" else "secondary",
                     on_click=_navigate_to, args=("Learning Module",))

        # =========================
        # IMPLEMENTATION
        # =========================

        st.caption("IMPLEMENTATION SECTION")

        menu_items = [
            ("ðŸ“‹  Admin Dashboard", "Admin Dashboard"),
            ("ðŸ   Owner Dashboard", "Owner Dashboard"),
            ("ðŸ¡  Renter Dashboard", "Renter Dashboard"),
            ("ðŸ¤  Broker Dashboard", "Broker Dashboard"),
            ("⚙️  Implementation Overview", "Implementation Overview"),
            ("📊  Dataset & EDA", "Dataset & EDA"),
            ("💰  Rent Prediction", "Rent Prediction"),
            ("🏷️  Rent Classification", "Rent Classification"),
            ("🔵  Property Clustering", "Property Clustering"),
            ("🔍  Property Search", "Property Search"),
            ("⚖️  Property Comparison", "Property Comparison"),
            ("📍  Location & Analytics", "Location & Analytics"),
            ("💡  Property Insights", "Property Insights"),
            ("🧪  Algo Lab", "Algo Lab"),
            ("📑  Admin & Reports", "Admin & Reports"),
        ]

        for label, page in menu_items:

            if allowed_pages is not None and page not in allowed_pages:
                continue

            st.button(
                label,
                key=f"nav_{page}",
                width="stretch",
                type="primary" if st.session_state.page == page else "secondary",
                on_click=_navigate_to,
                args=(page,),
            )

        # =========================
        # ACTIVE DATASET
        # =========================

        st.divider()

        st.caption("ACTIVE DATASET")

        st.write(f"📄 `{DATA_PATH.name}`")
        status = dataset_status(data)
        st.caption(f"🟢 {status}" if not data.empty else f"🟠 {status}")

        # =========================
        # FOOTER
        # =========================

        st.divider()

        st.caption(
            "Python • Machine Learning • Streamlit"
        )

        st.caption("RentAI Engine")
