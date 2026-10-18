"""FastAPI dependencies for verified SQLite sessions and role checks."""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.core.auth import verify_session
from backend.repositories.sqlite_repository import SQLiteRepository


VALID_ROLES = {"owner", "renter", "broker", "admin"}
bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    uid: str
    role: str
    claims: dict


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> CurrentUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Bearer token required")
    try:
        claims = verify_session(credentials.credentials)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid or revoked token") from exc
    uid = claims.get("uid")
    if not uid:
        raise HTTPException(status_code=403, detail="Verified account has no user identity")
    try:
        role = SQLiteRepository().get_user_role(uid)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Authorization service unavailable") from exc
    if role not in VALID_ROLES:
        raise HTTPException(status_code=403, detail="Verified account has no supported role")
    return CurrentUser(uid=uid, role=role, claims=claims)


def require_roles(*roles: str):
    invalid = set(roles) - VALID_ROLES
    if invalid:
        raise ValueError(f"Unsupported roles: {sorted(invalid)}")

    def dependency(user: Annotated[CurrentUser, Depends(get_current_user)]) -> CurrentUser:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Insufficient role")
        return user

    return dependency


AuthenticatedUser = Annotated[CurrentUser, Depends(get_current_user)]
