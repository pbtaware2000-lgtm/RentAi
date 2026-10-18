"""Owner dashboard totals must use their authorized portfolio records."""

import unittest
from unittest.mock import patch

import pandas as pd
from streamlit.testing.v1 import AppTest

from backend.auth.dependencies import CurrentUser
from backend.services.application_service import ApplicationService
from frontend.pages.owner_dashboard import calculate_dataset_metrics, calculate_metrics
from frontend.pages.owner_support import money
from frontend.pages.owner_sections import (
    build_owner_count_chart,
    build_owner_rent_chart,
    calculate_comparable_rents,
    calculate_owner_property_analytics,
    render_analytics,
    render_revenue,
)


class RecordsRepository:
    def __init__(self, records):
        self._records = records

    def list_records(self):
        return self._records


class OwnerDashboardMetricsTests(unittest.TestCase):
    def test_service_returns_only_current_owners_listings_and_enquiries(self):
        records = [
            {"id": "owned", "kind": "listing", "owner_uid": "owner-a", "data": {"status": "published"}},
            {"id": "other", "kind": "listing", "owner_uid": "owner-b", "data": {"status": "published"}},
            {"id": "own-enquiry", "kind": "inquiry", "owner_uid": "renter", "data": {"property_owner_uid": "owner-a"}},
            {"id": "other-enquiry", "kind": "inquiry", "owner_uid": "renter", "data": {"property_owner_uid": "owner-b"}},
        ]
        service = ApplicationService(RecordsRepository(records))
        owner = CurrentUser(uid="owner-a", role="owner", claims={})

        self.assertEqual([r["id"] for r in service.list_records_for_user(owner, "listing")], ["owned"])
        self.assertEqual([r["id"] for r in service.list_records_for_user(owner, "inquiry")], ["own-enquiry"])

    def test_metrics_count_known_occupancy_and_sum_published_rent(self):
        listings = [
            {"data": {"status": "published", "rent": 25000, "occupancy_status": "vacant"}},
            {"data": {"status": "published", "rent": 30000, "occupancy_status": "occupied"}},
            {"data": {"status": "draft", "rent": 40000, "occupancy_status": "not_tracked"}},
            {"data": {"status": "draft", "rent": "invalid"}},
        ]
        enquiries = [
            {"data": {"status": "new"}},
            {"data": {"status": "contacted"}},
        ]

        result = calculate_metrics(listings, enquiries, [])

        self.assertEqual(len(result["details"]), 4)
        self.assertEqual(len(result["published"]), 2)
        self.assertEqual(len(result["drafts"]), 2)
        self.assertEqual(result["vacant"], 1)
        self.assertEqual(result["occupied"], 1)
        self.assertEqual(result["occupancy_unknown"], 2)
        self.assertEqual(result["new_enquiries"], 2)
        self.assertEqual(result["potential_monthly_rent"], 55000)

    def test_empty_owner_portfolio_has_real_zero_potential(self):
        result = calculate_metrics([], [], [])
        self.assertEqual(result["potential_monthly_rent"], 0)
        self.assertEqual(result["vacant"], 0)
        self.assertEqual(result["occupied"], 0)

    def test_rent_metrics_use_saved_paid_and_pending_records(self):
        result = calculate_metrics([], [], [], [
            {"data": {"status": "paid", "amount": 30000, "paid_amount": 30000}},
            {"data": {"status": "pending", "amount": 28000}},
            {"data": {"status": "paid", "amount": 15000, "paid_amount": 15000}},
            {"data": {"status": "paid", "amount": "invalid", "paid_amount": "invalid"}},
        ])
        self.assertEqual(result["rent_collected"], 45000)
        self.assertEqual(result["rent_outstanding"], 28000)

    def test_revenue_page_renders_explicit_tenancy_and_collection_workflow(self):
        page_records = {
            "listing": [{"id": "listing-1", "data": {
                "title": "Saved Home", "city": "Pune", "rent": 28000,
                "status": "published", "occupancy_status": "occupied",
            }}],
            "inquiry": [{"id": "inquiry-1", "data": {
                "client_name": "Renter One", "status": "accepted",
                "property": {"title": "Saved Home"}, "listing_id": "listing-1",
            }}],
            "tenancy": [],
            "rent_payment": [{"id": "rent-1", "data": {
                "amount": 15000, "paid_amount": 15000, "status": "pending",
            }}],
        }
        source = "from frontend.pages.owner_sections import render_revenue\nrender_revenue()"
        with patch("frontend.pages.owner_sections.records", side_effect=lambda kind: page_records[kind]):
            pending_app = AppTest.from_string(source, default_timeout=30).run()
        self.assertFalse(pending_app.exception)
        self.assertIn("Confirm tenancy", [item.label for item in pending_app.button])
        pending_headings = [item.value for item in pending_app.markdown]
        self.assertIn("**Potential monthly listed rent**", pending_headings)
        self.assertIn("**Total rent collected**", pending_headings)
        self.assertIn("**Outstanding rent**", pending_headings)
        pending_values = [metric.value for metric in pending_app.metric]
        self.assertEqual(pending_values, [money(28000), money(0), money(15000)])

        page_records["rent_payment"][0]["data"]["status"] = "paid"
        with patch("frontend.pages.owner_sections.records", side_effect=lambda kind: page_records[kind]):
            paid_app = AppTest.from_string(source, default_timeout=30).run()
        self.assertFalse(paid_app.exception)
        paid_values = [metric.value for metric in paid_app.metric]
        self.assertEqual(paid_values, [money(28000), money(15000), money(0)])

    def test_dataset_analytics_are_separate_from_owner_portfolio_metrics(self):
        data = pd.DataFrame([
            {"rent": 1000, "rent_category": "Low", "city": "Pune", "locality": "Aundh", "property_type": "Apartment", "posted_date": "2025-01-12"},
            {"rent": 3000, "rent_category": "High", "city": "Pune", "locality": "Aundh", "property_type": "House", "posted_date": "2025-02-03"},
            {"rent": 2000, "rent_category": "Medium", "city": "Mumbai", "locality": "Bandra", "property_type": "Studio", "posted_date": "2025-02-14"},
        ])
        metrics = calculate_dataset_metrics(data)

        self.assertEqual(metrics["record_count"], 3)
        self.assertEqual(metrics["average_rent"], 2000)
        self.assertEqual(metrics["median_rent"], 2000)
        self.assertEqual(metrics["city_count"], 2)
        self.assertEqual(metrics["locality_count"], 2)
        self.assertEqual(metrics["category_counts"].to_dict(), {"Low": 1, "High": 1, "Medium": 1})
        self.assertEqual(metrics["monthly_rent"]["postings"].tolist(), [1, 2])

        empty_records = {"listings": [], "enquiries": [], "maintenance": []}
        with patch("frontend.pages.owner_dashboard.load_or_report", return_value=empty_records):
            app = AppTest.from_string(
                "import pandas as pd\n"
                "from frontend.pages.owner_dashboard import render\n"
                f"render(pd.DataFrame({data.to_dict(orient='records')!r}))",
                default_timeout=30,
            ).run()

        self.assertFalse(app.exception)
        rendered_metrics = {metric.label: metric.value for metric in app.metric}
        self.assertEqual(rendered_metrics["Total properties"], "0")
        self.assertEqual(rendered_metrics["Dataset records"], "3")
        self.assertEqual(rendered_metrics["Average dataset rent"], "₹2,000")
        self.assertIn("Rental market dataset", [item.value for item in app.subheader])

    def test_owner_property_analytics_distinguish_vacant_occupied_and_unknown(self):
        listings = [
            {"id": "vacant", "data": {"status": "published", "occupancy_status": "vacant", "rent": 15000, "city": "Pune", "property_type": "Apartment", "furnishing_status": "Unfurnished", "bedrooms": 2}},
            {"id": "occupied", "data": {"status": "published", "occupancy_status": "occupied", "rent": 20000, "city": "Pune", "property_type": "Apartment", "furnishing_status": "Furnished", "bedrooms": 3}},
            {"id": "unknown", "data": {"status": "draft", "occupancy_status": "not_tracked", "rent": "25000", "city": "Mumbai", "property_type": "Studio", "furnishing_status": "Unfurnished", "bedrooms": 1}},
            {"id": "unlisted", "data": {"status": "unlisted", "occupancy_status": None, "rent": "invalid", "city": "Pune", "property_type": "Villa"}},
        ]

        metrics = calculate_owner_property_analytics(listings)

        self.assertEqual(metrics["total"], 4)
        self.assertEqual(metrics["published"], 2)
        self.assertEqual(metrics["draft"], 1)
        self.assertEqual(metrics["unlisted"], 1)
        self.assertEqual(metrics["vacant"], 1)
        self.assertEqual(metrics["occupied"], 1)
        self.assertEqual(metrics["occupancy_unknown"], 2)
        self.assertEqual(metrics["average_rent"], 20000)
        self.assertEqual(metrics["minimum_rent"], 15000)
        self.assertEqual(metrics["maximum_rent"], 25000)
        self.assertEqual(metrics["furnishing_counts"]["Not recorded"], 1)

    def test_owner_property_analytics_active_tenancies_override_manual_occupancy(self):
        listings = [
            {"id": "vacant-with-tenant", "data": {"status": "published", "occupancy_status": "vacant", "rent": 15000, "city": "Pune"}},
            {"id": "vacant-with-ended-tenancy", "data": {"status": "published", "occupancy_status": "vacant", "rent": 18000, "city": "Pune"}},
            {"id": "occupied-manually", "data": {"status": "published", "occupancy_status": "occupied", "rent": 22000, "city": "Mumbai"}},
            {"id": "unknown", "data": {"status": "draft", "occupancy_status": "not_tracked", "rent": 20000, "city": "Mumbai"}},
        ]
        tenancies = [
            {"id": "active-tenancy", "data": {"listing_id": "vacant-with-tenant", "status": "active"}},
            {"id": "ended-tenancy", "data": {"listing_id": "vacant-with-ended-tenancy", "status": "ended"}},
        ]

        metrics = calculate_owner_property_analytics(listings, tenancies)

        self.assertEqual(metrics["total"], 4)
        self.assertEqual(metrics["published"], 3)
        self.assertEqual(metrics["vacant"], 1)
        self.assertEqual(metrics["occupied"], 2)
        self.assertEqual(metrics["occupancy_unknown"], 1)
        # Tenancy-aware occupancy must not alter listing or rent analytics.
        self.assertEqual(metrics["average_rent"], 18750)
        self.assertEqual(metrics["minimum_rent"], 15000)
        self.assertEqual(metrics["maximum_rent"], 22000)

    def test_comparable_rents_use_location_type_bedrooms_and_area_and_ignore_invalid_rents(self):
        data = pd.DataFrame([
            {"city": "Pune", "locality": "Wakad", "property_type": "Apartment", "bedrooms": 2, "area_sqft": 900, "rent": 18000},
            {"city": "Pune", "locality": "Wakad", "property_type": "Apartment", "bedrooms": 2, "area_sqft": 1100, "rent": 20000},
            {"city": "Pune", "locality": "Wakad", "property_type": "Apartment", "bedrooms": 2, "area_sqft": 1400, "rent": 30000},
            {"city": "Pune", "locality": "Wakad", "property_type": "Studio", "bedrooms": 2, "area_sqft": 950, "rent": 12000},
            {"city": "Mumbai", "locality": "Wakad", "property_type": "Apartment", "bedrooms": 2, "area_sqft": 950, "rent": 22000},
            {"city": "Pune", "locality": "Wakad", "property_type": "Apartment", "bedrooms": 2, "area_sqft": 925, "rent": "not-a-rent"},
        ])

        comparison = calculate_comparable_rents(data, {
            "city": "Pune", "locality": "Wakad", "property_type": "Apartment",
            "bedrooms": 2, "area_sqft": 1000,
        })

        self.assertEqual(comparison["count"], 2)
        self.assertEqual(comparison["median_rent"], 19000)
        self.assertEqual(comparison["filters"], [
            "Pune city", "Wakad locality", "Apartment property type",
            "2 bedrooms", "area within ±25%",
        ])

    def test_comparable_rents_handle_missing_features_and_empty_dataset(self):
        data = pd.DataFrame([
            {"city": "Pune", "rent": 12000},
            {"city": "Pune", "rent": "invalid"},
            {"city": None, "rent": 50000},
        ])
        comparison = calculate_comparable_rents(data, {
            "city": "Pune", "locality": "Wakad", "property_type": "Apartment",
        })
        self.assertEqual(comparison["count"], 1)
        self.assertEqual(comparison["median_rent"], 12000)
        self.assertTrue(any("locality" in note for note in comparison["notes"]))

        empty = calculate_comparable_rents(pd.DataFrame(), {"city": "Pune"})
        self.assertEqual(empty["count"], 0)
        self.assertIsNone(empty["median_rent"])
        self.assertTrue(empty["notes"])

    def test_property_analytics_show_owner_asking_rent_separate_from_market_and_ml(self):
        listing = {
            "id": "owner-property", "owner_uid": "owner-1", "kind": "listing",
            "data": {
                "title": "Wakad Apartment", "city": "Pune", "locality": "Wakad",
                "property_type": "Apartment", "rent": 15000, "bedrooms": 2,
                "bathrooms": 1, "area_sqft": 950, "floor": 3,
                "furnishing_status": "Unfurnished", "distance_to_city_center_km": 8,
                "age_of_property": 5, "parking": 1,
                "occupancy_status": "vacant", "status": "published",
            },
        }
        data = pd.DataFrame([
            {"city": "Pune", "locality": "Wakad", "property_type": "Apartment", "bedrooms": 2, "area_sqft": 900, "rent": 18000},
            {"city": "Pune", "locality": "Wakad", "property_type": "Apartment", "bedrooms": 2, "area_sqft": 1000, "rent": 22000},
        ])
        records_data = {"listings": [listing], "enquiries": [], "maintenance": []}
        with patch("frontend.pages.owner_sections.load_or_report", return_value=records_data), patch(
            "frontend.pages.owner_sections.predict_rent", return_value=21000.0
        ) as prediction, patch(
            "frontend.pages.owner_sections.classify_rent", return_value="High"
        ) as classification, patch(
            "frontend.pages.owner_sections.similar_properties_for_features",
            return_value=[{"property_id": "CSV-1", "city": "Pune", "locality": "Wakad", "property_type": "Apartment", "bedrooms": 2, "area_sqft": 900, "rent": 18000}],
        ) as similar:
            app = AppTest.from_string(
                "import pandas as pd\n"
                "from frontend.pages.owner_sections import render_analytics\n"
                f"render_analytics(pd.DataFrame({data.to_dict(orient='records')!r}))",
                default_timeout=30,
            ).run()

        self.assertFalse(app.exception)
        rendered = "\n".join(item.value for item in app.markdown)
        for label in (
            "Total Properties", "Published Listings", "Draft Listings", "Unlisted Properties",
            "Vacant Properties", "Occupied Properties", "Unknown Occupancy",
            "Average Asking Rent", "Lowest Asking Rent", "Highest Asking Rent",
            "Owner Asking Rent", "ML Predicted Monthly Rent", "Comparable Dataset Median Rent",
            "Difference Between Asking Rent and ML Estimate",
        ):
            self.assertIn(label, rendered)
        for value in ("1", "0", "15,000", "21,000", "20,000", "High"):
            self.assertIn(value, rendered)
        for label, value in (
            ("Total Properties", "1"), ("Published Listings", "1"),
            ("Draft Listings", "0"), ("Unlisted Properties", "0"),
            ("Vacant Properties", "1"), ("Occupied Properties", "0"),
            ("Unknown Occupancy", "0"),
        ):
            self.assertIn(f'aria-label="{label}: {value}"', rendered)
        self.assertEqual(money(15000), chr(0x20b9) + "15,000")
        self.assertTrue(any("authenticated Owner account" in item.value for item in app.caption))
        self.assertIn("CSV-trained model", rendered)
        self.assertEqual(len(app.get("plotly_chart")), 1)
        prediction.assert_called_once()
        classification.assert_called_once()
        similar.assert_called_once()

    def test_property_rent_chart_keeps_long_titles_horizontal_and_compact(self):
        long_title = "A spacious apartment overlooking the Wakad residential district near the metro"
        figure = build_owner_rent_chart(pd.DataFrame([
            {"Property": long_title, "Asking rent": 250000},
        ]))

        self.assertIsNotNone(figure)
        self.assertEqual(figure.data[0].orientation, "h")
        self.assertEqual(list(figure.data[0].y), [long_title])
        self.assertLessEqual(figure.layout.height, 220)
        self.assertEqual(figure.layout.xaxis.range[0], 0)
        self.assertIn("₹1,40,000", list(figure.layout.xaxis.ticktext))
        self.assertIsNone(build_owner_rent_chart(pd.DataFrame(columns=["Property", "Asking rent"])))

    def test_city_distribution_chart_uses_horizontal_full_labels_and_zero_based_counts(self):
        figure = build_owner_count_chart(
            pd.Series({"Bengaluru": 4, "Pune": 1}),
            "City",
            "#059669",
        )

        self.assertEqual(figure.data[0].orientation, "h")
        self.assertEqual(set(figure.data[0].y), {"Bengaluru", "Pune"})
        self.assertEqual(figure.layout.xaxis.range[0], 0)
        self.assertEqual(figure.layout.xaxis.title.text, "Listings")
        self.assertEqual(figure.layout.yaxis.title.text, "City")
        self.assertEqual(figure.layout.title.text, "Listings by city")
        self.assertIsNone(build_owner_count_chart(pd.Series(dtype="int64"), "City", "#059669"))

    def test_empty_property_analytics_renders_honest_empty_state_without_charts(self):
        with patch(
            "frontend.pages.owner_sections.load_or_report",
            return_value={"listings": [], "enquiries": [], "maintenance": [], "profiles": []},
        ):
            app = AppTest.from_string(
                "import pandas as pd\n"
                "from frontend.pages.owner_sections import render_analytics\n"
                "render_analytics(pd.DataFrame())",
                default_timeout=30,
            ).run()

        self.assertFalse(app.exception)
        self.assertTrue(any("No saved properties yet" in item.value for item in app.info))
        self.assertEqual(len(app.get("plotly_chart")), 0)


if __name__ == "__main__":
    unittest.main()
