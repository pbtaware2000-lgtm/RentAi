"""SQLite storage for RentAI accounts, sessions, and application records."""
import hashlib
import json
import os
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
<<<<<<< HEAD
=======
from datetime import datetime, timezone
>>>>>>> origin/development
from pathlib import Path

from backend.core.config import PROJECT_ROOT

<<<<<<< HEAD
DB_PATH = Path(os.getenv("RENTAI_DATABASE_PATH", PROJECT_ROOT / "data" / "rentai.sqlite3"))
VALID_ROLES = {"owner", "renter", "broker", "admin"}


=======

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


>>>>>>> origin/development
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
<<<<<<< HEAD
=======
        CREATE TABLE IF NOT EXISTS chatbot_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT NOT NULL REFERENCES users(uid) ON DELETE CASCADE,
            role TEXT NOT NULL CHECK(role IN ('user','assistant')),
            content TEXT NOT NULL,
            mode TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_chatbot_messages_uid_id ON chatbot_messages(uid, id);
>>>>>>> origin/development
        CREATE TABLE IF NOT EXISTS application_images (
            id TEXT PRIMARY KEY, owner_uid TEXT NOT NULL, uploader_uid TEXT NOT NULL,
            record_id TEXT NOT NULL, url TEXT NOT NULL, format TEXT, width INTEGER, height INTEGER,
            public_id TEXT NOT NULL
        );
<<<<<<< HEAD
        CREATE TABLE IF NOT EXISTS property_approval_requests (
            request_id TEXT PRIMARY KEY,
            property_id TEXT NOT NULL UNIQUE REFERENCES application_records(id) ON DELETE CASCADE,
            submitted_by_uid TEXT NOT NULL REFERENCES users(uid),
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected')),
            review_notes TEXT NOT NULL DEFAULT '',
            reviewed_by_uid TEXT REFERENCES users(uid),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            reviewed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_approval_status_created
            ON property_approval_requests(status, created_at);
        CREATE TABLE IF NOT EXISTS support_tickets (
            ticket_id TEXT PRIMARY KEY,
            user_uid TEXT NOT NULL REFERENCES users(uid),
            subject TEXT NOT NULL,
            description TEXT NOT NULL,
            priority TEXT NOT NULL DEFAULT 'normal' CHECK(priority IN ('low','normal','high','urgent')),
            status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','in_progress','resolved','closed')),
            admin_response TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS ix_support_status_created ON support_tickets(status, created_at);
        CREATE TABLE IF NOT EXISTS prediction_history (
            prediction_id TEXT PRIMARY KEY,
            actor_uid TEXT NOT NULL REFERENCES users(uid),
            inputs TEXT NOT NULL,
            predicted_rent REAL NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS activity_logs (
            log_id TEXT PRIMARY KEY,
            actor_uid TEXT REFERENCES users(uid) ON DELETE SET NULL,
            action TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_id TEXT NOT NULL,
            metadata TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS ix_activity_created ON activity_logs(created_at);
        CREATE TABLE IF NOT EXISTS app_reviews (
            review_id TEXT PRIMARY KEY,
            user_uid TEXT NOT NULL UNIQUE REFERENCES users(uid) ON DELETE CASCADE,
            reviewer_name TEXT NOT NULL DEFAULT '',
            role TEXT NOT NULL DEFAULT 'owner' CHECK(role IN ('owner','renter')),
            rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
            feedback TEXT NOT NULL CHECK(length(trim(feedback)) BETWEEN 1 AND 4000),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS ix_app_reviews_created
            ON app_reviews(created_at);
        """)
        user_columns = {row[1] for row in db.execute("PRAGMA table_info(users)")}
        if "account_status" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN account_status TEXT NOT NULL DEFAULT 'active'")
        review_columns = {row[1] for row in db.execute("PRAGMA table_info(app_reviews)")}
        if "reviewer_name" not in review_columns:
            db.execute("ALTER TABLE app_reviews ADD COLUMN reviewer_name TEXT NOT NULL DEFAULT ''")
            db.execute(
                "UPDATE app_reviews SET reviewer_name=COALESCE((SELECT name FROM users WHERE users.uid=app_reviews.user_uid),'')"
            )
        if "role" not in review_columns:
            db.execute("ALTER TABLE app_reviews ADD COLUMN role TEXT NOT NULL DEFAULT 'owner'")
            db.execute(
                "UPDATE app_reviews SET role=COALESCE((SELECT role FROM users WHERE users.uid=app_reviews.user_uid),role)"
            )
        db.execute("CREATE INDEX IF NOT EXISTS ix_users_role_created ON users(role, created_at)")
=======
        CREATE TABLE IF NOT EXISTS bookings (
            id TEXT PRIMARY KEY, property_id TEXT NOT NULL REFERENCES application_records(id),
            renter_uid TEXT NOT NULL REFERENCES users(uid), owner_uid TEXT NOT NULL REFERENCES users(uid),
            move_in_date TEXT NOT NULL, monthly_rent REAL NOT NULL, security_deposit REAL NOT NULL DEFAULT 0,
            amount_due INTEGER NOT NULL, currency TEXT NOT NULL DEFAULT 'INR',
            status TEXT NOT NULL, payment_status TEXT NOT NULL,
            availability_conflict INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_bookings_renter ON bookings(renter_uid, created_at);
        CREATE INDEX IF NOT EXISTS idx_bookings_owner ON bookings(owner_uid, created_at);
        CREATE INDEX IF NOT EXISTS idx_bookings_property_status ON bookings(property_id, status);
        CREATE TABLE IF NOT EXISTS booking_payments (
            id TEXT PRIMARY KEY, booking_id TEXT NOT NULL REFERENCES bookings(id),
            provider TEXT NOT NULL DEFAULT 'razorpay', payment_link_id TEXT UNIQUE,
            razorpay_order_id TEXT UNIQUE,
            transaction_id TEXT UNIQUE, amount INTEGER NOT NULL, currency TEXT NOT NULL DEFAULT 'INR',
            status TEXT NOT NULL, checkout_url TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_booking_payments_booking ON booking_payments(booking_id, created_at);
        CREATE TABLE IF NOT EXISTS payment_webhook_events (
            event_id TEXT PRIMARY KEY, received_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS booking_notifications (
            id TEXT PRIMARY KEY, booking_id TEXT NOT NULL REFERENCES bookings(id),
            recipient_uid TEXT NOT NULL REFERENCES users(uid), event_type TEXT NOT NULL,
            message TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(booking_id, recipient_uid, event_type)
        );
        CREATE INDEX IF NOT EXISTS idx_booking_notifications_recipient ON booking_notifications(recipient_uid, created_at);
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
        payment_columns = {row["name"] for row in db.execute("PRAGMA table_info(booking_payments)")}
        if "razorpay_order_id" not in payment_columns:
            db.execute("ALTER TABLE booking_payments ADD COLUMN razorpay_order_id TEXT")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_booking_payments_razorpay_order ON booking_payments(razorpay_order_id)")
        chatbot_columns = {row["name"] for row in db.execute("PRAGMA table_info(chatbot_messages)")}
        if "mode" not in chatbot_columns:
            db.execute("ALTER TABLE chatbot_messages ADD COLUMN mode TEXT")
>>>>>>> origin/development


def _hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


class SQLiteRepository:
<<<<<<< HEAD
=======
    def chatbot_history(self, uid, limit=20):
        with connect() as db:
            rows = db.execute(
                "SELECT role,content,mode,created_at FROM chatbot_messages WHERE uid=? ORDER BY id DESC LIMIT ?",
                (uid, limit),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def add_chatbot_message(self, uid, role, content, mode=None):
        with connect() as db:
            db.execute("INSERT INTO chatbot_messages(uid,role,content,mode) VALUES(?,?,?,?)", (uid, role, content, mode))
            db.execute(
                "DELETE FROM chatbot_messages WHERE uid=? AND id NOT IN "
                "(SELECT id FROM chatbot_messages WHERE uid=? ORDER BY id DESC LIMIT 40)",
                (uid, uid),
            )

    def clear_chatbot_history(self, uid):
        with connect() as db:
            db.execute("DELETE FROM chatbot_messages WHERE uid=?", (uid,))

>>>>>>> origin/development
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
<<<<<<< HEAD
            row = db.execute("SELECT u.uid,u.email,u.name,u.role FROM sessions s JOIN users u ON u.uid=s.uid WHERE s.token_hash=? AND s.expires_at>datetime('now') AND u.account_status='active'", (_hash_token(token),)).fetchone()
=======
            row = db.execute("SELECT u.uid,u.email,u.name,u.role FROM sessions s JOIN users u ON u.uid=s.uid WHERE s.token_hash=? AND s.expires_at>datetime('now')", (_hash_token(token),)).fetchone()
>>>>>>> origin/development
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
<<<<<<< HEAD
            db.execute("INSERT INTO application_records(id,owner_uid,kind,data,assigned_broker_uids) VALUES(?,?,?,?,?)", (record_id,data["owner_uid"],data["kind"],json.dumps(data.get("data",{})),json.dumps(data.get("assigned_broker_uids",[]))))
        return record_id, data

    def create_listing_with_approval(self, data):
        """Persist a submitted listing and its review queue entry atomically."""
        record_id = uuid.uuid4().hex
        request_id = uuid.uuid4().hex
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO application_records(id,owner_uid,kind,data,assigned_broker_uids) VALUES(?,?,'listing',?,'[]')",
                (record_id, data["owner_uid"], json.dumps(data["data"])),
            )
            db.execute(
                "INSERT INTO property_approval_requests(request_id,property_id,submitted_by_uid) VALUES(?,?,?)",
                (request_id, record_id, data["owner_uid"]),
            )
        return record_id, request_id
=======
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
>>>>>>> origin/development

    def _record(self, row):
        if not row: return None
        result=dict(row); result["data"]=json.loads(result["data"]); result["assigned_broker_uids"]=json.loads(result["assigned_broker_uids"]); return result

    def get(self, collection, record_id):
        with connect() as db: return self._record(db.execute("SELECT * FROM application_records WHERE id=?",(record_id,)).fetchone())

    def update(self, collection, record_id, data):
        if "data" in data:
            with connect() as db: return db.execute("UPDATE application_records SET data=? WHERE id=?",(json.dumps(data["data"]),record_id)).rowcount>0
        return False

<<<<<<< HEAD
=======
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

>>>>>>> origin/development
    def delete(self, collection, record_id):
        table="application_images" if collection=="application_images" else "application_records"
        with connect() as db: return db.execute(f"DELETE FROM {table} WHERE id=?",(record_id,)).rowcount>0

<<<<<<< HEAD
=======
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

>>>>>>> origin/development
    def list_records(self):
        with connect() as db: return [self._record(row) for row in db.execute("SELECT * FROM application_records")]

    def list_images(self):
        with connect() as db: return [dict(row) for row in db.execute("SELECT * FROM application_images")]
<<<<<<< HEAD

    def get_user_role(self, uid):
        with connect() as db:
            row = db.execute("SELECT role FROM users WHERE uid=? AND account_status='active'", (uid,)).fetchone()
            return row["role"] if row else None
=======
        with connect() as db: return [dict(row) for row in db.execute("SELECT * FROM application_images ORDER BY rowid DESC")]

    def get_user_role(self, uid):
        user=self.account_by_uid(uid); return user["role"] if user else None
>>>>>>> origin/development

    def set_user_role(self, uid, role):
        with connect() as db: db.execute("UPDATE users SET role=? WHERE uid=?",(role,uid))

    def create_user_role(self, uid, role):
        with connect() as db: return db.execute("UPDATE users SET role=? WHERE uid=? AND role='pending'",(role,uid)).rowcount==1

    def list_user_roles(self):
<<<<<<< HEAD
        with connect() as db:
            return [dict(r) for r in db.execute(
                "SELECT uid,email,name,role,account_status,created_at FROM users "
                "WHERE role IN ('admin','owner','renter') ORDER BY created_at DESC"
            )]

    def admin_dashboard_stats(self):
        with connect() as db:
            roles = {row["role"]: row["n"] for row in db.execute(
                "SELECT role,COUNT(*) n FROM users WHERE role IN ('admin','owner','renter') GROUP BY role"
            )}
            listing_statuses = {}
            visible_listings = set()
            for row in db.execute(
                "SELECT r.id,r.data FROM application_records r "
                "LEFT JOIN users u ON u.uid=r.owner_uid WHERE r.kind='listing' "
                "AND (u.role IN ('admin','owner','renter') OR u.role IS NULL)"
            ):
                try:
                    listing_data = json.loads(row["data"])
                    status = listing_data.get("status", "unknown")
                except (TypeError, json.JSONDecodeError):
                    status = "unknown"
                    listing_data = {}
                visible_listings.add(row["id"])
                listing_statuses[status] = listing_statuses.get(status, 0) + 1
            inquiry_statuses = {}
            visible_inquiries = 0
            for row in db.execute(
                "SELECT r.data FROM application_records r JOIN users u ON u.uid=r.owner_uid "
                "WHERE r.kind='inquiry' AND u.role IN ('admin','owner','renter')"
            ):
                try:
                    inquiry = json.loads(row["data"])
                    if inquiry.get("listing_id") not in visible_listings:
                        continue
                    status = inquiry.get("status", "new")
                except (TypeError, json.JSONDecodeError):
                    status = "unknown"
                    continue
                visible_inquiries += 1
                inquiry_statuses[status] = inquiry_statuses.get(status, 0) + 1
            support = {row["status"]: row["n"] for row in db.execute(
                "SELECT t.status,COUNT(*) n FROM support_tickets t "
                "JOIN users u ON u.uid=t.user_uid "
                "WHERE u.role IN ('admin','owner','renter') GROUP BY t.status"
            )}
            approval = {row["status"]: row["n"] for row in db.execute(
                "SELECT a.status,COUNT(*) n FROM property_approval_requests a "
                "JOIN users u ON u.uid=a.submitted_by_uid "
                "WHERE u.role IN ('admin','owner','renter') GROUP BY a.status"
            )}
            return {
                "users_total": sum(roles.values()), "users_by_role": roles,
                "listings_total": len(visible_listings),
                "listing_statuses": listing_statuses,
                "pending_approvals": approval.get("pending", 0),
                "rental_requests_total": visible_inquiries,
                "rental_request_statuses": inquiry_statuses,
                "active_rental_requests": sum(inquiry_statuses.get(status, 0) for status in ("new", "contacted", "viewing_scheduled")),
                "bookings_total": None,
                "support_open": support.get("open", 0) + support.get("in_progress", 0),
                "support_statuses": support,
                "predictions_total": db.execute("SELECT COUNT(*) FROM prediction_history").fetchone()[0],
            }

    def upsert_app_review(self, user_uid, reviewer_name, role, rating, feedback):
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT review_id FROM app_reviews WHERE user_uid=?", (user_uid,)
            ).fetchone()
            if row:
                review_id = row["review_id"]
                db.execute(
                    "UPDATE app_reviews SET reviewer_name=?,role=?,rating=?,feedback=?,updated_at=CURRENT_TIMESTAMP WHERE user_uid=?",
                    (reviewer_name, role, rating, feedback, user_uid),
                )
            else:
                review_id = uuid.uuid4().hex
                db.execute(
                    "INSERT INTO app_reviews(review_id,user_uid,reviewer_name,role,rating,feedback) VALUES(?,?,?,?,?,?)",
                    (review_id, user_uid, reviewer_name, role, rating, feedback),
                )
            return review_id

    def app_review_by_user(self, user_uid):
        with connect() as db:
            row = db.execute(
                "SELECT r.review_id,r.user_uid,r.reviewer_name,r.role,r.rating,r.feedback,r.created_at,r.updated_at "
                "FROM app_reviews r WHERE r.user_uid=?",
                (user_uid,),
            ).fetchone()
            return dict(row) if row else None

    def list_app_reviews(self, *, rating=None, role=None, search=None):
        sql = (
            "SELECT r.review_id,r.reviewer_name,r.role,r.rating,r.feedback,r.created_at,r.updated_at "
            "FROM app_reviews r JOIN users u ON u.uid=r.user_uid"
        )
        conditions, params = ["r.role IN ('owner','renter')"], []
        if rating is not None:
            conditions.append("r.rating=?")
            params.append(rating)
        if role in {"owner", "renter"}:
            conditions.append("r.role=?")
            params.append(role)
        if search:
            conditions.append("(u.name LIKE ? OR r.feedback LIKE ?)")
            term = f"%{search.strip()}%"
            params.extend((term, term))
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY r.created_at DESC, r.review_id DESC"
        with connect() as db:
            return [dict(row) for row in db.execute(sql, params)]

    def app_review_summary(self):
        with connect() as db:
            row = db.execute(
                "SELECT COUNT(*) AS total_reviews, AVG(rating) AS average_rating "
                "FROM app_reviews WHERE role IN ('owner','renter')"
            ).fetchone()
            return {
                "total_reviews": row["total_reviews"],
                "average_rating": round(row["average_rating"], 2) if row["average_rating"] is not None else None,
            }

    def list_admin_records(self, kind=None):
        with connect() as db:
            sql = "SELECT r.*,u.name AS submitter_name,u.email AS submitter_email,u.role AS submitter_role FROM application_records r LEFT JOIN users u ON u.uid=r.owner_uid WHERE (u.role IN ('admin','owner','renter') OR u.role IS NULL)"
            params = ()
            if kind:
                sql += " AND r.kind=?"
                params = (kind,)
            sql += " ORDER BY r.created_at DESC"
            rows = [self._record(row) for row in db.execute(sql, params)]
            listings = {
                row["id"] for row in db.execute(
                    "SELECT r.id FROM application_records r JOIN users u ON u.uid=r.owner_uid "
                    "WHERE r.kind='listing' AND (u.role IN ('admin','owner','renter') OR u.role IS NULL)"
                )
            }
            rows = [row for row in rows if row.get("kind") != "inquiry"
                    or (row.get("data") or {}).get("listing_id") in listings]
            for row in rows:
                row.pop("assigned_broker_uids", None)
                data = row.get("data")
                if isinstance(data, dict):
                    row["data"] = {
                        key: value for key, value in data.items()
                        if not key.casefold().startswith("broker")
                    }
            return rows

    def set_account_status(self, actor_uid, uid, status):
        if status not in {"active", "deactivated"}:
            raise ValueError("Account status must be active or deactivated")
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT role,account_status FROM users WHERE uid=?", (uid,)).fetchone()
            if row is None:
                raise LookupError("Account not found")
            if actor_uid == uid and status != "active":
                raise ValueError("You cannot deactivate your own Admin account")
            if row["role"] == "admin" and status != "active":
                active_admins = db.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND account_status='active'").fetchone()[0]
                if active_admins <= 1:
                    raise ValueError("The last active Admin account cannot be deactivated")
            db.execute("UPDATE users SET account_status=? WHERE uid=?", (status, uid))
            if status == "deactivated":
                db.execute("DELETE FROM sessions WHERE uid=?", (uid,))

    def change_user_role(self, actor_uid, uid, role):
        if role not in VALID_ROLES:
            raise ValueError("Unsupported role")
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT role,account_status FROM users WHERE uid=?", (uid,)).fetchone()
            if row is None:
                raise LookupError("Account not found")
            if actor_uid == uid and role != "admin":
                raise ValueError("You cannot remove your own Admin access")
            if row["role"] == "admin" and row["account_status"] == "active" and role != "admin":
                active_admins = db.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND account_status='active'").fetchone()[0]
                if active_admins <= 1:
                    raise ValueError("The last active Admin cannot be changed to another role")
            db.execute("UPDATE users SET role=? WHERE uid=?", (role, uid))

    def get_registered_user_monthly_counts(self):
        with connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT substr(created_at,1,7) AS month,COUNT(*) AS users "
                "FROM users WHERE role IN ('admin','owner','renter') "
                "GROUP BY substr(created_at,1,7) ORDER BY month"
            )]

    def create_approval_request(self, property_id, submitted_by_uid):
        with connect() as db:
            request_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO property_approval_requests(request_id,property_id,submitted_by_uid) VALUES(?,?,?)",
                (request_id, property_id, submitted_by_uid),
            )
            return request_id

    def list_approval_requests(self, status=None, *, exclude_broker=False):
        with connect() as db:
            sql = """SELECT a.*,r.data AS property_data,u.name AS submitter_name,u.email AS submitter_email
                     FROM property_approval_requests a
                     JOIN application_records r ON r.id=a.property_id
                     JOIN users u ON u.uid=a.submitted_by_uid"""
            clauses = []
            params = ()
            if exclude_broker:
                clauses.append("u.role IN ('admin','owner','renter')")
            if status:
                clauses.append("a.status=?")
                params = (status,)
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY a.created_at DESC"
            results = []
            for row in db.execute(sql, params):
                item = dict(row)
                item["owner_uid"] = item["submitted_by_uid"]
                try:
                    item["property"] = json.loads(item.pop("property_data"))
                except (TypeError, json.JSONDecodeError):
                    item["property"] = {}
                results.append(item)
            return results

    def review_property(self, property_id, reviewer_uid, status, notes):
        if status not in {"approved", "rejected"}:
            raise ValueError("Review status must be approved or rejected")
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            approval = db.execute(
                "SELECT a.request_id,a.status,u.role AS submitter_role "
                "FROM property_approval_requests a JOIN users u ON u.uid=a.submitted_by_uid "
                "WHERE a.property_id=?", (property_id,)
            ).fetchone()
            if approval is None or approval["submitter_role"] not in {"admin", "owner", "renter"}:
                raise LookupError("Property approval request not found")
            if approval["status"] != "pending":
                raise ValueError("This property has already been reviewed")
            record = db.execute("SELECT data FROM application_records WHERE id=? AND kind='listing'", (property_id,)).fetchone()
            if record is None:
                raise LookupError("Property listing not found")
            data = json.loads(record["data"])
            data["status"] = "published" if status == "approved" else "rejected"
            db.execute("UPDATE application_records SET data=? WHERE id=?", (json.dumps(data), property_id))
            db.execute(
                "UPDATE property_approval_requests SET status=?,review_notes=?,reviewed_by_uid=?,reviewed_at=CURRENT_TIMESTAMP WHERE property_id=? AND status='pending'",
                (status, notes, reviewer_uid, property_id),
            )
            db.execute(
                "INSERT INTO activity_logs(log_id,actor_uid,action,target_type,target_id,metadata) VALUES(?,?,?,?,?,?)",
                (uuid.uuid4().hex, reviewer_uid, f"property_{status}", "property", property_id, json.dumps({"notes": notes})),
            )
            return approval["request_id"]

    def update_record_admin(self, record_id, payload):
        with connect() as db:
            row = db.execute("SELECT data,kind FROM application_records WHERE id=?", (record_id,)).fetchone()
            if not row:
                return False
            data = json.loads(row["data"])
            data.update(payload)
            db.execute("UPDATE application_records SET data=? WHERE id=?", (json.dumps(data), record_id))
            return True

    def create_support_ticket(self, user_uid, subject, description, priority):
        ticket_id = uuid.uuid4().hex
        with connect() as db:
            db.execute(
                "INSERT INTO support_tickets(ticket_id,user_uid,subject,description,priority) VALUES(?,?,?,?,?)",
                (ticket_id, user_uid, subject, description, priority),
            )
        return ticket_id

    def list_support_tickets(self, user_uid=None, status=None):
        with connect() as db:
            sql = "SELECT t.*,u.name AS user_name,u.email AS user_email,u.role AS user_role FROM support_tickets t JOIN users u ON u.uid=t.user_uid"
            clauses, params = [], []
            if user_uid:
                clauses.append("t.user_uid=?")
                params.append(user_uid)
            if status:
                clauses.append("t.status=?")
                params.append(status)
            if user_uid is None:
                clauses.append("u.role IN ('admin','owner','renter')")
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY t.created_at DESC"
            return [dict(row) for row in db.execute(sql, params)]

    def update_support_ticket(self, ticket_id, status, response):
        with connect() as db:
            return db.execute(
                "UPDATE support_tickets SET status=?,admin_response=?,updated_at=CURRENT_TIMESTAMP WHERE ticket_id=?",
                (status, response, ticket_id),
            ).rowcount == 1

    def support_ticket_by_id(self, ticket_id):
        with connect() as db:
            row = db.execute(
                "SELECT t.*,u.name AS user_name,u.email AS user_email,u.role AS user_role "
                "FROM support_tickets t JOIN users u ON u.uid=t.user_uid WHERE t.ticket_id=?",
                (ticket_id,),
            ).fetchone()
            return dict(row) if row else None

    def add_prediction(self, actor_uid, inputs, predicted_rent):
        prediction_id = uuid.uuid4().hex
        with connect() as db:
            db.execute(
                "INSERT INTO prediction_history(prediction_id,actor_uid,inputs,predicted_rent) VALUES(?,?,?,?)",
                (prediction_id, actor_uid, json.dumps(inputs), predicted_rent),
            )
        return prediction_id

    def list_predictions(self, limit=100):
        with connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT p.prediction_id,p.actor_uid,p.inputs,p.predicted_rent,p.created_at,u.name AS actor_name "
                "FROM prediction_history p JOIN users u ON u.uid=p.actor_uid ORDER BY p.created_at DESC LIMIT ?",
                (limit,),
            )]

    def add_activity(self, actor_uid, action, target_type, target_id, metadata=None):
        with connect() as db:
            db.execute(
                "INSERT INTO activity_logs(log_id,actor_uid,action,target_type,target_id,metadata) VALUES(?,?,?,?,?,?)",
                (uuid.uuid4().hex, actor_uid, action, target_type, target_id, json.dumps(metadata or {})),
            )

    def list_activity(self, limit=200):
        with connect() as db:
            results = []
            for row in db.execute(
                "SELECT l.*,u.name AS actor_name FROM activity_logs l LEFT JOIN users u ON u.uid=l.actor_uid "
                "WHERE (u.role IN ('admin','owner','renter') OR u.role IS NULL) "
                "AND l.action != 'broker_assigned' ORDER BY l.created_at DESC LIMIT ?",
                (limit,),
            ):
                item = dict(row)
                try:
                    item["metadata"] = json.loads(item["metadata"])
                except (TypeError, json.JSONDecodeError):
                    item["metadata"] = {}
                if (item.get("action") == "broker_assigned"
                        or any(key.startswith("broker") for key in item["metadata"])
                        or item["metadata"].get("role") == "broker"):
                    continue
                results.append(item)
            return results
=======
        with connect() as db: return [dict(r) for r in db.execute("SELECT uid,email,name,role FROM users")]
>>>>>>> origin/development

    def assign_broker(self, record_id, broker_uid):
        with connect() as db:
            row=db.execute("SELECT assigned_broker_uids FROM application_records WHERE id=?",(record_id,)).fetchone()
            if not row: return None
            assigned=set(json.loads(row[0])); assigned.add(broker_uid); result=sorted(assigned)
            db.execute("UPDATE application_records SET assigned_broker_uids=? WHERE id=?",(json.dumps(result),record_id)); return result


initialize_database()
