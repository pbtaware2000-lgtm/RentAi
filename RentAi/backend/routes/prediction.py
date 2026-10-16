"""Rent prediction endpoint backed by an in-memory CSV-trained model."""

from fastapi import APIRouter, HTTPException

from backend.schemas.prediction_schema import RentPredictionRequest, RentPredictionResponse
from backend.services.prediction_service import predict_rent_details


router = APIRouter(prefix="/ml", tags=["machine learning"])


@router.post(
    "/rent-prediction",
    response_model=RentPredictionResponse,
    responses={503: {"description": "Model artifact is missing or incompatible"}},
)
def predict_rent(request: RentPredictionRequest):
    try:
        return predict_rent_details(request.model_dump())
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
