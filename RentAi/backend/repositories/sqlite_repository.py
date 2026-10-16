"""SQLite storage for RentAI accounts, sessions, and application records."""
import hashlib
import json
import os
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from backend.core.config import PROJECT_ROOT

DB_PATH = Path(os.getenv("RENTAI_DATABASE_PATH", PROJECT_ROOT / "data" / "rentai.sqlite3"))
VALID_ROLES = {"owner", "renter", "broker", "admin"}


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
            db.execute("INSERT INTO application_records(id,owner_uid,kind,data,assigned_broker_uids) VALUES(?,?,?,?,?)", (record_id,data["owner_uid"],data["kind"],json.dumps(data.get("data",{})),json.dumps(data.get("assigned_broker_uids",[]))))
        return record_id, data

    def _record(self, row):
        if not row: return None
        result=dict(row); result["data"]=json.loads(result["data"]); result["assigned_broker_uids"]=json.loads(result["assigned_broker_uids"]); return result

    def get(self, collection, record_id):
        with connect() as db: return self._record(db.execute("SELECT * FROM application_records WHERE id=?",(record_id,)).fetchone())

    def update(self, collection, record_id, data):
        if "data" in data:
            with connect() as db: return db.execute("UPDATE application_records SET data=? WHERE id=?",(json.dumps(data["data"]),record_id)).rowcount>0
        return False

    def delete(self, collection, record_id):
        table="application_images" if collection=="application_images" else "application_records"
        with connect() as db: return db.execute(f"DELETE FROM {table} WHERE id=?",(record_id,)).rowcount>0

    def list_records(self):
        with connect() as db: return [self._record(row) for row in db.execute("SELECT * FROM application_records")]

    def list_images(self):
        with connect() as db: return [dict(row) for row in db.execute("SELECT * FROM application_images")]

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


initialize_database()
