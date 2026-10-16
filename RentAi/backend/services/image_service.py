"""Cloudinary upload orchestration and SQLite metadata persistence."""

import io
import logging
import os

from fastapi import HTTPException

from backend.auth.dependencies import CurrentUser
from backend.core.config import get_cloudinary_credentials
from backend.repositories.sqlite_repository import SQLiteRepository

DEFAULT_MAX_IMAGE_BYTES = 10 * 1024 * 1024
MIN_CONFIGURABLE_IMAGE_BYTES = 1 * 1024 * 1024
MAX_CONFIGURABLE_IMAGE_BYTES = 25 * 1024 * 1024
logger = logging.getLogger(__name__)


def _max_image_bytes() -> int:
    """Use a safe default and cap overrides to avoid excessive memory use."""
    raw_limit = os.getenv("RENTAI_MAX_IMAGE_UPLOAD_BYTES")
    if not raw_limit:
        return DEFAULT_MAX_IMAGE_BYTES
    try:
        requested = int(raw_limit)
    except (TypeError, ValueError):
        logger.warning("Invalid RENTAI_MAX_IMAGE_UPLOAD_BYTES; using default limit")
        return DEFAULT_MAX_IMAGE_BYTES
    if requested < MIN_CONFIGURABLE_IMAGE_BYTES:
        logger.warning("Image upload limit below 1 MiB; using default limit")
        return DEFAULT_MAX_IMAGE_BYTES
    return min(requested, MAX_CONFIGURABLE_IMAGE_BYTES)


MAX_IMAGE_BYTES = _max_image_bytes()
MAX_IMAGE_DIMENSION = 8_000
MAX_IMAGE_PIXELS = 40_000_000
ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP", "GIF"}


class ImageConfigurationError(RuntimeError):
    """Safe storage configuration failure suitable for returning to the UI."""


def _load_cloudinary_sdk():
    """Import Cloudinary narrowly so dependency errors aren't called an absent SDK."""
    try:
        import cloudinary
    except ModuleNotFoundError as exc:
        if exc.name == "cloudinary":
            logger.error("Cloudinary package is missing from the API Python environment")
            raise ImageConfigurationError(
                "The Cloudinary package is missing from the API environment. Install project requirements and restart FastAPI."
            ) from None
        missing = exc.name or "unknown module"
        logger.error("Cloudinary import dependency is missing; module=%s", missing)
        raise ImageConfigurationError(
            f"Cloudinary could not load because API dependency '{missing}' is missing. Reinstall project requirements and restart FastAPI."
        ) from None
    except ImportError as exc:
        module = getattr(exc, "name", None) or "unknown module"
        logger.error("Cloudinary package import failed; error_type=%s module=%s", type(exc).__name__, module)
        raise ImageConfigurationError(
            f"Cloudinary could not load in the API environment (import error in '{module}'). Reinstall project requirements and restart FastAPI."
        ) from None

    try:
        import cloudinary.uploader
    except ModuleNotFoundError as exc:
        missing = exc.name or "unknown module"
        logger.error("Cloudinary uploader import dependency is missing; module=%s", missing)
        if missing == "cloudinary.uploader":
            detail = "The installed Cloudinary package has no uploader module. Reinstall project requirements and restart FastAPI."
        else:
            detail = f"Cloudinary's uploader depends on missing API module '{missing}'. Reinstall project requirements and restart FastAPI."
        raise ImageConfigurationError(detail) from None
    except ImportError as exc:
        module = getattr(exc, "name", None) or "unknown module"
        logger.error("Cloudinary uploader import failed; error_type=%s module=%s", type(exc).__name__, module)
        raise ImageConfigurationError(
            f"Cloudinary's uploader failed to import module '{module}'. Reinstall project requirements and restart FastAPI."
        ) from None
    return cloudinary, cloudinary.uploader


class ImageService:
    def __init__(self, repository: SQLiteRepository | None = None):
        self.repository = repository or SQLiteRepository()

    def upload(self, user: CurrentUser, record_id: str, image_bytes: bytes, filename: str) -> dict:
        record = self.repository.get("application_records", record_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Record not found")
        if record.get("kind") not in {"listing", "profile"}:
            raise HTTPException(status_code=400, detail="Images are supported only for listings and profiles")
        if user.role != "admin" and record.get("owner_uid") != user.uid:
            raise HTTPException(status_code=403, detail="Only the record owner may upload images")
        image_format, width, height = self._validate_image(image_bytes)
        cloud_name, api_key, api_secret, missing = get_cloudinary_credentials()
        if missing:
            logger.warning("Cloudinary configuration incomplete; missing_settings=%s", ",".join(missing))
            raise ImageConfigurationError(
                "Image storage is not configured. Set backend variables: " + ", ".join(missing)
            )
        cloudinary, uploader = _load_cloudinary_sdk()
        try:
            cloudinary.config(cloud_name=cloud_name, api_key=api_key, api_secret=api_secret, secure=True)
        except Exception as exc:
            logger.warning("Cloudinary initialization failed; error_type=%s", type(exc).__name__)
            raise ImageConfigurationError(
                "Cloudinary could not initialize. Verify CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, and CLOUDINARY_API_SECRET on the API server."
            ) from None
        try:
            result = uploader.upload(
                image_bytes,
                folder="rentai",
                resource_type="image",
            )
        except Exception as exc:
            status_code = getattr(exc, "http_code", None)
            logger.warning(
                "Cloudinary upload failed; error_type=%s http_status=%s",
                type(exc).__name__, status_code if isinstance(status_code, int) else "unknown",
            )
            if status_code in {401, 403}:
                raise ImageConfigurationError(
                    "Cloudinary rejected the configured credentials. Verify CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET on the API server."
                ) from None
            if status_code == 404:
                raise ImageConfigurationError(
                    "Cloudinary could not find the configured cloud. Verify CLOUDINARY_CLOUD_NAME on the API server."
                ) from None
            raise RuntimeError("Cloudinary image upload failed") from None
        public_id = result.get("public_id")
        secure_url = result.get("secure_url")
        if not public_id or not secure_url or not secure_url.startswith("https://"):
            if public_id:
                self._cleanup_cloudinary_asset(uploader, public_id)
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
            self._cleanup_cloudinary_asset(uploader, public_id)
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
            raise ValueError(f"Image must be between 1 byte and {MAX_IMAGE_BYTES / (1024 * 1024):g} MiB")
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
