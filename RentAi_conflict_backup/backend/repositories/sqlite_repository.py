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
            row = db.execute("SELECT u.uid,u.email,u.name,u.role FROM sessions s JOIN users u ON u.uid=s.uid WHERE s.token_hash=? AND s.expires_at>datetime('now') AND u.account_status='active'", (_hash_token(token),)).fetchone()
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
        with connect() as db:
            row = db.execute("SELECT role FROM users WHERE uid=? AND account_status='active'", (uid,)).fetchone()
            return row["role"] if row else None

    def set_user_role(self, uid, role):
        with connect() as db: db.execute("UPDATE users SET role=? WHERE uid=?",(role,uid))

    def create_user_role(self, uid, role):
        with connect() as db: return db.execute("UPDATE users SET role=? WHERE uid=? AND role='pending'",(role,uid)).rowcount==1

    def list_user_roles(self):
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

    def assign_broker(self, record_id, broker_uid):
        with connect() as db:
            row=db.execute("SELECT assigned_broker_uids FROM application_records WHERE id=?",(record_id,)).fetchone()
            if not row: return None
            assigned=set(json.loads(row[0])); assigned.add(broker_uid); result=sorted(assigned)
            db.execute("UPDATE application_records SET assigned_broker_uids=? WHERE id=?",(json.dumps(result),record_id)); return result


initialize_database()
