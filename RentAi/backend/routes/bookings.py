"""Authenticated booking actions and unauthenticated signed Razorpay webhook."""

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from backend.auth.dependencies import CurrentUser, get_current_user, require_roles
from backend.core.config import razorpay_configuration_status
from backend.schemas.application_schemas import RazorpayPaymentVerificationRequest
from backend.services.booking_service import BookingService

router = APIRouter(prefix="/bookings", tags=["bookings"])


class BookingRequest(BaseModel):
    property_id: str = Field(min_length=1, max_length=160)
    move_in_date: str = Field(min_length=10, max_length=10)


class BookingDecision(BaseModel):
    decision: str = Field(pattern="^(accept|reject)$")


@router.get("/payment-config")
def payment_configuration(user: CurrentUser = Depends(require_roles("renter"))):
    return razorpay_configuration_status()


@router.get("")
def list_bookings(user: CurrentUser = Depends(get_current_user)):
    service = BookingService()
    if user.role == "renter":
        return {"bookings": service.list_for_renter(user.uid)}
    if user.role == "owner":
        return {"bookings": service.list_for_owner(user.uid)}
    raise HTTPException(status_code=403, detail="Bookings are available to renters and owners")


@router.get("/notifications")
def list_booking_notifications(user: CurrentUser = Depends(require_roles("renter", "owner"))):
    return {"notifications": BookingService().list_notifications(user.uid)}


@router.post("", status_code=201)
def request_booking(payload: BookingRequest, user: CurrentUser = Depends(require_roles("renter"))):
    return {"booking": BookingService().create_request(user, payload.property_id, payload.move_in_date)}


@router.post("/{booking_id}/decision")
def decide_booking(booking_id: str, payload: BookingDecision, user: CurrentUser = Depends(require_roles("owner"))):
    return {"booking": BookingService().decide(user, booking_id, payload.decision)}


@router.post("/{booking_id}/checkout")
def create_checkout(booking_id: str, user: CurrentUser = Depends(require_roles("renter"))):
    return BookingService().create_checkout(user, booking_id)


@router.post("/{booking_id}/verify")
def verify_checkout(booking_id: str, payload: RazorpayPaymentVerificationRequest,
                    user: CurrentUser = Depends(require_roles("renter"))):
    return BookingService().verify_payment(
        user, booking_id, payload.razorpay_payment_id,
        payload.razorpay_order_id, payload.razorpay_signature,
    )


@router.post("/razorpay/webhook")
async def razorpay_webhook(request: Request, signature: str | None = Header(default=None, alias="X-Razorpay-Signature"),
                           event_id: str | None = Header(default=None, alias="X-Razorpay-Event-Id")):
    raw_body = await request.body()
    event = BookingService.verify_webhook(raw_body, signature)
    return BookingService().process_webhook(event_id or "", event)
