"""Similar-property recommendation API route."""

from fastapi import APIRouter, HTTPException

from backend.schemas.response_schema import SimilarPropertiesRequest, SimilarPropertiesResponse
from backend.services.clustering_service import find_similar_properties


router = APIRouter(prefix="/ml", tags=["machine learning"])


@router.post("/similar-properties", response_model=SimilarPropertiesResponse)
def similar_properties(request: SimilarPropertiesRequest):
    try:
        properties = find_similar_properties(
            request.property_id, request.limit,
            filters={"city": request.city, "locality": request.locality,
                     "property_type": request.property_type, "min_rent": request.min_rent,
                     "max_rent": request.max_rent},
            features=request.features.model_dump(exclude_unset=True) if request.features else None,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None

    if properties is None:
        raise HTTPException(status_code=404, detail="Property not found")
    return SimilarPropertiesResponse(count=len(properties), properties=properties)
