"""Dataset administration and downloadable status report."""

from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from frontend.components.data_utils import numeric_column, page_heading
from frontend.application_api import ApplicationAPIError, admin_users, set_user_role


def _report_text(data: pd.DataFrame) -> str:
	lines = [
		"RentAI Dataset & System Report",
		f"Generated at (UTC): {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
		f"Dataset rows: {len(data):,}",
		f"Dataset columns: {len(data.columns):,}",
		f"Duplicate rows: {int(data.duplicated().sum()):,}" if not data.empty else "Duplicate rows: 0",
		f"Missing values: {int(data.isna().sum().sum()):,}" if not data.empty else "Missing values: 0",
		"ML status: Not connected",
		"Dataset storage: CSV; account roles and application records: Firestore",
	]
	rents = numeric_column(data, "rent")
	lines.append(f"Average rent: {rents.mean():,.2f}" if not rents.empty else "Average rent: unavailable")
	return "\n".join(lines) + "\n"


def render(data: pd.DataFrame) -> None:
	page_heading("Admin Dashboard", "Review system statistics, users, and the active CSV dataset.")
	rows_col, columns_col, missing_col, duplicates_col = st.columns(4)
	rows_col.metric("Dataset rows", f"{len(data):,}")
	columns_col.metric("Dataset columns", f"{len(data.columns):,}")
	missing_col.metric("Missing values", f"{int(data.isna().sum().sum()):,}" if not data.empty else "0")
	duplicates_col.metric("Duplicate rows", f"{int(data.duplicated().sum()):,}" if not data.empty else "0")

	status_col, report_col = st.columns(2)
	with status_col:
		with st.container(border=True):
			st.subheader("System status")
			st.markdown("**Streamlit UI** · Available")
			st.markdown("**Dataset** · " + ("Loaded" if not data.empty else "Empty or unavailable"))
			st.markdown("**ML services** · Not connected")
			st.markdown("**Application records** · Firestore-backed")
	with report_col:
		with st.container(border=True):
			st.subheader("Report generation")
			st.write("Download a text summary of the current CSV and system status.")
			st.download_button("Download report", _report_text(data), file_name="rentai_report.txt", mime="text/plain", type="primary", width="stretch")

	st.subheader("Model statistics")
	st.info("No model training runs, metrics, or prediction history are recorded.")
	if not data.empty:
		with st.expander("Dataset schema"):
			st.dataframe(pd.DataFrame({"Column": data.columns, "Data type": data.dtypes.astype(str).values, "Missing": data.isna().sum().values}), width="stretch", hide_index=True)

	st.subheader("User management")
	try:
		users = admin_users()
		st.metric("Registered role records", len(users))
		if users:
			st.dataframe(pd.DataFrame(users), use_container_width=True, hide_index=True)
			with st.form("admin_change_user_role"):
				uid = st.selectbox("Account UID", [user["uid"] for user in users])
				new_role = st.selectbox("Assign role", ["owner", "renter", "broker"])
				change = st.form_submit_button("Save role")
			if change:
				set_user_role(uid, new_role)
				st.success("Role updated.")
				st.rerun()
	except ApplicationAPIError as exc:
		st.error(str(exc))
