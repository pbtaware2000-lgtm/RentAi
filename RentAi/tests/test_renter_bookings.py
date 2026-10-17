"""Renter booking payment controls use server configuration and verified API results."""

import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


BOOKING = {
    "id": "booking-id-1234567890",
    "status": "accepted_pending_payment",
    "payment_status": "pending",
    "amount_due": 150000,
    "monthly_rent": 1500,
    "security_deposit": 0,
    "move_in_date": "2026-10-17",
    "property": {"title": "Cozy 1 BHK Flat", "property_type": "Apartment", "bedrooms": 1, "locality": "Kothrud", "city": "Pune"},
    "owner_name": "Owner",
    "owner_email": "owner@example.test",
    "latest_payment": {"status": "pending", "order_id": None, "transaction_id": None},
}


class RenterBookingPaymentUITests(unittest.TestCase):
    @staticmethod
    def _app():
        return AppTest.from_string(
            "from frontend.pages.renter_workspace import render_bookings\nrender_bookings()",
            default_timeout=30,
        )

    def _patch_booking_data(self, payment_config):
        return (
            patch("frontend.pages.renter_workspace.booking_records", return_value=[BOOKING]),
            patch("frontend.pages.renter_workspace.booking_payment_configuration", return_value=payment_config),
            patch("frontend.pages.renter_workspace.booking_notifications", return_value=[]),
        )

    def test_missing_credentials_show_safe_help_and_hide_pay_now(self):
        patches = self._patch_booking_data({"configured": False, "missing": ["RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET"]})
        with patches[0], patches[1], patches[2]:
            app = self._app().run()
        self.assertFalse(app.exception)
        self.assertFalse(any(button.label == "Pay Now" for button in app.button))
        self.assertTrue(any("RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET" in warning.value for warning in app.warning))
        self.assertTrue(any("Payment status: **Pending**" in item.value for item in app.caption))

    def test_pay_now_requests_order_only_for_positive_accepted_booking(self):
        checkout = {
            "booking_id": BOOKING["id"], "key_id": "rzp_test_public", "order_id": "order_booking123",
            "amount": 150000, "currency": "INR", "name": "RentAI", "description": "Initial payment",
        }
        patches = self._patch_booking_data({"configured": True, "missing": []})
        with patches[0], patches[1], patches[2], \
             patch("frontend.pages.renter_workspace.booking_checkout", return_value=checkout) as create_order, \
             patch("frontend.pages.renter_workspace.razorpay_checkout", return_value=None):
            app = self._app().run()
            app.button[0].click().run()
        self.assertFalse(app.exception)
        create_order.assert_called_once_with(BOOKING["id"])

    def test_pay_now_is_not_shown_for_pending_owner_decision_or_zero_amount(self):
        for changes in ({"status": "pending_owner_approval"}, {"amount_due": 0}):
            booking = {**BOOKING, **changes}
            patches = self._patch_booking_data({"configured": True, "missing": []})
            with patches[0], patches[1], patches[2], \
                 patch("frontend.pages.renter_workspace.booking_records", return_value=[booking]):
                app = self._app().run()
            self.assertFalse(app.exception)
            self.assertFalse(any(button.label == "Pay Now" for button in app.button))

    def test_cancelled_or_failed_checkout_never_marks_booking_paid(self):
        checkout = {
            "booking_id": BOOKING["id"], "key_id": "rzp_test_public", "order_id": "order_booking123",
            "amount": 150000, "currency": "INR", "name": "RentAI", "description": "Initial payment",
        }
        for outcome, expected in (("checkout_cancelled", "Checkout was cancelled"), ("checkout_failed", "Checkout was not completed")):
            with self.subTest(outcome=outcome):
                patches = self._patch_booking_data({"configured": True, "missing": []})
                app = self._app()
                app.session_state["rentai_booking_checkout"] = checkout
                with patches[0], patches[1], patches[2], patch(
                    "frontend.pages.renter_workspace.razorpay_checkout",
                    side_effect=[{"status": outcome}, None],
                ):
                    app = app.run()
                self.assertFalse(app.exception)
                self.assertTrue(any(expected in message.value for message in app.info))
                self.assertTrue(any("Payment status: **Pending**" in item.value for item in app.caption))


if __name__ == "__main__":
    unittest.main()
