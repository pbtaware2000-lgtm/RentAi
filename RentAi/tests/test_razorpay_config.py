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
                "RAZORPAY_KEY_SECRET=server-only-secret-value\n",
                encoding="utf-8",
            )
            with patch.object(config, "PROJECT_ROOT", Path(directory)), patch.dict(os.environ, {}, clear=True):
                config._load_shared_project_environment()
                self.assertEqual(os.environ["RENTAI_API_URL"], "http://127.0.0.1:8001")
                self.assertEqual(os.environ["RAZORPAY_KEY_ID"], "rzp_test_public")
                self.assertNotIn("RAZORPAY_KEY_SECRET", os.environ)
                self.assertEqual(
                    config.get_razorpay_test_credentials(),
                    ("rzp_test_public", "server-only-secret-value"),
                )

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


if __name__ == "__main__":
    unittest.main()
