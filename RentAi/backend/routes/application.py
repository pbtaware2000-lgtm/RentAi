"""Authenticated routes for SQLite-backed application records."""

from fastapi import APIRouter, Depends, HTTPException, Query
import logging
<<<<<<< HEAD
import math
=======
>>>>>>> origin/development

logger = logging.getLogger(__name__)
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.auth.dependencies import CurrentUser, get_current_user, require_roles
from backend.core.auth import sign_in, register_account as create_sqlite_account
from backend.schemas.application_schemas import (
    ApplicationRecordCreate,
    ApplicationRecordResponse,
    AuthenticatedUserResponse,
<<<<<<< HEAD
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
=======
    BrokerAssignmentRequest,
    RoleGrantRequest,
    PublicRegistrationRequest,
    RazorpayPaymentVerificationRequest,
)
from backend.services.application_service import ApplicationService
from backend.services.razorpay_service import RazorpayRentService
>>>>>>> origin/development

router = APIRouter(prefix="/application/records", tags=["application"])
auth_router = APIRouter(prefix="/application/auth", tags=["application-auth"])
admin_router = APIRouter(prefix="/application/admin", tags=["application-admin"])
<<<<<<< HEAD
bearer_scheme = HTTPBearer(auto_error=False)


@auth_router.get("/app-reviews")
def list_app_reviews(
    rating: int | None = Query(default=None, ge=1, le=5),
    role: str | None = Query(default=None, pattern="^(owner|renter)$"),
    search: str | None = Query(default=None, max_length=120),
    user: CurrentUser = Depends(require_roles("admin", "owner", "renter")),
):
    repository = SQLiteRepository()
    is_admin = user.role == "admin"
    try:
        reviews = repository.list_app_reviews(
            rating=rating, role=role, search=search
        )
        own_review = repository.app_review_by_user(user.uid) if not is_admin else None
        summary = repository.app_review_summary()
    except Exception as exc:
        logger.exception("App review read failed")
        raise HTTPException(status_code=503, detail="App reviews are temporarily unavailable") from exc
    return {
        **summary,
        "reviews": reviews,
        "own_review": own_review,
    }


@auth_router.put("/app-reviews")
def submit_app_review(
    payload: AppReviewSubmit,
    user: CurrentUser = Depends(require_roles("owner", "renter")),
):
    feedback = payload.feedback.strip()
    if not feedback:
        raise HTTPException(status_code=422, detail="Feedback is required")
    if len(feedback) > 4000:
        raise HTTPException(status_code=422, detail="Feedback must be 4,000 characters or fewer")
    try:
        repository = SQLiteRepository()
        account = repository.account_by_uid(user.uid) or {}
        review_id = repository.upsert_app_review(
            user.uid, account.get("name") or "", user.role, payload.rating, feedback
        )
        return {"review": repository.app_review_by_user(user.uid), "review_id": review_id}
    except Exception as exc:
        logger.exception("App review submission failed")
        raise HTTPException(status_code=503, detail="App review could not be saved") from exc


=======
payments_router = APIRouter(prefix="/application/payments/razorpay", tags=["rent-payments"])
bearer_scheme = HTTPBearer(auto_error=False)


>>>>>>> origin/development
@auth_router.get("/me", response_model=AuthenticatedUserResponse)
def current_account(user: CurrentUser = Depends(get_current_user)):
    return {
        "uid": user.uid,
        "role": user.role,
        "name": user.claims.get("name"),
        "email": user.claims.get("email"),
    }


<<<<<<< HEAD
@auth_router.post("/sign-out", status_code=204)
def sign_out_account(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    user: CurrentUser = Depends(get_current_user),
):
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Bearer token required")
    SQLiteRepository().revoke_session(credentials.credentials)


=======
>>>>>>> origin/development
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
<<<<<<< HEAD
    kind: str | None = Query(default=None, pattern="^(listing|profile|favorite|inquiry|maintenance)$"),
=======
    kind: str | None = Query(default=None, pattern="^(listing|profile|favorite|inquiry|maintenance|tenancy|rent_payment)$"),
>>>>>>> origin/development
    user: CurrentUser = Depends(get_current_user),
):
    try:
        return {"records": ApplicationService().list_records_for_user(user, kind)}
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Application storage unavailable") from exc


<<<<<<< HEAD
=======
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


>>>>>>> origin/development
@auth_router.get("/listing-images")
def list_visible_listing_images(user: CurrentUser = Depends(get_current_user)):
    try:
        return {"images": ApplicationService().list_listing_images_for_user(user)}
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Application storage unavailable") from exc


<<<<<<< HEAD
@auth_router.get("/approvals")
def list_my_property_approvals(user: CurrentUser = Depends(get_current_user)):
    requests = SQLiteRepository().list_approval_requests(exclude_broker=user.role == "admin")
    if user.role != "admin":
        requests = [item for item in requests if item.get("submitted_by_uid") == user.uid]
    return {"requests": requests}
=======
@auth_router.get("/profile-images")
def list_visible_profile_images(user: CurrentUser = Depends(get_current_user)):
    try:
        return {"images": ApplicationService().list_profile_images_for_user(user)}
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Application storage unavailable") from exc
>>>>>>> origin/development


@admin_router.get("/users")
def list_registered_users(user: CurrentUser = Depends(require_roles("admin"))):
    try:
        return {"users": ApplicationService().list_users(user)}
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Application storage unavailable") from exc


<<<<<<< HEAD
@admin_router.get("/dashboard")
def admin_dashboard(user: CurrentUser = Depends(require_roles("admin"))):
    try:
        stats = SQLiteRepository().admin_dashboard_stats()
        from backend.services.property_service import get_property_count
        try:
            stats["dataset_properties"] = get_property_count()
        except (FileNotFoundError, OSError):
            stats["dataset_properties"] = None
        return stats
    except Exception as exc:
        logger.exception("Admin dashboard query failed")
        raise HTTPException(status_code=503, detail="Admin dashboard data is unavailable") from exc


@admin_router.get("/records")
def admin_records(
    kind: str | None = Query(default=None, pattern="^(listing|profile|favorite|inquiry|maintenance)$"),
    user: CurrentUser = Depends(require_roles("admin")),
):
    try:
        return {"records": ApplicationService().list_admin_records(user, kind)}
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Application storage unavailable") from exc


@admin_router.get("/approvals")
def admin_approvals(
    status: str | None = Query(default=None, pattern="^(pending|approved|rejected)$"),
    user: CurrentUser = Depends(require_roles("admin")),
):
    return {"requests": SQLiteRepository().list_approval_requests(status, exclude_broker=True)}


@admin_router.patch("/properties/{property_id}")
def update_admin_property(
    property_id: str,
    payload: AdminListingUpdate,
    user: CurrentUser = Depends(require_roles("admin")),
):
    repository = SQLiteRepository()
    record = next((item for item in repository.list_admin_records("listing") if item.get("id") == property_id), None)
    if not record or record.get("kind") != "listing":
        raise HTTPException(status_code=404, detail="Property listing not found")
    changes = payload.model_dump(exclude_none=True)
    current = record.get("data") or {}
    if not changes:
        return {"id": property_id, "data": current}
    updated = {**current, **changes}
    status = current.get("status", "draft")
    updated = ApplicationService._validate_listing_data({**updated, "status": status})
    repository.update_record_admin(property_id, updated)
    repository.add_activity(user.uid, "property_updated", "property", property_id, {"fields": sorted(changes)})
    return {"id": property_id, "data": updated}


@admin_router.put("/properties/{property_id}/status")
def set_admin_property_status(
    property_id: str,
    payload: dict,
    user: CurrentUser = Depends(require_roles("admin")),
):
    if payload.get("status") != "inactive":
        raise HTTPException(status_code=422, detail="Admin may deactivate a listing here; approvals use the review workflow")
    repository = SQLiteRepository()
    record = next((item for item in repository.list_admin_records("listing") if item.get("id") == property_id), None)
    if not record or record.get("kind") != "listing":
        raise HTTPException(status_code=404, detail="Property listing not found")
    if (record.get("data") or {}).get("status") == "pending":
        raise HTTPException(status_code=409, detail="Review this pending submission through the approval workflow")
    repository.update_record_admin(property_id, {"status": "inactive"})
    repository.add_activity(user.uid, "property_deactivated", "property", property_id)
    return {"id": property_id, "status": "inactive"}


@admin_router.post("/properties/{property_id}/review")
def review_property(
    property_id: str,
    payload: PropertyReviewRequest,
    user: CurrentUser = Depends(require_roles("admin")),
):
    request_id = ApplicationService().review_property(user, property_id, payload.status, payload.notes.strip())
    return {"request_id": request_id, "property_id": property_id, "status": payload.status}


@admin_router.put("/users/{uid}/status", status_code=204)
def set_account_status(
    uid: str,
    payload: AccountStatusRequest,
    user: CurrentUser = Depends(require_roles("admin")),
):
    ApplicationService().set_account_status(user, uid, payload.status)


@admin_router.put("/rental-requests/{record_id}")
def update_rental_request(
    record_id: str,
    payload: AdminRentalRequestUpdate,
    user: CurrentUser = Depends(require_roles("admin")),
):
    repository = SQLiteRepository()
    record = next((item for item in repository.list_admin_records("inquiry") if item.get("id") == record_id), None)
    if not record or record.get("kind") != "inquiry":
        raise HTTPException(status_code=404, detail="Rental request not found")
    current_status = (record.get("data") or {}).get("status", "new")
    if payload.status != current_status and payload.status not in INQUIRY_TRANSITIONS.get(current_status, set()):
        raise HTTPException(status_code=409, detail="Rental request cannot transition from its current status")
    update = {"status": payload.status}
    if payload.note.strip():
        update["admin_note"] = payload.note.strip()
    repository.update_record_admin(record_id, update)
    repository.add_activity(user.uid, "rental_request_status_changed", "rental_request", record_id, update)
    return {"id": record_id, "data": {**(record.get("data") or {}), **update}}


@auth_router.post("/support", status_code=201)
def create_support_ticket(
    payload: SupportTicketCreate,
    user: CurrentUser = Depends(require_roles("owner", "renter", "broker")),
):
    if not payload.subject.strip() or not payload.description.strip():
        raise HTTPException(status_code=422, detail="A non-empty subject and description are required")
    ticket_id = SQLiteRepository().create_support_ticket(
        user.uid, payload.subject.strip(), payload.description.strip(), payload.priority,
    )
    SQLiteRepository().add_activity(user.uid, "support_ticket_created", "support_ticket", ticket_id)
    return {"ticket_id": ticket_id}


@auth_router.get("/support")
def list_support_tickets(user: CurrentUser = Depends(get_current_user)):
    repository = SQLiteRepository()
    return {"tickets": repository.list_support_tickets(None if user.role == "admin" else user.uid)}


@admin_router.put("/support/{ticket_id}")
def update_support_ticket(
    ticket_id: str,
    payload: SupportTicketUpdate,
    user: CurrentUser = Depends(require_roles("admin")),
):
    repository = SQLiteRepository()
    ticket = repository.support_ticket_by_id(ticket_id)
    if ticket is None or ticket.get("user_role") == "broker":
        raise HTTPException(status_code=404, detail="Support ticket not found")
    repository.update_support_ticket(ticket_id, payload.status, payload.admin_response.strip())
    repository.add_activity(user.uid, "support_ticket_updated", "support_ticket", ticket_id, {
        "status": payload.status,
    })
    return {"ticket_id": ticket_id, "status": payload.status}


@admin_router.get("/activity")
def admin_activity(user: CurrentUser = Depends(require_roles("admin"))):
    return {"activity": SQLiteRepository().list_activity()}


@admin_router.get("/predictions")
def admin_predictions(user: CurrentUser = Depends(require_roles("admin"))):
    rows = SQLiteRepository().list_predictions()
    import json
    for row in rows:
        try:
            row["inputs"] = json.loads(row["inputs"])
        except (TypeError, json.JSONDecodeError):
            row["inputs"] = {}
    return {"predictions": rows}


@admin_router.post("/predictions")
def admin_test_prediction(payload: RentPredictionRequest, user: CurrentUser = Depends(require_roles("admin"))):
    from backend.services.prediction_service import predict_rent
    try:
        features = payload.model_dump()
        estimate = predict_rent(features)
        if not isinstance(estimate, (int, float)) or not math.isfinite(float(estimate)) or estimate <= 0:
            raise ValueError("The model did not produce a valid positive rent estimate")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail="The rent prediction dataset is unavailable") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Admin ML test failed")
        raise HTTPException(status_code=503, detail="The rent prediction model is unavailable") from exc
    prediction_id = SQLiteRepository().add_prediction(user.uid, features, estimate)
    SQLiteRepository().add_activity(user.uid, "ml_prediction_created", "prediction", prediction_id)
    return {"prediction_id": prediction_id, "predicted_rent": estimate}


@admin_router.get("/analytics")
def admin_analytics(user: CurrentUser = Depends(require_roles("admin"))):
    import pandas as pd
    from backend.services.property_service import load_properties
    try:
        data = load_properties()
    except (FileNotFoundError, OSError):
        data = pd.DataFrame()
    def distribution(column, limit=20):
        if column not in data or data.empty:
            return []
        counts = data[column].dropna().astype(str).value_counts().head(limit)
        return [{"label": str(label), "count": int(count)} for label, count in counts.items()]
    rent = pd.to_numeric(data.get("rent", pd.Series(dtype=float)), errors="coerce").dropna()
    monthly_rents = []
    if not data.empty and "posted_date" in data and "rent" in data:
        dates = pd.to_datetime(data["posted_date"], errors="coerce")
        trend = pd.DataFrame({"month": dates.dt.strftime("%Y-%m"), "rent": pd.to_numeric(data["rent"], errors="coerce")}).dropna()
        monthly_rents = [{"month": str(month), "average_rent": float(value)} for month, value in trend.groupby("month")["rent"].mean().sort_index().items()]
    return {
        "dataset_rows": int(len(data)),
        "average_rent": float(rent.mean()) if not rent.empty else None,
        "users_by_month": SQLiteRepository().get_registered_user_monthly_counts(),
        "users_by_role": SQLiteRepository().admin_dashboard_stats()["users_by_role"],
        "properties_by_location": distribution("city"),
        "property_types": distribution("property_type"),
        "rent_distribution": [float(value) for value in rent.sample(min(500, len(rent)), random_state=42).tolist()] if not rent.empty else [],
        "monthly_rent_trends": monthly_rents,
        "property_source": "rentai_dataset.csv",
    }


=======
>>>>>>> origin/development
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


<<<<<<< HEAD
=======
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


>>>>>>> origin/development
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
