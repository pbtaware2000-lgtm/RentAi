"""Booking and Razorpay reconciliation tests; gateway calls are always mocked."""

import json
import hashlib
import hmac
import os
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from backend.repositories import sqlite_repository
from backend.services import booking_service
from backend.services.booking_service import BookingService


class BookingWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(sqlite_repository, "DB_PATH", Path(self.temp_dir.name) / "bookings.sqlite3")
        self.db_patch.start()
        sqlite_repository.initialize_database()
        self.owner = SimpleNamespace(uid="owner-1", role="owner")
        self.renter = SimpleNamespace(uid="renter-1", role="renter")
        self.other_renter = SimpleNamespace(uid="renter-2", role="renter")
        self.payment_env = patch.dict(os.environ, {
            "RAZORPAY_KEY_ID": "rzp_test_booking",
            "RAZORPAY_KEY_SECRET": "razorpay-test-secret",
            "RAZORPAY_WEBHOOK_SECRET": "webhook-test-secret",
        })
        self.payment_env.start()
        with sqlite_repository.connect() as db:
            for uid, email, name, role in (("owner-1", "owner@example.test", "Owner", "owner"),
                                           ("renter-1", "renter@example.test", "Renter", "renter"),
                                           ("renter-2", "renter2@example.test", "Other Renter", "renter")):
                db.execute("INSERT INTO users(uid,email,name,password_hash,role) VALUES(?,?,?,?,?)", (uid, email, name, "unused", role))
            db.execute("INSERT INTO application_records(id,owner_uid,kind,data) VALUES(?,?,?,?)",
                       ("home-1", "owner-1", "listing", json.dumps({"title": "Real home", "city": "Pune", "locality": "Aundh", "status": "published", "occupancy_status": "vacant", "rent": 25000, "security_deposit": 50000})))
        self.service = BookingService()

    def tearDown(self):
        self.payment_env.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def request(self, renter=None):
        from datetime import date, timedelta
        day = (date.today() + timedelta(days=30)).isoformat()
        return self.service.create_request(renter or self.renter, "home-1", day)

    def test_only_real_published_vacant_owner_listing_can_be_requested(self):
        with self.assertRaises(HTTPException) as missing:
            self.service.create_request(self.renter, "CSV-REFERENCE-1", "2030-01-01")
        self.assertEqual(missing.exception.status_code, 404)
        self.assertEqual(self.request()["property"]["title"], "Real home")

    def test_booking_terms_and_owner_approval_keep_payment_unpaid(self):
        request = self.request()
        self.assertEqual(request["amount_due"], 7_500_000)
        self.assertIn("requested", self.service.list_notifications("owner-1")[0]["message"])
        with self.assertRaises(HTTPException) as wrong_owner:
            self.service.decide(SimpleNamespace(uid="other-owner", role="owner"), request["id"], "accept")
        self.assertEqual(wrong_owner.exception.status_code, 403)
        accepted = self.service.decide(self.owner, request["id"], "accept")
        self.assertEqual(accepted["status"], "accepted_pending_payment")
        self.assertEqual(accepted["payment_status"], "pending")
        self.assertIn("accepted", self.service.list_notifications("renter-1")[0]["message"])

    def test_renter_listing_is_scoped_and_payment_requires_approval(self):
        request = self.request()
        self.assertEqual([item["id"] for item in self.service.list_for_renter("renter-1")], [request["id"]])
        self.assertEqual(self.service.list_for_renter("renter-2"), [])
        self.assertEqual(self.service.list_for_owner("other-owner"), [])
        with self.assertRaises(HTTPException) as early:
            self.service.create_checkout(self.renter, request["id"])
        self.assertEqual(early.exception.status_code, 409)
        with self.assertRaises(HTTPException) as wrong_renter:
            self.service.create_checkout(self.other_renter, request["id"])
        self.assertEqual(wrong_renter.exception.status_code, 404)

    def test_owner_can_reject_and_rejected_request_cannot_be_paid(self):
        request = self.request()
        rejected = self.service.decide(self.owner, request["id"], "reject")
        self.assertEqual(rejected["status"], "rejected")
        with self.assertRaises(HTTPException):
            self.service.create_checkout(self.renter, request["id"])

    def test_only_one_of_two_renter_requests_can_be_accepted_concurrently(self):
        one = self.request(self.renter)
        two = self.request(self.other_renter)
        barrier = threading.Barrier(2)
        results = []

        def accept(booking_id):
            barrier.wait()
            try:
                results.append(self.service.decide(self.owner, booking_id, "accept")["status"])
            except HTTPException as exc:
                results.append(exc.status_code)

        threads = [threading.Thread(target=accept, args=(booking_id,)) for booking_id in (one["id"], two["id"])]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertCountEqual(results, ["accepted_pending_payment", 409])

    def test_invalid_signature_is_rejected(self):
        with self.assertRaises(HTTPException) as invalid:
            self.service.verify_webhook(b'{"event":"order.paid"}', "bad-signature")
        self.assertEqual(invalid.exception.status_code, 400)

    def test_webhook_signature_is_hmac_checked(self):
        body = b'{"event":"payment.failed","payload":{}}'
        signature = hmac.new(b"webhook-test-secret", body, hashlib.sha256).hexdigest()
        self.assertEqual(self.service.verify_webhook(body, signature)["event"], "payment.failed")

    def test_verified_payment_confirms_once_and_updates_availability(self):
        request = self.request()
        self.service.decide(self.owner, request["id"], "accept")
        with patch.object(booking_service, "_gateway", return_value={
            "id": "order_booking123", "amount": 7_500_000, "currency": "INR",
        }):
            checkout = self.service.create_checkout(self.renter, request["id"])
        self.assertEqual((checkout["payment_status"], checkout["amount"]), ("processing", 7_500_000))
        event = {"event": "payment.captured", "payload": {
                "payment": {"entity": {"id": "pay_booking1", "order_id": "order_booking123"}},
            }}
        with patch.object(booking_service, "_gateway", side_effect=[
            {"id": "order_booking123", "status": "paid", "amount": 7_500_000, "amount_paid": 7_500_000, "currency": "INR"},
            {"id": "pay_booking1", "order_id": "order_booking123", "status": "captured", "captured": True, "amount": 7_500_000, "currency": "INR"},
        ]):
            self.assertEqual(self.service.process_webhook("event-1", event), {"received": True})
        self.assertEqual(self.service.process_webhook("event-1", event), {"received": True, "duplicate": True})
        booking = self.service.list_for_renter("renter-1")[0]
        self.assertEqual(booking["status"], "confirmed")
        self.assertEqual(booking["payment_status"], "paid")
        self.assertEqual(booking["latest_payment"]["transaction_id"], "pay_booking1")
        self.assertIn("confirmed", self.service.list_notifications("renter-1")[0]["message"])
        listing = sqlite_repository.SQLiteRepository().get("application_records", "home-1")
        self.assertEqual(listing["data"]["occupancy_status"], "occupied")

    def test_payment_that_fails_independent_verification_never_confirms(self):
        request = self.request()
        self.service.decide(self.owner, request["id"], "accept")
        with patch.object(booking_service, "_gateway", return_value={"id": "order_bookingbad", "amount": 7_500_000, "currency": "INR"}):
            self.service.create_checkout(self.renter, request["id"])
        with patch.object(booking_service, "_gateway", side_effect=[
            {"id": "order_bookingbad", "status": "attempted", "amount": 7_500_000, "amount_paid": 0, "currency": "INR"},
            {"id": "pay_bookingbad", "order_id": "order_bookingbad", "status": "authorized", "captured": False, "amount": 7_500_000, "currency": "INR"},
        ]):
            with self.assertRaises(HTTPException):
                self.service.process_webhook("event-unverified", {"event": "payment.captured", "payload": {
                    "payment": {"entity": {"id": "pay_bookingbad", "order_id": "order_bookingbad"}},
                }})
        booking = self.service.list_for_renter("renter-1")[0]
        self.assertEqual((booking["status"], booking["payment_status"]), ("accepted_pending_payment", "processing"))

    def test_captured_payment_is_preserved_if_listing_becomes_unavailable(self):
        request = self.request()
        self.service.decide(self.owner, request["id"], "accept")
        with patch.object(booking_service, "_gateway", return_value={"id": "order_conflict", "amount": 7_500_000, "currency": "INR"}):
            self.service.create_checkout(self.renter, request["id"])
        with sqlite_repository.connect() as db:
            db.execute("UPDATE application_records SET data=? WHERE id=?", (
                json.dumps({"title": "Real home", "city": "Pune", "locality": "Aundh", "status": "published", "occupancy_status": "occupied", "rent": 25000, "security_deposit": 50000}),
                "home-1",
            ))
        with patch.object(booking_service, "_gateway", side_effect=[
            {"id": "order_conflict", "status": "paid", "amount": 7_500_000, "amount_paid": 7_500_000, "currency": "INR"},
            {"id": "pay_conflict", "order_id": "order_conflict", "status": "captured", "captured": True, "amount": 7_500_000, "currency": "INR"},
        ]):
            self.service.process_webhook("event-conflict", {"event": "payment.captured", "payload": {
                "payment": {"entity": {"id": "pay_conflict", "order_id": "order_conflict"}},
            }})
        booking = self.service.list_for_renter("renter-1")[0]
        self.assertEqual((booking["status"], booking["payment_status"], booking["availability_conflict"]),
                         ("accepted_pending_payment", "paid", 1))
        self.assertEqual(booking["latest_payment"]["transaction_id"], "pay_conflict")

    def test_signed_payment_failure_is_retryable_and_does_not_confirm(self):
        request = self.request()
        self.service.decide(self.owner, request["id"], "accept")
        with patch.object(booking_service, "_gateway", return_value={"id": "order_fail123", "amount": 7_500_000, "currency": "INR"}):
            self.service.create_checkout(self.renter, request["id"])
        self.service.process_webhook("event-fail", {"event": "payment.failed", "payload": {
            "payment": {"entity": {"id": "pay_fail123", "order_id": "order_fail123", "amount": 7_500_000, "currency": "INR"}}
        }})
        booking = self.service.list_for_renter("renter-1")[0]
        self.assertEqual((booking["status"], booking["payment_status"]), ("accepted_pending_payment", "failed"))
        with patch.object(booking_service, "_gateway") as gateway:
            retried = self.service.create_checkout(self.renter, request["id"])
        self.assertEqual(retried["payment_status"], "processing")
        self.assertEqual(retried["order_id"], "order_fail123")
        gateway.assert_not_called()

    def test_existing_payment_link_attempts_remain_webhook_verifiable(self):
        request = self.request()
        self.service.decide(self.owner, request["id"], "accept")
        with patch.object(booking_service, "_gateway", return_value={"id": "order_legacy123", "amount": 7_500_000, "currency": "INR"}):
            self.service.create_checkout(self.renter, request["id"])
        with sqlite_repository.connect() as db:
            db.execute("UPDATE booking_payments SET razorpay_order_id=NULL,payment_link_id=?,checkout_url=? WHERE booking_id=?",
                       ("plink_legacy123", "https://rzp.test/legacy", request["id"]))
        event = {"event": "payment_link.paid", "payload": {
            "payment_link": {"entity": {"id": "plink_legacy123"}},
            "payment": {"entity": {"id": "pay_legacy123", "payment_link_id": "plink_legacy123"}},
        }}
        with patch.object(booking_service, "_gateway", side_effect=[
            {"id": "plink_legacy123", "status": "paid", "amount": 7_500_000, "currency": "INR",
             "payments": [{"payment_id": "pay_legacy123", "status": "captured"}]},
            {"id": "pay_legacy123", "payment_link_id": "plink_legacy123", "status": "captured",
             "captured": True, "amount": 7_500_000, "currency": "INR"},
        ]):
            self.assertEqual(self.service.process_webhook("legacy-event", event), {"received": True})
        self.assertEqual(self.service.list_for_renter("renter-1")[0]["status"], "confirmed")

    def test_payment_schema_migration_preserves_existing_payment_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.sqlite3"
            with sqlite3.connect(path) as db:
                db.execute("""CREATE TABLE booking_payments (
                    id TEXT PRIMARY KEY, booking_id TEXT NOT NULL, provider TEXT NOT NULL DEFAULT 'razorpay',
                    payment_link_id TEXT UNIQUE, transaction_id TEXT UNIQUE, amount INTEGER NOT NULL,
                    currency TEXT NOT NULL DEFAULT 'INR', status TEXT NOT NULL, checkout_url TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                )""")
                db.execute("INSERT INTO booking_payments(id,booking_id,amount,status,created_at,updated_at) VALUES('legacy-row','booking-old',150000,'processing','2026-01-01','2026-01-01')")
            db.close()
            with patch.object(sqlite_repository, "DB_PATH", path):
                sqlite_repository.initialize_database()
                with sqlite_repository.connect() as db:
                    columns = {row["name"] for row in db.execute("PRAGMA table_info(booking_payments)")}
                    saved = db.execute("SELECT id,amount,status FROM booking_payments WHERE id='legacy-row'").fetchone()
            self.assertIn("razorpay_order_id", columns)
            self.assertEqual(tuple(saved), ("legacy-row", 150000, "processing"))

    def test_checkout_signature_and_provider_capture_are_both_required(self):
        request = self.request()
        self.service.decide(self.owner, request["id"], "accept")
        with patch.object(booking_service, "_gateway", return_value={"id": "order_verify123", "amount": 7_500_000, "currency": "INR"}):
            self.service.create_checkout(self.renter, request["id"])
        payment_id = "pay_verify123"
        signature = hmac.new(b"razorpay-test-secret", f"order_verify123|{payment_id}".encode(), hashlib.sha256).hexdigest()
        provider_results = [
            {"id": "order_verify123", "status": "paid", "amount": 7_500_000, "amount_paid": 7_500_000, "currency": "INR"},
            {"id": payment_id, "order_id": "order_verify123", "status": "captured", "captured": True, "amount": 7_500_000, "currency": "INR"},
        ]
        with patch.object(booking_service, "_gateway", side_effect=provider_results):
            self.assertEqual(self.service.verify_payment(self.renter, request["id"], payment_id, "order_verify123", signature)["payment_status"], "paid")
        self.assertEqual(self.service.list_for_renter("renter-1")[0]["status"], "confirmed")

    def test_invalid_checkout_signature_and_other_renter_are_rejected(self):
        request = self.request()
        self.service.decide(self.owner, request["id"], "accept")
        with patch.object(booking_service, "_gateway", return_value={"id": "order_secure123", "amount": 7_500_000, "currency": "INR"}):
            self.service.create_checkout(self.renter, request["id"])
        with self.assertRaises(HTTPException) as unauthorized:
            self.service.verify_payment(self.other_renter, request["id"], "pay_secure123", "order_secure123", "a" * 64)
        self.assertEqual(unauthorized.exception.status_code, 404)
        with self.assertRaises(HTTPException) as invalid:
            self.service.verify_payment(self.renter, request["id"], "pay_secure123", "order_secure123", "a" * 64)
        self.assertEqual(invalid.exception.status_code, 400)

    def test_missing_credentials_block_order_creation(self):
        with patch.dict(os.environ, {}, clear=True), patch("backend.core.config.PROJECT_ROOT", Path(self.temp_dir.name)):
            request = self.request()
            self.service.decide(self.owner, request["id"], "accept")
            with self.assertRaises(HTTPException) as missing:
                self.service.create_checkout(self.renter, request["id"])
        self.assertEqual(missing.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
