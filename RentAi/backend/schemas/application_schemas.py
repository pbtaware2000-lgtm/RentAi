"""HTTP schemas for Firebase-backed application records and images."""

from typing import Any

from pydantic import BaseModel, Field, HttpUrl


class ApplicationRecordCreate(BaseModel):
    kind: str = Field(min_length=1, max_length=40)
    data: dict[str, Any]


class ApplicationRecordResponse(BaseModel):
    id: str
    owner_uid: str
    kind: str
    data: dict[str, Any]


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


class BrokerAssignmentRequest(BaseModel):
    broker_uid: str = Field(min_length=1)


class RoleGrantRequest(BaseModel):
    role: str = Field(pattern="^(owner|renter|broker|admin)$")


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
