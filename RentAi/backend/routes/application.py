"""Authenticated routes for SQLite-backed application records."""

from fastapi import APIRouter, Depends, HTTPException, Query
import logging
import math


logger = logging.getLogger(__name__)
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.auth.dependencies import CurrentUser, get_current_user, require_roles
from backend.core.auth import sign_in, register_account as create_sqlite_account
from backend.schemas.application_schemas import (
    ApplicationRecordCreate,
    ApplicationRecordResponse,
    AuthenticatedUserResponse,
    RoleGrantRequest,
    PublicRegistrationRequest,
    AccountStatusRequest,
    AdminRentalRequestUpdate,
    PropertyReviewRequest,
    AppReviewSubmit,
    SupportTicketCreate,
    SupportTicketUpdate,
    AdminListingUpdate,
)
from backend.services.application_service import ApplicationService, INQUIRY_TRANSITIONS
from backend.repositories.sqlite_repository import SQLiteRepository
from backend.schemas.prediction_schema import RentPredictionRequest
    BrokerAssignmentRequest,
    RoleGrantRequest,
    PublicRegistrationRequest,
    RazorpayPaymentVerificationRequest,
)
from backend.services.application_service import ApplicationService
from backend.services.razorpay_service import RazorpayRentService


router = APIRouter(prefix="/application/records", tags=["application"])
auth_router = APIRouter(prefix="/application/auth", tags=["application-auth"])
admin_router = APIRouter(prefix="/application/admin", tags=["application-admin"])
payments_router = APIRouter(prefix="/application/payments/razorpay", tags=["rent-payments"])
bearer_scheme = HTTPBearer(auto_error=False)



@auth_router.get("/me", response_model=AuthenticatedUserResponse)
def current_account(user: CurrentUser = Depends(get_current_user)):
    return {
        "uid": user.uid,
        "role": user.role,
        "name": user.claims.get("name"),
        "email": user.claims.get("email"),
    }



@auth_router.post("/register", status_code=201, response_model=AuthenticatedUserResponse)
def register_account(
    payload: PublicRegistrationRequest,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
):
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Bearer token required")
    try:
        return ApplicationService().register_user_role(credentials.credentials, payload.role)
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail="An account role is already assigned") from exc
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid or revoked token") from exc
    except Exception:
        logger.exception("Public account role provisioning failed")
        raise HTTPException(status_code=503, detail="Account registration service unavailable") from None


@auth_router.post("/sign-in")
def sign_in_account(payload: dict):
    try:
        uid, token = sign_in(str(payload.get("email", "")).strip().lower(), str(payload.get("password", "")))
        from backend.repositories.sqlite_repository import SQLiteRepository
        user = SQLiteRepository().account_by_uid(uid)
        return {"id_token": token, "refresh_token": token, "expires_in": 30 * 24 * 3600, "user": user}
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@auth_router.post("/sign-up", status_code=201)
def sign_up_account(payload: dict):
    try:
        uid, token = create_sqlite_account(str(payload.get("email", "")).strip().lower(), str(payload.get("password", "")), str(payload.get("name", "")), str(payload.get("role", "")))
        from backend.repositories.sqlite_repository import SQLiteRepository
        return {"id_token": token, "refresh_token": token, "expires_in": 30 * 24 * 3600, "user": SQLiteRepository().account_by_uid(uid)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@auth_router.get("/records")
def list_account_records(
    kind: str | None = Query(default=None, pattern="^(listing|profile|favorite|inquiry|maintenance|tenancy|rent_payment)$"),

    kind: str | None = Query(default=None, pattern="^(listing|profile|favorite|inquiry|maintenance|tenancy|rent_payment)$"),

    user: CurrentUser = Depends(get_current_user),
):
    try:
        return {"records": ApplicationService().list_records_for_user(user, kind)}
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Application storage unavailable") from exc


@payments_router.post("/rent/{rent_record_id}/order")
def create_rent_order(rent_record_id: str, user: CurrentUser = Depends(get_current_user)):
    try:
        return RazorpayRentService().create_order(user, rent_record_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Razorpay rent payments are temporarily unavailable") from exc


@payments_router.post("/verify")
def verify_rent_payment(
    payload: RazorpayPaymentVerificationRequest,
    user: CurrentUser = Depends(get_current_user),
):
    try:
        return RazorpayRentService().verify_payment(
            user,
            payment_id=payload.razorpay_payment_id,
            order_id=payload.razorpay_order_id,
            signature=payload.razorpay_signature,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Razorpay rent verification is temporarily unavailable") from exc



@auth_router.get("/listing-images")
def list_visible_listing_images(user: CurrentUser = Depends(get_current_user)):
    try:
        return {"images": ApplicationService().list_listing_images_for_user(user)}
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Application storage unavailable") from exc


@auth_router.get("/profile-images")
def list_visible_profile_images(user: CurrentUser = Depends(get_current_user)):
    try:
        return {"images": ApplicationService().list_profile_images_for_user(user)}
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Application storage unavailable") from exc



@admin_router.get("/users")
def list_registered_users(user: CurrentUser = Depends(require_roles("admin"))):
    try:
        return {"users": ApplicationService().list_users(user)}
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Application storage unavailable") from exc



@router.post("", response_model=ApplicationRecordResponse, status_code=201)
def create_record(payload: ApplicationRecordCreate, user: CurrentUser = Depends(get_current_user)):
    try:
        return ApplicationService().create_record(user, payload.kind, payload.data)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Application storage unavailable") from exc


@router.get("/{record_id}", response_model=ApplicationRecordResponse)
def get_record(record_id: str, user: CurrentUser = Depends(get_current_user)):
    try:
        return ApplicationService().get_record(user, record_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Application storage unavailable") from exc


@router.put("/{record_id}", response_model=ApplicationRecordResponse)
def update_record(record_id: str, payload: ApplicationRecordCreate, user: CurrentUser = Depends(get_current_user)):
    try:
        service = ApplicationService()
        record = service.get_record(user, record_id)
        if record.get("kind") != payload.kind:
            raise HTTPException(status_code=400, detail="Record kind cannot be changed")
        return service.update_record(user, record_id, payload.data)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Application storage unavailable") from exc


@router.delete("/{record_id}", status_code=204)
def delete_record(record_id: str, user: CurrentUser = Depends(get_current_user)):
    try:
        ApplicationService().delete_record(user, record_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Application storage unavailable") from exc


@router.post("/{record_id}/broker-assignment", response_model=ApplicationRecordResponse)
def assign_broker(
    record_id: str,
    payload: BrokerAssignmentRequest,
    user: CurrentUser = Depends(require_roles("admin")),
):
    try:
        return ApplicationService().assign_broker(user, record_id, payload.broker_uid)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Application storage unavailable") from exc


@admin_router.put("/users/{uid}/role", status_code=204)
def provision_user_role(
    uid: str,
    payload: RoleGrantRequest,
    user: CurrentUser = Depends(require_roles("admin")),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
):
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Bearer token required")
    try:
        ApplicationService().provision_user_role(
            user, credentials.credentials, uid, payload.role
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Trusted Admin authorization is required") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Role provisioning request is invalid") from exc
