"""Request and response contracts for rent prediction."""

from pydantic import BaseModel, ConfigDict, Field


class RentPredictionRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    city: str = Field(min_length=1)
    locality: str = Field(min_length=1)
    property_type: str = Field(min_length=1)
    furnishing_status: str = Field(min_length=1)
    area_sqft: float = Field(gt=0, allow_inf_nan=False)
    bedrooms: int = Field(ge=0)
    bathrooms: int = Field(ge=0)
    balcony: int | None = Field(default=None, ge=0)
    floor: int = Field(ge=0)
    total_floors: int | None = Field(default=None, ge=0)
    distance_to_city_center_km: float = Field(ge=0, allow_inf_nan=False)
    distance_to_metro_km: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    distance_to_school_km: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    distance_to_hospital_km: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    distance_to_it_park_km: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    age_of_property: int = Field(ge=0)
    parking: int = Field(ge=0)
    lift: str | None = None
    gym: str | None = None
    security: str | None = None
    power_backup: str | None = None
    swimming_pool: str | None = None
    air_conditioning: str | None = None
    gated_community: str | None = None
    water_supply: str | None = None


class RentPredictionResponse(BaseModel):
    predicted_rent: float
    currency: str = "INR per month"
    model_name: str
    features: dict
    limitations: str
    test_metrics: dict = Field(default_factory=dict)
