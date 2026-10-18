"""Rent category prediction using the persisted classifier without rent leakage."""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from backend.services.classification_service import classify_rent_details
from frontend.components.charts import render_chart
from frontend.components.data_utils import category_options, page_heading
from ml.classification.evaluate_model import get_evaluation_results


def _model_performance() -> None:
    st.subheader("Held-out model evaluation")
    try:
        results = get_evaluation_results()
    except (FileNotFoundError, ValueError) as exc:
        st.info(str(exc))
        return
    columns = st.columns(4)
    for column, (label, value) in zip(columns, (("Accuracy", results["accuracy"]), ("Macro F1", results["f1_macro"]), ("Weighted F1", results["f1_weighted"]), ("Test records", results["test_records"]))):
        column.metric(label, f"{value:,}" if label == "Test records" else f"{value:.2%}")
    st.caption(f"Model: {results['model_name']}. {results['scenario']} Metrics describe the synthetic dataset holdout, not real-world market accuracy.")
    if results.get("per_class"):
        st.dataframe(pd.DataFrame(results["per_class"]).T.reset_index(names="Category"), hide_index=True, width="stretch")
    if results.get("feature_importance"):
        st.markdown("**Model feature importance / coefficient magnitude**")
        st.dataframe(results["feature_importance"], hide_index=True, width="stretch")
    matrix = go.Figure(go.Heatmap(z=results["confusion_matrix"], x=results["labels"], y=results["labels"], text=results["confusion_matrix"], texttemplate="%{text}", colorscale="Oranges", showscale=False))
    matrix.update_layout(xaxis_title="Predicted category", yaxis_title="Actual category", yaxis_autorange="reversed", height=340)
    render_chart(matrix, "rent-classification-confusion-matrix", height=340)


def render(data) -> None:
    page_heading("Rent Classification", "Estimate a rent band from property and location details before rent is known.")
    with st.container(border=True):
        st.subheader("Property details")
        c1, c2 = st.columns(2)
        with c1:
            cities = category_options(data, "city")
            city = st.selectbox("City", cities, key="classification-city") if cities else st.text_input("City", key="classification-city")
            localities = []
            if "city" in data and "locality" in data and city:
                localities = sorted(data.loc[data["city"].astype(str) == str(city), "locality"].dropna().astype(str).unique())
            locality = st.selectbox("Locality", localities, key="classification-locality") if localities else ""
            types = category_options(data, "property_type")
            property_type = st.selectbox("Property type", types, key="classification-property-type") if types else st.text_input("Property type", key="classification-property-type")
            furnishing_options = category_options(data, "furnishing_status")
            furnishing = st.selectbox("Furnishing", furnishing_options, key="classification-furnishing") if furnishing_options else None
        with c2:
            area_sqft = st.number_input("Area (sq ft)", min_value=1.0, value=700.0, step=50.0, key="classification-area-sqft")
            bedrooms = st.number_input("Bedrooms", min_value=0, value=1, step=1, key="classification-bedrooms")
            bathrooms = st.number_input("Bathrooms", min_value=0, value=1, step=1, key="classification-bathrooms")
            floor = st.number_input("Floor", min_value=0, value=1, step=1, key="classification-floor")
        submitted = st.button("Predict rent category", type="primary", width="stretch")

    if submitted:
        if not city or not property_type:
            st.error("City and property type are required.")
        else:
            features = {"city": city, "locality": locality or None, "property_type": property_type,
                "furnishing_status": furnishing, "area_sqft": area_sqft, "bedrooms": bedrooms,
                "bathrooms": bathrooms, "floor": floor}
            try:
                with st.spinner("Predicting with the saved classifier..."):
                    st.session_state["rent_classification_result"] = classify_rent_details(features)
                    st.session_state["rent_classification_inputs"] = features
                    st.session_state.pop("rent_classification_error", None)
            except (FileNotFoundError, ValueError) as exc:
                st.session_state["rent_classification_error"] = str(exc)
                st.session_state.pop("rent_classification_result", None)
    if st.session_state.get("rent_classification_error"):
        st.error(st.session_state["rent_classification_error"])
    result = st.session_state.get("rent_classification_result")
    if result:
        st.success(f"Predicted category: **{result['rent_category']}**")
        st.caption(f"Model: {result['model_name']}. Rent is intentionally excluded from prediction inputs to prevent target leakage.")
        if result.get("probabilities"):
            st.markdown("Raw classifier scores (not calibrated probabilities)")
            st.bar_chart(pd.Series(result["probabilities"], name="Raw score"))
    if "rent_category" in data:
        with st.expander("Dataset category distribution"):
            st.bar_chart(data["rent_category"].value_counts())
    _model_performance()
