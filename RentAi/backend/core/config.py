"""Shared non-secret local configuration."""
import os
from io import StringIO
from pathlib import Path
from urllib.parse import urlsplit
from dotenv import dotenv_values, load_dotenv

PROJECT_ROOT=Path(__file__).resolve().parents[2]
BACKEND_ONLY_ENV_NAMES = {
    "RAZORPAY_KEY_SECRET", "CLOUDINARY_API_SECRET",
    "RENTAI_EMAIL_SMTP_USERNAME", "RENTAI_EMAIL_SMTP_PASSWORD",
}

def _load_shared_project_environment():
    """Load ordinary app settings while keeping the gateway secret backend-only."""
    env_path = PROJECT_ROOT / ".env"
    if not env_path.is_file():
        return
    safe_lines = []
    for line in env_path.read_text(encoding="utf-8").splitlines():
        setting = line.strip()
        if setting.startswith("export "):
            setting = setting[7:].lstrip()
        name = setting.split("=", 1)[0].strip()
        if name not in BACKEND_ONLY_ENV_NAMES:
            safe_lines.append(line)
    load_dotenv(stream=StringIO("\n".join(safe_lines)), override=False)


_load_shared_project_environment()
class SettingsError(RuntimeError): pass

def get_api_base_url():
    value=os.getenv("RENTAI_API_URL","http://127.0.0.1:8001").strip().rstrip("/")
    parsed=urlsplit(value)
    if not parsed.hostname or parsed.username or parsed.password: raise SettingsError("RENTAI_API_URL is invalid.")
    if parsed.scheme!="https" and not(parsed.scheme=="http" and parsed.hostname in {"127.0.0.1","localhost","::1"}): raise SettingsError("RENTAI_API_URL must use HTTPS outside local development.")
    return value

def get_razorpay_test_credentials():
    """Return Razorpay test credentials without exposing the secret to clients."""
    key_id = os.getenv("RAZORPAY_KEY_ID", "").strip()
    key_secret = os.getenv("RAZORPAY_KEY_SECRET", "").strip()
    if not key_secret:
        # Read the secret only on the API path; never export it into shared app
        # environment where the Streamlit process could inherit it.
        try:
            key_secret = str(dotenv_values(PROJECT_ROOT / ".env").get("RAZORPAY_KEY_SECRET") or "").strip()
        except (OSError, UnicodeError):
            raise SettingsError("Razorpay credentials could not be read from the API server configuration.") from None
    if not key_id or not key_secret:
        raise SettingsError("Razorpay test payments are not configured. Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET on the API server.")
    if not key_id.startswith("rzp_test_"):
        raise SettingsError("RentAI accepts Razorpay Test Mode keys only. Replace RAZORPAY_KEY_ID with a test key.")
    if any(char.isspace() for char in key_id + key_secret):
        raise SettingsError("Razorpay credentials are invalid. Check the API server configuration.")
    return key_id, key_secret

def get_cloudinary_credentials():
    """Read image-storage credentials without putting the API secret in shared process env."""
    try:
        file_values = dotenv_values(PROJECT_ROOT / ".env")
    except (OSError, UnicodeError):
        file_values = {}
    cloud_name = (os.getenv("CLOUDINARY_CLOUD_NAME") or file_values.get("CLOUDINARY_CLOUD_NAME") or "").strip()
    api_key = (os.getenv("CLOUDINARY_API_KEY") or file_values.get("CLOUDINARY_API_KEY") or "").strip()
    api_secret = (os.getenv("CLOUDINARY_API_SECRET") or file_values.get("CLOUDINARY_API_SECRET") or "").strip()
    missing = [
        name for name, value in (
            ("CLOUDINARY_CLOUD_NAME", cloud_name),
            ("CLOUDINARY_API_KEY", api_key),
            ("CLOUDINARY_API_SECRET", api_secret),
        ) if not value
    ]
    if missing:
        return None, None, None, missing
    return cloud_name, api_key, api_secret, []

def startup_configuration_issues():
    try: get_api_base_url(); return ()
    except SettingsError: return ("RENTAI_API_URL",)
