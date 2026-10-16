"""Regression tests for the Streamlit-to-FastAPI local account sign-in flow."""

import io
import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.core.auth import register_account
from backend.main import app
from backend.repositories import sqlite_repository
from frontend import authentication


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class LocalAuthFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_directory.cleanup)
        self.database_path_patcher = patch.object(
            sqlite_repository,
            "DB_PATH",
            Path(self.temp_directory.name) / "auth.sqlite3",
        )
        self.database_path_patcher.start()
        self.addCleanup(self.database_path_patcher.stop)
        sqlite_repository.initialize_database()
        self.owner_uid, _session = register_account(
            "owner@example.test", "test-owner-password", "Test Owner", "owner"
        )
        self.client = TestClient(app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.api_url_patcher = patch(
            "frontend.authentication.get_api_base_url",
            return_value="http://testserver",
        )
        self.api_url_patcher.start()
        self.addCleanup(self.api_url_patcher.stop)
        self.urlopen_patcher = patch(
            "frontend.authentication.urlopen", side_effect=self._urlopen
        )
        self.urlopen_patcher.start()
        self.addCleanup(self.urlopen_patcher.stop)
        self.request_paths = []

    def _urlopen(self, request, timeout):
        self.assertEqual(timeout, 12)
        path = urlsplit(request.full_url).path
        self.request_paths.append((request.method, path))
        headers = dict(request.header_items())
        response = self.client.request(
            request.method,
            path,
            content=request.data,
            headers=headers,
        )
        if response.status_code >= 400:
            raise HTTPError(
                request.full_url,
                response.status_code,
                "API error",
                response.headers,
                io.BytesIO(response.content),
            )
        return _Response(response.content)

    def test_owner_sign_in_uses_api_endpoint_and_verifies_sqlite_session(self):
        tokens = authentication.password_sign_in(
            " OWNER@EXAMPLE.TEST ", "test-owner-password"
        )
        user = authentication.fetch_verified_user(tokens["id_token"])

        self.assertEqual(user["uid"], self.owner_uid)
        self.assertEqual(user["role"], "owner")
        self.assertEqual(
            self.request_paths,
            [
                ("POST", "/application/auth/sign-in"),
                ("GET", "/application/auth/me"),
            ],
        )
        self.assertNotIn("password_hash", tokens.get("user", {}))

    def test_invalid_owner_password_returns_authentication_message(self):
        with self.assertRaisesRegex(
            authentication.AuthenticationError,
            "Email or password is incorrect",
        ):
            authentication.password_sign_in(
                "owner@example.test", "incorrect-password"
            )

    def test_malformed_success_response_is_reported_as_auth_error(self):
        with patch(
            "frontend.authentication.urlopen",
            return_value=_Response(b"not-json"),
        ), self.assertRaisesRegex(
            authentication.AuthenticationError,
            "RentAI returned an invalid response",
        ):
            authentication.password_sign_in(
                "owner@example.test", "test-owner-password"
            )

    def test_success_response_without_session_tokens_is_rejected(self):
        with patch(
            "frontend.authentication.urlopen",
            return_value=_Response(json.dumps({"user": {}}).encode()),
        ), self.assertRaisesRegex(
            authentication.AuthenticationError,
            "invalid authentication response",
        ):
            authentication.password_sign_in(
                "owner@example.test", "test-owner-password"
            )


if __name__ == "__main__":
    unittest.main()