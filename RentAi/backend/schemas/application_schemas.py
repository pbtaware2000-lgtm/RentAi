"""HTTP schemas for application records and images."""

from typing import Any

<<<<<<< HEAD
from pydantic import BaseModel, ConfigDict, Field, HttpUrl
=======
from pydantic import BaseModel, Field, HttpUrl
>>>>>>> origin/development


class ApplicationRecordCreate(BaseModel):
    kind: str = Field(min_length=1, max_length=40)
    data: dict[str, Any]


class ApplicationRecordResponse(BaseModel):
    id: str
    owner_uid: str
    kind: str
    data: dict[str, Any]


<<<<<<< HEAD
=======
class RazorpayPaymentVerificationRequest(BaseModel):
    razorpay_payment_id: str = Field(min_length=1, max_length=80)
    razorpay_order_id: str = Field(min_length=1, max_length=80)
    razorpay_signature: str = Field(min_length=1, max_length=128)


>>>>>>> origin/development
class ImageMetadataRequest(BaseModel):
    url: HttpUrl
    public_id: str = Field(min_length=1)
    format: str | None = None
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    bytes: int | None = Field(default=None, gt=0)


class ImageMetadataResponse(ImageMetadataRequest):
    id: str
    owner_uid: str
    uploader_uid: str
    record_id: str


<<<<<<< HEAD
class RoleGrantRequest(BaseModel):
    role: str = Field(pattern="^(owner|renter|admin)$")


class AccountStatusRequest(BaseModel):
    status: str = Field(pattern="^(active|deactivated)$")


class PropertyReviewRequest(BaseModel):
    status: str = Field(pattern="^(approved|rejected)$")
    notes: str = Field(default="", max_length=2000)


class AppReviewSubmit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rating: int = Field(ge=1, le=5)
    feedback: str = Field(min_length=1, max_length=4000)


class AdminListingUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    city: str | None = Field(default=None, min_length=1, max_length=160)
    locality: str | None = Field(default=None, min_length=1, max_length=160)
    property_type: str | None = Field(default=None, min_length=1, max_length=80)
    rent: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    bedrooms: int | None = Field(default=None, ge=1, le=30)
    bathrooms: int | None = Field(default=None, ge=0, le=30)
    area_sqft: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    description: str | None = Field(default=None, max_length=5000)
    occupancy_status: str | None = Field(default=None, pattern="^(vacant|occupied|not_tracked)$")


class SupportTicketCreate(BaseModel):
    subject: str = Field(min_length=3, max_length=160)
    description: str = Field(min_length=5, max_length=4000)
    priority: str = Field(default="normal", pattern="^(low|normal|high|urgent)$")


class SupportTicketUpdate(BaseModel):
    status: str = Field(pattern="^(open|in_progress|resolved|closed)$")
    admin_response: str = Field(default="", max_length=4000)


class AdminRentalRequestUpdate(BaseModel):
    status: str = Field(pattern="^(new|contacted|viewing_scheduled|completed|closed)$")
    note: str = Field(default="", max_length=2000)
=======
class BrokerAssignmentRequest(BaseModel):
    broker_uid: str = Field(min_length=1)


class RoleGrantRequest(BaseModel):
    role: str = Field(pattern="^(owner|renter|broker|admin)$")
>>>>>>> origin/development


class PublicRegistrationRequest(BaseModel):
    role: str = Field(pattern="^(owner|renter|broker)$")


class UserRoleResponse(BaseModel):
    uid: str
    role: str


class AuthenticatedUserResponse(BaseModel):
    uid: str
    role: str
    name: str | None = None
    email: str | None = None
