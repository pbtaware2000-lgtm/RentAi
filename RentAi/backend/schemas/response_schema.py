"""Response contracts for future rent classification and similarity services."""

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RentClassificationRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    # rent is deliberately not accepted as a model feature: the class is a rent band.
    area_sqft: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    bedrooms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    bathrooms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    balcony: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    floor: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    total_floors: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    furnishing_status: str | None = None
    parking: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    lift: str | None = None
    gym: str | None = None
    security: str | None = None
    power_backup: str | None = None
    swimming_pool: str | None = None
    air_conditioning: str | None = None
    water_supply: str | None = None
    gated_community: str | None = None
    age_of_property: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    distance_to_city_center_km: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    distance_to_metro_km: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    distance_to_school_km: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    distance_to_hospital_km: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    distance_to_it_park_km: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    city: str = Field(min_length=1)
    property_type: str = Field(min_length=1)
    locality: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def require_prediction_context(self):
        if not self.city or not self.property_type:
            raise ValueError("city and property_type are required")
        return self


class RentClassificationResponse(BaseModel):
    rent_category: str
    probabilities: dict[str, float] | None = None
    probability_type: str | None = None
    model_name: str
    features: dict
    test_metrics: dict


class SimilarPropertyFeatures(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    rent: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    area_sqft: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    bedrooms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    bathrooms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    balcony: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    city: str | None = None
    locality: str | None = None
    property_type: str | None = None
    furnishing_status: str | None = None

    @model_validator(mode="after")
    def require_any_characteristic(self):
        if not self.model_fields_set:
            raise ValueError("Provide at least one property characteristic.")
        return self


class SimilarPropertiesRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    property_id: str | None = Field(default=None, min_length=1)
    features: SimilarPropertyFeatures | None = None
    limit: int = Field(default=5, ge=1, le=20)
    city: str | None = Field(default=None, min_length=1)
    locality: str | None = Field(default=None, min_length=1)
    property_type: str | None = Field(default=None, min_length=1)
    min_rent: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    max_rent: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_query(self):
        if not self.property_id and not self.features:
            raise ValueError("Provide property_id or property characteristics.")
        if self.min_rent is not None and self.max_rent is not None and self.min_rent > self.max_rent:
            raise ValueError("min_rent cannot be greater than max_rent")
        return self


class SimilarPropertyFeaturesRequest(BaseModel):
    city: str = Field(min_length=1)
    locality: str = Field(min_length=1)
    property_type: str = Field(min_length=1)
    furnishing_status: str = Field(min_length=1)
    rent: float = Field(gt=0, allow_inf_nan=False)
    area_sqft: float = Field(gt=0, allow_inf_nan=False)
    bedrooms: int = Field(ge=0)
    bathrooms: int = Field(ge=0)
    distance_to_city_center_km: float = Field(ge=0, allow_inf_nan=False)
    age_of_property: int = Field(ge=0)
    limit: int = Field(default=5, ge=1, le=20)


class SimilarPropertiesResponse(BaseModel):
    count: int
    properties: list[dict]
