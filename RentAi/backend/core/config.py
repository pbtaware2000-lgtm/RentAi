"""Shared, secret-safe configuration for Streamlit and FastAPI processes."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCAL_ENV_FILE = PROJECT_ROOT / ".env"
EXPECTED_FIREBASE_PROJECT_ID = "rentai-62ac9"
LOCAL_API_HOSTS = {"127.0.0.1", "localhost", "::1"}


class SettingsError(RuntimeError):
    """Raised when required application settings are missing or malformed."""


@dataclass(frozen=True)
class RentAISettings:
    firebase_project_id: str
    firebase_web_api_key: str
    firebase_credentials_path: Path | None
    rentai_api_url: str
    bootstrap_admin_uids: tuple[str, ...]
    whitespace_trimmed: tuple[str, ...]


def load_settings() -> RentAISettings:
    """Load process environment first, then the ignored project-root .env file."""
    load_dotenv(dotenv_path=LOCAL_ENV_FILE, override=False)
    source = {
        "FIREBASE_PROJECT_ID": os.getenv("FIREBASE_PROJECT_ID", ""),
        "FIREBASE_WEB_API_KEY": os.getenv("FIREBASE_WEB_API_KEY", ""),
        "FIREBASE_CREDENTIALS_PATH": os.getenv("FIREBASE_CREDENTIALS_PATH", ""),
        "RENTAI_API_URL": os.getenv("RENTAI_API_URL", "http://127.0.0.1:8001"),
        "RENTAI_BOOTSTRAP_ADMIN_UIDS": os.getenv("RENTAI_BOOTSTRAP_ADMIN_UIDS", ""),
    }
    trimmed = tuple(name for name, value in source.items() if value != value.strip())
    project_id = source["FIREBASE_PROJECT_ID"].strip()
    web_api_key = source["FIREBASE_WEB_API_KEY"].strip()
    credential_value = source["FIREBASE_CREDENTIALS_PATH"].strip()
    api_url = source["RENTAI_API_URL"].strip().rstrip("/")
    bootstrap_uids = tuple(
        uid.strip()
        for uid in source["RENTAI_BOOTSTRAP_ADMIN_UIDS"].split(",")
        if uid.strip()
    )
    return RentAISettings(
        firebase_project_id=project_id,
        firebase_web_api_key=web_api_key,
        firebase_credentials_path=Path(credential_value).expanduser() if credential_value else None,
        rentai_api_url=api_url,
        bootstrap_admin_uids=bootstrap_uids,
        whitespace_trimmed=trimmed,
    )


def validate_project_id(project_id: str) -> None:
    if not project_id:
        raise SettingsError("FIREBASE_PROJECT_ID is missing.")
    if project_id != EXPECTED_FIREBASE_PROJECT_ID:
        raise SettingsError("FIREBASE_PROJECT_ID does not match the configured RentAI project.")


def get_web_api_key() -> str:
    settings = load_settings()
    key = settings.firebase_web_api_key
    if not key:
        raise SettingsError("FIREBASE_WEB_API_KEY is missing.")
    validate_project_id(settings.firebase_project_id)
    try:
        encoded = key.encode("ascii")
    except UnicodeEncodeError as exc:
        raise SettingsError("FIREBASE_WEB_API_KEY must contain printable ASCII characters.") from exc
    if any(byte < 0x21 or byte > 0x7E for byte in encoded):
        raise SettingsError("FIREBASE_WEB_API_KEY contains invalid whitespace or control characters.")
    return key


def get_firebase_admin_settings() -> tuple[str, Path | None]:
    settings = load_settings()
    validate_project_id(settings.firebase_project_id)
    path = settings.firebase_credentials_path
    if path is not None and (not path.is_file()):
        raise SettingsError("FIREBASE_CREDENTIALS_PATH does not point to an available file.")
    return settings.firebase_project_id, path


def get_api_base_url() -> str:
    value = load_settings().rentai_api_url
    parsed = urlsplit(value)
    if not parsed.hostname or parsed.username or parsed.password:
        raise SettingsError("RENTAI_API_URL is invalid.")
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in LOCAL_API_HOSTS):
        raise SettingsError("RENTAI_API_URL must use HTTPS outside local development.")
    return value


def startup_configuration_issues() -> tuple[str, ...]:
    """Report only setting names that need attention; never return their values."""
    settings = load_settings()
    issues: list[str] = []
    if not settings.firebase_project_id or settings.firebase_project_id != EXPECTED_FIREBASE_PROJECT_ID:
        issues.append("FIREBASE_PROJECT_ID")
    key = settings.firebase_web_api_key
    try:
        encoded_key = key.encode("ascii")
    except UnicodeEncodeError:
        encoded_key = b""
    if not key or not encoded_key or any(byte < 0x21 or byte > 0x7E for byte in encoded_key):
        issues.append("FIREBASE_WEB_API_KEY")
    if settings.firebase_credentials_path and not settings.firebase_credentials_path.is_file():
        issues.append("FIREBASE_CREDENTIALS_PATH")
    try:
        get_api_base_url()
    except SettingsError:
        issues.append("RENTAI_API_URL")
    return tuple(dict.fromkeys(issues))
