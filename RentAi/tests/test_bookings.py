"""Booking and Razorpay reconciliation tests; gateway calls are always mocked."""

import json
import hashlib
import hmac
import os
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
        with sqlite_repository.connect() as db:
            for uid, email, name, role in (("owner-1", "owner@example.test", "Owner", "owner"),
                                           ("renter-1", "renter@example.test", "Renter", "renter"),
                                           ("renter-2", "renter2@example.test", "Other Renter", "renter")):
                db.execute("INSERT INTO users(uid,email,name,password_hash,role) VALUES(?,?,?,?,?)", (uid, email, name, "unused", role))
            db.execute("INSERT INTO application_records(id,owner_uid,kind,data) VALUES(?,?,?,?)",
                       ("home-1", "owner-1", "listing", json.dumps({"title": "Real home", "city": "Pune", "locality": "Aundh", "status": "published", "occupancy_status": "vacant", "rent": 25000, "security_deposit": 50000})))
        self.service = BookingService()

    def tearDown(self):
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
        with patch.dict(os.environ, {"RAZORPAY_WEBHOOK_SECRET": "webhook-test-secret"}):
            with self.assertRaises(HTTPException) as invalid:
                self.service.verify_webhook(b'{"event":"payment_link.paid"}', "bad-signature")
        self.assertEqual(invalid.exception.status_code, 400)

    def test_webhook_signature_is_hmac_checked(self):
        body = b'{"event":"payment.failed"}'
        signature = hmac.new(b"webhook-test-secret", body, hashlib.sha256).hexdigest()
        with patch.dict(os.environ, {"RAZORPAY_WEBHOOK_SECRET": "webhook-test-secret"}):
            self.assertEqual(self.service.verify_webhook(body, signature)["event"], "payment.failed")

    def test_verified_payment_confirms_once_and_updates_availability(self):
        request = self.request()
        self.service.decide(self.owner, request["id"], "accept")
        with patch.object(booking_service, "_gateway", return_value={"id": "plink-1", "short_url": "https://rzp.test/pay"}):
            checkout = self.service.create_checkout(self.renter, request["id"])
        self.assertEqual(checkout["payment_status"], "processing")
        event = {"event": "payment_link.paid", "payload": {
                "payment_link": {"entity": {"id": "plink-1"}},
                "payment": {"entity": {"id": "pay-1", "payment_link_id": "plink-1"}},
            }}
        with patch.object(booking_service, "_gateway", side_effect=[
            {"id": "plink-1", "status": "paid", "amount": 7_500_000, "currency": "INR", "payments": [{"payment_id": "pay-1", "status": "captured", "amount": 7_500_000}]},
            {"id": "pay-1", "status": "captured", "payment_link_id": "plink-1", "amount": 7_500_000, "currency": "INR"},
        ]):
            self.assertEqual(self.service.process_webhook("event-1", event), {"received": True})
        self.assertEqual(self.service.process_webhook("event-1", event), {"received": True, "duplicate": True})
        booking = self.service.list_for_renter("renter-1")[0]
        self.assertEqual(booking["status"], "confirmed")
        self.assertEqual(booking["payment_status"], "paid")
        self.assertEqual(booking["latest_payment"]["transaction_id"], "pay-1")
        self.assertIn("confirmed", self.service.list_notifications("renter-1")[0]["message"])
        listing = sqlite_repository.SQLiteRepository().get("application_records", "home-1")
        self.assertEqual(listing["data"]["occupancy_status"], "occupied")

    def test_payment_that_fails_independent_verification_never_confirms(self):
        request = self.request()
        self.service.decide(self.owner, request["id"], "accept")
        with patch.object(booking_service, "_gateway", return_value={"id": "plink-bad", "short_url": "https://rzp.test/pay"}):
            self.service.create_checkout(self.renter, request["id"])
        with patch.object(booking_service, "_gateway", side_effect=[
            {"id": "plink-bad", "status": "paid", "amount": 7_500_000, "currency": "INR", "payments": []},
            {"id": "pay-bad", "status": "captured", "amount": 7_500_000, "currency": "INR"},
        ]):
            with self.assertRaises(HTTPException):
                self.service.process_webhook("event-unverified", {"event": "payment_link.paid", "payload": {
                    "payment_link": {"entity": {"id": "plink-bad"}},
                    "payment": {"entity": {"id": "pay-bad", "payment_link_id": "plink-bad"}},
                }})
        booking = self.service.list_for_renter("renter-1")[0]
        self.assertEqual((booking["status"], booking["payment_status"]), ("accepted_pending_payment", "processing"))

    def test_captured_payment_is_preserved_if_listing_becomes_unavailable(self):
        request = self.request()
        self.service.decide(self.owner, request["id"], "accept")
        with patch.object(booking_service, "_gateway", return_value={"id": "plink-conflict", "short_url": "https://rzp.test/pay"}):
            self.service.create_checkout(self.renter, request["id"])
        with sqlite_repository.connect() as db:
            db.execute("UPDATE application_records SET data=? WHERE id=?", (
                json.dumps({"title": "Real home", "city": "Pune", "locality": "Aundh", "status": "published", "occupancy_status": "occupied", "rent": 25000, "security_deposit": 50000}),
                "home-1",
            ))
        with patch.object(booking_service, "_gateway", side_effect=[
            {"id": "plink-conflict", "status": "paid", "amount": 7_500_000, "currency": "INR", "payments": [{"payment_id": "pay-conflict", "status": "captured", "amount": 7_500_000}]},
            {"id": "pay-conflict", "status": "captured", "amount": 7_500_000, "currency": "INR"},
        ]):
            self.service.process_webhook("event-conflict", {"event": "payment_link.paid", "payload": {
                "payment_link": {"entity": {"id": "plink-conflict"}},
                "payment": {"entity": {"id": "pay-conflict", "payment_link_id": "plink-conflict"}},
            }})
        booking = self.service.list_for_renter("renter-1")[0]
        self.assertEqual((booking["status"], booking["payment_status"], booking["availability_conflict"]),
                         ("accepted_pending_payment", "paid", 1))
        self.assertEqual(booking["latest_payment"]["transaction_id"], "pay-conflict")

    def test_signed_payment_failure_is_retryable_and_does_not_confirm(self):
        request = self.request()
        self.service.decide(self.owner, request["id"], "accept")
        with patch.object(booking_service, "_gateway", return_value={"id": "plink-fail", "short_url": "https://rzp.test/pay"}):
            self.service.create_checkout(self.renter, request["id"])
        self.service.process_webhook("event-fail", {"event": "payment.failed", "payload": {
            "payment": {"entity": {"id": "pay-fail", "payment_link_id": "plink-fail"}}
        }})
        booking = self.service.list_for_renter("renter-1")[0]
        self.assertEqual((booking["status"], booking["payment_status"]), ("accepted_pending_payment", "failed"))
        with patch.object(booking_service, "_gateway", side_effect=[{}, {"id": "plink-retry", "short_url": "https://rzp.test/retry"}]):
            self.assertEqual(self.service.create_checkout(self.renter, request["id"])["payment_status"], "processing")


if __name__ == "__main__":
    unittest.main()
