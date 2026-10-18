"""Image byte validation, ownership metadata, and Cloudinary cleanup tests."""

import io
import asyncio
import builtins
import os
import sys
import types
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.datastructures import UploadFile
from starlette.datastructures import Headers
from PIL import Image

from backend.services.image_service import ImageConfigurationError, ImageService, _load_cloudinary_sdk
from backend.core.config import get_cloudinary_credentials
from backend.routes.images import upload_image


def png_bytes(size=(2, 2)):
    stream = io.BytesIO()
    Image.new("RGB", size, color="orange").save(stream, format="PNG")
    return stream.getvalue()


class Repository:
    def __init__(self, fail_create=False):
        self.fail_create = fail_create
        self.saved = None

    def get(self, collection, record_id):
        return {"id": record_id, "owner_uid": "owner-1", "kind": "listing"}

    def create(self, collection, data):
        if self.fail_create:
            raise RuntimeError("simulated Firestore failure")
        self.saved = data
        return "image-1", data


class FakeUploader:
    def __init__(self):
        self.destroyed = []

    def upload(self, image_bytes, **kwargs):
        return {
            "secure_url": "https://res.cloudinary.com/test/image/upload/sample.png",
            "public_id": "rentai/sample",
            "format": "png",
            "width": 2,
            "height": 2,
            "bytes": len(image_bytes),
        }

    def destroy(self, public_id, **kwargs):
        self.destroyed.append((public_id, kwargs))
        return {"result": "ok"}


def cloudinary_modules(uploader):
    package = types.ModuleType("cloudinary")
    package.config = lambda **kwargs: None
    upload_module = types.ModuleType("cloudinary.uploader")
    upload_module.upload = uploader.upload
    upload_module.destroy = uploader.destroy
    package.uploader = upload_module
    return package, upload_module


class ImageServiceTests(unittest.TestCase):
    def test_non_owner_cannot_upload_to_record(self):
        service = ImageService(Repository())
        user = type("User", (), {"uid": "owner-2", "role": "owner"})()
        with self.assertRaises(HTTPException) as caught:
            service.upload(user, "listing-1", b"not-read", "image.png")
        self.assertEqual(caught.exception.status_code, 403)

    def test_malformed_image_and_spoofed_image_mime_data_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Malformed"):
            ImageService._validate_image(b"not actually a jpeg")

    def test_route_rejects_malformed_bytes_with_spoofed_image_mime(self):
        file = UploadFile(
            filename="spoofed.jpg",
            file=io.BytesIO(b"not actually a jpeg"),
            headers=Headers({"content-type": "image/jpeg"}),
        )
        owner = type("User", (), {"uid": "owner-1", "role": "owner"})()
        with patch("backend.routes.images.ImageService", return_value=ImageService(Repository())):
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(upload_image("listing-1", file, owner))
        self.assertEqual(caught.exception.status_code, 400)

    def test_actual_format_and_dimension_limits_are_enforced(self):
        self.assertEqual(ImageService._validate_image(png_bytes()), ("PNG", 2, 2))
        with self.assertRaisesRegex(ValueError, "dimensions"):
            ImageService._validate_image(png_bytes((8001, 1)))
        with self.assertRaisesRegex(ValueError, "10 MiB"):
            ImageService._validate_image(b"x" * (10 * 1024 * 1024 + 1))

    def test_missing_cloudinary_configuration_names_are_reported_without_values(self):
        owner = type("User", (), {"uid": "owner-1", "role": "owner"})()
        with patch("backend.services.image_service.get_cloudinary_credentials", return_value=(None, None, None, [
            "CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET",
        ])):
            with self.assertRaises(ImageConfigurationError) as caught:
                ImageService(Repository()).upload(owner, "listing-1", png_bytes(), "image.png")
        message = str(caught.exception)
        self.assertIn("CLOUDINARY_CLOUD_NAME", message)
        self.assertIn("CLOUDINARY_API_KEY", message)
        self.assertIn("CLOUDINARY_API_SECRET", message)
        self.assertNotIn("test-secret", message)

    def test_cloudinary_credentials_are_read_from_backend_dotenv_without_shared_env_export(self):
        config_values = {
            "CLOUDINARY_CLOUD_NAME": "cloud-test",
            "CLOUDINARY_API_KEY": "key-test",
            "CLOUDINARY_API_SECRET": "secret-test",
        }
        with patch.dict("os.environ", {}, clear=True), patch(
            "backend.core.config.dotenv_values", return_value=config_values
        ):
            values = get_cloudinary_credentials()
        self.assertEqual(values, ("cloud-test", "key-test", "secret-test", []))
        self.assertNotIn("CLOUDINARY_API_SECRET", os.environ)

    def test_route_returns_safe_missing_configuration_error(self):
        file = UploadFile(filename="photo.png", file=io.BytesIO(png_bytes()), headers=Headers({"content-type": "image/png"}))
        owner = type("User", (), {"uid": "owner-1", "role": "owner"})()
        with patch("backend.services.image_service.get_cloudinary_credentials", return_value=(None, None, None, [
            "CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET",
        ])), \
             patch("backend.routes.images.ImageService", return_value=ImageService(Repository())):
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(upload_image("listing-1", file, owner))
        self.assertEqual(caught.exception.status_code, 503)
        self.assertIn("CLOUDINARY_API_SECRET", caught.exception.detail)

    def test_missing_sdk_has_actionable_backend_response(self):
        file = UploadFile(filename="photo.png", file=io.BytesIO(png_bytes()), headers=Headers({"content-type": "image/png"}))
        owner = type("User", (), {"uid": "owner-1", "role": "owner"})()
        with patch("backend.services.image_service.get_cloudinary_credentials", return_value=("cloud", "key", "secret", [])), \
             patch.dict(sys.modules, {"cloudinary": None, "cloudinary.uploader": None}), \
             patch("backend.routes.images.ImageService", return_value=ImageService(Repository())):
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(upload_image("listing-1", file, owner))
        self.assertEqual(caught.exception.status_code, 503)
        self.assertIn("Cloudinary package is missing", caught.exception.detail)

    def test_internal_cloudinary_import_dependency_is_not_mislabeled_as_missing_sdk(self):
        package = types.ModuleType("cloudinary")
        original_import = builtins.__import__

        def dependency_failure(name, *args, **kwargs):
            if name == "cloudinary.uploader":
                raise ModuleNotFoundError("No module named 'internal_crypto'", name="internal_crypto")
            return original_import(name, *args, **kwargs)

        with patch.dict(sys.modules, {"cloudinary": package}), patch(
            "builtins.__import__", side_effect=dependency_failure
        ):
            with self.assertRaises(ImageConfigurationError) as caught:
                _load_cloudinary_sdk()
        self.assertIn("internal_crypto", str(caught.exception))
        self.assertNotIn("package is missing", str(caught.exception))

    def test_cloudinary_credential_rejection_names_settings_without_values(self):
        class RejectedUploader(FakeUploader):
            def upload(self, *_args, **_kwargs):
                error = RuntimeError("provider response intentionally not included")
                error.http_code = 401
                raise error

        uploader = RejectedUploader()
        package, upload_module = cloudinary_modules(uploader)
        owner = type("User", (), {"uid": "owner-1", "role": "owner"})()
        with patch("backend.services.image_service.get_cloudinary_credentials", return_value=("cloud", "key", "secret", [])), \
             patch.dict(sys.modules, {"cloudinary": package, "cloudinary.uploader": upload_module}):
            with self.assertRaisesRegex(ImageConfigurationError, "Verify CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET"):
                ImageService(Repository()).upload(owner, "listing-1", png_bytes(), "image.png")

    @patch.dict(
        "os.environ",
        {"CLOUDINARY_CLOUD_NAME": "test", "CLOUDINARY_API_KEY": "key", "CLOUDINARY_API_SECRET": "secret"},
    )
    def test_metadata_separates_record_owner_from_admin_uploader(self):
        uploader = FakeUploader()
        package, upload_module = cloudinary_modules(uploader)
        repository = Repository()
        admin = type("User", (), {"uid": "admin-1", "role": "admin"})()
        with patch.dict(sys.modules, {"cloudinary": package, "cloudinary.uploader": upload_module}):
            result = ImageService(repository).upload(admin, "listing-1", png_bytes(), "image.png")
        self.assertEqual(result["owner_uid"], "owner-1")
        self.assertEqual(result["uploader_uid"], "admin-1")
        self.assertEqual(repository.saved["format"], "png")

    @patch.dict(
        "os.environ",
        {"CLOUDINARY_CLOUD_NAME": "test", "CLOUDINARY_API_KEY": "key", "CLOUDINARY_API_SECRET": "secret"},
    )
    def test_metadata_persistence_failure_triggers_cloudinary_cleanup(self):
        uploader = FakeUploader()
        package, upload_module = cloudinary_modules(uploader)
        owner = type("User", (), {"uid": "owner-1", "role": "owner"})()
        with patch.dict(sys.modules, {"cloudinary": package, "cloudinary.uploader": upload_module}):
            with self.assertRaisesRegex(RuntimeError, "cleanup was attempted"):
                ImageService(Repository(fail_create=True)).upload(
                    owner, "listing-1", png_bytes(), "image.png"
                )
        self.assertEqual(uploader.destroyed[0][0], "rentai/sample")


if __name__ == "__main__":
    unittest.main()
