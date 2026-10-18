"""Configuration validation tests without reading Firebase credentials."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.core import config


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.dotenv_patcher = patch("backend.core.config.load_dotenv")
        self.load_dotenv = self.dotenv_patcher.start()
        self.addCleanup(self.dotenv_patcher.stop)

    def test_shared_settings_load_project_root_dotenv_without_overriding_process_env(self):
        with patch.dict(os.environ, {"FIREBASE_PROJECT_ID": "rentai-62ac9"}, clear=True):
            settings = config.load_settings()
        self.load_dotenv.assert_called_once_with(dotenv_path=config.LOCAL_ENV_FILE, override=False)
        self.assertEqual(settings.firebase_project_id, "rentai-62ac9")
        self.assertEqual(settings.rentai_api_url, "http://127.0.0.1:8001")

    def test_web_key_validation_reports_missing_and_wrong_project_without_values(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(config.SettingsError, "FIREBASE_WEB_API_KEY is missing"):
                config.get_web_api_key()
        with patch.dict(os.environ, {
            "FIREBASE_PROJECT_ID": "different-project",
            "FIREBASE_WEB_API_KEY": "test-only-key",
        }, clear=True):
            with self.assertRaisesRegex(config.SettingsError, "does not match") as caught:
                config.get_web_api_key()
        self.assertNotIn("different-project", str(caught.exception))
        self.assertNotIn("test-only-key", str(caught.exception))

    def test_web_key_rejects_non_ascii_and_internal_whitespace(self):
        for key in ("kéy", "key with spaces"):
            with self.subTest(key_type="non-ascii" if "é" in key else "whitespace"), patch.dict(
                os.environ,
                {"FIREBASE_PROJECT_ID": "rentai-62ac9", "FIREBASE_WEB_API_KEY": key},
                clear=True,
            ), self.assertRaises(config.SettingsError):
                config.get_web_api_key()

    def test_settings_trim_and_parse_bootstrap_uids_without_exposing_them(self):
        with patch.dict(os.environ, {
            "FIREBASE_PROJECT_ID": " rentai-62ac9 ",
            "FIREBASE_WEB_API_KEY": " test-only-key ",
            "RENTAI_BOOTSTRAP_ADMIN_UIDS": " uid-one, uid-two ",
        }, clear=True):
            settings = config.load_settings()
        self.assertEqual(settings.firebase_project_id, "rentai-62ac9")
        self.assertEqual(settings.firebase_web_api_key, "test-only-key")
        self.assertEqual(settings.bootstrap_admin_uids, ("uid-one", "uid-two"))
        self.assertEqual(
            set(settings.whitespace_trimmed),
            {"FIREBASE_PROJECT_ID", "FIREBASE_WEB_API_KEY", "RENTAI_BOOTSTRAP_ADMIN_UIDS"},
        )

    def test_admin_credential_path_is_validated_without_reading_file(self):
        with tempfile.TemporaryDirectory() as directory:
            credential_path = Path(directory) / "service-account.json"
            credential_path.touch()
            with patch.dict(os.environ, {
                "FIREBASE_PROJECT_ID": "rentai-62ac9",
                "FIREBASE_CREDENTIALS_PATH": str(credential_path),
            }, clear=True):
                project_id, configured_path = config.get_firebase_admin_settings()
        self.assertEqual(project_id, "rentai-62ac9")
        self.assertEqual(configured_path, credential_path)

    def test_streamlit_api_url_is_validated_and_uses_configured_port(self):
        with patch.dict(os.environ, {"RENTAI_API_URL": "http://127.0.0.1:8001/"}, clear=True):
            self.assertEqual(config.get_api_base_url(), "http://127.0.0.1:8001")
        with patch.dict(os.environ, {"RENTAI_API_URL": "http://remote.example:8001"}, clear=True):
            with self.assertRaisesRegex(config.SettingsError, "HTTPS"):
                config.get_api_base_url()

    def test_startup_diagnostics_name_missing_fields_without_values(self):
        with patch.dict(os.environ, {}, clear=True):
            issues = config.startup_configuration_issues()
        self.assertIn("FIREBASE_PROJECT_ID", issues)
        self.assertIn("FIREBASE_WEB_API_KEY", issues)
        self.assertNotIn("RENTAI_API_URL", issues)
        self.assertNotIn("FIREBASE_CREDENTIALS_PATH", issues)

    def test_startup_diagnostics_detect_invalid_project_and_missing_credential_file(self):
        with patch.dict(os.environ, {
            "FIREBASE_PROJECT_ID": "private-wrong-project",
            "FIREBASE_WEB_API_KEY": "test-only-key",
            "FIREBASE_CREDENTIALS_PATH": "Z:/missing/service-account.json",
        }, clear=True):
            issues = config.startup_configuration_issues()
        self.assertIn("FIREBASE_PROJECT_ID", issues)
        self.assertIn("FIREBASE_CREDENTIALS_PATH", issues)
        self.assertNotIn("private-wrong-project", " ".join(issues))
        self.assertNotIn("test-only-key", " ".join(issues))


if __name__ == "__main__":
    unittest.main()
