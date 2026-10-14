"""Lazy Firebase Admin integration for trusted backend operations."""

from __future__ import annotations

from functools import lru_cache
import os
import time

from backend.core.config import SettingsError, get_firebase_admin_settings, load_settings


class FirebaseConfigurationError(RuntimeError):
    """Raised when Firebase cannot be initialized safely."""


def is_bootstrap_admin_uid(uid: str) -> bool:
    return uid in load_settings().bootstrap_admin_uids


@lru_cache(maxsize=1)
def get_firebase_app():
    try:
        import firebase_admin
        from firebase_admin import credentials
    except ImportError as exc:
        raise FirebaseConfigurationError(
            "Firebase support requires the firebase-admin package; install it "
            "in the deployment environment before enabling application routes."
        ) from exc

    try:
        project_id, credential_path = get_firebase_admin_settings()
    except SettingsError as exc:
        raise FirebaseConfigurationError(str(exc)) from None
    try:
        existing_app = firebase_admin.get_app()
        if existing_app.project_id != project_id:
            raise FirebaseConfigurationError(
                "Initialized Firebase app does not match FIREBASE_PROJECT_ID."
            )
        return existing_app
    except ValueError:
        options = {"projectId": project_id} if project_id else None
        try:
            credential = credentials.Certificate(credential_path) if credential_path else None
            return firebase_admin.initialize_app(credential, options=options)
        except Exception as exc:
            raise FirebaseConfigurationError(
                "Firebase initialization failed. Configure Application Default "
                "Credentials or FIREBASE_CREDENTIALS_PATH and FIREBASE_PROJECT_ID."
            ) from exc


def verify_id_token(token: str) -> dict:
    """Verify a Firebase client ID token; never accept a caller-supplied role."""
    try:
        import firebase_admin.auth
    except ImportError as exc:
        raise FirebaseConfigurationError(
            "Firebase ID-token verification requires firebase-admin."
        ) from exc
    app = get_firebase_app()
    try:
        return firebase_admin.auth.verify_id_token(token, app=app, check_revoked=True)
    except Exception as exc:
        raise ValueError("Invalid or revoked Firebase ID token") from exc


def get_firestore_client():
    try:
        from firebase_admin import firestore
    except ImportError as exc:
        raise FirebaseConfigurationError(
            "Firestore support requires firebase-admin."
        ) from exc
    return firestore.client(app=get_firebase_app())


def grant_role_to_user(*, actor_id_token: str, uid: str, role: str) -> None:
    """Grant a role only to an existing account through a trusted Admin identity."""
    allowed_roles = {"owner", "renter", "broker", "admin"}
    if role not in allowed_roles:
        raise ValueError("Unsupported role")
    actor_claims = verify_id_token(actor_id_token)
    actor_uid = actor_claims.get("uid")
    from backend.repositories.firestore_repository import FirestoreRepository

    role_repository = FirestoreRepository()
    if not actor_uid or role_repository.get_user_role(actor_uid) != "admin":
        raise PermissionError("Trusted Admin authorization is required")
    try:
        import firebase_admin.auth
    except ImportError as exc:
        raise FirebaseConfigurationError(
            "Role provisioning requires firebase-admin."
        ) from exc
    app = get_firebase_app()
    user = firebase_admin.auth.get_user(uid, app=app)
    claims = dict(user.custom_claims or {})
    claims["role"] = role
    firebase_admin.auth.set_custom_user_claims(uid, claims, app=app)
    # Firestore remains authoritative; a failed write cannot grant API access
    # from a custom claim alone.
    role_repository.set_user_role(uid, role)
    firebase_admin.auth.revoke_refresh_tokens(uid, app=app)


def bootstrap_first_admin(*, actor_id_token: str) -> str:
    """Provision the single allowlisted first Admin and disable this process allowlist."""
    claims = verify_id_token(actor_id_token)
    uid = claims.get("uid")
    if not uid or not is_bootstrap_admin_uid(uid):
        raise PermissionError("This account is not an approved bootstrap Admin")

    try:
        import firebase_admin.auth
    except ImportError as exc:
        raise FirebaseConfigurationError(
            "Role provisioning requires firebase-admin."
        ) from exc

    from backend.repositories.firestore_repository import FirestoreRepository

    repository = FirestoreRepository()
    if not repository.bootstrap_first_admin(uid):
        raise PermissionError("First Admin bootstrap is already complete")

    app = get_firebase_app()
    user = firebase_admin.auth.get_user(uid, app=app)
    user_claims = dict(user.custom_claims or {})
    user_claims["role"] = "admin"
    firebase_admin.auth.set_custom_user_claims(uid, user_claims, app=app)
    firebase_admin.auth.revoke_refresh_tokens(uid, app=app)

    os.environ["RENTAI_BOOTSTRAP_ADMIN_UIDS"] = ""
    return uid


def get_trusted_user_role(uid: str) -> str | None:
    """Read the backend-authoritative role record, not a possibly stale token claim."""
    from backend.repositories.firestore_repository import FirestoreRepository

    return FirestoreRepository().get_user_role(uid)


def register_user_role(*, id_token: str, role: str) -> str:
    """Set a new account's public role once, based on its verified identity."""
    if role not in {"owner", "renter", "broker"}:
        raise ValueError("Public registration supports Owner, Renter, and Broker only")
    claims = verify_id_token(id_token)
    uid = claims.get("uid")
    if not uid:
        raise ValueError("Verified account has no user identity")
    try:
        import firebase_admin.auth
    except ImportError as exc:
        raise FirebaseConfigurationError("Public registration requires firebase-admin.") from exc
    try:
        account = firebase_admin.auth.get_user(uid, app=get_firebase_app())
    except Exception as exc:
        raise FirebaseConfigurationError("Firebase could not verify the new account.") from exc
    created_ms = getattr(getattr(account, "user_metadata", None), "creation_timestamp", None)
    auth_time = claims.get("auth_time")
    now = time.time()
    if not isinstance(created_ms, (int, float)) or not isinstance(auth_time, (int, float)):
        raise PermissionError("Only a newly created Firebase account can use public registration")
    created_at = created_ms / 1000
    if now - created_at > 300 or created_at - now > 60 or abs(auth_time - created_at) > 300:
        raise PermissionError("Only a newly created Firebase account can use public registration")
    from backend.repositories.firestore_repository import FirestoreRepository

    if not FirestoreRepository().create_user_role(uid, role):
        raise PermissionError("An account role is already assigned")
    return uid
