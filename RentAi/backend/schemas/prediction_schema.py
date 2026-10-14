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
    floor: int = Field(ge=0)
    distance_to_city_center_km: float = Field(ge=0, allow_inf_nan=False)
    age_of_property: int = Field(ge=0)
    parking: int = Field(ge=0)


class RentPredictionResponse(BaseModel):
    predicted_rent: float
