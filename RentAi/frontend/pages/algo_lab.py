"""Model catalog and experiment configuration UI."""

import streamlit as st

from frontend.components.data_utils import page_heading


ALGORITHMS = {
	"Regression": ["Linear Regression", "Ridge", "Lasso", "XGBoost Regressor"],
	"Classification": ["Logistic Regression", "KNN", "Naive Bayes", "SVM", "Decision Tree", "Random Forest", "Bagging", "AdaBoost", "Gradient Boosting", "XGBoost", "Stacking"],
	"Clustering": ["K-Means", "DBSCAN", "PCA"],
}


def render(data) -> None:
	page_heading("Algo Lab", "Browse planned algorithms and configure an experiment. Training is disabled in this phase.")
	family = st.segmented_control("Algorithm family", list(ALGORITHMS), default="Regression", key="algo-family")
	family = family or "Regression"
	available = ALGORITHMS[family]
	left, right = st.columns([1, 1.5])
	with left:
		with st.container(border=True):
			st.subheader("Experiment setup")
			algorithm = st.selectbox("Algorithm", available, key="algo-choice")
			st.selectbox("Evaluation split", ["80 / 20", "75 / 25", "70 / 30"], key="algo-split")
			if family != "Clustering":
				metrics = ["Select later", "MAE", "RMSE", "R²"] if family == "Regression" else ["Select later", "Accuracy", "Precision", "Recall", "F1"]
				st.selectbox("Evaluation metric", metrics)
			run = st.button("Configure experiment", type="primary", width="stretch")
	with right:
		with st.container(border=True):
			st.subheader("Experiment status")
			st.markdown(f"**{algorithm}**")
			st.caption(f"{family} workflow · model training is not enabled")
			st.info("Metrics and fitted model artifacts will appear after the ML phase.")
	if run:
		st.info("Configuration noted for the future ML workflow. No model was trained.")

	st.subheader("Available algorithms")
	tabs = st.tabs(list(ALGORITHMS))
	for tab, (name, algorithms) in zip(tabs, ALGORITHMS.items()):
		with tab:
			st.dataframe({"Algorithm": algorithms, "Status": ["Not implemented"] * len(algorithms)}, hide_index=True, width="stretch")