"""Tests for the local provisioning client; all authentication/API calls are mocked."""

import contextlib
import io
import unittest
from unittest.mock import patch

from backend import role_provisioning


class RoleProvisioningCliTests(unittest.TestCase):
    def test_bootstrap_uses_existing_token_helper_and_never_prints_tokens_or_passwords(self):
        secret_token = "test-id-token-not-for-output"
        secret_password = "test-password-not-for-output"
        output = io.StringIO()
        with patch("builtins.input", return_value="trusted@example.com"), patch(
            "backend.role_provisioning.getpass", return_value=secret_password
        ), patch(
            "backend.role_provisioning.firebase_password_sign_in",
            return_value={"id_token": secret_token, "refresh_token": "refresh-secret", "uid": "uid-1"},
        ), patch("backend.role_provisioning._call_provisioning_api", return_value=204) as call, contextlib.redirect_stdout(output):
            result = role_provisioning.main(["bootstrap"])
        self.assertEqual(result, 0)
        call.assert_called_once_with("/application/admin/bootstrap", "POST", secret_token)
        self.assertIn("bootstrap succeeded", output.getvalue().lower())
        for secret in (secret_token, secret_password, "refresh-secret"):
            self.assertNotIn(secret, output.getvalue())

    def test_role_grant_uses_the_existing_admin_endpoint_and_selected_role(self):
        output = io.StringIO()
        with patch("builtins.input", return_value="admin@example.com"), patch(
            "backend.role_provisioning.getpass", return_value="password"
        ), patch(
            "backend.role_provisioning.firebase_password_sign_in",
            return_value={"id_token": "id-token", "refresh_token": "refresh", "uid": "admin-uid"},
        ), patch("backend.role_provisioning._call_provisioning_api", return_value=204) as call, contextlib.redirect_stdout(output):
            result = role_provisioning.main(["grant", "renter-uid", "--role", "renter"])
        self.assertEqual(result, 0)
        call.assert_called_once_with(
            "/application/admin/users/renter-uid/role", "PUT", "id-token", {"role": "renter"}
        )


if __name__ == "__main__":
    unittest.main()
