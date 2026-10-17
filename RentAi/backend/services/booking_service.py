"""Transactional owner-listing bookings and Razorpay order payments."""

import hashlib
import hmac
import json
import logging
import re
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from fastapi import HTTPException

from backend.core.config import (
    SettingsError,
    get_razorpay_test_credentials,
    get_razorpay_webhook_secret,
    razorpay_configuration_status,
)
from backend.repositories.sqlite_repository import connect
from backend.services.razorpay_service import RazorpayProviderError, _provider_request


_logger = logging.getLogger(__name__)
_ORDER_ID = re.compile(r"^order_[A-Za-z0-9]+$")
_PAYMENT_ID = re.compile(r"^pay_[A-Za-z0-9]+$")
_SIGNATURE = re.compile(r"^[A-Fa-f0-9]{64}$")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def razorpay_configured():
    return bool(razorpay_configuration_status()["configured"])


def _gateway(method, path, payload=None):
    key_id, key_secret = get_razorpay_test_credentials()
    return _provider_request(method, path, key_id, key_secret, payload)


class BookingService:
    @staticmethod
    def _listing_terms(data: dict):
        try:
            rent = Decimal(str(data.get("rent")))
            deposit = Decimal(str(data.get("security_deposit") or 0))
            amount = ((rent + deposit) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        except (InvalidOperation, TypeError, ValueError):
            raise HTTPException(status_code=409, detail="Listing rental terms are incomplete or invalid") from None
        if not rent.is_finite() or not deposit.is_finite() or rent <= 0 or deposit < 0 or amount <= 0:
            raise HTTPException(status_code=409, detail="Listing rental terms are incomplete or invalid")
        return float(rent), float(deposit), int(amount)

    @staticmethod
    def _notify(db, booking_id, recipient_uid, event_type, message):
        db.execute("INSERT OR IGNORE INTO booking_notifications(id,booking_id,recipient_uid,event_type,message,created_at) VALUES(?,?,?,?,?,?)",
                   (uuid.uuid4().hex, booking_id, recipient_uid, event_type, message, _now()))

    def list_notifications(self, recipient_uid):
        with connect() as db:
            rows = db.execute("SELECT * FROM booking_notifications WHERE recipient_uid=? ORDER BY created_at DESC LIMIT 50", (recipient_uid,)).fetchall()
        return [dict(row) for row in rows]

    def create_request(self, renter, property_id: str, move_in_date: str):
        try:
            requested_date = date.fromisoformat(move_in_date)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Move-in date must use YYYY-MM-DD") from None
        if requested_date < date.today():
            raise HTTPException(status_code=400, detail="Move-in date cannot be in the past")
        now = _now()
        booking_id = uuid.uuid4().hex
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            listing = db.execute("SELECT owner_uid,kind,data FROM application_records WHERE id=?", (property_id,)).fetchone()
            if not listing or listing["kind"] != "listing":
                raise HTTPException(status_code=404, detail="Bookable owner listing not found")
            data = json.loads(listing["data"])
            if data.get("status") != "published" or data.get("occupancy_status") != "vacant":
                raise HTTPException(status_code=409, detail="Only published listings explicitly marked vacant can receive booking requests")
            owner_uid = listing["owner_uid"]
            if owner_uid == renter.uid:
                raise HTTPException(status_code=400, detail="You cannot book your own listing")
            occupied = db.execute("SELECT 1 FROM bookings WHERE property_id=? AND status IN ('accepted_pending_payment','confirmed') LIMIT 1", (property_id,)).fetchone()
            if occupied:
                raise HTTPException(status_code=409, detail="This property already has an active booking")
            duplicate = db.execute("SELECT 1 FROM bookings WHERE property_id=? AND renter_uid=? AND status='pending_owner_approval' LIMIT 1", (property_id, renter.uid)).fetchone()
            if duplicate:
                raise HTTPException(status_code=409, detail="You already have a pending request for this property")
            monthly_rent, deposit, amount = self._listing_terms(data)
            db.execute("INSERT INTO bookings(id,property_id,renter_uid,owner_uid,move_in_date,monthly_rent,security_deposit,amount_due,currency,status,payment_status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,'pending_owner_approval','pending',?,?)",
                       (booking_id, property_id, renter.uid, owner_uid, requested_date.isoformat(), monthly_rent, deposit, amount, "INR", now, now))
            self._notify(db, booking_id, owner_uid, "request", f"A renter requested {data.get('title') or 'your property'} for move-in on {requested_date.isoformat()}.")
        return next(item for item in self.list_for_renter(renter.uid) if item["id"] == booking_id)

    @staticmethod
    def _hydrate(row):
        result = dict(row)
        result["property"] = json.loads(result.pop("property_data"))
        result["latest_payment"] = None
        return result

    def list_for_renter(self, renter_uid):
        with connect() as db:
            rows = db.execute("""SELECT b.*,r.data AS property_data,o.name AS owner_name,o.email AS owner_email,
                p.razorpay_order_id,p.payment_link_id,p.transaction_id,p.status AS latest_payment_status,p.checkout_url
                FROM bookings b JOIN application_records r ON r.id=b.property_id
                JOIN users o ON o.uid=b.owner_uid LEFT JOIN booking_payments p ON p.booking_id=b.id
                AND p.created_at=(SELECT MAX(p2.created_at) FROM booking_payments p2 WHERE p2.booking_id=b.id)
                WHERE b.renter_uid=? ORDER BY b.created_at DESC""", (renter_uid,)).fetchall()
        result = []
        for row in rows:
            item = self._hydrate(row)
            item["latest_payment"] = {"order_id": item.pop("razorpay_order_id"),
                                      "payment_link_id": item.pop("payment_link_id"),
                                      "transaction_id": item.pop("transaction_id"),
                                      "status": item.pop("latest_payment_status"),
                                      "checkout_url": item.pop("checkout_url")}
            result.append(item)
        return result

    def list_for_owner(self, owner_uid):
        with connect() as db:
            rows = db.execute("""SELECT b.*,r.data AS property_data,u.name AS renter_name,u.email AS renter_email,
                p.transaction_id,p.razorpay_order_id,p.status AS latest_payment_status
                FROM bookings b JOIN application_records r ON r.id=b.property_id
                JOIN users u ON u.uid=b.renter_uid LEFT JOIN booking_payments p ON p.booking_id=b.id
                AND p.created_at=(SELECT MAX(p2.created_at) FROM booking_payments p2 WHERE p2.booking_id=b.id)
                WHERE b.owner_uid=? ORDER BY b.created_at DESC""", (owner_uid,)).fetchall()
        result = []
        for row in rows:
            item = self._hydrate(row)
            item["latest_payment"] = {"transaction_id": item.pop("transaction_id"),
                                      "order_id": item.pop("razorpay_order_id"),
                                      "status": item.pop("latest_payment_status")}
            result.append(item)
        return result

    def decide(self, owner, booking_id: str, decision: str):
        if decision not in {"accept", "reject"}:
            raise HTTPException(status_code=400, detail="Decision must be accept or reject")
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            booking = db.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
            if not booking:
                raise HTTPException(status_code=404, detail="Booking request not found")
            if booking["owner_uid"] != owner.uid:
                raise HTTPException(status_code=403, detail="You can only decide requests for your own properties")
            if booking["status"] != "pending_owner_approval":
                raise HTTPException(status_code=409, detail="This booking request has already been decided")
            status = "rejected"
            if decision == "accept":
                listing = db.execute("SELECT data FROM application_records WHERE id=?", (booking["property_id"],)).fetchone()
                data = json.loads(listing["data"]) if listing else {}
                active = db.execute("SELECT 1 FROM bookings WHERE property_id=? AND id<>? AND status IN ('accepted_pending_payment','confirmed') LIMIT 1", (booking["property_id"], booking_id)).fetchone()
                if not listing or data.get("status") != "published" or data.get("occupancy_status") != "vacant" or active:
                    raise HTTPException(status_code=409, detail="This property is no longer eligible for booking")
                monthly_rent, deposit, amount = self._listing_terms(data)
                status = "accepted_pending_payment"
                db.execute("UPDATE bookings SET monthly_rent=?,security_deposit=?,amount_due=?,updated_at=? WHERE id=?",
                           (monthly_rent, deposit, amount, _now(), booking_id))
            db.execute("UPDATE bookings SET status=?,updated_at=? WHERE id=?", (status, _now(), booking_id))
            message = "Your booking request was accepted. Payment is due before confirmation." if decision == "accept" else "Your booking request was rejected by the owner."
            self._notify(db, booking_id, booking["renter_uid"], f"decision_{decision}", message)
        return next(item for item in self.list_for_owner(owner.uid) if item["id"] == booking_id)

    def create_checkout(self, renter, booking_id: str):
        if getattr(renter, "role", None) != "renter":
            raise HTTPException(status_code=403, detail="Only a Renter can pay for a booking")
        try:
            key_id, _key_secret = get_razorpay_test_credentials()
        except SettingsError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None
        now = _now()
        payment_id = uuid.uuid4().hex
        existing_order = None
        legacy_link_id = None
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            booking = db.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
            if not booking or booking["renter_uid"] != renter.uid:
                raise HTTPException(status_code=404, detail="Booking not found")
            if booking["status"] != "accepted_pending_payment" or booking["payment_status"] not in {"pending", "failed", "processing"}:
                raise HTTPException(status_code=409, detail="Payment is available only for an accepted booking awaiting payment")
            if booking["payment_status"] in {"processing", "failed"}:
                attempt_status = booking["payment_status"]
                existing_order = db.execute(
                    "SELECT * FROM booking_payments WHERE booking_id=? AND status=? AND razorpay_order_id IS NOT NULL ORDER BY created_at DESC LIMIT 1",
                    (booking_id, attempt_status),
                ).fetchone()
                if attempt_status == "processing" and not existing_order:
                    raise HTTPException(status_code=409, detail="An earlier payment attempt is still being prepared. Refresh this page and retry shortly.")
                if existing_order and attempt_status == "failed":
                    db.execute("UPDATE booking_payments SET status='processing',updated_at=? WHERE id=?", (now, existing_order["id"]))
                    db.execute("UPDATE bookings SET payment_status='processing',updated_at=? WHERE id=?", (now, booking_id))
                elif attempt_status == "failed":
                    legacy = db.execute("SELECT payment_link_id FROM booking_payments WHERE booking_id=? AND status='failed' AND payment_link_id IS NOT NULL ORDER BY created_at DESC LIMIT 1", (booking_id,)).fetchone()
                    legacy_link_id = legacy["payment_link_id"] if legacy else None
            if existing_order:
                payment = dict(existing_order)
                amount = int(payment["amount"])
                order = {"id": payment["razorpay_order_id"], "amount": amount, "currency": payment["currency"]}
            else:
                listing_row = db.execute("SELECT data FROM application_records WHERE id=? AND kind='listing'", (booking["property_id"],)).fetchone()
                listing_data = json.loads(listing_row["data"]) if listing_row else {}
                if not listing_row or listing_data.get("status") != "published" or listing_data.get("occupancy_status") != "vacant":
                    db.execute("UPDATE bookings SET availability_conflict=1,updated_at=? WHERE id=?", (_now(), booking_id))
                    raise HTTPException(status_code=409, detail="The listing is no longer marked available. Contact the owner before paying.")
                monthly_rent, deposit, amount = self._listing_terms(listing_data)
                if amount <= 0 or booking["currency"] != "INR":
                    raise HTTPException(status_code=409, detail="The saved booking payment amount is invalid")
                db.execute("UPDATE bookings SET monthly_rent=?,security_deposit=?,amount_due=?,payment_status='processing',availability_conflict=0,updated_at=? WHERE id=?",
                           (monthly_rent, deposit, amount, now, booking_id))
                db.execute("INSERT INTO booking_payments(id,booking_id,amount,currency,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                           (payment_id, booking_id, amount, booking["currency"], "processing", now, now))
        if existing_order:
            if order.get("amount") != amount or order.get("currency") != "INR":
                raise HTTPException(status_code=409, detail="Saved Razorpay order does not match the booking amount")
        else:
            try:
                if legacy_link_id:
                    _gateway("POST", f"/payment_links/{legacy_link_id}/cancel", {})
                order = _gateway("POST", "/orders", {
                    "amount": amount,
                    "currency": "INR",
                    "receipt": f"booking-{booking_id}"[:40],
                    "notes": {"booking_id": booking_id, "payment_attempt_id": payment_id},
                })
            except (RazorpayProviderError, SettingsError) as exc:
                with connect() as db:
                    db.execute("UPDATE bookings SET payment_status='failed',updated_at=? WHERE id=? AND payment_status='processing'", (_now(), booking_id))
                    db.execute("UPDATE booking_payments SET status='failed',updated_at=? WHERE id=?", (_now(), payment_id))
                raise HTTPException(status_code=502, detail="Razorpay could not create checkout. Please retry shortly.") from None
            if (not isinstance(order.get("id"), str) or not _ORDER_ID.fullmatch(order["id"])
                    or order.get("amount") != amount or order.get("currency") != "INR"):
                with connect() as db:
                    db.execute("UPDATE bookings SET payment_status='failed',updated_at=? WHERE id=? AND payment_status='processing'", (_now(), booking_id))
                    db.execute("UPDATE booking_payments SET status='failed',updated_at=? WHERE id=?", (_now(), payment_id))
                raise HTTPException(status_code=502, detail="Razorpay returned an invalid booking order")
            with connect() as db:
                db.execute("UPDATE booking_payments SET razorpay_order_id=?,updated_at=? WHERE id=?", (order["id"], _now(), payment_id))
        return {
            "booking_id": booking_id,
            "payment_status": "processing",
            "key_id": key_id,
            "order_id": order["id"],
            "amount": amount,
            "currency": "INR",
            "name": "RentAI",
            "description": f"Initial payment for {booking_id[:12]}",
        }

    def verify_payment(self, renter, booking_id: str, payment_id: str, order_id: str, signature: str):
        if getattr(renter, "role", None) != "renter":
            raise HTTPException(status_code=403, detail="Only a Renter can confirm a booking payment")
        if not isinstance(payment_id, str) or not _PAYMENT_ID.fullmatch(payment_id):
            raise HTTPException(status_code=400, detail="Razorpay returned an invalid payment ID")
        if not isinstance(order_id, str) or not _ORDER_ID.fullmatch(order_id):
            raise HTTPException(status_code=400, detail="Razorpay returned an invalid order ID")
        if not isinstance(signature, str) or not _SIGNATURE.fullmatch(signature):
            raise HTTPException(status_code=400, detail="Razorpay returned invalid payment verification details")
        with connect() as db:
            row = db.execute("""SELECT p.*,b.renter_uid,b.amount_due,b.currency AS booking_currency,b.status AS booking_status
                FROM booking_payments p JOIN bookings b ON b.id=p.booking_id
                WHERE p.booking_id=? AND p.razorpay_order_id=?""", (booking_id, order_id)).fetchone()
        if not row or row["renter_uid"] != renter.uid:
            raise HTTPException(status_code=404, detail="Razorpay order was not found for this Renter booking")
        if int(row["amount"]) != int(row["amount_due"]) or row["currency"] != row["booking_currency"]:
            raise HTTPException(status_code=409, detail="Saved Razorpay order does not match the booking amount")
        try:
            key_id, key_secret = get_razorpay_test_credentials()
        except SettingsError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None
        expected = hmac.new(key_secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature.lower()):
            raise HTTPException(status_code=400, detail="Razorpay payment signature could not be verified")
        if row["status"] == "paid" and row["transaction_id"] == payment_id:
            return {"booking_id": booking_id, "payment_status": "paid", "idempotent": True}
        try:
            provider_order = _gateway("GET", f"/orders/{order_id}")
            provider_payment = _gateway("GET", f"/payments/{payment_id}")
        except (RazorpayProviderError, SettingsError):
            raise HTTPException(status_code=502, detail="Razorpay could not verify this payment yet. Refresh and try again.") from None
        amount = int(row["amount"])
        if not (provider_order.get("id") == order_id and provider_order.get("status") == "paid"
                and provider_order.get("amount") == amount and provider_order.get("amount_paid") == amount
                and provider_order.get("currency") == "INR" and provider_payment.get("id") == payment_id
                and provider_payment.get("order_id") == order_id and provider_payment.get("status") == "captured"
                and provider_payment.get("captured") is True and provider_payment.get("amount") == amount
                and provider_payment.get("currency") == "INR"):
            raise HTTPException(status_code=409, detail="Razorpay has not confirmed a captured payment for this booking amount yet")
        result = self._record_verified_payment(booking_id, order_id, provider_payment)
        return {"booking_id": booking_id, "payment_status": "paid", "idempotent": result == "already_paid"}

    def _record_verified_payment(self, booking_id: str, order_id: str, payment: dict, *, payment_link_id: str | None = None):
        now = _now()
        payment_id = payment.get("id")
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            lookup_column = "payment_link_id" if payment_link_id else "razorpay_order_id"
            lookup_value = payment_link_id or order_id
            row = db.execute(f"""SELECT p.*,b.property_id,b.renter_uid,b.owner_uid,b.status AS booking_status,
                b.availability_conflict,b.amount_due,b.currency AS booking_currency
                FROM booking_payments p JOIN bookings b ON b.id=p.booking_id
                WHERE p.booking_id=? AND p.{lookup_column}=?""", (booking_id, lookup_value)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Razorpay order is not linked to a booking")
            payment_ref_matches = payment.get("payment_link_id") == payment_link_id if payment_link_id else payment.get("order_id") == order_id
            if (not payment_ref_matches or payment.get("amount") != row["amount_due"]
                    or payment.get("currency") != row["booking_currency"] or payment.get("status") != "captured"
                    or payment.get("captured") is not True):
                raise HTTPException(status_code=400, detail="Verified Razorpay payment does not match the saved booking terms")
            if row["status"] == "paid":
                if row["transaction_id"] == payment_id:
                    return "already_paid"
                raise HTTPException(status_code=409, detail="A different captured payment is already recorded for this booking")
            if row["booking_status"] != "accepted_pending_payment":
                raise HTTPException(status_code=409, detail="This booking is no longer awaiting payment")
            active_conflict = db.execute("SELECT 1 FROM bookings WHERE property_id=? AND id<>? AND status IN ('accepted_pending_payment','confirmed') LIMIT 1", (row["property_id"], booking_id)).fetchone()
            listing_row = db.execute("SELECT data FROM application_records WHERE id=? AND kind='listing'", (row["property_id"],)).fetchone()
            listing_data = json.loads(listing_row["data"]) if listing_row else {}
            conflict = bool(active_conflict) or listing_data.get("status") != "published" or listing_data.get("occupancy_status") != "vacant"
            db.execute("UPDATE booking_payments SET status='paid',transaction_id=?,updated_at=? WHERE id=?", (payment_id, now, row["id"]))
            if conflict:
                db.execute("UPDATE bookings SET payment_status='paid',availability_conflict=1,updated_at=? WHERE id=?", (now, booking_id))
                self._notify(db, booking_id, row["renter_uid"], "payment_conflict", "Razorpay verified your payment, but the listing needs availability review before confirmation.")
                self._notify(db, booking_id, row["owner_uid"], "payment_conflict", "A booking payment was captured while listing availability needed review.")
            else:
                db.execute("UPDATE bookings SET status='confirmed',payment_status='paid',updated_at=? WHERE id=?", (now, booking_id))
                listing_data["occupancy_status"] = "occupied"
                db.execute("UPDATE application_records SET data=? WHERE id=?", (json.dumps(listing_data), row["property_id"]))
                self._notify(db, booking_id, row["renter_uid"], "payment_paid", "Razorpay verified payment and your booking is confirmed.")
                self._notify(db, booking_id, row["owner_uid"], "payment_paid", "Razorpay verified payment and the booking is confirmed.")
        return "paid"

    def mark_payment_failed(self, booking_id: str, order_id: str | None, payment: dict | None = None,
                            *, payment_link_id: str | None = None):
        now = _now()
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            lookup_column = "payment_link_id" if payment_link_id else "razorpay_order_id"
            lookup_value = payment_link_id or order_id
            row = db.execute(f"""SELECT p.*,b.renter_uid,b.status AS booking_status,b.payment_status
                FROM booking_payments p JOIN bookings b ON b.id=p.booking_id
                WHERE p.booking_id=? AND p.{lookup_column}=?""", (booking_id, lookup_value)).fetchone()
            if not row or row["status"] == "paid":
                return False
            payment_ref_matches = payment.get("payment_link_id") == payment_link_id if payment_link_id else payment.get("order_id") == order_id
            if payment and (not payment_ref_matches or payment.get("amount") != row["amount"]
                            or payment.get("currency") != row["currency"]):
                raise HTTPException(status_code=400, detail="Failed Razorpay payment does not match the booking terms")
            db.execute("UPDATE booking_payments SET status='failed',transaction_id=COALESCE(?,transaction_id),updated_at=? WHERE id=?", ((payment or {}).get("id"), now, row["id"]))
            latest = db.execute("SELECT id FROM booking_payments WHERE booking_id=? ORDER BY created_at DESC LIMIT 1", (booking_id,)).fetchone()
            if latest and latest["id"] == row["id"] and row["booking_status"] == "accepted_pending_payment" and row["payment_status"] != "paid":
                db.execute("UPDATE bookings SET payment_status='failed',updated_at=? WHERE id=?", (now, booking_id))
                self._notify(db, booking_id, row["renter_uid"], "payment_failed", "Razorpay reported that this payment attempt failed. You can retry from My Bookings.")
            return True

    @staticmethod
    def verify_webhook(raw_body: bytes, signature: str | None):
        try:
            secret = get_razorpay_webhook_secret()
        except SettingsError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None
        expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
        if not signature or not hmac.compare_digest(expected, signature):
            raise HTTPException(status_code=400, detail="Invalid Razorpay webhook signature")
        try:
            event = json.loads(raw_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(status_code=400, detail="Invalid Razorpay webhook payload") from None
        if not isinstance(event, dict):
            raise HTTPException(status_code=400, detail="Invalid Razorpay webhook payload")
        return event

    def process_webhook(self, event_id: str, event: dict):
        if not event_id:
            raise HTTPException(status_code=400, detail="Missing Razorpay event id")
        with connect() as db:
            if db.execute("SELECT 1 FROM payment_webhook_events WHERE event_id=?", (event_id,)).fetchone():
                return {"received": True, "duplicate": True}
        if not isinstance(event, dict) or not isinstance(event.get("payload"), dict):
            raise HTTPException(status_code=400, detail="Invalid Razorpay webhook payload")
        event_type = event.get("event")
        payload = event["payload"]
        payment = ((payload.get("payment") or {}).get("entity") or {})
        order = ((payload.get("order") or {}).get("entity") or {})
        link = ((payload.get("payment_link") or {}).get("entity") or {})
        order_id = payment.get("order_id") or order.get("id")
        payment_link_id = link.get("id") or payment.get("payment_link_id") or payment.get("plink_id")
        payment_id = payment.get("id")
        legacy_link_event = event_type == "payment_link.paid"
        lookup_value = payment_link_id if legacy_link_event or (event_type == "payment.failed" and payment_link_id) else order_id
        lookup_column = "payment_link_id" if legacy_link_event or (event_type == "payment.failed" and payment_link_id) else "razorpay_order_id"
        if event_type not in {"order.paid", "payment.captured", "payment.failed", "payment_link.paid"} or not lookup_value:
            with connect() as db:
                db.execute("INSERT OR IGNORE INTO payment_webhook_events(event_id,received_at) VALUES(?,?)", (event_id, _now()))
            return {"received": True, "ignored": True}
        with connect() as db:
            attempt = db.execute(f"SELECT id,booking_id FROM booking_payments WHERE {lookup_column}=?", (lookup_value,)).fetchone()
        if not attempt:
            with connect() as db:
                db.execute("INSERT OR IGNORE INTO payment_webhook_events(event_id,received_at) VALUES(?,?)", (event_id, _now()))
            return {"received": True, "ignored": True}
        if event_type == "payment.failed":
            try:
                self.mark_payment_failed(
                    attempt["booking_id"], order_id, payment if payment_id else None,
                    payment_link_id=payment_link_id if lookup_column == "payment_link_id" else None,
                )
            except HTTPException:
                raise
        elif legacy_link_event:
            try:
                verified_link = _gateway("GET", f"/payment_links/{payment_link_id}")
                linked_payments = verified_link.get("payments") or []
                if not payment_id:
                    payment_id = next((
                        entry.get("payment_id") or entry.get("id") for entry in linked_payments
                        if isinstance(entry, dict) and entry.get("status", "captured") == "captured"
                    ), None)
                verified_payment = _gateway("GET", f"/payments/{payment_id}") if payment_id else {}
            except (SettingsError, RazorpayProviderError):
                raise HTTPException(status_code=502, detail="Razorpay could not verify this webhook yet") from None
            link_contains_payment = any(
                isinstance(entry, dict)
                and (entry.get("payment_id") or entry.get("id")) == payment_id
                and entry.get("status", "captured") == "captured"
                for entry in linked_payments
            )
            if (verified_link.get("id") != payment_link_id or verified_link.get("status") != "paid"
                    or not link_contains_payment or verified_payment.get("id") != payment_id
                    or verified_payment.get("payment_link_id") != payment_link_id
                    or verified_payment.get("status") != "captured" or verified_payment.get("captured") is not True
                    or verified_link.get("amount") != verified_payment.get("amount")
                    or verified_link.get("currency") != verified_payment.get("currency")):
                raise HTTPException(status_code=400, detail="Razorpay payment could not be independently verified")
            self._record_verified_payment(attempt["booking_id"], "", verified_payment, payment_link_id=payment_link_id)
        else:
            try:
                if event_type == "order.paid" and not payment_id:
                    payments = _gateway("GET", f"/orders/{order_id}/payments")
                    entries = payments.get("items", []) if isinstance(payments, dict) else []
                    payment_id = next((entry.get("id") for entry in entries if isinstance(entry, dict) and entry.get("status") == "captured"), None)
                if not payment_id:
                    raise HTTPException(status_code=400, detail="Captured Razorpay event did not include a payment ID")
                provider_order = _gateway("GET", f"/orders/{order_id}")
                verified_payment = _gateway("GET", f"/payments/{payment_id}")
            except (SettingsError, RazorpayProviderError) as exc:
                _logger.warning("Razorpay webhook verification failed event=%s reason=%s", event_id[:20], type(exc).__name__)
                raise HTTPException(status_code=502, detail="Razorpay could not verify this webhook yet") from None
            if (provider_order.get("id") != order_id or provider_order.get("status") != "paid"
                    or verified_payment.get("order_id") != order_id
                    or provider_order.get("amount") != verified_payment.get("amount")
                    or provider_order.get("amount_paid") != provider_order.get("amount")
                    or provider_order.get("currency") != verified_payment.get("currency")
                    or verified_payment.get("status") != "captured" or verified_payment.get("captured") is not True):
                raise HTTPException(status_code=400, detail="Razorpay payment could not be independently verified")
            self._record_verified_payment(attempt["booking_id"], order_id, verified_payment)
        with connect() as db:
            db.execute("INSERT OR IGNORE INTO payment_webhook_events(event_id,received_at) VALUES(?,?)", (event_id, _now()))
        return {"received": True}
