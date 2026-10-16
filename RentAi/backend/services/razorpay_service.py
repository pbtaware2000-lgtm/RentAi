"""Server-side Razorpay order creation and payment verification for rent records."""

import base64
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import hmac
import json
import logging
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import HTTPException

from backend.auth.dependencies import CurrentUser
from backend.core.config import SettingsError, get_razorpay_test_credentials
from backend.repositories.sqlite_repository import SQLiteRepository


RAZORPAY_API = "https://api.razorpay.com/v1"
_PAYMENT_ID = re.compile(r"^pay_[A-Za-z0-9]+$")
_ORDER_ID = re.compile(r"^order_[A-Za-z0-9]+$")
_SIGNATURE = re.compile(r"^[A-Fa-f0-9]{64}$")
# Temporary, value-free checkout diagnostics; remove after confirming the live flow.
_logger = logging.getLogger(__name__)


class RazorpayProviderError(RuntimeError):
    """Safe provider failure; never includes credentials or provider response bodies."""


def _provider_request(method: str, path: str, key_id: str, key_secret: str, payload: dict | None = None) -> dict:
    credential = base64.b64encode(f"{key_id}:{key_secret}".encode("utf-8")).decode("ascii")
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        f"{RAZORPAY_API}{path}",
        data=body,
        headers={
            "Authorization": f"Basic {credential}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urlopen(request, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise RazorpayProviderError("Razorpay could not complete the payment request. Please retry shortly.") from None
    if not isinstance(result, dict):
        raise RazorpayProviderError("Razorpay returned an invalid response. Please retry shortly.")
    return result


class RazorpayRentService:
    def __init__(self, repository: SQLiteRepository | None = None):
        self.repository = repository or SQLiteRepository()

    @staticmethod
    def _renter_record(user: CurrentUser, record: dict | None) -> dict:
        data = (record or {}).get("data") or {}
        if (
            not record
            or record.get("kind") != "rent_payment"
            or data.get("renter_uid") != user.uid
        ):
            raise HTTPException(status_code=404, detail="Payable rent record was not found for this Renter account")
        if data.get("status") != "pending":
            raise HTTPException(status_code=409, detail="This rent record is no longer payable")
        return data

    @staticmethod
    def _amount_paise(data: dict) -> int:
        try:
            amount = Decimal(str(data.get("amount")))
            paise = (amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        except (InvalidOperation, TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Rent record has an invalid amount") from None
        if not amount.is_finite() or amount <= 0 or paise <= 0 or Decimal(int(paise)) / 100 != amount:
            raise HTTPException(status_code=400, detail="Rent record has an invalid amount")
        return int(paise)

    def create_order(self, user: CurrentUser, rent_record_id: str) -> dict:
        _logger.info("Razorpay checkout stage=backend_order_request_received role=%s", user.role)
        if user.role != "renter":
            raise HTTPException(status_code=403, detail="Only a Renter can pay rent")
        record = self.repository.get("application_records", rent_record_id)
        data = self._renter_record(user, record)
        amount = self._amount_paise(data)
        try:
            key_id, key_secret = get_razorpay_test_credentials()
        except SettingsError as exc:
            _logger.error("Razorpay checkout stage=credentials_unavailable")
            raise HTTPException(status_code=503, detail=str(exc)) from None

        existing_order = data.get("razorpay_order")
        if isinstance(existing_order, dict) and existing_order.get("id"):
            _logger.info("Razorpay checkout stage=existing_order_reused")
            order = {
                "id": existing_order["id"],
                "amount": existing_order.get("amount"),
                "currency": existing_order.get("currency"),
            }
        else:
            _logger.info("Razorpay checkout stage=provider_order_request_started")
            receipt = f"rent-{rent_record_id}"[:40]
            try:
                order = _provider_request(
                    "POST", "/orders", key_id, key_secret,
                    {
                        "amount": amount,
                        "currency": "INR",
                        "receipt": receipt,
                        "notes": {"rent_record_id": rent_record_id},
                    },
                )
            except RazorpayProviderError as exc:
                _logger.warning("Razorpay checkout stage=provider_order_failed")
                raise HTTPException(status_code=502, detail=str(exc)) from None
            if (
                not isinstance(order.get("id"), str)
                or not _ORDER_ID.fullmatch(order["id"])
                or order.get("amount") != amount
                or order.get("currency") != "INR"
            ):
                raise HTTPException(status_code=502, detail="Razorpay returned an invalid rent order")
            saved_order = self.repository.attach_rent_order_if_pending(
                rent_record_id,
                {
                    "id": order["id"], "amount": amount, "currency": "INR",
                    "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                },
            )
            if not saved_order:
                raise HTTPException(status_code=409, detail="This rent record is no longer payable")
            _logger.info("Razorpay checkout stage=provider_order_persisted")
            order = {
                "id": saved_order.get("id"),
                "amount": saved_order.get("amount"),
                "currency": saved_order.get("currency"),
            }

        if order.get("amount") != amount or order.get("currency") != "INR":
            raise HTTPException(status_code=409, detail="Saved Razorpay order does not match this rent record")
        _logger.info("Razorpay checkout stage=backend_order_response_ready")
        return {
            "key_id": key_id,
            "order_id": order["id"],
            "amount": amount,
            "currency": "INR",
            "name": "RentAI",
            "description": f"Rent for {data.get('property_title', 'property')} · {data.get('period', '')}",
            "rent_record_id": rent_record_id,
        }

    def verify_payment(
        self,
        user: CurrentUser,
        *,
        payment_id: str,
        order_id: str,
        signature: str,
    ) -> dict:
        if user.role != "renter":
            raise HTTPException(status_code=403, detail="Only a Renter can confirm a rent payment")
        if (
            not isinstance(payment_id, str) or not _PAYMENT_ID.fullmatch(payment_id)
            or not isinstance(order_id, str) or not _ORDER_ID.fullmatch(order_id)
            or not isinstance(signature, str) or not _SIGNATURE.fullmatch(signature)
        ):
            raise HTTPException(status_code=400, detail="Razorpay returned invalid payment verification details")

        record = next((item for item in self.repository.list_records()
                       if item.get("kind") == "rent_payment"
                       and ((item.get("data") or {}).get("razorpay_order") or {}).get("id") == order_id), None)
        if not record or (record.get("data") or {}).get("renter_uid") != user.uid:
            raise HTTPException(status_code=404, detail="Razorpay order was not found for this Renter account")
        data = record.get("data") or {}
        try:
            key_id, key_secret = get_razorpay_test_credentials()
        except SettingsError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None
        expected = hmac.new(
            key_secret.encode("utf-8"), f"{order_id}|{payment_id}".encode("utf-8"), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, signature.lower()):
            raise HTTPException(status_code=400, detail="Razorpay payment signature could not be verified")

        if data.get("status") == "paid":
            if data.get("razorpay_payment_id") == payment_id:
                return {"rent_record_id": record["id"], "status": "paid", "idempotent": True}
            raise HTTPException(status_code=409, detail="This rent record has already been paid")
        if data.get("status") != "pending":
            raise HTTPException(status_code=409, detail="This rent record is no longer payable")

        amount = self._amount_paise(data)
        try:
            provider_order = _provider_request("GET", f"/orders/{order_id}", key_id, key_secret)
            provider_payment = _provider_request("GET", f"/payments/{payment_id}", key_id, key_secret)
        except RazorpayProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from None
        order_is_paid = (
            provider_order.get("id") == order_id
            and provider_order.get("status") == "paid"
            and provider_order.get("amount") == amount
            and provider_order.get("amount_paid") == amount
            and provider_order.get("currency") == "INR"
        )
        payment_is_captured = (
            provider_payment.get("id") == payment_id
            and provider_payment.get("order_id") == order_id
            and provider_payment.get("status") == "captured"
            and provider_payment.get("captured") is True
            and provider_payment.get("amount") == amount
            and provider_payment.get("currency") == "INR"
        )
        if not order_is_paid or not payment_is_captured:
            raise HTTPException(status_code=409, detail="Razorpay has not confirmed a captured payment for this rent amount")

        payment_method = provider_payment.get("method")
        if not isinstance(payment_method, str) or len(payment_method) > 40:
            payment_method = "Razorpay"
        updated_data = {
            **data,
            "status": "paid",
            "paid_amount": amount / 100,
            "paid_date": datetime.now(timezone.utc).date().isoformat(),
            "payment_method": f"Razorpay ({payment_method})",
            "payment_reference": payment_id,
            "payment_provider": "razorpay_test",
            "razorpay_payment_id": payment_id,
        }
        result = self.repository.mark_rent_paid_if_pending(record["id"], order_id, updated_data)
        if result == "missing":
            raise HTTPException(status_code=404, detail="Rent record is no longer available")
        if result == "conflict":
            raise HTTPException(status_code=409, detail="This rent record changed while payment was being verified")
        return {"rent_record_id": record["id"], "status": "paid", "idempotent": result == "already_paid"}
