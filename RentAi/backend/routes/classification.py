"""Rent classification API route."""

from fastapi import APIRouter, HTTPException

from backend.schemas.response_schema import RentClassificationRequest, RentClassificationResponse
from backend.services.classification_service import classify_rent_details


router = APIRouter(prefix="/ml", tags=["machine learning"])


@router.post("/rent-classification", response_model=RentClassificationResponse)
def classify_rent(request: RentClassificationRequest):
    try:
        result = classify_rent_details(request.model_dump(exclude_unset=True))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    return RentClassificationResponse(**result)
