"""Gateway credentials remain in the backend configuration boundary."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.core import config


class RazorpayConfigurationTests(unittest.TestCase):
    def test_shared_dotenv_loading_does_not_export_gateway_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, ".env").write_text(
                "RENTAI_API_URL=http://127.0.0.1:8001\n"
                "RAZORPAY_KEY_ID=rzp_test_public\n"
                "RAZORPAY_KEY_SECRET=server-only-secret-value\n"
                "RAZORPAY_WEBHOOK_SECRET=server-only-webhook-value\n",
                encoding="utf-8",
            )
            with patch.object(config, "PROJECT_ROOT", Path(directory)), patch.dict(os.environ, {}, clear=True):
                config._load_shared_project_environment()
                self.assertEqual(os.environ["RENTAI_API_URL"], "http://127.0.0.1:8001")
                self.assertEqual(os.environ["RAZORPAY_KEY_ID"], "rzp_test_public")
                self.assertNotIn("RAZORPAY_KEY_SECRET", os.environ)
                self.assertNotIn("RAZORPAY_WEBHOOK_SECRET", os.environ)
                self.assertEqual(
                    config.get_razorpay_test_credentials(),
                    ("rzp_test_public", "server-only-secret-value"),
                )
                self.assertEqual(config.get_razorpay_webhook_secret(), "server-only-webhook-value")

    def test_missing_or_live_credentials_fail_safely(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(config, "PROJECT_ROOT", Path(directory)), patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(config.SettingsError, "not configured"):
                    config.get_razorpay_test_credentials()
                os.environ["RAZORPAY_KEY_ID"] = "rzp_live_not-allowed"
                os.environ["RAZORPAY_KEY_SECRET"] = "secret-value"
                with self.assertRaisesRegex(config.SettingsError, "Test Mode") as error:
                    config.get_razorpay_test_credentials()
                self.assertNotIn("secret-value", str(error.exception))

    def test_example_placeholders_are_not_treated_as_configured_credentials(self):
        with patch.dict(os.environ, {
            "RAZORPAY_KEY_ID": "rzp_test_replace_with_key_id",
            "RAZORPAY_KEY_SECRET": "replace_with_test_key_secret",
            "RAZORPAY_WEBHOOK_SECRET": "replace_with_webhook_secret",
        }, clear=True):
            status = config.razorpay_configuration_status()
        self.assertFalse(status["configured"])
        self.assertEqual(status["missing"], ["RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET"])

    def test_valid_test_credentials_do_not_require_webhook_secret(self):
        with patch.dict(os.environ, {
            "RAZORPAY_KEY_ID": "rzp_test_public",
            "RAZORPAY_KEY_SECRET": "server-only-secret-value",
        }, clear=True):
            status = config.razorpay_configuration_status()
        self.assertTrue(status["configured"])
        self.assertEqual(status["missing"], [])


if __name__ == "__main__":
    unittest.main()
