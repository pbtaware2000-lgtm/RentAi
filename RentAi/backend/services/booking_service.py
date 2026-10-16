"""Transactional owner-listing bookings and Razorpay Payment Links."""

import hashlib
import hmac
import json
import os
import uuid
from datetime import date, datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import HTTPException

from backend.repositories.sqlite_repository import connect


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def razorpay_configured():
    return bool(os.getenv("RAZORPAY_KEY_ID", "").strip() and os.getenv("RAZORPAY_KEY_SECRET", "").strip()
                and os.getenv("RAZORPAY_WEBHOOK_SECRET", "").strip())


def _gateway(method, path, payload=None):
    import base64

    key_id = os.getenv("RAZORPAY_KEY_ID", "").strip()
    key_secret = os.getenv("RAZORPAY_KEY_SECRET", "").strip()
    if not key_id or not key_secret:
        raise HTTPException(status_code=503, detail="Razorpay is not configured. Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET on the API server.")
    credentials = base64.b64encode(f"{key_id}:{key_secret}".encode()).decode()
    data = json.dumps(payload).encode() if payload is not None else None
    request = Request("https://api.razorpay.com/v1" + path, data=data, method=method,
                      headers={"Authorization": f"Basic {credentials}", "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode())
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        raise HTTPException(status_code=502, detail="Razorpay could not complete the payment request. Please retry.") from None


class BookingService:
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
            try:
                monthly_rent = float(data["rent"])
                deposit = float(data.get("security_deposit") or 0)
            except (KeyError, TypeError, ValueError):
                raise HTTPException(status_code=409, detail="Listing rental terms are incomplete") from None
            if monthly_rent <= 0 or deposit < 0:
                raise HTTPException(status_code=409, detail="Listing rental terms are invalid")
            amount = round((monthly_rent + deposit) * 100)
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
                p.payment_link_id,p.transaction_id,p.status AS latest_payment_status,p.checkout_url
                FROM bookings b JOIN application_records r ON r.id=b.property_id
                JOIN users o ON o.uid=b.owner_uid LEFT JOIN booking_payments p ON p.booking_id=b.id
                AND p.created_at=(SELECT MAX(p2.created_at) FROM booking_payments p2 WHERE p2.booking_id=b.id)
                WHERE b.renter_uid=? ORDER BY b.created_at DESC""", (renter_uid,)).fetchall()
        result = []
        for row in rows:
            item = self._hydrate(row)
            item["latest_payment"] = {"payment_link_id": item.pop("payment_link_id"), "transaction_id": item.pop("transaction_id"),
                                      "status": item.pop("latest_payment_status"), "checkout_url": item.pop("checkout_url")}
            result.append(item)
        return result

    def list_for_owner(self, owner_uid):
        with connect() as db:
            rows = db.execute("""SELECT b.*,r.data AS property_data,u.name AS renter_name,u.email AS renter_email,
                p.transaction_id,p.status AS latest_payment_status
                FROM bookings b JOIN application_records r ON r.id=b.property_id
                JOIN users u ON u.uid=b.renter_uid LEFT JOIN booking_payments p ON p.booking_id=b.id
                AND p.created_at=(SELECT MAX(p2.created_at) FROM booking_payments p2 WHERE p2.booking_id=b.id)
                WHERE b.owner_uid=? ORDER BY b.created_at DESC""", (owner_uid,)).fetchall()
        result = []
        for row in rows:
            item = self._hydrate(row)
            item["latest_payment"] = {"transaction_id": item.pop("transaction_id"), "status": item.pop("latest_payment_status")}
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
                status = "accepted_pending_payment"
            db.execute("UPDATE bookings SET status=?,updated_at=? WHERE id=?", (status, _now(), booking_id))
            message = "Your booking request was accepted. Payment is due before confirmation." if decision == "accept" else "Your booking request was rejected by the owner."
            self._notify(db, booking_id, booking["renter_uid"], f"decision_{decision}", message)
        return next(item for item in self.list_for_owner(owner.uid) if item["id"] == booking_id)

    def create_checkout(self, renter, booking_id: str):
        # Reserve the single active slot during the hosted payment attempt. A successful
        # webhook is still independently reconciled and cannot be lost on conflicts.
        now = _now()
        payment_id = uuid.uuid4().hex
        availability_error = False
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            booking = db.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
            if not booking or booking["renter_uid"] != renter.uid:
                raise HTTPException(status_code=404, detail="Booking not found")
            if booking["status"] != "accepted_pending_payment" or booking["payment_status"] not in {"pending", "failed"}:
                raise HTTPException(status_code=409, detail="Payment is available only for an accepted booking awaiting payment")
            if booking["availability_conflict"]:
                db.execute("UPDATE bookings SET availability_conflict=0 WHERE id=?", (booking_id,))
            listing_row = db.execute("SELECT data FROM application_records WHERE id=?", (booking["property_id"],)).fetchone()
            listing_data = json.loads(listing_row["data"]) if listing_row else {}
            if not listing_row or listing_data.get("status") != "published" or listing_data.get("occupancy_status") != "vacant":
                db.execute("UPDATE bookings SET availability_conflict=1,updated_at=? WHERE id=?", (_now(), booking_id))
                availability_error = True
            if not availability_error:
                previous_payment = db.execute("SELECT payment_link_id FROM booking_payments WHERE booking_id=? AND payment_link_id IS NOT NULL ORDER BY created_at DESC LIMIT 1", (booking_id,)).fetchone()
                db.execute("UPDATE bookings SET payment_status='processing',updated_at=? WHERE id=?", (now, booking_id))
                db.execute("INSERT INTO booking_payments(id,booking_id,amount,currency,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                           (payment_id, booking_id, booking["amount_due"], booking["currency"], "processing", now, now))
        if availability_error:
            raise HTTPException(status_code=409, detail="The listing is no longer marked available. Contact the owner before paying.")
        try:
            if previous_payment and previous_payment["payment_link_id"]:
                _gateway("POST", f"/payment_links/{previous_payment['payment_link_id']}/cancel", {})
            link = _gateway("POST", "/payment_links", {
                "amount": booking["amount_due"], "currency": booking["currency"],
                "accept_partial": False, "reference_id": payment_id[:40],
                "description": f"RentAI booking {booking_id}",
                "notes": {"booking_id": booking_id, "payment_attempt_id": payment_id},
                "reminder_enable": False,
            })
            if not link.get("id") or not link.get("short_url"):
                raise HTTPException(status_code=502, detail="Razorpay did not return a usable checkout link")
        except Exception:
            with connect() as db:
                db.execute("UPDATE bookings SET payment_status='failed',updated_at=? WHERE id=? AND payment_status='processing'", (_now(), booking_id))
                db.execute("UPDATE booking_payments SET status='failed',updated_at=? WHERE id=?", (_now(), payment_id))
            raise
        with connect() as db:
            db.execute("UPDATE booking_payments SET payment_link_id=?,checkout_url=?,updated_at=? WHERE id=?", (link["id"], link["short_url"], _now(), payment_id))
        return {"booking_id": booking_id, "payment_status": "processing", "checkout_url": link["short_url"]}

    @staticmethod
    def verify_webhook(raw_body: bytes, signature: str | None):
        secret = os.getenv("RAZORPAY_WEBHOOK_SECRET", "").strip()
        if not secret:
            raise HTTPException(status_code=503, detail="Razorpay webhook is not configured")
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
        event_type = event.get("event")
        payload = event.get("payload") or {}
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Invalid Razorpay webhook payload")
        payment_entity = ((payload.get("payment") or {}).get("entity") or {})
        link_entity = ((payload.get("payment_link") or {}).get("entity") or {})
        link_id = (link_entity.get("id") or payment_entity.get("payment_link_id")
                   or payment_entity.get("plink_id") or payment_entity.get("invoice_id"))
        if event_type not in {"payment_link.paid", "payment.failed"} or not link_id:
            return {"received": True, "ignored": True}
        verified = None
        if event_type == "payment_link.paid":
            verified = _gateway("GET", f"/payment_links/{link_id}")
            linked_payments = verified.get("payments") or []
            payment_id = payment_entity.get("id") or next((
                entry.get("payment_id") or entry.get("id") for entry in linked_payments
                if isinstance(entry, dict) and entry.get("status", "captured") == "captured"
            ), None)
            payment = _gateway("GET", f"/payments/{payment_id}") if payment_id else {}
            link_contains_payment = any(
                (entry.get("payment_id") or entry.get("id")) == payment_id
                and entry.get("status", "captured") == "captured"
                for entry in linked_payments if isinstance(entry, dict)
            )
            if verified.get("status") != "paid" or payment.get("status") != "captured" or not link_contains_payment:
                raise HTTPException(status_code=400, detail="Razorpay payment could not be independently verified")
        now = _now()
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM payment_webhook_events WHERE event_id=?", (event_id,)).fetchone():
                return {"received": True, "duplicate": True}
            payment_row = db.execute("SELECT p.*,b.property_id,b.renter_uid,b.owner_uid,b.status AS booking_status,b.availability_conflict,b.amount_due FROM booking_payments p JOIN bookings b ON b.id=p.booking_id WHERE p.payment_link_id=?", (link_id,)).fetchone()
            if not payment_row:
                db.execute("INSERT INTO payment_webhook_events(event_id,received_at) VALUES(?,?)", (event_id, now))
                return {"received": True, "ignored": True}
            if event_type == "payment_link.paid":
                if (int(verified.get("amount", -1)) != payment_row["amount_due"]
                        or int(payment.get("amount", -1)) != payment_row["amount_due"]
                        or verified.get("currency") != payment_row["currency"]
                        or payment.get("currency") != payment_row["currency"]):
                    raise HTTPException(status_code=400, detail="Verified payment amount does not match the booking terms")
                active_conflict = db.execute("SELECT 1 FROM bookings WHERE property_id=? AND id<>? AND status IN ('accepted_pending_payment','confirmed') LIMIT 1", (payment_row["property_id"], payment_row["booking_id"])).fetchone()
                listing_row = db.execute("SELECT data FROM application_records WHERE id=?", (payment_row["property_id"],)).fetchone()
                listing_data = json.loads(listing_row["data"]) if listing_row else {}
                conflict = active_conflict or listing_data.get("status") != "published" or listing_data.get("occupancy_status") != "vacant"
                db.execute("UPDATE booking_payments SET status='paid',transaction_id=?,updated_at=? WHERE id=?", (payment["id"], now, payment_row["id"]))
                if conflict:
                    # Keep the captured transaction and expose the conflict for resolution.
                    db.execute("UPDATE bookings SET payment_status='paid',availability_conflict=1,updated_at=? WHERE id=?", (now, payment_row["booking_id"]))
                    self._notify(db, payment_row["booking_id"], payment_row["renter_uid"], "payment_conflict", "Razorpay verified your payment, but the listing needs availability review before confirmation.")
                    self._notify(db, payment_row["booking_id"], payment_row["owner_uid"], "payment_conflict", "A booking payment was captured while listing availability needed review.")
                else:
                    db.execute("UPDATE bookings SET status='confirmed',payment_status='paid',updated_at=? WHERE id=?", (now, payment_row["booking_id"]))
                    listing_data["occupancy_status"] = "occupied"
                    db.execute("UPDATE application_records SET data=? WHERE id=?", (json.dumps(listing_data), payment_row["property_id"]))
                    self._notify(db, payment_row["booking_id"], payment_row["renter_uid"], "payment_paid", "Razorpay verified payment and your booking is confirmed.")
                    self._notify(db, payment_row["booking_id"], payment_row["owner_uid"], "payment_paid", "Razorpay verified payment and the booking is confirmed.")
            else:
                db.execute("UPDATE booking_payments SET status='failed',transaction_id=?,updated_at=? WHERE id=? AND status<>'paid'", (payment_entity.get("id"), now, payment_row["id"]))
                db.execute("UPDATE bookings SET payment_status='failed',updated_at=? WHERE id=? AND status='accepted_pending_payment' AND payment_status<>'paid'", (now, payment_row["booking_id"]))
                self._notify(db, payment_row["booking_id"], payment_row["renter_uid"], "payment_failed", "Razorpay reported a failed payment. You can retry from My Bookings.")
            db.execute("INSERT INTO payment_webhook_events(event_id,received_at) VALUES(?,?)", (event_id, now))
        return {"received": True}
