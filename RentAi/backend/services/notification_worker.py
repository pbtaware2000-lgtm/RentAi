"""Durable SMTP outbox delivery and scheduled Owner portfolio summaries."""

from datetime import datetime, timedelta, timezone
import logging
import math
import time
import uuid

from backend.repositories.sqlite_repository import SQLiteRepository
from backend.services.email_service import (
    EmailConfigurationError,
    SMTPEmailTransport,
    get_smtp_settings,
)


logger = logging.getLogger(__name__)
WEEKLY_SEND_HOUR_UTC = 9
MONTHLY_SEND_HOUR_UTC = 9
POLL_SECONDS = 30
LEASE_SECONDS = 300


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def owner_settings(records: list[dict], owner_uid: str) -> dict:
    return next((
        item.get("data") or {} for item in records
        if item.get("kind") == "profile"
        and item.get("owner_uid") == owner_uid
        and (item.get("data") or {}).get("record_type") == "owner_settings"
    ), {})


def summary_period_due(frequency: str, now: datetime) -> str | None:
    """Return an idempotency period once this week's/month's UTC send time passed."""
    instant = now.astimezone(timezone.utc)
    if frequency == "weekly":
        monday = (instant - timedelta(days=instant.weekday())).replace(
            hour=WEEKLY_SEND_HOUR_UTC, minute=0, second=0, microsecond=0,
        )
        if instant < monday:
            return None
        iso = instant.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    if frequency == "monthly":
        first = instant.replace(day=1, hour=MONTHLY_SEND_HOUR_UTC, minute=0, second=0, microsecond=0)
        if instant < first:
            return None
        return instant.strftime("%Y-%m")
    return None


def calculate_portfolio_summary(records: list[dict], owner_uid: str, period: str) -> dict:
    owned = [item for item in records if item.get("owner_uid") == owner_uid]
    listings = [item for item in owned if item.get("kind") == "listing"]
    listing_data = [item.get("data") or {} for item in listings]
    summary = {
        "period": period,
        "properties": len(listings),
        "occupied": sum(data.get("occupancy_status") == "occupied" for data in listing_data),
        "vacant": sum(data.get("occupancy_status") == "vacant" for data in listing_data),
        "not_tracked": sum(data.get("occupancy_status") not in {"occupied", "vacant"} for data in listing_data),
    }
    payments = [item.get("data") or {} for item in owned if item.get("kind") == "rent_payment"]
    if payments:
        collected = 0.0
        outstanding = 0.0
        for payment in payments:
            try:
                if payment.get("status") == "paid":
                    amount = float(payment.get("paid_amount", payment.get("amount", 0)) or 0)
                    if math.isfinite(amount) and amount > 0:
                        collected += amount
                elif payment.get("status") == "pending":
                    amount = float(payment.get("amount", 0) or 0)
                    if math.isfinite(amount) and amount > 0:
                        outstanding += amount
            except (TypeError, ValueError):
                continue
        summary["rent_collected"] = collected
        summary["rent_outstanding"] = outstanding
    return summary


def _notification_message(event: dict) -> tuple[str, str]:
    payload = event["payload"]
    event_type = event["event_type"]
    if event_type == "new_enquiry":
        prop = payload.get("property") or {}
        title = prop.get("title") or "your property"
        subject = f"New enquiry for {title}"
        body = "\n".join((
            "A renter submitted a new enquiry on RentAI.",
            f"Property: {title}",
            f"Location: {' · '.join(filter(None, [prop.get('locality'), prop.get('city')])) or 'Not provided'}",
            f"Renter: {payload.get('client_name') or 'Not provided'}",
            f"Renter email: {payload.get('client_email') or 'Not provided'}",
            f"Renter phone: {payload.get('client_phone') or 'Not provided'}",
            f"Message: {payload.get('message') or 'Not provided'}",
            f"Submitted at: {payload.get('created_at') or 'Not provided'}",
            "Open RentAI and go to Owner Workspace > Applications & Enquiries to respond.",
        ))
        return subject, body
    if event_type == "maintenance_status_changed":
        subject = f"Maintenance update: {payload.get('title') or 'Request'}"
        body = "\n".join((
            "A maintenance request status changed in RentAI.",
            f"Property: {payload.get('property_title') or 'Not provided'}",
            f"Issue: {payload.get('title') or 'Not provided'}",
            f"Description: {payload.get('description') or 'Not provided'}",
            f"Status: {payload.get('previous_status') or 'unknown'} → {payload.get('new_status') or 'unknown'}",
            f"Updated at: {payload.get('updated_at') or 'Not provided'}",
            "Open RentAI and go to Owner Workspace > Maintenance Requests for details.",
        ))
        return subject, body
    if event_type == "portfolio_summary":
        subject = f"Your RentAI portfolio summary ({payload.get('period')})"
        body_lines = [
            f"Portfolio snapshot for {payload.get('period')}.",
            f"Properties: {payload.get('properties', 0)}",
            f"Occupied: {payload.get('occupied', 0)}",
            f"Vacant: {payload.get('vacant', 0)}",
            f"Occupancy not tracked: {payload.get('not_tracked', 0)}",
        ]
        if "rent_collected" in payload:
            body_lines.append(f"Rent collected: ₹{payload['rent_collected']:,.2f}")
            body_lines.append(f"Outstanding rent: ₹{payload['rent_outstanding']:,.2f}")
        body_lines.append("Open RentAI Owner Workspace for portfolio details.")
        return subject, "\n".join(body_lines)
    raise ValueError("Unsupported notification type")


class NotificationWorker:
    def __init__(self, repository: SQLiteRepository | None = None, transport=None):
        self.repository = repository or SQLiteRepository()
        self.transport = transport

    def enqueue_due_summaries(self, now: datetime | None = None) -> int:
        now = now or datetime.now(timezone.utc)
        records = self.repository.list_records()
        events = []
        owner_uids = {
            item.get("owner_uid") for item in records
            if item.get("kind") == "profile"
            and (item.get("data") or {}).get("record_type") == "owner_settings"
        }
        for owner_uid in owner_uids:
            account = self.repository.account_by_uid(owner_uid) if owner_uid else None
            if not account or account.get("role") != "owner":
                continue
            settings = owner_settings(records, owner_uid)
            frequency = settings.get("summary_frequency", "weekly")
            period = summary_period_due(frequency, now)
            if not period:
                continue
            summary = calculate_portfolio_summary(records, owner_uid, period)
            events.append({
                "event_key": f"portfolio_summary:{owner_uid}:{period}",
                "owner_uid": owner_uid,
                "event_type": "portfolio_summary",
                "payload": {**summary, "frequency": frequency},
            })
        return self.repository.enqueue_notifications(events)

    def run_once(self, *, now: datetime | None = None, dry_run: bool = False) -> int:
        now = now or datetime.now(timezone.utc)
        self.enqueue_due_summaries(now)
        if dry_run:
            counts = self.repository.notification_counts()
            logger.info("Notification dry run; queued=%s sent=%s", counts.get("pending", 0), counts.get("sent", 0))
            return 0
        sent = 0
        while True:
            lock_token = uuid.uuid4().hex
            current_text = _utc_text(now)
            notification = self.repository.claim_notification(
                now=current_text,
                lease_until=_utc_text(now + timedelta(seconds=LEASE_SECONDS)),
                lock_token=lock_token,
            )
            if notification is None:
                break
            try:
                delivered = self._deliver(notification)
            except EmailConfigurationError as exc:
                logger.error("Email configuration unavailable; missing_variables=%s", ",".join(exc.missing))
                self._retry(notification, lock_token, now, "Email configuration is missing or invalid")
            except Exception as exc:
                logger.warning(
                    "Notification delivery failed; event_id=%s error_type=%s",
                    notification["id"], type(exc).__name__,
                )
                self._retry(notification, lock_token, now, "SMTP delivery failed")
            else:
                if delivered:
                    self.repository.mark_notification_sent(
                        notification["id"], lock_token, _utc_text(now),
                    )
                    sent += 1
        return sent

    def _deliver(self, notification: dict) -> None:
        owner_uid = notification["owner_uid"]
        account = self.repository.account_by_uid(owner_uid)
        if not account or account.get("role") != "owner" or not account.get("email"):
            raise RuntimeError("Owner recipient is unavailable")
        settings = owner_settings(self.repository.list_records(), owner_uid)
        event_type = notification["event_type"]
        if event_type == "portfolio_summary":
            frequency = settings.get("summary_frequency", "weekly")
            if frequency == "off" or frequency != notification["payload"].get("frequency"):
                self.repository.mark_notification_skipped(notification["id"], notification["lock_token"], "Owner summary preference changed")
                return False
        subject, body = _notification_message(notification)
        transport = self.transport or SMTPEmailTransport()
        transport.send(account["email"], subject, body, event_id=notification["id"])
        return True

    def _retry(self, notification: dict, lock_token: str, now: datetime, safe_error: str) -> None:
        attempts = max(1, int(notification.get("attempts", 1)))
        backoff_seconds = min(6 * 60 * 60, 30 * (2 ** min(attempts - 1, 10)))
        self.repository.retry_notification(
            notification["id"], lock_token,
            next_attempt_at=_utc_text(now + timedelta(seconds=backoff_seconds)),
            error=safe_error,
        )


def run_forever(*, poll_seconds: int = POLL_SECONDS) -> None:
    worker = NotificationWorker()
    try:
        get_smtp_settings()
    except EmailConfigurationError as exc:
        logger.error("Email configuration unavailable; %s", str(exc))
    logger.info("RentAI notification worker started; schedule timezone=UTC")
    while True:
        worker.run_once()
        time.sleep(poll_seconds)


