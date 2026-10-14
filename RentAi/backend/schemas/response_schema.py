"""Response contracts for future rent classification and similarity services."""

from pydantic import BaseModel, Field


class RentClassificationRequest(BaseModel):
    rent: float = Field(ge=0, allow_inf_nan=False)
    area_sqft: float = Field(gt=0, allow_inf_nan=False)
    city: str = Field(min_length=1)
    property_type: str = Field(min_length=1)
    locality: str | None = Field(default=None, min_length=1)


class RentClassificationResponse(BaseModel):
    rent_category: str


class SimilarPropertiesRequest(BaseModel):
    property_id: str = Field(min_length=1)
    limit: int = Field(default=5, ge=1, le=20)


class SimilarPropertiesResponse(BaseModel):
    count: int
    properties: list[dict]
