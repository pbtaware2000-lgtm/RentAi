"""Integration checks for the SQLite-backed Admin workflows."""

import tempfile
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from fastapi import HTTPException
import pandas as pd
from streamlit.testing.v1 import AppTest

from backend.auth.dependencies import CurrentUser
from backend.repositories import sqlite_repository
from backend.repositories.sqlite_repository import SQLiteRepository
from backend.services.application_service import ApplicationService
from frontend.components.sidebar import render_sidebar
from frontend.components.owner_sidebar import render_owner_sidebar
from frontend.components.renter_sidebar import render_renter_sidebar


class AdminWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_patch = patch.object(
            sqlite_repository, "DB_PATH", Path(self.temp_dir.name) / "rentai-test.sqlite3"
        )
        self.database_patch.start()
        sqlite_repository.initialize_database()
        self.repository = SQLiteRepository()
        self.admin_uid = self.repository.create_account(
            "admin@example.test", "Admin", "test-hash", "admin"
        )
        self.owner_uid = self.repository.create_account(
            "owner@example.test", "Owner", "test-hash", "owner"
        )
        self.renter_uid = self.repository.create_account(
            "renter@example.test", "Renter", "test-hash", "renter"
        )
        self.broker_uid = self.repository.create_account(
            "broker@example.test", "Broker", "test-hash", "broker"
        )
        self.admin_token = self.repository.issue_session(self.admin_uid)
        self.owner_token = self.repository.issue_session(self.owner_uid)
        self.renter_token = self.repository.issue_session(self.renter_uid)

    def tearDown(self):
        self.database_patch.stop()
        self.temp_dir.cleanup()

    def test_listing_submission_requires_review_before_publication(self):
        owner = CurrentUser(self.owner_uid, "owner", {"email": "owner@example.test"})
        admin = CurrentUser(self.admin_uid, "admin", {"email": "admin@example.test"})
        service = ApplicationService(self.repository)
        submitted = service.create_record(owner, "listing", {
            "title": "Test apartment", "city": "Pune", "locality": "Aundh",
            "property_type": "Apartment", "rent": 24000, "bedrooms": 2,
            "status": "published",
        })
        self.assertEqual(submitted["data"]["status"], "pending")
        requests = self.repository.list_approval_requests("pending")
        self.assertEqual(len(requests), 1)
        service.review_property(admin, submitted["id"], "approved", "Verified")
        self.assertEqual(self.repository.get("application_records", submitted["id"])["data"]["status"], "published")
        self.assertEqual(self.repository.list_approval_requests("pending"), [])
        with self.assertRaises(HTTPException):
            service.review_property(admin, submitted["id"], "rejected", "Duplicate review")

    def test_account_views_exclude_password_hash_and_protect_last_admin(self):
        second_admin = self.repository.create_account(
            "admin2@example.test", "Second Admin", "test-hash", "admin"
        )
        users = self.repository.list_user_roles()
        self.assertEqual(len(users), 4)
        self.assertNotIn("broker", {user["role"] for user in users})
        self.assertIsNotNone(self.repository.account_by_uid(self.broker_uid))
        self.assertNotIn("password_hash", users[0])
        self.repository.set_account_status(self.admin_uid, second_admin, "deactivated")
        with self.assertRaises(ValueError):
            self.repository.set_account_status(self.admin_uid, self.admin_uid, "deactivated")
        self.repository.set_account_status(self.admin_uid, second_admin, "active")

    def test_deactivated_account_tokens_are_rejected_by_sqlite_session_lookup(self):
        renter_token = self.repository.issue_session(self.renter_uid)
        self.assertIsNotNone(self.repository.user_for_token(renter_token))
        self.repository.set_account_status(self.admin_uid, self.renter_uid, "deactivated")
        self.assertIsNone(self.repository.user_for_token(renter_token))

    def test_owner_listing_endpoint_and_listing_authorization(self):
        from backend.main import app

        listing_id, _ = self.repository.create("application_records", {
            "owner_uid": self.owner_uid,
            "kind": "listing",
            "data": {"title": "Published home", "status": "published"},
        })
        draft_id, _ = self.repository.create("application_records", {
            "owner_uid": self.owner_uid,
            "kind": "listing",
            "data": {"title": "Draft home", "status": "draft"},
        })
        other_owner_uid = self.repository.create_account(
            "other-owner@example.test", "Other Owner", "test-hash", "owner"
        )
        other_listing_id, _ = self.repository.create("application_records", {
            "owner_uid": other_owner_uid,
            "kind": "listing",
            "data": {"title": "Other home", "status": "published"},
        })
        owner = CurrentUser(self.owner_uid, "owner", {"email": "owner@example.test"})
        renter = CurrentUser(self.renter_uid, "renter", {"email": "renter@example.test"})
        service = ApplicationService(self.repository)
        self.assertEqual(service.get_record(owner, listing_id)["id"], listing_id)
        self.assertEqual(service.get_record(renter, listing_id)["id"], listing_id)
        with self.assertRaises(HTTPException) as hidden_draft:
            service.get_record(renter, draft_id)
        self.assertEqual(hidden_draft.exception.status_code, 403)
        with self.assertRaises(HTTPException) as hidden_owner_listing:
            service.get_record(owner, other_listing_id)
        self.assertEqual(hidden_owner_listing.exception.status_code, 403)

        response = TestClient(app).get(
            "/application/auth/records?kind=listing",
            headers={"Authorization": f"Bearer {self.owner_token}"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual({item["id"] for item in response.json()["records"]}, {listing_id, draft_id})

    def test_admin_api_is_role_protected_and_returns_dashboard_data(self):
        from backend.main import app

        client = TestClient(app)
        self.assertEqual(client.get("/application/admin/dashboard").status_code, 401)
        headers = {"Authorization": f"Bearer {self.admin_token}"}
        response = client.get("/application/admin/dashboard", headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["users_total"], 3)
        self.assertEqual(set(response.json()["users_by_role"]), {"admin", "owner", "renter"})
        user_response = client.get("/application/admin/users", headers=headers)
        self.assertEqual({user["role"] for user in user_response.json()["users"]}, {"admin", "owner", "renter"})
        self.assertEqual(
            client.post(
                "/application/records/a/broker-assignment",
                headers=headers,
                json={"broker_uid": self.broker_uid},
            ).status_code,
            404,
        )
        self.assertEqual(
            client.put(
                f"/application/admin/users/{self.broker_uid}/role",
                headers=headers,
                json={"role": "broker"},
            ).status_code,
            422,
        )
        self.assertEqual(
            client.put(
                f"/application/admin/users/{self.broker_uid}/role",
                headers=headers,
                json={"role": "owner"},
            ).status_code,
            404,
        )
        self.assertEqual(
            client.put(
                f"/application/admin/users/{self.broker_uid}/status",
                headers=headers,
                json={"status": "deactivated"},
            ).status_code,
            404,
        )
        self.assertEqual(self.repository.account_by_uid(self.broker_uid)["role"], "broker")
        self.assertEqual(self.repository.user_for_token(self.repository.issue_session(self.broker_uid))["role"], "broker")
        self.assertIn("dataset_properties", response.json())
        self.assertEqual(
            client.get("/application/admin/dashboard", headers={"Authorization": f"Bearer {self.owner_token}"}).status_code,
            403,
        )

    def test_support_ticket_is_persistent_and_admin_only_for_updates(self):
        ticket_id = self.repository.create_support_ticket(
            self.renter_uid, "Need help", "I need help with my enquiry", "normal"
        )
        self.assertEqual(len(self.repository.list_support_tickets(self.renter_uid)), 1)
        self.assertEqual(self.repository.list_support_tickets(self.admin_uid), [])
        self.repository.update_support_ticket(ticket_id, "resolved", "We have replied")
        ticket = self.repository.support_ticket_by_id(ticket_id)
        self.assertEqual(ticket["status"], "resolved")
        self.assertEqual(ticket["admin_response"], "We have replied")

    def test_app_reviews_persist_update_summarize_and_enforce_permissions(self):
        from backend.main import app

        client = TestClient(app)
        self.assertEqual(client.get("/application/auth/app-reviews").status_code, 401)
        owner_headers = {"Authorization": f"Bearer {self.owner_token}"}
        renter_headers = {"Authorization": f"Bearer {self.renter_token}"}
        admin_headers = {"Authorization": f"Bearer {self.admin_token}"}

        empty = client.get("/application/auth/app-reviews", headers=owner_headers)
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.json()["total_reviews"], 0)
        self.assertIsNone(empty.json()["average_rating"])
        self.assertEqual(empty.json()["reviews"], [])
        self.assertIsNone(empty.json()["own_review"])

        submitted = client.put(
            "/application/auth/app-reviews",
            headers=owner_headers,
            json={"rating": 5, "feedback": "RentAI is straightforward to use."},
        )
        self.assertEqual(submitted.status_code, 200)
        first_review_id = submitted.json()["review_id"]
        self.assertEqual(submitted.json()["review"]["user_uid"], self.owner_uid)
        self.assertEqual(submitted.json()["review"]["reviewer_name"], "Owner")
        self.assertEqual(submitted.json()["review"]["role"], "owner")
        self.assertTrue(submitted.json()["review"]["created_at"])

        # Resubmission edits the authenticated user's existing review.
        changed = client.put(
            "/application/auth/app-reviews",
            headers=owner_headers,
            json={"rating": 3, "feedback": "The application works well."},
        )
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(changed.json()["review_id"], first_review_id)
        self.assertEqual(len(self.repository.list_app_reviews()), 1)

        self.assertEqual(client.put(
            "/application/auth/app-reviews", headers=owner_headers,
            json={"rating": 6, "feedback": "Invalid rating"},
        ).status_code, 422)
        self.assertEqual(client.put(
            "/application/auth/app-reviews", headers=owner_headers,
            json={"rating": 4, "feedback": "   "},
        ).status_code, 422)

        renter_submit = client.put(
            "/application/auth/app-reviews", headers=renter_headers,
            json={"rating": 5, "feedback": "I like using RentAI."},
        )
        self.assertEqual(renter_submit.status_code, 200)
        owner_summary = client.get("/application/auth/app-reviews", headers=owner_headers).json()
        self.assertEqual(owner_summary["total_reviews"], 2)
        self.assertEqual(owner_summary["average_rating"], 4.0)
        self.assertEqual(owner_summary["own_review"]["user_uid"], self.owner_uid)

        # Owners cannot submit a review on behalf of another account.
        forbidden_identity = client.put(
            "/application/auth/app-reviews", headers=owner_headers,
            json={"rating": 3, "feedback": "The application works well.", "user_uid": self.renter_uid},
        )
        self.assertEqual(forbidden_identity.status_code, 422)
        self.assertEqual(
            client.get("/application/auth/app-reviews", headers=owner_headers).json()["own_review"]["user_uid"],
            self.owner_uid,
        )

        # Admin may read all Owner and Renter reviews, but cannot submit or modify them.
        admin_result = client.get("/application/auth/app-reviews", headers=admin_headers)
        self.assertEqual(admin_result.status_code, 200)
        self.assertEqual(admin_result.json()["total_reviews"], 2)
        self.assertEqual(len(admin_result.json()["reviews"]), 2)
        self.assertEqual({item["role"] for item in admin_result.json()["reviews"]}, {"owner", "renter"})
        owner_review = next(item for item in admin_result.json()["reviews"] if item["role"] == "owner")
        self.assertEqual(owner_review["reviewer_name"], "Owner")
        self.assertTrue(owner_review["created_at"])
        admin_submit = client.put(
            "/application/auth/app-reviews", headers=admin_headers,
            json={"rating": 5, "feedback": "Admins cannot submit."},
        )
        self.assertEqual(admin_submit.status_code, 403)
        admin_filtered = client.get(
            "/application/auth/app-reviews?rating=3&search=application&role=owner",
            headers=admin_headers,
        )
        self.assertEqual(admin_filtered.status_code, 200)
        self.assertEqual(len(admin_filtered.json()["reviews"]), 1)

        with patch.object(SQLiteRepository, "list_app_reviews", side_effect=sqlite3.OperationalError("offline")):
            failed_read = client.get("/application/auth/app-reviews", headers=admin_headers)
        self.assertEqual(failed_read.status_code, 503)
        self.assertEqual(failed_read.json()["detail"], "App reviews are temporarily unavailable")
        with patch.object(SQLiteRepository, "upsert_app_review", side_effect=sqlite3.OperationalError("offline")):
            failed_submit = client.put(
                "/application/auth/app-reviews", headers=owner_headers,
                json={"rating": 4, "feedback": "The save should fail safely."},
            )
        self.assertEqual(failed_submit.status_code, 503)
        self.assertEqual(failed_submit.json()["detail"], "App review could not be saved")

        # Brokers are authenticated but cannot access the three-role app-review feature.
        broker_token = self.repository.issue_session(self.broker_uid)
        broker_result = client.get(
            "/application/auth/app-reviews",
            headers={"Authorization": f"Bearer {broker_token}"},
        )
        self.assertEqual(broker_result.status_code, 403)

    def test_admin_queries_exclude_broker_data_without_deleting_it(self):
        broker_listing_id, _ = self.repository.create("application_records", {
            "owner_uid": self.broker_uid,
            "kind": "listing",
            "data": {"title": "Historical Broker listing", "status": "pending"},
        })
        owner_listing_id, _ = self.repository.create("application_records", {
            "owner_uid": self.owner_uid,
            "kind": "listing",
            "data": {"title": "Owner listing", "status": "pending"},
        })
        self.repository.create_approval_request(broker_listing_id, self.broker_uid)
        self.repository.create_approval_request(owner_listing_id, self.owner_uid)
        self.repository.create_support_ticket(
            self.broker_uid, "Broker history", "Historical support record", "normal"
        )
        self.repository.create_support_ticket(
            self.renter_uid, "Renter help", "Renter support record", "normal"
        )

        self.assertEqual(self.repository.admin_dashboard_stats()["users_total"], 3)
        self.assertEqual(self.repository.admin_dashboard_stats()["listings_total"], 1)
        self.assertEqual(self.repository.admin_dashboard_stats()["support_open"], 1)
        self.assertEqual(
            [record["id"] for record in self.repository.list_admin_records("listing")],
            [owner_listing_id],
        )
        self.assertEqual(
            [item["property_id"] for item in self.repository.list_approval_requests(exclude_broker=True)],
            [owner_listing_id],
        )
        self.assertEqual(len(self.repository.list_approval_requests()), 2)
        self.assertEqual(self.repository.get("application_records", broker_listing_id)["id"], broker_listing_id)
        self.assertEqual(
            {ticket["user_role"] for ticket in self.repository.list_support_tickets(None)},
            {"renter"},
        )

    def test_sign_out_revokes_the_server_session(self):
        from backend.main import app

        response = TestClient(app).post(
            "/application/auth/sign-out",
            headers={"Authorization": f"Bearer {self.admin_token}"},
        )
        self.assertEqual(response.status_code, 204)
        self.assertIsNone(self.repository.user_for_token(self.admin_token))

    def test_owner_support_renderer_accepts_the_workspace_dataset_argument(self):
        support_app = AppTest.from_string(
            "import pandas as pd\n"
            "from frontend.pages.admin_reports import render_user_support\n"
            "render_user_support(pd.DataFrame())",
            default_timeout=30,
        ).run()
        self.assertFalse(support_app.exception)

    def test_admin_broker_owner_and_renter_navigation_survives_reruns(self):
        admin_app = AppTest.from_string(
            "import pandas as pd, streamlit as st\n"
            "from frontend.components.sidebar import render_sidebar\n"
            "st.session_state.page = 'Admin Dashboard'\n"
            "render_sidebar(pd.DataFrame(), role='admin', allowed_pages=['Admin Dashboard', 'User Management'])",
            default_timeout=30,
        ).run()
        admin_pages = (
            "Admin Dashboard", "User Management", "App Reviews",
            "Property Approval Requests", "Booking & Rental Requests",
            "Owner Management", "Renter Management", "Analytics & Reports",
            "ML Predictions", "Support & Complaints", "Activity Logs",
        )
        self.assertNotIn("Settings", [button.label for button in admin_app.button])
        self.assertNotIn("Property Management", [button.label for button in admin_app.button])
        for page in admin_pages:
            admin_app.button(key=f"admin_nav_{page}").click().run()
            self.assertFalse(admin_app.exception)
            self.assertEqual(admin_app.session_state["page"], page)
        self.assertNotIn("Broker Management", [button.label for button in admin_app.button])

        broker_app = AppTest.from_string(
            "import pandas as pd, streamlit as st\n"
            "from frontend.components.sidebar import render_sidebar\n"
            "st.session_state.page = 'Broker Dashboard'\n"
            "render_sidebar(pd.DataFrame(), role='broker', allowed_pages=['Broker Dashboard', 'Support & Complaints'])",
            default_timeout=30,
        ).run()
        broker_app.button(key="nav_Support & Complaints").click().run()
        self.assertFalse(broker_app.exception)
        self.assertEqual(broker_app.session_state["page"], "Support & Complaints")

        owner_app = AppTest.from_string(
            "from frontend.components.owner_sidebar import render_owner_sidebar\n"
            "render_owner_sidebar(on_logout=lambda: None)", default_timeout=30,
        ).run()
        owner_app.button(key="owner_nav_Support & Complaints").click().run()
        self.assertFalse(owner_app.exception)
        self.assertEqual(owner_app.session_state["owner_active_page"], "Support & Complaints")
        owner_app.button(key="owner_nav_App Reviews").click().run()
        self.assertEqual(owner_app.session_state["owner_active_page"], "App Reviews")

        renter_app = AppTest.from_string(
            "from frontend.components.renter_sidebar import render_renter_sidebar\n"
            "render_renter_sidebar(on_logout=lambda: None)", default_timeout=30,
        ).run()
        renter_app.button(key="renter_nav_Support & Complaints").click().run()
        self.assertFalse(renter_app.exception)
        self.assertEqual(renter_app.session_state["renter_active_page"], "Support & Complaints")
        renter_app.button(key="renter_nav_App Reviews").click().run()
        self.assertEqual(renter_app.session_state["renter_active_page"], "App Reviews")

    def test_app_reviews_page_is_read_only_for_admin_and_shows_empty_state(self):
        admin_app = AppTest.from_string(
            "import streamlit as st\n"
            "from frontend.pages import app_reviews as page\n"
            "st.session_state.rentai_role = 'admin'\n"
            "page._load_reviews = lambda **kwargs: {'total_reviews': 0, 'average_rating': None, 'reviews': [], 'own_review': None}\n"
            "page.render()",
            default_timeout=30,
        ).run()
        self.assertFalse(admin_app.exception)
        self.assertNotIn("Submit Review", [button.label for button in admin_app.button])
        self.assertTrue(any("There are no app reviews" in item.value for item in admin_app.info))

        owner_app = AppTest.from_string(
            "import streamlit as st\n"
            "from frontend.pages import app_reviews as page\n"
            "st.session_state.rentai_role = 'owner'\n"
            "page._load_reviews = lambda **kwargs: {'total_reviews': 0, 'average_rating': None, 'reviews': [], 'own_review': None}\n"
            "page.render()",
            default_timeout=30,
        ).run()
        self.assertFalse(owner_app.exception)
        self.assertIn("Submit Review", [button.label for button in owner_app.button])


if __name__ == "__main__":
    unittest.main()
