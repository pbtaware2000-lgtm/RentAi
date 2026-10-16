"""SQLite storage for RentAI accounts, sessions, and application records."""
import hashlib
import json
import os
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from backend.core.config import PROJECT_ROOT


def resolve_database_path(value: str | os.PathLike | None = None, *, project_root: Path = PROJECT_ROOT) -> Path:
    """Resolve configured relative SQLite paths from the project root, not CWD."""
    configured = os.getenv("RENTAI_DATABASE_PATH") if value is None else value
    if configured is None or not str(configured).strip():
        configured = project_root / "data" / "rentai.sqlite3"
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


DB_PATH = resolve_database_path()
VALID_ROLES = {"owner", "renter", "broker", "admin"}


class DuplicateBusinessRecordError(ValueError):
    """Raised when a tenancy or rent period already exists."""


@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        yield db
        db.commit()
    finally:
        db.close()


def initialize_database():
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            uid TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            name TEXT NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY, uid TEXT NOT NULL REFERENCES users(uid) ON DELETE CASCADE,
            expires_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS application_records (
            id TEXT PRIMARY KEY, owner_uid TEXT NOT NULL, kind TEXT NOT NULL,
            data TEXT NOT NULL, assigned_broker_uids TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS application_images (
            id TEXT PRIMARY KEY, owner_uid TEXT NOT NULL, uploader_uid TEXT NOT NULL,
            record_id TEXT NOT NULL, url TEXT NOT NULL, format TEXT, width INTEGER, height INTEGER,
            public_id TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS notification_outbox (
            id TEXT PRIMARY KEY, event_key TEXT NOT NULL UNIQUE,
            owner_uid TEXT NOT NULL, event_type TEXT NOT NULL, payload TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at TEXT NOT NULL, locked_until TEXT, lock_token TEXT,
            last_error TEXT, created_at TEXT NOT NULL, sent_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_notification_outbox_due
            ON notification_outbox(status, next_attempt_at, locked_until);
        """)


def _hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


class SQLiteRepository:
    def create_account(self, email, name, password_hash, role):
        uid = uuid.uuid4().hex
        with connect() as db:
            db.execute("INSERT INTO users(uid,email,name,password_hash,role) VALUES(?,?,?,?,?)", (uid,email.lower(),name,password_hash,role))
        return uid

    def create_first_admin_account(self, email, name, password_hash):
        """Create the first Admin atomically for the local database owner."""
        uid = uuid.uuid4().hex
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM users WHERE role='admin' LIMIT 1").fetchone():
                raise ValueError("An Admin account already exists")
            db.execute(
                "INSERT INTO users(uid,email,name,password_hash,role) VALUES(?,?,?,?, 'admin')",
                (uid, email.lower(), name, password_hash),
            )
        return uid

    def account_by_email(self, email):
        with connect() as db:
            row = db.execute("SELECT * FROM users WHERE email=? COLLATE NOCASE", (email,)).fetchone()
            return dict(row) if row else None

    def account_by_uid(self, uid):
        with connect() as db:
            row = db.execute("SELECT uid,email,name,role FROM users WHERE uid=?", (uid,)).fetchone()
            return dict(row) if row else None

    def issue_session(self, uid):
        token = secrets.token_urlsafe(36)
        with connect() as db:
            db.execute("INSERT INTO sessions(token_hash,uid,expires_at) VALUES(?,?,datetime('now','+30 days'))", (_hash_token(token),uid))
        return token

    def user_for_token(self, token):
        with connect() as db:
            row = db.execute("SELECT u.uid,u.email,u.name,u.role FROM sessions s JOIN users u ON u.uid=s.uid WHERE s.token_hash=? AND s.expires_at>datetime('now')", (_hash_token(token),)).fetchone()
            return dict(row) if row else None

    def revoke_session(self, token):
        with connect() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (_hash_token(token),))

    def create(self, collection, data):
        if collection == "application_images":
            image_id=uuid.uuid4().hex
            with connect() as db:
                db.execute("INSERT INTO application_images(id,owner_uid,uploader_uid,record_id,url,format,width,height,public_id) VALUES(?,?,?,?,?,?,?,?,?)",(image_id,data["owner_uid"],data["uploader_uid"],data["record_id"],data["url"],data.get("format"),data.get("width"),data.get("height"),data["public_id"]))
            return image_id,data
        if collection != "application_records": raise ValueError("Unsupported collection")
        record_id = uuid.uuid4().hex
        with connect() as db:
            kind = data.get("kind")
            payload = data.get("data", {})
            if kind in {"tenancy", "rent_payment"}:
                db.execute("BEGIN IMMEDIATE")
                for row in db.execute("SELECT kind,data FROM application_records WHERE kind=?", (kind,)):
                    existing = json.loads(row["data"])
                    duplicate = (
                        kind == "tenancy"
                        and existing.get("inquiry_id") == payload.get("inquiry_id")
                        and existing.get("status") == "active"
                    ) or (
                        kind == "rent_payment"
                        and existing.get("tenancy_id") == payload.get("tenancy_id")
                        and existing.get("period") == payload.get("period")
                    )
                    if duplicate:
                        raise DuplicateBusinessRecordError("A matching tenancy or rent period already exists")
            db.execute("INSERT INTO application_records(id,owner_uid,kind,data,assigned_broker_uids) VALUES(?,?,?,?,?)", (record_id,data["owner_uid"],data["kind"],json.dumps(data.get("data",{})),json.dumps(data.get("assigned_broker_uids",[]))))
        return record_id, data

    @staticmethod
    def _utc_now_text():
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _insert_notification(db, event: dict, *, record_id: str | None = None) -> None:
        now = SQLiteRepository._utc_now_text()
        payload = dict(event.get("payload") or {})
        if record_id:
            payload.setdefault("record_id", record_id)
        event_key = event.get("event_key") or f"{event['event_type']}:{record_id or uuid.uuid4().hex}"
        db.execute(
            """INSERT OR IGNORE INTO notification_outbox
               (id,event_key,owner_uid,event_type,payload,next_attempt_at,created_at)
               VALUES(?,?,?,?,?,?,?)""",
            (uuid.uuid4().hex, event_key, event["owner_uid"], event["event_type"],
             json.dumps(payload), now, now),
        )

    def create_with_notification(
        self, data: dict, notification: dict | None = None, *, prevent_duplicate_enquiry: bool = False,
    ):
        """Persist a record and its notification event atomically."""
        record_id = uuid.uuid4().hex
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if prevent_duplicate_enquiry:
                for row in db.execute(
                    "SELECT data FROM application_records WHERE kind='inquiry'"
                ):
                    existing = json.loads(row["data"])
                    if (
                        existing.get("client_uid") == data["data"].get("client_uid")
                        and existing.get("listing_id") == data["data"].get("listing_id")
                        and existing.get("status", "new") in {
                            "new", "pending", "contacted", "viewing_scheduled", "accepted",
                        }
                    ):
                        raise DuplicateBusinessRecordError("An active enquiry already exists")
            db.execute(
                "INSERT INTO application_records(id,owner_uid,kind,data,assigned_broker_uids) VALUES(?,?,?,?,?)",
                (record_id, data["owner_uid"], data["kind"], json.dumps(data.get("data", {})),
                 json.dumps(data.get("assigned_broker_uids", []))),
            )
            if notification:
                self._insert_notification(db, notification, record_id=record_id)
        return record_id, data

    def update_maintenance_with_notification(
        self, record_id: str, expected_status: str, data: dict,
        notification: dict | None = None,
    ) -> tuple[str, dict | None]:
        """Atomically update maintenance and enqueue a status-change event once."""
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM application_records WHERE id=? AND kind='maintenance'", (record_id,),
            ).fetchone()
            if row is None:
                return "missing", None
            current = self._record(row)
            current_data = current.get("data") or {}
            requested_status = data.get("status")
            current_status = current_data.get("status", "open")
            if current_status not in {expected_status, requested_status}:
                return "conflict", current
            db.execute(
                "UPDATE application_records SET data=? WHERE id=?",
                (json.dumps(data), record_id),
            )
            if current_status != requested_status and notification:
                self._insert_notification(db, notification, record_id=record_id)
            return "updated", {**current, "data": data}

    def enqueue_notifications(self, events: list[dict]) -> int:
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            before = db.total_changes
            for event in events:
                self._insert_notification(db, event)
            return db.total_changes - before

    def claim_notification(self, *, now: str, lease_until: str, lock_token: str) -> dict | None:
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT * FROM notification_outbox
                   WHERE (status='pending' AND next_attempt_at<=?)
                      OR (status='sending' AND locked_until<=?)
                   ORDER BY created_at,id LIMIT 1""",
                (now, now),
            ).fetchone()
            if row is None:
                return None
            db.execute(
                """UPDATE notification_outbox
                   SET status='sending', attempts=attempts+1, locked_until=?, lock_token=?
                   WHERE id=?""",
                (lease_until, lock_token, row["id"]),
            )
            claimed = db.execute(
                "SELECT * FROM notification_outbox WHERE id=?", (row["id"],),
            ).fetchone()
            return self._notification(claimed)

    @staticmethod
    def _notification(row):
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        return result

    def mark_notification_sent(self, notification_id: str, lock_token: str, sent_at: str) -> bool:
        with connect() as db:
            return db.execute(
                """UPDATE notification_outbox
                   SET status='sent',sent_at=?,locked_until=NULL,lock_token=NULL,last_error=NULL
                   WHERE id=? AND status='sending' AND lock_token=?""",
                (sent_at, notification_id, lock_token),
            ).rowcount == 1

    def mark_notification_skipped(self, notification_id: str, lock_token: str, reason: str) -> bool:
        with connect() as db:
            return db.execute(
                """UPDATE notification_outbox
                   SET status='skipped',last_error=?,locked_until=NULL,lock_token=NULL
                   WHERE id=? AND status='sending' AND lock_token=?""",
                (reason[:200], notification_id, lock_token),
            ).rowcount == 1

    def retry_notification(
        self, notification_id: str, lock_token: str, *, next_attempt_at: str, error: str,
    ) -> bool:
        with connect() as db:
            return db.execute(
                """UPDATE notification_outbox
                   SET status='pending',next_attempt_at=?,last_error=?,locked_until=NULL,lock_token=NULL
                   WHERE id=? AND status='sending' AND lock_token=?""",
                (next_attempt_at, error[:200], notification_id, lock_token),
            ).rowcount == 1

    def notification_counts(self) -> dict[str, int]:
        with connect() as db:
            return {row["status"]: row["total"] for row in db.execute(
                "SELECT status,COUNT(*) AS total FROM notification_outbox GROUP BY status"
            )}

    def list_notifications(self) -> list[dict]:
        """Internal worker/test inspection; no public API exposes outbox contents."""
        with connect() as db:
            return [self._notification(row) for row in db.execute(
                "SELECT * FROM notification_outbox ORDER BY created_at,id"
            )]

    def _record(self, row):
        if not row: return None
        result=dict(row); result["data"]=json.loads(result["data"]); result["assigned_broker_uids"]=json.loads(result["assigned_broker_uids"]); return result

    def get(self, collection, record_id):
        with connect() as db: return self._record(db.execute("SELECT * FROM application_records WHERE id=?",(record_id,)).fetchone())

    def update(self, collection, record_id, data):
        if "data" in data:
            with connect() as db: return db.execute("UPDATE application_records SET data=? WHERE id=?",(json.dumps(data["data"]),record_id)).rowcount>0
        return False

    def attach_rent_order_if_pending(self, record_id, order_data):
        """Attach one provider order to a pending rent record under a write lock."""
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT kind,data FROM application_records WHERE id=?", (record_id,)
            ).fetchone()
            if not row or row["kind"] != "rent_payment":
                return None
            data = json.loads(row["data"])
            if data.get("status") != "pending":
                return None
            existing = data.get("razorpay_order")
            if isinstance(existing, dict) and existing.get("id"):
                return existing
            data["razorpay_order"] = order_data
            db.execute(
                "UPDATE application_records SET data=? WHERE id=?",
                (json.dumps(data), record_id),
            )
            return order_data

    def mark_rent_paid_if_pending(self, record_id, order_id, payment_data):
        """Atomically mark a matching pending order paid; repeated callback is safe."""
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT kind,data FROM application_records WHERE id=?", (record_id,)
            ).fetchone()
            if not row or row["kind"] != "rent_payment":
                return "missing"
            data = json.loads(row["data"])
            if data.get("status") == "paid":
                return "already_paid" if data.get("razorpay_payment_id") == payment_data.get("razorpay_payment_id") else "conflict"
            if data.get("status") != "pending" or (data.get("razorpay_order") or {}).get("id") != order_id:
                return "conflict"
            db.execute(
                "UPDATE application_records SET data=? WHERE id=?",
                (json.dumps(payment_data), record_id),
            )
            return "paid"

    def delete(self, collection, record_id):
        table="application_images" if collection=="application_images" else "application_records"
        with connect() as db: return db.execute(f"DELETE FROM {table} WHERE id=?",(record_id,)).rowcount>0

    def delete_listing_if_unreferenced(self, record_id):
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            listing = db.execute(
                "SELECT kind FROM application_records WHERE id=?", (record_id,)
            ).fetchone()
            if listing is None or listing["kind"] != "listing":
                return "missing"
            if db.execute(
                "SELECT 1 FROM application_images WHERE record_id=? LIMIT 1", (record_id,)
            ).fetchone():
                return "linked"
            rows = db.execute(
                "SELECT kind,data FROM application_records WHERE id<>?", (record_id,)
            ).fetchall()
            for row in rows:
                try:
                    data = json.loads(row["data"])
                except (TypeError, json.JSONDecodeError):
                    return "linked"
                if row["kind"] in {"inquiry", "maintenance", "tenancy", "rent_payment"} and data.get("listing_id") == record_id:
                    return "linked"
                if (
                    row["kind"] == "favorite"
                    and data.get("source") == "owner_listing"
                    and data.get("property_id") == record_id
                ):
                    return "linked"
            db.execute("DELETE FROM application_records WHERE id=?", (record_id,))
            return "deleted"

    def list_records(self):
        with connect() as db: return [self._record(row) for row in db.execute("SELECT * FROM application_records")]

    def list_images(self):
        with connect() as db: return [dict(row) for row in db.execute("SELECT * FROM application_images ORDER BY rowid DESC")]

    def get_user_role(self, uid):
        user=self.account_by_uid(uid); return user["role"] if user else None

    def set_user_role(self, uid, role):
        with connect() as db: db.execute("UPDATE users SET role=? WHERE uid=?",(role,uid))

    def create_user_role(self, uid, role):
        with connect() as db: return db.execute("UPDATE users SET role=? WHERE uid=? AND role='pending'",(role,uid)).rowcount==1

    def list_user_roles(self):
        with connect() as db: return [dict(r) for r in db.execute("SELECT uid,email,name,role FROM users")]

    def assign_broker(self, record_id, broker_uid):
        with connect() as db:
            row=db.execute("SELECT assigned_broker_uids FROM application_records WHERE id=?",(record_id,)).fetchone()
            if not row: return None
            assigned=set(json.loads(row[0])); assigned.add(broker_uid); result=sorted(assigned)
            db.execute("UPDATE application_records SET assigned_broker_uids=? WHERE id=?",(json.dumps(result),record_id)); return result
