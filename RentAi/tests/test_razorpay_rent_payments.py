"""Razorpay rent payments are authenticated, server-priced, and test-key only."""

import hashlib
import hmac
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.core.auth import register_account
from backend.main import app
from backend.repositories import sqlite_repository


class RazorpayRentPaymentTests(unittest.TestCase):
    key_id = "rzp_test_rentai_key"
    key_secret = "test-only-rzp-secret"

    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_directory.cleanup)
        path_patch = patch.object(sqlite_repository, "DB_PATH", Path(self.temp_directory.name) / "rent-payments.sqlite3")
        path_patch.start()
        self.addCleanup(path_patch.stop)
        sqlite_repository.initialize_database()
        self.owner_uid, self.owner_token = register_account(
            "rent-owner@example.test", "test-owner-password", "Rent Owner", "owner"
        )
        self.renter_uid, self.renter_token = register_account(
            "rent-renter@example.test", "test-renter-password", "Rent Renter", "renter"
        )
        _other_uid, self.other_renter_token = register_account(
            "other-renter@example.test", "test-other-password", "Other Renter", "renter"
        )
        self.client = TestClient(app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.owner_headers = {"Authorization": f"Bearer {self.owner_token}"}
        self.renter_headers = {"Authorization": f"Bearer {self.renter_token}"}
        self.other_renter_headers = {"Authorization": f"Bearer {self.other_renter_token}"}
        listing = self.client.post("/application/records", headers=self.owner_headers, json={
            "kind": "listing", "data": {
                "title": "Payment Test Home", "city": "Pune", "locality": "Aundh",
                "property_type": "Apartment", "rent": 30000, "bedrooms": 2,
                "status": "published",
            },
        })
        self.assertEqual(listing.status_code, 201, listing.text)
        inquiry = self.client.post("/application/records", headers=self.renter_headers, json={
            "kind": "inquiry", "data": {
                "listing_id": listing.json()["id"], "client_name": "Rent Renter",
                "client_phone": "555-0100", "message": "Interested",
            },
        })
        self.assertEqual(inquiry.status_code, 201, inquiry.text)
        accepted = self.client.put(
            f"/application/records/{inquiry.json()['id']}", headers=self.owner_headers,
            json={"kind": "inquiry", "data": {**inquiry.json()["data"], "status": "accepted"}},
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)
        tenancy = self.client.post("/application/records", headers=self.owner_headers, json={
            "kind": "tenancy", "data": {
                "inquiry_id": inquiry.json()["id"], "start_date": date.today().isoformat(),
                "monthly_rent": 30000, "due_day": 5,
            },
        })
        self.assertEqual(tenancy.status_code, 201, tenancy.text)
        rent = self.client.post("/application/records", headers=self.owner_headers, json={
            "kind": "rent_payment", "data": {
                "tenancy_id": tenancy.json()["id"], "period": date.today().strftime("%Y-%m"),
            },
        })
        self.assertEqual(rent.status_code, 201, rent.text)
        self.rent_record = rent.json()

    def test_only_linked_renter_can_create_an_order_at_saved_amount(self):
        with patch.dict("os.environ", {
            "RAZORPAY_KEY_ID": self.key_id, "RAZORPAY_KEY_SECRET": self.key_secret,
        }), patch("backend.services.razorpay_service._provider_request", return_value={
            "id": "order_test123", "amount": 3000000, "currency": "INR",
        }) as provider:
            denied = self.client.post(
                f"/application/payments/razorpay/rent/{self.rent_record['id']}/order",
                headers=self.other_renter_headers,
            )
            self.assertEqual(denied.status_code, 404)
            denied_owner = self.client.post(
                f"/application/payments/razorpay/rent/{self.rent_record['id']}/order",
                headers=self.owner_headers,
            )
            self.assertEqual(denied_owner.status_code, 403)
            order = self.client.post(
                f"/application/payments/razorpay/rent/{self.rent_record['id']}/order",
                headers=self.renter_headers,
                json={"amount": 1},
            )
            self.assertEqual(order.status_code, 200, order.text)
            body = order.json()
            self.assertEqual(body["key_id"], self.key_id)
            self.assertEqual(body["order_id"], "order_test123")
            self.assertEqual(body["amount"], 3000000)
            self.assertEqual(body["currency"], "INR")
            self.assertNotIn(self.key_secret, order.text)
            self.assertEqual(provider.call_args.args[:3], ("POST", "/orders", self.key_id))
            self.assertEqual(provider.call_args.args[3], self.key_secret)
            self.assertEqual(provider.call_args.args[4]["amount"], 3000000)

            listed = self.client.get("/application/auth/records?kind=rent_payment", headers=self.renter_headers)
            self.assertEqual(listed.status_code, 200)
            saved = next(item for item in listed.json()["records"] if item["id"] == self.rent_record["id"])
            self.assertEqual(saved["data"]["razorpay_order"]["id"], "order_test123")
            other_list = self.client.get("/application/auth/records?kind=rent_payment", headers=self.other_renter_headers)
            self.assertEqual(other_list.json()["records"], [])

    def test_signature_and_provider_capture_are_required_before_marking_paid(self):
        payment_id = "pay_test123"
        order_id = "order_test123"
        signature = hmac.new(
            self.key_secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256
        ).hexdigest()
        order_response = {"id": order_id, "amount": 3000000, "amount_paid": 3000000, "currency": "INR", "status": "paid"}
        payment_response = {
            "id": payment_id, "order_id": order_id, "amount": 3000000,
            "currency": "INR", "status": "captured", "captured": True, "method": "upi",
        }
        with patch.dict("os.environ", {
            "RAZORPAY_KEY_ID": self.key_id, "RAZORPAY_KEY_SECRET": self.key_secret,
        }), patch("backend.services.razorpay_service._provider_request", side_effect=[
            {"id": order_id, "amount": 3000000, "currency": "INR"},
            order_response, payment_response,
        ]) as provider:
            order = self.client.post(
                f"/application/payments/razorpay/rent/{self.rent_record['id']}/order",
                headers=self.renter_headers,
            )
            self.assertEqual(order.status_code, 200, order.text)
            pending_details = self.client.get(
                f"/application/records/{self.rent_record['id']}", headers=self.owner_headers,
            ).json()["data"]
            owner_override = self.client.put(
                f"/application/records/{self.rent_record['id']}", headers=self.owner_headers,
                json={"kind": "rent_payment", "data": {
                    **pending_details, "status": "paid", "paid_date": date.today().isoformat(),
                    "paid_amount": 30000, "payment_method": "UPI",
                }},
            )
            self.assertEqual(owner_override.status_code, 409, owner_override.text)
            invalid = self.client.post("/application/payments/razorpay/verify", headers=self.renter_headers, json={
                "razorpay_payment_id": payment_id, "razorpay_order_id": order_id,
                "razorpay_signature": "0" * 64,
            })
            self.assertEqual(invalid.status_code, 400)
            self.assertEqual(provider.call_count, 1)
            pending = self.client.get(
                f"/application/records/{self.rent_record['id']}", headers=self.renter_headers,
            )
            self.assertEqual(pending.status_code, 200)
            self.assertEqual(pending.json()["data"]["status"], "pending")

            verified = self.client.post("/application/payments/razorpay/verify", headers=self.renter_headers, json={
                "razorpay_payment_id": payment_id, "razorpay_order_id": order_id,
                "razorpay_signature": signature,
            })
            self.assertEqual(verified.status_code, 200, verified.text)
            self.assertEqual(verified.json()["status"], "paid")
            saved = self.client.get(
                f"/application/records/{self.rent_record['id']}", headers=self.renter_headers,
            ).json()["data"]
            self.assertEqual(saved["status"], "paid")
            self.assertEqual(saved["payment_provider"], "razorpay_test")
            self.assertEqual(saved["razorpay_payment_id"], payment_id)
            self.assertEqual(saved["paid_amount"], 30000)
            self.assertEqual(provider.call_count, 3)

    def test_live_key_is_rejected_without_calling_provider(self):
        with patch.dict("os.environ", {
            "RAZORPAY_KEY_ID": "rzp_live_do_not_use", "RAZORPAY_KEY_SECRET": self.key_secret,
        }), patch("backend.services.razorpay_service._provider_request") as provider:
            response = self.client.post(
                f"/application/payments/razorpay/rent/{self.rent_record['id']}/order",
                headers=self.renter_headers,
            )
            self.assertEqual(response.status_code, 503)
            self.assertNotIn(self.key_secret, response.text)
            provider.assert_not_called()

    def test_authorized_but_uncaptured_payment_does_not_mark_rent_paid(self):
        payment_id = "pay_pending123"
        order_id = "order_pending123"
        signature = hmac.new(
            self.key_secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256
        ).hexdigest()
        provider_responses = [
            {"id": order_id, "amount": 3000000, "currency": "INR"},
            {"id": order_id, "amount": 3000000, "amount_paid": 0, "currency": "INR", "status": "attempted"},
            {"id": payment_id, "order_id": order_id, "amount": 3000000, "currency": "INR", "status": "authorized", "captured": False},
        ]
        with patch.dict("os.environ", {
            "RAZORPAY_KEY_ID": self.key_id, "RAZORPAY_KEY_SECRET": self.key_secret,
        }), patch("backend.services.razorpay_service._provider_request", side_effect=provider_responses):
            created = self.client.post(
                f"/application/payments/razorpay/rent/{self.rent_record['id']}/order",
                headers=self.renter_headers,
            )
            self.assertEqual(created.status_code, 200, created.text)
            response = self.client.post("/application/payments/razorpay/verify", headers=self.renter_headers, json={
                "razorpay_payment_id": payment_id, "razorpay_order_id": order_id,
                "razorpay_signature": signature,
            })
            self.assertEqual(response.status_code, 409, response.text)
            saved = self.client.get(
                f"/application/records/{self.rent_record['id']}", headers=self.renter_headers,
            ).json()["data"]
            self.assertEqual(saved["status"], "pending")
            self.assertNotIn("razorpay_payment_id", saved)


if __name__ == "__main__":
    unittest.main()
