"""Rent prediction endpoint backed by an in-memory CSV-trained model."""

from fastapi import APIRouter, HTTPException

from backend.schemas.prediction_schema import RentPredictionRequest, RentPredictionResponse
from backend.services.prediction_service import predict_rent as estimate_rent


router = APIRouter(prefix="/ml", tags=["machine learning"])


@router.post(
    "/rent-prediction",
    response_model=RentPredictionResponse,
    responses={503: {"description": "Dataset unavailable for model training"}},
)
def predict_rent(request: RentPredictionRequest):
    try:
        return {"predicted_rent": estimate_rent(request.model_dump())}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
