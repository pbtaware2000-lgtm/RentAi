"""Owner listing updates must agree with linked active tenancies."""

import unittest

from fastapi import HTTPException

from backend.services.application_service import ApplicationService


class MemoryRepository:
    def __init__(self, records):
        self.records = records

    def get(self, _collection, record_id):
        return self.records.get(record_id)

    def list_records(self):
        return list(self.records.values())

    def update(self, _collection, record_id, data):
        self.records[record_id].update(data)
        return True


class OwnerOccupancyUpdateTests(unittest.TestCase):
    def setUp(self):
        self.owner = type("User", (), {"uid": "owner-1", "role": "owner"})()
        self.listing_data = {
            "title": "Pune Apartment", "city": "Pune", "locality": "Aundh",
            "rent": 15000, "bedrooms": 2, "property_type": "Apartment",
            "occupancy_status": "occupied", "status": "published",
        }

    def service_with_tenancy(self, status):
        records = {
            "listing-1": {"id": "listing-1", "kind": "listing", "owner_uid": "owner-1", "data": dict(self.listing_data)},
        }
        if status:
            records["tenancy-1"] = {
                "id": "tenancy-1", "kind": "tenancy", "owner_uid": "owner-1",
                "data": {"listing_id": "listing-1", "status": status},
            }
        repository = MemoryRepository(records)
        return ApplicationService(repository), repository

    def test_active_tenancy_blocks_marking_property_vacant(self):
        service, repository = self.service_with_tenancy("active")

        with self.assertRaises(HTTPException) as caught:
            service.update_record(self.owner, "listing-1", {**self.listing_data, "occupancy_status": "vacant"})

        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(repository.records["listing-1"]["data"]["occupancy_status"], "occupied")

    def test_ended_tenancy_allows_owner_to_mark_property_vacant(self):
        service, _repository = self.service_with_tenancy("ended")

        updated = service.update_record(self.owner, "listing-1", {**self.listing_data, "occupancy_status": "vacant"})

        self.assertEqual(updated["data"]["occupancy_status"], "vacant")

    def test_property_without_tenancy_can_be_marked_vacant(self):
        service, _repository = self.service_with_tenancy(None)

        updated = service.update_record(self.owner, "listing-1", {**self.listing_data, "occupancy_status": "vacant"})

        self.assertEqual(updated["data"]["occupancy_status"], "vacant")


if __name__ == "__main__":
    unittest.main()
