"""Authenticated image upload route for application records."""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from backend.auth.dependencies import CurrentUser, get_current_user
from backend.schemas.application_schemas import ImageMetadataResponse
from backend.services.image_service import ImageService

router = APIRouter(prefix="/application/records", tags=["images"])


@router.post("/{record_id}/images", response_model=ImageMetadataResponse, status_code=201)
async def upload_image(
    record_id: str,
    file: UploadFile = File(...),
    user: CurrentUser = Depends(get_current_user),
):
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="An image file is required")
    content = await file.read(10 * 1024 * 1024 + 1)
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image exceeds the 10 MiB upload limit")
    try:
        return ImageService().upload(user, record_id, content, file.filename or "upload")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Image service unavailable") from exc
