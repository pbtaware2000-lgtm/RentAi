"""Rent classification API route."""

from fastapi import APIRouter, HTTPException

from backend.schemas.response_schema import RentClassificationRequest, RentClassificationResponse
from backend.services.classification_service import classify_rent as classify_rent_category


router = APIRouter(prefix="/ml", tags=["machine learning"])


@router.post("/rent-classification", response_model=RentClassificationResponse)
def classify_rent(request: RentClassificationRequest):
    try:
        category = classify_rent_category(request.model_dump())
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return RentClassificationResponse(rent_category=category)
