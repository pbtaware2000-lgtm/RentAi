"""The Renter rent page delegates order and payment confirmation to the API."""

import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from frontend.application_api import ApplicationAPIError


PENDING_RENT = {
    "id": "rent-record-1",
    "kind": "rent_payment",
    "data": {
        "property_title": "Saved Rental Home",
        "period": "2026-10",
        "due_date": "2026-10-05",
        "amount": 28000,
        "status": "pending",
    },
}


class RenterPaymentsUITests(unittest.TestCase):
    @staticmethod
    def _app():
        return AppTest.from_string(
            "from frontend.pages.renter_workspace import render_rent_payments\nrender_rent_payments()",
            default_timeout=30,
        )

    def test_pending_rent_page_shows_saved_amount_and_requests_order_on_click(self):
        order = {
            "key_id": "rzp_test_public", "order_id": "order_test123",
            "amount": 2800000, "currency": "INR", "name": "RentAI",
            "description": "Rent for Saved Rental Home · 2026-10",
            "rent_record_id": PENDING_RENT["id"],
        }
        with patch("frontend.pages.renter_workspace.records", return_value=[PENDING_RENT]), \
             patch("frontend.pages.renter_workspace.create_rent_payment_order", return_value=order) as create_order, \
             patch("frontend.pages.renter_workspace.razorpay_checkout", return_value=None):
            app = self._app().run()
            self.assertFalse(app.exception)
            self.assertEqual([item.label for item in app.button], ["Pay with Razorpay"])
            self.assertIn("₹28,000.00", [metric.value for metric in app.metric])
            app.button[0].click().run()
        create_order.assert_called_once_with(PENDING_RENT["id"])
        self.assertFalse(app.exception)

    def test_checkout_response_is_sent_for_backend_verification_before_success(self):
        checkout = {
            "key_id": "rzp_test_public", "order_id": "order_test123",
            "amount": 2800000, "currency": "INR", "name": "RentAI",
            "description": "Rent for Saved Rental Home · 2026-10",
            "rent_record_id": PENDING_RENT["id"],
        }
        response = {
            "status": "checkout_success", "razorpay_payment_id": "pay_test123",
            "razorpay_order_id": "order_test123", "razorpay_signature": "a" * 64,
        }
        with patch("frontend.pages.renter_workspace.records", return_value=[PENDING_RENT]), \
             patch("frontend.pages.renter_workspace.verify_rent_payment", return_value={"status": "paid"}) as verify, \
             patch("frontend.pages.renter_workspace.razorpay_checkout", return_value=response):
            app = self._app()
            app.session_state["rentai_rent_checkout"] = checkout
            app = app.run()
        verify.assert_called_once_with("pay_test123", "order_test123", "a" * 64)
        self.assertFalse(app.exception)
        self.assertIn(
            "Razorpay confirmed the captured payment. Your rent record is now Paid.",
            [item.value for item in app.success],
        )

    def test_safe_api_error_is_visible_without_changing_pending_record(self):
        with patch("frontend.pages.renter_workspace.records", return_value=[PENDING_RENT]), \
             patch("frontend.pages.renter_workspace.create_rent_payment_order", side_effect=ApplicationAPIError("Razorpay Test Mode keys are missing.")):
            app = self._app().run()
            app.button[0].click().run()
        self.assertFalse(app.exception)
        self.assertIn("Razorpay Test Mode keys are missing.", [item.value for item in app.error])

    def test_cancelled_checkout_keeps_rent_pending_and_shows_retry_guidance(self):
        checkout = {
            "key_id": "rzp_test_public", "order_id": "order_test123",
            "amount": 2800000, "currency": "INR", "name": "RentAI",
            "description": "Rent for Saved Rental Home", "rent_record_id": PENDING_RENT["id"],
        }
        with patch("frontend.pages.renter_workspace.records", return_value=[PENDING_RENT]), \
             patch("frontend.pages.renter_workspace.razorpay_checkout", return_value={"status": "checkout_cancelled"}), \
             patch("frontend.pages.renter_workspace.verify_rent_payment") as verify:
            app = self._app()
            app.session_state["rentai_rent_checkout"] = checkout
            app = app.run()
        verify.assert_not_called()
        self.assertFalse(app.exception)
        self.assertIn("Checkout was cancelled. The rent remains Pending; you can retry when ready.", [item.value for item in app.info])


if __name__ == "__main__":
    unittest.main()
