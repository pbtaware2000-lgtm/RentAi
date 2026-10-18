"""Safe local image handling shared by property cards and owner listing forms."""

from __future__ import annotations

import hashlib
import io
import uuid
from pathlib import Path

import streamlit as st
from PIL import Image, ImageOps, UnidentifiedImageError

from backend.core.config import PROJECT_ROOT


IMAGE_DIRECTORY = PROJECT_ROOT / "assets" / "property_images"
OWNER_IMAGE_DIRECTORY = IMAGE_DIRECTORY / "owner_uploads"
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_IMAGE_DIMENSION = 8_000
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def validate_owner_image(uploaded_file) -> tuple[bytes, str]:
    """Decode and re-encode an uploaded photo to a safe, normalized JPEG."""
    raw = uploaded_file.getvalue()
    if not raw or len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError("Choose an image smaller than 8 MB.")
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in {"JPEG", "PNG", "WEBP"}:
                raise ValueError("Use a JPEG, PNG, or WebP image.")
            width, height = image.size
            if width < 1 or height < 1 or max(width, height) > MAX_IMAGE_DIMENSION or width * height > 40_000_000:
                raise ValueError("Image dimensions are too large.")
            if getattr(image, "n_frames", 1) != 1:
                raise ValueError("Animated images are not supported.")
            image.verify()
        with Image.open(io.BytesIO(raw)) as image:
            image = ImageOps.exif_transpose(image).convert("RGB")
            image.thumbnail((2400, 2400), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=88, optimize=True)
            return output.getvalue(), f"{uuid.uuid4().hex}.jpg"
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise ValueError("The selected file is not a valid supported image.") from exc


def store_owner_image(uploaded_file) -> str:
    """Persist a validated owner upload and return its portable relative path."""
    content, filename = validate_owner_image(uploaded_file)
    OWNER_IMAGE_DIRECTORY.mkdir(parents=True, exist_ok=True)
    target = OWNER_IMAGE_DIRECTORY / filename
    temporary = target.with_suffix(".tmp")
    try:
        temporary.write_bytes(content)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return (Path("assets") / "property_images" / "owner_uploads" / filename).as_posix()


def _local_image_path(image_path: object) -> Path | None:
    if isinstance(image_path, Path):
        candidate = image_path
    elif isinstance(image_path, str) and image_path.strip():
        candidate = Path(image_path)
        if candidate.is_absolute():
            return None
    else:
        return None
    try:
        resolved = (candidate if candidate.is_absolute() else PROJECT_ROOT / candidate).resolve(strict=True)
        resolved.relative_to(IMAGE_DIRECTORY.resolve())
    except (OSError, ValueError):
        return None
    if resolved.suffix.lower() not in _IMAGE_SUFFIXES or not resolved.is_file():
        return None
    return resolved


def _illustrative_sample(prop: dict) -> Path | None:
    image_id = str(prop.get("property_id") or prop.get("id") or "")
    property_type = str(prop.get("property_type") or "").casefold()
    if not IMAGE_DIRECTORY.exists():
        return None
    if any(word in property_type for word in ("house", "villa")):
        candidates = sorted({
            *IMAGE_DIRECTORY.glob("house_*"), *IMAGE_DIRECTORY.glob("villa_*"),
            *IMAGE_DIRECTORY.glob("studio_3.*"),
        })
    elif "apartment" in property_type or "studio" in property_type or "flat" in property_type:
        candidates = sorted({
            *IMAGE_DIRECTORY.glob("apartment_*"), *IMAGE_DIRECTORY.glob("studio_*"),
        } - set(IMAGE_DIRECTORY.glob("studio_3.*")))
    else:
        candidates = sorted({*IMAGE_DIRECTORY.glob("property_*"), *IMAGE_DIRECTORY.glob("sample_*")})
    if not candidates:
        candidates = sorted(path for path in IMAGE_DIRECTORY.iterdir()
                            if path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES)
    candidates = [path for path in candidates if path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES]
    if not candidates:
        return None
    stable_key = f"{property_type}:{image_id}" if image_id else property_type
    index = int(hashlib.sha256(stable_key.encode("utf-8")).hexdigest()[:8], 16) % len(candidates)
    return candidates[index]


@st.cache_data(show_spinner=False)
def _crop_local_image(path: str) -> bytes:
    """Crop local photos to 16:9 without stretching the original image."""
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        target_ratio = 16 / 9
        width, height = image.size
        if width / height > target_ratio:
            crop_width = int(height * target_ratio)
            left = (width - crop_width) // 2
            image = image.crop((left, 0, left + crop_width, height))
        else:
            crop_height = int(width / target_ratio)
            top = (height - crop_height) // 2
            image = image.crop((0, top, width, top + crop_height))
        image.thumbnail((1440, 810), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=86, optimize=True)
        return output.getvalue()


def render_property_image(prop: dict, source: str, key_suffix: str = "image") -> bool:
    """Render a card photo, returning whether it is an illustrative dataset sample."""
    image_source = prop.get("image_path") or prop.get("image_url") or prop.get("image") or prop.get("photo_url")
    illustrative = source == "catalog"
    if illustrative and not image_source:
        image_source = _illustrative_sample(prop)

    prop_id = str(prop.get("property_id") or prop.get("id") or "")
    safe_id = "".join(char if char.isalnum() or char in "-_" else "-" for char in prop_id)[:80]
    safe_suffix = "".join(char if char.isalnum() or char in "-_" else "-" for char in key_suffix)[:40]
    with st.container(key=f"rentai-property-photo-{source}-{safe_suffix}-{safe_id}"):
        if isinstance(image_source, str) and image_source.startswith(("https://", "http://")):
            try:
                st.image(image_source, width="stretch")
                return illustrative
            except Exception:
                pass
        local_path = _local_image_path(image_source)
        if local_path:
            try:
                st.image(_crop_local_image(str(local_path)), width="stretch")
                return illustrative
            except (OSError, ValueError, UnidentifiedImageError):
                pass
        st.markdown(
            "<div class='renter-property-placeholder' role='img' aria-label='Property image unavailable'>Photo unavailable</div>",
            unsafe_allow_html=True,
        )
        return False
