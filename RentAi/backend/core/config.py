"""Shared non-secret local configuration."""
import os
from io import StringIO
from pathlib import Path
from urllib.parse import urlsplit
from dotenv import dotenv_values, load_dotenv

PROJECT_ROOT=Path(__file__).resolve().parents[2]
BACKEND_ONLY_ENV_NAMES = {
    "RAZORPAY_KEY_SECRET", "RAZORPAY_WEBHOOK_SECRET", "CLOUDINARY_API_SECRET",
    "RENTAI_EMAIL_SMTP_USERNAME", "RENTAI_EMAIL_SMTP_PASSWORD",
    "XAI_API_KEY", "XAI_MODEL",
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

def load_backend_environment():
    """Load backend settings from the project file, leaving xAI settings explicit.

    xAI values are read by get_xai_config(), so a dotenv-loaded value cannot be
    mistaken for a separately supplied process environment override.
    """
    env_path = PROJECT_ROOT / ".env"
    if not env_path.is_file():
        return
    safe_lines = []
    for line in env_path.read_text(encoding="utf-8").splitlines():
        setting = line.strip()
        if setting.startswith("export "):
            setting = setting[7:].lstrip()
        name = setting.split("=", 1)[0].strip()
        if name not in {"XAI_API_KEY", "XAI_MODEL"}:
            safe_lines.append(line)
    load_dotenv(stream=StringIO("\n".join(safe_lines)), override=False)

class SettingsError(RuntimeError): pass

def get_api_base_url():
    value=os.getenv("RENTAI_API_URL","http://127.0.0.1:8001").strip().rstrip("/")
    parsed=urlsplit(value)
    if not parsed.hostname or parsed.username or parsed.password: raise SettingsError("RENTAI_API_URL is invalid.")
    if parsed.scheme!="https" and not(parsed.scheme=="http" and parsed.hostname in {"127.0.0.1","localhost","::1"}): raise SettingsError("RENTAI_API_URL must use HTTPS outside local development.")
    return value

def get_xai_config():
    """Read Grok credentials without exporting file secrets to the process environment.

    An explicit process environment value wins over the project .env value.
    The source field allows safe diagnostics of that precedence without revealing
    the credential itself.
    """
    env_path = PROJECT_ROOT / ".env"
    try:
        file_values = dotenv_values(env_path)
    except (OSError, UnicodeError):
        file_values = {}
    env_key = os.environ.get("XAI_API_KEY")
    file_key = file_values.get("XAI_API_KEY")
    env_model = os.environ.get("XAI_MODEL")
    file_model = file_values.get("XAI_MODEL")
    key = (env_key if env_key is not None else file_key or "")
    model = (env_model if env_model is not None else file_model or "grok-3-mini")
    return {
        "api_key": str(key).strip(),
        "model": str(model).strip() or "grok-3-mini",
        "key_source": "environment" if env_key is not None else ("project .env" if file_key else "missing"),
        "model_source": "environment" if env_model is not None else ("project .env" if file_model else "default"),
        "env_path": str(env_path),
    }

def xai_configuration_status():
    """Return safe configuration metadata; never return credential material."""
    config = get_xai_config()
    return {
        "project_root": str(PROJECT_ROOT),
        "env_path": config["env_path"],
        "env_file_exists": Path(config["env_path"]).is_file(),
        "api_key_configured": bool(config["api_key"]),
        "api_key_source": config["key_source"],
        "model": config["model"],
        "model_source": config["model_source"],
    }

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
    placeholders = ("replace_with", "your_", "placeholder", "example")
    if any(marker in value.casefold() for marker in placeholders for value in (key_id, key_secret)):
        raise SettingsError("Replace the placeholder Razorpay credentials with your API server's Test Mode keys.")
    if not key_id.startswith("rzp_test_"):
        raise SettingsError("RentAI accepts Razorpay Test Mode keys only. Replace RAZORPAY_KEY_ID with a test key.")
    if any(char.isspace() for char in key_id + key_secret):
        raise SettingsError("Razorpay credentials are invalid. Check the API server configuration.")
    return key_id, key_secret

def get_razorpay_webhook_secret():
    """Read the webhook signing secret without exporting it to the frontend process."""
    secret = os.getenv("RAZORPAY_WEBHOOK_SECRET", "").strip()
    if not secret:
        try:
            secret = str(dotenv_values(PROJECT_ROOT / ".env").get("RAZORPAY_WEBHOOK_SECRET") or "").strip()
        except (OSError, UnicodeError):
            raise SettingsError("Razorpay webhook configuration could not be read on the API server.") from None
    if not secret or any(char.isspace() for char in secret):
        raise SettingsError("Razorpay webhook signing is not configured. Set RAZORPAY_WEBHOOK_SECRET on the API server.")
    if any(marker in secret.casefold() for marker in ("replace_with", "your_", "placeholder", "example")):
        raise SettingsError("Replace the placeholder webhook secret with the value configured in the Razorpay dashboard.")
    return secret

def razorpay_configuration_status():
    """Return safe configuration status without returning any credential values."""
    missing = []
    try:
        get_razorpay_test_credentials()
    except SettingsError:
        missing.extend(name for name in ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET")
                       if not (os.getenv(name, "").strip() or str(dotenv_values(PROJECT_ROOT / ".env").get(name) or "").strip()))
        if not missing:
            missing.extend(("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET"))
    return {"configured": not missing, "missing": list(dict.fromkeys(missing))}

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
