"""Cloudinary upload orchestration and Firestore metadata persistence."""

import io
import logging
import os

from fastapi import HTTPException

from backend.auth.dependencies import CurrentUser
from backend.repositories.firestore_repository import FirestoreRepository

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_DIMENSION = 8_000
MAX_IMAGE_PIXELS = 40_000_000
ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP", "GIF"}
logger = logging.getLogger(__name__)


class ImageService:
    def __init__(self, repository: FirestoreRepository | None = None):
        self.repository = repository or FirestoreRepository()

    def upload(self, user: CurrentUser, record_id: str, image_bytes: bytes, filename: str) -> dict:
        record = self.repository.get("application_records", record_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Record not found")
        if record.get("kind") not in {"listing", "profile"}:
            raise HTTPException(status_code=400, detail="Images are supported only for listings and profiles")
        if user.role != "admin" and record.get("owner_uid") != user.uid:
            raise HTTPException(status_code=403, detail="Only the record owner may upload images")
        image_format, width, height = self._validate_image(image_bytes)
        try:
            import cloudinary
            import cloudinary.uploader
        except ImportError as exc:
            raise RuntimeError("Image uploads require the cloudinary package") from exc
        cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME")
        api_key = os.getenv("CLOUDINARY_API_KEY")
        api_secret = os.getenv("CLOUDINARY_API_SECRET")
        if not all((cloud_name, api_key, api_secret)):
            raise RuntimeError("Cloudinary environment configuration is incomplete")
        cloudinary.config(cloud_name=cloud_name, api_key=api_key, api_secret=api_secret, secure=True)
        try:
            result = cloudinary.uploader.upload(
                image_bytes,
                folder="rentai",
                resource_type="image",
            )
        except Exception as exc:
            raise RuntimeError("Cloudinary image upload failed") from exc
        public_id = result.get("public_id")
        secure_url = result.get("secure_url")
        if not public_id or not secure_url or not secure_url.startswith("https://"):
            if public_id:
                self._cleanup_cloudinary_asset(cloudinary.uploader, public_id)
            raise RuntimeError("Cloudinary returned incomplete image metadata")
        metadata = {
            "owner_uid": record["owner_uid"],
            "uploader_uid": user.uid,
            "record_id": record_id,
            "url": secure_url,
            "public_id": public_id,
            "format": image_format.lower(),
            "width": width,
            "height": height,
            "bytes": result.get("bytes"),
        }
        try:
            image_id, _ = self.repository.create("application_images", metadata)
        except Exception as exc:
            self._cleanup_cloudinary_asset(cloudinary.uploader, public_id)
            raise RuntimeError("Image metadata persistence failed; cleanup was attempted") from None
        return {"id": image_id, **metadata}

    @staticmethod
    def _cleanup_cloudinary_asset(uploader, public_id: str) -> None:
        try:
            result = uploader.destroy(public_id, resource_type="image", invalidate=True)
            if result.get("result") not in {"ok", "not found"}:
                raise RuntimeError("Cloudinary did not confirm deletion")
        except Exception:
            logger.error(
                "Cloudinary cleanup failed; reconciliation required for public_id=%s",
                public_id,
            )

    @staticmethod
    def _validate_image(image_bytes: bytes) -> tuple[str, int, int]:
        if not image_bytes or len(image_bytes) > MAX_IMAGE_BYTES:
            raise ValueError("Image must be between 1 byte and 10 MiB")
        try:
            from PIL import Image, UnidentifiedImageError
            bomb_error = Image.DecompressionBombError
        except ImportError as exc:
            raise RuntimeError("Image validation requires Pillow") from exc
        try:
            with Image.open(io.BytesIO(image_bytes)) as image:
                image_format = image.format
                width, height = image.size
                if image_format not in ALLOWED_IMAGE_FORMATS:
                    raise ValueError("Unsupported image format")
                if (
                    width < 1
                    or height < 1
                    or width > MAX_IMAGE_DIMENSION
                    or height > MAX_IMAGE_DIMENSION
                    or width * height > MAX_IMAGE_PIXELS
                ):
                    raise ValueError("Image dimensions exceed allowed limits")
                if getattr(image, "n_frames", 1) > 1:
                    raise ValueError("Animated images are not supported")
                image.verify()
            with Image.open(io.BytesIO(image_bytes)) as decoded:
                decoded.load()
            return image_format, width, height
        except (UnidentifiedImageError, OSError, SyntaxError, bomb_error) as exc:
            raise ValueError("Malformed or unsupported image data") from exc
