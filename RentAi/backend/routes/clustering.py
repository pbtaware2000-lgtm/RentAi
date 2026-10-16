"""Similar-property recommendation API route."""

from fastapi import APIRouter, HTTPException

from backend.schemas.response_schema import (
    SimilarPropertiesRequest,
    SimilarPropertiesResponse,
    SimilarPropertyFeaturesRequest,
)
from backend.services.clustering_service import (
    find_similar_properties,
    find_similar_properties_for_features,
)


router = APIRouter(prefix="/ml", tags=["machine learning"])


@router.post("/similar-properties", response_model=SimilarPropertiesResponse)
def similar_properties(request: SimilarPropertiesRequest):
    try:
        properties = find_similar_properties(request.property_id, request.limit)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if properties is None:
        raise HTTPException(status_code=404, detail="Property not found")
    return SimilarPropertiesResponse(count=len(properties), properties=properties)


@router.post("/similar-properties/search", response_model=SimilarPropertiesResponse)
def similar_properties_for_profile(request: SimilarPropertyFeaturesRequest):
    try:
        properties = find_similar_properties_for_features(
            request.model_dump(exclude={"limit"}), request.limit
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail="Property dataset is unavailable") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return SimilarPropertiesResponse(count=len(properties), properties=properties)
