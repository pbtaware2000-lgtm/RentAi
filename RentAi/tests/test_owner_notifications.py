"""Durable Owner email notifications and scheduled summary regressions."""

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.core.auth import register_account, sign_in
from backend.main import app
from backend.repositories import sqlite_repository
from backend.services.email_service import EmailConfigurationError, get_smtp_settings
from backend.services.notification_worker import (
    NotificationWorker,
    calculate_portfolio_summary,
    summary_period_due,
)


class FakeEmailTransport:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.messages = []

    def send(self, recipient, subject, body, *, event_id):
        if self.fail:
            raise OSError("test transport failure with hidden data")
        self.messages.append({
            "recipient": recipient, "subject": subject, "body": body, "event_id": event_id,
        })


class OwnerNotificationTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_directory.cleanup)
        patcher = patch.object(
            sqlite_repository, "DB_PATH", Path(self.temp_directory.name) / "notifications.sqlite3",
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        sqlite_repository.initialize_database()
        self.repository = sqlite_repository.SQLiteRepository()
        self.owner_uid, self.owner_token = register_account(
            "owner.one@example.test", "test-owner-password", "Owner One", "owner",
        )
        self.other_uid, self.other_token = register_account(
            "owner.two@example.test", "test-owner-password-two", "Owner Two", "owner",
        )
        self.renter_uid, self.renter_token = register_account(
            "renter.one@example.test", "test-renter-password", "Renter One", "renter",
        )
        self.client = TestClient(app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    @staticmethod
    def headers(token):
        return {"Authorization": f"Bearer {token}"}

    def set_settings(self, token, *, email=True, maintenance=True, frequency="off"):
        profiles = self.client.get(
            "/application/auth/records?kind=profile", headers=self.headers(token),
        ).json()["records"]
        setting = next((r for r in profiles if (r.get("data") or {}).get("record_type") == "owner_settings"), None)
        payload = {
            "kind": "profile",
            "data": {
                "record_type": "owner_settings", "email_alerts": email,
                "maintenance_alerts": maintenance, "summary_frequency": frequency,
            },
        }
        if setting:
            return self.client.put(
                f"/application/records/{setting['id']}", headers=self.headers(token), json=payload,
            )
        return self.client.post("/application/records", headers=self.headers(token), json=payload)

    def create_listing(self, token, title="Test Home"):
        response = self.client.post(
            "/application/records", headers=self.headers(token), json={
                "kind": "listing", "data": {
                    "title": title, "city": "Pune", "locality": "Aundh",
                    "property_type": "Apartment", "rent": 25000, "bedrooms": 2,
                    "occupancy_status": "vacant", "status": "published",
                },
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def create_enquiry(self, listing_id):
        return self.client.post(
            "/application/records", headers=self.headers(self.renter_token), json={
                "kind": "inquiry", "data": {
                    "listing_id": listing_id, "client_name": "Renter One",
                    "client_phone": "555-0100", "message": "Interested in viewing.",
                },
            },
        )

    def outbox(self, *, event_type=None):
        rows = self.repository.list_notifications()
        return [row for row in rows if event_type is None or row["event_type"] == event_type]

    def test_enquiry_notification_uses_actual_owner_and_retry_is_idempotent(self):
        self.set_settings(self.owner_token)
        listing = self.create_listing(self.owner_token)
        response = self.create_enquiry(listing["id"])
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(len(self.outbox(event_type="new_enquiry")), 1)

        retry = self.create_enquiry(listing["id"])
        self.assertEqual(retry.status_code, 409)
        self.assertEqual(len(self.outbox(event_type="new_enquiry")), 1)

        transport = FakeEmailTransport()
        worker = NotificationWorker(self.repository, transport)
        self.assertEqual(worker.run_once(), 1)
        self.assertEqual(transport.messages[0]["recipient"], "owner.one@example.test")
        self.assertIn("Renter One", transport.messages[0]["body"])
        self.assertIn("Applications & Enquiries", transport.messages[0]["body"])

    def test_enquiry_delivery_ignores_legacy_opt_out_and_isolates_recipients(self):
        self.set_settings(self.owner_token, email=False)
        self.set_settings(self.other_token, email=True)
        first_listing = self.create_listing(self.owner_token, "Owner One Home")
        other_listing = self.create_listing(self.other_token, "Owner Two Home")
        self.assertEqual(self.create_enquiry(first_listing["id"]).status_code, 201)
        self.assertEqual(len(self.outbox(event_type="new_enquiry")), 1)
        self.assertEqual(self.create_enquiry(other_listing["id"]).status_code, 201)
        self.assertEqual(len(self.outbox(event_type="new_enquiry")), 2)

        transport = FakeEmailTransport()
        NotificationWorker(self.repository, transport).run_once()
        self.assertEqual(
            {message["recipient"] for message in transport.messages},
            {"owner.one@example.test", "owner.two@example.test"},
        )
        self.assertEqual(
            {message["subject"] for message in transport.messages},
            {"New enquiry for Owner One Home", "New enquiry for Owner Two Home"},
        )

    def _create_maintenance_request(self):
        listing = self.create_listing(self.owner_token, "Maintenance Home")
        response = self.client.post(
            "/application/records", headers=self.headers(self.renter_token), json={
                "kind": "maintenance", "data": {
                    "listing_id": listing["id"], "title": "Leaking tap",
                    "description": "Kitchen tap is leaking.",
                },
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def _set_maintenance_status(self, record, status):
        return self.client.put(
            f"/application/records/{record['id']}", headers=self.headers(self.owner_token), json={
                "kind": "maintenance", "data": {"status": status, "owner_note": "Checked."},
            },
        )

    def test_maintenance_status_transition_notifies_once_and_unchanged_status_does_not(self):
        self.set_settings(self.owner_token, maintenance=True)
        request = self._create_maintenance_request()
        updated = self._set_maintenance_status(request, "in_progress")
        self.assertEqual(updated.status_code, 200, updated.text)
        unchanged = self._set_maintenance_status(request, "in_progress")
        self.assertEqual(unchanged.status_code, 200, unchanged.text)
        events = self.outbox(event_type="maintenance_status_changed")
        self.assertEqual(len(events), 1)

        transport = FakeEmailTransport()
        NotificationWorker(self.repository, transport).run_once()
        self.assertEqual(transport.messages[0]["recipient"], "owner.one@example.test")
        self.assertIn("open → in_progress", transport.messages[0]["body"])
        self.assertIn("Maintenance Home", transport.messages[0]["body"])

    def test_maintenance_delivery_ignores_legacy_opt_out_and_smtp_failure_does_not_break_operations(self):
        self.set_settings(self.owner_token, maintenance=False)
        request = self._create_maintenance_request()
        updated = self._set_maintenance_status(request, "completed")
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(len(self.outbox(event_type="maintenance_status_changed")), 1)

        self.set_settings(self.owner_token, email=True, maintenance=True)
        listing = self.create_listing(self.owner_token, "Failure Test Home")
        enquiry = self.create_enquiry(listing["id"])
        self.assertEqual(enquiry.status_code, 201, enquiry.text)
        event = self.outbox(event_type="new_enquiry")[0]
        transport = FakeEmailTransport(fail=True)
        worker = NotificationWorker(self.repository, transport)
        self.assertEqual(worker.run_once(), 0)
        queued = next(
            item for item in self.repository.list_notifications()
            if item["id"] == event["id"]
        )
        self.assertEqual(queued["status"], "pending")
        self.assertEqual(queued["last_error"], "SMTP delivery failed")
        self.assertNotIn("hidden data", queued["last_error"])
        self.assertEqual(self.repository.get("application_records", event["payload"]["record_id"])["kind"], "inquiry")

        maintenance = self._create_maintenance_request()
        changed = self._set_maintenance_status(maintenance, "in_progress")
        self.assertEqual(changed.status_code, 200, changed.text)
        worker.run_once(now=datetime.now(timezone.utc) + timedelta(seconds=120))
        saved_maintenance = self.repository.get("application_records", maintenance["id"])
        self.assertEqual(saved_maintenance["data"]["status"], "in_progress")
        transport.fail = False
        retry_time = datetime.now(timezone.utc) + timedelta(seconds=240)
        self.assertEqual(worker.run_once(now=retry_time), 3)
        sent_ids = {message["event_id"] for message in transport.messages}
        self.assertEqual(len(sent_ids), 3)
        self.assertTrue(all(item["status"] == "sent" for item in self.outbox()))
        self.assertEqual(worker.run_once(now=retry_time + timedelta(minutes=1)), 0)
        self.assertEqual(len(transport.messages), 3)

    def test_weekly_monthly_off_scheduling_restart_safety_and_real_metrics(self):
        self.set_settings(self.owner_token, frequency="weekly")
        self.set_settings(self.other_token, frequency="monthly")
        _off_uid, off_token = register_account(
            "owner.off@example.test", "test-owner-password-off", "Owner Off", "owner",
        )
        self.set_settings(off_token, frequency="off")
        listing = self.create_listing(self.owner_token, "Summary Home")
        occupied = {**listing, "id": "occupied-property", "data": {**listing["data"], "occupancy_status": "occupied"}}
        self.repository.create("application_records", {
            "owner_uid": self.owner_uid, "kind": "listing", "data": occupied["data"],
        })
        self.repository.create("application_records", {
            "owner_uid": self.owner_uid, "kind": "rent_payment",
            "data": {"status": "paid", "amount": 25000, "paid_amount": 25000, "period": "2026-10"},
        })
        self.repository.create("application_records", {
            "owner_uid": self.owner_uid, "kind": "rent_payment",
            "data": {"status": "pending", "amount": 25000, "period": "2026-11"},
        })

        monday = datetime(2026, 10, 19, 10, tzinfo=timezone.utc)
        self.assertEqual(summary_period_due("weekly", monday), "2026-W43")
        self.assertEqual(summary_period_due("monthly", monday), "2026-10")
        self.assertIsNone(summary_period_due("off", monday))
        summary = calculate_portfolio_summary(self.repository.list_records(), self.owner_uid, "2026-W43")
        self.assertEqual(summary["properties"], 2)
        self.assertEqual(summary["occupied"], 1)
        self.assertEqual(summary["vacant"], 1)
        self.assertEqual(summary["rent_collected"], 25000)
        self.assertEqual(summary["rent_outstanding"], 25000)

        first_worker = NotificationWorker(self.repository, FakeEmailTransport())
        self.assertEqual(first_worker.enqueue_due_summaries(monday), 2)
        restarted_worker = NotificationWorker(self.repository, FakeEmailTransport())
        self.assertEqual(restarted_worker.enqueue_due_summaries(monday), 0)
        events = self.outbox(event_type="portfolio_summary")
        self.assertEqual(len(events), 2)
        self.assertEqual({event["owner_uid"] for event in events}, {self.owner_uid, self.other_uid})
        self.assertEqual(events[0]["payload"]["properties"], 2 if events[0]["owner_uid"] == self.owner_uid else 0)

        transport = FakeEmailTransport()
        self.assertEqual(NotificationWorker(self.repository, transport).run_once(now=monday), 2)
        summary_message = next(message for message in transport.messages if "2026-W43" in message["subject"])
        self.assertIn("Rent collected: ₹25,000.00", summary_message["body"])
        self.assertIn("Outstanding rent: ₹25,000.00", summary_message["body"])

    def test_owner_settings_survive_fresh_authenticated_session(self):
        saved = self.set_settings(self.owner_token, email=False, maintenance=True, frequency="monthly")
        self.assertEqual(saved.status_code, 201, saved.text)
        sqlite_repository.SQLiteRepository().revoke_session(self.owner_token)
        _uid, new_token = sign_in("owner.one@example.test", "test-owner-password")
        response = self.client.get(
            "/application/auth/records?kind=profile", headers=self.headers(new_token),
        )
        settings = next(
            item["data"] for item in response.json()["records"]
            if item.get("data", {}).get("record_type") == "owner_settings"
        )
        self.assertFalse(settings["email_alerts"])
        self.assertTrue(settings["maintenance_alerts"])
        self.assertEqual(settings["summary_frequency"], "monthly")

    def test_missing_email_configuration_lists_names_without_values(self):
        names = (
            "RENTAI_EMAIL_SMTP_HOST", "RENTAI_EMAIL_SMTP_PORT",
            "RENTAI_EMAIL_SMTP_USERNAME", "RENTAI_EMAIL_SMTP_PASSWORD",
            "RENTAI_EMAIL_FROM",
        )
        with patch.dict(os.environ, {name: "" for name in names}), patch(
            "backend.services.email_service.dotenv_values", return_value={},
        ):
            with self.assertRaises(EmailConfigurationError) as caught:
                get_smtp_settings()
        message = str(caught.exception)
        for name in names:
            self.assertIn(name, message)
        self.assertNotIn("test-password", message)
        self.assertNotIn("test-secret", message)


if __name__ == "__main__":
    unittest.main()
