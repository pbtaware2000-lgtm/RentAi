import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query

from backend.core.config import startup_configuration_issues

from backend.routes.classification import router as classification_router
from backend.routes.clustering import router as clustering_router
from backend.routes.application import admin_router as application_admin_router
from backend.routes.application import auth_router as application_auth_router
from backend.routes.application import router as application_router
from backend.routes.images import router as images_router
from backend.routes.prediction import router as prediction_router

from backend.services.property_service import (
    count_matching_properties,
    get_all_properties,
    get_property_count,
    get_property_by_id,
    get_properties_by_ids,
    search_properties,
)

@asynccontextmanager
async def lifespan(_app):
    issues = startup_configuration_issues()
    if issues:
        logging.getLogger(__name__).warning(
            "RentAI configuration needs attention; fields=%s",
            ",".join(issues),
        )
    yield


app = FastAPI(
    title="RentAI API",
    description="API for the RentAI rental property analysis system",
    version="1.0.0",
    lifespan=lifespan,
)
app.include_router(prediction_router)
app.include_router(classification_router)
app.include_router(clustering_router)
app.include_router(application_router)
app.include_router(application_admin_router)
app.include_router(application_auth_router)
app.include_router(images_router)


@app.get("/properties/compare")
def compare_properties(
    property_ids: list[str] = Query(..., min_length=2, max_length=4),
):
    if any(not property_id.strip() for property_id in property_ids):
        raise HTTPException(
            status_code=400,
            detail="property_ids cannot be empty",
        )
    if len(set(property_ids)) != len(property_ids):
        raise HTTPException(
            status_code=400,
            detail="property_ids must be unique",
        )

    try:
        properties, missing_ids = get_properties_by_ids(property_ids)
        if missing_ids:
            raise HTTPException(
                status_code=404,
                detail={"message": "Properties not found", "property_ids": missing_ids},
            )
        locations = {
            (str(property_record.get("city", "")), str(property_record.get("locality", "")))
            for property_record in properties
        }
        if any(
            not city.strip()
            or not locality.strip()
            or city.casefold() in {"nan", "none", "<na>"}
            or locality.casefold() in {"nan", "none", "<na>"}
            for city, locality in locations
        ):
            raise HTTPException(
                status_code=400,
                detail="Every compared property must have a city and locality.",
            )
        if len(locations) != 1:
            raise HTTPException(
                status_code=400,
                detail="All compared properties must belong to the same city and locality.",
            )
        return {"count": len(properties), "properties": properties}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/")
def root():
    return {
        "message": "Welcome to RentAI API",
        "status": "running",
    }


@app.get("/health")
def health_check():
    return {
        "status": "healthy",
    }


@app.get("/properties/count")
def property_count():
    try:
        return {
            "total_properties": get_property_count()
        }
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        )


@app.get("/properties")
def list_properties(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    try:
        properties = get_all_properties(
            limit=limit,
            offset=offset,
        )

        return {
            "total": get_property_count(),
            "limit": limit,
            "offset": offset,
            "returned": len(properties),
            "properties": properties,
        }

    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        )


@app.get("/properties/search")
def property_search(
    city: str | None = Query(default=None, min_length=1),
    locality: str | None = Query(default=None, min_length=1),
    min_rent: float | None = Query(default=None, ge=0, allow_inf_nan=False),
    max_rent: float | None = Query(default=None, ge=0, allow_inf_nan=False),
    bedrooms: int | None = Query(default=None, ge=1),
    property_type: str | None = Query(default=None, min_length=1),
    sort_by: str = Query(
        default="rent",
        pattern="^(rent|area_sqft|bedrooms)$",
    ),
    order: str = Query(
        default="asc",
        pattern="^(asc|desc)$",
    ),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    if any(value is not None and not value.strip() for value in (city, locality, property_type)):
        raise HTTPException(status_code=400, detail="Text filters cannot be empty")

    city = city.strip() if city else None
    locality = locality.strip() if locality else None
    property_type = property_type.strip() if property_type else None

    if (
        min_rent is not None
        and max_rent is not None
        and min_rent > max_rent
    ):
        raise HTTPException(
            status_code=400,
            detail="min_rent cannot be greater than max_rent",
        )

    try:
        properties = search_properties(
            city=city,
            locality=locality,
            min_rent=min_rent,
            max_rent=max_rent,
            bedrooms=bedrooms,
            property_type=property_type,
            sort_by=sort_by,
            order=order,
            limit=limit,
            offset=offset,
        )

        return {
            "total_matching": count_matching_properties(
                city=city,
                locality=locality,
                min_rent=min_rent,
                max_rent=max_rent,
                bedrooms=bedrooms,
                property_type=property_type,
            ),
            "sort_by": sort_by,
            "order": order,
            "limit": limit,
            "offset": offset,
            "returned": len(properties),
            "properties": properties,
        }

    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )


@app.get("/properties/{property_id}")
def property_details(property_id: str):
    try:
        property_record = get_property_by_id(property_id)

        if property_record is None:
            raise HTTPException(
                status_code=404,
                detail="Property not found",
            )

        return property_record

    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        )
