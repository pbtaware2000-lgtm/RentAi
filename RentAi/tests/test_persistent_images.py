"""Persistent Owner profile and listing images use temporary SQLite storage."""

import io
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from PIL import Image

from backend.repositories import sqlite_repository
from backend.repositories.sqlite_repository import SQLiteRepository
from backend.services.application_service import ApplicationService
from backend.services.image_service import ImageService
from frontend.application_api import upload_image


def _png_bytes():
    stream = io.BytesIO()
    Image.new("RGB", (3, 2), color="orange").save(stream, format="PNG")
    return stream.getvalue()


class FakeUploader:
    def __init__(self):
        self.uploaded = []

    def upload(self, content, **_kwargs):
        self.uploaded.append(content)
        index = len(self.uploaded)
        return {
            "secure_url": f"https://res.cloudinary.com/test/image/upload/photo-{index}.png",
            "public_id": f"rentai/photo-{index}", "format": "png",
            "width": 3, "height": 2, "bytes": len(content),
        }

    def destroy(self, _public_id, **_kwargs):
        return {"result": "ok"}


def _cloudinary_modules(uploader):
    package = types.ModuleType("cloudinary")
    package.config = lambda **_kwargs: None
    upload_module = types.ModuleType("cloudinary.uploader")
    upload_module.upload = uploader.upload
    upload_module.destroy = uploader.destroy
    package.uploader = upload_module
    return package, upload_module


class PersistentImageTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path_patch = patch.object(sqlite_repository, "DB_PATH", Path(self.temp_dir.name) / "test.sqlite3")
        self.db_path_patch.start()
        self.addCleanup(self.db_path_patch.stop)
        self.addCleanup(self.temp_dir.cleanup)
        sqlite_repository.initialize_database()
        self.repository = SQLiteRepository()
        self.owner_profile_id = self._record("owner-1", "profile", {"record_type": "owner_profile", "name": "Owner One"})
        self.other_profile_id = self._record("owner-2", "profile", {"record_type": "owner_profile", "name": "Owner Two"})
        self.listing_id = self._record("owner-1", "listing", {"status": "published", "title": "Saved Home"})

    def _record(self, owner_uid, kind, data):
        record_id, _ = self.repository.create("application_records", {
            "owner_uid": owner_uid, "kind": kind, "data": data,
        })
        return record_id

    def test_profile_and_property_photos_persist_and_are_isolated_by_owner(self):
        uploader = FakeUploader()
        package, upload_module = _cloudinary_modules(uploader)
        owner = type("User", (), {"uid": "owner-1", "role": "owner"})()
        other_owner = type("User", (), {"uid": "owner-2", "role": "owner"})()
        env = {
            "CLOUDINARY_CLOUD_NAME": "test",
            "CLOUDINARY_API_KEY": "test-key",
            "CLOUDINARY_API_SECRET": "test-secret",
        }
        with patch.dict(os.environ, env), patch.dict(sys.modules, {
            "cloudinary": package, "cloudinary.uploader": upload_module,
        }):
            profile_image = ImageService(self.repository).upload(
                owner, self.owner_profile_id, _png_bytes(), "owner.png",
            )
            property_image = ImageService(self.repository).upload(
                owner, self.listing_id, _png_bytes(), "home.png",
            )

        self.assertEqual(len(self.repository.list_images()), 2)
        self.assertEqual(profile_image["record_id"], self.owner_profile_id)
        self.assertEqual(property_image["record_id"], self.listing_id)
        service = ApplicationService(self.repository)
        owner_profiles = service.list_profile_images_for_user(owner)
        other_profiles = service.list_profile_images_for_user(other_owner)
        owner_listings = service.list_listing_images_for_user(owner)
        self.assertEqual([image["url"] for image in owner_profiles], [profile_image["url"]])
        self.assertEqual(other_profiles, [])
        self.assertEqual([image["url"] for image in owner_listings], [property_image["url"]])

    def test_non_owner_cannot_upload_profile_or_listing_photos(self):
        user = type("User", (), {"uid": "owner-2", "role": "owner"})()
        service = ImageService(self.repository)
        for record_id in (self.owner_profile_id, self.listing_id):
            with self.subTest(record_id=record_id), self.assertRaises(HTTPException) as caught:
                service.upload(user, record_id, _png_bytes(), "not-authorized.png")
            self.assertEqual(getattr(caught.exception, "status_code", None), 403)
        self.assertEqual(self.repository.list_images(), [])

    def test_frontend_upload_sends_authenticated_multipart_request(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps({"id": "image-1", "record_id": self.record_id, "url": "https://example.test/photo.png"}).encode()

        response = Response()
        response.record_id = self.listing_id
        fake_streamlit = types.SimpleNamespace(session_state={"rentai_id_token": "session-token"})
        with patch("frontend.application_api.st", fake_streamlit), \
             patch("frontend.application_api.get_api_base_url", return_value="http://127.0.0.1:8000"), \
             patch("frontend.application_api.urlopen", return_value=response) as urlopen:
            result = upload_image(self.listing_id, b"image-bytes", "home photo.png", "image/png")

        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer session-token")
        self.assertTrue(request.get_header("Content-type").startswith("multipart/form-data; boundary="))
        self.assertIn(b'name="file"; filename="home_photo.png"', request.data)
        self.assertIn(b"image-bytes", request.data)
        self.assertEqual(result["record_id"], self.listing_id)


if __name__ == "__main__":
    unittest.main()
