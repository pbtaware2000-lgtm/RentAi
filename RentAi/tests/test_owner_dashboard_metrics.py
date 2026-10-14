"""Owner dashboard totals must use their authorized portfolio records."""

import unittest

from backend.auth.dependencies import CurrentUser
from backend.services.application_service import ApplicationService
from frontend.pages.owner_dashboard import calculate_metrics


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
        self.assertEqual(result["new_enquiries"], 1)
        self.assertEqual(result["potential_monthly_rent"], 55000)

    def test_empty_owner_portfolio_has_real_zero_potential(self):
        result = calculate_metrics([], [], [])
        self.assertEqual(result["potential_monthly_rent"], 0)
        self.assertEqual(result["vacant"], 0)
        self.assertEqual(result["occupied"], 0)


if __name__ == "__main__":
    unittest.main()
