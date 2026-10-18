"""SQLite-backed Owner listing creation and account isolation regressions."""

import io
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import date
from PIL import Image

from fastapi.testclient import TestClient

from backend.core.auth import create_first_admin_account, register_account, sign_in
from backend.main import app
from backend.repositories import sqlite_repository


class OwnerWorkspaceAPITests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_directory.cleanup)
        self.database_path_patcher = patch.object(
            sqlite_repository,
            "DB_PATH",
            Path(self.temp_directory.name) / "owner-workspace.sqlite3",
        )
        self.database_path_patcher.start()
        self.addCleanup(self.database_path_patcher.stop)
        sqlite_repository.initialize_database()
        self.owner_uid, self.owner_token = register_account(
            "owner.one@example.test", "test-owner-password-one", "Owner One", "owner"
        )
        self.other_owner_uid, self.other_owner_token = register_account(
            "owner.two@example.test", "test-owner-password-two", "Owner Two", "owner"
        )
        self.renter_uid, self.renter_token = register_account(
            "renter.one@example.test", "test-renter-password", "Renter One", "renter"
        )
        self.admin_uid = create_first_admin_account(
            "admin@example.test", "test-admin-password", "Test Admin"
        )
        _admin_uid, self.admin_token = sign_in("admin@example.test", "test-admin-password")
        self.client = TestClient(app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    @staticmethod
    def _headers(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def test_owner_can_create_listing_and_other_owner_cannot_view_it(self):
        created = self.client.post(
            "/application/records",
            headers=self._headers(self.owner_token),
            json={
                "kind": "listing",
                "data": {
                    "title": "Owner One Listing",
                    "city": "Pune",
                    "locality": "Aundh",
                    "property_type": "Apartment",
                    "rent": 28000,
                    "bedrooms": 2,
                    "occupancy_status": "vacant",
                    "status": "published",
                },
            },
        )

        self.assertEqual(created.status_code, 201, created.text)
        record = created.json()
        self.assertEqual(record["owner_uid"], self.owner_uid)
        self.assertEqual(record["data"]["rent"], 28000)

        owner_records = self.client.get(
            "/application/auth/records?kind=listing",
            headers=self._headers(self.owner_token),
        )
        other_owner_records = self.client.get(
            "/application/auth/records?kind=listing",
            headers=self._headers(self.other_owner_token),
        )
        other_owner_detail = self.client.get(
            f"/application/records/{record['id']}",
            headers=self._headers(self.other_owner_token),
        )

        self.assertEqual([item["id"] for item in owner_records.json()["records"]], [record["id"]])
        self.assertEqual(other_owner_records.json()["records"], [])
        self.assertEqual(other_owner_detail.status_code, 403)

    def test_image_api_reports_missing_cloudinary_sdk_actionably(self):
        created = self.client.post(
            "/application/records", headers=self._headers(self.owner_token),
            json={"kind": "listing", "data": {
                "title": "Photo Test", "city": "Pune", "locality": "Aundh",
                "property_type": "Apartment", "rent": 15000, "bedrooms": 1,
                "occupancy_status": "vacant", "status": "draft",
            }},
        )
        self.assertEqual(created.status_code, 201, created.text)
        image = io.BytesIO()
        Image.new("RGB", (2, 2), color="blue").save(image, format="PNG")
        with patch("backend.services.image_service.get_cloudinary_credentials", return_value=("cloud", "key", "secret", [])), \
             patch.dict(sys.modules, {"cloudinary": None, "cloudinary.uploader": None}):
            response = self.client.post(
                f"/application/records/{created.json()['id']}/images",
                headers=self._headers(self.owner_token),
                files={"file": ("photo.png", image.getvalue(), "image/png")},
            )
        self.assertEqual(response.status_code, 503)
        self.assertIn("Cloudinary package is missing", response.json()["detail"])
        self.assertNotIn("api_secret", response.json()["detail"].lower())

    def test_image_api_persists_successful_owner_upload_for_renter_listing_read(self):
        created = self.client.post(
            "/application/records", headers=self._headers(self.owner_token),
            json={"kind": "listing", "data": {
                "title": "Photo Test", "city": "Pune", "locality": "Aundh",
                "property_type": "Apartment", "rent": 15000, "bedrooms": 1,
                "occupancy_status": "vacant", "status": "published",
            }},
        )
        self.assertEqual(created.status_code, 201, created.text)
        image = io.BytesIO()
        Image.new("RGB", (2, 2), color="blue").save(image, format="PNG")
        package = types.ModuleType("cloudinary")
        package.config = lambda **_kwargs: None
        uploader = types.ModuleType("cloudinary.uploader")
        uploader.upload = lambda _content, **_kwargs: {
            "secure_url": "https://res.cloudinary.com/test/image/upload/persisted.png",
            "public_id": "rentai/persisted", "bytes": image.tell(),
        }
        uploader.destroy = lambda *_args, **_kwargs: {"result": "ok"}
        package.uploader = uploader
        with patch("backend.services.image_service.get_cloudinary_credentials", return_value=("cloud", "key", "secret", [])), \
             patch.dict(sys.modules, {"cloudinary": package, "cloudinary.uploader": uploader}):
            response = self.client.post(
                f"/application/records/{created.json()['id']}/images",
                headers=self._headers(self.owner_token),
                files={"file": ("photo.png", image.getvalue(), "image/png")},
            )
            renter_images = self.client.get(
                "/application/auth/listing-images", headers=self._headers(self.renter_token),
            )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(renter_images.status_code, 200, renter_images.text)
        self.assertEqual([item["url"] for item in renter_images.json()["images"]], [
            "https://res.cloudinary.com/test/image/upload/persisted.png",
        ])

    def test_owner_can_view_edit_and_delete_unlinked_property(self):
        created = self.client.post(
            "/application/records",
            headers=self._headers(self.owner_token),
            json={"kind": "listing", "data": {
                "title": "First title", "city": "Pune", "locality": "Aundh",
                "property_type": "Apartment", "rent": 25000, "bedrooms": 2,
                "bathrooms": 1, "area_sqft": 900, "floor": 3,
                "furnishing_status": "Semi-Furnished",
                "distance_to_city_center_km": 4.5, "age_of_property": 5,
                "parking": 1, "status": "draft",
            }},
        )
        self.assertEqual(created.status_code, 201, created.text)
        record_id = created.json()["id"]

        detail = self.client.get(
            f"/application/records/{record_id}",
            headers=self._headers(self.owner_token),
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["data"]["area_sqft"], 900.0)
        self.assertEqual(detail.json()["data"]["furnishing_status"], "Semi-Furnished")

        invalid_update = self.client.put(
            f"/application/records/{record_id}",
            headers=self._headers(self.owner_token),
            json={"kind": "listing", "data": {**detail.json()["data"], "rent": 0}},
        )
        self.assertEqual(invalid_update.status_code, 400)
        unchanged = self.client.get(
            f"/application/records/{record_id}",
            headers=self._headers(self.owner_token),
        )
        self.assertEqual(unchanged.json()["data"]["rent"], 25000)

        update_payload = {
            **detail.json()["data"],
            "title": "Updated title",
            "rent": 27000,
            "status": "unlisted",
        }
        updated = self.client.put(
            f"/application/records/{record_id}",
            headers=self._headers(self.owner_token),
            json={"kind": "listing", "data": update_payload},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["data"]["title"], "Updated title")
        self.assertEqual(updated.json()["data"]["status"], "unlisted")
        self.assertEqual(updated.json()["data"]["rent"], 27000)
        self.assertEqual(updated.json()["id"], record_id)
        self.assertEqual(updated.json()["owner_uid"], self.owner_uid)
        reloaded = self.client.get(
            f"/application/records/{record_id}",
            headers=self._headers(self.owner_token),
        )
        self.assertEqual(reloaded.status_code, 200)
        self.assertEqual(reloaded.json()["data"]["title"], "Updated title")
        self.assertEqual(reloaded.json()["data"]["rent"], 27000)

        other_read = self.client.get(
            f"/application/records/{record_id}",
            headers=self._headers(self.other_owner_token),
        )
        other_update = self.client.put(
            f"/application/records/{record_id}",
            headers=self._headers(self.other_owner_token),
            json={"kind": "listing", "data": update_payload},
        )
        other_delete = self.client.delete(
            f"/application/records/{record_id}",
            headers=self._headers(self.other_owner_token),
        )
        self.assertEqual(other_read.status_code, 403)
        self.assertEqual(other_update.status_code, 403)
        self.assertEqual(other_delete.status_code, 403)

        deleted = self.client.delete(
            f"/application/records/{record_id}",
            headers=self._headers(self.owner_token),
        )
        self.assertEqual(deleted.status_code, 204)
        missing = self.client.get(
            f"/application/records/{record_id}",
            headers=self._headers(self.owner_token),
        )
        self.assertEqual(missing.status_code, 404)

    def test_property_with_linked_maintenance_must_be_unlisted_not_deleted(self):
        created = self.client.post(
            "/application/records",
            headers=self._headers(self.owner_token),
            json={"kind": "listing", "data": {
                "title": "Linked property", "city": "Pune", "locality": "Aundh",
                "property_type": "Apartment", "rent": 25000, "bedrooms": 2,
                "status": "published",
            }},
        )
        self.assertEqual(created.status_code, 201, created.text)
        record_id = created.json()["id"]
        maintenance = self.client.post(
            "/application/records",
            headers=self._headers(self.owner_token),
            json={"kind": "maintenance", "data": {
                "listing_id": record_id,
                "title": "Repair tap",
                "description": "Kitchen tap is leaking",
            }},
        )
        self.assertEqual(maintenance.status_code, 201, maintenance.text)

        deletion = self.client.delete(
            f"/application/records/{record_id}",
            headers=self._headers(self.owner_token),
        )
        self.assertEqual(deletion.status_code, 409)
        self.assertIn("Unlist", deletion.json()["detail"])

        unlisted = self.client.put(
            f"/application/records/{record_id}",
            headers=self._headers(self.owner_token),
            json={"kind": "listing", "data": {**created.json()["data"], "status": "unlisted"}},
        )
        self.assertEqual(unlisted.status_code, 200, unlisted.text)
        self.assertEqual(unlisted.json()["data"]["status"], "unlisted")

    def test_invalid_property_data_is_rejected_without_persisting(self):
        base_data = {
            "title": "Valid title", "city": "Pune", "locality": "Aundh",
            "property_type": "Apartment", "rent": 25000, "bedrooms": 2,
        }
        invalid_payloads = (
            {**base_data, "rent": 0},
            {**base_data, "area_sqft": -1},
            {**base_data, "status": "deleted"},
            {**base_data, "parking": 1.5},
        )
        for data in invalid_payloads:
            with self.subTest(data=data):
                response = self.client.post(
                    "/application/records",
                    headers=self._headers(self.owner_token),
                    json={"kind": "listing", "data": data},
                )
                self.assertEqual(response.status_code, 400)
        listing_rows = self.client.get(
            "/application/auth/records?kind=listing",
            headers=self._headers(self.owner_token),
        )
        self.assertEqual(listing_rows.json()["records"], [])

    def test_owner_profile_and_settings_are_persisted_and_private(self):
        profile = self.client.post(
            "/application/records",
            headers=self._headers(self.owner_token),
            json={"kind": "profile", "data": {
                "record_type": "owner_profile", "name": "Owner One",
                "phone": "555-0100", "business_name": "Aundh Homes",
                "bio": "Owner profile saved in the application store.",
            }},
        )
        settings = self.client.post(
            "/application/records",
            headers=self._headers(self.owner_token),
            json={"kind": "profile", "data": {
                "record_type": "owner_settings", "email_alerts": False,
                "maintenance_alerts": True, "summary_frequency": "monthly",
            }},
        )
        self.assertEqual(profile.status_code, 201, profile.text)
        self.assertEqual(settings.status_code, 201, settings.text)

        saved_profile = self.client.put(
            f"/application/records/{profile.json()['id']}",
            headers=self._headers(self.owner_token),
            json={"kind": "profile", "data": {
                "record_type": "owner_profile", "name": "Owner One Updated",
                "phone": "555-0100", "business_name": "Aundh Homes",
                "bio": "Updated profile after reload.",
            }},
        )
        self.assertEqual(saved_profile.status_code, 200, saved_profile.text)
        reloaded_profiles = self.client.get(
            "/application/auth/records?kind=profile",
            headers=self._headers(self.owner_token),
        )
        self.assertEqual(reloaded_profiles.status_code, 200, reloaded_profiles.text)
        persisted_profile = next(
            record for record in reloaded_profiles.json()["records"]
            if record.get("data", {}).get("record_type") == "owner_profile"
        )
        self.assertEqual(persisted_profile["data"]["name"], "Owner One Updated")
        self.assertEqual(persisted_profile["data"]["phone"], "555-0100")
        self.assertEqual(persisted_profile["data"]["business_name"], "Aundh Homes")
        self.assertEqual(persisted_profile["data"]["bio"], "Updated profile after reload.")
        denied_profile_edit = self.client.put(
            f"/application/records/{profile.json()['id']}",
            headers=self._headers(self.other_owner_token),
            json={"kind": "profile", "data": {
                "record_type": "owner_profile", "name": "Other Owner Edit",
            }},
        )
        self.assertEqual(denied_profile_edit.status_code, 403)

        sqlite_repository.SQLiteRepository().revoke_session(self.owner_token)
        signed_in_uid, new_owner_token = sign_in(
            "owner.one@example.test", "test-owner-password-one"
        )
        self.assertEqual(signed_in_uid, self.owner_uid)
        after_login = self.client.get(
            "/application/auth/records?kind=profile",
            headers=self._headers(new_owner_token),
        )
        self.assertEqual(after_login.status_code, 200, after_login.text)
        profile_after_login = next(
            record for record in after_login.json()["records"]
            if record.get("data", {}).get("record_type") == "owner_profile"
        )
        self.assertEqual(profile_after_login["data"]["name"], "Owner One Updated")
        self.assertEqual(profile_after_login["data"]["phone"], "555-0100")
        self.assertEqual(profile_after_login["data"]["business_name"], "Aundh Homes")
        self.assertEqual(profile_after_login["data"]["bio"], "Updated profile after reload.")

        changed = self.client.put(
            f"/application/records/{settings.json()['id']}",
            headers=self._headers(new_owner_token),
            json={"kind": "profile", "data": {
                "record_type": "owner_settings", "email_alerts": True,
                "maintenance_alerts": False, "summary_frequency": "off",
            }},
        )
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertTrue(changed.json()["data"]["email_alerts"])

        owner_profiles = self.client.get(
            "/application/auth/records?kind=profile",
            headers=self._headers(new_owner_token),
        )
        other_owner_profiles = self.client.get(
            "/application/auth/records?kind=profile",
            headers=self._headers(self.other_owner_token),
        )
        self.assertEqual(len(owner_profiles.json()["records"]), 2)
        self.assertEqual(other_owner_profiles.json()["records"], [])

    def test_renter_enquiry_owner_decision_and_fresh_login_are_private_and_persistent(self):
        listing = self.client.post(
            "/application/records",
            headers=self._headers(self.owner_token),
            json={"kind": "listing", "data": {
                "title": "Available Home", "city": "Pune", "locality": "Aundh",
                "property_type": "Apartment", "rent": 28000, "bedrooms": 2,
                "status": "published",
            }},
        )
        self.assertEqual(listing.status_code, 201, listing.text)
        listing_id = listing.json()["id"]

        submission = self.client.post(
            "/application/records",
            headers=self._headers(self.renter_token),
            json={"kind": "inquiry", "data": {
                "listing_id": listing_id, "client_name": "Renter One",
                "client_phone": "555-0102", "message": "",
            }},
        )
        self.assertEqual(submission.status_code, 201, submission.text)
        enquiry = submission.json()
        self.assertEqual(enquiry["owner_uid"], self.renter_uid)
        self.assertEqual(enquiry["data"]["client_uid"], self.renter_uid)
        self.assertEqual(enquiry["data"]["property_owner_uid"], self.owner_uid)
        self.assertEqual(enquiry["data"]["status"], "pending")
        self.assertEqual(enquiry["data"]["property"]["title"], "Available Home")

        duplicate = self.client.post(
            "/application/records",
            headers=self._headers(self.renter_token),
            json={"kind": "inquiry", "data": {
                "listing_id": listing_id, "client_name": "Renter One", "message": "Again",
            }},
        )
        self.assertEqual(duplicate.status_code, 409, duplicate.text)

        visible_to_owner = self.client.get(
            "/application/auth/records?kind=inquiry", headers=self._headers(self.owner_token)
        )
        self.assertEqual([item["id"] for item in visible_to_owner.json()["records"]], [enquiry["id"]])
        hidden_from_other_owner = self.client.get(
            "/application/auth/records?kind=inquiry", headers=self._headers(self.other_owner_token)
        )
        self.assertEqual(hidden_from_other_owner.json()["records"], [])

        updated = self.client.put(
            f"/application/records/{enquiry['id']}",
            headers=self._headers(self.owner_token),
            json={"kind": "inquiry", "data": {**enquiry["data"], "status": "accepted"}},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["data"]["status"], "accepted")
        denied_owner_update = self.client.put(
            f"/application/records/{enquiry['id']}",
            headers=self._headers(self.other_owner_token),
            json={"kind": "inquiry", "data": {"status": "rejected"}},
        )
        self.assertEqual(denied_owner_update.status_code, 403)
        denied_renter_update = self.client.put(
            f"/application/records/{enquiry['id']}",
            headers=self._headers(self.renter_token),
            json={"kind": "inquiry", "data": {"status": "rejected"}},
        )
        self.assertEqual(denied_renter_update.status_code, 403)

        sqlite_repository.SQLiteRepository().revoke_session(self.renter_token)
        signed_in_uid, fresh_renter_token = sign_in(
            "renter.one@example.test", "test-renter-password"
        )
        self.assertEqual(signed_in_uid, self.renter_uid)
        after_login = self.client.get(
            "/application/auth/records?kind=inquiry",
            headers=self._headers(fresh_renter_token),
        )
        persisted = next(item for item in after_login.json()["records"] if item["id"] == enquiry["id"])
        self.assertEqual(persisted["data"]["status"], "accepted")
        self.assertEqual(persisted["data"]["property"]["title"], "Available Home")

        draft = self.client.post(
            "/application/records",
            headers=self._headers(self.owner_token),
            json={"kind": "listing", "data": {
                "title": "Draft Home", "city": "Pune", "locality": "Baner",
                "property_type": "Apartment", "rent": 25000, "bedrooms": 1,
                "status": "draft",
            }},
        )
        unpublished_enquiry = self.client.post(
            "/application/records",
            headers=self._headers(fresh_renter_token),
            json={"kind": "inquiry", "data": {
                "listing_id": draft.json()["id"], "client_name": "Renter One", "message": "Interested",
            }},
        )
        self.assertEqual(unpublished_enquiry.status_code, 403, unpublished_enquiry.text)
        dataset_only_enquiry = self.client.post(
            "/application/records",
            headers=self._headers(fresh_renter_token),
            json={"kind": "inquiry", "data": {
                "listing_id": "RENT-AI-DATASET-ROW-1", "client_name": "Renter One", "message": "Interested",
            }},
        )
        self.assertEqual(dataset_only_enquiry.status_code, 400, dataset_only_enquiry.text)

    def test_admin_user_role_change_is_persisted_and_immediately_effective(self):
        users = self.client.get(
            "/application/admin/users", headers=self._headers(self.admin_token)
        )
        self.assertEqual(users.status_code, 200, users.text)
        self.assertIn(self.owner_uid, {user["uid"] for user in users.json()["users"]})

        denied = self.client.get(
            "/application/admin/users", headers=self._headers(self.owner_token)
        )
        self.assertEqual(denied.status_code, 403)

        updated = self.client.put(
            f"/application/admin/users/{self.owner_uid}/role",
            headers=self._headers(self.admin_token),
            json={"role": "renter"},
        )
        self.assertEqual(updated.status_code, 204, updated.text)
        current = self.client.get(
            "/application/auth/me", headers=self._headers(self.owner_token)
        )
        self.assertEqual(current.json()["role"], "renter")

    def test_owner_confirms_tenancy_records_due_and_persists_paid_rent(self):
        listing = self.client.post(
            "/application/records", headers=self._headers(self.owner_token),
            json={"kind": "listing", "data": {
                "title": "Rented Home", "city": "Pune", "locality": "Aundh",
                "property_type": "Apartment", "rent": 30000, "bedrooms": 2,
                "status": "published",
            }},
        )
        self.assertEqual(listing.status_code, 201, listing.text)
        inquiry = self.client.post(
            "/application/records", headers=self._headers(self.renter_token),
            json={"kind": "inquiry", "data": {
                "listing_id": listing.json()["id"], "client_name": "Renter One",
                "client_phone": "555-0102", "message": "Interested",
            }},
        )
        self.assertEqual(inquiry.status_code, 201, inquiry.text)
        tenancy_payload = {"kind": "tenancy", "data": {
            "inquiry_id": inquiry.json()["id"], "start_date": date.today().isoformat(),
            "monthly_rent": 28000, "due_day": 5,
        }}
        not_accepted = self.client.post(
            "/application/records", headers=self._headers(self.owner_token), json=tenancy_payload,
        )
        self.assertEqual(not_accepted.status_code, 409, not_accepted.text)

        accepted = self.client.put(
            f"/application/records/{inquiry.json()['id']}", headers=self._headers(self.owner_token),
            json={"kind": "inquiry", "data": {**inquiry.json()["data"], "status": "accepted"}},
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)
        tenancy = self.client.post(
            "/application/records", headers=self._headers(self.owner_token), json=tenancy_payload,
        )
        self.assertEqual(tenancy.status_code, 201, tenancy.text)
        self.assertEqual(tenancy.json()["data"]["renter_uid"], self.renter_uid)
        self.assertEqual(tenancy.json()["data"]["monthly_rent"], 28000)
        duplicate_tenancy = self.client.post(
            "/application/records", headers=self._headers(self.owner_token), json=tenancy_payload,
        )
        self.assertEqual(duplicate_tenancy.status_code, 409)
        self.assertEqual(self.client.get(
            f"/application/records/{tenancy.json()['id']}", headers=self._headers(self.other_owner_token),
        ).status_code, 403)
        self.assertEqual(self.client.post(
            "/application/records", headers=self._headers(self.renter_token), json=tenancy_payload,
        ).status_code, 403)

        period = date.today().strftime("%Y-%m")
        due_record = self.client.post(
            "/application/records", headers=self._headers(self.owner_token),
            json={"kind": "rent_payment", "data": {"tenancy_id": tenancy.json()["id"], "period": period}},
        )
        self.assertEqual(due_record.status_code, 201, due_record.text)
        self.assertEqual(due_record.json()["data"]["status"], "pending")
        self.assertEqual(due_record.json()["data"]["amount"], 28000)
        duplicate_period = self.client.post(
            "/application/records", headers=self._headers(self.owner_token),
            json={"kind": "rent_payment", "data": {"tenancy_id": tenancy.json()["id"], "period": period}},
        )
        self.assertEqual(duplicate_period.status_code, 409)
        self.assertEqual(self.client.get(
            f"/application/records/{due_record.json()['id']}", headers=self._headers(self.other_owner_token),
        ).status_code, 403)
        self.assertEqual(self.client.put(
            f"/application/records/{due_record.json()['id']}", headers=self._headers(self.renter_token),
            json={"kind": "rent_payment", "data": {"status": "paid"}},
        ).status_code, 403)

        invalid_paid = self.client.put(
            f"/application/records/{due_record.json()['id']}", headers=self._headers(self.owner_token),
            json={"kind": "rent_payment", "data": {
                **due_record.json()["data"], "status": "paid", "paid_date": date.today().isoformat(),
                "paid_amount": 1, "payment_method": "UPI",
            }},
        )
        self.assertEqual(invalid_paid.status_code, 400, invalid_paid.text)
        paid = self.client.put(
            f"/application/records/{due_record.json()['id']}", headers=self._headers(self.owner_token),
            json={"kind": "rent_payment", "data": {
                **due_record.json()["data"], "status": "paid", "paid_date": date.today().isoformat(),
                "paid_amount": 28000, "payment_method": "UPI", "payment_reference": "receipt-1",
            }},
        )
        self.assertEqual(paid.status_code, 200, paid.text)
        self.assertEqual(paid.json()["data"]["status"], "paid")
        reloaded = self.client.get(
            "/application/auth/records?kind=rent_payment", headers=self._headers(self.owner_token),
        )
        self.assertEqual(reloaded.status_code, 200, reloaded.text)
        saved = next(item for item in reloaded.json()["records"] if item["id"] == due_record.json()["id"])
        self.assertEqual(saved["data"]["paid_amount"], 28000)
        self.assertEqual(saved["data"]["payment_reference"], "receipt-1")
        linked_listing_delete = self.client.delete(
            f"/application/records/{listing.json()['id']}", headers=self._headers(self.owner_token),
        )
        self.assertEqual(linked_listing_delete.status_code, 409, linked_listing_delete.text)

        ended = self.client.put(
            f"/application/records/{tenancy.json()['id']}", headers=self._headers(self.owner_token),
            json={"kind": "tenancy", "data": {
                **tenancy.json()["data"], "status": "ended", "end_date": date.today().isoformat(),
            }},
        )
        self.assertEqual(ended.status_code, 200, ended.text)
        self.assertEqual(ended.json()["data"]["status"], "ended")


if __name__ == "__main__":
    unittest.main()
