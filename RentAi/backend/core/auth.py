"""Local account authentication backed by the RentAI SQLite database."""
import hashlib
import hmac
import re
import secrets

from backend.repositories.sqlite_repository import SQLiteRepository


class AuthenticationServiceError(RuntimeError): pass


def _password_hash(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310000)
    return salt.hex() + "$" + digest.hex()


def _password_matches(password, stored):
    salt, _ = stored.split("$", 1)
    return hmac.compare_digest(_password_hash(password, bytes.fromhex(salt)), stored)


def register_account(email, password, name, role):
    if role not in {"owner", "renter", "broker"}: raise ValueError("Unsupported registration role")
    if not isinstance(email, str) or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email): raise ValueError("Enter a valid email address")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 160: raise ValueError("Enter your name (160 characters maximum)")
    if not isinstance(password, str): raise ValueError("Choose a password with at least 8 characters")
    if len(password) < 8: raise ValueError("Choose a password with at least 8 characters")
    repo=SQLiteRepository()
    if repo.account_by_email(email): raise ValueError("An account with this email already exists")
    try: uid=repo.create_account(email, name.strip(), _password_hash(password), role)
    except Exception as exc: raise ValueError("An account with this email already exists") from exc
    return uid, repo.issue_session(uid)


def create_first_admin_account(email, password, name):
    """Create the first Admin directly in SQLite; this is a local operator action."""
    if not isinstance(email, str) or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ValueError("Enter a valid email address")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 160:
        raise ValueError("Enter your name (160 characters maximum)")
    if not isinstance(password, str) or len(password) < 8:
        raise ValueError("Choose a password with at least 8 characters")
    try:
        return SQLiteRepository().create_first_admin_account(
            email.strip(), name.strip(), _password_hash(password)
        )
    except Exception as exc:
        if isinstance(exc, ValueError):
            raise
        if SQLiteRepository().account_by_email(email.strip()):
            raise ValueError("An account with this email already exists") from None
        raise


def sign_in(email, password):
    user=SQLiteRepository().account_by_email(email)
    if not user or not _password_matches(password, user["password_hash"]):
        raise ValueError("Email or password is incorrect")
    return user["uid"], SQLiteRepository().issue_session(user["uid"])


def verify_session(token):
    user=SQLiteRepository().user_for_token(token)
    if not user: raise ValueError("Invalid or expired session")
    return {"uid":user["uid"],"email":user["email"],"name":user["name"],"role":user["role"]}


def get_trusted_user_role(uid): return SQLiteRepository().get_user_role(uid)


def grant_role_to_user(*, actor_id_token, uid, role):
    if verify_session(actor_id_token)["role"] != "admin": raise PermissionError("Admin authorization required")
    if role not in {"owner","renter","broker","admin"}: raise ValueError("Unsupported role")
    if not SQLiteRepository().account_by_uid(uid): raise ValueError("Account not found")
    SQLiteRepository().set_user_role(uid, role)


def register_user_role(*, id_token, role):
    user=verify_session(id_token)
    return user["uid"]
