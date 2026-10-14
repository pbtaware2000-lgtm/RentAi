"""Focused tests for Firebase sign-in helpers and role-aware navigation."""

import ast
import contextlib
import io
import json
import socket
import ssl
import unittest
from pathlib import Path
from urllib.error import HTTPError, URLError
from unittest.mock import patch
from streamlit.testing.v1 import AppTest

from frontend.authentication import (
    AuthenticationError,
    accessible_pages,
    clear_auth_session,
    current_session_user,
    fetch_verified_user,
    firebase_password_sign_in,
    firebase_password_sign_up,
    firebase_api_key_matches,
    firebase_api_key_diagnostics,
    page_is_allowed,
    save_session,
    _firebase_error_details,
)


PAGE_NAMES = [
    "Home", "Learning Module", "Implementation Overview", "Dataset & EDA",
    "Rent Prediction", "Rent Classification", "Property Clustering",
    "Property Search", "Property Comparison", "Location & Analytics",
    "Property Insights", "Algo Lab", "Admin & Reports",
]


class AuthNavigationTests(unittest.TestCase):
    def test_password_sign_in_uses_firebase_and_returns_short_lived_tokens(self):
        payload = {
            "idToken": "id-token", "refreshToken": "refresh-token",
            "localId": "user-1", "expiresIn": "3600",
        }

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()

        def fake_urlopen(request, timeout):
            self.assertEqual(timeout, 12)
            self.assertIn("accounts:signInWithPassword", request.full_url)
            self.assertIn("key=test-key", request.full_url)
            sent_payload = json.loads(request.data)
            self.assertEqual(set(sent_payload), {"email", "password", "returnSecureToken"})
            self.assertEqual(sent_payload["email"], "vishal@gmail.com")
            self.assertEqual(sent_payload["password"], "password")
            self.assertIs(sent_payload["returnSecureToken"], True)
            self.assertIsInstance(sent_payload["email"], str)
            self.assertIsInstance(sent_payload["password"], str)
            return Response(json.dumps(payload).encode())

        with patch.dict("os.environ", {"FIREBASE_WEB_API_KEY": "test-key", "FIREBASE_PROJECT_ID": "rentai-62ac9"}), patch(
            "frontend.authentication.urlopen", side_effect=fake_urlopen
        ):
            result = firebase_password_sign_in(" vishal@gmail.com ", "password", now=100)
        self.assertEqual(result["uid"], "user-1")
        self.assertEqual(result["expires_at"], 3700)
        self.assertNotIn("password", result)

    def test_missing_firebase_web_key_fails_closed(self):
        with patch.dict("os.environ", {}, clear=True), patch(
            "backend.core.config.LOCAL_ENV_FILE", Path("missing-test-env")
        ):
            with self.assertRaisesRegex(AuthenticationError, "not configured"):
                firebase_password_sign_in("person@example.com", "password")

    def test_empty_form_values_get_local_validation_message(self):
        with self.assertRaisesRegex(AuthenticationError, "Enter your email and password"):
            firebase_password_sign_in("  ", "password")
        with self.assertRaisesRegex(AuthenticationError, "Enter your email and password"):
            firebase_password_sign_in("person@example.com", "")

    def test_incomplete_or_spaced_email_is_rejected_before_firebase_request(self):
        for email in ("vishal", "vishal@gmail", "vishal @gmail.com", "vishal@gmail. com"):
            with self.subTest(email=email), patch("frontend.authentication.urlopen") as send_request:
                with self.assertRaisesRegex(AuthenticationError, "valid complete email address"):
                    firebase_password_sign_in(email, "test-password")
                send_request.assert_not_called()

    def test_signup_sends_complete_trimmed_email_address(self):
        response_payload = {
            "idToken": "id-token", "refreshToken": "refresh-token",
            "localId": "new-user", "expiresIn": "3600",
        }

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()

        def fake_urlopen(request, timeout):
            self.assertIn("accounts:signUp", request.full_url)
            self.assertEqual(timeout, 12)
            sent_payload = json.loads(request.data)
            self.assertEqual(sent_payload["email"], "vishal@gmail.com")
            self.assertEqual(sent_payload["displayName"], "Vishal")
            return Response(json.dumps(response_payload).encode())

        with patch.dict("os.environ", {"FIREBASE_WEB_API_KEY": "test-key", "FIREBASE_PROJECT_ID": "rentai-62ac9"}), patch(
            "frontend.authentication.urlopen", side_effect=fake_urlopen
        ):
            result = firebase_password_sign_up(" vishal@gmail.com ", "password", " Vishal ")
        self.assertEqual(result["uid"], "new-user")

    def test_firebase_invalid_email_and_email_exists_codes_have_distinct_messages(self):
        cases = (
            ("INVALID_EMAIL", "Enter a valid complete email address"),
            ("EMAIL_EXISTS", "An account with this email already exists. Sign in instead."),
        )
        for firebase_code, expected_message in cases:
            error = HTTPError(
                "https://firebase.invalid", 400, "bad request", {},
                io.BytesIO(json.dumps({"error": {"message": firebase_code}}).encode()),
            )
            with self.subTest(firebase_code=firebase_code), patch.dict(
                "os.environ", {"FIREBASE_WEB_API_KEY": "test-key", "FIREBASE_PROJECT_ID": "rentai-62ac9"}
            ), patch("frontend.authentication.urlopen", side_effect=error) as send_request:
                with self.assertRaisesRegex(AuthenticationError, expected_message):
                    if firebase_code == "INVALID_EMAIL":
                        firebase_password_sign_in("vishal@gmail.com", "test-password")
                    else:
                        firebase_password_sign_up("vishal@gmail.com", "test-password", "Vishal")
                send_request.assert_called_once()

    def test_firebase_api_key_and_provider_errors_are_explained(self):
        cases = (
            ("API_KEY_INVALID", "FIREBASE_WEB_API_KEY"),
            ("API_KEY_SERVICE_BLOCKED", "API restrictions"),
            ("OPERATION_NOT_ALLOWED", "Email/Password sign-in is disabled"),
            ("INVALID_LOGIN_CREDENTIALS", "Email or password is incorrect"),
            ("EMAIL_NOT_FOUND", "Email or password is incorrect"),
        )
        for firebase_code, expected_message in cases:
            error_body = json.dumps({"error": {"message": firebase_code}}).encode()
            error = HTTPError("https://firebase.invalid", 400, "bad request", {}, io.BytesIO(error_body))
            with self.subTest(firebase_code=firebase_code), patch.dict(
                "os.environ", {"FIREBASE_WEB_API_KEY": "test-key", "FIREBASE_PROJECT_ID": "rentai-62ac9"}
            ), patch("frontend.authentication.urlopen", side_effect=error):
                with self.assertLogs("frontend.authentication", level="WARNING") as captured:
                    with self.assertRaisesRegex(AuthenticationError, expected_message) as raised:
                        firebase_password_sign_in("person@example.com", "sensitive-test-pass-value")
                self.assertIn(f"Firebase error code: {firebase_code}", str(raised.exception))
                diagnostic = "\n".join(captured.output)
                self.assertNotIn("test-key", diagnostic)
                self.assertNotIn("sensitive-test-pass-value", diagnostic)

    def test_unknown_firebase_error_code_is_preserved_safely(self):
        error = HTTPError(
            "https://firebase.invalid",
            400,
            "bad request",
            {},
            io.BytesIO(json.dumps({"error": {"message": "UNSUPPORTED_TEST_ERROR"}}).encode()),
        )
        with patch.dict("os.environ", {"FIREBASE_WEB_API_KEY": "test-secret-key", "FIREBASE_PROJECT_ID": "rentai-62ac9"}), patch(
            "frontend.authentication.urlopen", side_effect=error
        ):
            with self.assertLogs("frontend.authentication", level="WARNING") as captured:
                with self.assertRaisesRegex(AuthenticationError, "UNSUPPORTED_TEST_ERROR"):
                    firebase_password_sign_in("person@example.com", "sensitive-test-password")
        diagnostic = "\n".join(captured.output)
        self.assertIn("code=UNSUPPORTED_TEST_ERROR", diagnostic)
        self.assertNotIn("test-secret-key", diagnostic)
        self.assertNotIn("sensitive-test-password", diagnostic)

    def test_invalid_argument_diagnostic_records_message_source_and_payload_shape(self):
        body = {"error": {"code": 400, "message": "INVALID_ARGUMENT", "status": "BAD_REQUEST"}}
        error = HTTPError("https://firebase.invalid", 400, "bad request", {}, io.BytesIO(json.dumps(body).encode()))
        with patch.dict("os.environ", {"FIREBASE_WEB_API_KEY": "private-api-key", "FIREBASE_PROJECT_ID": "rentai-62ac9"}), patch(
            "frontend.authentication.urlopen", side_effect=error
        ):
            with self.assertLogs("frontend.authentication", level="WARNING") as captured:
                with self.assertRaisesRegex(AuthenticationError, "INVALID_ARGUMENT"):
                    firebase_password_sign_in("private@example.com", "private-password")
        diagnostic = "\n".join(captured.output)
        self.assertIn("http_status=400", diagnostic)
        self.assertIn("code=INVALID_ARGUMENT", diagnostic)
        self.assertIn("code_source=error.message", diagnostic)
        self.assertIn("json_error_code=400", diagnostic)
        self.assertIn("error_message=INVALID_ARGUMENT", diagnostic)
        self.assertIn("error_status=BAD_REQUEST", diagnostic)
        self.assertIn("payload_fields=email,password,returnSecureToken", diagnostic)
        self.assertIn("payload_types=email:str,password:str,returnSecureToken:bool", diagnostic)
        for secret in ("private-api-key", "private@example.com", "private-password"):
            self.assertNotIn(secret, diagnostic)

    def test_firebase_message_diagnostic_redacts_request_secrets(self):
        body = {"error": {"message": "Rejected private@example.com private-password"}}
        error = HTTPError("https://firebase.invalid", 400, "bad request", {}, io.BytesIO(json.dumps(body).encode()))
        with patch.dict("os.environ", {"FIREBASE_WEB_API_KEY": "private-api-key", "FIREBASE_PROJECT_ID": "rentai-62ac9"}), patch(
            "frontend.authentication.urlopen", side_effect=error
        ):
            with self.assertLogs("frontend.authentication", level="WARNING") as captured:
                with self.assertRaises(AuthenticationError):
                    firebase_password_sign_in("private@example.com", "private-password")
        diagnostic = "\n".join(captured.output)
        self.assertIn("code_source=error.message", diagnostic)
        self.assertNotIn("private-api-key", diagnostic)
        self.assertNotIn("private@example.com", diagnostic)
        self.assertNotIn("private-password", diagnostic)

    def test_signin_log_keeps_json_error_fields_distinct(self):
        body = {"error": {"code": 400, "message": "Request rejected", "status": "INVALID_ARGUMENT"}}
        error = HTTPError("https://firebase.invalid", 400, "bad request", {}, io.BytesIO(json.dumps(body).encode()))
        with patch.dict("os.environ", {"FIREBASE_WEB_API_KEY": "key-for-test", "FIREBASE_PROJECT_ID": "rentai-62ac9"}), patch(
            "frontend.authentication.urlopen", side_effect=error
        ):
            with self.assertLogs("frontend.authentication", level="WARNING") as captured:
                with self.assertRaisesRegex(AuthenticationError, "INVALID_ARGUMENT"):
                    firebase_password_sign_in("person@example.com", "password-for-test")
        diagnostic = captured.output[0]
        self.assertIn("code_source=error.status", diagnostic)
        self.assertIn("json_error_code=400", diagnostic)
        self.assertIn("error_message=Request rejected", diagnostic)
        self.assertIn("error_status=INVALID_ARGUMENT", diagnostic)

    def test_firebase_error_extraction_uses_complete_codes_and_safe_fallbacks(self):
        cases = (
            ({"error": {"message": "API_KEY_INVALID"}}, "API_KEY_INVALID"),
            ({"error": {"message": "INVALID_LOGIN_CREDENTIALS"}}, "INVALID_LOGIN_CREDENTIALS"),
            ({"error": {"message": "API key is invalid"}}, ""),
            ({"error": {"message": "Request rejected", "status": "INVALID_ARGUMENT"}}, "INVALID_ARGUMENT"),
            ({"error": {"message": "Request rejected", "details": [{"reason": "API_KEY_SERVICE_BLOCKED"}]}}, "API_KEY_SERVICE_BLOCKED"),
            ({"error": {"message": "API_KEY"}}, ""),
            ({"error": {"message": 400}}, ""),
            ({"unexpected": "shape"}, ""),
        )
        from frontend.authentication import _extract_firebase_error_code

        for body, expected in cases:
            with self.subTest(body=body):
                self.assertEqual(_extract_firebase_error_code(body), expected)

    def test_firebase_error_diagnostic_names_the_field_that_contains_the_code(self):
        cases = (
            ({"error": {"message": "INVALID_ARGUMENT", "status": "BAD_REQUEST"}}, "error.message"),
            ({"error": {"message": "Request rejected", "status": "INVALID_ARGUMENT"}}, "error.status"),
            ({"error": {"message": "Request rejected", "details": [{"reason": "INVALID_ARGUMENT"}]}}, "error.details[].reason"),
        )
        for body, expected_source in cases:
            with self.subTest(expected_source=expected_source):
                code, source, json_code, message, status = _firebase_error_details(body)
                self.assertEqual(code, "INVALID_ARGUMENT")
                self.assertEqual(source, expected_source)
                self.assertEqual(json_code, "unavailable")
                self.assertEqual(message, body["error"]["message"])
                self.assertEqual(status, body["error"].get("status", "unavailable"))

    def test_firebase_key_check_compares_without_exposing_values(self):
        with patch.dict("os.environ", {
            "FIREBASE_WEB_API_KEY": "configured-key",
            "FIREBASE_PROJECT_ID": "rentai-62ac9",
        }):
            self.assertTrue(firebase_api_key_matches("configured-key"))
            self.assertFalse(firebase_api_key_matches("different-key"))
        with patch.dict("os.environ", {
            "FIREBASE_WEB_API_KEY": "configured-key",
            "FIREBASE_PROJECT_ID": "another-project",
        }):
            self.assertFalse(firebase_api_key_matches("configured-key"))

    def test_firebase_key_check_handles_empty_missing_and_non_ascii_values(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(firebase_api_key_matches("configured-key"))
        with patch.dict("os.environ", {
            "FIREBASE_WEB_API_KEY": "",
            "FIREBASE_PROJECT_ID": "rentai-62ac9",
        }):
            self.assertFalse(firebase_api_key_matches("configured-key"))
        with patch.dict("os.environ", {
            "FIREBASE_WEB_API_KEY": "configured-key",
            "FIREBASE_PROJECT_ID": "rentai-62ac9",
        }):
            self.assertFalse(firebase_api_key_matches(""))
            self.assertFalse(firebase_api_key_matches("kéy"))
        with patch.dict("os.environ", {
            "FIREBASE_WEB_API_KEY": "kéy",
            "FIREBASE_PROJECT_ID": "rentai-62ac9",
        }):
            self.assertFalse(firebase_api_key_matches("kéy"))

    def test_firebase_key_checker_cli_completes_without_printing_keys(self):
        from frontend import firebase_key_check

        secret = "test-only-ascii-key"
        output = io.StringIO()
        with patch.dict("os.environ", {
            "FIREBASE_WEB_API_KEY": secret,
            "FIREBASE_PROJECT_ID": "rentai-62ac9",
        }), patch("frontend.firebase_key_check.getpass", return_value=secret), contextlib.redirect_stdout(output):
            self.assertEqual(firebase_key_check.main(), 0)
        self.assertIn("match", output.getvalue())
        self.assertNotIn(secret, output.getvalue())

    def test_firebase_key_diagnostics_identify_each_configuration_failure(self):
        cases = (
            ({"FIREBASE_WEB_API_KEY": "console-key"}, {
                "project_id_present": False, "project_id_matches": None,
                "web_api_key_present": True, "key_matches_console": True,
            }),
            ({"FIREBASE_PROJECT_ID": "rentai-62ac9"}, {
                "project_id_present": True, "project_id_matches": True,
                "web_api_key_present": False, "key_matches_console": None,
            }),
            ({"FIREBASE_PROJECT_ID": "other-project", "FIREBASE_WEB_API_KEY": "console-key"}, {
                "project_id_present": True, "project_id_matches": False,
                "web_api_key_present": True, "key_matches_console": True,
            }),
            ({"FIREBASE_PROJECT_ID": "rentai-62ac9", "FIREBASE_WEB_API_KEY": "configured-key"}, {
                "project_id_present": True, "project_id_matches": True,
                "web_api_key_present": True, "key_matches_console": False,
            }),
        )
        for environment, expected in cases:
            with self.subTest(environment_keys=tuple(environment)), patch(
                "backend.core.config.load_dotenv"
            ), patch.dict("os.environ", environment, clear=True):
                result = firebase_api_key_diagnostics("console-key")
                for field, value in expected.items():
                    self.assertEqual(result[field], value)
                self.assertFalse(result["whitespace_trimmed"])

    def test_firebase_key_diagnostics_reports_trimmed_whitespace(self):
        with patch.dict("os.environ", {
            "FIREBASE_PROJECT_ID": " rentai-62ac9 ",
            "FIREBASE_WEB_API_KEY": " configured-key ",
        }):
            result = firebase_api_key_diagnostics(" configured-key ")
        self.assertTrue(result["project_id_matches"])
        self.assertTrue(result["key_matches_console"])
        self.assertTrue(result["whitespace_trimmed"])

    def test_malformed_or_missing_firebase_error_has_no_partial_code(self):
        response_bodies = (b"not-json", b"{}", b'{"error":{"message":"API key invalid"}}')
        for response_body in response_bodies:
            error = HTTPError(
                "https://firebase.invalid", 400, "bad request", {}, io.BytesIO(response_body)
            )
            with self.subTest(response_body=response_body), patch.dict(
                "os.environ", {"FIREBASE_WEB_API_KEY": "test-key", "FIREBASE_PROJECT_ID": "rentai-62ac9"}
            ), patch("frontend.authentication.urlopen", side_effect=error):
                with self.assertRaises(AuthenticationError) as caught:
                    firebase_password_sign_in("person@example.com", "test-password")
                self.assertIn("error code unavailable", str(caught.exception))
                self.assertNotIn("Firebase error code: API.", str(caught.exception))

    def test_firebase_error_code_is_displayed_in_streamlit_login_ui(self):
        response_body = {
            "error": {
                "code": 400,
                "message": "INVALID_LOGIN_CREDENTIALS",
                "errors": [{"message": "INVALID_LOGIN_CREDENTIALS", "reason": "invalid"}],
            }
        }
        error = HTTPError(
            "https://firebase.invalid",
            400,
            "bad request",
            {},
            io.BytesIO(json.dumps(response_body).encode()),
        )
        with patch.dict("os.environ", {"FIREBASE_WEB_API_KEY": "test-secret-key", "FIREBASE_PROJECT_ID": "rentai-62ac9"}), patch(
            "frontend.authentication.urlopen", side_effect=error
        ):
            app = AppTest.from_string(
                "from frontend.pages.login import render\nrender()",
                default_timeout=30,
            ).run()
            self.assertFalse(app.exception)
            for widget in app.text_input:
                if widget.key == "rentai_login_email":
                    widget.set_value("person@example.com")
                elif widget.key == "rentai_login_password":
                    widget.set_value("sensitive-test-password")
            app.button[0].click().run()

        self.assertFalse(app.exception)
        self.assertTrue(
            any("Firebase error code: INVALID_LOGIN_CREDENTIALS" in error.value for error in app.error),
            [error.value for error in app.error],
        )

    def test_existing_admin_login_flow_saves_backend_verified_admin_role(self):
        with patch("frontend.pages.login.firebase_password_sign_in", return_value={
            "id_token": "admin-token", "refresh_token": "refresh", "expires_at": 9999999999,
        }), patch("frontend.pages.login.fetch_verified_user", return_value={"uid": "admin-uid", "role": "admin"}):
            app = AppTest.from_string(
                "from frontend.pages.login import render\nrender()", default_timeout=30
            ).run()
            app.text_input(key="rentai_login_email").set_value("admin@example.com")
            app.text_input(key="rentai_login_password").set_value("test-password")
            app.button[0].click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["rentai_uid"], "admin-uid")
        self.assertEqual(app.session_state["rentai_role"], "admin")

    def test_role_sign_in_rejects_account_with_different_verified_role(self):
        with patch("frontend.pages.login.firebase_password_sign_in", return_value={
            "id_token": "renter-token", "refresh_token": "refresh", "expires_at": 9999999999,
        }), patch(
            "frontend.pages.login.fetch_verified_user", return_value={"uid": "renter-uid", "role": "renter"}
        ):
            app = AppTest.from_string(
                "from frontend.pages.login import render\n"
                "render(selected_role='admin', allow_registration=False)", default_timeout=30
            ).run()
            app.text_input(key="rentai_login_email").set_value("vishal@gmail.com")
            app.text_input(key="rentai_login_password").set_value("test-password")
            app.button[0].click().run()
        self.assertFalse(app.exception)
        self.assertTrue(any("registered as Renter" in error.value for error in app.error))
        self.assertNotIn("rentai_id_token", app.session_state)
        self.assertNotIn("rentai_role", app.session_state)

    def test_role_selection_landing_routes_admin_to_sign_in_only(self):
        app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py", default_timeout=30).run()
        self.assertFalse(app.exception)
        self.assertEqual(
            {button.key for button in app.button if button.key and button.key.startswith("role_select_")},
            {"role_select_admin", "role_select_owner", "role_select_renter", "role_select_broker"},
        )
        app.button(key="role_select_admin").click().run()
        self.assertFalse(app.exception)
        self.assertIn("Admin sign in", [item.value for item in app.title])
        self.assertFalse(any(button.key == "open_registration" for button in app.button))
        self.assertIn("auth_back_to_roles", [button.key for button in app.button])

    def test_each_public_role_has_its_own_sign_in_and_registration_view(self):
        for role in ("owner", "renter", "broker"):
            with self.subTest(role=role):
                app = AppTest.from_file(
                    Path(__file__).resolve().parents[1] / "app.py", default_timeout=30
                ).run()
                app.button(key=f"role_select_{role}").click().run()
                self.assertFalse(app.exception)
                self.assertIn(f"{role.title()} sign in", [item.value for item in app.title])
                app.button(key="open_registration").click().run()
                self.assertFalse(app.exception)
                self.assertIn(f"Create your {role.title()} account", [item.value for item in app.title])
                self.assertFalse(any(item.key == "rentai_register_role" for item in app.selectbox))

    def test_admin_and_mismatched_role_cannot_render_public_registration(self):
        for session_role, render_role in (("admin", "admin"), ("renter", "owner")):
            with self.subTest(session_role=session_role, render_role=render_role):
                app = AppTest.from_string(
                    "import streamlit as st\n"
                    f"st.session_state['rentai_selected_role'] = '{session_role}'\n"
                    "from frontend.pages.register import render\n"
                    f"render('{render_role}')", default_timeout=30
                ).run()
                self.assertFalse(app.exception)
                self.assertEqual([error.value for error in app.error], [
                    "Choose an account type before registering."
                ])
                self.assertFalse(app.text_input)

    def test_login_form_submits_the_trimmed_complete_address(self):
        with patch("frontend.pages.login.firebase_password_sign_in", return_value={
            "id_token": "login-token", "refresh_token": "refresh", "expires_at": 9999999999,
        }) as sign_in, patch(
            "frontend.pages.login.fetch_verified_user", return_value={"uid": "renter-uid", "role": "renter"}
        ):
            app = AppTest.from_string(
                "from frontend.pages.login import render\nrender()", default_timeout=30
            ).run()
            app.text_input(key="rentai_login_email").set_value(" vishal@gmail.com ")
            app.text_input(key="rentai_login_password").set_value("test-password")
            app.button[0].click().run()
        self.assertFalse(app.exception)
        sign_in.assert_called_once_with("vishal@gmail.com", "test-password")

    def test_existing_email_registration_does_not_provision_role_or_retry_signup(self):
        from frontend.authentication import AuthenticationError

        with patch(
            "frontend.pages.register.firebase_password_sign_up",
            side_effect=AuthenticationError("An account with this email already exists. Sign in instead."),
        ) as sign_up, patch("frontend.pages.register.provision_public_role") as provision_role:
            app = AppTest.from_string(
                "import streamlit as st\n"
                "st.session_state['rentai_selected_role'] = 'renter'\n"
                "from frontend.pages.register import render\nrender('renter')", default_timeout=30
            ).run()
            app.text_input(key="rentai_register_name").set_value("Vishal")
            app.text_input(key="rentai_register_email").set_value(" vishal@gmail.com ")
            app.text_input(key="rentai_register_password").set_value("test-password")
            app.text_input(key="rentai_register_confirm").set_value("test-password")
            next(button for button in app.button if button.label == "Register").click().run()
        self.assertFalse(app.exception)
        sign_up.assert_called_once_with("vishal@gmail.com", "test-password", "Vishal")
        provision_role.assert_not_called()
        self.assertIn("An account with this email already exists. Sign in instead.", [error.value for error in app.error])

    def test_successful_registration_provisions_only_the_selected_verified_role(self):
        tokens = {"id_token": "id-token", "refresh_token": "refresh", "expires_at": 9999999999}
        user = {"uid": "broker-uid", "role": "broker"}
        with patch("frontend.pages.register.firebase_password_sign_up", return_value=tokens) as sign_up, patch(
            "frontend.pages.register.provision_public_role", return_value={"uid": "broker-uid", "role": "broker"}
        ) as provision_role, patch(
            "frontend.pages.register.fetch_verified_user", return_value=user
        ), patch("frontend.pages.register.save_session") as save_session, patch(
            "frontend.pages.register.st.rerun"
        ):
            app = AppTest.from_string(
                "import streamlit as st\n"
                "st.session_state['rentai_selected_role'] = 'broker'\n"
                "from frontend.pages.register import render\nrender('broker')", default_timeout=30
            ).run()
            app.text_input(key="rentai_register_name").set_value("Vishal")
            app.text_input(key="rentai_register_email").set_value("vishal@gmail.com")
            app.text_input(key="rentai_register_password").set_value("test-password")
            app.text_input(key="rentai_register_confirm").set_value("test-password")
            next(button for button in app.button if button.label == "Register").click().run()
        self.assertFalse(app.exception)
        sign_up.assert_called_once_with("vishal@gmail.com", "test-password", "Vishal")
        provision_role.assert_called_once_with("id-token", "broker")
        save_session.assert_called_once()
        self.assertEqual(save_session.call_args.args[1:], (tokens, user))
        self.assertEqual(app.session_state["page"], "Broker Dashboard")

    def test_registration_does_not_open_workspace_when_backend_role_differs(self):
        tokens = {"id_token": "id-token", "refresh_token": "refresh", "expires_at": 9999999999}
        with patch("frontend.pages.register.firebase_password_sign_up", return_value=tokens), patch(
            "frontend.pages.register.provision_public_role"
        ) as provision_role, patch(
            "frontend.pages.register.fetch_verified_user", return_value={"uid": "owner-uid", "role": "owner"}
        ), patch("frontend.pages.register.save_session") as save_session, patch(
            "frontend.pages.register.st.rerun"
        ):
            app = AppTest.from_string(
                "import streamlit as st\n"
                "st.session_state['rentai_selected_role'] = 'renter'\n"
                "from frontend.pages.register import render\nrender('renter')", default_timeout=30
            ).run()
            app.text_input(key="rentai_register_name").set_value("Vishal")
            app.text_input(key="rentai_register_email").set_value("vishal@gmail.com")
            app.text_input(key="rentai_register_password").set_value("test-password")
            app.text_input(key="rentai_register_confirm").set_value("test-password")
            next(button for button in app.button if button.label == "Register").click().run()
        self.assertFalse(app.exception)
        provision_role.assert_called_once_with("id-token", "renter")
        save_session.assert_not_called()
        self.assertIn("could not verify the selected account type", [error.value for error in app.error][0])

    def test_login_registration_navigation_survives_unauthenticated_reruns(self):
        app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py", default_timeout=30).run()
        app.button(key="role_select_owner").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["rentai_selected_role"], "owner")
        self.assertIn("Owner sign in", [item.value for item in app.title])

        app.button(key="open_registration").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["rentai_auth_view"], "register")
        self.assertIn("Create your Owner account", [item.value for item in app.title])
        self.assertFalse(any(item.key == "rentai_register_role" for item in app.selectbox))

        app.button(key="registration_login_link").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["rentai_auth_view"], "signin")
        self.assertIn("Owner sign in", [item.value for item in app.title])
        app.button(key="auth_back_to_roles").click().run()
        self.assertFalse(app.exception)
        self.assertNotIn("rentai_selected_role", app.session_state)
        self.assertEqual(len([button for button in app.button if button.key.startswith("role_select_")]), 4)

    def test_missing_authentication_clears_credentials_without_resetting_public_route(self):
        state = {"page": "Register", "rentai_id_token": "", "rentai_role": "owner"}
        self.assertIsNone(current_session_user(state))
        self.assertEqual(state["page"], "Register")
        self.assertNotIn("rentai_role", state)

    def test_registration_rejects_empty_fields_before_auth_request(self):
        app = AppTest.from_string(
            "import streamlit as st\n"
            "st.session_state['rentai_selected_role'] = 'owner'\n"
            "from frontend.pages.register import render\nrender('owner')", default_timeout=30
        ).run()
        app.button[0].click().run()
        self.assertFalse(app.exception)
        self.assertIn("Enter your full name, email address, and password.", [e.value for e in app.error])

    def test_backend_expired_token_and_unassigned_role_messages(self):
        cases = (
            (401, "session expired"),
            (403, "not assigned a RentAI role"),
        )
        for status, expected_message in cases:
            error = HTTPError("http://localhost", status, "request rejected", {}, io.BytesIO(b"{}"))
            with self.subTest(status=status), patch(
                "frontend.authentication.urlopen", side_effect=error
            ):
                with self.assertRaisesRegex(AuthenticationError, expected_message):
                    fetch_verified_user("test-id-token")

    def test_firebase_transport_failures_are_classified_without_logging_secrets(self):
        cases = (
            (URLError(socket.gaierror("private host detail")), "DNS"),
            (URLError(ConnectionRefusedError("private connection detail")), "connect"),
            (TimeoutError("private timeout detail"), "timed out"),
            (URLError(ssl.SSLCertVerificationError("private certificate detail")), "TLS"),
        )
        for failure, expected_message in cases:
            with self.subTest(category=expected_message), patch.dict(
                "os.environ", {"FIREBASE_WEB_API_KEY": "test-secret-api-key", "FIREBASE_PROJECT_ID": "rentai-62ac9"}
            ), patch("frontend.authentication.urlopen", side_effect=failure):
                with self.assertLogs("frontend.authentication", level="WARNING") as captured:
                    with self.assertRaisesRegex(AuthenticationError, expected_message):
                        firebase_password_sign_in("person@example.com", "test-secret-password")
                diagnostics = "\n".join(captured.output)
                self.assertNotIn("test-secret-api-key", diagnostics)
                self.assertNotIn("test-secret-password", diagnostics)
                self.assertNotIn("private", diagnostics)

    def test_backend_connection_refused_has_specific_safe_diagnostic(self):
        failure = URLError(ConnectionRefusedError("private backend detail"))
        with patch("frontend.authentication.urlopen", side_effect=failure):
            with self.assertLogs("frontend.authentication", level="WARNING") as captured:
                with self.assertRaisesRegex(AuthenticationError, "Could not connect"):
                    fetch_verified_user("test-id-token")
        self.assertIn("service=rentai_api category=connection_refused", captured.output[0])
        self.assertNotIn("test-id-token", captured.output[0])
        self.assertNotIn("private backend detail", captured.output[0])

    def test_role_navigation_filters_pages_and_hides_admin_from_non_admin(self):
        self.assertIn("Property Search", accessible_pages("renter", PAGE_NAMES))
        self.assertNotIn("Admin & Reports", accessible_pages("owner", PAGE_NAMES))
        self.assertEqual(accessible_pages("admin", PAGE_NAMES), PAGE_NAMES)
        self.assertFalse(page_is_allowed("broker", "Admin & Reports", PAGE_NAMES))
        with self.assertRaises(AuthenticationError):
            accessible_pages("unknown", PAGE_NAMES)

    @patch("frontend.authentication.fetch_verified_user", return_value={"uid": "u1", "role": "renter"})
    @patch("frontend.authentication.refresh_firebase_session")
    def test_expiring_session_refreshes_then_uses_backend_role(self, refresh, verified_user):
        refresh.return_value = {
            "id_token": "new-id", "refresh_token": "new-refresh", "expires_at": 5000,
        }
        state = {
            "rentai_id_token": "old-id", "rentai_refresh_token": "refresh",
            "rentai_expires_at": 100,
            "rentai_uid": "u1", "rentai_role": "admin",
        }
        result = current_session_user(state, now=1000)
        self.assertEqual(result, {"uid": "u1", "role": "renter"})
        self.assertEqual(state["rentai_id_token"], "new-id")
        self.assertEqual(state["rentai_role"], "renter")
        refresh.assert_called_once_with("refresh", 1000)
        verified_user.assert_called_once_with("new-id")

    def test_logout_discards_tokens_and_resets_navigation(self):
        state = {
            "rentai_id_token": "id", "rentai_refresh_token": "refresh",
            "rentai_login_password": "password", "page": "Admin & Reports",
        }
        clear_auth_session(state, clear_login_inputs=True)
        self.assertEqual(state, {"page": "Home"})

    def test_unauthenticated_rerun_preserves_submitted_login_widget_values(self):
        state = {
            "rentai_login_email": "person@example.com",
            "rentai_login_password": "submitted-password",
        }
        self.assertIsNone(current_session_user(state, now=100))
        self.assertEqual(state["rentai_login_email"], "person@example.com")
        self.assertEqual(state["rentai_login_password"], "submitted-password")

    def test_existing_streamlit_page_registry_is_preserved(self):
        source = Path("app.py").read_text(encoding="utf-8")
        module = ast.parse(source)
        registry = next(
            node.value for node in module.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "PAGE_RENDERERS" for target in node.targets)
        )
        registered = [key.value for key in registry.keys]
        self.assertEqual(registered, PAGE_NAMES)


if __name__ == "__main__":
    unittest.main()
